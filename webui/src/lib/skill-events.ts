// 技能变更事件让异步设置操作通知其它消费者；payload 是刷新提示而非独立缓存。
import type { SkillsPayload } from "@/lib/types";

export const SKILLS_CHANGED_EVENT = "nanobot:skills-changed";

export function isSkillsPayload(value: unknown): value is SkillsPayload {
  return (
    !!value
    && typeof value === "object"
    && Array.isArray((value as { skills?: unknown }).skills)
  );
}

export function notifySkillsChanged(payload: SkillsPayload): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<SkillsPayload>(SKILLS_CHANGED_EVENT, {
    detail: payload,
  }));
}
