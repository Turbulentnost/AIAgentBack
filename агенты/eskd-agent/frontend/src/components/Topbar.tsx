import { useState } from "react";
import { Bell, Search } from "lucide-react";
import OtkUserSelector from "@/components/OtkUserSelector";
import ThemeToggle from "@/components/ThemeToggle";

interface TopbarProps {
  title: string;
}

export default function Topbar({ title }: TopbarProps) {
  const [notificationCount] = useState(0);

  return (
    <header className="topbar">
      <a className="header-brand" href="/" aria-label="ESKD Agent">
        <img src="/platform-logo.png" alt="" width={28} height={28} />
        <span>ESKD Agent</span>
      </a>

      <div className="topbar-actions">
        <label className="header-search" aria-label={`Поиск на странице ${title}`}>
          <Search aria-hidden="true" size={17} strokeWidth={2.2} />
          <input type="search" placeholder="Поиск..." />
          <kbd>⌘K</kbd>
        </label>

        <button
          className="notification-button"
          type="button"
          aria-label={`Уведомления: ${notificationCount}`}
        >
          <Bell aria-hidden="true" size={22} strokeWidth={1.9} />
          {notificationCount > 0 ? <span className="notification-badge">{notificationCount}</span> : null}
        </button>

        <ThemeToggle />
        <OtkUserSelector />
      </div>
    </header>
  );
}
