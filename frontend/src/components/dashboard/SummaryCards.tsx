import { useQuery } from "@tanstack/react-query";
import { Activity, DollarSign, TrendingUp, Zap } from "lucide-react";
import { getSummary } from "../../api/client";

type Tone = "positive" | "negative" | "neutral" | "warning";

const TONE_STYLES: Record<Tone, { chip: string; value: string }> = {
  positive: {
    chip: "from-emerald-500/25 to-emerald-500/5 text-emerald-300 border-emerald-400/20",
    value: "text-emerald-400",
  },
  negative: {
    chip: "from-rose-500/25 to-rose-500/5 text-rose-300 border-rose-400/20",
    value: "text-rose-400",
  },
  warning: {
    chip: "from-amber-500/25 to-amber-500/5 text-amber-300 border-amber-400/20",
    value: "text-amber-300",
  },
  neutral: {
    chip: "from-indigo-500/25 to-indigo-500/5 text-indigo-300 border-indigo-400/20",
    value: "text-slate-100",
  },
};

function StatCard({
  icon,
  title,
  value,
  hint,
  tone = "neutral",
  loading,
}: {
  icon: React.ReactNode;
  title: string;
  value: React.ReactNode;
  hint?: string;
  tone?: Tone;
  loading?: boolean;
}) {
  const styles = TONE_STYLES[tone];
  return (
    <div className="card card-hover p-4 animate-fade-up">
      <div className="flex items-start justify-between">
        <span className="text-[11px] font-semibold text-slate-400 lg:text-xs">
          {title}
        </span>
        <span
          className={`rounded-lg border bg-gradient-to-br p-2 ${styles.chip}`}
        >
          {icon}
        </span>
      </div>
      <div className={`num mt-2 text-xl font-bold lg:text-2xl ${styles.value}`}>
        {loading ? (
          <span className="inline-block h-6 w-20 animate-pulse rounded bg-slate-800" />
        ) : (
          value
        )}
      </div>
      {hint && <div className="mt-1 text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

function formatCurrency(value: number | undefined) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
    signDisplay: "exceptZero",
  }).format(value ?? 0);
}

export default function SummaryCards() {
  const { data, isLoading } = useQuery({
    queryKey: ["summary"],
    queryFn: getSummary,
    refetchInterval: 60_000,
  });

  const open = data?.open_positions ?? 0;
  const totalPnl = data?.total_realized_pnl ?? 0;
  const weekly = data?.last_journal?.weekly_pnl ?? 0;
  const gex = data?.last_journal?.gex_regime ?? "—";
  const gexKnown = gex !== "—" && !!gex;
  const gexPositive = gexKnown && gex.toLowerCase().includes("positive");

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4">
      <StatCard
        icon={<Activity className="h-4 w-4" />}
        title="פוזיציות פתוחות"
        tone="neutral"
        loading={isLoading}
        value={open}
        hint={open === 0 ? "אין חשיפה כרגע" : "עסקאות פעילות"}
      />
      <StatCard
        icon={<DollarSign className="h-4 w-4" />}
        title="רווח/הפסד כולל"
        tone={totalPnl >= 0 ? "positive" : "negative"}
        loading={isLoading}
        value={formatCurrency(totalPnl)}
        hint="מצטבר מכל העסקאות"
      />
      <StatCard
        icon={<TrendingUp className="h-4 w-4" />}
        title="רווח השבוע"
        tone={weekly >= 0 ? "positive" : "negative"}
        loading={isLoading}
        value={formatCurrency(weekly)}
        hint="יעד: $1,000"
      />
      <StatCard
        icon={<Zap className="h-4 w-4" />}
        title="משטר GEX"
        tone={!gexKnown ? "neutral" : gexPositive ? "positive" : "warning"}
        loading={isLoading}
        value={!gexKnown ? "—" : gexPositive ? "Positive" : "Negative"}
        hint={
          !gexKnown
            ? "ממתין לנתונים"
            : gexPositive
              ? "שוק רגוע – מכירת פרמיה"
              : "תנודתי – זהירות"
        }
      />
    </div>
  );
}
