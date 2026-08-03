export const DEFAULT_THEMES = ["light", "dark"] as const;

export const CUSTOM_THEME_IDS = [
  "cyberpunk",
  "matrix",
  "glassmorphism",
  "retro",
  "sketch",
  "ocean",
  "sunset",
  "nord",
  "sakura",
  "amethyst"
] as const;

export const THEME_IDS = [...DEFAULT_THEMES, ...CUSTOM_THEME_IDS] as const;

export type ThemeId = (typeof THEME_IDS)[number];

export type ThemeDefinition = {
  id: ThemeId;
  label: string;
  description: string;
  group: "default" | "creative";
  colorScheme: "light" | "dark";
  preview: {
    bg: string;
    surface: string;
    primary: string;
    accent: string;
  };
};

export const THEME_DEFINITIONS: ThemeDefinition[] = [
  {
    id: "light",
    label: "Светлая",
    description: "Стандартная светлая тема платформы",
    group: "default",
    colorScheme: "light",
    preview: { bg: "#f5f7fb", surface: "#ffffff", primary: "#2563eb", accent: "#4f46e5" }
  },
  {
    id: "dark",
    label: "Тёмная",
    description: "Стандартная тёмная тема (default)",
    group: "default",
    colorScheme: "dark",
    preview: { bg: "#0d1117", surface: "#161b22", primary: "#58a6ff", accent: "#6e6ef5" }
  },
  {
    id: "cyberpunk",
    label: "Cyberpunk",
    description: "Неон, cyan и magenta на глубоком чёрном",
    group: "creative",
    colorScheme: "dark",
    preview: { bg: "#07070f", surface: "#12121f", primary: "#00f0ff", accent: "#ff2a6d" }
  },
  {
    id: "matrix",
    label: "Matrix",
    description: "Терминальный зелёный фосфор",
    group: "creative",
    colorScheme: "dark",
    preview: { bg: "#020804", surface: "#071108", primary: "#00ff41", accent: "#00cc33" }
  },
  {
    id: "glassmorphism",
    label: "Glassmorphism",
    description: "Матовое стекло и мягкие градиенты",
    group: "creative",
    colorScheme: "light",
    preview: { bg: "#e8eef8", surface: "rgba(255,255,255,0.55)", primary: "#6366f1", accent: "#06b6d4" }
  },
  {
    id: "retro",
    label: "Retro 80s",
    description: "Тёплый крем, оранжевый и фуксия",
    group: "creative",
    colorScheme: "light",
    preview: { bg: "#fff4e6", surface: "#fffaf2", primary: "#e85d04", accent: "#ff006e" }
  },
  {
    id: "sketch",
    label: "Sketch",
    description: "Бумага, карандаш и грубые тени",
    group: "creative",
    colorScheme: "light",
    preview: { bg: "#f7f3ea", surface: "#fffdf8", primary: "#2f2a25", accent: "#dc2626" }
  },
  {
    id: "ocean",
    label: "Ocean",
    description: "Глубокий морской teal и cyan",
    group: "creative",
    colorScheme: "dark",
    preview: { bg: "#03141f", surface: "#082636", primary: "#22d3ee", accent: "#0891b2" }
  },
  {
    id: "sunset",
    label: "Sunset",
    description: "Закатные розово-оранжевые акценты",
    group: "creative",
    colorScheme: "dark",
    preview: { bg: "#1a0f1e", surface: "#261428", primary: "#fb7185", accent: "#f97316" }
  },
  {
    id: "nord",
    label: "Nord",
    description: "Холодная арктическая палитра",
    group: "creative",
    colorScheme: "light",
    preview: { bg: "#eceff4", surface: "#e5e9f0", primary: "#5e81ac", accent: "#88c0d0" }
  },
  {
    id: "sakura",
    label: "Sakura",
    description: "Нежная вишня и розовые акценты",
    group: "creative",
    colorScheme: "light",
    preview: { bg: "#fff5f8", surface: "#fffafc", primary: "#ec4899", accent: "#f472b6" }
  },
  {
    id: "amethyst",
    label: "Amethyst",
    description: "Фиолетовая ночь и luxury-glow",
    group: "creative",
    colorScheme: "dark",
    preview: { bg: "#110818", surface: "#1a0f24", primary: "#a855f7", accent: "#e879f9" }
  }
];

export const THEME_BY_ID = Object.fromEntries(THEME_DEFINITIONS.map((theme) => [theme.id, theme])) as Record<
  ThemeId,
  ThemeDefinition
>;

export function isThemeId(value: string | null | undefined): value is ThemeId {
  return Boolean(value && THEME_IDS.includes(value as ThemeId));
}

export function isDarkTheme(themeId: ThemeId) {
  return THEME_BY_ID[themeId].colorScheme === "dark";
}
