import { useQuery } from "@tanstack/react-query";
import { UserCircle2 } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchOtkUsers } from "@/api/users";
import { getDevUser, setDevAuth } from "@/api/client";
import styles from "./OtkUserSelector.module.css";

export default function OtkUserSelector() {
  const [login, setLogin] = useState(getDevUser());
  const users = useQuery({
    queryKey: ["otk-users"],
    queryFn: () => fetchOtkUsers()
  });

  useEffect(() => {
    const row = users.data?.items.find((item) => item.login === login);
    if (row) {
      setDevAuth(row.login, row.role);
    }
  }, [login, users.data?.items]);

  return (
    <label className={styles.root}>
      <UserCircle2 size={16} />
      <span className={styles.label}>ОТК:</span>
      <select
        value={login}
        onChange={(e) => setLogin(e.target.value)}
        disabled={users.isLoading || !users.data?.items.length}
      >
        {(users.data?.items ?? []).map((user) => (
          <option key={user.id} value={user.login}>
            {user.display_name}
          </option>
        ))}
      </select>
    </label>
  );
}
