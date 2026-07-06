import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getJournal } from "../../api/client";

function useIsDesktop() {
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window === "undefined" ? false : window.matchMedia("(min-width: 768px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const handler = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return isDesktop;
}

export default function PnLChart() {
  const isDesktop = useIsDesktop();
  const { data, isLoading } = useQuery({
    queryKey: ["journal", "pnl-chart"],
    queryFn: () => getJournal(30),
  });

  const chartData = useMemo(() => {
    if (!data) return [];
    return [...data]
      .slice(0, 30)
      .reverse()
      .map((entry) => ({
        date: entry.date?.slice(5), // MM-DD
        pnl: Number(entry.daily_pnl ?? 0),
      }));
  }, [data]);

  const total = useMemo(
    () => chartData.reduce((sum, d) => sum + d.pnl, 0),
    [chartData],
  );
  const isUp = total >= 0;
  const stroke = isUp ? "#34d399" : "#fb7185";
  const gradientId = isUp ? "pnlGradUp" : "pnlGradDown";

  return (
    <div className="card p-4 lg:p-5 animate-fade-up">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h2 className="section-title">רווח/הפסד יומי</h2>
          <p className="text-[11px] text-slate-500">30 ימים אחרונים · לפי יומן המסחר</p>
        </div>
        <span
          className={`badge border ${
            isUp
              ? "border-emerald-400/20 bg-emerald-500/10 text-emerald-300"
              : "border-rose-400/20 bg-rose-500/10 text-rose-300"
          }`}
        >
          <span className="num">
            {new Intl.NumberFormat("en-US", {
              style: "currency",
              currency: "USD",
              maximumFractionDigits: 0,
              signDisplay: "exceptZero",
            }).format(total)}
          </span>
          סה"כ
        </span>
      </div>

      <div className="h-[200px] w-full lg:h-[300px]" dir="ltr">
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-500">
            טוען…
          </div>
        ) : chartData.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-1 text-sm text-slate-500">
            <span>אין רשומות יומן עדיין</span>
            <span className="text-xs text-slate-600">
              הגרף יתמלא אוטומטית אחרי ימי מסחר
            </span>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={chartData}
              margin={{ top: 10, right: 8, bottom: 0, left: 0 }}
            >
              <defs>
                <linearGradient id="pnlGradUp" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#34d399" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#34d399" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="pnlGradDown" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#fb7185" stopOpacity={0.35} />
                  <stop offset="100%" stopColor="#fb7185" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="date"
                stroke="#334155"
                fontSize={isDesktop ? 11 : 9}
                tick={isDesktop ? { fill: "#64748b" } : false}
                tickLine={false}
                axisLine={false}
                hide={!isDesktop}
              />
              <YAxis
                stroke="#334155"
                fontSize={isDesktop ? 11 : 9}
                tick={{ fill: "#64748b" }}
                tickLine={false}
                axisLine={false}
                width={isDesktop ? 44 : 34}
                tickFormatter={(v: number) => `$${v}`}
              />
              <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 4" />
              <Tooltip
                contentStyle={{
                  background: "rgba(6, 9, 19, 0.95)",
                  border: "1px solid #1e293b",
                  borderRadius: 10,
                  color: "#e2e8f0",
                  fontSize: 12,
                  boxShadow: "0 10px 30px rgba(0,0,0,0.5)",
                }}
                formatter={(value) => [`$${Number(value ?? 0).toFixed(0)}`, "P&L"]}
                labelFormatter={(label) => `תאריך: ${label}`}
              />
              <Area
                type="monotone"
                dataKey="pnl"
                stroke={stroke}
                strokeWidth={2.5}
                fill={`url(#${gradientId})`}
                dot={false}
                activeDot={{ r: 4, strokeWidth: 0 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
