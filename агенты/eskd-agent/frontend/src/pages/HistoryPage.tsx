import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import {
  fetchCheckHistory,
  fetchCheckRunChanges,
  fetchCheckRunDetail,
  fetchCheckRunVersions,
  fetchGostCatalog
} from "@/api/history";
import GostSummaryForm from "@/components/GostSummaryForm";
import GostSummaryCompact from "@/components/GostSummaryCompact";
import { EskdAnalysisView, itemToAnalysisData } from "@/components/EskdAnalysisView";
import type { EskdItemReport } from "@/types/eskd";
import type { CheckRunListItem } from "@/types/history";
import layout from "@/styles/pageLayout.module.css";
import styles from "./HistoryPage.module.css";

function formatDate(value: string) {
  return new Date(value).toLocaleString("ru-RU");
}

function statusLabel(status: string) {
  if (status === "running") return "В процессе";
  if (status === "cancelled") return "Отменено";
  if (status === "done" || status === "completed") return "Готово";
  return status;
}

function isRunning(status: string) {
  return status === "running";
}

export default function HistoryPage() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filename, setFilename] = useState("");
  const [designation, setDesignation] = useState("");

  const list = useQuery({
    queryKey: ["history", filename, designation],
    queryFn: () =>
      fetchCheckHistory({
        page: 1,
        size: 50,
        filename: filename.trim() || undefined,
        designation: designation.trim() || undefined
      }),
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((row) => isRunning(row.status)) ? 3000 : false;
    }
  });

  const catalog = useQuery({
    queryKey: ["gost-catalog"],
    queryFn: fetchGostCatalog
  });

  const detail = useQuery({
    queryKey: ["history-detail", selectedId],
    queryFn: () => fetchCheckRunDetail(selectedId!),
    enabled: Boolean(selectedId),
    refetchInterval: (query) => (query.state.data && isRunning(query.state.data.status) ? 3000 : false)
  });

  const versions = useQuery({
    queryKey: ["history-versions", selectedId],
    queryFn: () => fetchCheckRunVersions(selectedId!),
    enabled: Boolean(selectedId) && !isRunning(detail.data?.status ?? "")
  });

  const changes = useQuery({
    queryKey: ["history-changes", selectedId],
    queryFn: () => fetchCheckRunChanges(selectedId!),
    enabled: Boolean(selectedId) && !isRunning(detail.data?.status ?? "")
  });

  const detailItems = useMemo(() => {
    const raw = detail.data?.raw_result?.items;
    if (!Array.isArray(raw)) return [] as EskdItemReport[];
    return raw.filter((row): row is EskdItemReport => Boolean(row && typeof row === "object"));
  }, [detail.data?.raw_result]);

  function selectRow(row: CheckRunListItem) {
    setSelectedId(row.id);
  }

  return (
    <section className={layout.page}>
      <header className={layout.header}>
        <div className={layout.headerMain}>
          <h1>История проверок</h1>
          <p>
            Завершённые и текущие проверки. Формат сводки по 8 ГОСТ — как в разметке и базе знаний.
          </p>
        </div>
      </header>

      <div className={styles.filters}>
        <input
          placeholder="Фильтр по имени файла"
          value={filename}
          onChange={(e) => setFilename(e.target.value)}
        />
        <input
          placeholder="Фильтр по обозначению"
          value={designation}
          onChange={(e) => setDesignation(e.target.value)}
        />
      </div>

      <div className={styles.grid}>
        <section className={`card ${styles.listCard}`}>
          {list.isLoading ? (
            <div className={styles.loading}>
              <Loader2 size={18} className="spin" /> Загрузка…
            </div>
          ) : (
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Дата</th>
                  <th>Статус</th>
                  <th>Файл</th>
                  <th>ГОСТ</th>
                  <th>Ошибки</th>
                </tr>
              </thead>
              <tbody>
                {(list.data?.items ?? []).map((row) => (
                  <tr
                    key={row.id}
                    className={[
                      row.id === selectedId ? styles.rowActive : "",
                      isRunning(row.status) ? styles.rowRunning : ""
                    ]
                      .filter(Boolean)
                      .join(" ")}
                    onClick={() => selectRow(row)}
                  >
                    <td>{formatDate(row.created_at)}</td>
                    <td>
                      <span
                        className={`${styles.statusBadge} ${
                          isRunning(row.status) ? styles.statusRunning : styles.statusDone
                        }`}
                      >
                        {isRunning(row.status) && <Loader2 size={12} className="spin" />}
                        {statusLabel(row.status)}
                        {isRunning(row.status) && row.processed_pages != null && row.pages_count
                          ? ` ${row.processed_pages}/${row.pages_count}`
                          : null}
                      </span>
                    </td>
                    <td>
                      <div className={styles.fileCell}>
                        <span>{row.original_filename || "—"}</span>
                        {isRunning(row.status) && typeof row.progress_percent === "number" ? (
                          <div className={styles.miniProgress}>
                            <div
                              className={styles.miniProgressFill}
                              style={{ width: `${Math.min(row.progress_percent, 100)}%` }}
                            />
                          </div>
                        ) : null}
                      </div>
                    </td>
                    <td className={styles.gostCell}>
                      {row.gost_summary && catalog.data ? (
                        <GostSummaryCompact catalog={catalog.data} summary={row.gost_summary} />
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>{row.total_errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!list.isLoading && !list.data?.items.length && (
            <p className={styles.empty}>Проверок пока нет. Запустите анализ на вкладке «Проверка».</p>
          )}
        </section>

        <aside className={`card ${styles.detailCard}`}>
          {!selectedId && <p className={styles.empty}>Выберите проверку из списка</p>}
          {selectedId && detail.isLoading && (
            <div className={styles.loading}>
              <Loader2 size={18} className="spin" />
            </div>
          )}
          {detail.data && (
            <>
              <h2>{detail.data.original_filename || "Документ"}</h2>
              <dl className={styles.meta}>
                <dt>Обозначение</dt>
                <dd>{detail.data.designation || "—"}</dd>
                <dt>Статус</dt>
                <dd>{statusLabel(detail.data.status)}</dd>
                <dt>Дата</dt>
                <dd>{formatDate(detail.data.created_at)}</dd>
                <dt>Листов</dt>
                <dd>
                  {isRunning(detail.data.status)
                    ? `${detailItems.length}/${detail.data.pages_count ?? "?"} проверено`
                    : (detail.data.pages_count ?? "—")}
                </dd>
                <dt>Job ID</dt>
                <dd>{detail.data.job_id}</dd>
              </dl>

              {isRunning(detail.data.status) && typeof detail.data.progress_percent === "number" && (
                <div className={styles.progressBlock}>
                  <div className={styles.progressBar}>
                    <div
                      className={styles.progressFill}
                      style={{ width: `${Math.min(detail.data.progress_percent, 100)}%` }}
                    />
                  </div>
                  <div className={styles.progressMeta}>
                    Обработано листов: {detailItems.length}
                    {detail.data.pages_count ? ` / ${detail.data.pages_count}` : ""}
                    {" · "}
                    {Math.round(detail.data.progress_percent)}%
                  </div>
                </div>
              )}

              {detail.data.gost_summary && catalog.data && (
                <>
                  <h3 className={styles.sectionTitle}>Сводка по 8 ГОСТ</h3>
                  <GostSummaryForm
                    mode="readonly"
                    catalog={catalog.data}
                    summary={detail.data.gost_summary}
                  />
                </>
              )}

              {detailItems.length > 0 && (
                <div className={styles.pagesBlock}>
                  <h3 className={styles.sectionTitle}>
                    {isRunning(detail.data.status) ? "Проверенные листы" : "Листы"}
                  </h3>
                  {detailItems.map((item, idx) => (
                    <details key={`${item.source}-${idx}`} className={styles.pageCard} open={idx === detailItems.length - 1}>
                      <summary>
                        {item.source || `Лист ${item.page}`}
                        <span className={styles.pageMeta}>
                          {item.errors_count ? `ошибок ${item.errors_count}` : "без ошибок"}
                          {item.warnings_count ? ` · замеч. ${item.warnings_count}` : ""}
                        </span>
                      </summary>
                      <EskdAnalysisView data={itemToAnalysisData(item)} />
                    </details>
                  ))}
                </div>
              )}

              {!isRunning(detail.data.status) && (versions.data?.length ?? 0) > 1 && (
                <div className={styles.versionBlock}>
                  <h3>Версии комплекта</h3>
                  <ul className={styles.versionList}>
                    {versions.data?.map((item) => (
                      <li key={item.id}>
                        <button type="button" onClick={() => setSelectedId(item.id)}>
                          v{item.version_no} · {formatDate(item.created_at)} · {item.created_by_name || "—"} ·
                          ошибок {item.total_errors}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {!isRunning(detail.data.status) && (changes.data?.length ?? 0) > 0 && (
                <div className={styles.changesBlock}>
                  <h3>Журнал изменений</h3>
                  <ul className={styles.changesList}>
                    {changes.data?.map((item) => (
                      <li key={item.id}>
                        <div className={styles.changeHead}>
                          <span className={styles.changeType}>{item.change_type}</span>
                          <span>{formatDate(item.created_at)}</span>
                        </div>
                        <div>{item.summary}</div>
                        <div className={styles.changeActor}>
                          {item.changed_by_name || item.changed_by_login || "система"}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
