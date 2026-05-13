import { ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import type { JournalEntry } from "../../types";

const HEBREW_MONTHS = [
  "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
  "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
];

const WEEKDAYS = ["א'", "ב'", "ג'", "ד'", "ה'", "ו'", "ש'"];

interface JournalCalendarProps {
  entries: JournalEntry[];
  selectedDate?: string | null;
  onSelect: (date: string, entry?: JournalEntry) => void;
}

function toIsoDate(date: Date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export default function JournalCalendar({
  entries,
  selectedDate,
  onSelect,
}: JournalCalendarProps) {
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), 1);
  });

  const entriesByDate = useMemo(() => {
    const map: Record<string, JournalEntry> = {};
    for (const entry of entries) {
      map[entry.date] = entry;
    }
    return map;
  }, [entries]);

  const cells = useMemo(() => {
    const firstDay = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    const daysInMonth = new Date(cursor.getFullYear(), cursor.getMonth() + 1, 0).getDate();
    const lead = firstDay.getDay();

    const out: ({ date: string; entry?: JournalEntry } | null)[] = [];
    for (let i = 0; i < lead; i++) out.push(null);
    for (let d = 1; d <= daysInMonth; d++) {
      const date = new Date(cursor.getFullYear(), cursor.getMonth(), d);
      const iso = toIsoDate(date);
      out.push({ date: iso, entry: entriesByDate[iso] });
    }
    return out;
  }, [cursor, entriesByDate]);

  const monthlyPnl = useMemo(
    () =>
      cells.reduce((sum, cell) => {
        if (!cell?.entry) return sum;
        return sum + Number(cell.entry.daily_pnl ?? 0);
      }, 0),
    [cells],
  );

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3 shadow-lg lg:p-4">
      <div className="mb-3 flex items-center justify-between">
        <button
          type="button"
          onClick={() => setCursor((c) => new Date(c.getFullYear(), c.getMonth() - 1, 1))}
          className="rounded-md bg-slate-800 p-1.5 hover:bg-slate-700"
          aria-label="חודש קודם"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
        <div className="text-sm font-semibold">
          {HEBREW_MONTHS[cursor.getMonth()]} {cursor.getFullYear()}
          <span
            className={`mr-3 text-xs ${monthlyPnl >= 0 ? "text-emerald-300" : "text-rose-300"}`}
          >
            (${monthlyPnl.toFixed(2)})
          </span>
        </div>
        <button
          type="button"
          onClick={() => setCursor((c) => new Date(c.getFullYear(), c.getMonth() + 1, 1))}
          className="rounded-md bg-slate-800 p-1.5 hover:bg-slate-700"
          aria-label="חודש הבא"
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
      </div>

      <div className="grid grid-cols-7 gap-1 text-center text-xs text-slate-400">
        {WEEKDAYS.map((label) => (
          <div key={label} className="py-1">
            {label}
          </div>
        ))}
      </div>
      <div className="mt-1 grid grid-cols-7 gap-1">
        {cells.map((cell, idx) => {
          if (!cell)
            return (
              <div
                key={`empty-${idx}`}
                className="h-10 rounded-md bg-slate-950/30 lg:h-16"
              />
            );
          const pnl = Number(cell.entry?.daily_pnl ?? 0);
          const hasEntry = !!cell.entry;
          const isPositive = pnl > 0;
          const isNegative = pnl < 0;
          const isSelected = selectedDate === cell.date;
          return (
            <button
              key={cell.date}
              type="button"
              onClick={() => onSelect(cell.date, cell.entry)}
              className={`flex h-10 flex-col items-center justify-center rounded-md border text-xs transition lg:h-16 ${
                isSelected
                  ? "border-blue-500 bg-blue-500/10"
                  : "border-slate-800 bg-slate-950/40 hover:border-slate-600"
              }`}
            >
              <span className="text-[10px] text-slate-400 lg:text-[11px]">
                {Number(cell.date.slice(-2))}
              </span>
              {hasEntry && (
                <span
                  className={`mt-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-mono lg:mt-1 lg:px-2 lg:text-[10px] ${
                    isPositive
                      ? "bg-emerald-500/30 text-emerald-200"
                      : isNegative
                        ? "bg-rose-500/30 text-rose-200"
                        : "bg-slate-700 text-slate-200"
                  }`}
                >
                  ${pnl.toFixed(0)}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
