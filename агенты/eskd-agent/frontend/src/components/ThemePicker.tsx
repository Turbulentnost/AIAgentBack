import { Check } from "lucide-react";
import { useTheme } from "@/theme/ThemeContext";
import { THEME_DEFINITIONS, type ThemeId } from "@/theme/themes";
import styles from "./ThemePicker.module.css";

type ThemePickerProps = {
  className?: string;
};

export default function ThemePicker({ className }: ThemePickerProps) {
  const { theme, setTheme } = useTheme();

  function handleSelect(nextTheme: ThemeId, event: React.MouseEvent<HTMLButtonElement>) {
    if (nextTheme === theme) return;
    const rect = event.currentTarget.getBoundingClientRect();
    setTheme(nextTheme, {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2
    });
  }

  const defaultThemes = THEME_DEFINITIONS.filter((item) => item.group === "default");
  const creativeThemes = THEME_DEFINITIONS.filter((item) => item.group === "creative");

  return (
    <div className={`${styles.root} ${className ?? ""}`.trim()}>
      <section className={styles.group} aria-labelledby="theme-default-heading">
        <h3 id="theme-default-heading" className={styles.groupTitle}>
          Базовые
        </h3>
        <div className={styles.grid}>
          {defaultThemes.map((item) => (
            <ThemeCard key={item.id} item={item} active={theme === item.id} onSelect={handleSelect} />
          ))}
        </div>
      </section>

      <section className={styles.group} aria-labelledby="theme-creative-heading">
        <h3 id="theme-creative-heading" className={styles.groupTitle}>
          Креативные темы
        </h3>
        <p className={styles.groupHint}>10 авторских палитр с разной стилистикой и атмосферой</p>
        <div className={styles.grid}>
          {creativeThemes.map((item) => (
            <ThemeCard key={item.id} item={item} active={theme === item.id} onSelect={handleSelect} />
          ))}
        </div>
      </section>
    </div>
  );
}

function ThemeCard({
  item,
  active,
  onSelect
}: {
  item: (typeof THEME_DEFINITIONS)[number];
  active: boolean;
  onSelect: (theme: ThemeId, event: React.MouseEvent<HTMLButtonElement>) => void;
}) {
  return (
    <button
      type="button"
      className={`${styles.card} ${active ? styles.cardActive : ""}`.trim()}
      aria-pressed={active}
      aria-label={`Тема ${item.label}`}
      onClick={(event) => onSelect(item.id, event)}
    >
      <span className={styles.preview} style={{ background: item.preview.bg }} aria-hidden="true">
        <span className={styles.previewSurface} style={{ background: item.preview.surface }} />
        <span className={styles.previewPrimary} style={{ background: item.preview.primary }} />
        <span className={styles.previewAccent} style={{ background: item.preview.accent }} />
      </span>
      <span className={styles.copy}>
        <strong className={styles.label}>{item.label}</strong>
        <span className={styles.description}>{item.description}</span>
      </span>
      {active ? (
        <span className={styles.check} aria-hidden="true">
          <Check size={14} strokeWidth={2.5} />
        </span>
      ) : null}
    </button>
  );
}
