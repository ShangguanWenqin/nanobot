# 中文代码注释覆盖台账

本台账以当前分支的 Git 跟踪运行时代码为唯一候选源，给后续中文注释批次提供逐文件、无歧义的范围。架构语义和批次依赖见 [`architecture-map.md`](architecture-map.md)。

## 1. 候选生成规则

在仓库根目录用 Git pathspec 枚举：

```bash
git ls-files \
  'nanobot/*.py' 'nanobot/**/*.py' \
  'webui/src/*.ts' 'webui/src/*.tsx' \
  'webui/src/**/*.ts' 'webui/src/**/*.tsx' | sort -u
```

规则按两层执行：

1. **全局预过滤**：排除 `tests/`、`test_*.py`、`*.test.ts(x)`、`*.spec.ts(x)`，以及缓存、构建、生成目录和本地化文案。当前清单中 136 项全是测试（Python 65、WebUI 71）；没有命中缓存/构建/生成目录。翻译正文是 JSON，不在本任务的 TS/TSX 候选后缀内；`webui/src/i18n/*.ts` 与 channel locale registry 是运行时加载逻辑，保留。
2. **逐文件分类**：对预过滤后的每项标 `include`/`exclude`。空包标记、纯聚合/兼容重导出、第三方兼容声明和无业务逻辑的生成式 UI 原语明确排除；其余文件归入且只归入一个注释批次。

## 2. 统计与闭合检查

| 项目 | Python | WebUI TS/TSX | 合计 |
| --- | ---: | ---: | ---: |
| Git 跟踪候选 | 366 | 236 | 602 |
| 全局预过滤 | 65 | 71 | 136 |
| 进入逐文件台账 | 301 | 165 | 466 |
| `include` | 261 | 151 | 412 |
| `exclude` | 40 | 14 | 54 |

批次计数：B1 21、B2 35、B3 24、B4 61、B5 24、B6 57、B7 39、B8 81、B9 70；合计 412。`include + exclude = 466`，与预过滤后候选数一致；没有空状态、空批次、重复路径或未分类条目。

### 2.1 处理进度

| 批次 | 状态 | 已处理 `include` | 新增中文注释行 | 静态审计 |
| --- | --- | ---: | ---: | --- |
| B1 | 已完成 | 21 / 21 | 131 | Python 非注释 token 21 / 21 等价；无 Python 内容行新增或删除 |
| B2 | 已完成 | 35 / 35 | 185 | Python 非注释 token 35 / 35 等价；无 Python 内容行新增或删除 |
| B3 | 已完成 | 24 / 24 | 99 | Python 非注释 token 24 / 24 等价；无 Python 内容行新增或删除 |
| B4 | 已完成 | 61 / 61 | 90 | Python 非注释 token 61 / 61 等价；无 Python 内容行新增或删除 |
| B5 | 已完成 | 24 / 24 | 76 | Python 非注释 token 24 / 24 等价；无 Python 内容行新增或删除 |
| B6 | 已完成 | 57 / 57 | 59 | Python 非注释 token 57 / 57 等价；无 Python 内容行新增或删除 |
| B7 | 已完成 | 39 / 39 | 39 | Python 非注释 token 39 / 39 等价；无 Python 内容行新增或删除 |

当前完成 7 / 9 个批次、261 / 412 个 `include` 文件；其余 151 个 `include` 文件保持待处理。

## 3. 批次定义

| 批次 | 范围 | 主要依赖/边界 |
| --- | --- | --- |
| B1 | 核心代理与消息总线 | AgentLoop/Runner、context、hook、delivery、bus/runtime events |
| B2 | 工具、扩展与安全边界 | ToolRegistry、filesystem/shell/web/MCP、app、workspace/SSRF/sandbox |
| B3 | Provider、模型与媒体 | Provider registry/factory/后端兼容、Responses 协议、音频 |
| B4 | Channel 与配对 | BaseChannel、manager、各平台 manifest/runtime/validation、WebSocket、pairing |
| B5 | 会话、记忆与自动化 | Session、Memory、Goal、Cron、Trigger、compact/恢复/持久化 |
| B6 | 组合根、配置、CLI/API/SDK 与共享基础 | 进程入口、装配/清理、配置、命令、SDK、通用 utils |
| B7 | WebUI 后端与协议服务 | HTTP/WS 配套服务、transcript、settings、workspace/media/token |
| B8 | WebUI 外壳、设置与工作台 | App/Shell、导航、settings、workbench、i18n、浏览器状态 |
| B9 | WebUI 对话、流式渲染与媒体 | NanobotClient、协议类型、thread/activity、消息渲染、附件/语音 |

