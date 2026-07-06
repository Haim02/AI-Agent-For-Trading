import type { Position } from "../../types";

export default function PositionCard({ position }: { position: Position }) {
  const credit = position.premium_received ?? position.premium_paid ?? 0;
  return (
    <div className="card card-hover p-4">
      <div className="flex items-center justify-between">
        <span className="font-mono text-lg font-bold">{position.ticker}</span>
        <span
          className={`rounded-full px-2 py-0.5 text-xs ${
            position.status === "open"
              ? "bg-emerald-500/20 text-emerald-300"
              : "bg-slate-600/40 text-slate-300"
          }`}
        >
          {position.status}
        </span>
      </div>
      <div className="mt-2 text-sm text-slate-300">{position.strategy}</div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-400">
        <div>פרמיה: <span className="text-emerald-300">${credit}</span></div>
        <div>מקס׳ רווח: <span className="text-slate-200">${position.max_profit ?? "—"}</span></div>
        <div>פקיעה: {position.expiration_date?.slice(0, 10) ?? "—"}</div>
        <div>VIX: {position.vix_at_entry ?? "—"}</div>
      </div>
    </div>
  );
}
