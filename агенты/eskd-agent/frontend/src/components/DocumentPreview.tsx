import { useEffect } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { MarkingDocumentPage } from "@/types/marking";
import styles from "./DocumentPreview.module.css";

interface Props {
  pages: MarkingDocumentPage[];
  currentPage: number;
  markedPages?: number[];
  onPageChange: (page: number) => void;
  previewSrc: string;
}

export default function DocumentPreview({
  pages,
  currentPage,
  markedPages = [],
  onPageChange,
  previewSrc
}: Props) {
  const sorted = [...pages].sort((a, b) => a.page - b.page);
  const pageIndex = sorted.findIndex((p) => p.page === currentPage);
  const canPrev = pageIndex > 0;
  const canNext = pageIndex >= 0 && pageIndex < sorted.length - 1;

  function goPrev() {
    if (canPrev) onPageChange(sorted[pageIndex - 1].page);
  }

  function goNext() {
    if (canNext) onPageChange(sorted[pageIndex + 1].page);
  }

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      if (target?.closest("input, textarea, select")) return;
      if (e.key === "ArrowLeft" && pageIndex > 0) {
        onPageChange(sorted[pageIndex - 1].page);
      }
      if (e.key === "ArrowRight" && pageIndex >= 0 && pageIndex < sorted.length - 1) {
        onPageChange(sorted[pageIndex + 1].page);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [pageIndex, sorted, onPageChange]);

  const markedSet = new Set(markedPages);

  return (
    <div className={styles.wrap}>
      <div className={styles.toolbar}>
        <button type="button" className="secondaryBtn" disabled={!canPrev} onClick={goPrev}>
          <ChevronLeft size={16} /> Назад
        </button>
        <span className={styles.pageInfo}>
          Лист {currentPage} из {sorted.length}
        </span>
        <button type="button" className="secondaryBtn" disabled={!canNext} onClick={goNext}>
          Вперёд <ChevronRight size={16} />
        </button>
      </div>

      <div className={styles.viewer}>
        <img src={previewSrc} alt={`Лист ${currentPage}`} className={styles.image} />
      </div>

      {sorted.length > 1 && (
        <div className={styles.thumbs}>
          {sorted.map((p) => (
            <button
              key={p.page}
              type="button"
              className={`${styles.thumb} ${p.page === currentPage ? styles.thumbActive : ""} ${
                markedSet.has(p.page) ? styles.thumbMarked : ""
              }`}
              onClick={() => onPageChange(p.page)}
              title={markedSet.has(p.page) ? "Есть отметки" : undefined}
            >
              {p.page}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
