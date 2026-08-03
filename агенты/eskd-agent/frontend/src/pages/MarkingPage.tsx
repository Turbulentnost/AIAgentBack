import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Printer, Save, Upload } from "lucide-react";
import { fetchCheckRunDetail, fetchGostCatalog } from "@/api/history";
import { fetchKnowledgeBase, verifyKnowledgeBaseEntry } from "@/api/knowledgeBase";
import layout from "@/styles/pageLayout.module.css";
import type { MarkingOpenIntent } from "@/types/markingOpen";
import {
  createMarkingLabel,
  fetchMarkingDocument,
  fetchLatestMarkingLabel,
  fetchSuggestedMarkingLabel,
  lookupMarkingDocumentByFilename,
  markingPreviewUrl,
  openMarkingFromCheckRun,
  updateMarkingLabel,
  uploadMarkingDocument
} from "@/api/marking";
import type { MarkingDocumentLookup } from "@/types/marking";
import DocumentPreview from "@/components/DocumentPreview";
import GostSummaryForm from "@/components/GostSummaryForm";
import MarkingReportPrint from "@/components/MarkingReportPrint";
import { filterLlmMetaText } from "@/utils/llmMetaFilter";
import type { GostFinding, MarkingDocument, PageLevelFinding } from "@/types/marking";
import styles from "./MarkingPage.module.css";

function emptyFindings(catalog: { key: string }[]): GostFinding[] {
  return catalog.map((c) => ({ gost_key: c.key, severity: "ok", pages: [], note: "" }));
}

function buildDocumentLevelPerPage(pageFindings: PageLevelFinding[]): GostFinding[] {
  return pageFindings.flatMap((entry) =>
    entry.gost_findings
      .filter((f) => f.severity !== "ok")
      .map((f) => ({
        ...f,
        pages: [entry.page]
      }))
  );
}

