import { NavLink } from "react-router-dom";
import {
  CheckSquare,
  LayoutDashboard,
  LineChart,
  MessageCircle,
  TrendingUp,
} from "lucide-react";

const ITEMS = [
  { to: "/", label: "דשבורד", icon: LayoutDashboard },
  { to: "/chart", label: "צ'ארט", icon: LineChart },
  { to: "/checklist", label: "צ'קליסט", icon: CheckSquare },
  { to: "/positions", label: "פוזיציות", icon: TrendingUp },
  { to: "/chat", label: "צ'אט", icon: MessageCircle },
];

export default function BottomNav() {
  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 flex h-16 items-center justify-around border-t border-slate-700 bg-slate-800 lg:hidden"
      aria-label="ניווט תחתון"
    >
      {ITEMS.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === "/"}
          className={({ isActive }) =>
            `flex flex-1 flex-col items-center justify-center gap-0.5 py-1 transition-colors ${
              isActive ? "text-blue-400" : "text-slate-400 hover:text-slate-200"
            }`
          }
        >
          <Icon size={22} />
          <span className="text-xs">{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
