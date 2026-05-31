import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

const API =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000/api";

interface Stock {
  ticker: string;
  company: string;
  sector: string;
  price: number;
  change_pct: number;
  volume: string;
  rel_volume: number;
  chart_url: string;
  news: string[];
}

interface ScanResponse {
  count: number;
  stocks: Stock[];
  timestamp?: string;
  screener_url?: string;
}

async function fetchLatestScan(): Promise<ScanResponse> {
  const r = await fetch(`${API}/scanner/latest`);
  if (!r.ok) throw new Error("failed");
  return r.json();
}

async function runNewScan(): Promise<ScanResponse> {
  const r = await fetch(`${API}/scanner/momentum?limit=20`);
  if (!r.ok) throw new Error("failed");
  return r.json();
}

function StockCard({ stock }: { stock: Stock }) {
  const [imgError, setImgError] = useState(false);
  const isUp = (stock.change_pct ?? 0) >= 0;

  return (
    <div className="overflow-hidden rounded-2xl border border-gray-800 bg-gray-900">
      <div className="flex items-start justify-between px-4 pt-4 pb-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xl font-bold text-white">{stock.ticker}</span>
            <span
              className={`rounded-lg px-2 py-0.5 font-mono text-sm font-bold ${
                isUp
                  ? "bg-green-900/40 text-green-400"
                  : "bg-red-900/40 text-red-400"
              }`}
            >
              {isUp ? "▲" : "▼"} {Math.abs(stock.change_pct ?? 0).toFixed(2)}%
            </span>
          </div>
          {stock.company && (
            <div className="mt-1 max-w-48 truncate text-xs text-gray-500">
              {stock.company}
            </div>
          )}
        </div>
        <div className="text-right">
          <div className="font-mono text-lg font-bold text-white">
            ${(stock.price ?? 0).toFixed(2)}
          </div>
          {stock.sector && (
            <div className="text-xs text-gray-600">{stock.sector}</div>
          )}
        </div>
      </div>

      <div className="bg-gray-950 px-2" dir="ltr">
        {!imgError ? (
          <img
            src={stock.chart_url}
            alt={`${stock.ticker} chart`}
            className="w-full rounded-lg"
            loading="lazy"
            onError={() => setImgError(true)}
          />
        ) : (
          <div className="flex h-40 items-center justify-center text-sm text-gray-600">
            גרף לא זמין
          </div>
        )}
      </div>

      {stock.news?.length > 0 && (
        <div className="px-4 py-3">
          <div className="mb-2 flex items-center gap-1 text-xs font-semibold uppercase tracking-wider text-gray-500">
            📰 למה זה עולה
          </div>
          <div className="space-y-1.5">
            {stock.news.map((n, i) => (
              <div key={i} className="flex gap-2 text-sm leading-snug text-gray-300">
                <span className="flex-none text-green-500">•</span>
                <span>{n}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="flex gap-2 border-t border-gray-800 px-4 py-2.5">
        <a
          href={`https://finviz.com/quote.ashx?t=${stock.ticker}`}
          target="_blank"
          rel="noopener noreferrer"
          className="flex-1 rounded-lg bg-gray-800 py-2 text-center text-xs font-medium text-gray-300"
        >
          FinViz
        </a>
        <a
          href={`https://unusualwhales.com/stocks/${stock.ticker}`}
          target="_blank"
          rel="noopener noreferrer"
          className="flex-1 rounded-lg bg-gray-800 py-2 text-center text-xs font-medium text-gray-300"
        >
          UW
        </a>
      </div>
    </div>
  );
}

export default function Scanner() {
  const [scanning, setScanning] = useState(false);

  const { data, isLoading, refetch } = useQuery({
    queryKey: ["scanner-latest"],
    queryFn: fetchLatestScan,
    refetchOnWindowFocus: false,
  });

  async function handleScan() {
    setScanning(true);
    try {
      await runNewScan();
      await refetch();
    } finally {
      setScanning(false);
    }
  }

  const stocks: Stock[] = data?.stocks ?? [];

  return (
    <div className="min-h-screen bg-gray-950 pb-24 text-white">
      <div className="px-4 pt-5 pb-3">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">סורק מניות</h1>
            <p className="mt-1 text-sm text-gray-500">
              מניות במגמת עלייה + סיבות
            </p>
          </div>
          <button
            onClick={handleScan}
            disabled={scanning}
            className="flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
          >
            {scanning ? "🔄 סורק..." : "🔍 סרוק"}
          </button>
        </div>

        {data?.timestamp && (
          <div className="mt-2 text-xs text-gray-600">
            סריקה אחרונה: {data.timestamp} · {data.count} מניות
          </div>
        )}
      </div>

      <div className="mx-4 mb-4 rounded-xl border border-gray-800 bg-gray-900 p-3">
        <div className="text-xs leading-relaxed text-gray-500">
          🎯 פילטרים: Mid-Cap+ · נפח 500K+ · אופציונבילי · נפח יחסי 1+ · 70%+ מעל שפל 52 שבועות
        </div>
      </div>

      {isLoading && (
        <div className="py-12 text-center text-gray-500">טוען...</div>
      )}

      {!isLoading && stocks.length === 0 && (
        <div className="py-16 text-center">
          <div className="mb-4 text-5xl">🔍</div>
          <div className="mb-4 text-gray-400">אין תוצאות סריקה עדיין</div>
          <button
            onClick={handleScan}
            disabled={scanning}
            className="rounded-xl bg-blue-600 px-6 py-3 text-sm font-semibold text-white"
          >
            {scanning ? "סורק..." : "התחל סריקה"}
          </button>
        </div>
      )}

      <div className="space-y-4 px-4">
        {stocks.map((stock) => (
          <StockCard key={stock.ticker} stock={stock} />
        ))}
      </div>
    </div>
  );
}
