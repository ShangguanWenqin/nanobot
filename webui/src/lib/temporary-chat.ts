// 临时聊天的摘要仅用于当前连接内的本地导航；它不应在重连时作为持久 chat 自动 attach。
import type { ChatSummary } from "./types";

const WEBSOCKET_SESSION_KEY_PREFIX = "websocket:";

export function deriveTemporaryChatTitle(
  firstMessage: string | undefined,
  fallback: string,
): string {
  const oneLine = firstMessage?.replace(/\s+/g, " ").trim() ?? "";
  if (!oneLine) return fallback;
  return oneLine.length > 60 ? `${oneLine.slice(0, 57)}…` : oneLine;
}

export function createTemporaryChatSession(chatId: string): ChatSummary {
  const now = new Date().toISOString();
  return {
    key: `${WEBSOCKET_SESSION_KEY_PREFIX}${chatId}`,
    channel: "websocket",
    chatId,
    createdAt: now,
    updatedAt: now,
    preview: "",
  };
}
