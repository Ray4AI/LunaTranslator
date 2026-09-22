from translator.basetranslator import basetrans, GptTextWithDict, GptDict
import json, requests, hmac, hashlib, NativeUtils, re, functools
from datetime import datetime, timezone
from myutils.utils import (
    APIType,
    common_list_models,
    common_parse_normal_response,
    common_parse_gemini_response_text,
    common_create_gemini_request,
    common_create_gpt_data,
)
from myutils.proxy import getproxy
from language import Languages
from gui.customparams import getcustombodyheaders

# 为了 fallback 子对话框里能够加载 customparams（参见 gui.customparams.fallbackproviderbutton）
from gui.customparams import customparams, fallbackproviderbutton  # noqa: F401


class _ConfigView:
    """配置视图：在 base（主配置）上叠一层 overrides（如 fallback.provider）。
    只覆盖 overrides 中出现的键，其它键仍读 base，用于将同一份请求代码复用到 fallback 通道。
    """

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


class _FallbackTriggered(Exception):
    """流式过程中检测到 fallback 触发条件，需要立刻中断当前流。

    `partial` 是到检测点为止已经流出来的文本（可能包含审查关键词）。
    顶层 translate() 捕获后应该 `yield "\\0"` 立刻撤回 UI，然后切换到 fallback。
    """

    def __init__(self, partial: str):
        self.partial = partial
        super().__init__("fallback triggered")


def list_models(typename, regist: dict):
    return common_list_models(
        getproxy(("fanyi", typename)),
        APIType(regist["API接口地址"]()),
        regist.get("SECRET_KEY", lambda: "")().split("|")[0],
    )


def stream_event_parser(response: requests.Response):

    for response_data in response.iter_lines(decode_unicode=True):
        response_data = response_data.strip()
        if not response_data:
            continue
        if not response_data.startswith("data: "):
            continue
        response_data = response_data[6:]
        if response_data == "[DONE]":
            break
        try:
            json_data: dict = json.loads(response_data)
        except:
            raise Exception(response_data)
        yield json_data


def commonparseresponse_good(
    response: requests.Response,
    hidethinking: bool,
    markdown2html: bool,
    getmodelhook: list = None,
):
    message = ""
    thinkcnt = 0
    isthinking = False
    isreasoning_content = False
    unsafethinkonce = True
    for json_data in stream_event_parser(response):
        if getmodelhook is not None and json_data.get("model"):
            getmodelhook.append(json_data.get("model"))
        try:
            if len(json_data["choices"]) == 0:
                continue
            delta: dict = json_data["choices"][0].get("delta", {})
            msg: str = delta.get("content", None)
            reasoning_content: str = delta.get("reasoning_content", None)
            if reasoning_content:
                if hidethinking:
                    isreasoning_content = True
                    thinkcnt += len(reasoning_content)
                    yield "\0"
                    yield "thinking {} ...".format(thinkcnt)
                else:
                    yield reasoning_content
            elif msg:
                if isreasoning_content:
                    isreasoning_content = False
                    yield "\0"
                if hidethinking and (msg.strip() == "<think>"):
                    yield "thinking ..."
                    isthinking = True
                elif hidethinking and isthinking:
                    if msg.strip() == "</think>":
                        isthinking = False
                        yield "\0"
                    else:
                        thinkcnt += len(msg)
                        yield "\0"
                        yield "thinking {} ...".format(thinkcnt)

                else:
                    # 有时，会没有<think>只有</think>比如使用prefill的时候。移除第一个</think>之前的内容
                    if hidethinking and unsafethinkonce and (msg.strip() == "</think>"):
                        isthinking = False
                        message = ""
                        unsafethinkonce = False
                        yield "\0"
                    elif hidethinking and (not message) and (not msg.strip()):
                        # 跳过</think>后的\n
                        pass
                    else:
                        message += msg
                        if markdown2html:
                            _msg = NativeUtils.Markdown2Html(message)
                            yield "\0"
                            yield "LUNASHOWHTML" + _msg
                        else:
                            yield msg
            rs = json_data["choices"][0].get("finish_reason")
            if rs and rs != "null" and not (msg or reasoning_content):
                break
        except:
            raise Exception(json_data)
    return message


