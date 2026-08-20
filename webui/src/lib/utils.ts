// 类名合并集中处理条件样式和 Tailwind 冲突，调用方无需理解覆盖顺序。
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
