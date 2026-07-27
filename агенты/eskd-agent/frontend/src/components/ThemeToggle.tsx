import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/theme/ThemeContext";
import styles from "./ThemeToggle.module.css";

type ThemeToggleProps = {
  className?: string;
};

export default function ThemeToggle({ className }: ThemeToggleProps) {
  const { theme, isDark, toggleTheme } = useTheme();

  function handleClick(event: React.MouseEvent<HTMLButtonElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    toggleTheme({
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2
    });
  }

  return (
    <button
      className={`${styles.toggle} ${className ?? ""}`.trim()}
      type="button"
      aria-label={isDark ? "Включить светлую тему" : theme === "light" ? "Включить тёмную тему" : "Переключить на светлую или тёмную тему"}
      aria-pressed={isDark}
      onClick={handleClick}
    >
      <span className={styles.iconWrap} data-dark={isDark ? "true" : "false"}>
        <Sun className={`${styles.icon} ${styles.sun}`} size={18} strokeWidth={2} aria-hidden="true" />
        <Moon className={`${styles.icon} ${styles.moon}`} size={18} strokeWidth={2} aria-hidden="true" />
      </span>
      <span className={styles.srOnly}>
        {theme === "dark" ? "Тёмная тема" : theme === "light" ? "Светлая тема" : `Тема: ${theme}`}
      </span>
    </button>
  );
}
