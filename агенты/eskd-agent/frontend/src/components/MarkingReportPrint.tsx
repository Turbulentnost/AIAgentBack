import type { GostCatalogItem } from "@/types/history";
import {
  buildMarkingReportData,
  checkStatusLabel,
  formatReportDate,
  markingStatusLabel,
  severityLabel,
  type MarkingReportData
} from "@/utils/markingReport";
import type { CheckRunDetail } from "@/types/history";
import type { KnowledgeBaseItem } from "@/types/knowledgeBase";
import type { MarkingDocument, MarkingLabel, PageLevelFinding } from "@/types/marking";
import styles from "./MarkingReportPrint.module.css";

interface Props {
  document: MarkingDocument;
  catalog: GostCatalogItem[];
  pageFindings: PageLevelFinding[];
  problemReport: string;
  labelId: string | null;
  draftCheckRunId: string | null;
  savedLabel: MarkingLabel | null | undefined;
  checkRun: CheckRunDetail | null | undefined;
  kbEntry: KnowledgeBaseItem | null | undefined;
}

function GostSummaryTable({
  catalog,
  summary,
  title
}: {
  catalog: GostCatalogItem[];
  summary: MarkingReportData["humanGostSummary"];
  title: string;
}) {
  return (
    <>
      <h3>{title}</h3>
      <table className={styles.gostTable}>
        <thead>
          <tr>
            <th>ГОСТ</th>
            <th>Наименование</th>
            <th>Результат</th>
            <th>Листы</th>
          </tr>
        </thead>
        <tbody>
          {catalog.map((item) => {
            const errPages = summary.errors[item.key];
            const warnPages = summary.warnings[item.key];
            let resultClass = styles.ok;
            let resultText = "соответствует";
            let pages = "—";
            if (errPages?.length) {
              resultClass = styles.err;
              resultText = "не соответствует";
              pages = errPages.join(", ");
            } else if (warnPages?.length) {
              resultClass = styles.warn;
              resultText = "замечание";
              pages = warnPages.join(", ");
            }
            return (
              <tr key={item.key}>
                <td>{item.key}</td>
                <td>{item.title}</td>
                <td className={resultClass}>{resultText}</td>
                <td>{pages}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

export default function MarkingReportPrint(props: Props) {
  const report = buildMarkingReportData(props);

  return (
    <div className={styles.printRoot} aria-hidden="true">
      <h1>Отчёт по разметке документа</h1>
      <p className={styles.muted}>Сформирован: {formatReportDate(new Date().toISOString())}</p>

      <h2>Документ</h2>
      <table className={styles.metaTable}>
        <tbody>
          <tr>
            <th>Наименование файла</th>
            <td>{report.filename}</td>
          </tr>
          <tr>
            <th>Обозначение</th>
            <td>{report.designation || "—"}</td>
          </tr>
          <tr>
            <th>Загружен</th>
            <td>{report.uploadedAt}</td>
          </tr>
          <tr>
            <th>Кто загрузил</th>
            <td>{report.uploadedBy || "—"}</td>
          </tr>
          <tr>
            <th>Листов в документе</th>
            <td>{report.pagesCount}</td>
          </tr>
        </tbody>
      </table>

      <h2>Перечень проверок</h2>
      <ul className={styles.checksList}>
        {report.aiCheck ? (
          <li>
            <strong>Автоматическая проверка (ИИ)</strong>
            {" — "}
            {report.aiCheck.date}, исполнитель: {report.aiCheck.executor || "ИИ-система"},{" "}
            статус: {checkStatusLabel(report.aiCheck.status)}, ошибок: {report.aiCheck.totalErrors},
            замечаний: {report.aiCheck.totalWarnings}
          </li>
        ) : (
          <li>
            <strong>Автоматическая проверка (ИИ)</strong> — не выполнялась или данные недоступны
          </li>
        )}
        <li>
          <strong>Экспертная разметка</strong>
          {" — "}
          {markingStatusLabel(report.markingStatus)}
          {report.labelSavedAt ? `, сохранена ${report.labelSavedAt}` : ""}
          {", "}
          размечено листов: {report.markedPagesCount}
        </li>
        {report.kb ? (
          <li>
            <strong>База знаний</strong>
            {" — "}
            {report.kb.checked ? "подтверждено" : "не подтверждено"}
            {report.kb.verifiedAt ? ` (${formatReportDate(report.kb.verifiedAt)})` : ""}
            {report.kb.verifiers.length ? `, проверяющие: ${report.kb.verifiers.join(", ")}` : ""}
            {report.kb.checkCount > 0 ? `, проверок в базе: ${report.kb.checkCount}` : ""}
          </li>
        ) : (
          <li>
            <strong>База знаний</strong> — запись не найдена
          </li>
        )}
      </ul>

      <h2>Соответствие требованиям</h2>

      {report.aiCheck?.gostSummary && (
        <GostSummaryTable
          catalog={props.catalog}
          summary={report.aiCheck.gostSummary}
          title="Результаты автоматической проверки (ИИ)"
        />
      )}

      <GostSummaryTable
        catalog={props.catalog}
        summary={report.humanGostSummary}
        title="Результаты экспертной разметки"
      />

      {report.humanViolations.length > 0 && (
        <>
          <h3>Детализация нарушений (разметка)</h3>
          <table className={styles.gostTable}>
            <thead>
              <tr>
                <th>ГОСТ</th>
                <th>Наименование</th>
                <th>Степень</th>
                <th>Листы</th>
                <th>Примечание</th>
              </tr>
            </thead>
            <tbody>
              {report.humanViolations.map((row) => (
                <tr key={row.gost_key}>
                  <td>{row.gost_key}</td>
                  <td>{row.title}</td>
                  <td className={row.severity === "error" ? styles.err : styles.warn}>
                    {severityLabel(row.severity)}
                  </td>
                  <td>{row.pages.join(", ")}</td>
                  <td>{row.note || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {report.humanPassedCount > 0 && !report.humanViolations.length && (
        <p className={styles.ok}>
          По экспертной разметке все {report.humanPassedCount} проверяемых ГОСТ соответствуют требованиям.
        </p>
      )}

      {report.problemReport && (
        <>
          <h3>Общий отчёт по документу</h3>
          <div className={styles.problemBlock}>{report.problemReport}</div>
        </>
      )}

      {!report.aiCheck?.gostSummary && !report.humanViolations.length && !report.problemReport && (
        <p className={styles.muted}>
          Нарушений не зафиксировано. Сводка по {props.catalog.length} ГОСТ — все пункты «соответствует».
        </p>
      )}

      <div className={styles.footer}>
        Документ: {report.filename}
        {report.designation ? ` · ${report.designation}` : ""}
      </div>
    </div>
  );
}
