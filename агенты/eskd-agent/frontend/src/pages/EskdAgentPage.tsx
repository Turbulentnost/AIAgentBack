import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  Loader2,
  Square,
  Upload,
  X
} from "lucide-react";
import { cancelEskdJob, fetchHealth, lookupCheckCache, streamEskdCheck, type CheckCacheLookup } from "@/api/eskd";
import { EskdAnalysisView, itemToAnalysisData } from "@/components/EskdAnalysisView";
import type { EskdCheckResponse, EskdItemReport, PageMode } from "@/types/eskd";
import { parseLiveJson } from "@/utils/parseLiveJson";
import styles from "./EskdAgentPage.module.css";

function statusPillClass(modelLoaded?: boolean, running?: boolean) {
  if (running) return "warn";
  if (modelLoaded) return "ok";
  return "idle";
}

function itemTitle(item: EskdItemReport) {
  const name = item.filename || item.source;
  return `${name} · лист ${item.page}`;
}

export default function EskdAgentPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [designation, setDesignation] = useState("");
  const [pageMode, setPageMode] = useState<PageMode>("all");
  const [page, setPage] = useState(1);
  const [pageFrom, setPageFrom] = useState(1);
  const [pageTo, setPageTo] = useState(1);
  const [pagesList, setPagesList] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [progressLabel, setProgressLabel] = useState("");
  const [liveText, setLiveText] = useState("");
  const [result, setResult] = useState<EskdCheckResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [existingLookup, setExistingLookup] = useState<CheckCacheLookup | null>(null);
  const [lookupPending, setLookupPending] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const autoRanFor = useRef<string | null>(null);

  const health = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: running ? false : 15_000
  });

  const modelReady = Boolean(health.data?.model?.model_loaded);
  const modelOffline = health.data?.model?.reachable === false;

  const hasCachedResult = Boolean(files.length === 1 && existingLookup?.found);

  const canRun =
    (files.length === 1 && !lookupPending && (modelReady || hasCachedResult)) ||
    (files.length > 1 && modelReady);

  const runBlockedReason = useMemo(() => {
    if (!files.length || lookupPending) return null;
    if (files.length > 1 && !modelReady) {
      return "Для нескольких файлов нужна модель ИИ.";
    }
    if (files.length === 1 && !modelReady && !hasCachedResult) {
      return (
        existingLookup?.message ||
        "Модель ИИ не запущена. Выберите файл с сохранённой разметкой или проверкой в базе."
      );
    }
    return null;
  }, [existingLookup?.message, files.length, hasCachedResult, lookupPending, modelReady]);

  useEffect(() => {
    if (files.length !== 1) {
      setExistingLookup(null);
      setLookupPending(false);
      autoRanFor.current = null;
      return;
    }
    const filename = files[0].name;
    let cancelled = false;
    setLookupPending(true);
    setExistingLookup(null);

    void lookupCheckCache(filename)
      .then((hit) => {
        if (!cancelled) setExistingLookup(hit);
      })
      .catch(() => {
        if (!cancelled) setExistingLookup(null);
      })
      .finally(() => {
        if (!cancelled) setLookupPending(false);
      });

    return () => {
      cancelled = true;
    };
  }, [files]);

  const liveParsed = useMemo(() => parseLiveJson(liveText), [liveText]);
  const showLivePanel = Boolean(running || (liveParsed && !result));

  const addFiles = useCallback((incoming: FileList | File[]) => {
    const next = Array.from(incoming);
    setFiles((prev) => {
      const names = new Set(prev.map((f) => f.name));
      return [...prev, ...next.filter((f) => !names.has(f.name))];
    });
  }, []);

  const pageOpts = useMemo(() => {
    if (pageMode === "all") return { allPages: true as const };
    if (pageMode === "single") return { allPages: false as const, page };
    if (pageMode === "range") return { allPages: false as const, pageFrom, pageTo };
    return { allPages: false as const, pages: pagesList };
  }, [page, pageFrom, pageMode, pageTo, pagesList]);

  const handleRun = useCallback(async () => {
    if (!files.length) {
      setError("Выберите PDF, PNG, JPG или ZIP");
      return;
    }
    setError(null);
    setResult(null);
    setLiveText("");
    setProgress(0);
    setProgressLabel("Старт…");
    setRunning(true);
    abortRef.current = new AbortController();

    try {
      for await (const evt of streamEskdCheck(
        files,
        { designation, ...pageOpts },
        abortRef.current.signal
      )) {
        if (evt.event === "start") {
          const d = evt.data as { job_id?: string; total?: number };
          if (d.job_id) setJobId(d.job_id);
          setProgressLabel(`Задача ${d.job_id || ""} · ${d.total ?? "?"} лист(ов)`);
        }
        if (evt.event === "progress") {
          const d = evt.data as { percent?: number; message?: string };
          if (typeof d.percent === "number") setProgress(d.percent);
          if (d.message) setProgressLabel(d.message);
        }
        if (evt.event === "token") {
          const d = evt.data as { text?: string; delta?: string };
          const piece = d.text ?? d.delta;
          if (piece) setLiveText((t) => (t + piece).slice(-4000));
        }
        if (evt.event === "page_start") {
          const d = evt.data as { index?: number; total?: number; source?: string };
          setProgressLabel(`VLM: лист ${d.index}/${d.total} — ~2–4 мин на лист…`);
        }
        if (evt.event === "page_phase") {
          const d = evt.data as { phase?: string; index?: number; total?: number };
          if (d.phase === "ocr") {
            setProgressLabel(`OCR лист ${d.index}/${d.total}…`);
          } else {
            setProgressLabel(`VLM лист ${d.index}/${d.total} — ~2–4 мин на лист…`);
          }
        }
        if (evt.event === "package_eval") {
          const d = evt.data as { message?: string; percent?: number };
          if (typeof d.percent === "number") setProgress(d.percent);
          setProgressLabel(d.message || "Проверка комплекта…");
        }
        if (evt.event === "preprocess") {
          const d = evt.data as {
            warnings?: string[];
            vision_files?: number;
            extracted_count?: number;
            from_marking?: boolean;
            from_cache?: boolean;
          };
          if (d.from_marking || d.from_cache) {
            setProgress(100);
            setProgressLabel(
              d.from_marking
                ? "Найдена сохранённая разметка — проверка ИИ не выполняется"
                : "Найден сохранённый результат проверки — ИИ не вызывается"
            );
          } else if (d.warnings?.length) {
            setProgressLabel(`Конвертация: ${d.warnings[0]}`);
          } else {
            setProgressLabel(
              `Конвертация OK · vision=${d.vision_files ?? "?"} · text=${d.extracted_count ?? "?"}`
            );
          }
        }
        if (evt.event === "error") {
          const d = evt.data as { message?: string };
          throw new Error(d.message || "Ошибка model-сервиса");
        }
        if (evt.event === "complete") {
          const d = evt.data as EskdCheckResponse & { extracted_texts?: Array<{ source: string; text: string }> };
          setResult(d);
          setLiveText("");
          setProgress(100);
          setProgressLabel("Готово");
        }
      }
    } catch (exc) {
      if ((exc as Error).name !== "AbortError") {
        const raw = (exc as Error).message || "Ошибка проверки";
        const msg =
          raw === "Failed to fetch" || raw === "Network Error"
            ? "Сеть: backend недоступен"
            : raw;
        setError(msg);
      }
    } finally {
      setRunning(false);
      setJobId(null);
      abortRef.current = null;
    }
  }, [designation, files, pageOpts]);

  useEffect(() => {
    if (lookupPending || running || files.length !== 1 || modelReady) return;
    if (!hasCachedResult) {
      autoRanFor.current = null;
      return;
    }
    const name = files[0].name;
    if (autoRanFor.current === name) return;
    autoRanFor.current = name;
    void handleRun();
  }, [files, handleRun, hasCachedResult, lookupPending, modelReady, running]);

  async function handleCancel() {
    abortRef.current?.abort();
    if (jobId) {
      try {
        await cancelEskdJob(jobId);
      } catch {
        /* ignore */
      }
    }
    setRunning(false);
    setProgressLabel("Отменено");
  }

  return (
    <>
      <div className={styles.pageHeader}>
        <div>
          <h1>Проверка конструкторской документации по ЕСКД</h1>
          <p>
            Загрузите PDF, изображения чертежей или ZIP-комплект. Агент проверит штамп, обозначения,
            спецификацию и типовые нарушения ГОСТ.
          </p>
        </div>
        <span className={`statusPill ${statusPillClass(modelReady, running)}`}>
          {running ? (
            <>
              <Loader2 size={14} className="spin" /> Анализ…
            </>
          ) : modelReady ? (
            <>
              <CheckCircle2 size={14} /> Модель готова
            </>
          ) : (
            <>
              <AlertTriangle size={14} /> Модель загружается
            </>
          )}
        </span>
      </div>

      <div className={styles.grid}>
        <section className={`card ${styles.panel}`}>
          <h2 className={styles.panelTitle}>Загрузка документов</h2>

          <div
            className={`${styles.dropzone} ${dragOver ? styles.dropzoneActive : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
            }}
            onClick={() => inputRef.current?.click()}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && inputRef.current?.click()}
          >
            <Upload size={28} strokeWidth={1.8} />
            <div>Перетащите файлы или нажмите для выбора</div>
            <p className={styles.dropzoneHint}>
              PDF · PNG · DOCX · XLSX · XML · SPW · DXF · DWG · CDW · ZIP · до 200 MB
            </p>
            <input
              ref={inputRef}
              type="file"
              multiple
              accept=".pdf,.png,.jpg,.jpeg,.zip,.docx,.xlsx,.xml,.spw,.dxf,.dwg,.cdw,application/pdf,image/*"
              hidden
              onChange={(e) => e.target.files && addFiles(e.target.files)}
            />
          </div>

          {files.length > 0 && (
            <ul className={styles.fileList}>
              {files.map((f) => (
                <li key={f.name} className={styles.fileItem}>
                  <span>
                    <FileText size={16} style={{ verticalAlign: "middle", marginRight: 6 }} />
                    {f.name} ({Math.round(f.size / 1024)} KB)
                  </span>
                  <button
                    type="button"
                    className="secondaryBtn"
                    style={{ padding: "4px 8px" }}
                    onClick={() => setFiles((prev) => prev.filter((x) => x.name !== f.name))}
                  >
                    <X size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}

          {lookupPending && files.length === 1 && (
            <div className={styles.lookupPending}>
              <Loader2 size={14} className="spin" /> Проверяем базу знаний…
            </div>
          )}

          {!lookupPending && existingLookup?.message && (
            <div
              className={
                hasCachedResult
                  ? styles.existingHint
                  : existingLookup.checked_in_kb
                    ? styles.existingHint
                    : styles.blockedHint
              }
            >
              <p>{existingLookup.message}</p>
            </div>
          )}

          {!lookupPending && runBlockedReason && !hasCachedResult && (
            <div className={styles.blockedHint}>{runBlockedReason}</div>
          )}

          <div className={styles.field}>
            <label htmlFor="designation">Обозначение (опционально)</label>
            <input
              id="designation"
              placeholder="UFG-800-16.02.00.000"
              value={designation}
              onChange={(e) => setDesignation(e.target.value)}
            />
          </div>

          <div className={styles.field}>
            <span>Страницы PDF</span>
            <div className={styles.radioRow}>
              {(
                [
                  ["all", "Все"],
                  ["single", "Одна"],
                  ["range", "Диапазон"],
                  ["list", "Список"]
                ] as const
              ).map(([mode, label]) => (
                <label key={mode}>
                  <input
                    type="radio"
                    name="pageMode"
                    checked={pageMode === mode}
                    onChange={() => setPageMode(mode)}
                  />
                  {label}
                </label>
              ))}
            </div>
            {pageMode === "single" && (
              <input type="number" min={1} value={page} onChange={(e) => setPage(Number(e.target.value))} />
            )}
            {pageMode === "range" && (
              <div className={styles.radioRow}>
                <input type="number" min={1} value={pageFrom} onChange={(e) => setPageFrom(Number(e.target.value))} />
                <span>—</span>
                <input type="number" min={1} value={pageTo} onChange={(e) => setPageTo(Number(e.target.value))} />
              </div>
            )}
            {pageMode === "list" && (
              <input
                placeholder="1,3,5-8"
                value={pagesList}
                onChange={(e) => setPagesList(e.target.value)}
              />
            )}
          </div>

          <div className={styles.actions}>
            <button type="button" className="primaryBtn" disabled={running || !canRun} onClick={() => void handleRun()}>
              {running ? <Loader2 size={16} className="spin" /> : null}
              {running ? "Проверка…" : lookupPending ? "Проверка базы…" : hasCachedResult && !modelReady ? "Показать результат" : "Запустить проверку"}
            </button>
            {running && (
              <button type="button" className="secondaryBtn" onClick={() => void handleCancel()}>
                <Square size={16} /> Отмена
              </button>
            )}
          </div>

          {(running || progress > 0) && (
            <div className={styles.progressWrap}>
              <div className={styles.progressBar}>
                <div className={styles.progressFill} style={{ width: `${Math.min(progress, 100)}%` }} />
              </div>
              <div className={styles.progressMeta}>
                <span>{progressLabel}</span>
                <span>{Math.round(progress)}%</span>
              </div>
            </div>
          )}

          {showLivePanel && (
            <div className={styles.livePanel}>
              {liveParsed ? (
                <EskdAnalysisView data={liveParsed} streaming={running} />
              ) : (
                <div className={styles.liveWaiting}>
                  <Loader2 size={18} className="spin" />
                  <span>{running ? "Модель анализирует чертёж…" : "Ожидание ответа…"}</span>
                </div>
              )}
            </div>
          )}
          {error && <div className={styles.errorBanner}>{error}</div>}
        </section>

        <aside>
          <div className={`card ${styles.sideCard}`}>
            <h3>Статус сервисов</h3>
            <dl>
              <dt>Backend</dt>
              <dd>{health.data?.status ?? "…"}</dd>
              <dt>Model</dt>
              <dd>
                {health.data?.model?.reachable
                  ? modelReady
                    ? "loaded"
                    : "loading"
                  : modelOffline
                    ? "offline (не нужна для файлов из базы)"
                    : "offline"}
              </dd>
              <dt>Adapter</dt>
              <dd>{health.data?.model?.adapter_path || "—"}</dd>
            </dl>
          </div>
          {result && (
            <div className={`card ${styles.sideCard}`}>
              <h3>Итог</h3>
              {result.status === "from_marking" && (
                <p className={styles.fromMarkingNote}>Результат из сохранённой разметки (ИИ не вызывался)</p>
              )}
              {result.status === "from_cache" && (
                <p className={styles.fromMarkingNote}>Результат из сохранённой проверки (ИИ не вызывался)</p>
              )}
              {result.pipeline_mode === "two_stage" && (
                <p className={styles.pipelineBadge}>
                  Pipeline: extract → rules → {result.evaluator || "rules_only"}
                </p>
              )}
              <dl>
                <dt>Листов</dt>
                <dd>
                  {result.processed}/{result.total_items}
                </dd>
                <dt>Ошибок</dt>
                <dd>{result.total_errors}</dd>
                <dt>Предупреждений</dt>
                <dd>{result.total_warnings}</dd>
                <dt>Время inference</dt>
                <dd>
                  {result.status === "from_marking" || result.status === "from_cache"
                    ? "—"
                    : `${result.total_infer_seconds.toFixed(1)} с`}
                </dd>
              </dl>
            </div>
          )}
        </aside>
      </div>

      <section className={styles.results}>
        {!result && !running && (
          <div className={`card ${styles.empty}`}>Результаты проверки появятся здесь после запуска</div>
        )}
        {result?.extracted_texts?.map((block) => (
          <article key={block.name} className={`card ${styles.resultCard}`}>
            <div className={styles.resultHead}>
              <h3>Извлечённый текст · {block.source}</h3>
              <span className="statusPill idle">{block.format}</span>
            </div>
            <pre className={styles.liveBox} style={{ maxHeight: 280, background: "#f0f2f5", color: "#172033" }}>
              {block.text}
            </pre>
          </article>
        ))}
        {result?.package_errors && result.package_errors.length > 0 && (
          <article className={`card ${styles.resultCard} ${styles.packageCard}`}>
            <div className={styles.resultHead}>
              <h3>Проверка комплекта (cross-page)</h3>
              <span className={`statusPill ${result.package_errors.some((e) => e.severity === "error") ? "err" : "warn"}`}>
                {result.package_errors.length} замеч.
              </span>
            </div>
            <ul className={styles.packageList}>
              {result.package_errors.map((item, idx) => (
                <li
                  key={`${item.code}-${idx}`}
                  className={item.severity === "error" ? styles.packageError : styles.packageWarning}
                >
                  <code>{item.code}</code>
                  <span>{item.message}</span>
                  {item.gost_reference && <span className={styles.packageGost}>{item.gost_reference}</span>}
                  {item.pages && item.pages.length > 0 && (
                    <span className={styles.packagePages}>листы: {item.pages.join(", ")}</span>
                  )}
                </li>
              ))}
            </ul>
          </article>
        )}
        {result?.items.map((item) => (
          <article key={`${item.source}-${item.page}`} className={`card ${styles.resultCard}`}>
            <div className={styles.resultHead}>
              <h3>{itemTitle(item)}</h3>
              <span
                className={`statusPill ${
                  item.errors_count > 0 ? "err" : item.warnings_count > 0 ? "warn" : "ok"
                }`}
              >
                {item.errors_count > 0
                  ? `${item.errors_count} ошибок`
                  : item.warnings_count > 0
                    ? `${item.warnings_count} предупр.`
                    : "OK"}
              </span>
            </div>
            <EskdAnalysisView data={itemToAnalysisData(item)} />
          </article>
        ))}
      </section>

      <style>{`.spin { animation: spin 1s linear infinite; } @keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </>
  );
}
