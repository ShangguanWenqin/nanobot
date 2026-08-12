import type { RagProgressPayload, UIMessage } from "@/lib/types";

type RagTurnFields = Pick<UIMessage, "turnId" | "turnPhase" | "turnSeq">;

export function projectRagProgress(
  messages: UIMessage[],
  progress: RagProgressPayload,
  turn: RagTurnFields,
  now: number,
): UIMessage[] {
  const index = messages.findIndex((message) => message.ragOperationId === progress.operation_id);
  const phaseKey = `${progress.phase}:${progress.state}`;
  const previous = index >= 0 ? messages[index] : undefined;
  if (previous?.ragPhaseKeys?.includes(phaseKey)) return messages;

  const message: UIMessage = {
    ...(previous ?? {
      id: `rag-${progress.operation_id}`,
      role: "tool",
      kind: "trace",
      createdAt: now,
    }),
    content: progress.fallback_text,
    traces: [...(previous?.traces ?? []), progress.fallback_text],
    ragOperationId: progress.operation_id,
    ragState: progress.state,
    ragPhaseKeys: [...(previous?.ragPhaseKeys ?? []), phaseKey],
    isStreaming: progress.state === "queued" || progress.state === "running",
    ...turn,
  };
  if (index < 0) return [...messages, message];
  return [...messages.slice(0, index), message, ...messages.slice(index + 1)];
}
