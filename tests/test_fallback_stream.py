"""Stream-mode fallback tests (chunk-level \\0 reset behaviour).

Run:  python3 test_fallback_stream.py
"""
import os
import sys

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


# Patch parsestreamresp in the gptcommon module — the real one needs a real
# requests.Response with iter_lines(); for stream-mode fallback logic tests a
# chunk-emitting stub is enough.
import importlib

gc = importlib.import_module("translator.gptcommon")


def fake_parsestreamresp(apitype, response, hidethinking, markdown2html, model, getmodelhook=None):
    for chunk in response._chunks:
        yield chunk
    return response._final


gc.parsestreamresp = fake_parsestreamresp


def make_stream_cfg(**overrides):
    import copy

    cfg = copy.deepcopy(S.BASE_CFG)
    cfg["流式输出"] = True
    cfg.update(overrides)
    return cfg


def drive(t, src="hi"):
    collected = ""
    resets = 0
    events = []
    for x in t.translate(gc.GptTextWithDict(src)):
        if x == "\0":
            collected = ""
            resets += 1
            events.append("RESET")
        elif x:
            collected += x
            events.append("CHUNK:" + x)
    return collected, resets, events


print("\n== stream scenario A: primary OK -> single pass, no reset ==")
t = S.make_translator(make_stream_cfg())
t.proxysession.queue = [S.FakeStreamResp(["こ", "ん", "に", "ち", "は"], "こんにちは")]
out, resets, events = drive(t)
check("output is primary", out == "こんにちは", out)
check("no reset", resets == 0, str(resets))
check("1 request", len(t.proxysession.calls) == 1)

print("\n== stream scenario B: primary stream censored at end -> reset + fallback ==")
t = S.make_translator(make_stream_cfg())
t.proxysession.queue = [
    S.FakeStreamResp(["抱", "歉", "此", "内", "容", "违", "规"], "抱歉此内容违规"),
    S.FakeStreamResp(["正", "常", "結", "果"], "正常結果"),
]
out, resets, events = drive(t)
check("output is fallback's", out == "正常結果", out)
check("exactly 1 reset before fallback", resets == 1, str(resets))
check("2 requests", len(t.proxysession.calls) == 2)
check(
    "reset happens AFTER primary chunks and BEFORE fallback chunks",
    events.index("RESET") == 7 and events[8].startswith("CHUNK:正"),
    str(events),
)

print("\n== stream scenario C: primary stream censored, fallback errors -> restore primary ==")
t = S.make_translator(make_stream_cfg())
t.proxysession.queue = [
    S.FakeStreamResp(["违", "规"], "违规"),
    Exception("fallback network error"),
]
out, resets, events = drive(t)
check("output falls back to primary content", out == "违规", out)
check("resets happened (clear + restore)", resets >= 1, str(resets))
check("2 requests", len(t.proxysession.calls) == 2)

print("\n== stream scenario D: primary HTTP error -> fallback streams ==")
t = S.make_translator(make_stream_cfg())
t.proxysession.queue = [
    Exception(S.FakeResp("content blocked")),
    S.FakeStreamResp(["f", "b"], "fb"),
]
out, resets, events = drive(t)
check("output is fallback's", out == "fb", out)
check("2 requests", len(t.proxysession.calls) == 2)

print("\n== stream scenario E: primary stream OK (unrelated text), no fallback ==")
t = S.make_translator(make_stream_cfg())
t.proxysession.queue = [S.FakeStreamResp(["ok"], "ok")]
out, resets, events = drive(t)
check("output is primary", out == "ok", out)
check("no reset", resets == 0)
check("1 request", len(t.proxysession.calls) == 1)

print("\n== stream scenario F: fallback is different API type (claude URL) ==")
cfg = make_stream_cfg()
cfg["fallback.provider"] = dict(
    cfg["fallback.provider"], API接口地址="https://api.anthropic.com/v1/messages"
)
t = S.make_translator(cfg)
# _do_request will branch to _req_claude_ex which does proxysession.post to
# https://api.anthropic.com/v1/messages and expects a real response; here we
# just feed a normal body and use the patched parsestreamresp.
t.proxysession.queue = [
    S.FakeStreamResp(["违", "规"], "违规"),
    S.FakeStreamResp(["claude", "ok"], "claudeok"),
]
out, resets, events = drive(t)
check("fallback output used", out == "claudeok", out)
check(
    "fallback URL is anthropic messages endpoint",
    "api.anthropic.com" in t.proxysession.calls[1]["url"],
    t.proxysession.calls[1]["url"],
)
check("claude body has model", t.proxysession.calls[1]["json"]["model"] == "fallback-model")


# ============================================================================
# Early-abort: 流式过程中命中审查正则应该立刻撤回，不等 fallback 输出才覆盖
# ============================================================================

