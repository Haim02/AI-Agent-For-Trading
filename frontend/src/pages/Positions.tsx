import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

const API =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000/api";

interface Position {
  _id: string;
  ticker: string;
  strategy: string;
  entry_date: string;
  entry_credit: number;
  entry_debit?: number;
  entry_spot?: number;
  dte: number;
  strikes: Record<string, number>;
  status: "open" | "closed";
  unrealized_pnl: number;
  pnl_pct: number;
  realized_pnl?: number;
  notes: string;
  profit_target: number;
  stop_loss: number;
}

interface NewPosition {
  ticker: string;
  strategy: string;
  entry_credit: number;
  dte: number;
  strikes: Record<string, number>;
  notes: string;
}

interface PositionsList {
  positions: Position[];
}

async function fetchPositions(status: "open" | "closed"): Promise<PositionsList> {
  const r = await fetch(`${API}/positions?status=${status}`);
  if (!r.ok) throw new Error("fetch failed");
  const raw = await r.json();
  const items: Position[] = Array.isArray(raw) ? raw : (raw.positions ?? []);
  // Backend returns the document with `id` (set by `_fix_id`); the UI uses `_id`.
  const positions = items.map((p) => ({
    ...p,
    _id: (p as Position & { id?: string })._id ?? (p as Position & { id?: string }).id ?? "",
  }));
  return { positions };
}

