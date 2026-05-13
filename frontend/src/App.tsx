import { Route, Routes } from "react-router-dom";
import BottomNav from "./components/layout/BottomNav";
import Header from "./components/layout/Header";
import Sidebar from "./components/layout/Sidebar";
import Chat from "./pages/Chat";
import Dashboard from "./pages/Dashboard";
import Journal from "./pages/Journal";
import Positions from "./pages/Positions";
import Scanner from "./pages/Scanner";

export default function App() {
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
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/positions" element={<Positions />} />
            <Route path="/journal" element={<Journal />} />
            <Route path="/scanner" element={<Scanner />} />
            <Route path="/chat" element={<Chat />} />
          </Routes>
        </main>
      </div>

      {/* Bottom nav – mobile only */}
      <BottomNav />
    </div>
  );
}
