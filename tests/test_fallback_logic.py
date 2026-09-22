"""Standalone unit tests for LunaTranslator LLM fallback logic.

Avoids importing the full GUI stack — extracts just the pure helpers.
Run:  python3 test_fallback_logic.py
"""
import re
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)  # so `import test_stubs` works
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "LunaTranslator"))

# ---- Mirror of gptcommon._ConfigView (kept in sync with src/LunaTranslator/translator/gptcommon.py) ----


class _ConfigView:
    def __init__(self, base, overrides: dict = None):
        self._base = base
        self._overrides = overrides or {}

    def __getitem__(self, k):
        if k in self._overrides:
            return self._overrides[k]
        return self._base[k]

    def get(self, k, default=None):
        if k in self._overrides:
            return self._overrides[k]
        return self._base.get(k, default)

    def __contains__(self, k):
        return (k in self._overrides) or (k in self._base)


# ---- Mirror of the fallback regex matcher (kept in sync) ----


def _fallback_patterns(raw):
    return [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]


def _matches_fallback_regex(text, regex_str, ignorecase=True):
    pats = _fallback_patterns(regex_str)
    if not pats or not text:
        return False
    flags = re.IGNORECASE if ignorecase else 0
    for p in pats:
        try:
            if re.search(p, text, flags):
                return True
        except re.error:
            continue
    return False


def _error_to_text(e):
    class FakeResp:
        def __init__(self, t):
            self.text = t

    parts = []
    for arg in getattr(e, "args", []):
        if isinstance(arg, FakeResp):
            parts.append(arg.text)
        else:
            parts.append(str(arg))
    if not parts:
        parts.append(str(e))
    return "\n".join(parts)


# ---------------------------- tests ----------------------------

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print("  PASS", name)
    else:
        FAILED += 1
        print("  FAIL", name, detail)


print("\n== _ConfigView ==")
base = {"model": "primary-model", "max_tokens": 1024, "customparams": [1, 2]}
override = {"model": "fallback-model", "Temperature": 0.5}
cv = _ConfigView(base, override)
check("override wins", cv["model"] == "fallback-model")
check("base fallback when not in override", cv["max_tokens"] == 1024)
check("override introduces new key", cv["Temperature"] == 0.5)
check("get with default works", cv.get("nonexistent", "dflt") == "dflt")
check("get with default respects override", cv.get("model", "dflt") == "fallback-model")
check("in operator", "model" in cv and "max_tokens" in cv and "Temperature" in cv)
check("not in operator", "not-a-key" not in cv)
check("shared non-scalar preserved", cv["customparams"] == [1, 2])

print("\n== fallback regex matching ==")
PATTERNS_CN_EN = (
    "内容.*违规\n"
    "包含.*敏感\n"
    "被拦截\n"
    "不允许服务\n"
    "审核不通过\n"
    "sensitive|blocked|violates|policy\n"
    "\n"  # blank lines should be ignored
    "   \n"  # whitespace-only lines ignored
)
check(
    "Chinese censor in translation",
    _matches_fallback_regex("您的内容违规，无法翻译。", PATTERNS_CN_EN),
)
check(
    "Chinese sensitive-word refusal",
    _matches_fallback_regex("包含敏感信息，请修改。", PATTERNS_CN_EN),
)
check(
    "Chinese short refusal",
    _matches_fallback_regex("此内容被拦截", PATTERNS_CN_EN),
)
check(
    "English refusal (uppercase, ignorecase=True)",
    _matches_fallback_regex("This response was BLOCKED.", PATTERNS_CN_EN, ignorecase=True),
)
check(
    "English refusal (uppercase, ignorecase=False) should NOT match",
    not _matches_fallback_regex("This response was BLOCKED.", PATTERNS_CN_EN, ignorecase=False),
)
check(
    "Normal translation should NOT match",
    not _matches_fallback_regex("これは正常な翻訳結果です。", PATTERNS_CN_EN),
)
check(
    "Empty regex should NOT match",
    not _matches_fallback_regex("anything", ""),
)
check(
    "Empty text should NOT match",
    not _matches_fallback_regex("", PATTERNS_CN_EN),
)
check(
    "Invalid regex line is silently skipped",
    _matches_fallback_regex("blocked", "(unclosed[\nblocked"),
)
check(
    "Multi-line patterns each on own line",
    _matches_fallback_regex("some text policy violation here", PATTERNS_CN_EN),
)

