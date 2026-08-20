// 文件编辑展示模式是用户本地偏好，只决定活动卡片呈现方式，不影响工具执行或差异数据。
import { useEffect, useState } from "react";

import {
  LOCAL_PREFS_CHANGED_EVENT,
  normalizeFileEditDisplayMode,
  readLocalPreferences,
  type FileEditDisplayMode,
  type LocalPreferences,
} from "@/lib/local-preferences";

export function useFileEditDisplayMode(): FileEditDisplayMode {
  const [mode, setMode] = useState<FileEditDisplayMode>(() =>
    readLocalPreferences().fileEditDisplayMode,
  );

  useEffect(() => {
    const refresh = () => setMode(readLocalPreferences().fileEditDisplayMode);
    const refreshFromLocalPreferenceEvent = (event: Event) => {
      const detail = (event as CustomEvent<Partial<LocalPreferences> | undefined>).detail;
      setMode(
        detail
          ? normalizeFileEditDisplayMode(detail.fileEditDisplayMode)
          : readLocalPreferences().fileEditDisplayMode,
      );
    };
    window.addEventListener("storage", refresh);
    window.addEventListener("focus", refresh);
    window.addEventListener(LOCAL_PREFS_CHANGED_EVENT, refreshFromLocalPreferenceEvent);
    return () => {
      window.removeEventListener("storage", refresh);
      window.removeEventListener("focus", refresh);
      window.removeEventListener(LOCAL_PREFS_CHANGED_EVENT, refreshFromLocalPreferenceEvent);
    };
  }, []);

  return mode;
}
