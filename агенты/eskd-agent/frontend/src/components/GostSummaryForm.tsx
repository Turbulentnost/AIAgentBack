import type { GostCatalogItem } from "@/types/history";
import type { GostFinding, GostSeverity } from "@/types/marking";
import styles from "./GostSummaryForm.module.css";

export interface GostSummaryData {
  passed: string[];
  warnings: Record<string, number[]>;
  errors: Record<string, number[]>;
}

interface ReadonlyProps {
  mode: "readonly";
  catalog: GostCatalogItem[];
  summary: GostSummaryData;
}

interface EditableProps {
  mode: "editable";
  catalog: GostCatalogItem[];
  findings: GostFinding[];
  onChange: (findings: GostFinding[]) => void;
  /** Если задан — разметка только для этого листа, поле «страницы» скрыто */
  fixedPage?: number;
}

type Props = ReadonlyProps | EditableProps;

function titleFor(catalog: GostCatalogItem[], key: string) {
  return catalog.find((c) => c.key === key)?.title ?? key;
}

function pagesText(pages: number[]) {
  return pages.length ? pages.join(", ") : "";
}

export default function GostSummaryForm(props: Props) {
  if (props.mode === "readonly") {
    const { catalog, summary } = props;
    const warningKeys = Object.keys(summary.warnings);
    const errorKeys = Object.keys(summary.errors);

    return (
      <div className={styles.wrap}>
        <section className={styles.group}>
          <h4 className={styles.groupOk}>Пройденные ГОСТ</h4>
          <ul className={styles.list}>
            {summary.passed.map((key) => (
              <li key={key}>{titleFor(catalog, key)}</li>
            ))}
            {!summary.passed.length && <li className={styles.empty}>—</li>}
          </ul>
        </section>
        <section className={styles.group}>
          <h4 className={styles.groupWarn}>С замечаниями</h4>
          <ul className={styles.list}>
            {warningKeys.map((key) => (
              <li key={key}>
                <strong>{titleFor(catalog, key)}</strong>
                {summary.warnings[key]?.length ? (
                  <span className={styles.pages}>стр. {pagesText(summary.warnings[key])}</span>
                ) : null}
              </li>
            ))}
            {!warningKeys.length && <li className={styles.empty}>—</li>}
          </ul>
        </section>
        <section className={styles.group}>
          <h4 className={styles.groupErr}>С ошибками</h4>
          <ul className={styles.list}>
            {errorKeys.map((key) => (
              <li key={key}>
                <strong>{titleFor(catalog, key)}</strong>
                {summary.errors[key]?.length ? (
                  <span className={styles.pages}>стр. {pagesText(summary.errors[key])}</span>
                ) : null}
              </li>
            ))}
            {!errorKeys.length && <li className={styles.empty}>—</li>}
          </ul>
        </section>
      </div>
    );
  }

  const { catalog, findings, onChange, fixedPage } = props;

  function updateFinding(key: string, patch: Partial<GostFinding>) {
    const next = [...findings];
    const idx = next.findIndex((f) => f.gost_key === key);
    const base: GostFinding = idx >= 0 ? next[idx] : { gost_key: key, severity: "ok", pages: [], note: "" };
    const merged = { ...base, ...patch };
    if (idx >= 0) next[idx] = merged;
    else next.push(merged);
    onChange(next);
  }

  function parsePages(raw: string): number[] {
    return raw
      .split(/[,;\s]+/)
      .map((p) => parseInt(p.trim(), 10))
      .filter((n) => Number.isFinite(n) && n > 0);
  }

  return (
    <div className={styles.editWrap}>
      {catalog.map((item) => {
        const finding = findings.find((f) => f.gost_key === item.key) ?? {
          gost_key: item.key,
          severity: "ok" as GostSeverity,
          pages: [],
          note: ""
        };
        const violated = finding.severity !== "ok";
        return (
          <div key={item.key} className={styles.editRow}>
            <label className={styles.editHead}>
              <input
                type="checkbox"
                checked={violated}
                onChange={(e) =>
                  updateFinding(item.key, {
                    severity: e.target.checked ? "error" : "ok",
                    pages: e.target.checked ? (fixedPage ? [fixedPage] : finding.pages) : []
                  })
                }
              />
              <span>{item.title}</span>
            </label>
            {violated && (
              <div className={styles.editFields}>
                <select
                  value={finding.severity === "warning" ? "warning" : "error"}
                  onChange={(e) =>
                    updateFinding(item.key, {
                      severity: e.target.value as GostSeverity,
                      pages: fixedPage ? [fixedPage] : finding.pages
                    })
                  }
                >
                  <option value="error">Ошибка</option>
                  <option value="warning">Замечание</option>
                </select>
                {!fixedPage && (
                  <input
                    placeholder="Страницы: 1,3,5"
                    value={pagesText(finding.pages)}
                    onChange={(e) => updateFinding(item.key, { pages: parsePages(e.target.value) })}
                  />
                )}
                <input
                  placeholder="Краткое описание"
                  value={finding.note}
                  onChange={(e) =>
                    updateFinding(item.key, {
                      note: e.target.value,
                      pages: fixedPage ? [fixedPage] : finding.pages
                    })
                  }
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
