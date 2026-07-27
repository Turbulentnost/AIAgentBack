export type AppTab = "check" | "history" | "marking" | "stats" | "knowledge" | "integration";

export const APP_TABS: { id: AppTab; label: string }[] = [
  { id: "check", label: "Проверка" },
  { id: "history", label: "История" },
  { id: "marking", label: "Разметка" },
  { id: "knowledge", label: "База знаний" },
  { id: "stats", label: "Статистика" },
  { id: "integration", label: "Интеграции" }
];
