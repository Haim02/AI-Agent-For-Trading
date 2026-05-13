import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { createPosition, deletePosition, getPositions } from "../api/client";
import PositionsTable from "../components/positions/PositionsTable";

const STRATEGIES: { value: string; label: string }[] = [
  { value: "iron_condor", label: "Iron Condor" },
  { value: "short_strangle", label: "Short Strangle" },
  { value: "bull_put_spread", label: "Bull Put Spread" },
  { value: "bear_call_spread", label: "Bear Call Spread" },
  { value: "long_straddle", label: "Long Straddle" },
  { value: "calendar_spread", label: "Calendar Spread" },
  { value: "other", label: "אחר" },
];

function formatDate(dateStr: string): string | undefined {
  if (!dateStr) return undefined;
  return new Date(dateStr).toISOString();
}

export default function Positions() {
  const [open, setOpen] = useState(false);
  const [ticker, setTicker] = useState("");
  const [strategy, setStrategy] = useState(STRATEGIES[0].value);
  const [premium, setPremium] = useState("");
  const [expiration, setExpiration] = useState("");
  const queryClient = useQueryClient();

  const { data: positions = [] } = useQuery({
    queryKey: ["positions"],
    queryFn: () => getPositions(),
  });

  const createMutation = useMutation({
    mutationFn: createPosition,
    onSuccess: () => {
      setOpen(false);
      setTicker("");
      setStrategy(STRATEGIES[0].value);
      setPremium("");
      setExpiration("");
      queryClient.invalidateQueries({ queryKey: ["positions"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: deletePosition,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["positions"] });
      queryClient.invalidateQueries({ queryKey: ["summary"] });
    },
  });

  const submit = () => {
    if (!ticker.trim()) return;
    createMutation.mutate({
      ticker: ticker.trim().toUpperCase(),
      strategy,
      premium_received: premium ? Number(premium) : undefined,
      expiration_date: formatDate(expiration),
    });
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">פוזיציות</h1>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
        >
          <Plus className="h-4 w-4" />
          הוסף פוזיציה +
        </button>
      </div>

      <PositionsTable
        positions={positions}
        onDelete={(id) => deleteMutation.mutate(id)}
      />

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900 p-6 shadow-xl">
            <h2 className="mb-4 text-lg font-semibold">פוזיציה חדשה</h2>
            <div className="flex flex-col gap-3">
              <label className="flex flex-col text-xs text-slate-400">
                מניה
                <input
                  type="text"
                  value={ticker}
                  onChange={(e) => setTicker(e.target.value)}
                  className="mt-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
                  placeholder="AAPL"
                />
              </label>
              <label className="flex flex-col text-xs text-slate-400">
                אסטרטגיה
                <select
                  value={strategy}
                  onChange={(e) => setStrategy(e.target.value)}
                  className="mt-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
                >
                  {STRATEGIES.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col text-xs text-slate-400">
                פרמיה ($)
                <input
                  type="number"
                  value={premium}
                  onChange={(e) => setPremium(e.target.value)}
                  className="mt-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
                  placeholder="150"
                />
              </label>
              <label className="flex flex-col text-xs text-slate-400">
                תאריך פקיעה
                <input
                  type="date"
                  value={expiration}
                  onChange={(e) => setExpiration(e.target.value)}
                  className="mt-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
                />
              </label>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-lg bg-slate-700 px-4 py-2 text-sm text-slate-200 hover:bg-slate-600"
              >
                ביטול
              </button>
              <button
                type="button"
                disabled={createMutation.isPending}
                onClick={submit}
                className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
              >
                {createMutation.isPending ? "שומר…" : "שמור"}
              </button>
            </div>
            {createMutation.isError && (
              <div className="mt-3 text-xs text-rose-300">
                שגיאה בשמירה: {(createMutation.error as Error).message}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
