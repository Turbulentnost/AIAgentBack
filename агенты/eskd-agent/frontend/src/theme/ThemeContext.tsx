import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { isDarkTheme, isThemeId, type ThemeId } from "@/theme/themes";

const STORAGE_KEY = "ai-platform-theme";

type ThemeContextValue = {
  theme: ThemeId;
  isDark: boolean;
  toggleTheme: (origin?: { x: number; y: number }) => void;
  setTheme: (theme: ThemeId, origin?: { x: number; y: number }) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStoredTheme(): ThemeId {
  const stored = localStorage.getItem(STORAGE_KEY);
  return isThemeId(stored) ? stored : "light";
}

function applyThemeToDocument(theme: ThemeId, origin?: { x: number; y: number }) {
  if (origin) {
    document.documentElement.style.setProperty("--theme-origin-x", `${origin.x}px`);
    document.documentElement.style.setProperty("--theme-origin-y", `${origin.y}px`);
  }

  document.documentElement.dataset.theme = theme;
  localStorage.setItem(STORAGE_KEY, theme);
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(() => readStoredTheme());

  useEffect(() => {
    applyThemeToDocument(theme);
  }, []);

  const setTheme = useCallback((nextTheme: ThemeId, origin?: { x: number; y: number }) => {
    const update = () => {
      applyThemeToDocument(nextTheme, origin);
      setThemeState(nextTheme);
    };

    if (!document.startViewTransition) {
      update();
      return;
    }

    document.startViewTransition(() => {
      update();
    });
  }, []);

  const toggleTheme = useCallback(
    (origin?: { x: number; y: number }) => {
      setTheme(theme === "light" ? "dark" : "light", origin);
    },
    [setTheme, theme]
  );

  const value = useMemo(
    () => ({
      theme,
      isDark: isDarkTheme(theme),
      toggleTheme,
      setTheme
    }),
    [theme, toggleTheme, setTheme]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) {
    throw new Error("useTheme must be used within ThemeProvider");
  }
  return context;
}
