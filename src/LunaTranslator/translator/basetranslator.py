from traceback import print_exc
from threading import Thread, Lock
import time, types
import gobject
import json
import functools, hashlib
from myutils.wrapper import threader
from myutils.config import globalconfig, translatorsetting, dynamicapiname
from myutils.utils import stringfyerror, PriorityQueue
from myutils.commonbase import ArgsEmptyExc, commonbase


class Interrupted(Exception):
    pass


class RequestCancelled(Exception):
    """请求被外部取消（用户切换到下一句字幕且开启了 cancel_previous_request）。

    与 Interrupted 不同：Interrupted 是"等结果时不等了"（线程还在跑），
    RequestCancelled 是"主动杀掉这个请求"（关 HTTP 连接、退出 generator）。
    translate_and_collect 收到后不写缓存、不 callback 终态。
    """

    pass


class GptDictItem:
    def __init__(self, d: dict = None):
        d = d if d else {}
        self.src = d.get("src")
        self.dst = d.get("dst")
        self.info = d.get("info")


class GptDict:
    def __bool__(self):
        return bool(self.__)

    def __iter__(self):
        for _ in self.L:
            yield _

    def __init__(self, d: "list[dict[str, str]]" = None):
        self.L = [GptDictItem(_) for _ in d] if d else []
        self.__ = d

    def __str__(self):
        return json.dumps(self.__, ensure_ascii=False)

    def __repr__(self):
        return self.__str__()


class GptTextWithDict:
    def __init__(self, rawtext: str = None, parsedtext: str = None, dictionary=None):
        if rawtext and not parsedtext:
            parsedtext = rawtext
        self.parsedtext = parsedtext
        self.dictionary = GptDict(dictionary)
        self.rawtext = rawtext

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        return json.dumps(
            {
                "text": self.parsedtext,
                "gpt_dict": str(self.dictionary),
                "contentraw": self.rawtext,
            },
            ensure_ascii=False,
        )


class Threadwithresult(Thread):
    def __init__(self, func):
        super(Threadwithresult, self).__init__(daemon=True)
        self.func = func
        self.isInterrupted = True
        self.exception = None

    def run(self):
        try:
            self.result = self.func()
        except Exception as e:
            self.exception = e
        self.isInterrupted = False

    def get_result(self, checktutukufunction=None):
        # Thread.join(self,timeout)
        # 不再超时等待，只检查是否是最后一个请求，若是则无限等待，否则立即放弃。
        while checktutukufunction and checktutukufunction() and self.isInterrupted:
            self.join(0.1)

        if self.isInterrupted:
            raise Interrupted()
        elif self.exception:
            raise self.exception
        else:
            return self.result


def timeoutfunction(func, checktutukufunction=None):
    t = Threadwithresult(func)
    t.start()
    return t.get_result(checktutukufunction)


