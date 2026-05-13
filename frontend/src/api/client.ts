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
  timeout: 30000,
  headers: { "Content-Type": "application/json" },
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (!error.response) {
      console.error("Network Error - Backend unreachable", error.message);
    }
    return Promise.reject(error);
  },
);

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

// ───────────────────────── analytics ─────────────────────────

export interface PerformanceMetrics {
  total_pnl: number;
  win_rate: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  max_drawdown: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  best_month?: { month: string; pnl: number } | null;
  worst_month?: { month: string; pnl: number } | null;
}

export interface EquityPoint {
  date: string;
  daily_pnl: number;
  cumulative_pnl: number;
}

export interface StrategyPerformance {
  strategy: string;
  trades: number;
  win_rate: number;
  avg_pnl: number;
  total_pnl: number;
}

export interface MonthlyPoint {
  month: string;
  pnl: number;
  trades: number;
  win_rate: number;
}

export interface HeatmapCell {
  year: number;
  week: number;
  weekday: number;
  pnl: number;
  trades: number;
}

export interface BestWorstTrade {
  id: string;
  ticker?: string;
  strategy?: string;
  entry_date?: string | null;
  exit_date?: string | null;
  pnl: number;
  dte?: number | null;
}

export async function getAnalyticsPerformance() {
  const { data } = await apiClient.get<PerformanceMetrics>("/analytics/performance");
  return data;
}

export async function getAnalyticsEquityCurve() {
  const { data } = await apiClient.get<EquityPoint[]>("/analytics/equity-curve");
  return data;
}

export async function getAnalyticsByStrategy() {
  const { data } = await apiClient.get<StrategyPerformance[]>("/analytics/by-strategy");
  return data;
}

export async function getAnalyticsMonthly() {
  const { data } = await apiClient.get<MonthlyPoint[]>("/analytics/monthly");
  return data;
}

export async function getAnalyticsHeatmap() {
  const { data } = await apiClient.get<HeatmapCell[]>("/analytics/heatmap");
  return data;
}

export async function getAnalyticsBestWorst() {
  const { data } = await apiClient.get<{
    best: BestWorstTrade[];
    worst: BestWorstTrade[];
  }>("/analytics/best-worst");
  return data;
}
