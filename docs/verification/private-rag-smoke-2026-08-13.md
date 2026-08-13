# 私人 RAG 真实模型发布验证记录（2026-08-13）

## 本机结果

- 主机：macOS 14.3.1（Darwin 23.3.0），Apple Silicon arm64，Python 3.12.13，ONNX Runtime 1.28.0。
- 固定模型：`intfloat/multilingual-e5-small@5697a65b0a002a92fe8c4fc9d495303ffff9c7d2` 与 `BAAI/bge-reranker-base@711afb1eff814a80f5363996cd76e1b5f39cc7d7`；约 1.6 GB 产物通过 Manifest 字节数与 SHA-256 校验。
- 解析：Markdown 3 个 Block、120 字符，约 0.0053 秒。
- USearch：3 条 384 维向量生成 5,152 字节索引；检索约 0.00010 秒；相关中文段落排名第一。
- CPU：查询 Embedding 约 0.0050 秒，3 条批量 Embedding 约 0.0116 秒，3 对 Reranker 约 0.0506 秒。
- CoreML：查询 Embedding 约 0.0977 秒，3 条批量 Embedding 约 0.1524 秒，3 对 Reranker 约 0.1267 秒。
- 正确性：CoreML 与 CPU 的 Embedding 余弦和 Reranker 分数均通过门禁。
- 选择：三个工作负载均选择 `CPUExecutionProvider`；CoreML 正确但更慢，因此没有被选中，符合回退预期。

机器可读原始报告由以下命令生成，默认写到仓库外的临时路径：

```bash
python -m scripts.run_rag_smoke \
  --cache-dir /private/tmp/nanobot-rag-models \
  --offline \
  --output /private/tmp/nanobot-rag-smoke-macos.json
```

## 跨平台发布覆盖

- `.github/workflows/rag-real-model-smoke.yml` 提供 Windows、macOS、Linux CPU-only 真实模型矩阵，以及 Apple Silicon CPU/CoreML 对比任务；每次运行上传 JSON 报告。
- NVIDIA CUDA、Intel OpenVINO 与 Windows DirectML 依赖带真实驱动的发布机。启用任何对应 Profile 前，发布负责人必须在相应自托管机器运行同一脚本，并把 JSON 报告附到发布记录。
- 没有可用硬件的加速 Profile 不宣称已通过实机验证，也不阻止 portable CPU Profile 交付。
