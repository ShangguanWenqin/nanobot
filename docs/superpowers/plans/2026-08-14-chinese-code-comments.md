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

### Task 2: B1 核心代理与消息总线注释

**Files:**
- Modify: `docs/code-commentary/coverage.md` 中批次为 `B1` 且状态为 `include` 的 21 个文件

**Interfaces:**
- Consumes: Task 1 的主消息链路、turn 阶段和状态所有权。
- Produces: AgentLoop/AgentRunner、context、hook、delivery、MessageBus 与 runtime event 的统一中文解释。

- [ ] **Step 1: 阅读 B1 全部 21 个 include 文件及其必要直接调用者**
- [ ] **Step 2: 注释模块位置、turn 上下文、消息/事件接口和数据所有权**
- [ ] **Step 3: 注释 restore→compact→command→build→run→save→respond、provider/tool 内环、流式交付与注入边界**
- [ ] **Step 4: 注释 Hook 隔离、模型运行时快照、插件/技能与子代理回注的原因边界**
- [ ] **Step 5: 执行 21/21 Python 纯注释 token 等价、源码 diff 仅新增注释和 `git diff --check` 审计**
- [ ] **Step 6: 审计通过后更新覆盖台账并提交 `docs: annotate agent core and bus in Chinese`**

### Task 3: B2 工具、扩展与安全边界注释

**Files:**
- Modify: `docs/code-commentary/coverage.md` 中批次为 `B2` 且状态为 `include` 的 35 个文件

**Interfaces:**
- Consumes: B1 的 ContextVar、hook、checkpoint 与 delivery 契约。
- Produces: ToolRegistry、文件/Shell/Web/MCP、App、workspace、SSRF 与 sandbox 边界注释。

- [ ] **Step 1: 阅读 B2 全部 include 文件及其必要直接调用者**
- [ ] **Step 2: 注释工具发现、Schema、prepare/execute、并发和结果限制**
- [ ] **Step 3: 注释路径能力、Shell 限制、SSRF、MCP 生命周期和应用进程边界**
- [ ] **Step 4: 区分应用级 workspace guard 与系统级 sandbox，不夸大安全保证**
- [ ] **Step 5: 执行 Python 纯注释 token 等价、源码 diff 仅新增注释和 `git diff --check` 审计**
- [ ] **Step 6: 审计通过后更新覆盖台账并提交 `docs: annotate tools and security in Chinese`**

### Task 4: B3 Provider、模型与媒体注释

**Files:**
- Modify: `docs/code-commentary/coverage.md` 中批次为 `B3` 且状态为 `include` 的 24 个文件

**Interfaces:**
- Consumes: B1 的 provider request/response、tool call 和 conversation state 语义。
- Produces: Provider registry/factory、后端兼容、Responses 协议、图像生成和音频转写注释。

- [ ] **Step 1: 阅读 B3 全部 include 文件及其必要直接调用者**
- [ ] **Step 2: 注释 Provider 公共契约、注册顺序、构造分派和不可变快照**
- [ ] **Step 3: 注释专用后端、OpenAI-compatible、Responses continuation 与 fallback 差异**
- [ ] **Step 4: 注释流式解析、reasoning/tool 参数、重试与媒体能力边界**
- [ ] **Step 5: 执行 Python 纯注释 token 等价、源码 diff 仅新增注释和 `git diff --check` 审计**
- [ ] **Step 6: 审计通过后更新覆盖台账并提交 `docs: annotate providers and media in Chinese`**

### Task 5: B4 Channel 与配对注释

**Files:**
- Modify: `docs/code-commentary/coverage.md` 中批次为 `B4` 且状态为 `include` 的 61 个文件

**Interfaces:**
- Consumes: B1 的 InboundMessage、OutboundEvent、MessageBus 与 delivery 契约。
- Produces: Channel 发现/生命周期、平台适配、WebSocket 鉴权与 DM 配对注释。

- [ ] **Step 1: 阅读 B4 全部 include 文件及其必要直接调用者**
- [ ] **Step 2: 注释 manifest/registry、BaseChannel、Manager 与多实例生命周期**
- [ ] **Step 3: 按平台注释事件转换、媒体、分段、流式、重试和协议限制**
- [ ] **Step 4: 注释 WebSocket 握手信任边界与 pairing store 的 fail-closed/原子写语义**
- [ ] **Step 5: 执行 Python 纯注释 token 等价、源码 diff 仅新增注释和 `git diff --check` 审计**
- [ ] **Step 6: 审计通过后更新覆盖台账并提交 `docs: annotate channels and pairing in Chinese`**

### Task 6: B5 会话、记忆与自动化注释

**Files:**
- Modify: `docs/code-commentary/coverage.md` 中批次为 `B5` 且状态为 `include` 的 24 个文件

**Interfaces:**
- Consumes: B1/B4 的 turn 生命周期、消息路由与工作区命名空间。
- Produces: Session、Memory、Goal、Cron、Trigger、压缩/恢复和持久化所有权注释。

