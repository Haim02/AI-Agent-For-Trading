import { Send } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAgent } from "../../hooks/useAgent";

const QUICK_PROMPTS = [
  { label: "🔍 סרוק שוק", value: "סרוק את השוק ומצא הזדמנויות" },
  { label: "📊 מצב GEX", value: "מה מצב ה-GEX של SPY עכשיו?" },
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
    <div className="flex h-full flex-col rounded-xl border border-slate-800 bg-slate-900/70 shadow-lg">
      <div className="flex-1 space-y-3 overflow-y-auto px-3 py-3 lg:px-4 lg:py-4">
        {messages.length === 0 && (
          <div className="text-center text-sm text-slate-400">
            שלח שאלה לסוכן, או השתמש בפעולה מהירה למטה.
          </div>
        )}
        {messages.map((msg, idx) => (
          <div
            key={idx}
            className={`flex ${msg.role === "user" ? "justify-start" : "justify-end"}`}
          >
            <div
              className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm leading-relaxed shadow lg:max-w-[70%] lg:px-4 ${
                msg.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-slate-800 text-slate-100"
              }`}
            >
              <MessageContent content={msg.content} />
            </div>
          </div>
        ))}
        {pending && (
          <div className="flex justify-end">
            <div className="rounded-2xl bg-slate-800 px-4 py-2 text-sm text-slate-300">
              <span className="inline-flex gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-slate-400" />
              </span>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="border-t border-slate-800 px-3 py-3 lg:px-4">
        <div className="scrollbar-hide mb-2 flex gap-2 overflow-x-auto pb-2">
          {QUICK_PROMPTS.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => handleSubmit(p.value)}
              disabled={pending}
              className="shrink-0 whitespace-nowrap rounded-full bg-slate-800 px-3 py-1 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50"
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
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="שאלה לסוכן…"
            className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
            disabled={pending}
          />
          <button
            type="submit"
            disabled={pending || !input.trim()}
            className="inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm text-white hover:bg-blue-500 disabled:opacity-50"
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
