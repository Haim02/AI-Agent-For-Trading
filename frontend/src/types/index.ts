export interface Position {
  id: string;
  ticker: string;
  strategy: string;
  status: "open" | "closed" | "expired";
  premium_received?: number;
  premium_paid?: number;
  max_profit?: number;
  max_loss?: number;
  entry_date: string;
  expiration_date?: string;
  realized_pnl?: number;
  vix_at_entry?: number;
  gex_regime_at_entry?: string;
  agent_reasoning?: string;
  notes?: string;
}

export interface JournalEntry {
  id: string;
  date: string;
  daily_pnl: number;
  weekly_pnl?: number;
  vix_open?: number;
  vix_close?: number;
  gex_regime?: string;
  agent_summary?: string;
  next_day_watchlist?: string[];
  lessons_learned?: string;
  notes?: string;
}

export interface IVRankResult {
  ticker: string;
  current_iv: number;
  iv_52w_high?: number;
  iv_52w_low?: number;
  iv_rank: number;
  iv_percentile?: number;
  signal: "SELL" | "BUY" | "NEUTRAL";
  signal_strength: string;
  recommended_strategies: string[];
  explanation: string;
  timestamp?: string;
}

export interface IVScanResponse {
  sell_opportunities: IVRankResult[];
  golden_opportunities: IVRankResult[];
  buy_opportunities: IVRankResult[];
  scan_time: string;
  market_summary: string;
}

export interface GEXData {
  ticker: string;
  spot_price: number;
  gex_total: number;
  gamma_flip_level: number;
  call_wall: number;
  put_wall: number;
  regime: "positive" | "negative";
  dealer_behavior: string;
  top_gex_strikes?: { strike: number; gex_value: number; type: string }[];
  timestamp?: string;
}

export interface GEXKeyLevels {
  ticker: string;
  spot_price: number;
  gamma_flip: number;
  call_wall: number;
  put_wall: number;
  call_resistance_levels: number[];
  put_support_levels: number[];
  regime: "positive" | "negative";
  zero_dte_safe: boolean;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
}

export interface Summary {
  open_positions: number;
  closed_positions: number;
  total_realized_pnl: number;
  last_journal?: JournalEntry;
}

export interface AgentStatus {
  status: string;
  last_scan: string | null;
  last_reflection: string | null;
  memory_stats: Record<string, number | string>;
}
