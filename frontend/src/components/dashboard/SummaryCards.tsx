import { useQuery } from "@tanstack/react-query";
import { Activity, DollarSign, TrendingUp, Zap } from "lucide-react";
import { getSummary } from "../../api/client";

function Card({
  icon,
  title,
  value,
  accent,
}: {
  icon: React.ReactNode;
  title: string;
  value: React.ReactNode;
  accent?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3 shadow-lg lg:p-4">
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-slate-400 lg:text-xs">{title}</span>
        <span
          className={`rounded-md p-1.5 lg:p-2 ${accent ?? "bg-slate-800 text-slate-300"}`}
        >
          {icon}
        </span>
      </div>
      <div className="mt-2 text-xl font-bold lg:mt-3 lg:text-2xl">{value}</div>
    </div>
  );
}

function formatCurrency(value: number | undefined) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
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
  const gexPositive = gex?.toLowerCase().includes("positive");

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4">
      <Card
        icon={<Activity className="h-4 w-4" />}
        title="פוזיציות פתוחות"
        accent="bg-emerald-500/20 text-emerald-300"
        value={isLoading ? "…" : open}
      />
      <Card
        icon={<DollarSign className="h-4 w-4" />}
        title="רווח/הפסד כולל"
        accent={
          totalPnl >= 0
            ? "bg-emerald-500/20 text-emerald-300"
            : "bg-rose-500/20 text-rose-300"
        }
        value={
          <span className={totalPnl >= 0 ? "text-emerald-400" : "text-rose-400"}>
            {isLoading ? "…" : formatCurrency(totalPnl)}
          </span>
        }
      />
      <Card
        icon={<TrendingUp className="h-4 w-4" />}
        title="רווח השבוע"
        accent={
          weekly >= 0
            ? "bg-emerald-500/20 text-emerald-300"
            : "bg-rose-500/20 text-rose-300"
        }
        value={
          <span className={weekly >= 0 ? "text-emerald-400" : "text-rose-400"}>
            {isLoading ? "…" : formatCurrency(weekly)}
          </span>
        }
      />
      <Card
        icon={<Zap className="h-4 w-4" />}
        title="מצב GEX"
        accent={
          gexPositive
            ? "bg-emerald-500/20 text-emerald-300"
            : "bg-amber-500/20 text-amber-300"
        }
        value={
          <span>{gex === "—" ? "—" : gexPositive ? "חיובי 🟢" : "שלילי 🔴"}</span>
        }
      />
    </div>
  );
}
