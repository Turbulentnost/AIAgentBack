import type { AppTab } from "@/navigation";
import { APP_TABS } from "@/navigation";

interface AppSubheaderProps {
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
}

export default function AppSubheader({ activeTab, onTabChange }: AppSubheaderProps) {
  return (
    <div className="app-subheader">
      <nav className="section-nav-box" aria-label="Разделы ESKD Agent">
        {APP_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`section-nav-link ${activeTab === tab.id ? "active" : ""}`}
            onClick={() => onTabChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
