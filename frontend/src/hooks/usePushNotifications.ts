import { useEffect, useState } from "react";

type PermissionState = NotificationPermission | "unsupported";

function initialPermission(): PermissionState {
  if (typeof Notification === "undefined") return "unsupported";
  return Notification.permission;
}

export function usePushNotifications() {
  const [permission, setPermission] = useState<PermissionState>(initialPermission());
  const [subscription, setSubscription] = useState<PushSubscription | null>(null);

  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker
      .register("/sw.js")
      .then((reg) => {
        console.log("SW registered");
        return reg.pushManager.getSubscription();
      })
      .then((sub) => {
        if (sub) setSubscription(sub);
      })
      .catch((err) => console.warn("SW registration failed", err));
  }, []);

  const requestPermission = async (): Promise<PermissionState> => {
    if (typeof Notification === "undefined") return "unsupported";
    const result = await Notification.requestPermission();
    setPermission(result);

    if (result === "granted") {
      new Notification("Options Agent 🤖", {
        body: "התראות פעילות! תקבל עדכונים על הזדמנויות מסחר",
        icon: "/icon-192.png",
      });
    }
    return result;
  };

  const sendLocalNotification = (title: string, body: string, url?: string) => {
    if (permission !== "granted") return;
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    navigator.serviceWorker.ready.then((reg) => {
      reg.showNotification(title, {
        body,
        icon: "/icon-192.png",
        data: { url: url || "/" },
      } as NotificationOptions);
    });
  };

  return { permission, requestPermission, sendLocalNotification, subscription };
}
