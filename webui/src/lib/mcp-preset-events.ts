// MCP 预设事件携带已规整 payload，订阅者刷新呈现而不把事件本身当作持久化来源。
import type { McpPresetInfo, McpPresetsPayload } from "@/lib/types";

export const MCP_PRESETS_CHANGED_EVENT = "nanobot:mcp-presets-changed";

export function isMcpPresetsPayload(value: unknown): value is McpPresetsPayload {
  return !!value
    && typeof value === "object"
    && Array.isArray((value as { presets?: unknown }).presets);
}

export function installedMcpPresetsFromPayload(payload: McpPresetsPayload): McpPresetInfo[] {
  return payload.presets.filter(
    (preset) => preset.source !== "agent-plugin"
      && (preset.enabled ?? (preset.installed && preset.configured)),
  );
}

export function notifyMcpPresetsChanged(payload: McpPresetsPayload): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<McpPresetsPayload>(MCP_PRESETS_CHANGED_EVENT, {
    detail: payload,
  }));
}
