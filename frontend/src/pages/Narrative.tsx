import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

const API =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000/api";

const TICKERS = ["SPY", "QQQ", "SPX", "IWM", "NVDA", "TSLA", "AAPL", "META"];

interface NarrativeResponse {
  symbol: string;
  spot_price?: number;
  sections_hebrew?: Record<string, string>;
  error?: string;
  message?: string;
}

async function fetchNarrative(ticker: string): Promise<NarrativeResponse> {
  const r = await fetch(`${API}/flashalpha/narrative/${ticker}`);
  if (!r.ok) throw new Error("failed");
  return r.json();
}

interface Section {
  key: string;
  label: string;
  icon: string;
  color: string;
}

const SECTIONS: Section[] = [
  { key: "regime", label: "משטר Gamma", icon: "📊", color: "bg-blue-900/30 border-blue-700" },
  { key: "gex_change", label: "שינוי יומי", icon: "📈", color: "bg-green-900/20 border-green-700" },
  { key: "key_levels", label: "רמות מפתח", icon: "🎯", color: "bg-purple-900/30 border-purple-700" },
  { key: "flow", label: "תזרים אופציות", icon: "🌊", color: "bg-cyan-900/30 border-cyan-700" },
  { key: "vanna", label: "Vanna", icon: "🌀", color: "bg-pink-900/20 border-pink-700" },
  { key: "charm", label: "Charm", icon: "⏳", color: "bg-orange-900/20 border-orange-700" },
  { key: "zero_dte", label: "0DTE", icon: "⚡", color: "bg-yellow-900/20 border-yellow-700" },
  { key: "outlook", label: "תחזית כללית", icon: "🔮", color: "bg-indigo-900/40 border-indigo-500" },
];

export default function Narrative() {
  const [ticker, setTicker] = useState("SPY");

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["narrative", ticker],
    queryFn: () => fetchNarrative(ticker),
    refetchInterval: 5 * 60 * 1000,
    retry: 1,
  });

  const sections = data?.sections_hebrew ?? {};
  const errKey = data?.error;
  const isPlanReq = errKey === "plan_required";

  return (
    <div className="min-h-screen bg-gray-950 pb-24 text-white">
      <div className="px-4 pt-5 pb-3">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">ניתוח AI מלא</h1>
            <p className="mt-1 text-sm text-gray-500">
              ניתוח טקסטואלי של מבנה השוק
            </p>
          </div>
          <button
            onClick={() => refetch()}
            className="rounded-xl bg-gray-800 p-2.5 text-lg"
            aria-label="רענן"
          >
            🔄
          </button>
        </div>

        {data?.spot_price ? (
          <div className="mt-2 font-mono text-xl font-bold text-blue-400">
            ${data.spot_price.toLocaleString()}
          </div>
        ) : null}
      </div>

      <div className="scrollbar-none flex gap-2 overflow-x-auto px-4 pb-4">
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

      {isLoading && (
        <div className="py-16 text-center">
          <div className="mb-3 text-4xl">🧠</div>
          <div className="text-gray-400">מייצר ניתוח עבור {ticker}...</div>
          <div className="mt-2 text-xs text-gray-600">~15 שניות</div>
        </div>
      )}

      {isPlanReq && (
        <div className="mx-4 rounded-2xl border border-yellow-700 bg-yellow-900/30 p-5 text-center">
          <div className="mb-2 text-3xl">⚠️</div>
          <div className="font-semibold text-yellow-300">דורש תוכנית Growth</div>
          <div className="mt-2 text-sm text-yellow-400 opacity-80">
            Narrative API זמין רק עם תוכנית Growth ומעלה ב-FlashAlpha
          </div>
        </div>
      )}

      {(isError || (errKey && !isPlanReq)) && (
        <div className="mx-4 rounded-2xl border border-red-700 bg-red-900/30 p-5 text-center text-red-300">
          ⚠️ {data?.message || "שגיאה בטעינה"}
        </div>
      )}

      {!isLoading && !errKey && (
        <div className="space-y-3 px-4">
          {SECTIONS.map((s) => (
            <div key={s.key} className={`rounded-2xl border p-4 ${s.color}`}>
              <div className="mb-2 flex items-center gap-2">
                <span className="text-xl">{s.icon}</span>
                <span className="text-sm font-bold text-white">{s.label}</span>
              </div>
              <p className="text-sm leading-relaxed text-gray-200">
                {sections[s.key] || "—"}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
