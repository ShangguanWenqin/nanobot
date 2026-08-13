# 本地私人 RAG

nanobot 的私人 RAG 使用本机 Embedding、Hybrid（BM25 + Dense）检索和本地 Reranker。原始文件、完整 Chunk、向量及中间候选不会发送给远程主模型；主模型只会收到当前问题最终选中的证据和引用元数据。

## 安装与配置

CPU 基线安装：

```bash
pip install "nanobot-ai[rag]"
```

在 `~/.nanobot/config.json` 中启用：

```json
{
  "rag": {
    "enabled": true,
    "perUserQuotaBytes": 1073741824,
    "globalMaxBytes": 10737418240,
    "minFreeDiskBytes": 2147483648,
    "executionProvider": "auto"
  }
}
```

`perUserQuotaBytes` 是每个渠道身份的原始文件配额，上例为 1 GiB；`globalMaxBytes` 是整台实例的上限。预占中的上传也计入配额，删除彻底完成后才释放。始终为数据目录预留足够空间。

`executionProvider: "auto"` 会对本机已安装且兼容的 CPU、Apple CoreML、NVIDIA CUDA、Intel OpenVINO 或 Windows DirectML Provider 做有界基准，并分别选择查询 Embedding、批量 Embedding 和 Reranker 的最快合格方案。它不会在运行时自动安装驱动或 Python 包。可通过 `/rag status` 查看选中 Profile；强制指定不可用 Provider 会让 RAG 明确不可用，而不是静默换用远程服务。

首次运行前应在允许联网的管理环境执行模型预取，校验固定 Manifest 中的 SHA-256 后再把模型缓存部署到离线机器。运行时禁止远程仓库代码；离线环境若缺少或损坏模型，会返回可操作错误，不会访问第三方推理 API。

## 使用命令

RAG 只允许渠道确认的私聊，并以稳定、经过认证的发送者身份隔离。WebUI、Telegram、Discord 等渠道身份分别视为不同用户；群聊、身份不稳定或渠道无法证明私聊范围时会失败即关闭。

- `/rag add`：仅将当前这条消息附带的文件入库。普通附件不会自动入库。
- `/rag status [job_id]`：查看配额、Profile、活动任务，或指定任务状态。
- `/rag list`：列出当前用户的文档及稳定 `document_id`。
- `/rag delete <document_id>`：立即从检索中隐藏文档，随后后台持久删除。
- `/rag ask <question>`：强制先检索再回答；普通问题由 Agent 自主判断是否调用知识库工具。

如果没有足够证据，系统会明确说明私人知识库没有依据，不会退回其他用户、公共语料或普通会话附件。基于证据的回答应包含文件名、文档 ID 和页码、标题、幻灯片、工作表行范围或文本行号。

## 支持格式与安全限制

支持 PDF、DOCX、PPTX、XLSX、HTML、Markdown 和常见纯文本格式。解析有文件大小、页数、压缩展开量、字符数和墙钟超时限制，并在隔离 Worker 中运行。

以下内容会被拒绝或明确标记失败：OCR-only 扫描件、加密文件、宏文件、无可提取文本、伪造扩展名、危险 OOXML 压缩包以及超过安全上限的文件。系统不会执行公式、宏、脚本或文档中的指令；检索文本始终作为不可信引用证据处理。

## 故障排查

- 提示安装 `nanobot-ai[rag]`：安装 CPU Extra，重启 gateway，并确认 `rag.enabled` 为 `true`。
- 提示模型缺失或完整性失败：重新执行管理员模型预取，确认缓存可读且产物哈希与 Manifest 一致。
- 只能关键词检索：Dense 索引或本地模型暂时不可用；查看 `/rag status` 和日志，完成兼容 Profile 的索引重建。回答会披露降级状态。
- 入库长期停留：使用 `/rag status <job_id>` 查看 parsing、chunking、embedding 或 indexing 阶段；永久错误不会无限重试。
- 配额不足：删除不需要的文档并等待删除任务完成，或由管理员提高系统配置的用户/全局上限。
- GPU 未被选择：确认对应 Execution Provider、驱动和运行时已预先安装。自动基准也可能因为结果不一致、内存超限、不稳定或实际更慢而选择 CPU。

## 发布用真实模型冒烟

发布前使用统一入口运行真实模型校验。它会校验固定模型缓存，记录 Markdown 解析、真实 E5 Embedding、USearch 索引大小与检索延迟、真实 BGE Reranker、各 Execution Provider 的正确性和分工作负载选择结果：

```bash
uv run --no-sync python -m scripts.run_rag_smoke \
  --cache-dir .rag-model-cache \
  --output rag-smoke.json
```

默认测试 CPU 以及本机所有已安装且平台兼容的加速 Provider。也可以明确限定候选；CPU 基线始终必选：

```bash
# Apple Silicon
uv run --no-sync python -m scripts.run_rag_smoke --cache-dir .rag-model-cache \
  --provider CPUExecutionProvider --provider CoreMLExecutionProvider

# NVIDIA / Intel / Windows GPU 发布机分别追加：
# --provider CUDAExecutionProvider
# --provider OpenVINOExecutionProvider
# --provider DmlExecutionProvider
```

Windows、macOS、Linux 的 CPU-only 矩阵和 Apple Silicon 冒烟已配置在 `.github/workflows/rag-real-model-smoke.yml`。NVIDIA、OpenVINO 和 DirectML 需要带相应驱动的发布机；启用对应加速 Profile 前，必须保留本脚本生成的 JSON 报告作为发布证据。候选输出不一致会标记 `correctness_gate_failed`；候选正确但更慢时，`selected` 会继续指向 CPU。

私人 RAG 数据目录应仅允许 nanobot 进程访问，并纳入加密磁盘、备份、保留期与实例销毁策略。`/rag list`、状态事件和错误不会返回托管宿主路径或文档正文。
