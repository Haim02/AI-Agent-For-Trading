import { useEffect } from "react";
import { Route, Routes } from "react-router-dom";
import { ErrorBoundary } from "./components/ErrorBoundary";
import BottomNav from "./components/layout/BottomNav";
import Header from "./components/layout/Header";
import Sidebar from "./components/layout/Sidebar";
import { usePushNotifications } from "./hooks/usePushNotifications";
import Analytics from "./pages/Analytics";
import Chat from "./pages/Chat";
import Checklist from "./pages/Checklist";
import Dashboard from "./pages/Dashboard";
import GEXChart from "./pages/GEXChart";
import Journal from "./pages/Journal";
import Narrative from "./pages/Narrative";
import Positions from "./pages/Positions";
import Scanner from "./pages/Scanner";
import Signals from "./pages/Signals";

export default function App() {
  const { supported, permission, requestPermission } = usePushNotifications();

  useEffect(() => {
    if (supported && permission === "default") {
      const t = setTimeout(() => {
        void requestPermission();
      }, 3000);
      return () => clearTimeout(t);
    }
  }, [supported, permission, requestPermission]);

  return (
    <div dir="rtl" className="flex h-screen bg-slate-900 text-white">
      {/* Sidebar – desktop only */}
      <div className="hidden lg:block">
        <Sidebar />
      </div>

      {/* Main content */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-auto p-3 pb-20 lg:p-6 lg:pb-6">
          <ErrorBoundary>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/positions" element={<Positions />} />
            <Route path="/journal" element={<Journal />} />
            <Route path="/scanner" element={<Scanner />} />
            <Route path="/chart" element={<GEXChart />} />
            <Route path="/checklist" element={<Checklist />} />
            <Route path="/narrative" element={<Narrative />} />
            <Route path="/signals" element={<Signals />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/analytics" element={<Analytics />} />
          </Routes>
          </ErrorBoundary>
        </main>
      </div>

      {/* Bottom nav – mobile only */}
      <BottomNav />
    </div>
  );
}
