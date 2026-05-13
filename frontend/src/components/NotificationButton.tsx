import { Bell } from "lucide-react";
import { usePushNotifications } from "../hooks/usePushNotifications";

export default function NotificationButton() {
  const { permission, requestPermission } = usePushNotifications();

  if (permission === "unsupported") {
    return null;
  }

  if (permission === "granted") {
    return (
      <div className="flex items-center gap-1 text-sm text-emerald-400">
        <Bell size={16} />
        <span className="hidden sm:inline">התראות פעילות</span>
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={requestPermission}
      className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
    >
      <Bell size={16} />
      <span className="hidden sm:inline">הפעל התראות</span>
    </button>
  );
}
