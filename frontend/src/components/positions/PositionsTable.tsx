import { Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import type { Position } from "../../types";

type Filter = "all" | "open" | "closed" | "expired";

const FILTER_LABEL: Record<Filter, string> = {
  all: "הכל",
  open: "פתוח",
  closed: "סגור",
  expired: "פג תוקף",
};

const STATUS_LABEL: Record<Position["status"], string> = {
  open: "פתוח",
  closed: "סגור",
  expired: "פג תוקף",
};

function daysUntil(date?: string) {
  if (!date) return null;
  const target = new Date(date).getTime();
  if (Number.isNaN(target)) return null;
  return Math.ceil((target - Date.now()) / (1000 * 60 * 60 * 24));
}

function dteClass(dte: number | null) {
  if (dte === null) return "text-slate-300";
  if (dte < 7) return "text-rose-400";
  if (dte < 14) return "text-amber-400";
  return "text-slate-300";
}

function StatusBadge({ status }: { status: Position["status"] }) {
  const isOpen = status === "open";
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs ${
        isOpen
          ? "bg-emerald-500/20 text-emerald-300"
          : "bg-slate-600/40 text-slate-300"
      }`}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

interface Props {
  positions: Position[];
  onDelete: (id: string) => void;
}

export default function PositionsTable({ positions, onDelete }: Props) {
  const [filter, setFilter] = useState<Filter>("all");

  const rows = useMemo(() => {
    if (filter === "all") return positions;
    return positions.filter((p) => p.status === filter);
  }, [positions, filter]);

  const handleDelete = (p: Position) => {
    if (window.confirm(`למחוק את ${p.ticker}?`)) onDelete(p.id);
  };

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3 shadow-lg lg:p-4">
      {/* Filter chips – scrollable horizontally on mobile */}
      <div className="scrollbar-hide mb-3 flex gap-2 overflow-x-auto pb-2">
        {(Object.keys(FILTER_LABEL) as Filter[]).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setFilter(key)}
            className={`shrink-0 whitespace-nowrap rounded-lg px-4 py-2 text-xs font-medium transition-colors ${
              filter === key
                ? "bg-blue-600 text-white"
                : "bg-slate-800 text-slate-300 hover:bg-slate-700"
            }`}
          >
            {FILTER_LABEL[key]}
          </button>
        ))}
      </div>

      {/* Mobile: cards */}
      <div className="block space-y-3 lg:hidden">
        {rows.length === 0 ? (
          <div className="py-6 text-center text-sm text-slate-400">
            אין פוזיציות לתצוגה.
          </div>
        ) : (
          rows.map((p) => {
            const dte = daysUntil(p.expiration_date);
            return (
              <div
                key={p.id}
                className="space-y-2 rounded-xl bg-slate-800 p-4 shadow"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-lg font-bold">{p.ticker}</span>
                  <StatusBadge status={p.status} />
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-slate-400">{p.strategy}</span>
                  <span className="text-emerald-400">
                    ${p.premium_received ?? p.premium_paid ?? 0}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs text-slate-400">
                  <span>פקיעה: {p.expiration_date?.slice(0, 10) ?? "—"}</span>
                  <span className={dteClass(dte)}>
                    {dte === null ? "—" : `${dte} ימים`}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => handleDelete(p)}
                  className="mt-1 text-xs text-rose-400 hover:text-rose-300"
                >
                  מחק
                </button>
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
              <th className="px-3 py-2">סטטוס</th>
              <th className="px-3 py-2">פרמיה</th>
              <th className="px-3 py-2">מקס רווח</th>
              <th className="px-3 py-2">תאריך פקיעה</th>
              <th className="px-3 py-2">ימים לפקיעה</th>
              <th className="px-3 py-2">פעולות</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-6 text-center text-slate-400">
                  אין פוזיציות לתצוגה.
                </td>
              </tr>
            ) : (
              rows.map((p) => {
                const dte = daysUntil(p.expiration_date);
                return (
                  <tr
                    key={p.id}
                    className="border-t border-slate-800/70 hover:bg-slate-800/40"
                  >
                    <td className="px-3 py-2 font-mono font-semibold">{p.ticker}</td>
                    <td className="px-3 py-2 text-slate-300">{p.strategy}</td>
                    <td className="px-3 py-2">
                      <StatusBadge status={p.status} />
                    </td>
                    <td className="px-3 py-2 text-emerald-300">
                      ${p.premium_received ?? p.premium_paid ?? 0}
                    </td>
                    <td className="px-3 py-2 text-slate-300">
                      {p.max_profit !== undefined ? `$${p.max_profit}` : "—"}
                    </td>
                    <td className="px-3 py-2 text-slate-400">
                      {p.expiration_date?.slice(0, 10) ?? "—"}
                    </td>
                    <td className={`px-3 py-2 font-mono text-xs ${dteClass(dte)}`}>
                      {dte ?? "—"}
                    </td>
                    <td className="px-3 py-2">
                      <button
                        type="button"
                        onClick={() => handleDelete(p)}
                        className="rounded-md bg-rose-500/20 px-2 py-1 text-xs text-rose-300 hover:bg-rose-500/30"
                        aria-label="מחק פוזיציה"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
