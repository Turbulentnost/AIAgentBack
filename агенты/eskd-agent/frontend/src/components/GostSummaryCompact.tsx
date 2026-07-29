import type { GostCatalogItem } from "@/types/history";
import type { GostSummaryData } from "@/components/GostSummaryForm";
import styles from "./GostSummaryCompact.module.css";

interface Props {
  catalog: GostCatalogItem[];
  summary: GostSummaryData;
  title?: string;
}

function severityForKey(summary: GostSummaryData, key: string): "ok" | "warn" | "err" {
  if (summary.errors[key]?.length) return "err";
  if (summary.warnings[key]?.length) return "warn";
  if (summary.passed.includes(key)) return "ok";
  return "ok";
}

function pagesHint(summary: GostSummaryData, key: string): string {
  const err = summary.errors[key];
  const warn = summary.warnings[key];
  if (err?.length) return `ошибки: стр. ${err.join(", ")}`;
  if (warn?.length) return `замеч.: стр. ${warn.join(", ")}`;
  return "без нарушений";
}

export default function GostSummaryCompact({ catalog, summary, title }: Props) {
  return (
    <div className={styles.wrap}>
      {title ? <div className={styles.title}>{title}</div> : null}
      <div className={styles.grid}>
        {catalog.map((item) => {
          const severity = severityForKey(summary, item.key);
          return (
            <div
              key={item.key}
              className={`${styles.chip} ${styles[severity]}`}
              title={`${item.title}\n${pagesHint(summary, item.key)}`}
            >
              <span className={styles.key}>{item.key}</span>
            </div>
          );
        })}
      </div>
      <div className={styles.legend}>
        <span className={styles.ok}>OK</span>
        <span className={styles.warn}>замеч.</span>
        <span className={styles.err}>ошибки</span>
      </div>
    </div>
  );
}