- [ ] **Step 1: 阅读 B5 全部 include 文件及其必要直接调用者**
- [ ] **Step 2: 注释 Session 与 Memory 的不同权威存储、锁和原子写路径**
- [ ] **Step 3: 注释 Goal、Cron、Automation、Trigger 与后续 turn 协调**
- [ ] **Step 4: 注释压缩、provider state、崩溃恢复、历史可见性和并发防护**
- [ ] **Step 5: 执行 Python 纯注释 token 等价、源码 diff 仅新增注释和 `git diff --check` 审计**
- [ ] **Step 6: 审计通过后更新覆盖台账并提交 `docs: annotate sessions and automation in Chinese`**

### Task 7: B6 组合根、配置、CLI/API/SDK 与共享基础注释

**Files:**
- Modify: `docs/code-commentary/coverage.md` 中批次为 `B6` 且状态为 `include` 的 57 个文件

**Interfaces:**
- Consumes: B1–B5 已稳定的核心、Provider、Tool、Channel 与状态构件。
- Produces: 进程入口、装配/清理、配置、命令、SDK、API、Gateway 和通用 utils 注释。

- [ ] **Step 1: 阅读 B6 全部 include 文件及其必要直接调用者**
- [ ] **Step 2: 注释 CLI/SDK/Gateway/API 组合根的构造、共享和关闭顺序**
- [ ] **Step 3: 注释配置别名、环境解析、动态失效、公开适配层和进程身份边界**
- [ ] **Step 4: 注释共享 utils 的单一职责，不复制下游子系统内部说明**
- [ ] **Step 5: 执行 Python 纯注释 token 等价、源码 diff 仅新增注释和 `git diff --check` 审计**
- [ ] **Step 6: 审计通过后更新覆盖台账并提交 `docs: annotate composition and config in Chinese`**

### Task 8: B7 WebUI 后端与协议服务注释

**Files:**
- Modify: `docs/code-commentary/coverage.md` 中批次为 `B7` 且状态为 `include` 的 39 个文件

**Interfaces:**
- Consumes: B4/B5/B6 的 Channel、Session、配置与 Gateway 生命周期。
- Produces: WebUI HTTP/WS 配套服务、transcript、settings、workspace、media 和 token 注释。

- [ ] **Step 1: 阅读 B7 全部 include 文件及其必要直接调用者**
- [ ] **Step 2: 注释 bootstrap/WS 鉴权、请求路由、mutation 权限和异步复核边界**
- [ ] **Step 3: 注释 transcript/session 索引、临时聊天、workspace/media/token 服务**
- [ ] **Step 4: 注释 settings controller/service、动态应用与需重启能力的所有权**
- [ ] **Step 5: 执行 Python 纯注释 token 等价、源码 diff 仅新增注释和 `git diff --check` 审计**
- [ ] **Step 6: 审计通过后更新覆盖台账并提交 `docs: annotate webui backend in Chinese`**

### Task 9: B8 WebUI 外壳、设置与工作台注释

**Files:**
- Modify: `docs/code-commentary/coverage.md` 中批次为 `B8` 且状态为 `include` 的 81 个文件

**Interfaces:**
- Consumes: B7 的 bootstrap、settings、session 与 workspace 协议。
- Produces: App/Shell、导航、settings、workbench、i18n 和浏览器状态层注释。

- [ ] **Step 1: 阅读 B8 全部 include 文件及其必要直接调用者**
- [ ] **Step 2: 从 App Shell 注释启动鉴权、单 ClientProvider、路由和全局状态所有权**
- [ ] **Step 3: 注释 session/settings/provider/channel/apps/automation/skills 数据流**
- [ ] **Step 4: 注释工作台布局、浏览器状态、本地持久化、i18n 与刷新边界**
- [ ] **Step 5: 执行 TypeScript/TSX 纯注释 token 等价、源码 diff 仅新增注释和 `git diff --check` 审计**
- [ ] **Step 6: 审计通过后更新覆盖台账并提交 `docs: annotate webui shell and settings in Chinese`**

### Task 10: B9 WebUI 对话、流式渲染与媒体注释

**Files:**
- Modify: `docs/code-commentary/coverage.md` 中批次为 `B9` 且状态为 `include` 的 70 个文件

**Interfaces:**
- Consumes: B1/B7 的 turn/event 语义与 B8 的 App/Shell 状态层。
- Produces: NanobotClient、对话状态、activity、消息渲染、附件与语音交互注释。

- [ ] **Step 1: 阅读 B9 全部 include 文件及其必要直接调用者**
- [ ] **Step 2: 对照 Python 事件注释 NanobotClient 的连接、请求关联、重连、generation/fence 和流聚合**
- [ ] **Step 3: 注释 thread/activity/run、消息列表、Composer 与临时聊天流程**
- [ ] **Step 4: 注释附件、预览、音频转写、媒体渲染和迟到事件防护**
- [ ] **Step 5: 对业务无关 UI 原语执行排除复核，不添加机械注释**
- [ ] **Step 6: 执行 TypeScript/TSX 纯注释 token 等价、源码 diff 仅新增注释和 `git diff --check` 审计**
- [ ] **Step 7: 审计通过后更新覆盖台账并提交 `docs: annotate webui chat and media in Chinese`**

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
