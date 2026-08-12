import { describe, expect, it } from "vitest";

import { projectRagProgress } from "@/lib/rag-progress";
import type { RagProgressPayload } from "@/lib/types";

const base: RagProgressPayload = {
  kind: "rag_progress",
  operation_id: "a".repeat(32),
  operation: "query",
  phase: "querying",
  state: "running",
  fallback_text: "正在从 RAG 知识库中查询…",
};

describe("projectRagProgress", () => {
  it("updates one operation timeline and folds it after completion", () => {
    const started = projectRagProgress([], base, { turnId: "turn-1" }, 100);
    const completed = projectRagProgress(
      started,
      { ...base, phase: "completed", state: "completed", fallback_text: "查询完成。" },
      { turnId: "turn-1" },
      200,
    );

    expect(completed).toHaveLength(1);
    expect(completed[0].id).toBe(`rag-${"a".repeat(32)}`);
    expect(completed[0].traces).toEqual(["正在从 RAG 知识库中查询…", "查询完成。"]);
    expect(completed[0].isStreaming).toBe(false);
    expect(completed[0].ragState).toBe("completed");
  });

  it("deduplicates replayed phases after reconnect and preserves failure", () => {
    const failed = { ...base, phase: "failed", state: "failed", fallback_text: "查询失败。" } as const;
    const once = projectRagProgress([], failed, {}, 100);
    const replayed = projectRagProgress(once, failed, {}, 200);

    expect(replayed).toBe(once);
    expect(replayed[0].ragState).toBe("failed");
    expect(replayed[0].traces).toEqual(["查询失败。"]);
  });

  it("recreates the latest compact state after reconnect", () => {
    const restored = projectRagProgress(
      [],
      { ...base, phase: "completed", state: "completed", fallback_text: "RAG 查询完成。" },
      { turnId: "turn-reconnected" },
      300,
    );

    expect(restored).toHaveLength(1);
    expect(restored[0]).toMatchObject({
      id: `rag-${base.operation_id}`,
      content: "RAG 查询完成。",
      ragState: "completed",
      isStreaming: false,
      turnId: "turn-reconnected",
    });
  });
});