批次为 `—` 的行必须是 `exclude`；`include` 行的理由列用 `—`，具体注释关注点由批次定义和认知地图决定。

## 4. 逐文件台账

| 文件 | 状态 | 批次 | 排除理由 |
| --- | --- | --- | --- |
| `nanobot/__init__.py` | `include` | `B6` | — |
| `nanobot/__main__.py` | `include` | `B6` | — |
| `nanobot/agent/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/agent/autocompact.py` | `include` | `B5` | — |
| `nanobot/agent/automation_turns.py` | `include` | `B5` | — |
| `nanobot/agent/context.py` | `include` | `B1` | — |
| `nanobot/agent/context_governance.py` | `include` | `B1` | — |
| `nanobot/agent/cron_turns.py` | `include` | `B5` | — |
| `nanobot/agent/goal_permission.py` | `include` | `B1` | — |
| `nanobot/agent/hook.py` | `include` | `B1` | — |
| `nanobot/agent/hooks/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/agent/hooks/file_edit_activity.py` | `include` | `B1` | — |
| `nanobot/agent/loop.py` | `include` | `B1` | — |
| `nanobot/agent/memory.py` | `include` | `B5` | — |
| `nanobot/agent/model_presets.py` | `include` | `B1` | — |
| `nanobot/agent/model_runtime.py` | `include` | `B1` | — |
| `nanobot/agent/plugins.py` | `include` | `B1` | — |
| `nanobot/agent/progress_hook.py` | `include` | `B1` | — |
| `nanobot/agent/runner.py` | `include` | `B1` | — |
| `nanobot/agent/skills.py` | `include` | `B1` | — |
| `nanobot/agent/subagent.py` | `include` | `B1` | — |
| `nanobot/agent/tools/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/agent/tools/_windows_job.py` | `include` | `B2` | — |
| `nanobot/agent/tools/apply_patch.py` | `include` | `B2` | — |
| `nanobot/agent/tools/base.py` | `include` | `B2` | — |
| `nanobot/agent/tools/cli_apps.py` | `include` | `B2` | — |
| `nanobot/agent/tools/context.py` | `include` | `B2` | — |
| `nanobot/agent/tools/cron.py` | `include` | `B2` | — |
| `nanobot/agent/tools/exec_session.py` | `include` | `B2` | — |
| `nanobot/agent/tools/file_state.py` | `include` | `B2` | — |
| `nanobot/agent/tools/filesystem.py` | `include` | `B2` | — |
| `nanobot/agent/tools/image_generation.py` | `include` | `B2` | — |
| `nanobot/agent/tools/loader.py` | `include` | `B2` | — |
| `nanobot/agent/tools/long_task.py` | `include` | `B2` | — |
| `nanobot/agent/tools/mcp.py` | `include` | `B2` | — |
| `nanobot/agent/tools/mcp_oauth.py` | `include` | `B2` | — |
| `nanobot/agent/tools/message.py` | `include` | `B2` | — |
| `nanobot/agent/tools/path_utils.py` | `include` | `B2` | — |
| `nanobot/agent/tools/registry.py` | `include` | `B2` | — |
| `nanobot/agent/tools/runtime_control.py` | `include` | `B2` | — |
| `nanobot/agent/tools/sandbox.py` | `include` | `B2` | — |
| `nanobot/agent/tools/schema.py` | `include` | `B2` | — |
| `nanobot/agent/tools/search.py` | `include` | `B2` | — |
| `nanobot/agent/tools/self.py` | `include` | `B2` | — |
| `nanobot/agent/tools/sessions.py` | `include` | `B2` | — |
| `nanobot/agent/tools/shell.py` | `include` | `B2` | — |
| `nanobot/agent/tools/spawn.py` | `include` | `B2` | — |
| `nanobot/agent/tools/web.py` | `include` | `B2` | — |
| `nanobot/agent/turn_delivery.py` | `include` | `B1` | — |
| `nanobot/agent/turn_hooks.py` | `include` | `B1` | — |
| `nanobot/api/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/api/runtime.py` | `include` | `B6` | — |
| `nanobot/api/server.py` | `include` | `B6` | — |
| `nanobot/apps/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/apps/cli/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/apps/cli/service.py` | `include` | `B2` | — |
| `nanobot/apps/cli/utils.py` | `include` | `B2` | — |
| `nanobot/apps/protocol.py` | `include` | `B2` | — |
| `nanobot/audio/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/audio/transcription.py` | `include` | `B3` | — |
| `nanobot/audio/transcription_registry.py` | `include` | `B3` | — |
| `nanobot/bus/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/bus/events.py` | `include` | `B1` | — |
| `nanobot/bus/outbound_events.py` | `include` | `B1` | — |
| `nanobot/bus/progress.py` | `include` | `B1` | — |
| `nanobot/bus/queue.py` | `include` | `B1` | — |
| `nanobot/bus/runtime_events.py` | `include` | `B1` | — |
| `nanobot/channels/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/_manifest.py` | `include` | `B4` | — |
| `nanobot/channels/_setup.py` | `include` | `B4` | — |
| `nanobot/channels/base.py` | `include` | `B4` | — |
| `nanobot/channels/connect.py` | `include` | `B4` | — |
| `nanobot/channels/contracts.py` | `include` | `B4` | — |
| `nanobot/channels/dingtalk/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/dingtalk/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/dingtalk/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/discord/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/discord/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/discord/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/discord/validation.py` | `include` | `B4` | — |
| `nanobot/channels/email/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/email/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/email/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/email/validation.py` | `include` | `B4` | — |
| `nanobot/channels/feishu/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/feishu/config.py` | `include` | `B4` | — |
| `nanobot/channels/feishu/connect.py` | `include` | `B4` | — |
| `nanobot/channels/feishu/instances.py` | `include` | `B4` | — |
| `nanobot/channels/feishu/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/feishu/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/feishu/validation.py` | `include` | `B4` | — |
| `nanobot/channels/feishu/websocket.py` | `include` | `B4` | — |
| `nanobot/channels/manager.py` | `include` | `B4` | — |
| `nanobot/channels/matrix/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/matrix/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/matrix/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/matrix/validation.py` | `include` | `B4` | — |
| `nanobot/channels/mattermost/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/mattermost/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/mattermost/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/mochat/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/mochat/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/mochat/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/msteams/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/msteams/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/msteams/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/napcat/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/napcat/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/napcat/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/plugin.py` | `include` | `B4` | — |
| `nanobot/channels/qq/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/qq/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/qq/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/registry.py` | `include` | `B4` | — |
| `nanobot/channels/signal/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/signal/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/signal/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/slack/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/slack/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/slack/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/slack/validation.py` | `include` | `B4` | — |
| `nanobot/channels/telegram/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/telegram/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/telegram/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/telegram/validation.py` | `include` | `B4` | — |
| `nanobot/channels/validation.py` | `include` | `B4` | — |
| `nanobot/channels/websocket/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/websocket/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/websocket/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/websocket/validation.py` | `include` | `B4` | — |
| `nanobot/channels/wecom/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/wecom/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/wecom/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/weixin/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/weixin/connect.py` | `include` | `B4` | — |
| `nanobot/channels/weixin/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/weixin/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/weixin/state.py` | `include` | `B4` | — |
| `nanobot/channels/weixin/validation.py` | `include` | `B4` | — |
| `nanobot/channels/whatsapp/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/channels/whatsapp/manifest.py` | `include` | `B4` | — |
| `nanobot/channels/whatsapp/runtime.py` | `include` | `B4` | — |
| `nanobot/channels/whatsapp/state.py` | `include` | `B4` | — |
| `nanobot/channels/whatsapp/validation.py` | `include` | `B4` | — |
| `nanobot/cli/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/cli/agent.py` | `include` | `B6` | — |
| `nanobot/cli/commands.py` | `include` | `B6` | — |
| `nanobot/cli/gateway.py` | `include` | `B6` | — |
| `nanobot/cli/gateway_runtime.py` | `include` | `B6` | — |
| `nanobot/cli/log_control.py` | `include` | `B6` | — |
| `nanobot/cli/models.py` | `include` | `B6` | — |
| `nanobot/cli/onboard.py` | `include` | `B6` | — |
| `nanobot/cli/provider.py` | `include` | `B6` | — |
| `nanobot/cli/runtime_config.py` | `include` | `B6` | — |
| `nanobot/cli/stream.py` | `include` | `B6` | — |
| `nanobot/cli/terminal.py` | `include` | `B6` | — |
| `nanobot/cli/webui.py` | `include` | `B6` | — |
| `nanobot/cli/webui_support.py` | `include` | `B6` | — |
| `nanobot/command/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/command/builtin.py` | `include` | `B6` | — |
| `nanobot/command/router.py` | `include` | `B6` | — |
| `nanobot/config/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/config/errors.py` | `include` | `B6` | — |
| `nanobot/config/loader.py` | `include` | `B6` | — |
| `nanobot/config/paths.py` | `include` | `B6` | — |
| `nanobot/config/schema.py` | `include` | `B6` | — |
| `nanobot/config/timezone.py` | `include` | `B6` | — |
| `nanobot/config/watcher.py` | `include` | `B6` | — |
| `nanobot/config_base.py` | `include` | `B6` | — |
| `nanobot/cron/__init__.py` | `include` | `B5` | — |
| `nanobot/cron/bound_runner.py` | `include` | `B5` | — |
| `nanobot/cron/service.py` | `include` | `B5` | — |
| `nanobot/cron/session_delivery.py` | `include` | `B5` | — |
| `nanobot/cron/session_turns.py` | `include` | `B5` | — |
| `nanobot/cron/types.py` | `include` | `B5` | — |
| `nanobot/cron/webui_metadata.py` | `include` | `B5` | — |
| `nanobot/gateway/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/gateway/runtime.py` | `include` | `B6` | — |
| `nanobot/gateway/service.py` | `include` | `B6` | — |
| `nanobot/nanobot.py` | `include` | `B6` | — |
| `nanobot/optional_features.py` | `include` | `B6` | — |
| `nanobot/pairing/__init__.py` | `include` | `B4` | — |
| `nanobot/pairing/store.py` | `include` | `B4` | — |
| `nanobot/process_runtime.py` | `include` | `B6` | — |
| `nanobot/providers/__init__.py` | `include` | `B3` | — |
| `nanobot/providers/anthropic_provider.py` | `include` | `B3` | — |
| `nanobot/providers/azure_openai_provider.py` | `include` | `B3` | — |
| `nanobot/providers/base.py` | `include` | `B3` | — |
| `nanobot/providers/bedrock_provider.py` | `include` | `B3` | — |
| `nanobot/providers/conversation_state.py` | `include` | `B3` | — |
| `nanobot/providers/factory.py` | `include` | `B3` | — |
| `nanobot/providers/fallback_provider.py` | `include` | `B3` | — |
| `nanobot/providers/github_copilot_provider.py` | `include` | `B3` | — |
| `nanobot/providers/image_generation.py` | `include` | `B3` | — |
| `nanobot/providers/oauth_guidance.py` | `include` | `B3` | — |
| `nanobot/providers/openai_codex_oauth.py` | `include` | `B3` | — |
| `nanobot/providers/openai_codex_provider.py` | `include` | `B3` | — |
| `nanobot/providers/openai_compat_provider.py` | `include` | `B3` | — |
| `nanobot/providers/openai_responses/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/providers/openai_responses/converters.py` | `include` | `B3` | — |
| `nanobot/providers/openai_responses/parsing.py` | `include` | `B3` | — |
| `nanobot/providers/openai_responses/state.py` | `include` | `B3` | — |
| `nanobot/providers/registry.py` | `include` | `B3` | — |
| `nanobot/providers/transcription.py` | `include` | `B3` | — |
| `nanobot/providers/unconfigured_provider.py` | `include` | `B3` | — |
| `nanobot/providers/xai_grok_provider.py` | `include` | `B3` | — |
| `nanobot/providers/xai_oauth.py` | `include` | `B3` | — |
| `nanobot/runtime_context.py` | `include` | `B1` | — |
| `nanobot/sdk/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/sdk/clients.py` | `include` | `B6` | — |
| `nanobot/sdk/runtime.py` | `include` | `B6` | — |
| `nanobot/sdk/streaming.py` | `include` | `B6` | — |
| `nanobot/sdk/types.py` | `include` | `B6` | — |
| `nanobot/security/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/security/network.py` | `include` | `B2` | — |
| `nanobot/security/workspace_access.py` | `include` | `B2` | — |
| `nanobot/security/workspace_policy.py` | `include` | `B2` | — |
| `nanobot/session/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/session/automation_turns.py` | `include` | `B5` | — |
| `nanobot/session/goal_state.py` | `include` | `B5` | — |
| `nanobot/session/history_visibility.py` | `include` | `B5` | — |
| `nanobot/session/keys.py` | `include` | `B5` | — |
| `nanobot/session/manager.py` | `include` | `B5` | — |
| `nanobot/session/model_selection.py` | `include` | `B5` | — |
| `nanobot/session/turn_continuation.py` | `include` | `B5` | — |
| `nanobot/session/webui_turns.py` | `include` | `B5` | — |
| `nanobot/skills/skill-creator/scripts/init_skill.py` | `include` | `B2` | — |
| `nanobot/skills/skill-creator/scripts/package_skill.py` | `include` | `B2` | — |
| `nanobot/skills/skill-creator/scripts/quick_validate.py` | `include` | `B2` | — |
| `nanobot/templates/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/templates/memory/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/triggers/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/triggers/local_runner.py` | `include` | `B5` | — |
| `nanobot/triggers/local_session_turns.py` | `include` | `B5` | — |
| `nanobot/triggers/local_store.py` | `include` | `B5` | — |
| `nanobot/triggers/local_turns.py` | `include` | `B5` | — |
| `nanobot/triggers/local_types.py` | `include` | `B5` | — |
| `nanobot/utils/__init__.py` | `include` | `B6` | — |
| `nanobot/utils/artifacts.py` | `include` | `B6` | — |
| `nanobot/utils/cancellation.py` | `include` | `B6` | — |
| `nanobot/utils/dict_keys.py` | `include` | `B6` | — |
| `nanobot/utils/document.py` | `include` | `B6` | — |
| `nanobot/utils/evaluator.py` | `include` | `B6` | — |
| `nanobot/utils/file_edit_events.py` | `include` | `B6` | — |
| `nanobot/utils/gitstore.py` | `include` | `B6` | — |
| `nanobot/utils/helpers.py` | `include` | `B6` | — |
| `nanobot/utils/llm_runtime.py` | `include` | `B6` | — |
| `nanobot/utils/logging_bridge.py` | `include` | `B6` | — |
| `nanobot/utils/media_decode.py` | `include` | `B6` | — |
| `nanobot/utils/path.py` | `include` | `B6` | — |
| `nanobot/utils/progress_events.py` | `include` | `B6` | — |
| `nanobot/utils/prompt_templates.py` | `include` | `B6` | — |
| `nanobot/utils/restart.py` | `include` | `B6` | — |
| `nanobot/utils/run_records.py` | `include` | `B6` | — |
| `nanobot/utils/runtime.py` | `include` | `B6` | — |
| `nanobot/utils/searchusage.py` | `include` | `B6` | — |
| `nanobot/utils/subagent_channel_display.py` | `include` | `B6` | — |
| `nanobot/utils/tool_hints.py` | `include` | `B6` | — |
| `nanobot/utils/workspace_prompts.py` | `include` | `B6` | — |
| `nanobot/web/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/webui/__init__.py` | `exclude` | — | 纯包标记或聚合导出，无独立运行时业务逻辑 |
| `nanobot/webui/attachment_ingress.py` | `include` | `B7` | — |
| `nanobot/webui/build.py` | `include` | `B7` | — |
| `nanobot/webui/cli_apps_api.py` | `include` | `B7` | — |
| `nanobot/webui/dev.py` | `include` | `B7` | — |
| `nanobot/webui/file_preview.py` | `include` | `B7` | — |
| `nanobot/webui/forking.py` | `include` | `B7` | — |
| `nanobot/webui/gateway_services.py` | `include` | `B7` | — |
| `nanobot/webui/gateway_tokens.py` | `include` | `B7` | — |
| `nanobot/webui/http_utils.py` | `include` | `B7` | — |
| `nanobot/webui/ingress_policy.py` | `include` | `B7` | — |
| `nanobot/webui/mcp_oauth_api.py` | `include` | `B7` | — |
| `nanobot/webui/mcp_presets_api.py` | `include` | `B7` | — |
| `nanobot/webui/mcp_presets_runtime.py` | `exclude` | — | 仅兼容性重导出 session_extra |
| `nanobot/webui/media_api.py` | `include` | `B7` | — |
| `nanobot/webui/media_gateway.py` | `include` | `B7` | — |
| `nanobot/webui/metadata.py` | `include` | `B7` | — |
| `nanobot/webui/nanobot_features_api.py` | `include` | `B7` | — |
| `nanobot/webui/native_folder_picker.py` | `include` | `B7` | — |
| `nanobot/webui/session_access.py` | `include` | `B7` | — |
| `nanobot/webui/session_automations.py` | `include` | `B7` | — |
| `nanobot/webui/session_list_index.py` | `include` | `B7` | — |
| `nanobot/webui/settings_api.py` | `include` | `B7` | — |
| `nanobot/webui/settings_capabilities.py` | `include` | `B7` | — |
| `nanobot/webui/settings_contracts.py` | `include` | `B7` | — |
| `nanobot/webui/settings_models.py` | `include` | `B7` | — |
| `nanobot/webui/settings_routes.py` | `include` | `B7` | — |
| `nanobot/webui/settings_services.py` | `include` | `B7` | — |
| `nanobot/webui/settings_system.py` | `include` | `B7` | — |
| `nanobot/webui/sidebar_state.py` | `include` | `B7` | — |
| `nanobot/webui/skills_api.py` | `include` | `B7` | — |
| `nanobot/webui/skills_marketplace.py` | `include` | `B7` | — |
| `nanobot/webui/temporary_chats.py` | `include` | `B7` | — |
| `nanobot/webui/thread_disk.py` | `include` | `B7` | — |
| `nanobot/webui/token_usage.py` | `include` | `B7` | — |
| `nanobot/webui/transcript.py` | `include` | `B7` | — |
| `nanobot/webui/transcription_ws.py` | `include` | `B7` | — |
| `nanobot/webui/version_check.py` | `include` | `B7` | — |
| `nanobot/webui/websocket_logging.py` | `include` | `B7` | — |
| `nanobot/webui/workspaces.py` | `include` | `B7` | — |
| `nanobot/webui/ws_http.py` | `include` | `B7` | — |
| `webui/src/App.tsx` | `include` | `B8` | — |
| `webui/src/channel-plugins/i18n.ts` | `include` | `B8` | — |
| `webui/src/channel-plugins/locale-registry.ts` | `include` | `B8` | — |
| `webui/src/channel-plugins/registry.ts` | `include` | `B8` | — |
| `webui/src/channel-plugins/types.ts` | `include` | `B8` | — |
| `webui/src/components/AttachmentTile.tsx` | `include` | `B9` | — |
| `webui/src/components/ChatList.tsx` | `include` | `B8` | — |
| `webui/src/components/CliAppMentionText.tsx` | `include` | `B9` | — |
| `webui/src/components/CodeBlock.tsx` | `include` | `B9` | — |
| `webui/src/components/ConnectionBadge.tsx` | `include` | `B8` | — |
| `webui/src/components/DeleteConfirm.tsx` | `include` | `B8` | — |
| `webui/src/components/FilePreviewAvailabilityContext.tsx` | `include` | `B9` | — |
| `webui/src/components/FilePreviewPanel.tsx` | `include` | `B9` | — |
| `webui/src/components/FileReferenceChip.tsx` | `include` | `B9` | — |
| `webui/src/components/ImageLightbox.tsx` | `include` | `B9` | — |
| `webui/src/components/InlineTokenHighlight.tsx` | `include` | `B9` | — |
| `webui/src/components/LanguageSwitcher.tsx` | `include` | `B8` | — |
| `webui/src/components/MarkdownText.tsx` | `include` | `B9` | — |
| `webui/src/components/MarkdownTextRenderer.tsx` | `include` | `B9` | — |
| `webui/src/components/MessageBubble.tsx` | `include` | `B9` | — |
| `webui/src/components/RenameChatDialog.tsx` | `include` | `B8` | — |
| `webui/src/components/SessionSearchDialog.tsx` | `include` | `B8` | — |
| `webui/src/components/Sidebar.tsx` | `include` | `B8` | — |
| `webui/src/components/SidebarSelectionHighlight.tsx` | `include` | `B8` | — |
| `webui/src/components/SlashCommandText.tsx` | `include` | `B9` | — |
| `webui/src/components/UserMessageText.tsx` | `include` | `B9` | — |
| `webui/src/components/settings/SettingsPage.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/SettingsSidebar.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/SettingsView.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/SkillsCatalogSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/SkillsMarketplace.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/ToggleButton.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/TokenUsageHeatmap.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/capabilities/ImageGenerationSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/capabilities/SecuritySettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/capabilities/TranscriptionSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/capabilities/WebSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/capabilities/useCapabilitySettingsActions.ts` | `include` | `B8` | — |
| `webui/src/components/settings/capabilities/useCapabilitySettingsState.ts` | `include` | `B8` | — |
| `webui/src/components/settings/channels/ChannelIdentity.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/channels/ChannelInstancesPanel.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/channels/ChannelQrConnectFlow.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/channels/ChannelSetupPanel.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/channels/ChannelSetupParts.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/channels/CredentialForm.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/channels/catalog.ts` | `include` | `B8` | — |
| `webui/src/components/settings/contracts.ts` | `include` | `B8` | — |
| `webui/src/components/settings/models/ModelsSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/models/ProviderSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/models/useModelSettingsActions.ts` | `include` | `B8` | — |
| `webui/src/components/settings/models/useModelSettingsEffects.ts` | `include` | `B8` | — |
| `webui/src/components/settings/models/useModelSettingsState.ts` | `include` | `B8` | — |
| `webui/src/components/settings/overview/OverviewSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/shared/ModelControls.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/shared/SettingsControls.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/system/AppsSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/system/AutomationsSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/system/ChannelsSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/system/McpManagementDialog.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/system/RuntimeSettings.tsx` | `include` | `B8` | — |
| `webui/src/components/settings/system/createSystemSettingsActions.ts` | `include` | `B8` | — |
| `webui/src/components/settings/system/useSystemSettingsEffects.ts` | `include` | `B8` | — |
| `webui/src/components/settings/system/useSystemSettingsState.ts` | `include` | `B8` | — |
| `webui/src/components/settings/useSettingsController.ts` | `include` | `B8` | — |
| `webui/src/components/thread/AgentActivityCluster.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/AssistantSelectionAction.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/ModelPresetBadge.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/PromptNavigator.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/PromptRail.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/SessionInfoPopover.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/StreamErrorNotice.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/ThreadComposer.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/ThreadHeader.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/ThreadMessages.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/ThreadShell.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/ThreadViewport.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/WorkspaceControls.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/activity/ActivityStep.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/activity/DiffPair.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/activity/DiffSyntaxHighlight.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/activity/FileEditRow.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/activity/GenericToolRun.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/activity/ReasoningRow.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/activity/ThinkingReasoningShell.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/activity/WebActivityRow.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/activity/WebSearchRun.tsx` | `include` | `B9` | — |
| `webui/src/components/thread/activity/activity-message-model.ts` | `include` | `B9` | — |
| `webui/src/components/thread/activity/activity-text.ts` | `include` | `B9` | — |
| `webui/src/components/thread/activity/generic-tool-model.ts` | `include` | `B9` | — |
| `webui/src/components/thread/activity/mcp-activity-model.ts` | `include` | `B9` | — |
| `webui/src/components/thread/activity/reasoning-preview.ts` | `include` | `B9` | — |
| `webui/src/components/thread/activity/trace-activity-model.ts` | `include` | `B9` | — |
| `webui/src/components/thread/activity/web-search-model.ts` | `include` | `B9` | — |
| `webui/src/components/thread/activity/web-url.ts` | `include` | `B9` | — |
| `webui/src/components/thread/promptNavigation.ts` | `include` | `B9` | — |
| `webui/src/components/thread/thread-camera.ts` | `include` | `B9` | — |
| `webui/src/components/thread/thread-motion.ts` | `include` | `B9` | — |
| `webui/src/components/ui/alert-dialog.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/button.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/combobox.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/dialog.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/dropdown-menu.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/floating-surface.ts` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/form-control.ts` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/input.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/popover.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/segmented-control.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/sheet.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/textarea.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/ui/tooltip.tsx` | `exclude` | — | 通用生成式 UI 原语，无产品业务逻辑 |
| `webui/src/components/workbench/PaneWorkbench.tsx` | `include` | `B8` | — |
| `webui/src/components/workbench/workbench-layout.ts` | `include` | `B8` | — |
| `webui/src/components/workbench/workbench-model.ts` | `include` | `B8` | — |
| `webui/src/hooks/useAttachedImages.ts` | `include` | `B9` | — |
| `webui/src/hooks/useClipboardAndDrop.ts` | `include` | `B9` | — |
| `webui/src/hooks/useDeferredTitleRefresh.ts` | `include` | `B8` | — |
| `webui/src/hooks/useFileEditDisplayMode.ts` | `include` | `B9` | — |
| `webui/src/hooks/useLogoFallback.ts` | `include` | `B8` | — |
| `webui/src/hooks/useMediaQuery.ts` | `include` | `B8` | — |
| `webui/src/hooks/useNanobotStream.ts` | `include` | `B9` | — |
| `webui/src/hooks/usePageVisibility.ts` | `include` | `B8` | — |
| `webui/src/hooks/useSessionAutomationJobs.ts` | `include` | `B8` | — |
| `webui/src/hooks/useSessions.ts` | `include` | `B8` | — |
| `webui/src/hooks/useSidebarState.ts` | `include` | `B8` | — |
| `webui/src/hooks/useSkills.ts` | `include` | `B8` | — |
| `webui/src/hooks/useTheme.ts` | `include` | `B8` | — |
| `webui/src/hooks/useVoiceRecorder.ts` | `include` | `B9` | — |
| `webui/src/i18n/config.ts` | `include` | `B8` | — |
| `webui/src/i18n/index.ts` | `include` | `B8` | — |
| `webui/src/lib/activity-timeline.ts` | `include` | `B9` | — |
| `webui/src/lib/ansi.ts` | `include` | `B9` | — |
| `webui/src/lib/api.ts` | `include` | `B8` | — |
| `webui/src/lib/bootstrap.ts` | `include` | `B8` | — |
| `webui/src/lib/chat-groups.ts` | `include` | `B8` | — |
| `webui/src/lib/cli-app-events.ts` | `include` | `B9` | — |
| `webui/src/lib/clipboard.ts` | `include` | `B8` | — |
| `webui/src/lib/code-language.ts` | `include` | `B9` | — |
| `webui/src/lib/file-diff.ts` | `include` | `B9` | — |
| `webui/src/lib/format.ts` | `include` | `B9` | — |
| `webui/src/lib/http.ts` | `include` | `B8` | — |
| `webui/src/lib/imageEncode.ts` | `include` | `B9` | — |
| `webui/src/lib/local-preferences.ts` | `include` | `B8` | — |
| `webui/src/lib/mcp-preset-events.ts` | `include` | `B8` | — |
| `webui/src/lib/media.ts` | `include` | `B9` | — |
| `webui/src/lib/nanobot-client.ts` | `include` | `B9` | — |
| `webui/src/lib/network.ts` | `include` | `B8` | — |
| `webui/src/lib/provider-brand.ts` | `include` | `B8` | — |
| `webui/src/lib/remark-tex-math.ts` | `include` | `B9` | — |
| `webui/src/lib/runtime.ts` | `include` | `B8` | — |
| `webui/src/lib/session-drag.ts` | `include` | `B8` | — |
| `webui/src/lib/skill-events.ts` | `include` | `B8` | — |
| `webui/src/lib/slash-command.ts` | `include` | `B9` | — |
| `webui/src/lib/subagent-channel-display.ts` | `include` | `B9` | — |
| `webui/src/lib/temporary-chat.ts` | `include` | `B9` | — |
| `webui/src/lib/thread-display-compat.ts` | `include` | `B9` | — |
| `webui/src/lib/thread-event-projection.ts` | `include` | `B9` | — |
| `webui/src/lib/tool-traces.ts` | `include` | `B9` | — |
| `webui/src/lib/types.ts` | `include` | `B9` | — |
| `webui/src/lib/user-message-quote.ts` | `include` | `B9` | — |
| `webui/src/lib/utils.ts` | `include` | `B8` | — |
| `webui/src/lib/workspace.ts` | `include` | `B8` | — |
| `webui/src/main.tsx` | `include` | `B8` | — |
| `webui/src/providers/ClientProvider.tsx` | `include` | `B8` | — |
| `webui/src/types/react-syntax-highlighter-subpaths.d.ts` | `exclude` | — | 第三方兼容类型声明，无运行时逻辑 |
| `webui/src/workers/imageEncode.worker.ts` | `include` | `B9` | — |
