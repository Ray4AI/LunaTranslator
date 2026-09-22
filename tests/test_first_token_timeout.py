"""First-token timeout tests (NSFW API hangs -> fallback).

Run:  python3 test_first_token_timeout.py
"""
import os
import sys
import time
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)  # so `import test_stubs` works

import test_stubs as S

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print("  PASS", name)
    else:
        FAILED += 1
        print("  FAIL", name, "|", detail)


import importlib

gc = importlib.import_module("translator.gptcommon")


def make_cfg(**overrides):
    import copy

    cfg = copy.deepcopy(S.BASE_CFG)
    cfg.update(overrides)
    return cfg


print("\n== setting semantics: fallback.first_token_timeout.use 开关 ==")

t = S.make_translator(make_cfg())
check(
    "switch=True + value=2.0 -> 2.0",
    t._first_token_timeout_s() == 2.0,
    str(t._first_token_timeout_s()),
)

t = S.make_translator(make_cfg(**{"fallback.first_token_timeout.use": False}))
check(
    "switch=False -> 0 (disabled)",
    t._first_token_timeout_s() == 0.0,
    str(t._first_token_timeout_s()),
)

t = S.make_translator(make_cfg(**{"fallback.first_token_timeout": 5.5}))
check(
    "switch=True + value=5.5 -> 5.5",
    t._first_token_timeout_s() == 5.5,
    str(t._first_token_timeout_s()),
)

t = S.make_translator(make_cfg(**{"fallback.first_token_timeout": 0}))
check(
    "switch=True + value=0 -> 0 (treated as disabled)",
    t._first_token_timeout_s() == 0.0,
    str(t._first_token_timeout_s()),
)

t = S.make_translator(make_cfg(**{"fallback.first_token_timeout": -1}))
check(
    "switch=True + value=-1 -> 0",
    t._first_token_timeout_s() == 0.0,
    str(t._first_token_timeout_s()),
)

t = S.make_translator(make_cfg(**{"fallback.first_token_timeout": "bad"}))
check(
    "switch=True + non-numeric value -> 0",
    t._first_token_timeout_s() == 0.0,
    str(t._first_token_timeout_s()),
)


print("\n== trigger semantics: 超时独立触发 fallback（不受 on_any_error 影响）==")

# 注意：LunaTranslator 自带 requests.py（src/LunaTranslator/requests.py），
# import requests 拿到的不是真的 requests 库，其 exceptions 是一个 class，
# 里面只有 RequestException / Timeout / HTTPError（没有 ReadTimeout/ConnectTimeout）。
import requests as luna_requests

t = S.make_translator(make_cfg())  # on_any_error=False
check(
    "_FirstTokenTimeout triggers fallback (on_any_error=False)",
    t._should_fallback_on_error(gc._FirstTokenTimeout(2.0)),
)
check(
    "requests.exceptions.Timeout triggers fallback",
    t._should_fallback_on_error(luna_requests.exceptions.Timeout()),
)
check(
    "requests.exceptions.RequestException does NOT auto-trigger (falls through to regex)",
    not t._should_fallback_on_error(luna_requests.exceptions.RequestException()),
)
check(
    "non-timeout error with on_any_error=False does NOT trigger",
    not t._should_fallback_on_error(Exception("some random error")),
)

t = S.make_translator(make_cfg(**{"fallback.enabled": False}))
check(
    "fallback.enabled=False -> timeout does NOT trigger",
    not t._should_fallback_on_error(gc._FirstTokenTimeout(2.0)),
)


print("\n== _ResponseProxy wraps iter_lines() with first-line timeout ==")


class FakeLineIter:
    """Fake iter_lines() source. Sleeps before yielding the first line."""

    def __init__(self, first_delay, lines):
        self._first_delay = first_delay
        self._lines = list(lines)
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self._first_delay > 0:
            time.sleep(self._first_delay)
            self._first_delay = 0  # only delay the first line
        if not self._lines:
            raise StopIteration
        return self._lines.pop(0)

    def close(self):
        self.closed = True


class FakeResp:
    def __init__(self, line_iter):
        self._line_iter = line_iter
        self.closed = False

    def iter_lines(self, *a, **kw):
        return self._line_iter

    def close(self):
        self.closed = True


#1) first line fast -> no timeout
it = FakeLineIter(first_delay=0, lines=["a", "b", "c"])
resp = FakeResp(it)
proxy = gc._ResponseProxy(resp, 0.3)
got = list(proxy.iter_lines(decode_unicode=True))
check("first line fast -> all lines yielded", got == ["a", "b", "c"], str(got))

# 2) first line slow -> _FirstTokenTimeout
it = FakeLineIter(first_delay=1.0, lines=["a", "b"])
resp = FakeResp(it)
proxy = gc._ResponseProxy(resp, 0.3)
got = []
err = None
try:
    for x in proxy.iter_lines(decode_unicode=True):
        got.append(x)
