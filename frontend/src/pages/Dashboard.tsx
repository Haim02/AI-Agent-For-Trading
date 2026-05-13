import { useQuery } from "@tanstack/react-query";
import AgentInsight from "../components/dashboard/AgentInsight";
import PnLChart from "../components/dashboard/PnLChart";
import SummaryCards from "../components/dashboard/SummaryCards";
import { getPositions } from "../api/client";

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

      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3 shadow-lg lg:p-5">
        <h2 className="mb-3 text-sm font-semibold">5 פוזיציות פתוחות אחרונות</h2>

        {/* Mobile: card list */}
        <div className="space-y-2 lg:hidden">
          {recent.length === 0 ? (
            <div className="py-4 text-center text-sm text-slate-400">
              אין פוזיציות פתוחות.
            </div>
          ) : (
            recent.map((p) => {
              const pnl = Number(p.realized_pnl ?? 0);
              const premium = p.premium_received ?? p.premium_paid ?? 0;
              return (
                <div
                  key={p.id}
                  className="flex items-center justify-between rounded-lg bg-slate-800 p-3"
                >
                  <div className="min-w-0">
                    <span className="font-mono font-bold">{p.ticker}</span>
                    <span className="mr-2 text-xs text-slate-400">{p.strategy}</span>
                  </div>
                  <span
                    className={
                      pnl >= 0 ? "text-emerald-400" : "text-rose-400"
                    }
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
              <tr className="text-right text-xs uppercase text-slate-400">
                <th className="px-3 py-2">מניה</th>
                <th className="px-3 py-2">אסטרטגיה</th>
                <th className="px-3 py-2">פרמיה</th>
                <th className="px-3 py-2">פקיעה</th>
              </tr>
            </thead>
            <tbody>
              {recent.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-3 py-4 text-center text-slate-400">
                    אין פוזיציות פתוחות.
                  </td>
                </tr>
              ) : (
                recent.map((p) => (
                  <tr key={p.id} className="border-t border-slate-800/70">
                    <td className="px-3 py-2 font-mono font-semibold">{p.ticker}</td>
                    <td className="px-3 py-2 text-slate-300">{p.strategy}</td>
                    <td className="px-3 py-2 text-emerald-300">
                      ${p.premium_received ?? p.premium_paid ?? 0}
                    </td>
                    <td className="px-3 py-2 text-slate-400">
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
