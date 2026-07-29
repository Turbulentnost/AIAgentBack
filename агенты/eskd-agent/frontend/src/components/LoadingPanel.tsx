import styles from "./LoadingPanel.module.css";

interface LoadingPanelProps {
  title: string;
  subtitle?: string;
}

export default function LoadingPanel({ title, subtitle }: LoadingPanelProps) {
  return (
    <div className={styles.panel} role="status" aria-live="polite">
      <span className={styles.spinner} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
    </div>
  );
}
