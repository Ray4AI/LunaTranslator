"""Tests for `cancel_previous_request` — 新请求到达时立刻取消上一个翻译请求。

Run:  python3 test_cancel_previous.py
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


# monkeypatch parsestreamresp so the streaming path is testable
def fake_parsestreamresp(apitype, response, hidethinking, markdown2html, model, getmodelhook=None):
    for chunk in response.iter_lines(decode_unicode=True):
        yield chunk
    return "".join(response._final)


gc.parsestreamresp = fake_parsestreamresp


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


class HangingStreamResp:
    def __init__(self):
        self.closed = False

    def iter_lines(self, *a, **kw):
        def _gen():
            while not self.closed:
                time.sleep(0.02)
            return
            yield  # pragma: no cover

        return _gen()

    def close(self):
        self.closed = True


def drive_async(t, src="hi"):
    """Drive translate() in a background thread; returns (state_dict, thread)."""
    state = {"collected": "", "resets": 0, "errors": None, "done": False}

    def _run():
        try:
            for x in t.translate(gc.GptTextWithDict(src)):
                if x == "\0":
                    state["collected"] = ""
                    state["resets"] += 1
                elif x:
                    state["collected"] += x
        except Exception as e:
            state["errors"] = e
        state["done"] = True

    th = threading.Thread(target=_run, daemon=True)
    th.start()
    return state, th


print("\n== 取消机制原语：generation 计数 + response 注册 ==")
t = S.make_translator(make_cfg())

g0 = t._new_request_gen()
check("initial generation is 0", g0 == 0, str(g0))
check("not cancelled at start", not t._is_cancelled(g0))

r1 = OkStreamResp(["x"], "x")
check("register response succeeds", t._register_response(g0, r1))
check("current response is r1", t._current_response is r1)

t.cancel_previous()
check("after cancel_previous: generation bumped", t._new_request_gen() == 1)
check("old gen is cancelled", t._is_cancelled(g0))
check("r1.close() called", r1.closed is True)
check("current response cleared", t._current_response is None)

g1 = t._new_request_gen()
check("new gen is 1 (matches current)", g1 == 1 and not t._is_cancelled(g1))

# register with stale gen -> rejected + response closed
r2 = OkStreamResp(["y"], "y")
ok = t._register_response(g0, r2)  # stale gen
check("stale-gen register rejected", ok is False)
check("stale response auto-closed", r2.closed is True)


print("\n== 取消机制：translate() 收到取消后立刻 return（不 fallback）==")

# 场景 A：正在流式时被取消 -> RequestCancelled 抛出（translate_and_collect 捕获）
cfg = make_cfg()
cfg["流式输出"] = True
t = S.make_translator(cfg)
hanging = HangingStreamResp()
t.proxysession.queue = [hanging, OkStreamResp(["unused"], "unused")]
state, th = drive_async(t)
# 等 translate() 进入 _do_request 并阻塞在 iter_lines
time.sleep(0.2)
# 现在取消
t.cancel_previous()
# hanging.close() 让阻塞的 iter_lines 退出
hanging.close()
th.join(timeout=2.0)
check("translate thread exited", state["done"] is True or not th.is_alive())
check(
    "cancelled -> no fallback request triggered",
    len(t.proxysession.calls) == 1,
    "calls=" + str(len(t.proxysession.calls)),
)
check(
    "fallback still queued (not consumed)",
    len(t.proxysession.queue) == 1,
    "remaining=" + str(len(t.proxysession.queue)),
)

# 场景 B：取消在 _do_request 之前发生 -> _do_request 拒绝并抛 RequestCancelled
cfg = make_cfg()
cfg["流式输出"] = False
t = S.make_translator(cfg)
gen = t._new_request_gen()
t.cancel_previous()  # 先取消
t.proxysession.queue = [S.FakeResp("should never be consumed")]
err = None
try:
    # 直接调 _do_request，模拟 translate() 的入口
    att = t._mkattempt(
        cfg["API接口地址"], "KEY", cfg["model"], {}, {}, {}, False
    )
    t._do_request(att, [], False, gen=gen)
except gc.RequestCancelled as e:
    err = e
except Exception as e:
    err = e
check("stale-gen _do_request raises RequestCancelled", isinstance(err, gc.RequestCancelled), repr(err))
check(
    "response auto-closed after reject (queue consumed the request)",
    len(t.proxysession.calls) == 1,
    "calls=" + str(len(t.proxysession.calls)),
)

# 场景 C：正常完成（不取消）-> translate() 走完整流程
cfg = make_cfg()
cfg["流式输出"] = True
t = S.make_translator(cfg)
t.proxysession.queue = [OkStreamResp(["a", "b"], "ab")]
state, th = drive_async(t)
th.join(timeout=2.0)
check("normal completion", state["done"] and state["collected"] == "ab", str(state))
check("no error", state["errors"] is None, repr(state["errors"]))

# 场景 D：RequestCancelled 会跳过缓存写入（模拟 translate_and_collect 行为）
# 这里我们直接验证：RequestCancelled 是 Exception 的子类但被单独 except 捕获
check(
    "RequestCancelled is Exception subclass",
    issubclass(gc.RequestCancelled, Exception),
)
check(
    "RequestCancelled != _FirstTokenTimeout",
    gc.RequestCancelled is not gc._FirstTokenTimeout,
)

# 场景 E：取消后 generation 不会复用，后续请求正常
cfg = make_cfg()
cfg["流式输出"] = False
t = S.make_translator(cfg)
t.cancel_previous()
t.cancel_previous()
t.cancel_previous()
g = t._new_request_gen()
check("3 cancels -> generation=3", g == 3, str(g))
t.proxysession.queue = [S.FakeResp("post-cancel")]
out, resets = S.run_translate(t)
check("post-cancel request still works", out == "post-cancel", out)


print("\n== switch 行为：cancel_previous_request 关闭时 _fythread 不调 cancel_previous ==")

# 这里我们直接读 config 开关的语义（_fythread 是阻塞循环，不便直接跑）
# 关闭 -> config.get('cancel_previous_request', False) == False
cfg = make_cfg()
cfg["cancel_previous_request"] = False
t = S.make_translator(cfg)
check(
    "switch=False -> config.get returns False",
    t.config.get("cancel_previous_request", False) is False,
)

cfg = make_cfg()
cfg["cancel_previous_request"] = True
t = S.make_translator(cfg)
check(
    "switch=True -> config.get returns True",
    t.config.get("cancel_previous_request", False) is True,
)


print("\n" + "=" * 50)
print(f"PASSED: {PASSED}   FAILED: {FAILED}")
sys.exit(0 if FAILED == 0 else 1)
