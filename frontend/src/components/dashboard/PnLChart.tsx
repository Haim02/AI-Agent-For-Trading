import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
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
        date: entry.date,
        pnl: Number(entry.daily_pnl ?? 0),
      }));
  }, [data]);

  const last = chartData[chartData.length - 1]?.pnl ?? 0;
  const trendColor = last >= 0 ? "#34d399" : "#f87171";

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3 shadow-lg lg:p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">רווח/הפסד יומי – 30 ימים אחרונים</h2>
        <span className="hidden text-xs text-slate-400 lg:inline">
          מנוקד לפי יומן המסחר
        </span>
      </div>
      <div className="h-[200px] w-full lg:h-[300px]">
        {isLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">
            טוען…
          </div>
        ) : chartData.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-slate-400">
            אין רשומות יומן עדיין
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={chartData}
              margin={{ top: 10, right: 8, bottom: 0, left: 0 }}
            >
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
              <XAxis
                dataKey="date"
                stroke="#64748b"
                fontSize={isDesktop ? 11 : 9}
                tick={isDesktop ? { fill: "#94a3b8" } : false}
                hide={!isDesktop}
              />
              <YAxis
                stroke="#64748b"
                fontSize={isDesktop ? 11 : 9}
                tick={{ fill: "#94a3b8" }}
                width={isDesktop ? 40 : 32}
              />
              <Tooltip
                contentStyle={{
                  background: "#0f172a",
                  border: "1px solid #1e293b",
                  borderRadius: 8,
                  color: "#e2e8f0",
                  fontSize: 12,
                }}
                formatter={(value: any) => [
                  `$${Number(value).toFixed(0)}`,
                  "P&L",
                ]}
                labelFormatter={(label) => `תאריך: ${label}`}
              />
              <Line
                type="monotone"
                dataKey="pnl"
                stroke={trendColor}
                strokeWidth={2}
                dot={false}
                activeDot={{ r: 4 }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