except gc._FirstTokenTimeout as e:
    err = e
check("first line slow -> _FirstTokenTimeout raised", err is not None, repr(err))
check("timeout value carried over", err is not None and err.timeout_s == 0.3)
check("underlying response.close() called on timeout", resp.closed is True)

# 3) 后续行慢但首行快 -> 不抛超时（符合"首 token"语义）
it = FakeLineIter(first_delay=0, lines=["fast-first", "slow-second"])
# 手动构造：让第二个 next 慢
class SlowAfterFirst(FakeLineIter):
    def __next__(self):
        if self._first_delay == 0 and not self._lines == ["fast-first", "slow-second"]:
            time.sleep(0.6)
        return super().__next__()

it2 = SlowAfterFirst(first_delay=0, lines=["fast-first", "slow-second"])
resp2 = FakeResp(it2)
proxy2 = gc._ResponseProxy(resp2, 0.3)
got2 = []
err2 = None
try:
    for x in proxy2.iter_lines(decode_unicode=True):
        got2.append(x)
except gc._FirstTokenTimeout as e:
    err2 = e
check("slow second line -> no timeout (first token already received)", err2 is None, repr(err2))
check("all lines yielded", got2 == ["fast-first", "slow-second"], str(got2))

# 4) iter_lines 只包装第一次
it = FakeLineIter(first_delay=0, lines=["x"])
resp = FakeResp(it)
proxy = gc._ResponseProxy(resp, 0.3)
list(proxy.iter_lines())  # first call wrapped
it2_inner = FakeLineIter(first_delay=1.0, lines=["y"])  # 2nd iter_lines would time out if wrapped
resp._line_iter = it2_inner
got4 = list(proxy.iter_lines())  # should NOT be wrapped
check("only first iter_lines() is wrapped", got4 == ["y"], str(got4))


print("\n== end-to-end: 主接口首 token 超时 -> fallback ==")

# monkeypatch parsestreamresp to a simple chunk emitter
def fake_parsestreamresp(apitype, response, hidethinking, markdown2html, model, getmodelhook=None):
    for chunk in response.iter_lines(decode_unicode=True):
        yield chunk
    return "".join(response._final)


gc.parsestreamresp = fake_parsestreamresp


class HangingStreamResp:
    """模拟 NSFW API：接受连接但一直不吐任何字节（首 token 永远不来）。"""

    def __init__(self):
        self.closed = False

    def iter_lines(self, *a, **kw):
        def _gen():
            # 永远阻塞（或直到 close）
            while not self.closed:
                time.sleep(0.05)
            return
            yield  # pragma: no cover

        return _gen()

    def close(self):
        self.closed = True


class OkStreamResp:
    def __init__(self, chunks, final):
        self._chunks = list(chunks)
        self._final = final
        self.closed = False

    def iter_lines(self, *a, **kw):
        def _gen():
            for c in self._chunks:
                yield c

        return _gen()

    def close(self):
        self.closed = True


def drive(t):
    collected = ""
    resets = 0
    errors = None
    try:
        for x in t.translate(gc.GptTextWithDict("hi")):
            if x == "\0":
                collected = ""
                resets += 1
            elif x:
                collected += x
    except Exception as e:
        errors = e
    return collected, resets, errors


# 场景 A：主接口挂住不响应 + 首 token 超时启用 -> fallback
cfg = make_cfg()
cfg["流式输出"] = True
cfg["fallback.first_token_timeout.use"] = True
cfg["fallback.first_token_timeout"] = 0.3
t = S.make_translator(cfg)
t.proxysession.queue = [
    HangingStreamResp(),
    OkStreamResp(["ok"], "ok"),
]
start = time.time()
collected, resets, errors = drive(t)
elapsed = time.time() - start
check("output is fallback's", collected == "ok", collected)
check("no error raised", errors is None, repr(errors))
check("2 requests sent", len(t.proxysession.calls) == 2)
check(
    "timeout triggered around 0.3s (not stuck forever)",
    0.2 < elapsed < 2.0,
    "elapsed={:.2f}s".format(elapsed),
)

# 场景 B：首 token 超时关闭 -> 主接口挂住时不会 fallback（会一直等）
cfg = make_cfg()
cfg["流式输出"] = True
cfg["fallback.first_token_timeout.use"] = False
t = S.make_translator(cfg)
hanging = HangingStreamResp()
t.proxysession.queue = [
    hanging,
    OkStreamResp(["unused"], "unused"),
]


