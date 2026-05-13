import { Menu, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import NotificationButton from "../NotificationButton";

const PAGE_TITLES: Record<string, string> = {
  "/": "דשבורד",
  "/positions": "פוזיציות",
  "/journal": "יומן מסחר",
  "/scanner": "סורק שוק",
  "/chat": "שיחה עם הסוכן",
  "/analytics": "דשבורד אנליטיקס",
};

const MENU_ITEMS = [
  { path: "/", label: "דשבורד", icon: "🏠" },
  { path: "/positions", label: "פוזיציות", icon: "📈" },
  { path: "/journal", label: "יומן", icon: "📅" },
  { path: "/scanner", label: "סורק", icon: "🔍" },
  { path: "/analytics", label: "אנליטיקס", icon: "📊" },
  { path: "/chat", label: "צ'אט", icon: "💬" },
];

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
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Close drawer whenever route changes (safety net for back/forward nav).
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

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
    <>
      <header className="flex items-center justify-between border-b border-slate-800 bg-slate-950/60 p-3 backdrop-blur lg:px-6 lg:py-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            className="touch-manipulation rounded-lg p-2 text-slate-300 hover:bg-slate-700 active:bg-slate-600 lg:hidden"
            aria-label="תפריט"
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X size={24} /> : <Menu size={24} />}
          </button>
          <div>
            <h1 className="text-sm font-semibold lg:text-lg">{title}</h1>
            <p className="hidden text-xs text-slate-400 lg:block">{dateStr}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-sm lg:gap-4">
          <NotificationButton />
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

      {menuOpen && (
        <div
          className="fixed inset-0 z-50 lg:hidden"
          onClick={() => setMenuOpen(false)}
        >
          <div className="absolute inset-0 bg-black/50" />
          <div
            className="absolute right-0 top-0 h-full w-72 bg-slate-800 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-700 p-4">
              <span className="text-xl font-bold">Options Agent 🤖</span>
              <button
                type="button"
                onClick={() => setMenuOpen(false)}
                className="rounded-lg p-2 hover:bg-slate-700"
                aria-label="סגור תפריט"
              >
                <X size={20} />
              </button>
            </div>
            <nav className="space-y-2 p-4">
              {MENU_ITEMS.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  end={item.path === "/"}
                  onClick={() => setMenuOpen(false)}
                  className={({ isActive }) =>
                    `flex items-center gap-3 rounded-xl p-3 text-lg ${
                      isActive
                        ? "bg-blue-600 text-white"
                        : "text-slate-300 hover:bg-slate-700"
                    }`
                  }
                >
                  <span>{item.icon}</span>
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </nav>
          </div>
        </div>
      )}
    </>
  );
}