class basetrans(commonbase):
    def langmap(self):
        # The mapping between standard language code and API language code, if not declared, defaults to using standard language code.
        # But the exception is cht. If api support cht, if must be explicitly declared the support of cht, otherwise it will translate to chs and then convert to cht.
        return {}

    def init(self):
        pass

    def translate(self, content: "str|GptTextWithDict"):
        return ""

    ############################################################
    _globalconfig_key = "fanyi"
    _setting_dict = translatorsetting

    def result_cache_key(self, src, tgt, sentence):
        return src, tgt, sentence

    def __init__(self, typename):
        super().__init__(typename)
        if (self.transtype == "offline") and (not self.is_gpt_like):
            self.gconfig["useproxy"] = False
        self.queue = PriorityQueue()
        try:
            self._private_init()
        except Exception as e:
            gobject.base.displayinfomessage(
                dynamicapiname(self.typename)
                + " init translator failed : "
                + str(stringfyerror(e)),
                "<msg_error_Translator>",
            )
            print_exc()

        self.lastrequesttime = 0
        self._cache = {}

        self.newline = None

        # 取消机制：generation 计数 + 当前 response 追踪
        # cancel_previous() 会让所有旧 generation 的请求立刻退出（不 fallback、不缓存）
        self._cancel_gen = 0
        self._current_response = None
        self._cancel_lock = Lock()

        threader(self._fythread)()

    def _private_init(self):
        self.initok = False
        self.init()
        self.initok = True

    # ---------------- 取消机制 ----------------

    def cancel_previous(self):
        """取消当前在飞的请求（如果有）：bump generation + 关 response。

        之后所有旧 generation 的 translate() 会在下一个检查点 return，
        _do_request 也会拒绝注册新 response 并抛 RequestCancelled。
        """
        with self._cancel_lock:
            self._cancel_gen += 1
            r = self._current_response
            self._current_response = None
        if r is not None:
            try:
                r.close()
            except Exception:
                pass

    def _new_request_gen(self):
        """translate() 启动时调用，拿到当前 generation 作为自己的身份。"""
        with self._cancel_lock:
            return self._cancel_gen

    def _is_cancelled(self, gen) -> bool:
        with self._cancel_lock:
            return self._cancel_gen != gen

    def _register_response(self, gen, response) -> bool:
        """把 response 登记为"当前在飞"。若 generation 已过期，直接关 response 并返回 False。"""
        with self._cancel_lock:
            if self._cancel_gen != gen:
                try:
                    response.close()
                except Exception:
                    pass
                return False
            self._current_response = response
            return True

    def _unregister_response(self, response):
        with self._cancel_lock:
            if self._current_response is response:
                self._current_response = None

    @property
    def using_gpt_dict(self):
        # 决定translator接口传入GptTextWithDict还是str
        return self.gconfig.get("is_gpt_like", False)

    @property
    def use_trans_cache(self):
        return (self.gconfig.get("use_trans_cache", True)) and (
            not self.transtype in ("pre", "other")
        )

    @property
    def is_gpt_like(self):
        return self.gconfig.get("is_gpt_like", False)

    @property
    def onlymanual(self):
        # Only used during manual translation, not used during automatic translation
        return self.gconfig.get("manual", False)

    @property
    def using(self):
        return self.gconfig["use"]

    @property
    def transtype(self):
        # free/dev/api/offline/pre
        # dev/offline 无视请求间隔
        # pre全都有额外的处理，不走该pipeline，不使用翻译缓存
        # offline不被新的请求打断
        return self.gconfig.get("type", "free")

    def gettask(self, content):
        # fmt: off
        callback, contentsolved, callback, is_auto_run, optimization_params = content
        # fmt: on
        if callback:
            priority = 1
        else:
            priority = 0
        self.queue.put(content, priority)

    def shortorlongcacheget(self, content, is_auto_run):
        if self.is_gpt_like and not is_auto_run:
            return None, None
        if not self.use_trans_cache:
            return None, None
        key = hashlib.md5(
            str(self.result_cache_key(self.srclang_1, self.tgtlang_1, content)).encode()
        ).digest()
        res = self._cache.get(key)
        if res:
            return res, key
        return None, key

    def __cap_trans(self, t):
        if isinstance(t, GptTextWithDict) and (
            "_compatible_flag_is_sakura_less_than_5_52_3" in dir(self)
        ):
            t = str(t)
        return self.translate(t)

    def intervaledtranslate(self, content):
        interval = globalconfig["requestinterval"]
        current = time.time()
        self.current = current
        sleeptime = interval - (current - self.lastrequesttime)

        if sleeptime > 0:
            time.sleep(sleeptime)
        self.lastrequesttime = time.time()
        if (current != self.current) or (self.using == False):
            raise Exception()

        return self.multiapikeywrapper(self.__cap_trans)(content)

    def _gptlike_createquery(self, query, usekey, tempk):
        return self._gptlike_get_user_prompt(usekey, tempk).replace("{sentence}", query)

    def _gptlike_get_user_prompt(self, usekey, tempk):
        user_prompt = (
            self.config.get(tempk, "") if self.config.get(usekey, False) else ""
        )
        default = "{DictWithPrompt[When translating, please ensure to translate the specified nouns into the translations I have designated: ]}\n{sentence}"
        user_prompt = user_prompt if user_prompt else default
        if "{sentence}" not in user_prompt:
            user_prompt += "{sentence}"
        return user_prompt

    def _gptlike_createsys(self, usekey, tempk):

        default = "You are a translator. Please help me translate the following {srclang} text into {tgtlang}. You should only tell me the translation result without any additional explanations."
        template = self.config[tempk] if self.config.get(usekey, False) else None
        template = template if template else default
        template = self.smartparselangprompt(template)
        return template

    def _gptlike_create_prefill(self, usekey, tempk):
        user_prompt = (
            self.config.get(tempk, "") if self.config.get(usekey, False) else ""
        )
        return user_prompt

    def _gpt_common_parse_context(
        self, messages: list, context: "list[dict]", num: int
    ):
        offset = 0
        _i = 0
        msgs = []
        while (_i + offset < (len(context) // 2)) and (_i < num):
            i = len(context) // 2 - _i - offset - 1
            msgs.append(context[i * 2 + 1])
            msgs.append(context[i * 2])
            _i += 1
        messages.extend(reversed(msgs))

    def maybeneedreinit(self):
        if not (self.needreinit or not self.initok):
            return
        self.needreinit = False
        self.renewsesion()
        try:
            self._private_init()
        except Exception as e:
            raise Exception("init translator failed : " + str(stringfyerror(e)))

    def maybezhconvwrapper(self, callback, tgtlang_1):
        def __maybeshow(callback, tgtlang_1, res, is_iter_res):
            if self.needzhconv:
                res = self.checklangzhconv(tgtlang_1, res)
            callback(res, is_iter_res)

        return functools.partial(__maybeshow, callback, tgtlang_1)

    def translate_and_collect(
        self, tgtlang_1, contentsolved: "GptTextWithDict|str", is_auto_run, callback
    ):
        if isinstance(contentsolved, GptTextWithDict):
            cache_use = contentsolved.rawtext
            if contentsolved.dictionary:
                cache_use = str((contentsolved, contentsolved.dictionary))
            TS_use = contentsolved
        else:
            cache_use = TS_use = contentsolved
        res, cachekey = self.shortorlongcacheget(cache_use, is_auto_run)
        if not res:
            res = self.intervaledtranslate(TS_use)
        # 不能因为被打断而放弃后面的操作，发出的请求不会因为不再处理而无效，所以与其浪费不如存下来
        # gettranslationcallback里已经有了是否为当前请求的校验，这里无脑输出就行了

        callback = self.maybezhconvwrapper(callback, tgtlang_1)
        if isinstance(res, types.GeneratorType):
            collectiterres = ""
            try:
                for _res in res:
                    if _res == "\0":
                        collectiterres = ""
                    elif _res:  # 可能为None
                        collectiterres += _res
                    callback(collectiterres, 1)
            except RequestCancelled:
                # 请求被用户切换字幕时取消：不写缓存、不发终态 callback
                return
            callback(collectiterres, 2)
            res = collectiterres

        else:
            if globalconfig.get("fix_translate_rank", False):
                # 这个性能会稍微差一点，不然其实可以全都这样的。
                callback(res, 1)
                callback(res, 2)
            else:
                callback(res, 0)

        # 保存缓存
        # 不管是否使用翻译缓存，都存下来
        self._cache[cachekey] = res

    def __parse_gpt_dict(self, contentsolved, optimization_params):
        gpt_dict = []
        contentraw = contentsolved
        for _ in optimization_params:
            if isinstance(_, dict):
                _gpt_dict = _.get("gpt_dict", None)
                if not _gpt_dict:
                    continue
                gpt_dict = _gpt_dict
                contentraw = _.get("gpt_dict_origin")
                break

        return GptTextWithDict(
            parsedtext=contentsolved, dictionary=gpt_dict, rawtext=contentraw
        )

    def _fythread(self):
        self.needreinit = False
        while self.using:

            content = self.queue.get()
            if not self.using:
                break
            if content is None:
                break
            # fmt: off
            callback, contentsolved, waitforresultcallback, is_auto_run, optimization_params = content
            # fmt: on
            if self.onlymanual and is_auto_run:
                continue
            if self.srclang_1 == self.tgtlang_1:
                callback(None, 0)
                continue
            try:
                checktutukufunction = (
                    lambda: ((waitforresultcallback is not None) or self.queue.empty())
                    and self.using
                )
                if not checktutukufunction():
                    # 检查请求队列是否空，请求队列有新的请求，则放弃当前请求。但对于内嵌翻译请求，不可以放弃。
                    continue

                self.maybeneedreinit()

                if self.using_gpt_dict:
                    contentsolved = self.__parse_gpt_dict(
                        contentsolved, optimization_params
                    )

                func = functools.partial(
                    self.translate_and_collect,
                    self.tgtlang_1,
                    contentsolved,
                    is_auto_run,
                    callback,
                )
                if self.transtype == "offline":
                    # 离线翻译例如sakura不要被中断，因为即使中断了，部署的服务仍然在运行，直到请求结束
                    func()
                else:
                    # 用户点到下一句字幕：可选立刻取消上一个在飞的请求
                    # （默认关，保留"后台跑完写缓存"的老行为；打开后翻字幕快时不再堆积挂住的请求）
                    if self.config.get("cancel_previous_request", False):
                        self.cancel_previous()
                    timeoutfunction(
                        func,
                        checktutukufunction=checktutukufunction,
                    )
            except Exception as e:
                if not (self.using):
                    continue
                if isinstance(e, ArgsEmptyExc):
                    msg = str(e)
                elif isinstance(e, Interrupted):
                    # 因为有新的请求而被打断
                    continue
                else:
                    print_exc()
                    msg = stringfyerror(e)
                    self.needreinit = True
                callback(msg, 0, True)