def _drive_with_deadline(t, deadline_s=0.5):
    """在另一个线程驱动 translate()，超时就放弃（用于测试'不会自动 fallback'）。"""
    result = {"collected": "", "errors": None, "done": False}

    def _run():
        try:
            for x in t.translate(gc.GptTextWithDict("hi")):
                if x == "\0":
                    result["collected"] = ""
                elif x:
                    result["collected"] += x
        except Exception as e:
            result["errors"] = e
        result["done"] = True

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    th.join(timeout=deadline_s)
    hanging.close()  # 让阻塞的 iter_lines 退出
    th.join(timeout=0.5)
    return result


r = _drive_with_deadline(t, 0.5)
check(
    "switch off -> primary hangs (no fallback within 0.5s)",
    len(t.proxysession.calls) == 1,
    "calls=" + str(len(t.proxysession.calls)),
)
check(
    "switch off -> fallback not consumed",
    len(t.proxysession.queue) == 1,
    "remaining queue=" + str(len(t.proxysession.queue)),
)

# 场景 C：主接口立刻返回正常内容 -> 不触发超时
cfg = make_cfg()
cfg["流式输出"] = True
cfg["fallback.first_token_timeout.use"] = True
cfg["fallback.first_token_timeout"] = 0.5
t = S.make_translator(cfg)
t.proxysession.queue = [OkStreamResp(["a", "b"], "ab")]
start = time.time()
collected, resets, errors = drive(t)
elapsed = time.time() - start
check("output is primary's", collected == "ab", collected)
check("1 request", len(t.proxysession.calls) == 1)
check("no error", errors is None, repr(errors))
check("completed quickly (no spurious timeout)", elapsed < 0.4, "{:.2f}s".format(elapsed))

# 场景 D：首 token 超时 + fallback 也超时 -> 报错（没得救）
cfg = make_cfg()
cfg["流式输出"] = True
cfg["fallback.first_token_timeout.use"] = True
cfg["fallback.first_token_timeout"] = 0.3
t = S.make_translator(cfg)
t.proxysession.queue = [
    HangingStreamResp(),
    HangingStreamResp(),
]
collected, resets, errors = drive(t)
check(
    "both hang -> error raised",
    errors is not None,
    repr(errors),
)
check("2 requests sent", len(t.proxysession.calls) == 2)

# 场景 E：非流式首 token 超时（post 的 timeout=）
# 说明：非流式走 requests 的 timeout=(connect, ft_timeout)；这里我们通过 monkeypatch 验证
#      proxysession.post 收到了 timeout 参数。
cfg = make_cfg()
cfg["流式输出"] = False
cfg["fallback.first_token_timeout.use"] = True
cfg["fallback.first_token_timeout"] = 1.7
t = S.make_translator(cfg)
# 记录 post 调用的 timeout
orig_post = t.proxysession.post
captured = {"timeouts": []}


def capture_post(url, headers=None, json=None, stream=False, timeout=None, **kw):
    captured["timeouts"].append(timeout)
    return orig_post(url, headers=headers, json=json, stream=stream, **kw)


t.proxysession.post = capture_post
t.proxysession.queue = [S.FakeResp("normal")]
S.run_translate(t)
check(
    "non-stream passes timeout=(5, 1.7) to post",
    captured["timeouts"] and captured["timeouts"][0] == (5, 1.7),
    str(captured["timeouts"]),
)

# 场景 F：流式 safety net = (5, ft+5)
cfg = make_cfg()
cfg["流式输出"] = True
cfg["fallback.first_token_timeout.use"] = True
cfg["fallback.first_token_timeout"] = 2.0
t = S.make_translator(cfg)
orig_post = t.proxysession.post
captured = {"timeouts": []}


def capture_post2(url, headers=None, json=None, stream=False, timeout=None, **kw):
    captured["timeouts"].append(timeout)
    return orig_post(url, headers=headers, json=json, stream=stream, **kw)


t.proxysession.post = capture_post2
t.proxysession.queue = [OkStreamResp(["x"], "x")]
drive(t)
check(
    "stream passes timeout=(5, 7.0) safety-net to post",
    captured["timeouts"] and captured["timeouts"][0] == (5, 7.0),
    str(captured["timeouts"]),
)

# 场景 G：禁用时 post 不带 timeout
cfg = make_cfg()
cfg["流式输出"] = False
cfg["fallback.first_token_timeout.use"] = False
t = S.make_translator(cfg)
orig_post = t.proxysession.post
captured = {"timeouts": []}


def capture_post3(url, headers=None, json=None, stream=False, timeout=None, **kw):
    captured["timeouts"].append(timeout)
    return orig_post(url, headers=headers, json=json, stream=stream, **kw)


t.proxysession.post = capture_post3
t.proxysession.queue = [S.FakeResp("ok")]
S.run_translate(t)
check(
    "disabled -> post(timeout=None)",
    captured["timeouts"] and captured["timeouts"][0] is None,
    str(captured["timeouts"]),
)


print("\n" + "=" * 50)
print(f"PASSED: {PASSED}   FAILED: {FAILED}")
sys.exit(0 if FAILED == 0 else 1)
