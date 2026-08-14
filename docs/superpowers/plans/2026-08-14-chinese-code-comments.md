# nanobot 主体代码中文注释实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变任何源码非注释 token 的前提下，为 nanobot Python 后端与 React/TypeScript WebUI 的有效运行时代码增加面向初学者的中文教学注释。

**Architecture:** 先从入口、组合根、核心数据对象和协议边界建立全局代码认知地图，再按依赖顺序分批注释。批次之间共享同一套术语和调用链；每批提交前都与 `main` 对照，证明差异仅包含 Python `#` 注释或 TypeScript/TSX 注释。

**Tech Stack:** Python 3.11、asyncio、Pydantic、React 18、TypeScript、WebSocket、Git。

## Global Constraints

- 基线固定为创建分支时的 `main` 提交 `6bf08edf`。
- 工作分支固定为 `codex/chinese-code-comments`，工作目录固定为 `/Users/sgwq/Documents/nanobot/.worktrees/chinese-code-comments`。
- Python 只新增 `#` 注释；不得新增或修改 docstring。
- TypeScript/TSX 只新增 `//`、`/* ... */` 或 JSDoc 注释。
- 不删除、移动、替换或重新格式化任何已有源码 token。
- 不运行测试、类型检查或构建。
- 注释必须基于完整调用链和可验证的代码事实，不猜测设计动机。
- 排除测试、缓存、构建产物、生成文件、空 `__init__.py`、纯导出/类型转发文件及无 nanobot 业务逻辑的生成式 UI 基础组件。
- 每个批次提交前执行纯注释 token 等价审计和 `git diff --check`。

---

### Task 1: 建立全局代码认知地图与覆盖台账

**Files:**
- Create: `docs/code-commentary/architecture-map.md`
- Create: `docs/code-commentary/coverage.md`
- Read: `docs/architecture.md`
- Read: `.agent/design.md`
- Read: `.agent/security.md`
- Read: `.agent/gotchas.md`
- Read: `nanobot/cli/commands.py`
- Read: `nanobot/nanobot.py`
- Read: `nanobot/process_runtime.py`
- Read: `nanobot/channels/manager.py`
- Read: `nanobot/bus/events.py`
- Read: `nanobot/bus/queue.py`
- Read: `nanobot/agent/loop.py`
- Read: `nanobot/agent/runner.py`
- Read: `nanobot/agent/context.py`
- Read: `nanobot/session/manager.py`
- Read: `nanobot/providers/factory.py`
- Read: `nanobot/providers/registry.py`
- Read: `nanobot/agent/tools/registry.py`
- Read: `nanobot/channels/websocket/runtime.py`
- Read: `nanobot/webui/gateway_services.py`
- Read: `webui/src/App.tsx`
- Read: `webui/src/lib/nanobot-client.ts`

**Interfaces:**
- Consumes: 当前 `main` 实现、架构文档和候选源码列表。
- Produces: 所有后续批次共同使用的入口图、消息流、状态所有权、扩展点、安全边界、前后端协议图，以及每个候选文件的 `include`/`exclude` 状态。

- [ ] **Step 1: 生成候选源码清单**

  只使用 Git 跟踪文件，分别枚举 `nanobot/**/*.py` 与 `webui/src/**/*.{ts,tsx}`；过滤测试、缓存、构建目录和本地化文案。把剩余文件逐项记录到 `coverage.md`，不得只记录目录统计。

- [ ] **Step 2: 追踪所有组合根和入口**

  从 CLI、SDK、Gateway、API、Channel 和 WebUI 入口向内追踪对象构造与生命周期，记录实际调用路径、关键类型和清理顺序。

- [ ] **Step 3: 追踪主消息与状态链路**

  记录 `InboundMessage → MessageBus → AgentLoop → AgentRunner → Provider/Tool → OutboundMessage`，并补充 Session、Memory、Goal、Cron、RuntimeContext 与 WorkspaceScope 在各阶段的读写责任。

- [ ] **Step 4: 追踪前后端协议**

  对照 Python WebSocket/HTTP 处理器、TypeScript 协议类型与 `NanobotClient`，记录鉴权、复用连接、重连、流式事件、会话刷新和设置更新链路。

- [ ] **Step 5: 标记安全与兼容边界**

  在认知地图中列出路径约束、SSRF、Shell 沙箱、配对、持久化原子性、Provider 差异和 Channel 协议限制，注明其代码所有者。

- [ ] **Step 6: 完成覆盖台账分类**

  每个候选文件标记为 `include` 或带明确理由的 `exclude`。`include` 文件还要标记所属批次，不允许出现未分类条目。

