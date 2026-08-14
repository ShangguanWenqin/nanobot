"""Tool registry for dynamic tool management.
管理工具 → 暴露工具定义 → 准备工具调用 → 执行工具

AgentRunner
    │
    │ LLM 返回 tool_call
    ▼
ToolRegistry.execute(name, params)
    │
    ├── 1. 找 Tool
    │
    ├── 2. 兼容不同参数格式
    │
    ├── 3. 类型转换 cast_params()
    │
    ├── 4. 参数校验 validate_params()
    │
    ├── 5. Tool.execute(**params)
    │
    └── 6. 统一错误处理
    │
    ▼
ToolResult / 正常结果
    │
    ▼
AgentRunner
    │
    ▼
LLM 下一轮推理
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.context import ContextAware, current_request_context

if TYPE_CHECKING:
    from nanobot.runtime_context import RuntimeContextProvider

# 是否为工具错误结果
def is_tool_error_result(result: Any) -> bool:
    return isinstance(result, ToolResult) and result.is_error

# 当前 Agent 这一轮到底有哪些 Tool，以及如何调用它们
class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}  # tool.name -> tool;
        self._cached_definitions: list[dict[str, Any]] | None = None # 缓存OpenAI的function schema，在注册和注销工具时会清楚缓存

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool
        self._cached_definitions = None

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
        self._cached_definitions = None

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    # 获取所有按名称排好序的工具的运行时上下文provider
    def get_runtime_context_providers(self) -> list[RuntimeContextProvider]:
        """Return tool-owned providers in stable tool-name order."""
        providers: list[RuntimeContextProvider] = []
        for name in sorted(self._tools):
            provider = self._tools[name].runtime_context_provider()
            if provider is not None:
                providers.append(provider)
        return providers

    # 将工具名称转换为纯小写字母形式
    @staticmethod
    def _lookup_key(name: str) -> str:
        """Normalize names for suggestions only; never for execution."""
        return "".join(ch.lower() for ch in name if ch.isalnum())

    # 用普通的工具名称匹配正式的工具名称
    def _suggest_name(self, name: str) -> str | None:
        key = self._lookup_key(str(name or ""))
        if not key:
            return None
        matches = [
            registered
            for registered in self._tools
            if self._lookup_key(registered) == key
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    # 从OpenAI的function schema或flat schema获取工具名
    @staticmethod
    def _schema_name(schema: dict[str, Any]) -> str:
        """Extract a normalized tool name from either OpenAI or flat schemas."""
        fn = schema.get("function")
        if isinstance(fn, dict):
            name = cast(dict[str, Any], fn).get("name")
            if isinstance(name, str):
                return name
        name = schema.get("name")
        return name if isinstance(name, str) else ""

    # 获取所有注册工具的定义，是内置工具和mcp工具分别排序后相加，稳定排序是为了 Prompt Cache
    def get_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions with stable ordering for cache-friendly prompts.

        Built-in tools are sorted first as a stable prefix, then MCP tools are
        sorted and appended. The result is cached until the next
        register/unregister call.
        """
        if self._cached_definitions is None:
            definitions = [tool.to_schema() for tool in self._tools.values()]
            builtins: list[dict[str, Any]] = []
            mcp_tools: list[dict[str, Any]] = []
            for schema in definitions:
                name = self._schema_name(schema)
                if name.startswith("mcp_"):
                    mcp_tools.append(schema)
                else:
                    builtins.append(schema)

            builtins.sort(key=self._schema_name)
            mcp_tools.sort(key=self._schema_name)
            self._cached_definitions = builtins + mcp_tools

        return self._cached_definitions

    # 解析、转换、验证工具调用
    def prepare_call(
        self,
        name: str,
        params: Any,
    ) -> tuple[Tool | None, Any, str | None]:
        """Resolve, cast, and validate one tool call."""
        tool = self._tools.get(name)
        # 如果没找到工具，查找是否有近似的名称，返回tool result
        if not tool:
            suggestion = self._suggest_name(str(name))
            hint = f" Did you mean '{suggestion}'? Tool names must match exactly." if suggestion else ""
            return None, params, (
                ToolResult.error(
                    f"Error: Tool '{name}' not found.{hint} Available: {', '.join(self.tool_names)}"
                )
            )
        # Compatibility for external tools that still implement the legacy
        # setter protocol. Built-ins read the authoritative ContextVar
        # directly and never copy routing state.
        # 兼容旧版tool 的 Context Setter，新版本直接用current_request_context()
        if isinstance(tool, ContextAware) and (ctx := current_request_context()) is not None:
            tool.set_context(ctx)

        # 兼容多种参数
        params = self._coerce_params(tool, params)
        if not isinstance(params, dict):
            return tool, params, (
                ToolResult.error(
                    f"Error: Tool '{name}' parameters must be a JSON object, got "
                    f"{type(params).__name__}. Use named parameters like "
                    'tool_name(param1="value1", param2="value2") matching the tool schema.'
                )
            )

        # 参数类型纠偏
        cast_params = tool.cast_params(cast(dict[str, Any], params))
        # 验证参数是否正确
        errors = tool.validate_params(cast_params)
        if errors:
            return tool, cast_params, (
                ToolResult.error(f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors))
            )
        return tool, cast_params, None

    # 尝试将[、{开头的str按照json格式解析
    @classmethod
    def _coerce_argument_value(cls, value: Any) -> Any:
        if value is None:
            return {}
        if not isinstance(value, str):
            return value

        stripped = value.strip()
        if not stripped:
            return {}

        if not stripped.startswith(("{", "[")):
            return value

        try:
            parsed = json.loads(stripped)
        except Exception:
            return value

        return parsed

    # 多种参数兼容
    @classmethod
    def _coerce_params(cls, tool: Tool, params: Any) -> Any:
        params = cls._coerce_argument_value(params)
        return cls._unwrap_arguments_payload(tool, params)

    # 兼容一些外部tool call 格式
    @classmethod
    def _unwrap_arguments_payload(cls, tool: Tool, params: Any) -> Any:
        if not isinstance(params, dict):
            return params
        arguments_payload = cast(dict[str, Any], params)
        if set(arguments_payload) != {"arguments"}:
            return arguments_payload
        properties = (tool.parameters or {}).get("properties", {})
        if isinstance(properties, dict) and "arguments" in properties:
            return arguments_payload
        return cls._coerce_argument_value(arguments_payload.get("arguments"))

    # 通过工具名和参数来执行tool
    async def execute(self, name: str, params: Any) -> Any:
        """Execute a tool by name with given parameters."""
        # 错误提示，让llm换一种方式，工具换或者工具名称换或者工具参数换...
        hint = "\n\n[Analyze the error above and try a different approach.]"
        tool, params, error = self.prepare_call(name, params)
        if error:
            return ToolResult.error(str(error) + hint)

        try:
            assert tool is not None  # guarded by prepare_call()
            result = await tool.execute(**params)
            if is_tool_error_result(result):
                return ToolResult.error(str(result) + hint)
            return result
        except Exception as e:
            return ToolResult.error(f"Error executing {name}: {str(e)}" + hint)

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools



"""
                    ┌────────────────────┐
                    │     ToolLoader     │
                    └─────────┬──────────┘
                              │ register()
                              ▼
                    ┌────────────────────┐
                    │   ToolRegistry     │
                    │                    │
                    │  _tools            │
                    └─────────┬──────────┘
                              │
              ┌───────────────┴───────────────┐
              │                               │
              │ get_definitions()             │ execute()
              ▼                               ▼
       Tool.to_schema()                  prepare_call()
              │                               │
              ▼                               ├── Resolve
       LLM Tool Schema                       ├── Context
              │                               ├── Coerce
              ▼                               ├── Cast
             LLM                              ├── Validate
              │                               │
              │ Tool Call                     ▼
              └──────────────────────────► Tool.execute()
                                              │
                                              ▼
                                         ToolResult
                                              │
                                              ▼
                                          AgentRunner
                                              │
                                              ▼
                                             LLM
"""