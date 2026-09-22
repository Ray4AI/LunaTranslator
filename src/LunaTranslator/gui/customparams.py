from qtsymbols import *
import functools, json
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
                try:
                    v = json.loads(v)
                except:
                    try:
                        v = eval(v, kw)
                    except:
                        from traceback import print_exc

                        print_exc()
                        continue
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
