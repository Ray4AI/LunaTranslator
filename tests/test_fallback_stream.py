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

print("\n" + "=" * 50)
print(f"PASSED: {PASSED}   FAILED: {FAILED}")
sys.exit(0 if FAILED == 0 else 1)
