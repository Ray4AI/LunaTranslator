"""Shared test stubs for LunaTranslator LLM fallback tests.

Importing this module sets up sys.modules with fakes so translator.gptcommon
can be imported without Qt. Both test_fallback_flow.py and
test_fallback_stream.py use it.
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO_ROOT, "src", "LunaTranslator"))


# ---- stub NativeUtils ----
nu = types.ModuleType("NativeUtils")
nu.Markdown2Html = lambda s: "<p>" + s + "</p>"
sys.modules["NativeUtils"] = nu


# ---- stub gui package (real path so gui.customparams imports can resolve) ----
gui_pkg = types.ModuleType("gui")
gui_pkg.__path__ = [os.path.join(REPO_ROOT, "src", "LunaTranslator", "gui")]
sys.modules["gui"] = gui_pkg


def _fake_getcustombodyheaders(customparams, **kw):
    eb = {}
    eh = {}
    for p in customparams or []:
        if p.get("type") == "header":
            eh[p["key"]] = p["value"]
        else:
            eb[p["key"]] = p["value"]
    return eb, eh


cp = types.ModuleType("gui.customparams")
cp.getcustombodyheaders = _fake_getcustombodyheaders
cp.customparams = object
cp.fallbackproviderbutton = object
sys.modules["gui.customparams"] = cp


# ---- myutils stubs ----
myutils_pkg = types.ModuleType("myutils")
myutils_pkg.__path__ = []
sys.modules["myutils"] = myutils_pkg


class APIType:
    class openai:
        pass

    class gemini:
        pass

    class claude:
        pass

    class qianfan(openai):
        pass

    class azure(openai):
        pass

    class aliyuncs(openai):
        pass

    class cohere(openai):
        pass

    class mistral(openai):
        pass

    class zhipuocr:
        pass

    def __eq__(self, value):
        return issubclass(self._value_, value)

    def __init__(self, url):
        self.url = url.strip()
        if "gemini" in url or "generativelanguage" in url:
            self._value_ = APIType.gemini
        elif "anthropic" in url:
            self._value_ = APIType.claude
        else:
            self._value_ = APIType.openai

    def finalurl(self, checkend="/chat/completions"):
        return self.url.rstrip("/#") + checkend


mu = types.ModuleType("myutils.utils")
mu.APIType = APIType
mu.common_list_models = lambda *a, **kw: []
mu.common_parse_normal_response = lambda response, apitype, hidethinking=False: getattr(
    response, "_body", ""
)
mu.common_parse_gemini_response_text = lambda js: ""
mu.common_create_gemini_request = lambda *a, **kw: {"_body": "gemini"}
mu.common_create_gpt_data = (
    lambda cfg, msg, eb: {"model": cfg["model"], "messages": msg, **(eb or {})}
)
sys.modules["myutils.utils"] = mu

mp = types.ModuleType("myutils.proxy")
mp.getproxy = lambda *a, **kw: None
sys.modules["myutils.proxy"] = mp

mw = types.ModuleType("myutils.wrapper")
mw.threader = lambda f: (lambda *a, **kw: f(*a, **kw))
mw.stripwrapper = lambda d: d
sys.modules["myutils.wrapper"] = mw

mc = types.ModuleType("myutils.config")
mc.globalconfig = {"fanyi": {}}
mc.translatorsetting = {}
mc.dynamicapiname = lambda x: x
mc._TR = lambda s: s
sys.modules["myutils.config"] = mc


class ArgsEmptyExc(Exception):
    pass


class _Proxysession:
    def __init__(self):
        self.calls = []
        self.queue = []

    def post(self, url, headers=None, json=None, stream=False, **kw):
        self.calls.append({"url": url, "json": json, "stream": stream, "headers": headers})
        if not self.queue:
            raise AssertionError("mock queue empty — unexpected request to " + url)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class commonbase:
    def __init__(self, typename):
        self.typename = typename
        self.proxysession = _Proxysession()

    @property
    def config(self):
        return self._config

    @property
    def rawconfig(self):
        return self._config

    @property
    def proxy(self):
        return None

    def checkempty(self, keys):
        pass

    def smartparselangprompt(self, s):
        return s.replace("{srclang}", "Japanese").replace("{tgtlang}", "Chinese")

    @property
    def multiapikeycurrent(self):
        return {"SECRET_KEY": "PRIMARY_KEY"}

    def _gptlike_createsys(self, usekey, tempk):
        return "You are a translator."

    def _gptlike_get_user_prompt(self, usekey, tempk):
        return "{sentence}"

    def _gptlike_create_prefill(self, usekey, tempk):
        return ""

    def _gpt_common_parse_context(self, out, ctx, n):
        return None


mcb = types.ModuleType("myutils.commonbase")
mcb.commonbase = commonbase
mcb.ArgsEmptyExc = ArgsEmptyExc
sys.modules["myutils.commonbase"] = mcb


# ---- translator.basetranslator stub ----
translator_pkg = types.ModuleType("translator")
translator_pkg.__path__ = [os.path.join(REPO_ROOT, "src", "LunaTranslator", "translator")]
sys.modules["translator"] = translator_pkg


class GptDict:
    def __bool__(self):
        return False

    def __iter__(self):
        return iter([])


class GptTextWithDict:
    def __init__(self, rawtext=None, parsedtext=None, dictionary=None):
        self.rawtext = rawtext or parsedtext
        self.parsedtext = parsedtext or rawtext
        self.dictionary = GptDict()


class basetrans(commonbase):
    pass


bt = types.ModuleType("translator.basetranslator")
bt.basetrans = basetrans
bt.GptDict = GptDict
bt.GptTextWithDict = GptTextWithDict
sys.modules["translator.basetranslator"] = bt


# ---- language stub ----
lang = types.ModuleType("language")


class Languages:
    @staticmethod
    def createenglishlangmap():
        return {}


lang.Languages = Languages
sys.modules["language"] = lang


# ---- shared helpers for tests ----
class FakeResp:
    """Pretends to be requests.Response for _error_to_text and common_parse_normal_response."""

    def __init__(self, text):
        self.text = text
        self._body = text

    def __str__(self):
        return self.text

    def __repr__(self):
        return "FakeResp(" + self.text + ")"


class FakeStreamResp:
    def __init__(self, chunks, final):
        self._chunks = list(chunks)
        self._final = final


BASE_CFG = {
    "API接口地址": "https://primary.example/v1",
    "SECRET_KEY": "PRIMARY_KEY",
    "model": "primary-model",
    "流式输出": False,
    "max_tokens": 512,
    "Temperature": 0.0,
    "Temperature.use": True,
    "top_p": 0.3,
    "top_p_use": True,
    "frequency_penalty": 0,
    "frequency_penalty_use": False,
    "reasoning_effort": "medium",
    "reasoning_effort_use": False,
    "thinking.type": "disabled",
    "thinking.type.use": False,
    "use_max_completion_tokens": False,
    "附带上下文个数": 0,
    "使用自定义promt": False,
    "自定义promt": "",
    "use_user_user_prompt": False,
    "user_user_prompt": "",
    "prefill_use": False,
    "prefill": "",
    "customparams": [],
    "markdown2html": False,
    "fallback.enabled": True,
    "fallback.on_any_error": False,
    "fallback.trigger_regex": "违规|blocked|sensitive",
    "fallback.ignorecase": True,
    "fallback.provider": {
        "API接口地址": "https://fallback.example/v1",
        "SECRET_KEY": "FB_KEY_1|FB_KEY_2",
        "model": "fallback-model",
        "max_tokens": 256,
        "Temperature": 0.5,
        "customparams": [
            {"key": "X-Custom", "value": "abc", "type": "header"},
            {"key": "user", "value": "me", "type": "string"},
        ],
    },
}


def make_translator(cfg):
    """Create a gptcommon instance wired to a mocked config + proxysession."""
    import importlib

    gc = importlib.import_module("translator.gptcommon")
    t = gc.gptcommon("chatgpt-3rd-party")
    t._config = cfg
    return t


def run_translate(t, src="こんにちは"):
    """Drive the translate() generator. Returns (collected_text, reset_count)."""
    import importlib

    gc = importlib.import_module("translator.gptcommon")
    collected = ""
    resets = 0
    for x in t.translate(gc.GptTextWithDict(src)):
        if x == "\0":
            collected = ""
            resets += 1
        elif x:
            collected += x
    return collected, resets
