import { Bot, Send, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAgent } from "../../hooks/useAgent";

const QUICK_PROMPTS = [
  { label: "⚡ רמות GEX של SPX", value: "מה רמות ה-GEX של SPX להיום?" },
  { label: "🧲 תמיכות Delta", value: "מה התמיכות וההתנגדויות של Delta עכשיו?" },
  { label: "🔍 סרוק שוק", value: "סרוק את השוק ומצא הזדמנויות" },
  { label: "💼 הפוזיציות שלי", value: "הצג את הפוזיציות הפתוחות שלי" },
  { label: "📋 סיכום יום", value: "תן לי סיכום של יום המסחר" },
];

function renderInline(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, idx) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={idx} className="font-semibold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <span key={idx}>{part}</span>;
  });
}

function MessageContent({ content }: { content: string }) {
  const lines = useMemo(() => content.split(/\n/), [content]);
  return (
    <div className="space-y-1">
      {lines.map((line, idx) => (
        <div key={idx}>{line ? renderInline(line) : <br />}</div>
      ))}
    </div>
  );
}

export default function ChatBox() {
  const { messages, send, pending } = useAgent();
  const [input, setInput] = useState("");
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  const handleSubmit = (text?: string) => {
    const payload = text ?? input;
    if (!payload.trim() || pending) return;
    send(payload);
    if (!text) setInput("");
  };

  return (
    <div className="card flex h-full flex-col overflow-hidden">
      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-4 lg:px-5">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-cyan-400 shadow-lg shadow-indigo-500/30">
              <Bot className="h-7 w-7 text-white" />
            </div>
            <div>
              <div className="font-bold text-slate-100">שלום חיים 👋</div>
              <div className="mt-1 max-w-xs text-sm text-slate-500">
                שאל אותי על GEX, רמות Delta, פוזיציות או חדשות — או בחר פעולה
                מהירה למטה
              </div>
            </div>
          </div>
        )}
        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={`flex items-end gap-2 ${
              msg.role === "user" ? "justify-start" : "justify-end"
            }`}
          >
            {msg.role !== "user" && (
              <div className="order-2 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-cyan-400">
                <Bot className="h-3.5 w-3.5 text-white" />
              </div>
            )}
            <div
              className={`max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed lg:max-w-[70%] lg:px-4 ${
                msg.role === "user"
                  ? "rounded-bl-md bg-gradient-to-br from-indigo-600 to-indigo-500 text-white shadow-md shadow-indigo-600/25"
                  : "order-1 rounded-br-md border border-slate-800/70 bg-slate-900/80 text-slate-100"
              }`}
            >
              <MessageContent content={msg.content} />
            </div>
          </div>
        ))}
        {pending && (
          <div className="flex items-end justify-end gap-2">
            <div className="order-2 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-cyan-400">
              <Bot className="h-3.5 w-3.5 text-white" />
            </div>
            <div className="order-1 rounded-2xl rounded-br-md border border-slate-800/70 bg-slate-900/80 px-4 py-3">
              <span className="inline-flex gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-400 [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-400 [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-indigo-400" />
              </span>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="border-t border-slate-800/60 bg-slate-950/40 px-3 py-3 lg:px-4">
        <div className="scrollbar-hide mb-2.5 flex gap-2 overflow-x-auto pb-1">
          {QUICK_PROMPTS.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => handleSubmit(p.value)}
              disabled={pending}
              className="shrink-0 whitespace-nowrap rounded-full border border-slate-700/60 bg-slate-800/60 px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:border-indigo-400/40 hover:bg-slate-700/60 hover:text-white disabled:opacity-50"
            >
              {p.label}
            </button>
          ))}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSubmit();
          }}
          className="flex items-center gap-2"
        >
          <div className="relative flex-1">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="שאל את הסוכן..."
              className="w-full rounded-xl border border-slate-700/70 bg-slate-950/80 py-2.5 pl-9 pr-3.5 text-sm text-white placeholder:text-slate-600 transition-colors focus:border-indigo-400/60 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
              disabled={pending}
            />
            <Sparkles className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-600" />
          </div>
          <button
            type="submit"
            disabled={pending || !input.trim()}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-br from-indigo-600 to-indigo-500 px-4 py-2.5 text-sm font-semibold text-white shadow-md shadow-indigo-600/25 transition-all hover:brightness-110 disabled:opacity-40 disabled:shadow-none"
            aria-label="שלח"
          >
            <Send className="h-4 w-4" />
            <span className="hidden lg:inline">שלח</span>
          </button>
        </form>
      </div>
    </div>
  );
}