- [ ] **Step 7: 自检并提交全局认知材料**

  检查地图中的每条跨模块关系都能定位到具体源码；运行 `git diff --check`，然后提交：

  ```bash
  git add docs/code-commentary/architecture-map.md docs/code-commentary/coverage.md
  git commit -m "docs: map nanobot runtime for Chinese commentary"
  ```

### Task 2: 基础模型、配置与安全边界注释

**Files:**
- Modify: `nanobot/bus/`
- Modify: `nanobot/config/`
- Modify: `nanobot/security/`
- Modify: `nanobot/runtime_context.py`
- Modify: `nanobot/config_base.py`
- Modify: `nanobot/optional_features.py`
- Modify: `nanobot/utils/` 中被配置、上下文与安全链路直接使用的 `include` 文件

**Interfaces:**
- Consumes: Task 1 的数据流、配置流和安全边界。
- Produces: 初学者可以先理解的消息对象、配置解析、路径归属、运行时上下文与安全守卫注释。

- [ ] **Step 1: 阅读本批全部 include 文件及其直接调用者**
- [ ] **Step 2: 添加模块位置与数据所有权注释**
- [ ] **Step 3: 为配置别名、动态边界、路径能力和网络限制添加原因注释**
- [ ] **Step 4: 执行 Python 纯注释 token 等价审计与 `git diff --check`**
- [ ] **Step 5: 更新覆盖台账并提交 `docs: annotate config bus and security in Chinese`**

### Task 3: Agent 核心执行链路注释

**Files:**
- Modify: `nanobot/agent/loop.py`
- Modify: `nanobot/agent/runner.py`
- Modify: `nanobot/agent/context.py`
- Modify: `nanobot/agent/context_governance.py`
- Modify: `nanobot/agent/model_runtime.py`
- Modify: `nanobot/agent/hook.py`
- Modify: `nanobot/agent/hooks.py`
- Modify: `nanobot/agent/turn_hooks.py`
- Modify: `nanobot/agent/turn_delivery.py`
- Modify: `nanobot/agent/automation_turns.py`
- Modify: `nanobot/agent/cron_turns.py`
- Modify: `nanobot/agent/autocompact.py`
- Modify: `nanobot/agent/` 中其余非工具类 `include` 文件

**Interfaces:**
- Consumes: MessageBus、配置、Session、WorkspaceScope、Provider 和 ToolRegistry。
- Produces: 对一次 turn 从接纳、恢复、构建、运行、保存到投递的完整中文解释。

- [ ] **Step 1: 对照 AgentLoop 阶段与 AgentRunner 循环复核调用链**
- [ ] **Step 2: 注释 turn 上下文、生命周期阶段和状态所有权**
- [ ] **Step 3: 注释模型调用、工具回填、流式输出、注入、重试与终止条件**
- [ ] **Step 4: 注释 Hook、自动压缩和投递边界为何位于当前层**
- [ ] **Step 5: 执行 Python 纯注释 token 等价审计与 `git diff --check`**
- [ ] **Step 6: 更新覆盖台账并提交 `docs: annotate agent execution core in Chinese`**

### Task 4: 会话、记忆、目标与调度状态注释

**Files:**
- Modify: `nanobot/session/`
- Modify: `nanobot/agent/memory.py`
- Modify: `nanobot/cron/`
- Modify: `nanobot/triggers/`
- Modify: `nanobot/agent/long_task.py`

**Interfaces:**
- Consumes: Agent turn 生命周期与工作区命名空间。
- Produces: 短期会话、长期记忆、持续目标、定时任务和本地触发器的状态关系注释。

- [ ] **Step 1: 复核持久化文件、锁、原子写入、压缩与恢复路径**
- [ ] **Step 2: 注释 Session 与 Memory 的不同职责和数据流**
- [ ] **Step 3: 注释 Goal、Cron、Automation 和 Trigger 如何产生后续 turn**
- [ ] **Step 4: 注释崩溃恢复、并发访问和历史污染防护原因**
- [ ] **Step 5: 执行 Python 纯注释 token 等价审计与 `git diff --check`**
- [ ] **Step 6: 更新覆盖台账并提交 `docs: annotate state and scheduling in Chinese`**

### Task 5: 工具、MCP 与子代理系统注释

**Files:**
- Modify: `nanobot/agent/tools/`
- Modify: `nanobot/agent/subagent.py`
- Modify: `nanobot/skills/` 下具有 Python 运行逻辑的 `include` 文件

**Interfaces:**
- Consumes: AgentRunner 工具调用请求、RequestContext、WorkspaceScope 和应用持有的 MCP 生命周期。
- Produces: 工具发现、Schema、执行、并发、结果限制、权限与扩展机制注释。

