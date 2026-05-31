import { NavLink } from "react-router-dom";
import {
  BarChart2,
  Brain,
  Calendar,
  CheckSquare,
  Fish,
  LayoutDashboard,
  LineChart,
  MessageCircle,
  Search,
  TrendingUp,
} from "lucide-react";

const NAV_ITEMS = [
  { to: "/", label: "דשבורד", icon: LayoutDashboard },
  { to: "/positions", label: "פוזיציות", icon: TrendingUp },
  { to: "/journal", label: "יומן", icon: Calendar },
  { to: "/scanner", label: "סורק", icon: Search },
  { to: "/chart", label: "GEX Chart", icon: LineChart },
  { to: "/checklist", label: "צ'קליסט", icon: CheckSquare },
  { to: "/narrative", label: "ניתוח AI", icon: Brain },
  { to: "/signals", label: "סיגנלים", icon: Fish },
  { to: "/chat", label: "צ'אט", icon: MessageCircle },
  { to: "/analytics", label: "דשבורד אנליטיקס", icon: BarChart2 },
];

export default function Sidebar() {
  return (
    <aside className="flex w-60 shrink-0 flex-col border-l border-slate-800 bg-slate-950">
      <div className="px-5 py-6 text-xl font-bold tracking-tight">
        Options Agent <span aria-hidden>🤖</span>
      </div>
      <nav className="flex flex-col gap-1 px-3">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                isActive
                  ? "bg-blue-600/90 text-white shadow"
                  : "text-slate-300 hover:bg-slate-800/70 hover:text-white"
              }`
            }
          >
            <Icon className="h-4 w-4" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto px-5 py-4 text-xs text-slate-500">
        v0.1 • סוכן אוטונומי
      </div>
    </aside>
  );
}
