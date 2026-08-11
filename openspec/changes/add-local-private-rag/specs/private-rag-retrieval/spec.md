## Purpose

本能力定义强制查询和 Agent 自主查询私人知识库时的检索行为、证据质量、引用格式，以及无答案和降级情况下的安全结果。

## ADDED Requirements

### Requirement: 强制检索与 Agent 自主检索
系统 SHALL 支持以 `/rag ask <question>` 作为强制检索入口，并以 `search_knowledge_base` 作为 Agent 自主选择的检索入口；两者都 SHALL 由服务端限定到当前已认证的私人主体。

#### Scenario: 强制查询
- **WHEN** 用户在允许的私聊中发送 `/rag ask <question>`
- **THEN** 系统在生成答案前执行私人知识库检索

#### Scenario: Agent 选择检索
- **WHEN** Agent 判断私人知识可能有助于回答普通对话
- **THEN** Agent 可以调用 `search_knowledge_base`，但不能选择或修改主体

#### Scenario: Agent 未选择检索
- **WHEN** 普通对话没有触发知识库工具
- **THEN** 系统本轮不搜索或暴露私人 RAG 知识库

### Requirement: 仅检索就绪文档
系统 SHALL 只检索当前主体中处于就绪状态、且属于当前兼容活动索引代次的 Chunk。

#### Scenario: 文档正在处理或已失败
- **WHEN** 查询发生时文档处于处理中、失败、删除中或 Profile 不兼容状态
- **THEN** 该文档的 Chunk 不得出现在候选或证据中

#### Scenario: 查询期间发布新索引
- **WHEN** 活动查询期间新的索引代次变为就绪
- **THEN** 当前查询使用一个一致的代次，后续查询可以使用新代次

### Requirement: 中英文 Hybrid 检索
系统 SHALL 组合面向中英文的词法检索与本地 Dense 检索，对两个有序候选集合去重和融合，并在选择证据前使用本地模型重排融合结果。

#### Scenario: 精确标识符查询
- **WHEN** 问题包含精确的产品编码、文件名词语、数字或少见关键词
- **THEN** 词法检索候选参与最终融合候选集

#### Scenario: 语义改写查询
- **WHEN** 问题使用与文档不同的中文或英文措辞表达相同含义
- **THEN** Dense 检索候选参与最终融合候选集

#### Scenario: 中英混合查询
- **WHEN** 查询混合中文、英文、数字或标识符
- **THEN** 两条检索路径使用兼容的规范化方式并共同参与一次重排

### Requirement: 证据与来源引用
系统 SHALL 返回不超过配置上限的证据，默认最多六个 Chunk。每条证据 SHALL 包含准确证据文本、原始显示文件名、稳定文档 ID 和可获得的最精确来源位置。

#### Scenario: PDF 证据
- **WHEN** 选中证据来自 PDF 页面
- **THEN** 引用包含显示文件名、文档 ID 和从 1 开始的页码

#### Scenario: Office 或文本证据
- **WHEN** 选中证据来自 DOCX、PPTX、XLSX、HTML、Markdown 或纯文本
- **THEN** 引用包含相应的标题路径、幻灯片编号、工作表与行范围或行号范围

#### Scenario: 最终 RAG 回答
- **WHEN** 最终回答提出基于 RAG 证据的事实
- **THEN** 回答引用对应的返回来源，而不是编造来源

### Requirement: 明确的无证据结果
系统 SHALL 在没有候选满足相关性策略时返回明确、类型化的无证据结果，并 SHALL NOT 静默搜索其他主体、公共语料或普通会话附件。

#### Scenario: 强制查询没有相关来源
- **WHEN** `/rag ask` 没有得到达到相关性策略的证据
- **THEN** 系统明确告知用户私人知识库没有提供充分依据

#### Scenario: Agent 工具没有相关来源
- **WHEN** `search_knowledge_base` 没有得到可接受证据
- **THEN** 工具返回带原因的空结果，并要求 Agent 不得伪造 RAG 支持

### Requirement: 披露检索降级
系统 SHALL 在兼容的 Dense 索引和本地模型可用时执行完整 Hybrid 检索。如果策略允许仅使用词法检索恢复，系统 SHALL 明确披露降级模式，且 SHALL NOT 把它描述为完整 Hybrid 检索。

#### Scenario: Dense 检索暂时不可用
- **WHEN** 词法索引仍有效，但兼容 Dense 检索暂时不可用
- **THEN** 系统依据配置明确失败，或使用已披露的仅词法降级模式

#### Scenario: 索引损坏或不兼容
- **WHEN** 活动向量索引损坏，或与当前 Embedding Profile 不兼容
- **THEN** 系统排除该索引，直到发布经过验证的重建代次

### Requirement: 远程主模型的数据边界
系统 SHALL 把原始文件、全部 Chunk、查询向量、语料向量、词法候选和重排候选保留在本机。当主回答模型为远程模型时，它 SHALL 只接收回答当前问题所需的最终选中证据。

#### Scenario: 使用远程主语言模型
- **WHEN** 配置的主语言模型为远程模型，且 RAG 返回可接受证据
- **THEN** 只有最终选中证据和引用元数据被加入该模型的当前轮次上下文

#### Scenario: 本地检索流水线
- **WHEN** 系统生成查询向量、搜索索引、融合候选或重排候选
- **THEN** 这些操作不发起任何第三方推理请求

### Requirement: 检索内容始终是不可信数据
系统 SHALL 把检索文本视为引用证据，SHALL NOT 允许文档中的指令改变主体范围、系统策略或工具授权。

#### Scenario: 文档要求调用其他工具
- **WHEN** 选中证据包含要求 Agent 调用工具或忽略策略的文本
- **THEN** 该文本仍只具有证据权限，不获得额外授权

### Requirement: 检索质量验收
发布用检索 Profile SHALL 在版本化的中英文评测集上达到以下标准：可回答问题的 Recall@30 不低于 90%，至少 80% 的可回答问题在最终六条证据中包含相关证据，无答案问题误命中率不高于 10%，引用位置正确率不低于 95%，跨主体检索数量为 0，且 Hybrid 综合表现不低于 BM25-only 与 Dense-only 基线中的较优者。

#### Scenario: 发布前检索评测
- **WHEN** 使用版本化测试语料评测固定发布 Profile
- **THEN** 发布前所有质量、引用和隔离阈值均满足要求
