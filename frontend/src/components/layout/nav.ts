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
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  subtitle: string;
  icon: LucideIcon;
}

export interface NavGroup {
  title: string;
  items: NavItem[];
}

/** Single source of truth for every navigation surface (sidebar, header, bottom nav). */
export const NAV_GROUPS: NavGroup[] = [
  {
    title: "סקירה",
    items: [
      { to: "/", label: "דשבורד", subtitle: "תמונת מצב יומית", icon: LayoutDashboard },
      { to: "/analytics", label: "אנליטיקס", subtitle: "ביצועים לאורך זמן", icon: BarChart2 },
    ],
  },
  {
    title: "מסחר",
    items: [
      { to: "/chart", label: "GEX Chart", subtitle: "רמות גמא על SPX", icon: LineChart },
      { to: "/positions", label: "פוזיציות", subtitle: "עסקאות פתוחות וסגורות", icon: TrendingUp },
      { to: "/journal", label: "יומן מסחר", subtitle: "תיעוד יומי", icon: Calendar },
      { to: "/checklist", label: "צ'קליסט", subtitle: "בדיקות לפני עסקה", icon: CheckSquare },
    ],
  },
  {
    title: "סוכן AI",
    items: [
      { to: "/chat", label: "צ'אט", subtitle: "שיחה עם הסוכן", icon: MessageCircle },
      { to: "/narrative", label: "ניתוח AI", subtitle: "נרטיב שוק יומי", icon: Brain },
      { to: "/signals", label: "סיגנלים", subtitle: "תזרים אופציות חריג", icon: Fish },
      { to: "/scanner", label: "סורק שוק", subtitle: "מניות חמות היום", icon: Search },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

export function pageInfo(pathname: string): NavItem | undefined {
  return NAV_ITEMS.find((item) => item.to === pathname);
}
