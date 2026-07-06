import ChatBox from "../components/chat/ChatBox";

export default function Chat() {
  return (
    <div className="flex h-[calc(100vh-136px)] flex-col lg:h-[calc(100vh-88px)]">
      <div className="flex-1 min-h-0">
        <ChatBox />
      </div>
    </div>
  );
}
