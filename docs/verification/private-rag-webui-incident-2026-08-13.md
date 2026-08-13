# WebUI 私人 RAG 文档入库故障记录（2026-08-13）

## 摘要

WebUI 可以选择受支持的文档，但无法可靠地通过 `/rag add` 加入私人 RAG 库。
排查发现上传传输、RAG 启动校验和 Agent 命令处理三个独立阻断。修复后使用真实
WebUI bootstrap token 和 WebSocket 消息完成了文档上传、异步入库、索引、任务状态
查询和文档列表查询的端到端验证。

## 用户可见现象

- 较大的文档在浏览器端提示超过网关传输上限，消息没有到达 RAG 服务。
- 较小的文档到达 gateway 后，私人 RAG 仍可能提示不可用。
- RAG 运行时可用后，带文档的 `/rag add` 被识别为未知 `/rag` 命令。

普通附件不会自动入库。有效操作仍要求在同一条消息中发送 `/rag add` 和文档附件。

## 根因

### 1. WebSocket 帧上限沿用了旧配置

本机配置显式设置了 `channels.websocket.maxMessageBytes: 1048576`。WebUI 将文档编码为
Base64 后放入 WebSocket JSON 帧；扣除文本和协议预留后，约 688 KiB 以上的原始文件
会在浏览器侧以 `transport_too_large` 被拒绝。当前默认值为 `37748736`，WebUI 业务
策略仍限制单文件最大 6 MiB。

运维修复是将该配置更新为 `37748736` 并重启 gateway。原配置备份为
`~/.nanobot/config.json.rag-verify-backup-20260813T2237`；用户配置不属于本仓库 commit。

### 2. 固定 Embedding 样例摘要与固定模型实际输出不一致

固定的 `intfloat/multilingual-e5-small` ONNX 模型和 Tokenizer 均通过 revision、字节数和
SHA-256 产物校验，但启动阶段的 `LocalEmbedder.validate_samples()` 返回：

```text
expected=ed2cd9707fbd01f027aff3e59402946fd290d39c1318d5bfc92026e6dbe1d529
actual=e28f38dc2b01a0a90b1a73bafa4c1d0f77e4b44fe3681ac707863823c994e0b6
```

因此 gateway 将 RAG 标记为 unavailable。原发布 smoke 只比较不同 Execution Provider
相对于 CPU 的数值一致性，没有调用固定 Manifest 的 `validate_samples()`，所以没有发现
错误摘要。修复将 Manifest 和对应测试更新为经过真实固定产物验证的实际摘要。

### 3. 文档附件适配先于命令路由执行

`AgentLoop._restore_turn()` 在命令阶段之前调用 `reference_non_image_attachments()`，把
文档从 `InboundMessage.media` 移除，并把消息改写为：

```text
/rag add

[Attachment: /managed/media/path/document.md]
```

这既破坏了 `/rag add` 的精确匹配，也让 RAG 命令无法读取当前消息附件。修复后，所有
可分发的 Slash 命令会先保留原始附件供命令处理；普通 Agent 消息仍使用现有的附件引用
适配行为。回归测试通过真实 `AgentLoop._process_message()` 覆盖这一阶段顺序。

## 端到端验证

验证使用与浏览器相同的链路：

1. 请求 `/webui/bootstrap` 并取得一次性 WebUI token。
2. 建立经过认证的 `ws://127.0.0.1:8765/` 连接。
3. 发送带 Markdown Data URL 附件的 `/rag add` 消息。
4. 轮询 `/rag status <job_id>` 至 `ready`。
5. 执行 `/rag list` 并确认文档 ID、文件名、大小和 `ready` 状态。

实际结果：

```text
任务 9db4d4f9a0be4968a8e0cf16c9cadbb6：ready
文档 247c5aa71baf491088b2826b4aa63f69
文件 f4f17f7e9dc7_webui-rag-e2e-2026-08-13.md（ready，125 字节）
```

媒体网关为托管文件名增加随机前缀，原始文件名仍保留在后缀中。

## 自动化验证

- Embedding Manifest 与 AgentLoop RAG 回归测试：4 passed。
- WebUI 全量测试：982 passed（其中包含 13 项文档附件测试）。
- WebUI 生产构建：通过。
- `git diff --check`：通过。
- 修改文件的 Python 编译检查：通过。
- 当前 shell 环境没有 `uv`、`ruff` 或 `pytest-asyncio`，因此没有在该环境重新运行完整
  Python 异步测试和 Ruff；真实 WebUI/RAG 端到端验证覆盖了本次故障链路。

## 后续防回归要求

- RAG 的真实模型 smoke 应调用固定 Manifest 样例校验，不能只做 Provider 间相对比较。
- WebUI bootstrap 暴露的传输限制必须与浏览器预检使用同一配置。
- 需要原始附件的命令必须在普通附件引用适配之前处理。
- 发布验证至少保留一次 WebUI `/rag add` 到 `/rag list` 的完整链路证据。
