import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import { useQuery } from "@tanstack/react-query";

const API =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000/api";

const TICKERS = ["SPX", "SPY", "QQQ", "NVDA", "TSLA", "META", "AAPL", "IWM"];

interface Candle {
  time: number;
  time_il: string;
  time_et: string;
  open: number;
  high: number;
  low: number;
  close: number;
}

interface Level {
  id: string;
  label: string;
  price: number;
  color: string;
  style: string;
  width: number;
  side: string;
  description: string;
}

interface Arrow {
  id: string;
  label: string;
  price: number;
  color: string;
  direction: "up" | "down";
  description: string;
}

interface DayInfo {
  date: string;
  open: number;
  high: number;
  low: number;
  change: number;
  change_pct: number;
  is_market_open: boolean;
  session_label: string;
  candle_count: number;
  interval: string;
  timeframe: string;
}

interface GEXLevelsResponse {
  ticker: string;
  spot: number;
  regime: string;
  candles: Candle[];
  levels: Level[];
  arrows: Arrow[];
  day_info?: DayInfo;
  timestamp: string;
}

async function fetchGEXLevels(ticker: string): Promise<GEXLevelsResponse> {
  const r = await fetch(`${API}/gex/levels/${ticker}`);
  if (!r.ok) throw new Error(`fetch failed: ${r.status}`);
  return r.json();
}

function OHLCBar({ info }: { info: DayInfo }) {
  const isUp = info.change >= 0;
  return (
    <div
      dir="ltr"
      className="scrollbar-none flex items-center gap-4 overflow-x-auto border-b border-slate-800 bg-slate-900 px-4 py-2 text-xs"
    >
      <span className="flex-none text-slate-500">{info.date}</span>
      <span className={`flex-none font-bold ${isUp ? "text-green-400" : "text-red-400"}`}>
        {isUp ? "▲" : "▼"} {Math.abs(info.change_pct).toFixed(2)}%
      </span>
      <span className="flex-none text-slate-500">
        פתיחה: <span className="text-slate-300">{info.open?.toLocaleString()}</span>
      </span>
      <span className="flex-none text-slate-500">
        גבוה: <span className="text-green-400">{info.high?.toLocaleString()}</span>
      </span>
      <span className="flex-none text-slate-500">
        נמוך: <span className="text-red-400">{info.low?.toLocaleString()}</span>
      </span>
      <span
        className={`flex-none rounded-full px-2 py-0.5 text-xs font-semibold ${
          info.is_market_open
            ? "bg-green-900 text-green-300"
            : "bg-slate-800 text-slate-400"
        }`}
      >
        ● {info.session_label}
      </span>
    </div>
  );
}

