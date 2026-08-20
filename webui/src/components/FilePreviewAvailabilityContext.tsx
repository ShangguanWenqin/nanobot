// 文件预览可用性由线程容器注入并异步查询，Markdown 子节点只消费该能力而不自行假定文件仍存在。
import { createContext, useContext, type ReactNode } from "react";

export type FilePreviewAvailabilityResolver = (path: string) => Promise<boolean>;

const FilePreviewAvailabilityContext = createContext<
  FilePreviewAvailabilityResolver | undefined
>(undefined);

export function FilePreviewAvailabilityProvider({
  children,
  resolve,
}: {
  children: ReactNode;
  resolve?: FilePreviewAvailabilityResolver;
}) {
  return (
    <FilePreviewAvailabilityContext.Provider value={resolve}>
      {children}
    </FilePreviewAvailabilityContext.Provider>
  );
}

export function useFilePreviewAvailabilityResolver() {
  return useContext(FilePreviewAvailabilityContext);
}
