import axios from "axios";
import type {
  AgentStatus,
  GEXData,
  GEXKeyLevels,
  IVRankResult,
  IVScanResponse,
  JournalEntry,
  Position,
  Summary,
} from "../types";

const baseURL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000/api";

export const apiClient = axios.create({
  baseURL,
  headers: { "Content-Type": "application/json" },
});

export async function getHealth() {
  const { data } = await apiClient.get("/health");
  return data as { status: string; timestamp: string };
}

export async function getSummary() {
  const { data } = await apiClient.get<Summary>("/summary");
  return data;
}

export async function getPositions(status?: string) {
  const { data } = await apiClient.get<Position[]>("/positions", {
    params: status ? { status } : undefined,
  });
  return data;
}

export async function createPosition(payload: Record<string, unknown>) {
  const { data } = await apiClient.post<{ id: string }>("/positions", payload);
  return data;
}

export async function updatePosition(id: string, payload: Record<string, unknown>) {
  const { data } = await apiClient.patch(`/positions/${id}`, payload);
  return data;
}

export async function deletePosition(id: string) {
  const { data } = await apiClient.delete(`/positions/${id}`);
  return data;
}

export async function getJournal(limit?: number) {
  const { data } = await apiClient.get<JournalEntry[]>("/journal", {
    params: limit ? { limit } : undefined,
  });
  return data;
}

export async function getTrades(ticker?: string) {
  const { data } = await apiClient.get("/trades", {
    params: ticker ? { ticker } : undefined,
  });
  return data;
}

export async function scanMorning() {
  const { data } = await apiClient.get("/scan/morning");
  return data;
}

export async function getIVScan(minIVRank = 50) {
  const { data } = await apiClient.get<IVScanResponse>("/iv/scan", {
    params: { min_iv_rank: minIVRank },
  });
  return data;
}

export async function getIVGolden() {
  const { data } = await apiClient.get<{
    count: number;
    golden_opportunities: IVRankResult[];
    scan_time: string;
    market_summary: string;
  }>("/iv/golden");
  return data;
}

export async function getIVForTicker(ticker: string) {
  const { data } = await apiClient.get<IVRankResult>(`/iv/${ticker}`);
  return data;
}

export async function getGEX(ticker: string) {
  const { data } = await apiClient.get<{ gex: GEXData; key_levels: GEXKeyLevels }>(
    `/gex/${ticker}`,
  );
  return data;
}

export async function getGEXMenthorq() {
  const { data } = await apiClient.get<GEXData>("/gex/menthorq");
  return data;
}

export async function chatWithAgent(message: string, sessionId: string) {
  const { data } = await apiClient.post<{ response: string; session_id: string }>(
    "/agent/chat",
    { message, session_id: sessionId },
  );
  return data;
}

export async function runAgentTask(task: string) {
  const { data } = await apiClient.post<{ response: string }>("/agent/task", { task });
  return data;
}

export async function getAgentStatus() {
  const { data } = await apiClient.get<AgentStatus>("/agent/status");
  return data;
}

export async function getMemoryProfile() {
  const { data } = await apiClient.get("/memory/profile");
  return data;
}
