import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { getJournal } from "../api/client";
import JournalCalendar from "../components/journal/JournalCalendar";
import type { JournalEntry } from "../types";

export default function Journal() {
  const { data } = useQuery({
    queryKey: ["journal", "all"],
    queryFn: () => getJournal(60),
  });

  const entries: JournalEntry[] = useMemo(() => data ?? [], [data]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedEntry, setSelectedEntry] = useState<JournalEntry | null>(null);

  const monthlyTotal = useMemo(() => {
    const now = new Date();
    const ym = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
    return entries
      .filter((e) => e.date?.startsWith(ym))
      .reduce((sum, e) => sum + Number(e.daily_pnl ?? 0), 0);
  }, [entries]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">יומן מסחר</h1>
        <div className="rounded-md bg-slate-900/70 px-4 py-2 text-sm">
          סיכום חודשי:{" "}
          <span className={monthlyTotal >= 0 ? "text-emerald-300" : "text-rose-300"}>
            ${monthlyTotal.toFixed(2)}
          </span>
        </div>
      </div>

      <JournalCalendar
        entries={entries}
        selectedDate={selectedDate}
        onSelect={(date, entry) => {
          setSelectedDate(date);
          setSelectedEntry(entry ?? null);
        }}
      />

      <div className="card p-5">
        <h2 className="mb-3 text-sm font-semibold">
          {selectedEntry ? `פרטים – ${selectedEntry.date}` : "בחר יום מהיומן"}
        </h2>
        {selectedEntry ? (
          <div className="grid grid-cols-1 gap-4 text-sm md:grid-cols-2">
            <div>
              <div className="text-xs text-slate-400">P&L יומי</div>
              <div
                className={`text-lg font-semibold ${
                  Number(selectedEntry.daily_pnl ?? 0) >= 0
                    ? "text-emerald-300"
                    : "text-rose-300"
                }`}
              >
                ${Number(selectedEntry.daily_pnl ?? 0).toFixed(2)}
              </div>
            </div>
            <div>
              <div className="text-xs text-slate-400">VIX</div>
              <div>
                פתיחה: {selectedEntry.vix_open ?? "—"} | סגירה:{" "}
                {selectedEntry.vix_close ?? "—"}
              </div>
            </div>
            <div>
              <div className="text-xs text-slate-400">GEX Regime</div>
              <div>{selectedEntry.gex_regime ?? "—"}</div>
            </div>
            <div>
              <div className="text-xs text-slate-400">P&L שבועי</div>
              <div>${Number(selectedEntry.weekly_pnl ?? 0).toFixed(2)}</div>
            </div>
            <div className="md:col-span-2">
              <div className="text-xs text-slate-400">סיכום הסוכן</div>
              <div className="whitespace-pre-wrap">
                {selectedEntry.agent_summary ?? "—"}
              </div>
            </div>
            <div className="md:col-span-2">
              <div className="text-xs text-slate-400">תובנות / לקחים</div>
              <div className="whitespace-pre-wrap">
                {selectedEntry.lessons_learned ?? "—"}
              </div>
            </div>
            <div className="md:col-span-2">
              <div className="text-xs text-slate-400">הערות</div>
              <div className="whitespace-pre-wrap">{selectedEntry.notes ?? "—"}</div>
            </div>
          </div>
        ) : (
          <div className="text-sm text-slate-400">
            בחר יום כדי לראות סיכום מהסוכן, נתוני VIX ו-GEX, ולקחים.
          </div>
        )}
      </div>
    </div>
  );
}
