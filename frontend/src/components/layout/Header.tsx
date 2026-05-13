import { Menu } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useLocation } from "react-router-dom";

const PAGE_TITLES: Record<string, string> = {
  "/": "דשבורד",
  "/positions": "פוזיציות",
  "/journal": "יומן מסחר",
  "/scanner": "סורק שוק",
  "/chat": "שיחה עם הסוכן",
};

function getEstParts(date: Date) {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "numeric",
    minute: "numeric",
    hour12: false,
  });
  const parts = fmt.formatToParts(date);
  const out: Record<string, string> = {};
  for (const part of parts) {
    if (part.type !== "literal") out[part.type] = part.value;
  }
  return out;
}

function isMarketOpen(date: Date) {
  const parts = getEstParts(date);
  const weekday = parts.weekday;
  if (weekday === "Sat" || weekday === "Sun") return false;
  const minutes = Number(parts.hour) * 60 + Number(parts.minute);
  return minutes >= 9 * 60 + 30 && minutes <= 16 * 60;
}

export default function Header() {
  const [now, setNow] = useState(new Date());
  const location = useLocation();

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const title = PAGE_TITLES[location.pathname] ?? "Options Agent";
  const marketOpen = useMemo(() => isMarketOpen(now), [now]);
  const dateStr = useMemo(
    () =>
      new Intl.DateTimeFormat("he-IL", {
        weekday: "long",
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
      }).format(now),
    [now],
  );
  const timeStr = useMemo(
    () =>
      new Intl.DateTimeFormat("he-IL", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }).format(now),
    [now],
  );

  return (
    <header className="flex items-center justify-between border-b border-slate-800 bg-slate-950/60 p-3 backdrop-blur lg:px-6 lg:py-3">
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="rounded-md p-1.5 text-slate-300 hover:bg-slate-800 lg:hidden"
          aria-label="תפריט"
        >
          <Menu className="h-5 w-5" />
        </button>
        <div>
          <h1 className="text-sm font-semibold lg:text-lg">{title}</h1>
          <p className="hidden text-xs text-slate-400 lg:block">{dateStr}</p>
        </div>
      </div>
      <div className="flex items-center gap-2 text-sm lg:gap-4">
        <div
          className={`rounded-full px-2 py-1 text-[11px] font-medium lg:px-3 lg:text-xs ${
            marketOpen
              ? "bg-emerald-500/20 text-emerald-300"
              : "bg-rose-500/20 text-rose-300"
          }`}
        >
          {marketOpen ? "שוק פתוח 🟢" : "שוק סגור 🔴"}
        </div>
        <div className="hidden rounded-md bg-slate-800/60 px-3 py-1 font-mono text-xs lg:block">
          {timeStr}
        </div>
      </div>
    </header>
  );
}
