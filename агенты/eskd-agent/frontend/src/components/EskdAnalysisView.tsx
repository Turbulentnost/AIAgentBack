import { AlertTriangle, CheckCircle2, ClipboardList, Info, Loader2 } from "lucide-react";
import type { EskdItemReport, EskdPosition, EskdRemark } from "@/types/eskd";
import type { LiveCheck, LiveEskdPayload } from "@/utils/parseLiveJson";
import { filterUserFacingRemarks } from "@/utils/internalValidation";
import { filterLlmMetaText } from "@/utils/llmMetaFilter";
import styles from "./EskdAnalysisView.module.css";

type AnalysisData = LiveEskdPayload & {
  elements?: EskdItemReport["elements"];
};

function statusTone(summary?: string, errors?: unknown[], warnings?: unknown[]) {
  const errN = errors?.length ?? 0;
  const warnN = warnings?.length ?? 0;
  if (errN > 0) return "err" as const;
  if (warnN > 0) return "warn" as const;
  if (summary?.toLowerCase().includes("не выявлено")) return "ok" as const;
  return "ok" as const;
}

function CheckRow({ check }: { check: LiveCheck }) {
  const ok = check.status === "ok" || check.status === "pass";
  return (
    <div className={`${styles.checkRow} ${ok ? styles.checkOk : styles.checkBad}`}>
      <div className={styles.checkMain}>
        <span className={styles.checkElement}>{check.element || "элемент"}</span>
        {check.zone && <span className={styles.checkZone}>{check.zone}</span>}
      </div>
      {check.value && <div className={styles.checkValue}>{check.value}</div>}
      {check.message && <div className={styles.checkMessage}>{check.message}</div>}
      {check.gost_reference && (
        <div className={styles.checkGost}>{check.gost_reference}</div>
      )}
      <span className={`${styles.badge} ${ok ? styles.badgeOk : styles.badgeBad}`}>
        {ok ? "OK" : check.status || "?"}
      </span>
    </div>
  );
}

function PositionsTable({ positions, orderOk }: { positions: EskdPosition[]; orderOk?: boolean }) {
  if (!positions.length) return null;
  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        <ClipboardList size={16} />
        <h4>Спецификация</h4>
        {orderOk === false ? (
          <span className={`${styles.badge} ${styles.badgeBad}`}>порядок нарушен</span>
        ) : (
          <span className={`${styles.badge} ${styles.badgeOk}`}>порядок OK</span>
        )}
      </div>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Поз.</th>
              <th>Наименование</th>
              <th>Кол.</th>
              <th>Обозначение</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p, i) => (
              <tr key={`${p.pos}-${i}`} className={p.order_ok === false ? styles.rowBad : undefined}>
                <td>{String(p.pos)}</td>
                <td>{p.title || "—"}</td>
                <td>{p.quantity || "—"}</td>
                <td>{p.designation || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Remarks({
  items,
  kind
}: {
  items: EskdRemark[] | Array<{ code?: string; message?: string }>;
  kind: "error" | "warning";
}) {
  if (!items.length) return null;
  return (
    <div className={styles.section}>
      <div className={styles.sectionHead}>
        {kind === "error" ? <AlertTriangle size={16} /> : <Info size={16} />}
        <h4>{kind === "error" ? "Ошибки" : "Предупреждения"}</h4>
      </div>
      <div className={styles.remarkList}>
        {items.map((r, i) => (
          <div key={i} className={kind === "error" ? styles.remarkError : styles.remarkWarning}>
            {"code" in r && r.code && <code>{r.code}</code>}
            <span>{r.message || "—"}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function EskdAnalysisView({
  data,
  streaming = false
}: {
  data: AnalysisData;
  streaming?: boolean;
}) {
  const checks = data.checks ?? [];
  const positions = (data.positions ?? []) as EskdPosition[];
  const errors = filterUserFacingRemarks(data.errors ?? []);
  const warnings = filterUserFacingRemarks(data.warnings ?? []);
  const summary = filterLlmMetaText(data.summary);
  const tone = statusTone(summary, errors, warnings);

  const SummaryIcon =
    tone === "err" ? AlertTriangle : tone === "warn" ? Info : CheckCircle2;

  return (
    <div className={styles.root}>
      {(summary || streaming) && (
        <div className={`${styles.summaryBanner} ${styles[`tone_${tone}`]}`}>
          <SummaryIcon size={20} className={styles.summaryIcon} />
          <div>
            <div className={styles.summaryTitle}>
              {summary || (streaming ? "Формирование ответа…" : "Результат")}
            </div>
            {streaming && !summary && (
              <div className={styles.summaryHint}>Модель анализирует чертёж</div>
            )}
          </div>
          {streaming && <Loader2 size={18} className={`${styles.spin} ${styles.summarySpinner}`} />}
        </div>
      )}

      {checks.length > 0 && (
        <div className={styles.section}>
          <div className={styles.sectionHead}>
            <CheckCircle2 size={16} />
            <h4>Проверки штампа и реквизитов</h4>
            <span className={styles.count}>{checks.length}</span>
          </div>
          <div className={styles.checkGrid}>
            {checks.map((c, i) => (
              <CheckRow key={`${c.element}-${i}`} check={c} />
            ))}
          </div>
        </div>
      )}

      <PositionsTable positions={positions} orderOk={data.positions_order_ok} />

      <Remarks items={errors as EskdRemark[]} kind="error" />
      <Remarks items={warnings as EskdRemark[]} kind="warning" />
    </div>
  );
}

export function itemToAnalysisData(item: EskdItemReport): AnalysisData {
  return {
    summary: item.summary,
    positions: item.positions,
    positions_order_ok: item.positions_order_ok,
    errors: item.errors,
    warnings: item.warnings,
    checks: item.elements?.map((el) => ({
      element: el.name,
      zone: el.zone,
      status: el.ok === false ? "error" : "ok",
      value: el.value,
      gost_reference: el.gost_reference ?? undefined,
      message: el.note || undefined
    }))
  };
}
