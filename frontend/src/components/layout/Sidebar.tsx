import { NavLink } from "react-router-dom";
import { Bot } from "lucide-react";
import { NAV_GROUPS } from "./nav";

export default function Sidebar() {
  return (
    <aside className="glass flex h-full w-64 shrink-0 flex-col border-l">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-6">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-cyan-400 shadow-lg shadow-indigo-500/25">
          <Bot className="h-5 w-5 text-white" />
        </div>
        <div>
          <div className="text-base font-extrabold tracking-tight">
            Options <span className="grad-text">Agent</span>
          </div>
          <div className="text-[11px] font-medium text-slate-500">
            SPX 0DTE · GEX/DEX
          </div>
        </div>
      </div>

      {/* Nav groups */}
      <nav className="flex-1 space-y-5 overflow-y-auto px-3 pb-4">
        {NAV_GROUPS.map((group) => (
          <div key={group.title}>
            <div className="mb-1.5 px-3 text-[10px] font-bold uppercase tracking-widest text-slate-500">
              {group.title}
            </div>
            <div className="flex flex-col gap-0.5">
              {group.items.map(({ to, label, icon: Icon }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={to === "/"}
                  className={({ isActive }) =>
                    `group relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-150 ${
                      isActive
                        ? "bg-gradient-to-l from-indigo-500/25 to-indigo-500/5 text-white"
                        : "text-slate-400 hover:bg-white/[0.04] hover:text-slate-100"
                    }`
                  }
                >
                  {({ isActive }) => (
                    <>
                      {/* Active indicator bar (RTL: sits on the right edge) */}
                      <span
                        className={`absolute -right-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-full bg-gradient-to-b from-indigo-400 to-cyan-400 transition-opacity ${
                          isActive ? "opacity-100" : "opacity-0"
                        }`}
                      />
                      <Icon
                        className={`h-4 w-4 shrink-0 transition-colors ${
                          isActive
                            ? "text-indigo-300"
                            : "text-slate-500 group-hover:text-slate-300"
                        }`}
                      />
                      <span>{label}</span>
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-slate-800/60 px-5 py-4">
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse-dot" />
          <span>הסוכן פעיל · v1.0</span>
        </div>
      </div>
    </aside>
  );
}