def parseresponsegemini(response: requests.Response, markdown2html: bool):
    line = ""
    _gemini_text_line = re.compile(r'^"text"\s*:\s*("(?:\\.|[^"\\])*")\s*,?$')
    for __x in response.iter_lines(decode_unicode=True):
        __x = __x.strip()
        if not __x:
            continue
        if __x.startswith("data: "):
            __x = __x[6:].strip()
            if __x == "[DONE]":
                break
        try:
            text = common_parse_gemini_response_text(json.loads(__x))
        except:
            text = None
        if text is None:
            match = _gemini_text_line.match(__x)
            if not match:
                continue
            try:
                text = json.loads(match.group(1))
            except:
                # Some Gemini variants add non-text fields around text chunks.
                continue
        if not text:
            continue
        line += text
        if markdown2html:
            _msg = NativeUtils.Markdown2Html(line)
            yield "\0"
            yield "LUNASHOWHTML" + _msg
        else:
            yield text
    return line


def parseresponseclaude(response: requests.Response):
    message = ""
    for response_data in response.iter_lines(decode_unicode=True):
        response_data = response_data.strip()
        if not response_data:
            continue
        if response_data.startswith("data: "):
            try:
                json_data = json.loads(response_data[6:])
                if json_data["type"] == "message_stop":
                    break
                elif json_data["type"] == "content_block_delta":
                    msg = json_data["delta"]["text"]
                    message += msg
                elif json_data["type"] == "content_block_start":
                    msg = json_data["content_block"]["text"]
                    message += msg
                else:
                    continue
            except:
                raise Exception(response_data)
            yield msg
    return message


def parseresponseQWENMT(response: requests.Response):
    message = ""
    for json_data in stream_event_parser(response):
        try:
            if len(json_data["choices"]) == 0:
                continue
            delta: dict = json_data["choices"][0].get("delta", {})
            msg: str = delta.get("content", None)
            if msg:
                if msg.startswith(message):
                    yield msg[len(message) :]
                else:
                    yield "\0"
                    yield msg
                message = msg
            rs = json_data["choices"][0].get("finish_reason")
            if rs and rs != "null":
                break
        except:
            raise Exception(json_data)
    return message


def parsestreamresp(
    apitype: APIType,
    response: requests.Response,
    hidethinking: bool,
    markdown2html: bool,
    model: str,
    getmodelhook=None,
):
    if (response.status_code != 200) and (
        not response.headers.get("Content-Type", "").startswith("text/event-stream")
    ):
        # application/json
        # text/html
        raise Exception(response)
    if apitype == APIType.gemini:
        respmessage = yield from parseresponsegemini(response, markdown2html)
    elif apitype == APIType.claude:
        respmessage = yield from parseresponseclaude(response)
    elif model.startswith("qwen-mt-") and apitype == APIType.aliyuncs:
        respmessage = yield from parseresponseQWENMT(response)
    else:
        respmessage = yield from commonparseresponse_good(
            response, hidethinking, markdown2html, getmodelhook=getmodelhook
        )
    return respmessage


class qianfanIAM:

    @staticmethod
    def sign(access_key_id: str, secret_access_key: str):
        now = datetime.now(timezone.utc)
        canonical_time = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        sign_key_info = "bce-auth-v1/{}/{}/8640000".format(
            access_key_id, canonical_time
        )
        sign_key = hmac.new(
            secret_access_key.encode(), sign_key_info.encode(), hashlib.sha256
        ).hexdigest()
        string_to_sign = "GET\n/v1/BCE-BEARER/token\nexpireInSeconds=8640000\nhost:iam.bj.baidubce.com"
        sign_result = hmac.new(
            sign_key.encode(), string_to_sign.encode(), hashlib.sha256
        ).hexdigest()
        return "{}/host/{}".format(sign_key_info, sign_result)

    @staticmethod
    def getkey(ak: str, sk: str, proxy):
        headers = {
            "Host": "iam.bj.baidubce.com",
            "Authorization": qianfanIAM.sign(ak, sk),
        }
        return requests.get(
            "https://iam.bj.baidubce.com/v1/BCE-BEARER/token",
            params={"expireInSeconds": 8640000},
            headers=headers,
            proxies=proxy,
        ).json()["token"]


