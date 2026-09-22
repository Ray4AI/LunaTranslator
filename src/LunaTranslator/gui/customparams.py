from qtsymbols import *
import functools, json, re, ast
from traceback import print_exc
from myutils.wrapper import tryprint
from gui.usefulwidget import (
    getIconButton,
    VisGridLayout,
    SuperCombo,
    FocusDoubleSpin,
    FocusSpin,
    MySwitch,
)


class typeswitcheditor(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cacheswitchtypevalue = {}
        self.t = None
        self.l = QHBoxLayout(self)
        self.l.setContentsMargins(0, 0, 0, 0)
        self.w: "FocusDoubleSpin|FocusSpin|QLineEdit|MySwitch|QPlainTextEdit" = None

    def gettype(self):
        return self.t

    @tryprint
    def settype(self, t):
        if self.w:
            self.cacheswitchtypevalue[self.t] = self.getvalue()
            self.w.deleteLater()
        self.t = t
        if t == "int":
            self.w = FocusSpin()
            self.w.setMaximum(0x7FFFFFFF)
            self.w.setMinimum(-0x7FFFFFFF)
        elif t == "number":
            self.w = FocusDoubleSpin()
            self.w.setMaximum(0x7FFFFFFF)
            self.w.setMinimum(-0x7FFFFFFF)
        elif t == "bool":
            self.w = MySwitch()
        elif t == "other":
            self.w = QPlainTextEdit()
        else:
            self.w = QLineEdit()
        self.l.addWidget(self.w)
        self.setvalue(self.cacheswitchtypevalue.get(self.t))

    @tryprint
    def setvalue(self, v):
        if not self.w:
            return
        if v is None:
            return
        if self.t == "int":
            self.w.setValue(v)
        elif self.t == "number":
            self.w.setValue(v)
        elif self.t == "bool":
            self.w.setChecked(v)
        elif self.t == "other":
            self.w.setPlainText(v)
        else:
            self.w.setText(v)

    @tryprint
    def getvalue(self):
        if not self.w:
            return
        if self.t == "int":
            return self.w.value()
        elif self.t == "number":
            return self.w.value()
        elif self.t == "bool":
            return self.w.isChecked()
        elif self.t == "other":
            return self.w.toPlainText()
        else:
            return self.w.text()


class customparams(QWidget):
    def createline(self, lay: VisGridLayout, i, d: dict):
        k = d.get("key", "")
        v = d.get("value")
        t_ = d.get("type", "string" if self.stringonly else "number")
        self.ks.insert(i, QLineEdit(k))
        ts = typeswitcheditor()
        ts.settype(t_)
        ts.setvalue(v)
        self.vs.insert(i, ts)
        lb = QLabel(":")
        icon = getIconButton(icon="fa.times")

        def __(ts: typeswitcheditor, t: SuperCombo):
            ts.settype(t.getCurrentData())

        if not self.stringonly:
            vs = ["字符串", "数值", "整数", "布尔", "json/python", "Header"]
            vvs = ["string", "number", "int", "bool", "other", "header"]
            if not self.needheader:
                vs.pop(-1)
                vvs.pop(-1)
            t = SuperCombo()
            t.addItems(items=vs, internals=vvs)

            t.setCurrentData(t_)
            t.currentIndexChanged.connect(functools.partial(__, ts, t))
            ws = (self.ks[i], lb, self.vs[i], t, icon)
        else:
            ws = (self.ks[i], lb, self.vs[i], icon)
        for j in range(len(ws)):
            lay.addWidget(ws[j], i, j)
        icon.clicked.connect(functools.partial(lay.setRowVisible, i, False))

    def addline(self, lay: VisGridLayout, btn):
        lay.addWidget(btn, lay.rowCount(), 0, 1, 5 - self.stringonly)
        self.createline(lay, lay.rowCount() - 2, {})

    def __init__(self, dd: dict, key="customparams", stringonly=False, needheader=True, dialog=None):
        super().__init__()
        self.needheader = needheader
        self.stringonly = stringonly
        self.ks: "list[QLineEdit]" = []
        self.vs: "list[typeswitcheditor]" = []
        lay = VisGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.lay = lay
        value: list = dd[key]
        self._key = key
        for i, d in enumerate(value):
            self.createline(lay, i, d)
        icon = getIconButton(icon="fa.plus", fix=False)
        lay.addWidget(icon, len(value), 0, 1, 5 - self.stringonly)
        icon.clicked.connect(functools.partial(self.addline, lay, icon))

    def updateValues(self):
        collect = []
        for i in range(len(self.ks)):
            if not self.lay.rowVisible(i):
                continue
            k = self.ks[i].text()
            v = self.vs[i].getvalue()
            t = self.vs[i].gettype()
            if not k:
                continue
            collect.append(dict(key=k, value=v, type=t))
        return {self._key: collect}


# 解析失败哨兵（区别于合法的 None）
_PARSE_FAILED = object()


def _normalize_js_like(text: str) -> str:
    """把 JS 风格对象/数组字面量尽量归一化为 Python 字面量语法。

    处理顺序很重要：
      1) 单引号字符串 -> 双引号（先做，后续正则才不会误伤字符串内容）
      2) JS 关键字 true/false/null -> Python True/False/None
      3) 字段之间缺逗号 -> 补 `,`（这一步在加引号之前做，这样未引号的 key
         也能被认出来，后续才能被补上引号）
      4) 无引号 key -> 双引号 key（此时它们都已跟在 `{` 或 `,` 后面）
    """
    s = text

    # 1) 单引号字符串 -> 双引号（只处理不含 escape 和双引号的简单情况）
    def _sq_to_dq(m):
        inner = m.group(1).replace('"', '\\"')
        return '"' + inner + '"'

    s = re.sub(r"'([^'\\]*)'", _sq_to_dq, s)

    # 2) JS 关键字 -> Python 字面量
    s = re.sub(r"\btrue\b", "True", s)
    s = re.sub(r"\bfalse\b", "False", s)
    s = re.sub(r"\bnull\b", "None", s)

    # 3) 字段之间缺逗号：在 value 后跟 `key:` 之间补 `,`
    #    value 形如：`"str"` / `'str'` / 数字 / `]` / `}` / True / False / None
    #    key 可以是已加引号的 `"key"` 或未加引号的 `key`
    s = re.sub(
        r'([\]}"\']|\d|\bTrue\b|\bFalse\b|\bNone\b)'
        r'\s+'
        r'((?:"[A-Za-z0-9_$]+"|[A-Za-z_$][A-Za-z0-9_$]*)\s*:)',
        r"\1, \2",
        s,
    )

    # 4) 无引号 key -> 双引号 key：在 `{` 或 `,` 后出现的 identifier:
    s = re.sub(
        r"([{,]\s*)([A-Za-z_$][A-Za-z0-9_$]*)(\s*):",
        r'\1"\2"\3:',
        s,
    )
    return s


def _loose_object_parse(text: str):
    """尽量宽容地把用户输入的 value 解析为 Python 对象。

    依次尝试：
      1) 严格 JSON
      2) Python 字面量 (True/False/None, 单/双引号 key)
      3) JS 风格归一化后重试（无引号 key、true/false/null、缺逗号）

    失败时返回 _PARSE_FAILED。
    """
    s = (text or "").strip()
    if not s:
        return _PARSE_FAILED

    # 1) 严格 JSON
    try:
        return json.loads(s)
    except Exception:
        pass

    # 2) Python 字面量
    try:
        return ast.literal_eval(s)
    except Exception:
        pass

    # 3) JS 风格归一化后重试
    normalized = _normalize_js_like(s)
    if normalized != s:
        try:
            return ast.literal_eval(normalized)
        except Exception:
            pass
        try:
            return json.loads(normalized)
        except Exception:
            pass

    return _PARSE_FAILED


def getcustombodyheaders(customparams: "list[dict]", **kw):
    extrabody = {}
    extraheader = {}
    for other in customparams if customparams else []:
        k = other.get("key")
        v = other.get("value")
        t = other.get("type")
        if t == "header":
            extraheader[k] = v
        else:
            if t == "number":
                try:
                    v = float(v)
                except:
                    continue
            elif t == "int":
                try:
                    v = int(v)
                except:
                    continue
            elif t == "bool":
                try:
                    v = bool(v)
                except:
                    continue
            elif t == "other":
                # json/python 类型：尽量宽容地支持 JSON / Python 字面量 / JS 风格
                # （包括无引号 key、true/false/null、字段间缺逗号）。
                # 之前只尝试 json.loads + eval，用户填 JS 风格时会被静默 continue 丢掉。
                parsed = _loose_object_parse(v)
                # 若失败，再尝试 eval(v, kw)（允许引用上下文变量，保留向后兼容）
                if parsed is _PARSE_FAILED:
                    try:
                        parsed = eval(v, kw)
                    except Exception:
                        parsed = _PARSE_FAILED
                if parsed is _PARSE_FAILED:
                    print(
                        "[LunaTranslator] 其他参数 (json/python) 解析失败，已跳过：\n"
                        f"  key   = {k!r}\n"
                        f"  value = {v!r}\n"
                        "  提示：请用以下任一格式（字段之间务必用逗号分隔）：\n"
                        "    - JSON 严格格式 : {\"order\": [\"wafer\"], \"allowFallbacks\": false}\n"
                        "    - Python 字面量 : {'order': ['wafer'], 'allowFallbacks': False}\n"
                        "    - JS 风格      : {order: ['wafer'], allowFallbacks: false}"
                    )
                    continue
                v = parsed
            extrabody[k] = v
    return extrabody, extraheader


class fallbackproviderbutton(QWidget):
    """LLM fallback 提供商配置按钮。

    点击后弹出一个独立的子对话框（subautoinitdialog），可以像主提供商一样填写：
      - API 接口地址 / API Key / 模型（支持刷新模型列表）
      - 流式输出、max_tokens、Temperature、top_p、frequency_penalty
      - reasoning_effort、thinking.type 等参数
      - 其他参数（extrabody / 自定义 headers）—— 兼容 customparams

    存储位置：dd[key]（一个 dict）。默认字段缺失时自动补齐。
    """

    DEFAULTS = {
        "API接口地址": "",
        "SECRET_KEY": "",
        "model": "",
        "modellistcache": [],
        "流式输出": True,
        "use_max_completion_tokens": False,
        "max_tokens": 1024,
        "Temperature": 0,
        "Temperature.use": True,
        "top_p": 0.3,
        "top_p_use": True,
        "frequency_penalty": 0,
        "frequency_penalty_use": False,
        "reasoning_effort": "medium",
        "reasoning_effort_use": False,
        "thinking.type": "disabled",
        "thinking.type.use": False,
        "customparams": [],
    }

    ARGSTYPE = {
        "API接口地址": {
            "rank": 0,
            "type": "llm_api_urls",
            "name": "Fallback API 接口地址",
        },
        "SECRET_KEY": {
            "rank": 1,
            "name": "Fallback API Key",
            "type": "textlist",
            "issecret": True,
        },
        "model": {
            "rank": 2,
            "name": "Fallback 模型",
            "type": "lineedit_or_combo",
            "list_function": "list_models",
            "list_cache": "modellistcache",
        },
        "modellistcache": {"type": "list_cache"},
        "s_1": {"type": "split", "rank": 3},
        "流式输出": {"rank": 3.1, "name": "流式输出", "type": "switch"},
        "max_tokens": {
            "rank": 3.2,
            "name": "max_tokens",
            "type": "intspin",
            "min": 1,
            "max": 1000000,
            "appends": ["use_max_completion_tokens"],
        },
        "use_max_completion_tokens": {
            "name": "使用_max_completion_tokens",
            "type": "switch",
        },
        "s_2": {"type": "split", "rank": 4},
        "Temperature": {
            "rank": 4.1,
            "name": "Temperature",
            "refswitch": "Temperature.use",
            "type": "spin",
            "min": 0,
            "max": 2,
            "step": 0.01,
        },
        "Temperature.use": {"type": "switch"},
        "top_p": {
            "rank": 4.2,
            "name": "top_p",
            "refswitch": "top_p_use",
            "type": "spin",
            "min": 0,
            "max": 1,
            "step": 0.01,
        },
        "top_p_use": {"type": "switch"},
        "frequency_penalty": {
            "rank": 4.3,
            "name": "frequency_penalty",
            "refswitch": "frequency_penalty_use",
            "type": "spin",
            "min": -2,
            "max": 2,
            "step": 0.01,
        },
        "frequency_penalty_use": {"type": "switch"},
        "reasoning_effort": {
            "rank": 4.4,
            "name": "reasoning_effort",
            "refswitch": "reasoning_effort_use",
            "type": "combo",
            "internal": ["none", "minimal", "low", "medium", "high", "xhigh"],
            "list": ["none", "minimal", "low", "medium", "high", "xhigh"],
        },
        "reasoning_effort_use": {"type": "switch"},
        "thinking.type": {
            "rank": 4.5,
            "name": "thinking.type",
            "refswitch": "thinking.type.use",
            "type": "combo",
            "internal": ["disabled", "enabled"],
            "list": ["disabled", "enabled"],
        },
        "thinking.type.use": {"type": "switch"},
        "s_3": {"type": "split", "rank": 5},
        "customparams": {
            "rank": 5.1,
            "name": "其他参数 (extrabody / headers)",
            "type": "custom",
            "function": "customparams",
        },
    }

    def __init__(self, dd: dict, key="fallback.provider", dialog=None):
        super().__init__()
        self._key = key
        self._dialog = dialog
        import copy as _copy

        if not isinstance(dd.get(key), dict):
            dd[key] = {}
        self._sub = dd[key]
        # 默认字段缺失时自动补齐（防止升级后旧配置缺字段）
        for k, v in self.DEFAULTS.items():
            if k not in self._sub:
                self._sub[k] = _copy.deepcopy(v)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton("配置 Fallback 提供商...")
        btn.clicked.connect(self._open)
        lay.addWidget(btn)
        lay.addStretch()

    def _find_dialog(self):
        # 优先使用构造时传入的 dialog；否则沿 Qt 父链查找 autoinitdialog 实例
        d = self._dialog
        if d is not None and hasattr(d, "modelfile"):
            return d
        p = self.parentWidget()
        while p is not None:
            if hasattr(p, "modelfile") and hasattr(p, "maybehasextrainfo"):
                return p
            p = p.parentWidget()
        return None

    def _open(self):
        try:
            from gui.inputdialog import subautoinitdialog, autoinitdialog_items
        except Exception:
            from traceback import print_exc

            print_exc()
            return
        outer = self._find_dialog()
        modelfile = getattr(outer, "modelfile", None) or "translator.gptcommon"
        maybehasextrainfo = getattr(outer, "maybehasextrainfo", None)
        items = autoinitdialog_items(
            {"args": self._sub, "argstype": self.ARGSTYPE}
        )
        # exec_=True 使子对话框模态，避免外层同时被修改导致保存冲突
        subautoinitdialog(
            outer,
            self._sub,
            "Fallback 提供商",
            800,
            items,
            modelfile,
            maybehasextrainfo,
            exec_=True,
        )

    def updateValues(self):
        # 子对话框直接原地写 self._sub，这里只需保证 dd[key] 指向它
        return {self._key: self._sub}
