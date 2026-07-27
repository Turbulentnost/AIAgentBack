import type { ReactNode } from "react";
import { ShieldCheck } from "lucide-react";
import OtkUserSelector from "@/components/OtkUserSelector";
import styles from "./Layout.module.css";

export type AppTab = "check" | "history" | "marking" | "stats" | "knowledge" | "integration";

const TABS: { id: AppTab; label: string }[] = [
  { id: "check", label: "Проверка" },
  { id: "history", label: "История" },
  { id: "marking", label: "Разметка" },
  { id: "knowledge", label: "База знаний" },
  { id: "stats", label: "Статистика" },
  { id: "integration", label: "Интеграции" }
];

interface LayoutProps {
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
  children: ReactNode;
}

export default function Layout({ activeTab, onTabChange, children }: LayoutProps) {
  return (
    <div className="shell">
      <header className="topbar">
        <a className="header-brand" href="/">
          <ShieldCheck size={22} strokeWidth={2.2} />
          <span>ESKD Agent</span>
        </a>
        <nav className={styles.tabs} aria-label="Разделы">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`${styles.tab} ${activeTab === tab.id ? styles.tabActive : ""}`}
              onClick={() => onTabChange(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
        <OtkUserSelector />
      </header>
      <main>
        <section className="content">{children}</section>
      </main>
    </div>
  );
}
