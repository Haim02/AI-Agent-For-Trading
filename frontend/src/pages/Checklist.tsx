import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

const API =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000/api";

interface Check {
  id: string;
  name: string;
  status: "pass" | "fail" | "warning" | "info";
  value: string;
  message: string;
  points: number;
}

interface ChecklistResponse {
  ticker: string;
  strategy: string;
  checks: Check[];
  score: number;
  verdict: "go" | "caution" | "no_go";
  verdict_text: string;
  verdict_color: string;
  timestamp: string;
}

interface ChecklistParams {
  ticker: string;
  strategy: string;
  short_call?: number;
  short_put?: number;
  credit?: number;
  dte: number;
}

async function runChecklist(params: ChecklistParams): Promise<ChecklistResponse> {
  const qs = new URLSearchParams();
  qs.set("strategy", params.strategy);
  qs.set("dte", String(params.dte));
  if (params.short_call !== undefined) qs.set("short_call", String(params.short_call));
  if (params.short_put !== undefined) qs.set("short_put", String(params.short_put));
  if (params.credit !== undefined) qs.set("credit", String(params.credit));
  const r = await fetch(`${API}/checklist/${params.ticker}?${qs}`);
  if (!r.ok) throw new Error("failed");
  return r.json();
}

const STATUS_STYLES: Record<
  Check["status"],
  { bg: string; border: string; icon: string; text: string }
> = {
  pass: { bg: "bg-green-900/30", border: "border-green-700", icon: "✅", text: "text-green-400" },
  fail: { bg: "bg-red-900/30", border: "border-red-700", icon: "❌", text: "text-red-400" },
  warning: { bg: "bg-yellow-900/20", border: "border-yellow-700", icon: "⚠️", text: "text-yellow-400" },
  info: { bg: "bg-gray-800", border: "border-gray-700", icon: "ℹ️", text: "text-gray-400" },
};

const STRATEGIES: [string, string][] = [
  ["iron_condor", "Iron Condor"],
  ["bull_put", "Bull Put"],
  ["bear_call", "Bear Call"],
  ["short_strangle", "Short Strangle"],
  ["call_debit", "Call Debit"],
  ["put_debit", "Put Debit"],
];

const VERDICT_STYLE: Record<ChecklistResponse["verdict"], string> = {
  go: "bg-green-900/40 border-green-500 text-green-300",
  caution: "bg-yellow-900/40 border-yellow-500 text-yellow-300",
  no_go: "bg-red-900/40 border-red-500 text-red-300",
};

export default function Checklist() {
  const [form, setForm] = useState({
    ticker: "SPY",
    strategy: "iron_condor",
    short_call: "",
    short_put: "",
    credit: "",
    dte: 45,
  });
  const [submitted, setSubmitted] = useState(false);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["checklist", form],
    queryFn: () =>
      runChecklist({
        ticker: form.ticker,
        strategy: form.strategy,
        short_call: form.short_call ? parseFloat(form.short_call) : undefined,
        short_put: form.short_put ? parseFloat(form.short_put) : undefined,
        credit: form.credit ? parseFloat(form.credit) : undefined,
        dte: form.dte,
      }),
    enabled: submitted,
    staleTime: 0,
  });

  const running = isLoading || isFetching;

  return (
    <div className="min-h-screen bg-gray-950 pb-24 text-white">
      <div className="px-4 pt-5 pb-4">
        <h1 className="text-2xl font-bold">צ'קליסט לפני עסקה</h1>
        <p className="mt-1 text-sm text-gray-500">בדוק כל פרמטר לפני כניסה</p>
      </div>

      <div className="space-y-4 px-4">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">Ticker</label>
            <input
              value={form.ticker}
              onChange={(e) =>
                setForm((f) => ({ ...f, ticker: e.target.value.toUpperCase() }))
              }
              className="w-full rounded-xl border border-gray-700 bg-gray-900 px-4 py-3 font-mono text-white outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">DTE</label>
            <input
              type="number"
              value={form.dte}
              onChange={(e) =>
                setForm((f) => ({ ...f, dte: parseInt(e.target.value) || 0 }))
              }
              className="w-full rounded-xl border border-gray-700 bg-gray-900 px-4 py-3 font-mono text-white outline-none focus:border-blue-500"
            />
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2">
          {STRATEGIES.map(([k, l]) => (
            <button
              key={k}
              onClick={() => setForm((f) => ({ ...f, strategy: k }))}
              className={`rounded-xl py-2 text-xs font-medium transition-all ${
                form.strategy === k ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400"
              }`}
            >
              {l}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">Short Call Strike</label>
            <input
              type="number"
              value={form.short_call}
              onChange={(e) => setForm((f) => ({ ...f, short_call: e.target.value }))}
              placeholder="אופציונלי"
              className="w-full rounded-xl border border-gray-700 bg-gray-900 px-4 py-3 font-mono text-sm text-white outline-none focus:border-green-500"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs text-gray-400">Short Put Strike</label>
            <input
              type="number"
              value={form.short_put}
              onChange={(e) => setForm((f) => ({ ...f, short_put: e.target.value }))}
              placeholder="אופציונלי"
              className="w-full rounded-xl border border-gray-700 bg-gray-900 px-4 py-3 font-mono text-sm text-white outline-none focus:border-red-500"
            />
          </div>
        </div>

        <button
          onClick={() => {
            if (submitted) refetch();
            else setSubmitted(true);
          }}
          className="w-full rounded-2xl bg-blue-600 py-4 text-base font-bold text-white active:bg-blue-700"
        >
          {running ? "בודק..." : "🔍 הרץ צ'קליסט"}
        </button>
      </div>

      {data && !running && (
        <div className="mt-6 space-y-3 px-4">
          <div
            className={`rounded-2xl border-2 p-5 text-center ${
              VERDICT_STYLE[data.verdict] ?? VERDICT_STYLE.caution
            }`}
          >
            <div className="mb-1 text-3xl">{data.score}</div>
            <div className="mb-2 text-xs opacity-70">ניקוד</div>
            <div className="text-xl font-bold">{data.verdict_text}</div>
          </div>

          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
            <div className="mb-2 flex justify-between text-xs text-gray-500">
              <span>0</span>
              <span>ציון: {data.score}/100</span>
              <span>100</span>
            </div>
            <div className="h-3 overflow-hidden rounded-full bg-gray-800">
              <div
                className={`h-full rounded-full transition-all duration-700 ${
                  data.score >= 80
                    ? "bg-green-500"
                    : data.score >= 60
                      ? "bg-yellow-500"
                      : "bg-red-500"
                }`}
                style={{ width: `${data.score}%` }}
              />
            </div>
          </div>

          <div className="space-y-2">
            {data.checks?.map((c) => {
              const s = STATUS_STYLES[c.status] ?? STATUS_STYLES.info;
              return (
                <div key={c.id} className={`rounded-xl border p-4 ${s.bg} ${s.border}`}>
                  <div className="mb-1 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span>{s.icon}</span>
                      <span className={`text-sm font-semibold ${s.text}`}>{c.name}</span>
                    </div>
                    <span className={`font-mono text-sm font-bold ${s.text}`}>{c.value}</span>
                  </div>
                  <p className="mr-6 text-xs text-gray-400">{c.message}</p>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
