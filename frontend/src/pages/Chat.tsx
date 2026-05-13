import ChatBox from "../components/chat/ChatBox";

export default function Chat() {
  return (
    <div className="flex h-[calc(100vh-136px)] flex-col gap-3 lg:h-[calc(100vh-64px)]">
      <h1 className="text-lg font-semibold lg:text-xl">שיחה עם הסוכן 🤖</h1>
      <div className="flex-1 min-h-0">
        <ChatBox />
      </div>
    </div>
  );
}
