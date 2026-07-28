import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { fetchMarkingStats } from "@/api/marking";
import layout from "@/styles/pageLayout.module.css";
import styles from "./StatsPage.module.css";

export default function StatsPage() {
  const stats = useQuery({
    queryKey: ["marking-stats"],
    queryFn: fetchMarkingStats
  });

  return (
    <section className={layout.page}>
      <header className={layout.header}>
        <div className={layout.headerMain}>
          <h1>Статистика ГОСТ в разметке</h1>
          <p>Какие стандарты чаще всего отмечают при разметке документов.</p>
        </div>
      </header>

      <section className={`card ${styles.card}`}>
        {stats.isLoading ? (
          <div className={styles.loading}>
            <Loader2 size={18} className="spin" /> Загрузка…
          </div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>ГОСТ</th>
                <th>Ошибки</th>
                <th>Замечания</th>
                <th>Всего</th>
                <th>После проверки ИИ</th>
              </tr>
            </thead>
            <tbody>
              {(stats.data?.items ?? []).map((row) => (
                <tr key={row.gost_key}>
                  <td>
                    <div className={styles.gostTitle}>{row.title}</div>
                    <div className={styles.gostKey}>{row.gost_key}</div>
                  </td>
                  <td>{row.error_count}</td>
                  <td>{row.warning_count}</td>
                  <td>{row.total}</td>
                  <td title={`Ошибки: ${row.after_ai_error_count}, замечания: ${row.after_ai_warning_count}`}>
                    {row.after_ai_total}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {!stats.isLoading && !stats.data?.items.some((i) => i.total > 0) && (
          <p className={styles.empty}>Нет данных разметки. Создайте записи на вкладке «Разметка».</p>
        )}
      </section>
    </section>
  );
}
