import { useEffect, useState } from "react";
import { fetchAuthMe, fetchExchangeLog, type ExchangeLogItem } from "@/api/integration";
import layout from "@/styles/pageLayout.module.css";
import styles from "./IntegrationLogPage.module.css";

export default function IntegrationLogPage() {
  const [authSubject, setAuthSubject] = useState("…");
  const [authRoles, setAuthRoles] = useState<string[]>([]);
  const [items, setItems] = useState<ExchangeLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [requestId, setRequestId] = useState("");
  const [sourceSystem, setSourceSystem] = useState("");

  useEffect(() => {
    fetchAuthMe()
      .then((me) => {
        setAuthSubject(me.subject);
        setAuthRoles(me.roles);
      })
      .catch((exc: Error) => setError(exc.message));
  }, []);

  useEffect(() => {
    fetchExchangeLog({
      page: 1,
      size: 100,
      request_id: requestId || undefined,
      source_system: sourceSystem || undefined,
    })
      .then((resp) => {
        setItems(resp.items);
        setTotal(resp.total);
      })
      .catch((exc: Error) => setError(exc.message));
  }, [requestId, sourceSystem]);

  return (
    <section className={layout.page}>
      <header className={layout.header}>
        <div className={layout.headerMain}>
          <h1>Журнал интеграций</h1>
          <p className={styles.sub}>
            Пользователь: <strong>{authSubject}</strong> · роли: {authRoles.join(", ") || "—"}
          </p>
        </div>
      </header>

      <div className={styles.filters}>
        <label>
          request_id
          <input value={requestId} onChange={(e) => setRequestId(e.target.value)} />
        </label>
        <label>
          source_system
          <input value={sourceSystem} onChange={(e) => setSourceSystem(e.target.value)} />
        </label>
      </div>

      {error ? <p className={styles.error}>{error}</p> : null}

      <p className={styles.meta}>Записей: {total}</p>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Время</th>
              <th>Отправитель</th>
              <th>Операция</th>
              <th>Результат</th>
              <th>request_id</th>
              <th>Обозначение</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <tr key={row.id}>
                <td>{new Date(row.occurred_at).toLocaleString()}</td>
                <td>{row.sender}</td>
                <td>{row.operation}</td>
                <td>{row.result}</td>
                <td>{row.request_id || "—"}</td>
                <td>{row.designation || "—"}</td>
              </tr>
            ))}
            {!items.length ? (
              <tr>
                <td colSpan={6}>Нет записей</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </section>
  );
}
