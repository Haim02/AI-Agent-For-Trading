import { useMutation, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { getGEX, getIVScan } from "../api/client";
import ScanResults from "../components/scanner/ScanResults";
import type { IVScanResponse } from "../types";

function fmt(value: number | string | undefined | null): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return num.toLocaleString();
}

export default function Scanner() {
  const gexQuery = useQuery({
    queryKey: ["gex", "SPX"],
    queryFn: () => getGEX("SPX"),
  });

  const scanMutation = useMutation<IVScanResponse>({
    mutationFn: () => getIVScan(50),
  });

  const lastScan = scanMutation.data?.scan_time;
  const gex = gexQuery.data?.gex;
  const gexRegime = gex?.regime;
  const totalMillions = gex?.gex_total;
  const isPositive = gexRegime === "positive";
  const regimeText = isPositive ? "חיובי" : gexRegime === "negative" ? "שלילי" : (gexRegime ?? "—");
  const gexAmountClass = isPositive
    ? "text-emerald-300"
    : gexRegime === "negative"
      ? "text-rose-300"
      : "text-slate-300";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col items-stretch gap-3 lg:flex-row lg:items-center lg:justify-between">
        <h1 className="text-xl font-semibold">סורק שוק 🔍</h1>
        <button
          type="button"
          onClick={() => scanMutation.mutate()}
          disabled={scanMutation.isPending}
          className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50 lg:w-auto"
        >
          <Search className="h-4 w-4" />
          {scanMutation.isPending ? "סורק…" : "סרוק עכשיו"}
        </button>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3 shadow-lg lg:p-4">
        <div className="flex flex-col gap-2 text-sm lg:flex-row lg:flex-wrap lg:items-center lg:gap-4">
          <div>
            <span className="text-xs text-slate-400">SPX GEX:</span>{" "}
            <span
              className={`rounded-md px-2 py-0.5 text-xs ${
                isPositive
                  ? "bg-emerald-500/20 text-emerald-300"
                  : gexRegime === "negative"
                    ? "bg-rose-500/20 text-rose-300"
                    : "bg-amber-500/20 text-amber-300"
              }`}
            >
              {regimeText === "—" && gexQuery.isLoading ? "טוען…" : regimeText}
            </span>
          </div>
          <div>
            <span className="text-xs text-slate-400">Spot SPX:</span>{" "}
            <span className="font-mono">{fmt(gex?.spot_price)}</span>
          </div>
          <div>
            <span className="text-xs text-slate-400">GEX:</span>{" "}
            <span className={`font-mono ${gexAmountClass}`}>
              {totalMillions !== undefined && totalMillions !== null
                ? `${Number(totalMillions).toLocaleString()}M$`
                : "—"}
            </span>
          </div>
          <div>
            <span className="text-xs text-slate-400">Gamma Flip:</span>{" "}
            <span className="font-mono">{fmt(gex?.gamma_flip_level)}</span>
          </div>
          <div>
            <span className="text-xs text-slate-400">Call Wall:</span>{" "}
            <span className="font-mono">{fmt(gex?.call_wall)}</span>
          </div>
          <div>
            <span className="text-xs text-slate-400">Put Wall:</span>{" "}
            <span className="font-mono">{fmt(gex?.put_wall)}</span>
          </div>
          <div className="text-xs text-slate-400 lg:ml-auto">
            סריקה אחרונה: {lastScan ? new Date(lastScan).toLocaleString("he-IL") : "—"}
          </div>
        </div>
      </div>

      <ScanResults data={scanMutation.data} isLoading={scanMutation.isPending} />
    </div>
  );
}
