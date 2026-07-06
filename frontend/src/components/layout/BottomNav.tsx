import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  LineChart,
  MessageCircle,
  Search,
  TrendingUp,
} from "lucide-react";

const ITEMS = [
  { to: "/", label: "דשבורד", icon: LayoutDashboard },
  { to: "/chart", label: "צ'ארט", icon: LineChart },
  { to: "/chat", label: "צ'אט", icon: MessageCircle },
  { to: "/positions", label: "פוזיציות", icon: TrendingUp },
  { to: "/scanner", label: "סורק", icon: Search },
];

export default function BottomNav() {
  return (
    <nav
      className="glass fixed bottom-0 left-0 right-0 z-40 flex h-16 items-center justify-around border-t pb-[env(safe-area-inset-bottom)] lg:hidden"
      aria-label="ניווט תחתון"
    >
      {ITEMS.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === "/"}
          className={({ isActive }) =>
            `relative flex flex-1 flex-col items-center justify-center gap-0.5 py-1.5 transition-colors ${
              isActive ? "text-indigo-300" : "text-slate-500 hover:text-slate-300"
            }`
          }
        >
          {({ isActive }) => (
            <>
              <span
                className={`absolute top-0 h-0.5 w-8 rounded-full bg-gradient-to-r from-indigo-400 to-cyan-400 transition-opacity ${
                  isActive ? "opacity-100" : "opacity-0"
                }`}
              />
              <Icon size={21} />
              <span className="text-[10px] font-medium">{label}</span>
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
