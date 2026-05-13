import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  getAnalyticsBestWorst,
  getAnalyticsByStrategy,
  getAnalyticsEquityCurve,
  getAnalyticsHeatmap,
  getAnalyticsMonthly,
  getAnalyticsPerformance,
  getPositions,
} from "../api/client";
import type {
  BestWorstTrade,
  HeatmapCell,
  PerformanceMetrics,
  StrategyPerformance,
} from "../api/client";
import type { Position } from "../types";

const EMPTY = "אין מספיק נתונים עדיין";
const MONTHLY_TARGET = 4000; // $1k weekly ≈ $4k month

const WEEKDAY_LABELS = ["א'", "ב'", "ג'", "ד'", "ה'", "ו'", "ש'"];

function pnlColor(pnl: number): string {
  if (pnl > 0) return "text-emerald-300";
  if (pnl < 0) return "text-rose-300";
  return "text-slate-300";
}

function fmt(value: number | null | undefined, prefix = "$"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${prefix}${Number(value).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

// ────────────────────────────────────────────────────────────
// Section 1 — Performance Overview
// ────────────────────────────────────────────────────────────

function PerformanceOverview({ data }: { data?: PerformanceMetrics }) {
  if (!data) return <Card>{EMPTY}</Card>;
  const items = [
    { label: "P&L כולל", value: fmt(data.total_pnl), accent: pnlColor(data.total_pnl) },
    { label: "Win Rate", value: `${data.win_rate.toFixed(1)}%` },
    { label: "Avg Win", value: fmt(data.avg_win), accent: "text-emerald-300" },
    { label: "Avg Loss", value: fmt(data.avg_loss), accent: "text-rose-300" },
    { label: "Profit Factor", value: data.profit_factor.toFixed(2) },
    { label: "Max Drawdown", value: fmt(data.max_drawdown), accent: "text-rose-300" },
    {
      label: "חודש הכי טוב",
      value: data.best_month
        ? `${data.best_month.month} (${fmt(data.best_month.pnl)})`
        : "—",
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-4">
      {items.map((item) => (
        <div
          key={item.label}
          className="rounded-xl border border-slate-700 bg-slate-800 p-3 shadow"
        >
          <div className="text-[11px] text-slate-400">{item.label}</div>
          <div className={`mt-1 text-lg font-semibold ${item.accent ?? "text-slate-100"}`}>
            {item.value}
          </div>
        </div>
      ))}
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// Section 2 — Equity Curve
// ────────────────────────────────────────────────────────────

function EquityCurve() {
  const { data, isLoading } = useQuery({
    queryKey: ["analytics", "equity"],
    queryFn: getAnalyticsEquityCurve,
  });

  if (isLoading) return <Card>טוען…</Card>;
  if (!data || data.length === 0) return <Card>{EMPTY}</Card>;

  const finalValue = data[data.length - 1].cumulative_pnl;
  const fillColor = finalValue >= 0 ? "#34d399" : "#f87171";

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-3">
      <h3 className="mb-2 text-sm font-semibold">עקומת הון מצטברת</h3>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 10, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={fillColor} stopOpacity={0.4} />
                <stop offset="100%" stopColor={fillColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
            <XAxis dataKey="date" stroke="#64748b" fontSize={10} hide />
            <YAxis stroke="#64748b" fontSize={10} width={48} />
            <Tooltip
              contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", color: "#e2e8f0", fontSize: 12 }}
              formatter={(v: any) => [`$${Number(v).toFixed(0)}`, "מצטבר"]}
              labelFormatter={(label) => `תאריך: ${label}`}
            />
            <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 4" />
            <Area
              type="monotone"
              dataKey="cumulative_pnl"
              stroke={fillColor}
              strokeWidth={2}
              fill="url(#eqFill)"
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// Section 3 — Strategy Performance Table
// ────────────────────────────────────────────────────────────

function StrategyTable() {
  const { data, isLoading } = useQuery({
    queryKey: ["analytics", "by-strategy"],
    queryFn: getAnalyticsByStrategy,
  });

  if (isLoading) return <Card>טוען…</Card>;
  if (!data || data.length === 0) return <Card>{EMPTY}</Card>;

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-3">
      <h3 className="mb-2 text-sm font-semibold">ביצועים לפי אסטרטגיה</h3>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-right text-xs uppercase text-slate-400">
              <th className="px-3 py-2">אסטרטגיה</th>
              <th className="px-3 py-2">עסקאות</th>
              <th className="px-3 py-2">Win Rate</th>
              <th className="px-3 py-2">Avg P&L</th>
              <th className="px-3 py-2">סך P&L</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row: StrategyPerformance) => (
              <tr key={row.strategy} className="border-t border-slate-700/60">
                <td className="px-3 py-2 font-medium">{row.strategy}</td>
                <td className="px-3 py-2">{row.trades}</td>
                <td className="px-3 py-2">{row.win_rate.toFixed(1)}%</td>
                <td className={`px-3 py-2 ${pnlColor(row.avg_pnl)}`}>
                  {fmt(row.avg_pnl)}
                </td>
                <td className={`px-3 py-2 ${pnlColor(row.total_pnl)}`}>
                  {fmt(row.total_pnl)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// Section 4 — Weekly Heatmap
// ────────────────────────────────────────────────────────────

function intensity(pnl: number, max: number): string {
  if (pnl === 0) return "bg-slate-700/40";
  const ratio = Math.min(1, Math.abs(pnl) / Math.max(max, 1));
  if (pnl > 0) {
    if (ratio > 0.66) return "bg-emerald-500/80";
    if (ratio > 0.33) return "bg-emerald-500/50";
    return "bg-emerald-500/25";
  }
  if (ratio > 0.66) return "bg-rose-500/80";
  if (ratio > 0.33) return "bg-rose-500/50";
  return "bg-rose-500/25";
}

function Heatmap() {
  const { data, isLoading } = useQuery({
    queryKey: ["analytics", "heatmap"],
    queryFn: getAnalyticsHeatmap,
  });

  if (isLoading) return <Card>טוען…</Card>;
  if (!data || data.length === 0) return <Card>{EMPTY}</Card>;

  // Take the last 4 weeks present in the data
  const weekKeys = Array.from(new Set(data.map((c) => `${c.year}-${c.week}`)));
  const lastWeeks = weekKeys.slice(-4);
  const grid: Record<string, Record<number, HeatmapCell>> = {};
  for (const wk of lastWeeks) grid[wk] = {};
  for (const cell of data) {
    const key = `${cell.year}-${cell.week}`;
    if (!grid[key]) continue;
    grid[key][cell.weekday] = cell;
  }
  const max = Math.max(...data.map((c) => Math.abs(c.pnl)), 1);

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-3">
      <h3 className="mb-3 text-sm font-semibold">חום שבועי (4 שבועות אחרונים)</h3>
      <div className="grid grid-cols-7 gap-1 text-center text-[11px] text-slate-400">
        {WEEKDAY_LABELS.map((label) => (
          <div key={label}>{label}</div>
        ))}
      </div>
      <div className="mt-1 grid grid-cols-7 gap-1">
        {lastWeeks.map((wk) =>
          Array.from({ length: 7 }, (_, weekday) => {
            const cell = grid[wk]?.[weekday];
            const pnl = cell?.pnl ?? 0;
            return (
              <div
                key={`${wk}-${weekday}`}
                className={`flex h-12 flex-col items-center justify-center rounded text-[11px] ${
                  cell ? intensity(pnl, max) : "bg-slate-700/20"
                }`}
                title={cell ? `${pnl.toFixed(0)}$ · ${cell.trades} עסקאות` : ""}
              >
                {cell ? <span className="font-mono">{`$${pnl.toFixed(0)}`}</span> : <span>—</span>}
              </div>
            );
          }),
        )}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// Section 5 — Monthly Bars
// ────────────────────────────────────────────────────────────

function MonthlyBars() {
  const { data, isLoading } = useQuery({
    queryKey: ["analytics", "monthly"],
    queryFn: getAnalyticsMonthly,
  });

  if (isLoading) return <Card>טוען…</Card>;
  if (!data || data.length === 0) return <Card>{EMPTY}</Card>;

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-3">
      <h3 className="mb-2 text-sm font-semibold">P&L חודשי</h3>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
            <XAxis dataKey="month" stroke="#64748b" fontSize={10} />
            <YAxis stroke="#64748b" fontSize={10} width={48} />
            <Tooltip
              contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", color: "#e2e8f0", fontSize: 12 }}
              formatter={(v: any) => [`$${Number(v).toFixed(0)}`, "P&L"]}
            />
            <ReferenceLine
              y={MONTHLY_TARGET}
              stroke="#60a5fa"
              strokeDasharray="4 4"
              label={{ value: "יעד חודשי", fill: "#60a5fa", fontSize: 10 }}
            />
            <Bar dataKey="pnl">
              {data.map((row) => (
                <Cell key={row.month} fill={row.pnl >= 0 ? "#34d399" : "#f87171"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// Section 6 — Trade Duration Scatter
// ────────────────────────────────────────────────────────────

function DurationScatter() {
  const { data: positions } = useQuery({
    queryKey: ["positions", "closed-scatter"],
    queryFn: () => getPositions("closed"),
  });

  const points = useMemo(() => {
    if (!positions) return [];
    return positions
      .map((p: Position) => {
        const entry = p.entry_date ? new Date(p.entry_date) : null;
        const exit = (p as any).closed_at
          ? new Date((p as any).closed_at)
          : (p as any).exit_date
            ? new Date((p as any).exit_date)
            : null;
        if (!entry || !exit) return null;
        const days = Math.max(0, Math.round((exit.getTime() - entry.getTime()) / (1000 * 60 * 60 * 24)));
        const pnl = Number(p.realized_pnl ?? 0);
        return { days, pnl, ticker: p.ticker };
      })
      .filter(Boolean) as Array<{ days: number; pnl: number; ticker: string }>;
  }, [positions]);

  if (points.length === 0) return <Card>{EMPTY}</Card>;

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-3">
      <h3 className="mb-2 text-sm font-semibold">משך עסקה מול P&L</h3>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart>
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
            <XAxis
              type="number"
              dataKey="days"
              stroke="#64748b"
              fontSize={10}
              name="ימים"
              label={{ value: "ימים בפוזיציה", fill: "#94a3b8", fontSize: 11, dy: 12 }}
            />
            <YAxis type="number" dataKey="pnl" stroke="#64748b" fontSize={10} />
            <Tooltip
              contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", color: "#e2e8f0", fontSize: 12 }}
              cursor={{ strokeDasharray: "3 3" }}
              formatter={(v: any, _name: any) => [`$${Number(v).toFixed(0)}`, "P&L"]}
            />
            <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 4" />
            <Scatter data={points} fill="#60a5fa">
              {points.map((p, i) => (
                <Cell key={i} fill={p.pnl >= 0 ? "#34d399" : "#f87171"} />
              ))}
            </Scatter>
          </ScatterChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// Section 7 — Best / Worst Trades
// ────────────────────────────────────────────────────────────

function BestWorstTrades() {
  const { data, isLoading } = useQuery({
    queryKey: ["analytics", "best-worst"],
    queryFn: getAnalyticsBestWorst,
  });
  if (isLoading) return <Card>טוען…</Card>;
  if (!data || (data.best.length === 0 && data.worst.length === 0)) return <Card>{EMPTY}</Card>;

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <TradeList title="🏆 5 הטובות ביותר" rows={data.best} positive />
      <TradeList title="💔 5 הגרועות ביותר" rows={data.worst} />
    </div>
  );
}

function TradeList({
  title,
  rows,
  positive = false,
}: {
  title: string;
  rows: BestWorstTrade[];
  positive?: boolean;
}) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-3">
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead>
            <tr className="text-right text-[11px] uppercase text-slate-400">
              <th className="px-2 py-1">מניה</th>
              <th className="px-2 py-1">אסטרטגיה</th>
              <th className="px-2 py-1">כניסה</th>
              <th className="px-2 py-1">יציאה</th>
              <th className="px-2 py-1">DTE</th>
              <th className="px-2 py-1">P&L</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6} className="px-2 py-3 text-center text-slate-400">
                  {EMPTY}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr key={row.id} className="border-t border-slate-700/60">
                  <td className="px-2 py-1 font-mono">{row.ticker ?? "—"}</td>
                  <td className="px-2 py-1">{row.strategy ?? "—"}</td>
                  <td className="px-2 py-1 text-slate-400">{row.entry_date ?? "—"}</td>
                  <td className="px-2 py-1 text-slate-400">{row.exit_date ?? "—"}</td>
                  <td className="px-2 py-1">{row.dte ?? "—"}</td>
                  <td className={`px-2 py-1 ${positive ? "text-emerald-300" : pnlColor(row.pnl)}`}>
                    {fmt(row.pnl)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────
// Wrapper / page
// ────────────────────────────────────────────────────────────

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-700 bg-slate-800 p-6 text-center text-sm text-slate-400">
      {children}
    </div>
  );
}

export default function Analytics() {
  const performanceQuery = useQuery({
    queryKey: ["analytics", "performance"],
    queryFn: getAnalyticsPerformance,
  });

  return (
    <div className="space-y-4 lg:space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold lg:text-xl">דשבורד אנליטיקס 📊</h1>
      </div>

      <PerformanceOverview data={performanceQuery.data} />
      <EquityCurve />
      <StrategyTable />
      <Heatmap />
      <MonthlyBars />
      <DurationScatter />
      <BestWorstTrades />
    </div>
  );
}
