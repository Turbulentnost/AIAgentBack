import { useMemo, useState, type KeyboardEvent, type MouseEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, FileText, Loader2, Search, Sparkles, Trash2, X } from "lucide-react";
import { deleteKnowledgeBaseEntry, fetchKnowledgeBase, verifyKnowledgeBaseEntry } from "@/api/knowledgeBase";
import type { KnowledgeBaseFilter, KnowledgeBaseItem } from "@/types/knowledgeBase";
import layout from "@/styles/pageLayout.module.css";
import styles from "./KnowledgeBasePage.module.css";

function formatDate(value: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleString("ru-RU");
}

function DeleteDialog({
  item,
  step,
  deleting,
  onClose,
  onContinue,
  onConfirm
}: {
  item: KnowledgeBaseItem;
  step: 1 | 2;
  deleting: boolean;
  onClose: () => void;
  onContinue: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className={styles.modalBackdrop} onClick={onClose}>
      <div
        className={styles.modal}
        role="dialog"
        aria-modal="true"
        aria-labelledby="kb-delete-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHead}>
          <h2 id="kb-delete-title">{step === 1 ? "Удалить запись?" : "Подтвердите удаление"}</h2>
          <button type="button" className={styles.modalClose} onClick={onClose} aria-label="Закрыть">
            <X size={18} />
          </button>
        </div>
        {step === 1 ? (
          <>
            <p>
              Запись <strong>{item.display_name}</strong> будет удалена из базы знаний вместе с проверками ИИ и
              разметкой.
            </p>
            <div className={styles.modalActions}>
              <button type="button" className="secondaryBtn" onClick={onClose}>
                Отмена
              </button>
              <button type="button" className="secondaryBtn" onClick={onContinue}>
                Продолжить
              </button>
            </div>
          </>
        ) : (
          <>
            <p className={styles.modalWarn}>
              Это необратимо. Будут удалены все связанные данные по файлу <strong>{item.display_name}</strong>.
            </p>
            <div className={styles.modalActions}>
              <button type="button" className="secondaryBtn" onClick={onClose} disabled={deleting}>
                Отмена
              </button>
              <button type="button" className={styles.deleteBtn} onClick={onConfirm} disabled={deleting}>
                {deleting ? <Loader2 size={16} className="spin" /> : <Trash2 size={16} />}
                Удалить навсегда
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Tile({
  item,
  onOpenMarking,
  onOpenMarkingFromCheck,
  onVerify,
  onDelete,
  verifying
}: {
  item: KnowledgeBaseItem;
  onOpenMarking?: (documentId: string) => void;
  onOpenMarkingFromCheck?: (checkRunId: string, filename: string) => void;
  onVerify?: (item: KnowledgeBaseItem) => void;
  onDelete?: (item: KnowledgeBaseItem) => void;
  verifying?: boolean;
}) {
  const canOpenCheck = Boolean(item.has_ai_check && item.last_check_run_id);
  const canOpenMarking = Boolean(item.marking_document_id);
  const canOpen = canOpenCheck || canOpenMarking;
  const canVerify = !item.checked && Boolean(item.last_check_run_id || item.marking_document_id);

  function openItem() {
    if (canOpenMarking && item.marking_document_id) {
      onOpenMarking?.(item.marking_document_id);
      return;
    }
    if (canOpenCheck && item.last_check_run_id) {
      onOpenMarkingFromCheck?.(item.last_check_run_id, item.display_name);
    }
  }

  function stopClick(e: MouseEvent | KeyboardEvent) {
    e.stopPropagation();
  }

  return (
    <article
      className={`${styles.tile} ${item.checked ? styles.tileChecked : styles.tileUnchecked} ${
        canOpen ? styles.tileClickable : styles.tileStatic
      }`}
      role={canOpen ? "button" : undefined}
      tabIndex={canOpen ? 0 : undefined}
      onClick={canOpen ? openItem : undefined}
      onKeyDown={
        canOpen
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                openItem();
              }
            }
          : undefined
      }
    >
      <div className={styles.tileHead}>
        <FileText size={18} />
        <div className={styles.tileHeadRight}>
          <span className={`statusPill ${item.checked ? "ok" : "warn"}`}>
            {item.checked ? (
              <>
                <CheckCircle2 size={12} /> Проверен
              </>
            ) : (
              <>
                <CircleDashed size={12} /> Не проверен
              </>
            )}
          </span>
          <button
            type="button"
            className={styles.deleteIconBtn}
            title="Удалить из базы знаний"
            aria-label={`Удалить ${item.display_name}`}
            onClick={(e) => {
              stopClick(e);
              onDelete?.(item);
            }}
          >
            <Trash2 size={15} />
          </button>
        </div>
      </div>
      <h3 className={styles.tileTitle} title={item.display_name}>
        {item.display_name}
      </h3>
      {item.has_ai_check && !item.checked && (
        <p className={styles.aiBadge}>
          <Sparkles size={12} /> Ответ ИИ — ждёт проверки человеком
        </p>
      )}
      {item.designation && <p className={styles.tileDesignation}>{item.designation}</p>}
      <dl className={styles.tileMeta}>
        <dt>Листов</dt>
        <dd>{item.pages_count ?? "—"}</dd>
        {item.has_marking && (
          <>
            <dt>Разметка</dt>
            <dd>{item.marked_pages_count} лист(ов)</dd>
            <dt>Ошибки / замеч.</dt>
            <dd>
              {item.marking_errors_count ?? 0} / {item.marking_warnings_count ?? 0}
            </dd>
            <dt>Дата разметки</dt>
            <dd>{formatDate(item.marking_updated_at)}</dd>
          </>
        )}
        {item.has_ai_check && (
          <>
            <dt>Проверка ИИ</dt>
            <dd>{formatDate(item.last_checked_at)}</dd>
            {!item.has_marking && (
              <>
                <dt>Ошибки / замеч.</dt>
                <dd>
                  {item.total_errors ?? 0} / {item.total_warnings ?? 0}
                </dd>
              </>
            )}
          </>
        )}
        {item.checked && item.human_verified_at && (
          <>
            <dt>Подтверждено</dt>
            <dd>{formatDate(item.human_verified_at)}</dd>
          </>
        )}
        {item.verifiers_count > 0 && (
          <>
            <dt>Проверили</dt>
            <dd
              className={styles.verifiersCell}
              title={item.verifiers.join("\n")}
              onClick={stopClick}
              onKeyDown={stopClick}
            >
              <span className={styles.verifiersValue}>{item.verifiers_count}</span>
              <span className={styles.verifiersTooltip} role="tooltip">
                {item.verifiers.map((name) => (
                  <span key={name}>{name}</span>
                ))}
              </span>
            </dd>
          </>
        )}
      </dl>
      {canVerify && (
        <button
          type="button"
          className={styles.verifyBtn}
          disabled={verifying}
          onClick={(e) => {
            stopClick(e);
            onVerify?.(item);
          }}
        >
          {verifying ? <Loader2 size={14} className="spin" /> : null}
          Подтвердить
        </button>
      )}
    </article>
  );
}

export default function KnowledgeBasePage({
  onOpenMarking,
  onOpenMarkingFromCheck
}: {
  onOpenMarking?: (documentId: string) => void;
  onOpenMarkingFromCheck?: (checkRunId: string, filename: string) => void;
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<KnowledgeBaseFilter>("all");
  const [page, setPage] = useState(1);
  const [verifyingKey, setVerifyingKey] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<KnowledgeBaseItem | null>(null);
  const [deleteStep, setDeleteStep] = useState<1 | 2 | null>(null);

  const list = useQuery({
    queryKey: ["knowledge-base", query, filter, page],
    queryFn: () => fetchKnowledgeBase({ q: query, filter, page, size: 24 })
  });

  const verify = useMutation({
    mutationFn: verifyKnowledgeBaseEntry,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge-base"] });
      void queryClient.invalidateQueries({ queryKey: ["marking-stats"] });
    }
  });

  const remove = useMutation({
    mutationFn: deleteKnowledgeBaseEntry,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge-base"] });
      void queryClient.invalidateQueries({ queryKey: ["marking-stats"] });
      setDeleteTarget(null);
      setDeleteStep(null);
    }
  });

  const summary = useMemo(() => {
    if (!list.data) return null;
    return {
      total: list.data.total,
      checked: list.data.checked_count,
      unchecked: list.data.unchecked_count
    };
  }, [list.data]);

  const totalPages = list.data ? Math.max(1, Math.ceil(list.data.total / list.data.size)) : 1;

  async function handleVerify(item: KnowledgeBaseItem) {
    setVerifyingKey(item.key);
    try {
      await verify.mutateAsync({
        checkRunId: item.last_check_run_id,
        markingDocumentId: item.marking_document_id
      });
    } finally {
      setVerifyingKey(null);
    }
  }

  function startDelete(item: KnowledgeBaseItem) {
    setDeleteTarget(item);
    setDeleteStep(1);
  }

  function closeDeleteDialog() {
    if (remove.isPending) return;
    setDeleteTarget(null);
    setDeleteStep(null);
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    await remove.mutateAsync(deleteTarget.key);
  }

  return (
    <section className={layout.page}>
      <header className={layout.header}>
        <div className={layout.headerMain}>
          <h1>База знаний</h1>
          <p>
            После проверки ИИ файлы попадают сюда как непроверенные (жёлтые). Человек подтверждает или
            размечает — тогда статус «Проверен».
          </p>
        </div>
        {summary && (
          <div className={layout.headerAside}>
            <div className={styles.summary}>
              <span>Всего: {summary.total}</span>
              <span className={styles.summaryOk}>Проверено: {summary.checked}</span>
              <span className={styles.summaryPending}>Не проверено: {summary.unchecked}</span>
            </div>
          </div>
        )}
      </header>

      <section className={`card ${styles.toolbar}`}>
        <label className={styles.search}>
          <Search size={16} />
          <input
            placeholder="Поиск по имени файла или обозначению…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <div className={styles.filters}>
          {(
            [
              ["all", "Все"],
              ["checked", "Проверенные"],
              ["unchecked", "Не проверенные"]
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`${styles.filterBtn} ${filter === id ? styles.filterBtnActive : ""}`}
              onClick={() => {
                setFilter(id);
                setPage(1);
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </section>

      {list.isLoading ? (
        <div className={styles.loading}>
          <Loader2 size={20} className="spin" /> Загрузка…
        </div>
      ) : (
        <>
          <div className={styles.grid}>
            {(list.data?.items ?? []).map((item) => (
              <Tile
                key={item.key}
                item={item}
                onOpenMarking={onOpenMarking}
                onOpenMarkingFromCheck={onOpenMarkingFromCheck}
                onVerify={(entry) => void handleVerify(entry)}
                onDelete={startDelete}
                verifying={verifyingKey === item.key && verify.isPending}
              />
            ))}
          </div>
          {!list.data?.items.length && (
            <div className={`card ${styles.empty}`}>
              {query.trim() ? "Ничего не найдено по запросу" : "База знаний пока пуста — загрузите файл на проверку или в разметку"}
            </div>
          )}
          {totalPages > 1 && (
            <div className={styles.pagination}>
              <button
                type="button"
                className="secondaryBtn"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                Назад
              </button>
              <span>
                {page} / {totalPages}
              </span>
              <button
                type="button"
                className="secondaryBtn"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Вперёд
              </button>
            </div>
          )}
        </>
      )}

      {deleteTarget && deleteStep && (
        <DeleteDialog
          item={deleteTarget}
          step={deleteStep}
          deleting={remove.isPending}
          onClose={closeDeleteDialog}
          onContinue={() => setDeleteStep(2)}
          onConfirm={() => void confirmDelete()}
        />
      )}
    </section>
  );
}
