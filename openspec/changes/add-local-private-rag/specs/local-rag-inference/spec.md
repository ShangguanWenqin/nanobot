## Purpose

本能力定义隐私优先的本地模型执行、跨平台 CPU 基线、模型完整性，以及通过正确性门禁自动选择硬件加速的行为契约。

## ADDED Requirements

### Requirement: Embedding 与 Reranker 全部本地执行
系统 SHALL 在 nanobot 所在主机上执行文档 Embedding、查询 Embedding 和 Reranker，且不得提供远程推理回退。

#### Scenario: 本地模型可用
- **WHEN** 入库或检索需要执行 Embedding 或 Reranker
- **THEN** 系统使用经过验证的本地运行 Profile 完成推理

#### Scenario: 本地模型不可用
- **WHEN** 必需的本地模型无法加载，且没有其他经过验证的本地回退
- **THEN** 操作明确失败或进入允许且已披露的降级模式，不调用远程 Embedding 或 Reranker 服务

### Requirement: 跨平台默认模型 Profile
第一版 SHALL 默认使用 `intfloat/multilingual-e5-small` 生成 Embedding，并使用 `BAAI/bge-reranker-base` 执行重排；在支持的 Windows、macOS 和 Linux 环境中 SHALL 提供完整的 CPU-only 路径。

#### Scenario: 只有 CPU 的主机
- **WHEN** 主机没有安装兼容加速器
- **THEN** 入库和完整本地检索仍能通过 CPU Profile 工作

#### Scenario: 中英文输入
- **WHEN** 默认 Profile 接收到长度限制内的中文、英文或中英混合 Chunk 与查询
- **THEN** 系统使用固定 Profile 生成规范化向量和本地相关性分数

### Requirement: 固定且可信的模型供应链
系统 SHALL 只加载由 Manifest 固定不可变 revision、必需文件、密码学哈希、Tokenizer 行为、Pooling、归一化、向量维度、精度变体和许可证元数据的模型。系统 SHALL NOT 执行远程模型仓库代码。

#### Scenario: 固定模型缓存有效
- **WHEN** 所有必需模型产物均与 Manifest 一致
- **THEN** 系统无需执行模型仓库代码即可加载 Profile

#### Scenario: 产物缺失或被修改
- **WHEN** 必需模型产物缺失或哈希校验失败
- **THEN** 系统拒绝加载并报告模型准备或完整性失败

#### Scenario: 离线环境首次使用
- **WHEN** 已启用自动模型下载，但主机离线且本地没有固定模型缓存
- **THEN** 任务明确失败，不创建就绪文档，也不联系替代推理服务

### Requirement: 全局模型缓存不计入用户配额
系统 SHALL 把模型文件视为共享系统资源，SHALL NOT 把其字节计入任何用户的私人 RAG 配额。

#### Scenario: 首个用户触发模型下载
- **WHEN** 固定模型被下载到共享缓存
- **THEN** 任何主体的原始文件配额使用量都不发生变化

#### Scenario: 多个任务并发首次使用
- **WHEN** 多个操作同时需要同一个尚未下载的模型
- **THEN** 系统协调缓存写入，使所有使用者最终看到同一套经过验证的模型产物

### Requirement: 自动选择硬件 Profile
在自动模式下，系统 SHALL 检查已安装的本地执行后端，并针对当前硬件和模型 Profile 测试所有兼容的 CPU 与加速候选。系统 SHALL 分别为交互式 Embedding、批量 Embedding 和 Reranker 选择通过正确性与资源校验后的最快候选。

#### Scenario: Apple 加速更快
- **WHEN** 兼容的 Apple 执行后端通过验证，并在某项工作负载中快于 CPU
- **THEN** 系统为该工作负载选择 Apple 加速后端

#### Scenario: 检测到 GPU 但比 CPU 慢
- **WHEN** 已安装的 GPU 候选通过正确性校验，但本机基准测试慢于 CPU
- **THEN** 系统为该工作负载继续使用 CPU

#### Scenario: 不同工作负载的最优后端不同
- **WHEN** 单查询 Embedding、批量 Embedding 和 Reranker 各自具有不同的最快候选
- **THEN** 系统分别记录并使用各工作负载的验证通过者

#### Scenario: 命中硬件指纹缓存
- **WHEN** 当前硬件、模型和运行时版本与已验证指纹一致
- **THEN** 系统复用缓存的选择结果，不重复执行首次基准测试

### Requirement: 加速必须通过正确性门禁
加速候选只有在输出满足参考容差并通过内存与稳定性限制时 SHALL 具备可选资格。更快但不兼容的输出 SHALL NOT 被选中。

#### Scenario: Embedding 输出超过容差
- **WHEN** 候选在固定样例上的 Embedding 超出允许的相似度容差
- **THEN** 无论其速度多快，系统都拒绝该候选

#### Scenario: Reranker 改变验收排序
- **WHEN** 候选 Reranker 输出无法满足固定样例的排序或分数容差
- **THEN** 系统拒绝该候选

#### Scenario: 候选耗尽加速器内存
- **WHEN** 初始化或基准测试超过候选的内存策略
- **THEN** 系统拒绝或暂时列入黑名单，并继续评估下一个本地候选

### Requirement: Embedding Profile 兼容性
每个持久化向量代次 SHALL 标识生成它的完整 Embedding Profile。系统 SHALL NOT 使用模型产物、Tokenizer、Pooling、归一化、维度或精度不兼容的 Profile 查询该代次。

#### Scenario: 仅执行设备变化且输出兼容
- **WHEN** 只有执行设备发生变化，并且数值兼容校验通过
- **THEN** 现有向量代次仍可查询

#### Scenario: 模型产物或精度 Profile 变化
- **WHEN** 选中的 Embedding Profile 与活动向量代次不兼容
- **THEN** 系统在切换检索前构建并验证新代次

#### Scenario: Reranker 执行 Profile 变化
- **WHEN** 系统选择了另一个经过验证的 Reranker Profile
- **THEN** 因为 Reranker 输出不会持久化，该 Profile 可立即使用

### Requirement: 安全的运行时回退
系统 SHALL 在加速器初始化或执行失败时依次回退到经过验证的本地候选，并最终回退到跨平台 CPU Profile。可恢复的回退 SHALL NOT 使原本有效的查询导致网关失败。

#### Scenario: 查询期间加速器失败
- **WHEN** 已选加速器发生不支持算子、初始化错误、内存不足或运行时失败
- **THEN** 系统记录失败、发送一次回退提示，并在安全时通过经过验证的本地 Profile 重试

#### Scenario: 强制指定的运行时不可用
- **WHEN** 配置强制使用未安装或无效的运行后端
- **THEN** 配置校验或启动过程明确报告不兼容问题

### Requirement: 交互式推理优先
系统 SHALL 让交互式查询 Embedding 和 Reranker 优先于后台入库，并 SHALL 限制本地推理并发，避免大规模导入导致普通 Agent 对话失去响应。

#### Scenario: 大规模入库期间收到查询
- **WHEN** 后台 Embedding 正在运行时收到私人查询
- **THEN** 查询在有界调度点获得优先权，入库随后继续
