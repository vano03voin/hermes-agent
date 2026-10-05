import { fetchJSON } from "./api";

export interface MemoryDocument {
  content: string;
  version: string;
}

export const memoryApi = {
  getMemoryDocument: (target: "memory" | "user") =>
    fetchJSON<MemoryDocument>(`/api/memory/files/${target}`),
  saveMemoryDocument: (target: "memory" | "user", content: string, version: string) =>
    fetchJSON<MemoryDocument>(`/api/memory/files/${target}`, {
      method: "PUT", body: JSON.stringify({ content, version }),
    }),
};