print("\n== stream early-abort scenario 1: 主流式第 4 个 chunk 命中 -> 立刻撤回 ==")
t = S.make_translator(make_stream_cfg())
# 主模型：第 4 个 chunk 「规」会凑出「违规」（触发）；之后还有 3 个未流的 chunk
# 设计上触发 chunk 本身会先展示（用户能看到是什么词触发了），紧接着 RESET 擦掉
t.proxysession.queue = [
    S.FakeStreamResp(["内", "容", "违", "规", "啊", "啊", "啊"], "内容违规啊啊啊"),
    S.FakeStreamResp(["o", "k"], "ok"),
]
events = []
collected = ""
resets = 0
gen = t.translate(gc.GptTextWithDict("hi"))
for x in gen:
    if x == "\0":
        collected = ""
        resets += 1
        events.append("RESET")
    elif x:
        collected += x
        events.append("CHUNK:" + x)
check("output is fallback's", collected == "ok", collected)
check("exactly 1 reset", resets == 1, str(resets))
check("2 requests", len(t.proxysession.calls) == 2)
# 主模型4 个 chunk 后就中断（包括触发的「规」，用户能看到是什么词触发了）
# 后面 「啊」「啊」「啊」都不该出现
primary_chunks_shown = [e for e in events if e.startswith("CHUNK:")][:4]
check(
    "primary stopped after trigger chunk (4 chunks shown, not 7)",
    primary_chunks_shown == ["CHUNK:内", "CHUNK:容", "CHUNK:违", "CHUNK:规"],
    str(events),
)
check(
    "reset happens immediately after trigger chunk (position 5)",
    len(events) >= 2 and events[4] == "RESET",
    str(events),
)
check(
    "fallback chunks come after the reset",
    events[5:] == ["CHUNK:o", "CHUNK:k"],
    str(events),
)

print("\n== stream early-abort scenario 2: 主流式末尾才命中 -> 仍然撤回 ==")
t = S.make_translator(make_stream_cfg())
t.proxysession.queue = [
    S.FakeStreamResp(["正", "常", "内", "容", "最", "后", "违", "规"], "正常内容最后违规"),
    S.FakeStreamResp(["f", "b"], "fb"),
]
collected, resets, events = drive(t)
check("output is fallback's", collected == "fb", collected)
check("1 reset", resets == 1)
check("2 requests", len(t.proxysession.calls) == 2)

print("\n== stream early-abort scenario 3: 无 fallback 时不提前撤回 ==")
cfg = make_stream_cfg()
cfg["fallback.provider"] = {}
t = S.make_translator(cfg)
t.proxysession.queue = [
    S.FakeStreamResp(["内", "容", "违", "规"], "内容违规"),
]
collected, resets, events = drive(t)
check("output preserved (no fallback to switch to)", collected == "内容违规", collected)
check("no reset", resets == 0)
check("all 4 chunks shown", events == ["CHUNK:内", "CHUNK:容", "CHUNK:违", "CHUNK:规"], str(events))
check("1 request", len(t.proxysession.calls) == 1)

print("\n== stream early-abort scenario 4: fallback.enabled=false 也不提前撤回 ==")
cfg = make_stream_cfg(**{"fallback.enabled": False})
t = S.make_translator(cfg)
t.proxysession.queue = [S.FakeStreamResp(["违", "规"], "违规")]
collected, resets, events = drive(t)
check("output preserved", collected == "违规")
check("no reset", resets == 0)

print("\n== stream early-abort scenario 5: 主流式无命中 -> 不撤回，正常完成 ==")
t = S.make_translator(make_stream_cfg())
t.proxysession.queue = [S.FakeStreamResp(["a", "b", "c"], "abc")]
collected, resets, events = drive(t)
check("output preserved", collected == "abc")
check("no reset", resets == 0)
check("1 request", len(t.proxysession.calls) == 1)

print("\n== stream early-abort scenario 6: markdown2html + early-abort ==")
cfg = make_stream_cfg(**{"markdown2html": True})
t = S.make_translator(cfg)
t.proxysession.queue = [
    # 每块都 emit "\0" + "LUNASHOWHTML<full html so far>"
    S.FakeStreamResp(
        [
            "\0",
            "LUNASHOWHTML<p>内</p>",
            "\0",
            "LUNASHOWHTML<p>内容</p>",
            "\0",
            "LUNASHOWHTML<p>内容违规</p>",
            "\0",
            "LUNASHOWHTML<p>内容违规后续</p>",
        ],
        "内容违规后续",
    ),
    S.FakeStreamResp(["\0", "LUNASHOWHTML<p>fb</p>"], "fb"),
]
collected = ""
resets = 0
events = []
for x in t.translate(gc.GptTextWithDict("hi")):
    if x == "\0":
        collected = ""
        resets += 1
        events.append("RESET")
    elif x:
        collected += x
        events.append("CHUNK:" + x[:30])
check("final output is fallback's html", "fb" in collected, collected)
check("requests sent", len(t.proxysession.calls) == 2)
check(
    "trigger detected before last chunk (no 内容违规后续)",
    not any("后续" in e for e in events),
    str(events),
)

print("\n" + "=" * 50)
print(f"PASSED: {PASSED}   FAILED: {FAILED}")
sys.exit(0 if FAILED == 0 else 1)
