import type { ReactNode } from "react";
import AppSubheader from "@/components/AppSubheader";
import Topbar from "@/components/Topbar";
import type { AppTab } from "@/navigation";

interface LayoutProps {
  activeTab: AppTab;
  onTabChange: (tab: AppTab) => void;
  children: ReactNode;
}

export default function Layout({ activeTab, onTabChange, children }: LayoutProps) {
  return (
    <div className="shell">
      <Topbar title="ESKD Agent" />
      <AppSubheader activeTab={activeTab} onTabChange={onTabChange} />
      <main>
        <section className="content">{children}</section>
      </main>
    </div>
  );
}
