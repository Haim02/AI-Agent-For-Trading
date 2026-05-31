import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

const API =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000/api";

const TICKERS = ["SPY", "QQQ", "NVDA", "TSLA", "AAPL", "META", "AMD", "PLTR"];

interface Enrichment {
  iv: number | null;
  delta: number | null;
  moneyness: string;
}

interface Signal {
  ts: string;
  expiry: string;
  strike: number;
  right: string;
  side: string;
  price: number;
  size: number;
  premium: number;
  dte: number;
  structure: string;
  aggressor: string;
  intent: string;
  score: number;
  conviction: string;
  tags: string[];
  enrichment: Enrichment;
}

interface Chain {
  call_wall?: number;
  put_wall?: number;
  gamma_flip?: number;
  max_pain?: number;
}

interface SignalsResponse {
  signals?: Signal[];
  chain?: Chain;
  error?: string;
}

async function fetchSignals(
  ticker: string,
  minScore: number,
  structure: string | null,
): Promise<SignalsResponse> {
  const params = new URLSearchParams({
    min_score: String(minScore),
    window_minutes: "240",
    limit: "30",
  });
  if (structure) params.append("structure", structure);
  const r = await fetch(`${API}/flashalpha/signals/${ticker}/raw?${params}`);
  if (!r.ok) throw new Error("failed");
  return r.json();
}

function SignalCard({ sig }: { sig: Signal }) {
  const isCall = sig.right === "C";
  const isBull = sig.intent === "bullish";
  const isBear = sig.intent === "bearish";

  const intentColor = isBull
    ? "text-green-400"
    : isBear
      ? "text-red-400"
      : "text-gray-400";

  const scoreColor =
    sig.score >= 85 ? "bg-purple-600" : sig.score >= 75 ? "bg-blue-600" : "bg-gray-600";

  const isSweep = sig.structure === "sweep";

  return (
    <div className="rounded-2xl border border-gray-800 bg-gray-900 p-4">
      <div className="mb-3 flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold">
            {isSweep ? "⚡ SWEEP" : "🏦 BLOCK"}
          </span>
          {sig.tags.includes("whale") && (
            <span title="Whale (>$1M)" className="text-lg">
              🐋
            </span>
          )}
          {sig.tags.includes("golden") && (
            <span title="Golden" className="text-lg">
              ⭐
            </span>
          )}
          {sig.tags.includes("0dte") && (
            <span title="0DTE" className="text-lg">
              ⏰
            </span>
          )}
          {sig.tags.includes("opening") && (
            <span
              title="Opening"
              className="rounded-full bg-blue-900 px-2 py-0.5 text-xs text-blue-300"
            >
              NEW
            </span>
          )}
        </div>
        <div className={`rounded-full px-3 py-1 text-sm font-bold text-white ${scoreColor}`}>
          {sig.score}/100
        </div>
      </div>

      <div className="mb-3 flex items-center gap-2">
        <span
          className={`text-base font-bold ${
            isCall ? "text-green-400" : "text-red-400"
          }`}
        >
          {isCall ? "CALL" : "PUT"} ${sig.strike}
        </span>
        <span className="text-sm text-gray-500">•</span>
        <span className="text-sm text-gray-400">
          {sig.expiry} ({sig.dte}d)
        </span>
      </div>

      <div className="mb-3 flex gap-3 text-xs">
        <span className={intentColor}>
          {isBull ? "🟢 שורי" : isBear ? "🔴 דובי" : "🟡 ניטרלי"}
        </span>
        <span className="text-gray-500">{sig.aggressor.replace("_", " ")}</span>
        {sig.enrichment?.moneyness && (
          <span className="text-gray-500">{sig.enrichment.moneyness}</span>
        )}
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="rounded-lg bg-gray-800 p-2">
          <div className="mb-0.5 text-gray-500">פרמיה</div>
          <div className="font-mono font-bold text-white">
            ${(sig.premium / 1000).toFixed(0)}K
          </div>
        </div>
        <div className="rounded-lg bg-gray-800 p-2">
          <div className="mb-0.5 text-gray-500">גודל</div>
          <div className="font-mono font-bold text-white">
            {sig.size.toLocaleString()}
          </div>
        </div>
        <div className="rounded-lg bg-gray-800 p-2">
          <div className="mb-0.5 text-gray-500">מחיר</div>
          <div className="font-mono font-bold text-white">
            ${sig.price.toFixed(2)}
          </div>
        </div>
      </div>

      {(sig.enrichment?.iv || sig.enrichment?.delta) && (
        <div className="mt-3 flex gap-4 text-xs text-gray-500">
          {sig.enrichment.iv != null && (
            <span>IV: {(sig.enrichment.iv * 100).toFixed(1)}%</span>
          )}
          {sig.enrichment.delta != null && (
            <span>Δ: {sig.enrichment.delta.toFixed(2)}</span>
          )}
        </div>
      )}

      <div className="mt-3 text-xs text-gray-600">
        {new Date(sig.ts).toLocaleTimeString("he-IL", {
          timeZone: "Asia/Jerusalem",
          hour: "2-digit",
          minute: "2-digit",
        })}
      </div>
    </div>
  );
}

