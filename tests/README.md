# LLM Fallback Tests

针对 `chatgpt-3rd-party`（大模型通用接口）新增的 fallback 请求链路的独立测试。

不依赖 Qt / GUI，通过 `test_stubs.py` 注入 stub 后直接调用 `translator.gptcommon`。

## 运行

```bash
cd tests
python3 test_fallback_logic.py    # 正则匹配、_ConfigView、配置 schema、源代码符号
python3 test_fallback_flow.py     # translate() 非流式 fallback 流程端到端
python3 test_fallback_stream.py   # translate() 流式 fallback 流程 + \0 重置
```

全部通过时最后一行会打印 `FAILED: 0`，进程退出码为 0。

## 覆盖点

- **fallback 触发**
  - 主模型返回文本命中自定义正则（中/英文审查关键词）→ 切 fallback
  - 主模型请求抛错，错误文本命中正则 → 切 fallback
  - `fallback.on_any_error=True` 时任何请求错误都切 fallback
  - 无关错误（`on_any_error=False` 且正则不命中）→ 正常抛出，不切
  - 未启用 / 未配置 fallback → 主模型结果直接使用
- **fallback 请求**
  - 使用 fallback 自己的 `API接口地址` / `model` / `max_tokens` / `Temperature`
  - 使用 fallback 自己的 extrabody / headers（与主提供商隔离）
  - `fallback.SECRET_KEY` 支持 `|` 分隔多 key 轮转
  - 支持不同 API 类型（openai 兼容 / gemini / claude）
- **UI 输出**
  - 流式：切 fallback 前 `\0` 重置，避免残留审查文本
  - 非流式：主提供商结果缓冲，命中正则时静默切换
  - fallback 也失败但主模型有结果 → 回显主模型结果
  - `markdown2html=True` 时用 `LUNASHOWHTML` 前缀包一层
- **配置**
  - `fallback.provider` 是一个嵌套 dict（弹出独立子对话框填写）
  - 子对话框 schema 与主提供商一致：URL / Key / 模型 / 请求参数 / extrabody