function CandleChart({
  candles,
  levels,
  arrows,
  spot,
}: {
  candles: Candle[];
  levels: Level[];
  arrows: Arrow[];
  spot: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverInfo, setHoverInfo] = useState<{
    x: number;
    y: number;
    candle?: Candle;
    level?: Level;
  } | null>(null);

  const HEIGHT = 480;
  const PAD_LEFT = 8;
  const PAD_RIGHT = 110;
  const PAD_TOP = 20;
  const PAD_BOT = 36;
  const MAX_DIST = 0.08; // ±8% of spot — anything farther is clipped from chart

  // Filter levels/arrows that are within MAX_DIST of spot. Both the bounds
  // calculation and the rendering loops must use the same filtered sets so the
  // hover tooltip's price-mapping stays in sync with what's actually drawn.
  const visibleLevels =
    spot > 0
      ? levels.filter((l) => Math.abs(l.price - spot) / spot <= MAX_DIST)
      : levels;
  const visibleArrows =
    spot > 0
      ? arrows.filter((a) => Math.abs(a.price - spot) / spot <= MAX_DIST)
      : arrows;

  const computeBounds = useCallback(() => {
    if (!candles.length) return { pMin: 0, pMax: 1 };
    const candlePrices = candles.flatMap((c) => [c.high, c.low]);
    const allVisiblePx = [
      ...candlePrices,
      ...visibleLevels.map((l) => l.price),
      ...visibleArrows.map((a) => a.price),
    ];
    const rawMin = Math.min(...allVisiblePx);
    const rawMax = Math.max(...allVisiblePx);
    // Floor the visible range at 1% of spot so a quiet session doesn't flatten the chart.
    const minRange = spot * 0.01;
    const finalRange = Math.max(rawMax - rawMin, minRange);
    const pad = finalRange * 0.15;
    return { pMin: rawMin - pad, pMax: rawMax + pad };
  }, [candles, visibleLevels, visibleArrows, spot]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) {
      console.warn("GEXChart: canvas or container missing");
      return;
    }
    if (!candles.length) {
      console.warn("GEXChart: no candles to draw");
      return;
    }
    console.log(
      `GEXChart: drawing ${candles.length} candles, spot=${spot}, ` +
        `first=${candles[0]?.close}, last=${candles[candles.length - 1]?.close}`,
    );

    const dpr = window.devicePixelRatio || 1;
    const W = container.clientWidth;
    const H = HEIGHT;

    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);

    const chartW = W - PAD_LEFT - PAD_RIGHT;
    const chartH = H - PAD_TOP - PAD_BOT;
    const { pMin, pMax } = computeBounds();
    const pRange = pMax - pMin || 1;

    const toY = (p: number) =>
      PAD_TOP + chartH - ((p - pMin) / pRange) * chartH;
    const toX = (i: number) =>
      PAD_LEFT + (i + 0.5) * (chartW / candles.length);

    // Background
    ctx.fillStyle = "#0b0e15";
    ctx.fillRect(0, 0, W, H);

    // Price grid + ladder
    const gridCount = 7;
    for (let i = 0; i <= gridCount; i++) {
      const p = pMin + (pRange / gridCount) * i;
      const y = toY(p);
      ctx.strokeStyle = "#1a1f2e";
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(PAD_LEFT, y);
      ctx.lineTo(W - PAD_RIGHT, y);
      ctx.stroke();
      ctx.fillStyle = "#374151";
      ctx.font = '10px "SF Mono", monospace';
      ctx.textAlign = "left";
      ctx.fillText(p.toFixed(0), W - PAD_RIGHT + 4, y + 4);
    }

    // Vertical time grid
    const timeStep = Math.max(1, Math.floor(candles.length / 8));
    for (let i = 0; i < candles.length; i += timeStep) {
      const x = toX(i);
      ctx.strokeStyle = "#1a1f2e";
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 4]);
      ctx.beginPath();
      ctx.moveTo(x, PAD_TOP);
      ctx.lineTo(x, H - PAD_BOT);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // GEX level lines + right-side pills (only the ones within ±8% of spot)
    visibleLevels.forEach((lv) => {
      const y = toY(lv.price);
      if (y < PAD_TOP - 2 || y > H - PAD_BOT + 2) return;

      ctx.save();
      ctx.shadowColor = lv.color;
      ctx.shadowBlur = 6;
      ctx.strokeStyle = lv.color;
      ctx.lineWidth = lv.width;
      ctx.globalAlpha = 0.9;
      if (lv.style === "dashed") ctx.setLineDash([10, 6]);
      ctx.beginPath();
      ctx.moveTo(PAD_LEFT, y);
      ctx.lineTo(W - PAD_RIGHT, y);
      ctx.stroke();
      ctx.restore();

      ctx.save();
      const lblW = 110;
      const lblH = 20;
      const lx = W - PAD_RIGHT + 2;
      const ly = y - lblH / 2;
      ctx.fillStyle = lv.color + "20";
      ctx.strokeStyle = lv.color + "aa";
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(lx, ly, lblW, lblH, 3);
      else ctx.rect(lx, ly, lblW, lblH);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = lv.color;
      ctx.font = 'bold 10px -apple-system, monospace';
      ctx.textAlign = "left";
      ctx.fillText(lv.label, lx + 5, ly + 13);
      ctx.restore();
    });

    // Entry arrows
    visibleArrows.forEach((ar) => {
      const y = toY(ar.price);
      if (y < PAD_TOP || y > H - PAD_BOT) return;

      ctx.save();
      ctx.strokeStyle = ar.color + "88";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      ctx.moveTo(PAD_LEFT + 60, y);
      ctx.lineTo(W - PAD_RIGHT, y);
      ctx.stroke();

      const ax = PAD_LEFT + 14;
      ctx.fillStyle = ar.color;
      ctx.setLineDash([]);
      ctx.beginPath();
      if (ar.direction === "up") {
        ctx.moveTo(ax - 7, y + 8);
        ctx.lineTo(ax, y - 2);
        ctx.lineTo(ax + 7, y + 8);
      } else {
        ctx.moveTo(ax - 7, y - 8);
        ctx.lineTo(ax, y + 2);
        ctx.lineTo(ax + 7, y - 8);
      }
      ctx.fill();

      const lblX = ax + 12;
      const tw = ctx.measureText(ar.label).width + 10;
      ctx.fillStyle = ar.color + "dd";
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(lblX, y - 9, tw, 17, 3);
      else ctx.rect(lblX, y - 9, tw, 17);
      ctx.fill();
      ctx.fillStyle = "#0b0e15";
      ctx.font = 'bold 9px "SF Mono", monospace';
      ctx.textAlign = "left";
      ctx.fillText(ar.label, lblX + 5, y + 4);
      ctx.restore();
    });

    // Spot line + price pill
    const sy = toY(spot);
    ctx.save();
    ctx.strokeStyle = "#60a5fa";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.globalAlpha = 0.7;
    ctx.beginPath();
    ctx.moveTo(PAD_LEFT, sy);
    ctx.lineTo(W - PAD_RIGHT, sy);
    ctx.stroke();
    ctx.globalAlpha = 1;

    ctx.fillStyle = "#1d4ed8";
    ctx.setLineDash([]);
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(W - PAD_RIGHT + 2, sy - 10, 104, 20, 3);
    else ctx.rect(W - PAD_RIGHT + 2, sy - 10, 104, 20);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.font = 'bold 11px "SF Mono", monospace';
    ctx.textAlign = "left";
    ctx.fillText(spot.toLocaleString(), W - PAD_RIGHT + 6, sy + 4);
    ctx.restore();

    // Area fill under close line (subtle TradingView-style gradient)
    const isUpDay = candles[candles.length - 1].close >= candles[0].open;
    const areaGrad = ctx.createLinearGradient(0, PAD_TOP, 0, H - PAD_BOT);
    if (isUpDay) {
      areaGrad.addColorStop(0, "#26a69a18");
      areaGrad.addColorStop(1, "#26a69a00");
    } else {
      areaGrad.addColorStop(0, "#ef535018");
      areaGrad.addColorStop(1, "#ef535000");
    }
    ctx.save();
    ctx.fillStyle = areaGrad;
    ctx.beginPath();
    ctx.moveTo(toX(0), toY(candles[0].close));
    candles.forEach((c, i) => ctx.lineTo(toX(i), toY(c.close)));
    ctx.lineTo(toX(candles.length - 1), H - PAD_BOT);
    ctx.lineTo(toX(0), H - PAD_BOT);
    ctx.closePath();
    ctx.fill();
    ctx.restore();

    // Candles
    const slotW = chartW / candles.length;
    const candleW = Math.max(3, Math.min(12, slotW * 0.75));
    candles.forEach((c, i) => {
      const x = toX(i);
      const oY = toY(c.open);
      const cY = toY(c.close);
      const hY = toY(c.high);
      const lY = toY(c.low);
      const isGreen = c.close >= c.open;
      const color = isGreen ? "#26a69a" : "#ef5350";
      const bodyTop = Math.min(oY, cY);
      const bodyH = Math.max(1.5, Math.abs(oY - cY));

      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(x, hY);
      ctx.lineTo(x, lY);
      ctx.stroke();
      ctx.fillRect(x - candleW / 2, bodyTop, candleW, bodyH);

      if (i === candles.length - 1) {
        ctx.save();
        ctx.shadowColor = color;
        ctx.shadowBlur = 8;
        ctx.fillRect(x - candleW / 2, bodyTop, candleW, bodyH);
        ctx.restore();
      }
    });

    // Time axis (Israel time)
    ctx.fillStyle = "#4b5563";
    ctx.font = '9px "SF Mono", monospace';
    ctx.textAlign = "center";
    ctx.setLineDash([]);
    for (let i = 0; i < candles.length; i += timeStep) {
      const x = toX(i);
      const label = candles[i].time_il || "";
      ctx.fillText(label, x, H - PAD_BOT + 18);
    }

    // Top-left meta info
    ctx.fillStyle = "#4b5563";
    ctx.font = "10px monospace";
    ctx.textAlign = "left";
    ctx.fillText(
      `${candles.length} נרות · 5m · שעון ישראל`,
      PAD_LEFT + 4,
      PAD_TOP - 6,
    );
  }, [candles, visibleLevels, visibleArrows, spot, computeBounds]);

  useEffect(() => {
    draw();
    const obs = new ResizeObserver(draw);
    if (containerRef.current) obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, [draw]);

  function handleMouseMove(e: ReactMouseEvent<HTMLCanvasElement>) {
    const canvas = canvasRef.current;
    if (!canvas || !candles.length) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    const chartW = rect.width - PAD_LEFT - PAD_RIGHT;
    const chartH = HEIGHT - PAD_TOP - PAD_BOT;

    const slotW = chartW / candles.length;
    const ci = Math.floor((mx - PAD_LEFT) / slotW);
    const candle = candles[ci] || undefined;

    const { pMin, pMax } = computeBounds();
    const pRange = pMax - pMin || 1;
    const hoverPrice = pMax - ((my - PAD_TOP) / chartH) * pRange;
    const nearby = visibleLevels.find(
      (l) => Math.abs(l.price - hoverPrice) < pRange * 0.007,
    );

    setHoverInfo({ x: mx, y: my, candle, level: nearby });
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full"
      style={{ height: HEIGHT }}
      dir="ltr"
    >
      <canvas
        ref={canvasRef}
        style={{ height: HEIGHT }}
        className="block w-full cursor-crosshair"
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setHoverInfo(null)}
      />

      {candles.length > 0 && spot > 0 && (
        <div className="pointer-events-none absolute top-2 left-2 text-xs text-slate-600">
          {candles.length} נרות
        </div>
      )}

      {hoverInfo?.candle && (
        <div
          className="pointer-events-none absolute z-20 min-w-36 rounded-lg border border-slate-700 bg-slate-900/95 px-3 py-2 text-xs shadow-xl"
          style={{
            left: Math.min(
              hoverInfo.x + 12,
              (containerRef.current?.clientWidth || 300) - 150,
            ),
            top: Math.max(hoverInfo.y - 60, 4),
          }}
        >
          <div className="mb-1 font-mono text-slate-400">
            {hoverInfo.candle.time_il}
          </div>
          <div className="grid grid-cols-2 gap-x-3 font-mono">
            <span className="text-slate-500">פתיחה</span>
            <span className="text-right text-white">
              {hoverInfo.candle.open.toLocaleString()}
            </span>
            <span className="text-slate-500">גבוה</span>
            <span className="text-right text-green-400">
              {hoverInfo.candle.high.toLocaleString()}
            </span>
            <span className="text-slate-500">נמוך</span>
            <span className="text-right text-red-400">
              {hoverInfo.candle.low.toLocaleString()}
            </span>
            <span className="text-slate-500">סגירה</span>
            <span
              className={`text-right font-bold ${
                hoverInfo.candle.close >= hoverInfo.candle.open
                  ? "text-green-400"
                  : "text-red-400"
              }`}
            >
              {hoverInfo.candle.close.toLocaleString()}
            </span>
          </div>
        </div>
      )}

      {hoverInfo?.level && !hoverInfo?.candle && (
        <div
          className="pointer-events-none absolute z-20 rounded-lg border px-3 py-2 text-xs shadow-xl"
          style={{
            left: Math.min(hoverInfo.x + 12, 250),
            top: hoverInfo.y - 16,
            backgroundColor: hoverInfo.level.color + "22",
            borderColor: hoverInfo.level.color + "88",
            color: hoverInfo.level.color,
          }}
        >
          {hoverInfo.level.description}
        </div>
      )}
    </div>
  );
}

