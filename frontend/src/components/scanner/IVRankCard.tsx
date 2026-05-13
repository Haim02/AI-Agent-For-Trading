import type { IVRankResult } from "../../types";

function barColor(rank: number) {
  if (rank > 80) return "bg-rose-500";
  if (rank > 50) return "bg-amber-500";
  if (rank < 25) return "bg-blue-500";
  return "bg-slate-500";
}

function signalLabel(signal: IVRankResult["signal"]) {
  switch (signal) {
    case "SELL":
      return "מכור";
    case "BUY":
      return "קנה";
    default:
      return "ניטרלי";
  }
}

function signalAccent(signal: IVRankResult["signal"]) {
  switch (signal) {
    case "SELL":
      return "bg-rose-500/20 text-rose-200";
    case "BUY":
      return "bg-blue-500/20 text-blue-200";
    default:
      return "bg-slate-700 text-slate-200";
  }
}

export default function IVRankCard({ result }: { result: IVRankResult }) {
  const rank = Math.max(0, Math.min(100, Number(result.iv_rank ?? 0)));
  // Mobile shows top 2 strategies, desktop shows the rest via lg:inline-flex
  const strategies = result.recommended_strategies ?? [];

  return (
    <div className="flex flex-col gap-2 rounded-xl border border-slate-800 bg-slate-900/70 p-3 shadow-md lg:gap-3 lg:p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-lg font-bold lg:text-2xl">
          {result.ticker}
        </span>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium lg:text-xs ${signalAccent(
            result.signal,
          )}`}
        >
          {signalLabel(result.signal)} · {result.signal_strength}
        </span>
      </div>

      <div>
        <div className="flex items-center justify-between text-xs text-slate-400">
          <span>IV Rank</span>
          <span className="font-mono text-slate-200">{rank.toFixed(1)}</span>
        </div>
        <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-800">
          <div className={`${barColor(rank)} h-full`} style={{ width: `${rank}%` }} />
        </div>
      </div>

      <p
        className="line-clamp-2 whitespace-pre-wrap text-xs leading-relaxed text-slate-300 lg:line-clamp-none"
        title={result.explanation}
      >
        {result.explanation}
      </p>

      {strategies.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {strategies.slice(0, 2).map((s) => (
            <span
              key={s}
              className="rounded-md bg-slate-800 px-2 py-0.5 text-[11px] text-slate-200"
            >
              {s}
            </span>
          ))}
          {strategies.slice(2).map((s) => (
            <span
              key={s}
              className="hidden rounded-md bg-slate-800 px-2 py-0.5 text-[11px] text-slate-200 lg:inline-block"
            >
              {s}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
