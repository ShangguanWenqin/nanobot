// 推理预览只截取首段用于折叠标题，完整增量仍保存在所属消息的 reasoning 内容中。
export function compactReasoningPreview(value: string): string {
  return value
    .replace(/\[([^\]]+)]\([^)]+\)/g, "$1")
    .replace(/[*_#`~]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
