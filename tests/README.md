# LLM Fallback Tests

针对 `chatgpt-3rd-party`（大模型通用接口）新增的 fallback 请求链路 + 首 token 超时 + 请求取消 的独立测试。

不依赖 Qt / GUI，通过 `test_stubs.py` 注入 stub 后直接调用 `translator.gptcommon`。

## 运行

```bash
cd tests
python3 test_fallback_logic.py       # 59 项：正则匹配、_ConfigView、配置 schema、源代码符号
python3 test_fallback_flow.py        # 27 项：translate() 非流式 fallback 流程端到端
python3 test_fallback_stream.py      # 39 项：translate() 流式 fallback + \0 重置 + early-abort
python3 test_first_token_timeout.py  # 33 项：主接口首 token 超时（NSFW 挂住 -> fallback）
python3 test_cancel_previous.py      # 24 项：新请求到达时立刻取消上一个翻译请求
```

全部通过时最后一行会打印 `FAILED: 0`，进程退出码为 0。**总计 182 项**。

## 覆盖点

### Fallback 触发
- 主模型返回文本命中自定义正则（中/英文审查关键词）→ 切 fallback
- 主模型请求抛错，错误文本命中正则 → 切 fallback
- `fallback.on_any_error=True` 时任何请求错误都切 fallback
- **`fallback.first_token_timeout.use=True` 且主接口首 token 超时** → 切 fallback
- 无关错误（`on_any_error=False` 且正则不命中）→ 正常抛出，不切
- 未启用 / 未配置 fallback → 主模型结果直接使用

### Fallback 请求
- 使用 fallback 自己的 `API接口地址` / `model` / `max_tokens` / `Temperature`
- 使用 fallback 自己的 extrabody / headers（与主提供商隔离）
- `fallback.SECRET_KEY` 支持 `|` 分隔多 key 轮转
- 支持不同 API 类型（openai 兼容 / gemini / claude）
- `customparams` 的 `json/python` 类型支持 JSON / Python 字面量 / JS 风格（含缺逗号容错）

### UI 输出
- 流式：切 fallback 前 `\0` 重置，避免残留审查文本
- 流式：**主模型吐出审查关键词的那一瞬间立刻撤回**（early-abort），不等整段流完
- 非流式：主提供商结果缓冲，命中正则时静默切换
- fallback 也失败但主模型有结果 → 回显主模型结果
- `markdown2html=True` 时用 `LUNASHOWHTML` 前缀包一层

### 首 token 超时（`fallback.first_token_timeout.use`）
- 流式：用 `_ResponseProxy` 精确控制**首行**超时；后续行不设限（符合"首 token"语义）
- 非流式：`post(timeout=(5, N))`，无响应 N 秒后 fallback
- 流式的 `post` 用 `(5, N+5)` 作为 safety net（防 HTTP headers 也不返）
- `_FirstTokenTimeout` 独立触发 fallback，不受 `on_any_error` 影响
- 值 `<=0` 或 switch 关闭 → 完全禁用

### 请求取消（`cancel_previous_request`）
- 新请求到达时（`_fythread` 拿到 queue 里下一个 content）触发 `cancel_previous()`
- `cancel_previous()` 递增 generation 计数 + 关当前 response
- 旧 generation 的 `translate()` 在下一个检查点立刻 `return`
- 被取消的请求**不 fallback、不写缓存、不发终态 callback**
- `_do_request` 拒绝为旧 generation 注册新 response（fallback 也不会发）
- 默认关（保留"后台跑完写缓存"的老行为）；开 → 翻字幕快时不再堆积挂住的请求

### 配置
- `fallback.provider` 是一个嵌套 dict（弹出独立子对话框填写）
- 子对话框 schema 与主提供商一致：URL / Key / 模型 / 请求参数 / extrabody