function Legend({ levels, arrows }: { levels: Level[]; arrows: Arrow[] }) {
  return (
    <div
      dir="ltr"
      className="flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-800 bg-slate-900 px-4 py-2.5"
    >
      {levels.map((l) => (
        <div key={l.id} className="flex items-center gap-1.5">
          <div
            className="h-px w-5"
            style={{ backgroundColor: l.color, boxShadow: `0 0 4px ${l.color}` }}
          />
          <span className="text-xs" style={{ color: l.color + "cc" }}>
            {l.label}
          </span>
        </div>
      ))}
      {arrows.map((a) => (
        <div
          key={a.id}
          className="flex items-center gap-1 text-xs"
          style={{ color: a.color + "cc" }}
        >
          {a.direction === "up" ? "↑" : "↓"}
          {a.label}
        </div>
      ))}
    </div>
  );
}

function LevelList({ levels, spot }: { levels: Level[]; spot: number }) {
  const sorted = [...levels].sort((a, b) => b.price - a.price);
  return (
    <div className="space-y-2 px-4 pt-4 pb-2">
      <div className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">
        רמות GEX – 0DTE
      </div>
      {sorted.map((l) => {
        const dist = ((l.price - spot) / spot) * 100;
        const isAbove = l.price > spot;
        const isOffChart = spot > 0 && Math.abs(l.price - spot) / spot > 0.08;
        return (
          <div
            key={l.id}
            className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-900 px-4 py-3"
          >
            <div className="flex items-center gap-3">
              <div
                className="h-8 w-1 flex-none rounded-full"
                style={{ backgroundColor: l.color }}
              />
              <div>
                <div className="flex items-center gap-2">
                  <span
                    className="font-mono text-sm font-bold"
                    style={{ color: l.color }}
                  >
                    {l.label}
                  </span>
                  {isOffChart && (
                    <span className="rounded-full bg-slate-700 px-2 py-0.5 text-xs text-slate-400">
                      מחוץ לטווח
                    </span>
                  )}
                </div>
                <div className="text-xs text-slate-500">{l.description}</div>
              </div>
            </div>
            <div className="text-right">
              <div className="font-mono text-base font-bold text-white">
                {l.price.toLocaleString()}
              </div>
              <div
                className={`font-mono text-xs ${
                  isAbove ? "text-green-400" : "text-red-400"
                }`}
              >
                {isAbove ? "+" : ""}
                {dist.toFixed(2)}%
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function GEXChart() {
  const [ticker, setTicker] = useState("SPX");
  const [forceKey, setForceKey] = useState(0);

  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ["gex-levels", ticker, forceKey],
    queryFn: () => fetchGEXLevels(ticker),
    refetchInterval: 60 * 1000,
    retry: 3,
    retryDelay: 2000,
    staleTime: 0,
    gcTime: 5 * 60 * 1000,
  });

  const lastUpdate = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("he-IL", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        timeZone: "Asia/Jerusalem",
      })
    : null;

  const isPositive = data?.regime === "positive";

  return (
    <div className="min-h-screen select-none bg-slate-950 pb-24 text-white">
      <div className="flex items-center justify-between px-4 pt-5 pb-2">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight">GEX Chart</h1>
            <span className="rounded-full bg-blue-900 px-2 py-0.5 text-xs font-medium text-indigo-300">
              0DTE
            </span>
          </div>
          {data && (
            <div
              className={`mt-0.5 text-sm font-medium ${
                isPositive ? "text-green-400" : "text-red-400"
              }`}
            >
              {isPositive ? "🟢 Positive Gamma" : "🔴 Negative Gamma"}
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          {data && (
            <div className="text-right">
              <div className="font-mono text-xl font-bold text-white">
                {data.spot?.toLocaleString()}
              </div>
              {data.day_info && (
                <div
                  className={`font-mono text-sm ${
                    data.day_info.change >= 0 ? "text-green-400" : "text-red-400"
                  }`}
                >
                  {data.day_info.change >= 0 ? "+" : ""}
                  {data.day_info.change_pct.toFixed(2)}%
                </div>
              )}
            </div>
          )}
          <button
            onClick={() => {
              setForceKey((k) => k + 1);
              refetch();
            }}
            className="rounded-xl bg-slate-800 p-2.5 text-lg transition-colors active:bg-slate-700"
            aria-label="רענן"
          >
            🔄
          </button>
        </div>
      </div>

      {data?.day_info && <OHLCBar info={data.day_info} />}

      <div className="scrollbar-none flex gap-2 overflow-x-auto px-4 py-3">
        {TICKERS.map((t) => (
          <button
            key={t}
            onClick={() => setTicker(t)}
            className={`flex-none rounded-full px-4 py-1.5 text-sm font-semibold transition-all ${
              ticker === t ? "bg-indigo-600 text-white" : "bg-slate-800 text-slate-400"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div className="border-y border-slate-800 bg-slate-950">
        {isLoading && (
          <div
            style={{ height: 480 }}
            className="flex items-center justify-center text-slate-500"
          >
            <div className="text-center">
              <div className="mb-3 text-4xl">📊</div>
              <div className="text-sm">טוען גרף 0DTE – {ticker}</div>
              <div className="mt-1 text-xs text-slate-600">נרות יומיים 5 דקות</div>
            </div>
          </div>
        )}

        {isError && (
          <div
            style={{ height: 480 }}
            className="flex items-center justify-center text-red-400"
          >
            <div className="text-center">
              <div className="mb-2 text-3xl">⚠️</div>
              <div>שגיאה בטעינת נתונים</div>
              <button
                onClick={() => refetch()}
                className="mt-3 rounded-xl bg-slate-800 px-4 py-2 text-sm text-slate-300"
              >
                נסה שוב
              </button>
            </div>
          </div>
        )}

        {data && !isLoading && (
          <CandleChart
            candles={data.candles || []}
            levels={data.levels || []}
            arrows={data.arrows || []}
            spot={data.spot}
          />
        )}
      </div>

      {data && <Legend levels={data.levels || []} arrows={data.arrows || []} />}

      {lastUpdate && (
        <div className="py-2 text-center text-xs text-slate-600">
          עדכון אחרון: {lastUpdate} ● מתרענן כל דקה
        </div>
      )}

      {data && <LevelList levels={data.levels || []} spot={data.spot} />}
    </div>
  );
}
