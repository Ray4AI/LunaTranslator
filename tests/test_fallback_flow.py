"""End-to-end mock tests for gptcommon.translate() fallback flow (non-stream).

Run:  python3 test_fallback_flow.py
"""
import copy
import json
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


def make_cfg(**overrides):
    cfg = copy.deepcopy(S.BASE_CFG)
    cfg.update(overrides)
    return cfg


print("\n== scenario 1: primary OK, no fallback ==")
t = S.make_translator(make_cfg())
t.proxysession.queue = [S.FakeResp("这是正常翻译。")]
out, resets = S.run_translate(t)
check("output is primary", out == "这是正常翻译。", out)
check("no resets", resets == 0, str(resets))
check("only 1 request sent", len(t.proxysession.calls) == 1)
check("primary model used", t.proxysession.calls[0]["json"]["model"] == "primary-model")

print("\n== scenario 2: primary returns censor text -> fallback ==")
t = S.make_translator(make_cfg())
t.proxysession.queue = [
    S.FakeResp("抱歉，此内容违规，无法提供翻译。"),
    S.FakeResp("正常な翻訳結果です。"),
]
out, resets = S.run_translate(t)
check("output is fallback's", out == "正常な翻訳結果です。", out)
check("no reset (non-stream buffers before emit)", resets == 0, str(resets))
check("2 requests sent", len(t.proxysession.calls) == 2)
check("fallback model used", t.proxysession.calls[1]["json"]["model"] == "fallback-model")
check(
    "fallback body merges its own extrabody",
    t.proxysession.calls[1]["json"].get("user") == "me",
    str(t.proxysession.calls[1]["json"]),
)
check(
    "primary body does NOT contain fallback extrabody",
    "user" not in t.proxysession.calls[0]["json"],
    str(t.proxysession.calls[0]["json"]),
)

print("\n== scenario 3: primary raises HTTP error with censor message -> fallback ==")
t = S.make_translator(make_cfg())
t.proxysession.queue = [
    Exception(S.FakeResp('{"error":"content blocked by policy"}')),
    S.FakeResp("fallback result"),
]
out, resets = S.run_translate(t)
check("fallback used after err", out == "fallback result", out)
check("2 requests", len(t.proxysession.calls) == 2)

print("\n== scenario 4: unrelated primary error -> NO fallback (on_any_error=False) ==")
t = S.make_translator(make_cfg())
t.proxysession.queue = [Exception("Connection refused by remote host")]


def _run():
    try:
        S.run_translate(t)
        return None
    except Exception as e:
        return e


e = _run()
check(
    "error propagates",
    isinstance(e, Exception) and "Connection refused" in str(e),
    repr(e),
)
check("only 1 request", len(t.proxysession.calls) == 1)


print("\n== scenario 5: on_any_error=True -> any error triggers fallback ==")
t = S.make_translator(make_cfg(**{"fallback.on_any_error": True}))
t.proxysession.queue = [
    Exception("Connection refused"),
    S.FakeResp("rescued by fallback"),
]
out, _ = S.run_translate(t)
check("fallback rescued", out == "rescued by fallback", out)

print("\n== scenario 6: fallback fails too, primary had censored content -> show primary ==")
t = S.make_translator(make_cfg())
t.proxysession.queue = [
    S.FakeResp("内容违规"),
    Exception("fallback network error"),
]
out, resets = S.run_translate(t)
check("falls back to primary content", out == "内容违规", out)

print("\n== scenario 7: fallback not configured -> primary used even if censored ==")
cfg = make_cfg()
cfg["fallback.provider"] = {}
t = S.make_translator(cfg)
t.proxysession.queue = [S.FakeResp("内容违规")]
out, _ = S.run_translate(t)
check("primary shown as-is", out == "内容违规", out)
check("1 request", len(t.proxysession.calls) == 1)

print("\n== scenario 8: fallback disabled -> primary used even if censored ==")
t = S.make_translator(make_cfg(**{"fallback.enabled": False}))
t.proxysession.queue = [S.FakeResp("内容违规")]
out, _ = S.run_translate(t)
check("primary shown as-is", out == "内容违规", out)
check("1 request", len(t.proxysession.calls) == 1)

print("\n== scenario 9: fallback key rotation ==")
t = S.make_translator(make_cfg())
t.proxysession.queue = [S.FakeResp("blocked"), S.FakeResp("ok")]
S.run_translate(t)
check("first rotation landed in {0, 1}", t._fallback_key_idx in (0, 1))
t.proxysession.queue = [S.FakeResp("blocked"), S.FakeResp("ok")]
S.run_translate(t)
check("second call rotates again", t._fallback_key_idx in (0, 1))

print("\n== scenario 10: markdown2html wraps output ==")
t = S.make_translator(make_cfg(**{"markdown2html": True}))
t.proxysession.queue = [S.FakeResp("hello")]
out, _ = S.run_translate(t)
check(
    "markdown converted",
    out.startswith("LUNASHOWHTML<p>") and out.endswith("</p>"),
    out,
)

print("\n== scenario 11: fallback.regex empty -> only on_any_error triggers ==")
cfg = make_cfg(**{"fallback.trigger_regex": ""})
t = S.make_translator(cfg)
t.proxysession.queue = [S.FakeResp("内容违规"), S.FakeResp("unused")]
out, _ = S.run_translate(t)
check("no regex -> no fallback", len(t.proxysession.calls) == 1, str(len(t.proxysession.calls)))
check("primary output shown", out == "内容违规", out)

print("\n== scenario 12: context appended after fallback ==")
t = S.make_translator(make_cfg())
t._context = []
t.proxysession.queue = [
    S.FakeResp("blocked"),
    S.FakeResp("fallback translation"),
]
S.run_translate(t)
check(
    "context has user + assistant entries",
    len(t._context) == 2
    and t._context[0]["role"] == "user"
    and t._context[1]["role"] == "assistant",
    str(t._context),
)
check(
    "assistant message is the fallback output",
    t._context[1]["content"] == "fallback translation",
    t._context[1]["content"],
)


print("\n" + "=" * 50)
print(f"PASSED: {PASSED}   FAILED: {FAILED}")
sys.exit(0 if FAILED == 0 else 1)