def createheaders(apitype: APIType, curkey: str, maybeuse: dict, proxy, extra):
    _ = {}
    if curkey:
        # 部分白嫖接口可以不填，填了反而报错
        _.update({"Authorization": "Bearer " + curkey})
    if apitype == APIType.azure:
        _.update({"api-key": curkey})
    elif (apitype == APIType.qianfan) and (":" in curkey):
        if not maybeuse.get(curkey):
            Access_Key, Secret_Key = curkey.split(":")
            key = qianfanIAM.getkey(Access_Key, Secret_Key, proxy)
            maybeuse[curkey] = key
        _.update({"Authorization": "Bearer " + maybeuse[curkey]})
    if extra:
        _.update(extra)
    return _


class gptcommon(basetrans):
    def langmap(self):
        return Languages.createenglishlangmap()

    def result_cache_key(self, src, tgt, sentence):
        __ = {}
        __.update(self.rawconfig)
        # 纯 UI/缓存类字段不参与 key，否则刷新模型列表会清翻译缓存
        for _k in ("modellistcache", "fallback.provider"):
            if _k in __:
                __.pop(_k)
        return (
            src,
            tgt,
            sentence,
            str(__),
        )

    def __init__(self, typename):
        self._context = []
        self._context_skipinter = 0
        self._context_skipinter_shouldmove = False
        self.maybeuse = {}
        self._fallback_key_idx = 0
        super().__init__(typename)

    # ---------------- Fallback 相关 ----------------

    def _fallback_provider(self) -> dict:
        fb = self.config.get("fallback.provider")
        return fb if isinstance(fb, dict) else {}

    def _fallback_patterns(self) -> "list[str]":
        raw = self.config.get("fallback.trigger_regex") or ""
        return [ln.strip() for ln in raw.splitlines() if ln.strip()]

    def _matches_fallback_regex(self, text) -> bool:
        pats = self._fallback_patterns()
        if not pats or not text:
            return False
        flags = re.IGNORECASE if self.config.get("fallback.ignorecase", True) else 0
        for p in pats:
            try:
                if re.search(p, text, flags):
                    return True
            except re.error:
                continue
        return False

    @staticmethod
    def _error_to_text(e: Exception) -> str:
        parts = []
        for arg in getattr(e, "args", []):
            if isinstance(arg, requests.Response):
                try:
                    parts.append(arg.text)
                except Exception:
                    parts.append(repr(arg))
            else:
                parts.append(str(arg))
        if not parts:
            parts.append(str(e))
        return "\n".join(parts)

    def _should_fallback_on_response(self, msg) -> bool:
        if not self.config.get("fallback.enabled", False):
            return False
        return self._matches_fallback_regex(msg)

    def _should_fallback_on_error(self, e: Exception) -> bool:
        if not self.config.get("fallback.enabled", False):
            return False
        if self.config.get("fallback.on_any_error", False):
            return True
        return self._matches_fallback_regex(self._error_to_text(e))

    def _next_fallback_key(self, raw) -> str:
        keys = [k.strip() for k in (raw or "").split("|") if k.strip()]
        if not keys:
            return ""
        self._fallback_key_idx = (self._fallback_key_idx + 1) % len(keys)
        return keys[self._fallback_key_idx]

    def _mkattempt(
        self, api_url, api_key, model, cfg_overrides, extrabody, extraheader, is_fallback
    ):
        return {
            "api_url": api_url,
            "api_key": api_key,
            "model": model,
            "cfgview": _ConfigView(self.config, cfg_overrides),
            "extrabody": extrabody,
            "extraheader": extraheader,
            "is_fallback": is_fallback,
        }

    def _mkfallbackattempt(self):
        """构造 fallback 请求参数。未启用或未配置完整时返回 None。"""
        if not self.config.get("fallback.enabled", False):
            return None
        fbcfg = self._fallback_provider()
        api_url = (fbcfg.get("API接口地址") or "").strip()
        model = (fbcfg.get("model") or "").strip()
        if not api_url or not model:
            return None
        api_key = self._next_fallback_key(fbcfg.get("SECRET_KEY"))
        # fallback 自己的 extrabody/headers（与主提供商独立）
        extrabody, extraheader = getcustombodyheaders(
            fbcfg.get("customparams") or [],
            config=self.config,
            fbcfg=fbcfg,
            self=self,
        )
        return self._mkattempt(
            api_url, api_key, model, fbcfg, extrabody, extraheader, True
        )

    # ---------------- 请求 ----------------

    def _request_gemini_ex(
        self, apitype, cfg, api_key, messages: list, extrabody, extraheader
    ):
        sysprompt = messages[0]["content"]
        messages.pop(0)
        for i, item in enumerate(messages):
            messages[i] = {
                "role": {"assistant": "model", "user": "user"}[item["role"]],
                "parts": [{"text": item["content"]}],
            }
        return common_create_gemini_request(
            self.proxysession,
            cfg,
            api_key,
            sysprompt,
            messages,
            extraheader,
            extrabody,
            apitype,
        )

    def _req_claude_ex(self, cfg, api_key, messages: list, extrabody, extraheader):
        sysprompt = messages[0]["content"]
        messages.pop(0)
        cache_control = cfg.get("cachecontext", True)
        if cache_control and isinstance(sysprompt, str):
            sysprompt = [
                {
                    "type": "text",
                    "text": sysprompt,
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }
            ]
        headers = {
            "anthropic-version": "2023-06-01",
            "accept": "application/json",
            "X-Api-Key": api_key,
        }
        usingstream = cfg.get("流式输出", False)
        data = dict(
            model=cfg["model"],
            messages=messages,
            system=sysprompt,
            max_tokens=cfg["max_tokens"],
            stream=usingstream,
        )
        if cfg.get("Temperature.use", True):
            data.update(temperature=cfg["Temperature"])
        headers.update(extraheader)
        data.update(extrabody)
        response = self.proxysession.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=data,
            stream=usingstream,
        )
        return response

    def _do_request(self, att: dict, messages: list, usingstream: bool):
        apitype = APIType(att["api_url"])
        cfg = att["cfgview"]
        # gemini / claude 分支会原地修改 messages，这里传副本
        _messages = [dict(m) for m in messages]
        if apitype == APIType.gemini:
            response = self._request_gemini_ex(
                apitype, cfg, att["api_key"], _messages,
                att["extrabody"], att["extraheader"],
            )
        elif apitype == APIType.claude:
            response = self._req_claude_ex(
                cfg, att["api_key"], _messages,
                att["extrabody"], att["extraheader"],
            )
        else:
            headers = createheaders(
                apitype, att["api_key"], self.maybeuse, self.proxy, att["extraheader"]
            )
            _json = common_create_gpt_data(
                cfg, self.__parse_qwen_mt_turbo(apitype, _messages), att["extrabody"]
            )
            response = self.proxysession.post(
                apitype.finalurl(), headers=headers, json=_json, stream=usingstream
            )
        return response, apitype

    def _stream_with_check(self, gen, check_fn):
        """包装 parsestreamresp 的 generator：每 yield 一个 chunk 就检测累积文本。

        一旦 `check_fn(累积文本)` 返回 True：
          - 先 yield 当前 chunk（用户能看到触发关键词，不会莫名其妙被擦）
          - 然后关闭内部 generator 并抛 `_FallbackTriggered`
          - translate() 捕获后 `yield "\\0"` 立刻擦掉，切 fallback
        这样能在主模型刚开始吐审查/拦截文案时就中断，不用等整段流完。
        """
        accumulated = ""
        while True:
            try:
                chunk = next(gen)
            except StopIteration as si:
                # generator 自然结束，透传 return value（完整 message）
                return si.value
            should_abort = False
            trigger_display = ""
            if chunk == "\0":
                accumulated = ""
            elif chunk:
                accumulated += chunk
                # markdown2html 模式下 chunk 前缀是 LUNASHOWHTML，检测时去掉
                display = accumulated
                if display.startswith("LUNASHOWHTML"):
                    display = display[len("LUNASHOWHTML") :]
                if check_fn(display):
                    should_abort = True
                    trigger_display = display
            # 总是 yield 当前 chunk（包括触发的那一个）
            yield chunk
            if should_abort:
                try:
                    gen.close()
                except Exception:
                    pass
                raise _FallbackTriggered(trigger_display)

    def translate(self, query_2: GptTextWithDict):
        self.checkempty("API接口地址")
        if isinstance(query_2, str):
            query_2 = GptTextWithDict(query_2)
        extrabody, extraheader = getcustombodyheaders(
            self.config.get("customparams"), **locals()
        )
        usingstream = self.config["流式输出"]
        messages, query, query_1 = self.commoncreatemessages(query_2)
        hidethinking = self.config.get("hidethinking", True)
        markdown2html = self.config.get("markdown2html", False)

        # 依次尝试：主提供商 -> fallback 提供商
        attempts = [
            self._mkattempt(
                self.config["API接口地址"],
                self.multiapikeycurrent["SECRET_KEY"],
                self.config["model"],
                {},
                extrabody,
                extraheader,
                False,
            )
        ]
        fb = self._mkfallbackattempt()
        if fb is not None:
            attempts.append(fb)

        respmessage = None
        primary_msg = None  # 主模型解析出的完整内容（可能包含审查关键词）
        primary_yielded = False  # 主模型内容是否已经 yield 到 UI

        for idx, att in enumerate(attempts):
            is_fb = att["is_fallback"]
            if is_fb and primary_yielded:
                # 切到 fallback 前清空 UI，避免残留审查/拦截提示文本
                yield "\0"
                primary_yielded = False
            try:
                response, apitype = self._do_request(att, messages, usingstream)
                if usingstream:
                    gen = parsestreamresp(
                        apitype, response, hidethinking, markdown2html, att["model"]
                    )
                    if (not is_fb) and (idx + 1 < len(attempts)):
                        # 主模型 + 已配置 fallback：边流边检测，命中立刻撤回
                        # 注意：`yield from` 中途 raise 会跳出这里，所以 primary_yielded
                        # 要提前置 True（只要走过流式就假设 UI 上已有内容）
                        primary_yielded = True
                        msg = yield from self._stream_with_check(
                            gen, self._matches_fallback_regex
                        )
                    else:
                        msg = yield from gen
                        if not is_fb:
                            primary_yielded = True
                else:
                    msg = common_parse_normal_response(
                        response, apitype, hidethinking=hidethinking
                    )
            except _FallbackTriggered as ft:
                # 流式中途命中审查/拦截正则：立刻撤回 UI，然后切 fallback
                # 不等 fallback 开始输出才覆盖 —— 就在检测点上马上清空。
                yield "\0"
                primary_yielded = False
                primary_msg = ft.partial
                try:
                    response.close()
                except Exception:
                    pass
                continue
            except Exception as e:
                # 请求/解析出错：符合条件则切到 fallback
                if (
                    (not is_fb)
                    and (idx + 1 < len(attempts))
                    and self._should_fallback_on_error(e)
                ):
                    continue
                if is_fb and primary_msg is not None:
                    # fallback 也失败，但主模型至少有内容，展示主模型结果
                    respmessage = primary_msg
                    yield "\0"
                    if markdown2html:
                        yield "LUNASHOWHTML" + NativeUtils.Markdown2Html(respmessage)
                    else:
                        yield respmessage
                    break
                raise

            # 正则命中审查/拦截关键词：切到 fallback
            if (
                (not is_fb)
                and (idx + 1 < len(attempts))
                and self._should_fallback_on_response(msg)
            ):
                primary_msg = msg
                continue

            # 成功（或 fallback 已经拿到结果）
            respmessage = msg
            if not usingstream:
                if is_fb and primary_yielded:
                    yield "\0"
                    primary_yielded = False
                if markdown2html:
                    yield "LUNASHOWHTML" + NativeUtils.Markdown2Html(respmessage)
                else:
                    yield respmessage
            break

        if not (respmessage and query_1.strip() and respmessage.strip()):
            return
        # 改为始终使用query来作为history请求
        self._context.append({"role": "user", "content": query, "query_1": query_1})
        self._context.append({"role": "assistant", "content": respmessage})

    def __parse_qwen_mt_turbo(self, apitype: APIType, messages: list):
        if self.config["model"].startswith("qwen-mt-") and apitype == APIType.aliyuncs:
            if messages and messages[0]["role"] == "system":
                messages.pop(0)
        return messages

    def __replace_history(self, which, match: re.Match):
        n = (
            self.config["附带上下文个数"]
            if match.group(1) == "N"
            else int(match.group(1))
        )
        __message: "list[dict]" = []
        self._gpt_common_parse_context(__message, self._context, n)
        check = lambda k: (which == 2) or (k == ("user", "assistant")[which])
        __message = [
            _.get("query_1", _.get("content"))
            for _ in __message
            if (check(_.get("role")))
        ]
        return "\n".join(__message)

    def __parsecontextN(self, query):
        for k, b in (
            (r"\{contextOriginal\[(N|[\d+])\]\}", 0),
            (r"\{contextTranslation\[(N|[\d+])\]\}", 1),
            (r"\{contextBoth\[(N|[\d+])\]\}", 2),
        ):
            query = re.sub(k, functools.partial(self.__replace_history, b), query)
        return query

    def __if_has_dwp(self, dictionary: GptDict, prompt):
        _has = re.search(r"\{DictWithPrompt(.*?)\[(.*?)\]\}", prompt)
        if _has:

            def __rep(m: re.Match):
                tabsplit = m.groups()[0] == "TabSplit"
                nextc = m.groups()[2]
                if not dictionary:
                    if nextc == "\n":
                        return ""
                    return nextc
                __ = []
                for _ in dictionary:
                    info = ("", (" #{}", "\t{}")[tabsplit].format(_.info))[bool(_.info)]
                    single = (
                        "{}{}{}{}".format(
                            ("\t", "")[tabsplit], _.src, ("->", "\t")[tabsplit], _.dst
                        )
                        + info
                    )
                    __.append(single)
                if nextc != "\n":
                    nextc = "\n" + nextc
                pro: str = m.groups()[1]
                if not pro.endswith("\n"):
                    pro += "\n"
                return pro + "\n".join(__) + nextc

            prompt = re.sub(r"\{DictWithPrompt(.*?)\[(.*?)\]\}([\s\S]?)", __rep, prompt)
        return prompt, bool(_has)

    def __gpt_create_query_maybe_with_dict(self, query_2: GptTextWithDict, _has_1):

        user_prompt = self._gptlike_get_user_prompt(
            "use_user_user_prompt", "user_user_prompt"
        )
        user_prompt, _has = self.__if_has_dwp(query_2.dictionary, user_prompt)
        _has = _has or _has_1
        query_1 = (query_2.parsedtext, query_2.rawtext)[_has]
        query = user_prompt.replace("{sentence}", query_1)
        query = self.__parsecontextN(query)
        return query, query_1

    def commoncreatemessages(self, query_2: GptTextWithDict):
        sysprompt = self._gptlike_createsys("使用自定义promt", "自定义promt")
        sysprompt, _has = self.__if_has_dwp(query_2.dictionary, sysprompt)
        query, query_1 = self.__gpt_create_query_maybe_with_dict(query_2, _has)
        sysprompt = self.__parsecontextN(sysprompt)
        message = [{"role": "system", "content": sysprompt}]
        checknum = self.config["附带上下文个数"]
        __message = []
        if self.config.get("cachecontext", True):
            if self._context_skipinter_shouldmove:
                self._context_skipinter = len(self._context) - (checknum // 2) * 2
                self._context_skipinter_shouldmove = False
            if len(self._context) < checknum:
                self._context_skipinter = 0
            self._gpt_common_parse_context(
                __message,
                self._context[self._context_skipinter :],
                checknum,
            )
        self._context_skipinter_shouldmove = len(__message) == checknum * 2
        message.extend(__message)
        message.append({"role": "user", "content": query})
        prefill = self._gptlike_create_prefill("prefill_use", "prefill")
        if prefill:
            message.append({"role": "assistant", "content": prefill})
        return message, query, query_1

    def request_gemini(self, apitype, messages: list, extrabody, extraheader):
        # 保留原有签名供外部调用（内部转发到参数化版本）
        return self._request_gemini_ex(
            apitype,
            self.config,
            self.multiapikeycurrent["SECRET_KEY"],
            messages,
            extrabody,
            extraheader,
        )

    def req_claude(self, messages: list, extrabody, extraheader, cache_control):
        # 保留原有签名供外部调用（内部转发到参数化版本）
        return self._req_claude_ex(
            self.config,
            self.multiapikeycurrent["SECRET_KEY"],
            messages,
            extrabody,
            extraheader,
        )