- [ ] **Step 1: 复核内置工具扫描、entry point、MCP 注册与关闭顺序**
- [ ] **Step 2: 注释 Tool 基类、Registry 和模型可见 Schema**
- [ ] **Step 3: 注释文件、Shell、Web、MCP、Cron、媒体和运行时控制工具的能力边界**
- [ ] **Step 4: 注释子代理消息回注、并发和父子上下文隔离**
- [ ] **Step 5: 执行 Python 纯注释 token 等价审计与 `git diff --check`**
- [ ] **Step 6: 更新覆盖台账并提交 `docs: annotate tools and subagents in Chinese`**

### Task 6: Provider 与媒体模型适配注释

**Files:**
- Modify: `nanobot/providers/`
- Modify: `nanobot/audio/`

**Interfaces:**
- Consumes: Config、ModelPreset、AgentRunner 消息与 ToolCallRequest。
- Produces: Provider 注册、选择、协议适配、流式解析、会话状态、回退和媒体能力注释。

- [ ] **Step 1: 从 Registry 和 Factory 追踪每类 Provider 的构造路径**
- [ ] **Step 2: 注释公共 Provider 契约与请求/响应标准化**
- [ ] **Step 3: 注释 OpenAI 兼容路径与专用 Provider 分支的差异原因**
- [ ] **Step 4: 注释流式事件、工具参数、防损坏重放、重试和 fallback**
- [ ] **Step 5: 注释图像生成与音频转写的独立能力路径**
- [ ] **Step 6: 执行 Python 纯注释 token 等价审计与 `git diff --check`**
- [ ] **Step 7: 更新覆盖台账并提交 `docs: annotate providers and media in Chinese`**

### Task 7: Channel 与 WebSocket 传输注释

**Files:**
- Modify: `nanobot/channels/base.py`
- Modify: `nanobot/channels/manager.py`
- Modify: `nanobot/channels/registry.py`
- Modify: `nanobot/channels/` 各平台包中的 `include` 文件
- Modify: `nanobot/pairing/`

**Interfaces:**
- Consumes: 外部平台事件、MessageBus、Channel 配置和配对状态。
- Produces: 平台协议到统一消息模型的转换、发送策略、流式能力和生命周期注释。

- [ ] **Step 1: 复核 ChannelPlugin 发现、多实例命名、依赖加载和启动/停止顺序**
- [ ] **Step 2: 注释 BaseChannel 与 Manager 的职责边界**
- [ ] **Step 3: 按平台注释鉴权、事件转换、媒体处理、分段、流式与重试差异**
- [ ] **Step 4: 深入注释 WebSocket 复用协议、Gateway 服务组合与 WebUI 专用事件**
- [ ] **Step 5: 执行 Python 纯注释 token 等价审计与 `git diff --check`**
- [ ] **Step 6: 更新覆盖台账并提交 `docs: annotate channels in Chinese`**

### Task 8: CLI、SDK、Gateway、API 与后端 WebUI 服务注释

**Files:**
- Modify: `nanobot/cli/`
- Modify: `nanobot/command/`
- Modify: `nanobot/sdk/`
- Modify: `nanobot/gateway/`
- Modify: `nanobot/api/`
- Modify: `nanobot/apps/`
- Modify: `nanobot/webui/`
- Modify: `nanobot/nanobot.py`
- Modify: `nanobot/process_runtime.py`
- Modify: `nanobot/__main__.py`

**Interfaces:**
- Consumes: 已注释的核心、Provider、Channel、Tool 与状态子系统。
- Produces: 各公开入口如何组合相同运行时能力，以及 WebUI 后端 REST/WS 服务如何分工的注释。

- [ ] **Step 1: 复核各入口创建、共享和关闭 Runtime、ToolRegistry 与 MCPProvider 的方式**
- [ ] **Step 2: 注释 CLI 命令、SDK 同步/异步接口和 OpenAI 兼容 API 的适配层**
- [ ] **Step 3: 注释 Gateway 后台服务、Channel 生命周期和健康检查**
- [ ] **Step 4: 注释 WebUI 后端鉴权、设置、工作区、媒体、转录与临时会话服务**
- [ ] **Step 5: 执行 Python 纯注释 token 等价审计与 `git diff --check`**
- [ ] **Step 6: 更新覆盖台账并提交 `docs: annotate public entrypoints in Chinese`**

### Task 9: WebUI 基础协议与状态层注释

