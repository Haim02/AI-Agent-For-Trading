import { useQuery } from "@tanstack/react-query";
import { Bot, Eye } from "lucide-react";
import { getJournal } from "../../api/client";

export default function AgentInsight() {
  const { data } = useQuery({
    queryKey: ["journal", "latest"],
    queryFn: () => getJournal(1),
  });

  const last = data?.[0];
  const watchlist = last?.next_day_watchlist ?? [];

  return (
    <div className="card relative flex h-full flex-col overflow-hidden p-5 animate-fade-up">
      {/* Brand glow accent */}
      <div className="pointer-events-none absolute -left-16 -top-16 h-40 w-40 rounded-full bg-indigo-500/15 blur-3xl" />

      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-cyan-400 shadow-md shadow-indigo-500/25">
            <Bot className="h-4 w-4 text-white" />
          </div>
          <h2 className="section-title">תובנת הסוכן האחרונה</h2>
        </div>
        <span className="num text-[11px] text-slate-500">{last?.date ?? "—"}</span>
      </div>

      <div className="flex-1 overflow-y-auto whitespace-pre-wrap text-sm leading-7 text-slate-200">
        {last?.agent_summary?.trim() || (
          <span className="text-slate-500">
            אין סיכום מהסוכן עדיין. הסיכום הראשון ייווצר אוטומטית אחרי סגירת השוק (23:30).
          </span>
        )}
      </div>

      {watchlist.length > 0 && (
        <div className="mt-4 border-t border-slate-800/60 pt-3">
          <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-400">
            <Eye className="h-3.5 w-3.5" />
            מעקב למחר
          </div>
          <div className="flex flex-wrap gap-1.5">
            {watchlist.map((ticker) => (
              <span
                key={ticker}
                className="badge num border border-indigo-400/20 bg-indigo-500/10 text-indigo-200"
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