export default function MarkingPage({
  openIntent = null,
  onOpenIntentHandled
}: {
  openIntent?: MarkingOpenIntent | null;
  onOpenIntentHandled?: () => void;
}) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [designation, setDesignation] = useState("");
  const [document, setDocument] = useState<MarkingDocument | null>(null);
  const [labelId, setLabelId] = useState<string | null>(null);
  const [draftCheckRunId, setDraftCheckRunId] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [pageFindings, setPageFindings] = useState<PageLevelFinding[]>([]);
  const [problemReport, setProblemReport] = useState("");
  const [uploading, setUploading] = useState(false);
  const [loadingDoc, setLoadingDoc] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [existingLookup, setExistingLookup] = useState<MarkingDocumentLookup | null>(null);
  const [forceNewUpload, setForceNewUpload] = useState(false);
  const [pendingCheckRun, setPendingCheckRun] = useState<{ checkRunId: string; filename: string } | null>(
    null
  );

  const catalog = useQuery({
    queryKey: ["gost-catalog"],
    queryFn: fetchGostCatalog
  });

  const kbEntry = useQuery({
    queryKey: ["knowledge-base-entry", document?.id],
    queryFn: async () => {
      if (!document) return null;
      const data = await fetchKnowledgeBase({ q: document.source_filename, size: 50 });
      return data.items.find((item) => item.marking_document_id === document.id) ?? null;
    },
    enabled: Boolean(document?.id)
  });

  const savedLabel = useQuery({
    queryKey: ["marking-label-report", labelId, document?.id],
    queryFn: () => fetchLatestMarkingLabel(document!.id),
    enabled: Boolean(document?.id && labelId)
  });

  const reportCheckRunId =
    draftCheckRunId ?? savedLabel.data?.check_run_id ?? kbEntry.data?.last_check_run_id ?? null;

  const reportCheckRun = useQuery({
    queryKey: ["marking-report-check-run", reportCheckRunId],
    queryFn: () => fetchCheckRunDetail(reportCheckRunId!),
    enabled: Boolean(reportCheckRunId)
  });

  function handlePrintReport() {
    window.print();
  }

  const verify = useMutation({
    mutationFn: verifyKnowledgeBaseEntry,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["knowledge-base"] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-base-entry"] });
      void queryClient.invalidateQueries({ queryKey: ["marking-stats"] });
      setMessage("Запись подтверждена в базе знаний");
    },
    onError: (exc: Error) => {
      setError(exc.message || "Не удалось подтвердить");
    }
  });

  const canVerify = Boolean(
    kbEntry.data &&
      !kbEntry.data.checked &&
      (kbEntry.data.last_check_run_id || kbEntry.data.marking_document_id)
  );

  const currentPageEntry = useMemo(
    () => pageFindings.find((p) => p.page === currentPage),
    [pageFindings, currentPage]
  );

  const markedPages = useMemo(
    () =>
      pageFindings
        .filter((p) => p.gost_findings.some((f) => f.severity !== "ok") || p.note.trim())
        .map((p) => p.page),
    [pageFindings]
  );

  function resetEditor() {
    setDocument(null);
    setLabelId(null);
    setDraftCheckRunId(null);
    setPageFindings([]);
    setProblemReport("");
    setCurrentPage(1);
    setFile(null);
    setMessage(null);
    setError(null);
    setExistingLookup(null);
    setForceNewUpload(false);
    setPendingCheckRun(null);
  }

  async function checkExistingByFilename(filename: string) {
    try {
      const hit = await lookupMarkingDocumentByFilename(filename);
      setExistingLookup(hit.found ? hit : null);
      setForceNewUpload(false);
    } catch {
      setExistingLookup(null);
    }
  }

  async function applySuggestedLabel(docId: string) {
    const suggested = await fetchSuggestedMarkingLabel(docId);
    setLabelId(suggested.source === "saved" ? suggested.label_id : null);
    setDraftCheckRunId(suggested.source === "check_run" ? suggested.check_run_id : null);
    setPageFindings(
      (suggested.page_level ?? []).map((entry) => ({
        ...entry,
        note: filterLlmMetaText(entry.note)
      }))
    );
    setProblemReport(filterLlmMetaText(suggested.problem_report ?? ""));
    if (!suggested.found) {
      return "Документ открыт — разметка пока пустая";
    }
    if (suggested.source === "saved") {
      return "Загружена сохранённая разметка — можно дополнить";
    }
    return "Подставлены ошибки из последней проверки ИИ — проверьте и сохраните";
  }

  async function openSavedDocument(docId: string) {
    setLoadingDoc(true);
    setError(null);
    try {
      const doc = await fetchMarkingDocument(docId);
      const messageText = await applySuggestedLabel(docId);
      setDocument(doc);
      setCurrentPage(doc.pages[0]?.page ?? 1);
      setFile(null);
      setMessage(messageText);
    } catch (exc) {
      setError((exc as Error).message || "Не удалось открыть документ");
    } finally {
      setLoadingDoc(false);
    }
  }

  useEffect(() => {
    if (!openIntent) return;
    if (openIntent.type === "document") {
      void openSavedDocument(openIntent.documentId).finally(() => onOpenIntentHandled?.());
      return;
    }
    void openFromCheckRun(openIntent.checkRunId, openIntent.filename).finally(() =>
      onOpenIntentHandled?.()
    );
  }, [openIntent, onOpenIntentHandled]);

  async function openFromCheckRun(checkRunId: string, filename: string, attachFile?: File) {
    setLoadingDoc(true);
    setError(null);
    setMessage(null);
    try {
      const doc = await openMarkingFromCheckRun(checkRunId, attachFile);
      const messageText = await applySuggestedLabel(doc.id);
      setDocument(doc);
      setCurrentPage(doc.pages[0]?.page ?? 1);
      setFile(null);
      setPendingCheckRun(null);
      setMessage(messageText);
    } catch (exc) {
      const err = exc as Error & { response?: { status?: number; data?: { detail?: string } } };
      const detail = err.response?.data?.detail;
      const text = typeof detail === "string" ? detail : err.message || "Не удалось открыть разметку";
      if (err.response?.status === 409) {
        setPendingCheckRun({ checkRunId, filename });
        setError(null);
        setMessage(text);
      } else {
        setError(text);
      }
    } finally {
      setLoadingDoc(false);
    }
  }

  async function handlePendingCheckUpload() {
    if (!pendingCheckRun) return;
    if (!file) {
      setError(`Выберите файл «${pendingCheckRun.filename}»`);
      return;
    }
    if (file.name.trim().toLowerCase() !== pendingCheckRun.filename.trim().toLowerCase()) {
      setError(`Нужен файл «${pendingCheckRun.filename}», выбран «${file.name}»`);
      return;
    }
    await openFromCheckRun(pendingCheckRun.checkRunId, pendingCheckRun.filename, file);
  }

  async function handleUpload() {
    if (!file) {
      setError("Выберите файл");
      return;
    }
    setError(null);
    setMessage(null);
    setUploading(true);
    try {
      const doc = await uploadMarkingDocument(file, designation, { forceNew: forceNewUpload });
      setDocument(doc);
      setCurrentPage(doc.pages[0]?.page ?? 1);
      if (doc.reused_existing) {
        const messageText = await applySuggestedLabel(doc.id);
        setMessage(
          messageText.startsWith("Подставлены")
            ? `Найдена существующая запись по имени «${doc.source_filename}». ${messageText}`
            : messageText.startsWith("Загружена")
              ? `Найдена существующая разметка по имени «${doc.source_filename}» — открыта для редактирования`
              : `Документ «${doc.source_filename}» уже был загружен ранее — разметка пока пустая`
        );
      } else {
        const messageText = await applySuggestedLabel(doc.id);
        if (!messageText.startsWith("Подставлены")) {
          setLabelId(null);
          setDraftCheckRunId(null);
          setPageFindings([]);
          setProblemReport("");
          setMessage(`Загружено: ${doc.pages.length} лист(ов)`);
        } else {
          setMessage(`Загружено: ${doc.pages.length} лист(ов). ${messageText}`);
        }
      }
      setExistingLookup(null);
      setForceNewUpload(false);
      void queryClient.invalidateQueries({ queryKey: ["knowledge-base"] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-base-entry"] });
    } catch (exc) {
      setError((exc as Error).message || "Ошибка загрузки");
    } finally {
      setUploading(false);
    }
  }

  function updatePageFindings(page: number, findings: GostFinding[], note?: string) {
    setPageFindings((prev) => {
      const prevEntry = prev.find((p) => p.page === page);
      const pageNote = note ?? prevEntry?.note ?? "";
      const violations = findings
        .filter((f) => f.severity !== "ok")
        .map((f) => ({ ...f, pages: f.pages.length ? f.pages : [page] }));
      const rest = prev.filter((p) => p.page !== page);
      if (!violations.length && !pageNote.trim()) return rest;
      return [...rest, { page, gost_findings: violations, note: pageNote }];
    });
  }

  async function handleSave() {
    if (!document) {
      setError("Сначала загрузите документ");
      return;
    }
    setSaving(true);
    setError(null);
    const payload = {
      document_level: buildDocumentLevelPerPage(pageFindings),
      page_level: pageFindings,
      problem_report: problemReport
    };
    try {
      if (labelId) {
        await updateMarkingLabel(labelId, payload);
        setMessage("Разметка обновлена");
      } else {
        const created = await createMarkingLabel({
          document_id: document.id,
          check_run_id: draftCheckRunId,
          ...payload
        });
        setLabelId(created.id);
        setDraftCheckRunId(null);
        setMessage("Разметка сохранена");
      }
      void queryClient.invalidateQueries({ queryKey: ["knowledge-base"] });
      void queryClient.invalidateQueries({ queryKey: ["knowledge-base-entry"] });
      void queryClient.invalidateQueries({ queryKey: ["marking-stats"] });
    } catch (exc) {
      setError((exc as Error).message || "Ошибка сохранения");
    } finally {
      setSaving(false);
    }
  }

  async function handleVerify() {
    if (!document || !kbEntry.data) return;
    setError(null);
    await verify.mutateAsync({
      checkRunId: kbEntry.data.last_check_run_id,
      markingDocumentId: document.id
    });
  }

  const previewSrc =
    document && document.pages.length
      ? markingPreviewUrl(
          document.pages.find((p) => p.page === currentPage)?.preview_url ??
            document.pages[0].preview_url
        )
      : "";

  return (
    <section className={layout.page}>
      <div className={styles.screenOnly}>
      <header className={layout.header}>
        <div className={layout.headerMain}>
          <h1>Разметка</h1>
          <p>
            Пролистывайте листы и отмечайте нарушения ГОСТ. Можно переключаться на другие вкладки — черновик не
            сбросится.
          </p>
        </div>
      </header>

      {!document && loadingDoc && (
        <div className={`card ${styles.uploadCard}`}>
          <div className={styles.loadingRow}>
            <Loader2 size={18} className="spin" /> Открываем документ…
          </div>
        </div>
      )}

      {!document && !loadingDoc && pendingCheckRun && (
        <section className={`card ${styles.uploadCard}`}>
          <div className={styles.pendingHint}>
            <p>
              Для разметки нужен исходный PDF проверки: <strong>{pendingCheckRun.filename}</strong>
            </p>
            {message && <p className={styles.pendingNote}>{message}</p>}
          </div>
          <div className={styles.uploadRow}>
            <label className={styles.fileBtn}>
              <Upload size={16} />
              {file ? file.name : pendingCheckRun.filename}
              <input
                type="file"
                hidden
                accept=".pdf,.png,.jpg,.jpeg"
                onChange={(e) => {
                  const picked = e.target.files?.[0] ?? null;
                  setFile(picked);
                  setError(null);
                }}
              />
            </label>
            <button
              type="button"
              className="primaryBtn"
              disabled={loadingDoc || !file}
              onClick={() => void handlePendingCheckUpload()}
            >
              {loadingDoc ? <Loader2 size={16} className="spin" /> : null}
              Открыть разметку
            </button>
          </div>
        </section>
      )}

      {!document && !loadingDoc && !pendingCheckRun && (
        <section className={`card ${styles.uploadCard}`}>
          <div className={styles.uploadRow}>
            <label className={styles.fileBtn}>
              <Upload size={16} />
              {file ? file.name : "Выбрать PDF / JPG"}
              <input
                type="file"
                hidden
                accept=".pdf,.png,.jpg,.jpeg"
                onChange={(e) => {
                  const picked = e.target.files?.[0] ?? null;
                  setFile(picked);
                  setError(null);
                  setMessage(null);
                  if (picked) {
                    void checkExistingByFilename(picked.name);
                  } else {
                    setExistingLookup(null);
                    setForceNewUpload(false);
                  }
                }}
              />
            </label>
            <input
              placeholder="Обозначение (опционально)"
              value={designation}
              onChange={(e) => setDesignation(e.target.value)}
            />
            <button
              type="button"
              className="primaryBtn"
              disabled={uploading || !file}
              onClick={() => void handleUpload()}
            >
              {uploading ? <Loader2 size={16} className="spin" /> : null}
              {existingLookup && !forceNewUpload ? "Открыть существующую" : "Загрузить"}
            </button>
          </div>
          {existingLookup?.found && (
            <div className={styles.existingHint}>
              <p>
                Для файла <strong>{existingLookup.document?.source_filename}</strong> уже есть запись в базе
                {existingLookup.marked_pages_count > 0
                  ? ` (разметка: ${existingLookup.marked_pages_count} л.)`
                  : existingLookup.document?.has_saved_label
                    ? " (сохранена без отметок по листам)"
                    : ""}
                . Будет открыта существующая разметка, новая копия не создастся.
              </p>
              <label className={styles.forceNewLabel}>
                <input
                  type="checkbox"
                  checked={forceNewUpload}
                  onChange={(e) => setForceNewUpload(e.target.checked)}
                />
                Всё равно загрузить как новую копию
              </label>
            </div>
          )}
        </section>
      )}

      {document && catalog.data && (
        <div className={styles.grid}>
          <section className={`card ${styles.previewCard}`}>
            <DocumentPreview
              pages={document.pages}
              currentPage={currentPage}
              markedPages={markedPages}
              onPageChange={setCurrentPage}
              previewSrc={previewSrc}
            />
          </section>

          <section className={`card ${styles.formCard}`}>
            <div className={styles.docHead}>
              <div>
                <h2>{document.source_filename}</h2>
                <p className={styles.formHint}>
                  Нарушения по документу — лист {currentPage}
                  {labelId
                    ? " · сохранённая разметка"
                    : draftCheckRunId
                      ? " · черновик из проверки ИИ"
                      : " · новая разметка"}
                </p>
              </div>
              <button
                type="button"
                className={`secondaryBtn ${styles.printBtn}`}
                onClick={handlePrintReport}
                title="Печать отчёта по разметке"
              >
                <Printer size={16} />
                Печать отчёта
              </button>
            </div>

            <GostSummaryForm
              mode="editable"
              catalog={catalog.data}
              fixedPage={currentPage}
              findings={currentPageEntry?.gost_findings ?? emptyFindings(catalog.data)}
              onChange={(findings) => updatePageFindings(currentPage, findings)}
            />

            <label className={styles.reportLabel}>
              Замечание по этому листу
              <textarea
                rows={3}
                value={currentPageEntry?.note ?? ""}
                onChange={(e) =>
                  updatePageFindings(
                    currentPage,
                    currentPageEntry?.gost_findings ?? emptyFindings(catalog.data),
                    e.target.value
                  )
                }
                placeholder="Что не так на этом листе…"
              />
            </label>

            <label className={styles.reportLabel}>
              Общий отчёт по документу
              <textarea
                rows={4}
                value={problemReport}
                onChange={(e) => setProblemReport(e.target.value)}
                placeholder="Итоговое описание проблем по комплекту…"
              />
            </label>

            <div className={styles.actions}>
              {canVerify && (
                <button
                  type="button"
                  className={styles.verifyBtn}
                  disabled={verify.isPending}
                  onClick={() => void handleVerify()}
                >
                  {verify.isPending ? <Loader2 size={16} className="spin" /> : <CheckCircle2 size={16} />}
                  Подтвердить
                </button>
              )}
              <button type="button" className="primaryBtn" disabled={saving} onClick={() => void handleSave()}>
                {saving ? <Loader2 size={16} className="spin" /> : <Save size={16} />}
                {labelId ? "Обновить разметку" : "Сохранить разметку"}
              </button>
              <button type="button" className="secondaryBtn" onClick={resetEditor}>
                Закрыть / новый файл
              </button>
            </div>
          </section>
        </div>
      )}

      {message && <p className={styles.ok}>{message}</p>}
      {error && <p className={styles.err}>{error}</p>}
      </div>

      {document && catalog.data && (
        <MarkingReportPrint
          document={document}
          catalog={catalog.data}
          pageFindings={pageFindings}
          problemReport={problemReport}
          labelId={labelId}
          draftCheckRunId={draftCheckRunId}
          savedLabel={savedLabel.data}
          checkRun={reportCheckRun.data}
          kbEntry={kbEntry.data}
        />
      )}
    </section>
  );
}
