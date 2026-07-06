import { Bot, Menu, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import NotificationButton from "../NotificationButton";
import { NAV_GROUPS, pageInfo } from "./nav";

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

function formatClock(date: Date, timeZone?: string) {
  return new Intl.DateTimeFormat("he-IL", {
    hour: "2-digit",
    minute: "2-digit",
    ...(timeZone ? { timeZone } : {}),
  }).format(date);
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

  const info = pageInfo(location.pathname);
  const marketOpen = useMemo(() => isMarketOpen(now), [now]);
  const dateStr = useMemo(
    () =>
      new Intl.DateTimeFormat("he-IL", {
        weekday: "long",
        day: "2-digit",
        month: "2-digit",
      }).format(now),
    [now],
  );

  return (
    <>
      <header className="glass flex items-center justify-between border-b px-3 py-2.5 lg:px-6">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            className="touch-manipulation rounded-lg p-2 text-slate-300 transition-colors hover:bg-white/[0.06] active:bg-white/[0.1] lg:hidden"
            aria-label="תפריט"
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X size={22} /> : <Menu size={22} />}
          </button>
          <div>
            <h1 className="text-sm font-bold tracking-tight lg:text-lg">
              {info?.label ?? "Options Agent"}
            </h1>
            <p className="hidden text-[11px] text-slate-500 lg:block">
              {info?.subtitle ? `${info.subtitle} · ` : ""}
              {dateStr}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 lg:gap-3">
          <NotificationButton />

          {/* Market status */}
          <div
            className={`badge border ${
              marketOpen
                ? "border-emerald-400/20 bg-emerald-500/10 text-emerald-300"
                : "border-rose-400/20 bg-rose-500/10 text-rose-300"
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                marketOpen ? "bg-emerald-400 animate-pulse-dot" : "bg-rose-400"
              }`}
            />
            {marketOpen ? "שוק פתוח" : "שוק סגור"}
          </div>

          {/* Dual clock – Israel + New York */}
          <div className="num hidden items-center gap-3 rounded-lg border border-slate-800/80 bg-slate-900/60 px-3 py-1 text-xs lg:flex">
            <span>
              <span className="text-slate-500">ת"א </span>
              <span className="text-slate-200">{formatClock(now)}</span>
            </span>
            <span className="h-3 w-px bg-slate-700" />
            <span>
              <span className="text-slate-500">NY </span>
              <span className="text-slate-200">
                {formatClock(now, "America/New_York")}
              </span>
            </span>
          </div>
        </div>
      </header>

      {/* Mobile drawer */}
      {menuOpen && (
        <div
          className="fixed inset-0 z-50 lg:hidden"
          onClick={() => setMenuOpen(false)}
        >
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
          <div
            className="glass absolute right-0 top-0 flex h-full w-72 flex-col border-l shadow-2xl animate-fade-up"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-800/60 p-4">
              <div className="flex items-center gap-2.5">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-cyan-400">
                  <Bot className="h-4 w-4 text-white" />
                </div>
                <span className="font-extrabold tracking-tight">
                  Options <span className="grad-text">Agent</span>
                </span>
              </div>
              <button
                type="button"
                onClick={() => setMenuOpen(false)}
                className="rounded-lg p-2 text-slate-400 hover:bg-white/[0.06]"
                aria-label="סגור תפריט"
              >
                <X size={18} />
              </button>
            </div>
            <nav className="flex-1 space-y-4 overflow-y-auto p-3">
              {NAV_GROUPS.map((group) => (
                <div key={group.title}>
                  <div className="mb-1 px-3 text-[10px] font-bold uppercase tracking-widest text-slate-500">
                    {group.title}
                  </div>
                  {group.items.map(({ to, label, icon: Icon }) => (
                    <NavLink
                      key={to}
                      to={to}
                      end={to === "/"}
                      onClick={() => setMenuOpen(false)}
                      className={({ isActive }) =>
                        `flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium ${
                          isActive
                            ? "bg-gradient-to-l from-indigo-500/25 to-indigo-500/5 text-white"
                            : "text-slate-300 hover:bg-white/[0.04]"
                        }`
                      }
                    >
                      <Icon className="h-4 w-4 text-slate-400" />
                      <span>{label}</span>
                    </NavLink>
                  ))}
                </div>
              ))}
            </nav>
          </div>
        </div>
      )}
    </>
  );
}
