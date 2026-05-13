import { useQuery } from "@tanstack/react-query";
import { getJournal } from "../../api/client";

export default function AgentInsight() {
  const { data } = useQuery({
    queryKey: ["journal", "latest"],
    queryFn: () => getJournal(1),
  });

  const last = data?.[0];
  const watchlist = last?.next_day_watchlist ?? [];

  return (
    <div className="flex h-full flex-col rounded-xl border border-slate-800 bg-slate-900/70 p-5 shadow-lg">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">תובנת הסוכן האחרונה 🤖</h2>
        <span className="text-xs text-slate-400">
          {last?.date ?? "—"}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto whitespace-pre-wrap text-sm leading-relaxed text-slate-200">
        {last?.agent_summary?.trim() || "אין סיכום מהסוכן עדיין. הרצה ראשונה תיווצר אחרי סגירת השוק."}
      </div>

      {watchlist.length > 0 && (
        <div className="mt-4 border-t border-slate-800 pt-3">
          <div className="mb-2 text-xs text-slate-400">מעקב למחר:</div>
          <div className="flex flex-wrap gap-2">
            {watchlist.map((ticker) => (
              <span
                key={ticker}
                className="rounded-full bg-blue-500/20 px-3 py-1 text-xs font-medium text-blue-200"
              >
                {ticker}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