export default function Signals() {
  const [ticker, setTicker] = useState("SPY");
  const [minScore, setMinScore] = useState(70);
  const [structure, setStructure] = useState<string | null>(null);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["signals", ticker, minScore, structure],
    queryFn: () => fetchSignals(ticker, minScore, structure),
    refetchInterval: 2 * 60 * 1000,
    retry: 1,
  });

  const signals: Signal[] = data?.signals ?? [];
  const chain: Chain = data?.chain ?? {};
  const errKey = data?.error;
  const isPlanReq = errKey === "plan_required";

  return (
    <div className="min-h-screen bg-gray-950 pb-24 text-white">
      <div className="px-4 pt-5 pb-3">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">🐋 Flow Signals</h1>
            <p className="mt-1 text-sm text-gray-500">עסקות חריגות מסומנות</p>
          </div>
          <button
            onClick={() => refetch()}
            className="rounded-xl bg-gray-800 p-2.5 text-lg"
            aria-label="רענן"
          >
            🔄
          </button>
        </div>
      </div>

      <div className="scrollbar-none flex gap-2 overflow-x-auto px-4 pb-3">
        {TICKERS.map((t) => (
          <button
            key={t}
            onClick={() => setTicker(t)}
            className={`flex-none rounded-full px-4 py-1.5 text-sm font-semibold ${
              ticker === t ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 px-4">
        <div>
          <label className="mb-1 block text-xs text-gray-500">ניקוד מינימלי</label>
          <select
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
            className="w-full rounded-xl border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white"
          >
            <option value={50}>50+ (הכל)</option>
            <option value={70}>70+ (חזק)</option>
            <option value={80}>80+ (חזק מאוד)</option>
            <option value={85}>85+ (לוויתנים)</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-gray-500">סוג עסקה</label>
          <select
            value={structure ?? ""}
            onChange={(e) => setStructure(e.target.value || null)}
            className="w-full rounded-xl border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-white"
          >
            <option value="">הכל</option>
            <option value="sweep">⚡ Sweep</option>
            <option value="block">🏦 Block</option>
          </select>
        </div>
      </div>

      {chain.call_wall != null && (
        <div className="mx-4 mb-4 grid grid-cols-4 gap-2 rounded-xl border border-gray-800 bg-gray-900 p-3 text-xs">
          <div>
            <div className="text-gray-500">CW</div>
            <div className="font-mono text-green-400">
              ${chain.call_wall.toFixed(0)}
            </div>
          </div>
          <div>
            <div className="text-gray-500">Flip</div>
            <div className="font-mono text-yellow-400">
              ${chain.gamma_flip?.toFixed(0) ?? "—"}
            </div>
          </div>
          <div>
            <div className="text-gray-500">PW</div>
            <div className="font-mono text-red-400">
              ${chain.put_wall?.toFixed(0) ?? "—"}
            </div>
          </div>
          <div>
            <div className="text-gray-500">MP</div>
            <div className="font-mono text-blue-400">
              ${chain.max_pain?.toFixed(0) ?? "—"}
            </div>
          </div>
        </div>
      )}

      {isLoading && (
        <div className="py-12 text-center text-gray-500">סורק...</div>
      )}

      {isPlanReq && (
        <div className="mx-4 rounded-2xl border border-yellow-700 bg-yellow-900/30 p-5 text-center">
          <div className="mb-2 text-3xl">⚠️</div>
          <div className="font-semibold text-yellow-300">דורש תוכנית Alpha</div>
          <div className="mt-2 text-sm text-yellow-400 opacity-80">
            Flow Signals דורש Alpha ב-FlashAlpha
          </div>
        </div>
      )}

      {!isLoading && !errKey && signals.length === 0 && (
        <div className="py-12 text-center text-gray-500">
          <div className="mb-2 text-3xl">🔍</div>
          <div>אין סיגנלים כרגע</div>
          <div className="mt-2 text-xs">נסה להוריד ניקוד מינימלי</div>
        </div>
      )}

      <div className="space-y-3 px-4">
        {signals.map((sig, i) => (
          <SignalCard key={`${sig.ts}-${i}`} sig={sig} />
        ))}
      </div>

      {!isLoading && signals.length > 0 && (
        <div className="py-4 text-center text-xs text-gray-600">
          {signals.length} סיגנלים · מתרענן כל 2 דק'
        </div>
      )}
    </div>
  );
}
