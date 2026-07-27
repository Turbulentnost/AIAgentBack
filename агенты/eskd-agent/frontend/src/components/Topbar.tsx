import { ShieldCheck } from "lucide-react";
import OtkUserSelector from "@/components/OtkUserSelector";
import ThemeToggle from "@/components/ThemeToggle";
import type { AppTab } from "@/components/Layout";

const TABS: { id: AppTab; label: string }[] = [
  { id: "check", label: "Проверка" },
  { id: "history", label: "История" },
  { id: "marking", label: "Разметка" },
  { id: "knowledge", label: "База знаний" },
  { id: "stats", label: "Статистика" },
  { id: "integration", label: "Интеграции" }
];

interface TopbarProps {
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
}

export default function Topbar({ activeTab, onTabChange }: TopbarProps) {
  return (
    <header className="topbar">
      <a className="header-brand" href="/" aria-label="ESKD Agent">
        <ShieldCheck size={26} strokeWidth={2.2} aria-hidden="true" />
        <span>ESKD Agent</span>
      </a>

      <nav className="header-nav" aria-label="Разделы">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`header-nav-link ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      <div className="topbar-actions">
        <ThemeToggle />
        <OtkUserSelector />
      </div>
    </header>
  );
}