**Files:**
- Modify: `webui/src/lib/` 中所有 `include` 文件
- Modify: `webui/src/hooks/` 中所有 `include` 文件
- Modify: `webui/src/providers/` 中所有 `include` 文件
- Modify: `webui/src/types/` 中所有 `include` 文件
- Modify: `webui/src/workers/` 中所有 `include` 文件
- Modify: `webui/src/channel-plugins/` 中所有 `include` 文件

**Interfaces:**
- Consumes: Python Gateway 的 HTTP/WebSocket 协议与浏览器运行时。
- Produces: 客户端连接、请求关联、重连、会话状态、工作区、临时聊天和公共 Hook 的注释。

- [ ] **Step 1: 对照 Python 事件类型复核 TypeScript 协议联合类型**
- [ ] **Step 2: 注释 NanobotClient 的单连接多会话、请求关联、缓存、重连和状态围栏**
- [ ] **Step 3: 注释 REST 包装、bootstrap、runtime、workspace 与本地持久化工具**
- [ ] **Step 4: 注释 Context 与 Hook 的状态所有权、订阅和清理时机**
- [ ] **Step 5: 执行 TypeScript/TSX 纯注释 token 等价审计与 `git diff --check`**
- [ ] **Step 6: 更新覆盖台账并提交 `docs: annotate webui protocol layer in Chinese`**

### Task 10: WebUI 组件与交互层注释

**Files:**
- Modify: `webui/src/App.tsx`
- Modify: `webui/src/main.tsx`
- Modify: `webui/src/components/` 中所有 `include` 文件
- Modify: `webui/src/i18n/` 中具有运行逻辑的 `include` 文件

**Interfaces:**
- Consumes: Task 9 的客户端、Context、Hook、类型与工具函数。
- Produces: Shell 路由、会话列表、消息流、Composer、工作台、设置、自动化、技能和渠道配置 UI 的注释。

- [ ] **Step 1: 从 App Shell 追踪路由、启动鉴权、ClientProvider 与全局状态**
- [ ] **Step 2: 注释会话选择、消息订阅、运行进度和临时聊天流程**
- [ ] **Step 3: 注释工作台多窗格模型与持久化不变量**
- [ ] **Step 4: 注释设置、Provider、Channel、Apps、Automation 和 Skills 界面数据流**
- [ ] **Step 5: 对业务无关 UI 原语执行排除复核，不添加机械注释**
- [ ] **Step 6: 执行 TypeScript/TSX 纯注释 token 等价审计与 `git diff --check`**
- [ ] **Step 7: 更新覆盖台账并提交 `docs: annotate webui components in Chinese`**

### Task 11: 全局一致性与最终纯注释审计

**Files:**
- Modify: `docs/code-commentary/architecture-map.md`
- Modify: `docs/code-commentary/coverage.md`
- Inspect: Task 2–10 修改的全部 Python、TypeScript 和 TSX 文件

**Interfaces:**
- Consumes: 所有批次注释和覆盖台账。
- Produces: 术语一致、覆盖完整、只有注释 token 发生变化的最终分支。

- [ ] **Step 1: 检查覆盖完整性**

  将当前 Git 跟踪的候选源码重新与 `coverage.md` 比较，确保每个文件恰好有一个 `include` 或 `exclude` 状态，且所有 `include` 文件均已处理。

- [ ] **Step 2: 检查中文教学质量**

  搜索核心术语和跨模块引用，统一 AgentLoop、AgentRunner、turn、session、runtime、workspace scope、provider、channel、tool 等称谓；删除逐行复述、无依据原因和过密注释。

- [ ] **Step 3: 对整个分支执行 Python token 等价审计**

  使用标准库 `tokenize` 分别读取 `git show 6bf08edf:<path>` 与工作树文件，忽略 `COMMENT`、`NL`、`ENCODING` 和位置字段后比较 `(token_type, token_string)` 序列。任一文件不一致即停止并修复差异。

- [ ] **Step 4: 对整个分支执行 TypeScript/TSX token 等价审计**

  使用项目已有 TypeScript scanner，以 `skipTrivia=true` 分别扫描基线内容和工作树内容，并比较每个非 trivia token 的 kind 与原文。不得用正则去除注释。任一文件不一致即停止并修复差异。

- [ ] **Step 5: 执行 Git 差异检查**

  运行 `git diff --check 6bf08edf...HEAD`，并逐文件审阅源码 diff，确认源码中只有新增注释行；不运行测试、类型检查或构建。

- [ ] **Step 6: 完成最终记录并提交**

  在 `coverage.md` 记录处理数量、排除数量、各批次提交和两类 token 审计结果，然后提交：

  ```bash
  git add docs/code-commentary/architecture-map.md docs/code-commentary/coverage.md
  git commit -m "docs: finalize Chinese code commentary audit"
  ```