print("\n== error text extraction ==")
err = Exception("HTTP 400", '{"error":{"message":"content filtered by policy"}}')
txt = _error_to_text(err)
check("error args joined", "content filtered" in txt and "HTTP 400" in txt)
check(
    "regex matches error text",
    _matches_fallback_regex(_error_to_text(err), PATTERNS_CN_EN),
)

print("\n== config schema sanity ==")
import json

cfg_path = os.path.join(
    REPO_ROOT, "src", "LunaTranslator", "defaultconfig", "translatorsetting.json"
)
with open(cfg_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)
gpt_args = cfg["chatgpt-3rd-party"]["args"]
gpt_types = cfg["chatgpt-3rd-party"]["argstype"]
for k in (
    "fallback.enabled",
    "fallback.on_any_error",
    "fallback.trigger_regex",
    "fallback.ignorecase",
    "fallback.provider",
):
    check(f"args has {k}", k in gpt_args)
    check(f"argstype has {k}", k in gpt_types)
check(
    "fallback.provider points to fallbackproviderbutton",
    gpt_types["fallback.provider"].get("function") == "fallbackproviderbutton",
)
check(
    "fallback.provider type is custom",
    gpt_types["fallback.provider"].get("type") == "custom",
)
check(
    "fallback.provider default is empty dict",
    gpt_args["fallback.provider"] == {},
)

print("\n== fallbackproviderbutton defaults sanity ==")
# Extract DEFAULTS keys from source without importing (avoids Qt)
src_path = os.path.join(REPO_ROOT, "src", "LunaTranslator", "gui", "customparams.py")
with open(src_path, "r", encoding="utf-8") as f:
    src = f.read()
for k in (
    "API接口地址",
    "SECRET_KEY",
    "model",
    "modellistcache",
    "customparams",
    "max_tokens",
    "Temperature",
    "top_p",
):
    check(f"fallbackproviderbutton.DEFAULTS has {k!r}", f'"{k}"' in src)
check(
    "fallbackproviderbutton class exists",
    "class fallbackproviderbutton" in src,
)
check(
    "fallbackproviderbutton uses subautoinitdialog",
    "subautoinitdialog" in src,
)

print("\n== inputdialog exposes subautoinitdialog ==")
inpd_path = os.path.join(REPO_ROOT, "src", "LunaTranslator", "gui", "inputdialog.py")
with open(inpd_path, "r", encoding="utf-8") as f:
    inpd = f.read()
check(
    "subautoinitdialog alias defined",
    "subautoinitdialog = autoinitdialog_impl" in inpd,
)
check(
    "autoinitdialog still exported (Singleton)",
    "autoinitdialog = Singleton(autoinitdialog_impl)" in inpd,
)
check(
    "custom type passes dialog to widget",
    "lineWF(self._dict, key, dialog=self)" in inpd,
)

print("\n== gptcommon fallback entry points ==")
gc_path = os.path.join(REPO_ROOT, "src", "LunaTranslator", "translator", "gptcommon.py")
with open(gc_path, "r", encoding="utf-8") as f:
    gc = f.read()
for fn in (
    "_fallback_provider",
    "_fallback_patterns",
    "_matches_fallback_regex",
    "_should_fallback_on_response",
    "_should_fallback_on_error",
    "_mkattempt",
    "_mkfallbackattempt",
    "_do_request",
    "_request_gemini_ex",
    "_req_claude_ex",
):
    check(f"gptcommon defines {fn}", f"def {fn}" in gc)
check("_ConfigView defined in gptcommon", "class _ConfigView" in gc)
check(
    "translate uses fallback attempt",
    "_mkfallbackattempt" in gc.split("def translate")[1][:3000],
)
check(
    "fallback re-exports customparams & fallbackproviderbutton",
    "from gui.customparams import customparams, fallbackproviderbutton" in gc,
)

print("\n" + "=" * 50)
print(f"PASSED: {PASSED}   FAILED: {FAILED}")
sys.exit(0 if FAILED == 0 else 1)
