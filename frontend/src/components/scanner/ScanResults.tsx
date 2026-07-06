import * as Tabs from "@radix-ui/react-tabs";
import { Loader2 } from "lucide-react";
import type { IVScanResponse } from "../../types";
import IVRankCard from "./IVRankCard";

interface Props {
  data?: IVScanResponse;
  isLoading: boolean;
}

const TABS = [
  { value: "golden", label: "הזדמנויות זהב" },
  { value: "sell", label: "מכירה" },
  { value: "buy", label: "קנייה" },
];

export default function ScanResults({ data, isLoading }: Props) {
  if (isLoading) {
    return (
      <div className="flex h-40 items-center justify-center gap-2 text-slate-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        סורק שוק…
      </div>
    );
  }

  if (!data) {
    return (
      <div className="card p-6 text-sm text-slate-400">
        לחץ "סרוק עכשיו" כדי להתחיל סריקה.
      </div>
    );
  }

  const groups: Record<string, typeof data.golden_opportunities> = {
    golden: data.golden_opportunities ?? [],
    sell: data.sell_opportunities ?? [],
    buy: data.buy_opportunities ?? [],
  };

  return (
    <Tabs.Root
      defaultValue="golden"
      className="card p-3 lg:p-4"
    >
      <Tabs.List className="scrollbar-hide mb-4 flex gap-2 overflow-x-auto border-b border-slate-700 pb-2">
        {TABS.map((tab) => (
          <Tabs.Trigger
            key={tab.value}
            value={tab.value}
            className="shrink-0 whitespace-nowrap rounded-md px-3 py-1 text-xs text-slate-300 hover:bg-slate-800 data-[state=active]:bg-indigo-600 data-[state=active]:text-white"
          >
            {tab.label} ({groups[tab.value].length})
          </Tabs.Trigger>
        ))}
      </Tabs.List>

      {TABS.map((tab) => (
        <Tabs.Content key={tab.value} value={tab.value}>
          {groups[tab.value].length === 0 ? (
            <div className="py-10 text-center text-sm text-slate-400">
              אין תוצאות בקטגוריה זו.
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
              {groups[tab.value].map((result) => (
                <IVRankCard key={result.ticker} result={result} />
              ))}
            </div>
          )}
        </Tabs.Content>
      ))}
    </Tabs.Root>
  );
}
