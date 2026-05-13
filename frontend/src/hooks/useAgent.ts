import { useCallback, useEffect, useRef, useState } from "react";
import { chatWithAgent } from "../api/client";
import type { ChatMessage } from "../types";

function makeSessionId() {
  return `web:${Date.now()}:${Math.random().toString(36).slice(2, 8)}`;
}

export function useAgent() {
  const sessionRef = useRef<string>(makeSessionId());
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(async (content: string) => {
    const trimmed = content.trim();
    if (!trimmed) return;
    setError(null);
    setPending(true);

    const userMessage: ChatMessage = {
      role: "user",
      content: trimmed,
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);

    try {
      const result = await chatWithAgent(trimmed, sessionRef.current);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.response,
          timestamp: new Date().toISOString(),
        },
      ]);
    } catch (exc) {
      setError((exc as Error).message);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: `⚠️ שגיאה: ${(exc as Error).message}`,
          timestamp: new Date().toISOString(),
        },
      ]);
    } finally {
      setPending(false);
    }
  }, []);

  useEffect(() => {
    sessionRef.current = makeSessionId();
  }, []);

  return {
    messages,
    send,
    pending,
    error,
    sessionId: sessionRef.current,
  };
}
