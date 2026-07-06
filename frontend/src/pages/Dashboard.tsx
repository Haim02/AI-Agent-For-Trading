import { useQuery } from "@tanstack/react-query";
import { Briefcase } from "lucide-react";
import { Link } from "react-router-dom";
import AgentInsight from "../components/dashboard/AgentInsight";
import PnLChart from "../components/dashboard/PnLChart";
import SummaryCards from "../components/dashboard/SummaryCards";
import { getPositions } from "../api/client";

const STRATEGY_LABELS: Record<string, string> = {
  iron_condor: "Iron Condor",
  short_strangle: "Short Strangle",
  bull_put_spread: "Bull Put",
  bear_call_spread: "Bear Call",
  long_straddle: "Long Straddle",
  calendar_spread: "Calendar",
  other: "אחר",
};

function StrategyBadge({ strategy }: { strategy?: string }) {
  return (
    <span className="badge border border-slate-700/60 bg-slate-800/60 text-slate-300">
      {STRATEGY_LABELS[strategy ?? ""] ?? strategy ?? "—"}
    </span>
  );
}

export default function Dashboard() {
  const { data: positions } = useQuery({
    queryKey: ["positions", "open"],
    queryFn: () => getPositions("open"),
  });
  const recent = (positions ?? []).slice(0, 5);

  return (
    <div className="space-y-4 lg:space-y-6">
      <SummaryCards />

      {/* Mobile: stacked. Desktop: 5-col grid */}
      <div className="space-y-4 lg:grid lg:grid-cols-5 lg:gap-6 lg:space-y-0">
        <div className="lg:col-span-3">
          <PnLChart />
        </div>
        <div className="lg:col-span-2">
          <AgentInsight />
        </div>
      </div>

      <div className="card p-4 lg:p-5 animate-fade-up">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Briefcase className="h-4 w-4 text-indigo-300" />
            <h2 className="section-title">פוזיציות פתוחות אחרונות</h2>
          </div>
          <Link
            to="/positions"
            className="text-xs font-medium text-indigo-300 transition-colors hover:text-indigo-200"
          >
            לכל הפוזיציות ←
          </Link>
        </div>

        {/* Mobile: card list */}
        <div className="space-y-2 lg:hidden">
          {recent.length === 0 ? (
            <div className="py-6 text-center text-sm text-slate-500">
              אין פוזיציות פתוחות כרגע
            </div>
          ) : (
            recent.map((p) => {
              const pnl = Number(p.realized_pnl ?? 0);
              const premium = p.premium_received ?? p.premium_paid ?? 0;
              return (
                <div
                  key={p.id}
                  className="flex items-center justify-between rounded-xl border border-slate-800/60 bg-slate-900/50 p-3"
                >
                  <div className="flex min-w-0 items-center gap-2.5">
                    <span className="num font-bold text-slate-100">{p.ticker}</span>
                    <StrategyBadge strategy={p.strategy} />
                  </div>
                  <span
                    className={`num text-sm font-semibold ${
                      pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    ${p.realized_pnl ?? premium}
                  </span>
                </div>
              );
            })
          )}
        </div>

        {/* Desktop: table */}
        <div className="hidden overflow-x-auto lg:block">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-right text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                <th className="px-3 py-2">מניה</th>
                <th className="px-3 py-2">אסטרטגיה</th>
                <th className="px-3 py-2">פרמיה</th>
                <th className="px-3 py-2">פקיעה</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-3 py-6 text-center text-slate-500">
                    אין פוזיציות פתוחות כרגע
                  </td>
                </tr>
              ) : (
                recent.map((p) => (
                  <tr
                    key={p.id}
                    className="border-t border-slate-800/50 transition-colors hover:bg-white/[0.025]"
                  >
                    <td className="num px-3 py-2.5 font-bold text-slate-100">
                      {p.ticker}
                    </td>
                    <td className="px-3 py-2.5">
                      <StrategyBadge strategy={p.strategy} />
                    </td>
                    <td className="num px-3 py-2.5 text-emerald-300">
                      ${p.premium_received ?? p.premium_paid ?? 0}
                    </td>
                    <td className="num px-3 py-2.5 text-slate-400">
                      {p.expiration_date?.slice(0, 10) ?? "—"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