async function addPosition(data: NewPosition): Promise<unknown> {
  const r = await fetch(`${API}/positions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!r.ok) throw new Error("add failed");
  return r.json();
}

async function closePosition({ id, pnl }: { id: string; pnl: number }): Promise<unknown> {
  const r = await fetch(`${API}/positions/${id}/close`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pnl }),
  });
  if (!r.ok) throw new Error("close failed");
  return r.json();
}

const STRATEGY_COLORS: Record<string, string> = {
  iron_condor: "#8b5cf6",
  bull_put_spread: "#22c55e",
  bull_put: "#22c55e",
  bear_call_spread: "#ef4444",
  bear_call: "#ef4444",
  short_strangle: "#f59e0b",
  call_debit_spread: "#06b6d4",
  call_debit: "#06b6d4",
  put_debit_spread: "#f97316",
  put_debit: "#f97316",
  iron_butterfly: "#ec4899",
  calendar: "#64748b",
};

const STRATEGY_LABELS: Record<string, string> = {
  iron_condor: "Iron Condor",
  bull_put: "Bull Put Spread",
  bear_call: "Bear Call Spread",
  short_strangle: "Short Strangle",
  call_debit: "Call Debit",
  put_debit: "Put Debit",
  iron_butterfly: "Iron Butterfly",
  calendar: "Calendar Spread",
};

function PositionCard({
  pos,
  onClose,
}: {
  pos: Position;
  onClose: (id: string, pnl: number) => void;
}) {
  const [showClose, setShowClose] = useState(false);
  const [closePnl, setClosePnl] = useState(pos.unrealized_pnl?.toString() ?? "0");

  const pnl = pos.unrealized_pnl ?? 0;
  const pnlPct = pos.pnl_pct ?? 0;
  const isProfit = pnl >= 0;
  const color = STRATEGY_COLORS[pos.strategy] ?? "#6b7280";
  const label = STRATEGY_LABELS[pos.strategy] ?? pos.strategy;

  const entryDate = new Date(pos.entry_date);
  const elapsed = Math.floor((Date.now() - entryDate.getTime()) / 86_400_000);
  const dteLeft = Math.max(0, (pos.dte ?? 0) - elapsed);

  return (
    <div className="card overflow-hidden">
      <div className="flex items-center justify-between px-4 pt-4 pb-3">
        <div className="flex items-center gap-3">
          <div className="h-10 w-2 flex-none rounded-full" style={{ backgroundColor: color }} />
          <div>
            <div className="flex items-center gap-2">
              <span className="text-lg font-bold text-white">{pos.ticker}</span>
              <span
                className="rounded-full px-2 py-0.5 text-xs font-medium"
                style={{ backgroundColor: color + "22", color }}
              >
                {label}
              </span>
            </div>
            <div className="mt-0.5 text-xs text-slate-500">
              {new Date(pos.entry_date).toLocaleDateString("he-IL")}
              {" · "}
              <span className={dteLeft <= 7 ? "text-orange-400" : "text-slate-500"}>
                {dteLeft} DTE
              </span>
            </div>
          </div>
        </div>

        <div className="text-right">
          <div className={`font-mono text-xl font-bold ${isProfit ? "text-green-400" : "text-red-400"}`}>
            {isProfit ? "+" : ""}${pnl.toFixed(0)}
          </div>
          <div className={`font-mono text-sm ${isProfit ? "text-green-500" : "text-red-500"}`}>
            {isProfit ? "+" : ""}
            {pnlPct.toFixed(1)}%
          </div>
        </div>
      </div>

      <div className="px-4 pb-3">
        <div className="mb-1 flex justify-between text-xs text-slate-600">
          <span>0%</span>
          <span className="text-yellow-500">יעד 50%</span>
          <span className="text-red-500">Stop 200%</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-slate-800">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              pnlPct >= 50 ? "bg-green-500" : pnlPct < -100 ? "bg-red-500" : "bg-indigo-500"
            }`}
            style={{ width: `${Math.min(100, Math.abs(pnlPct) / 2)}%` }}
          />
        </div>
      </div>

      {pos.strikes && (
        <div className="flex flex-wrap gap-2 px-4 pb-3">
          {Object.entries(pos.strikes)
            .filter(([k]) => !["type", "width", "call_width", "put_width"].includes(k))
            .map(([k, v]) => (
              <div key={k} className="rounded-lg bg-slate-800 px-2 py-1 font-mono text-xs">
                <span className="text-slate-500">{k}: </span>
                <span className="font-bold text-white">${v}</span>
              </div>
            ))}
        </div>
      )}

      <div className="grid grid-cols-3 gap-2 px-4 pb-3 text-xs">
        <div className="rounded-lg bg-slate-800 p-2">
          <div className="mb-0.5 text-slate-500">Credit</div>
          <div className="font-mono font-bold text-white">${(pos.entry_credit ?? 0).toFixed(0)}</div>
        </div>
        <div className="rounded-lg bg-slate-800 p-2">
          <div className="mb-0.5 text-slate-500">יעד</div>
          <div className="font-mono font-bold text-green-400">
            ${(pos.profit_target ?? 0).toFixed(0)}
          </div>
        </div>
        <div className="rounded-lg bg-slate-800 p-2">
          <div className="mb-0.5 text-slate-500">Stop</div>
          <div className="font-mono font-bold text-red-400">
            ${(pos.stop_loss ?? 0).toFixed(0)}
          </div>
        </div>
      </div>

      {pos.notes && (
        <div className="px-4 pb-3">
          <div className="rounded-lg bg-slate-800 px-3 py-2 text-xs italic text-slate-400">
            {pos.notes}
          </div>
        </div>
      )}

      <div className="border-t border-slate-800 px-4 py-3">
        {!showClose ? (
          <button
            onClick={() => setShowClose(true)}
            className="w-full rounded-xl bg-slate-800 py-2.5 text-sm font-semibold text-slate-300 transition-colors hover:bg-slate-700 active:bg-slate-600"
          >
            סגור פוזיציה
          </button>
        ) : (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="flex-none text-xs text-slate-400">P&L סופי:</span>
              <input
                type="number"
                value={closePnl}
                onChange={(e) => setClosePnl(e.target.value)}
                className="flex-1 rounded-lg border border-slate-700 bg-slate-800 px-3 py-1.5 font-mono text-sm text-white outline-none focus:border-indigo-400"
              />
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => {
                  onClose(pos._id, parseFloat(closePnl) || 0);
                  setShowClose(false);
                }}
                className="flex-1 rounded-xl bg-indigo-600 py-2 text-sm font-semibold text-white"
              >
                אשר סגירה
              </button>
              <button
                onClick={() => setShowClose(false)}
                className="flex-1 rounded-xl bg-slate-800 py-2 text-sm text-slate-400"
              >
                ביטול
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const STRIKE_FIELDS: Record<string, string[]> = {
  iron_condor: ["short_call", "long_call", "short_put", "long_put"],
  bull_put: ["short_put", "long_put"],
  bear_call: ["short_call", "long_call"],
  short_strangle: ["short_call", "short_put"],
  call_debit: ["long_call", "short_call"],
  put_debit: ["long_put", "short_put"],
  iron_butterfly: ["short_call", "short_put", "long_call", "long_put"],
  calendar: ["short_strike", "long_strike"],
};

function AddPositionModal({
  onClose,
  onAdd,
}: {
  onClose: () => void;
  onAdd: (data: NewPosition) => void;
}) {
  const [form, setForm] = useState<NewPosition>({
    ticker: "SPY",
    strategy: "iron_condor",
    entry_credit: 0,
    dte: 45,
    strikes: {},
    notes: "",
  });

  const STRATEGIES = Object.entries(STRATEGY_LABELS) as [string, string][];
  const fields = STRIKE_FIELDS[form.strategy] ?? [];

  function updateStrike(key: string, val: string) {
    setForm((f) => ({
      ...f,
      strikes: { ...f.strikes, [key]: parseFloat(val) || 0 },
    }));
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end bg-black/70"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="max-h-[90vh] w-full overflow-y-auto rounded-t-3xl border-t border-slate-700 bg-slate-900 pb-8">
        <div className="flex justify-center pt-3 pb-4">
          <div className="h-1 w-12 rounded-full bg-slate-700" />
        </div>

        <div className="space-y-4 px-5">
          <h2 className="text-lg font-bold text-white">הוסף פוזיציה חדשה</h2>

          <div>
            <label className="mb-1.5 block text-xs text-slate-400">Ticker</label>
            <input
              value={form.ticker}
              onChange={(e) =>
                setForm((f) => ({ ...f, ticker: e.target.value.toUpperCase() }))
              }
              className="w-full rounded-xl border border-slate-700 bg-slate-800 px-4 py-3 font-mono text-white outline-none focus:border-indigo-400"
              placeholder="SPY"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-xs text-slate-400">אסטרטגיה</label>
            <div className="grid grid-cols-2 gap-2">
              {STRATEGIES.map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setForm((f) => ({ ...f, strategy: key }))}
                  className={`rounded-xl px-3 py-2.5 text-right text-sm font-medium transition-all ${
                    form.strategy === key ? "border text-white" : "bg-slate-800 text-slate-400"
                  }`}
                  style={
                    form.strategy === key
                      ? {
                          backgroundColor: (STRATEGY_COLORS[key] ?? "#6b7280") + "22",
                          borderColor: STRATEGY_COLORS[key] ?? "#6b7280",
                        }
                      : undefined
                  }
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {fields.length > 0 && (
            <div>
              <label className="mb-1.5 block text-xs text-slate-400">Strikes</label>
              <div className="grid grid-cols-2 gap-2">
                {fields.map((f) => (
                  <div key={f}>
                    <div className="mb-1 text-xs text-slate-500">{f}</div>
                    <input
                      type="number"
                      onChange={(e) => updateStrike(f, e.target.value)}
                      className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2.5 font-mono text-sm text-white outline-none focus:border-indigo-400"
                      placeholder="0"
                    />
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1.5 block text-xs text-slate-400">Credit ($)</label>
              <input
                type="number"
                value={form.entry_credit || ""}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    entry_credit: parseFloat(e.target.value) || 0,
                  }))
                }
                className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2.5 font-mono text-sm text-white outline-none focus:border-indigo-400"
                placeholder="150"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs text-slate-400">DTE</label>
              <input
                type="number"
                value={form.dte}
                onChange={(e) =>
                  setForm((f) => ({ ...f, dte: parseInt(e.target.value) || 45 }))
                }
                className="w-full rounded-xl border border-slate-700 bg-slate-800 px-3 py-2.5 font-mono text-sm text-white outline-none focus:border-indigo-400"
              />
            </div>
          </div>

          <div>
            <label className="mb-1.5 block text-xs text-slate-400">הערות (אופציונלי)</label>
            <textarea
              value={form.notes}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              rows={2}
              className="w-full resize-none rounded-xl border border-slate-700 bg-slate-800 px-4 py-3 text-sm text-white outline-none focus:border-indigo-400"
              placeholder="סיבת הכניסה, GEX Regime..."
            />
          </div>

          <button
            onClick={() => onAdd(form)}
            className="w-full rounded-2xl bg-indigo-600 py-4 text-base font-bold text-white transition-colors active:bg-blue-700"
          >
            הוסף פוזיציה
          </button>
        </div>
      </div>
    </div>
  );
}

function SummaryBar({ positions }: { positions: Position[] }) {
  const open = positions.filter((p) => p.status === "open");
  const totalPnl = open.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);
  const winners = open.filter((p) => (p.unrealized_pnl ?? 0) > 0).length;
  const winRate = open.length ? Math.round((winners / open.length) * 100) : 0;

  return (
    <div className="grid grid-cols-3 gap-3 px-4 pb-4">
      <div className="card p-3 text-center">
        <div className="text-2xl font-bold text-white">{open.length}</div>
        <div className="mt-0.5 text-xs text-slate-500">פוזיציות פתוחות</div>
      </div>
      <div className="card p-3 text-center">
        <div
          className={`font-mono text-2xl font-bold ${
            totalPnl >= 0 ? "text-green-400" : "text-red-400"
          }`}
        >
          {totalPnl >= 0 ? "+" : "-"}${Math.abs(totalPnl).toFixed(0)}
        </div>
        <div className="mt-0.5 text-xs text-slate-500">P&L כולל</div>
      </div>
      <div className="card p-3 text-center">
        <div
          className={`text-2xl font-bold ${
            winRate >= 60 ? "text-green-400" : "text-yellow-400"
          }`}
        >
          {winRate}%
        </div>
        <div className="mt-0.5 text-xs text-slate-500">Win Rate</div>
      </div>
    </div>
  );
}

export default function Positions() {
  const [showAdd, setShowAdd] = useState(false);
  const [tab, setTab] = useState<"open" | "closed">("open");
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["positions", tab],
    queryFn: () => fetchPositions(tab),
    refetchInterval: 30_000,
  });

  const addMutation = useMutation({
    mutationFn: addPosition,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["positions"] });
      setShowAdd(false);
    },
  });

  const closeMutation = useMutation({
    mutationFn: closePosition,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["positions"] }),
  });

  const positions: Position[] = data?.positions ?? [];

  return (
    <div className="min-h-screen bg-slate-950 pb-24 text-white">
      <div className="flex items-center justify-between px-4 pt-5 pb-4">
        <h1 className="text-2xl font-bold">פוזיציות</h1>
        <button
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white"
        >
          + הוסף
        </button>
      </div>

      {tab === "open" && positions.length > 0 && <SummaryBar positions={positions} />}

      <div className="mx-4 mb-4 flex gap-1 rounded-xl bg-slate-900 p-1">
        {(["open", "closed"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`flex-1 rounded-lg py-2 text-sm font-semibold transition-all ${
              tab === t ? "bg-slate-700 text-white" : "text-slate-500"
            }`}
          >
            {t === "open" ? "פתוחות" : "סגורות"}
          </button>
        ))}
      </div>

      <div className="space-y-4 px-4">
        {isLoading && <div className="py-12 text-center text-slate-500">טוען...</div>}

        {!isLoading && positions.length === 0 && (
          <div className="py-16 text-center">
            <div className="mb-4 text-5xl">📭</div>
            <div className="text-slate-400">
              {tab === "open" ? "אין פוזיציות פתוחות" : "אין פוזיציות סגורות"}
            </div>
            {tab === "open" && (
              <button
                onClick={() => setShowAdd(true)}
                className="mt-4 rounded-xl bg-indigo-600 px-6 py-3 text-sm font-semibold text-white"
              >
                הוסף פוזיציה ראשונה
              </button>
            )}
          </div>
        )}

        {positions.map((pos) => (
          <PositionCard
            key={pos._id}
            pos={pos}
            onClose={(id, pnl) => closeMutation.mutate({ id, pnl })}
          />
        ))}
      </div>

      {showAdd && (
        <AddPositionModal
          onClose={() => setShowAdd(false)}
          onAdd={(data) => addMutation.mutate(data)}
        />
      )}
    </div>
  );
}
