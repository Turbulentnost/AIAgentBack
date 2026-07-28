import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Palette } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { fetchOtkUsers } from "@/api/users";
import { getDevUser, setDevAuth } from "@/api/client";
import ThemePicker from "@/components/ThemePicker";

function initialsFromName(name: string) {
  return name
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export default function OtkUserSelector() {
  const [login, setLogin] = useState(getDevUser());
  const [menuOpen, setMenuOpen] = useState(false);
  const [themeOpen, setThemeOpen] = useState(false);
  const users = useQuery({
    queryKey: ["otk-users"],
    queryFn: () => fetchOtkUsers()
  });

  const currentUser = users.data?.items.find((item) => item.login === login);
  const displayName = currentUser?.display_name ?? login;
  const position = currentUser?.department ?? "ОТК";
  const initials = useMemo(() => initialsFromName(displayName), [displayName]);

  useEffect(() => {
    if (currentUser) {
      setDevAuth(currentUser.login, currentUser.role);
    }
  }, [currentUser]);

  function selectUser(nextLogin: string) {
    setLogin(nextLogin);
    setMenuOpen(false);
  }

  return (
    <>
      <div className="profile-menu">
        <button
          className="profile-button"
          type="button"
          aria-expanded={menuOpen}
          aria-haspopup="menu"
          onClick={() => setMenuOpen((value) => !value)}
        >
          <span className="profile-avatar fallback">{initials || "О"}</span>
          <span className="profile-copy">
            <strong>{displayName}</strong>
            <small>{position}</small>
          </span>
          <ChevronDown
            aria-hidden="true"
            className={menuOpen ? "profile-chevron open" : "profile-chevron"}
            size={16}
            strokeWidth={2.2}
          />
        </button>

        {menuOpen ? (
          <div className="profile-dropdown" role="menu">
            {(users.data?.items ?? []).map((user) => (
              <button
                key={user.id}
                type="button"
                role="menuitem"
                className={user.login === login ? "profile-dropdownItemActive" : ""}
                onClick={() => selectUser(user.login)}
              >
                {user.display_name}
              </button>
            ))}
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                setThemeOpen(true);
              }}
            >
              <Palette aria-hidden="true" size={15} strokeWidth={2.2} />
              Тема оформления
            </button>
          </div>
        ) : null}
      </div>

      {themeOpen ? (
        <div
          className="themeModalBackdrop"
          role="presentation"
          onClick={() => setThemeOpen(false)}
        >
          <div
            className="themeModal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="theme-modal-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="themeModalHead">
              <h2 id="theme-modal-title">Тема оформления</h2>
              <button
                type="button"
                className="themeModalClose"
                aria-label="Закрыть"
                onClick={() => setThemeOpen(false)}
              >
                ×
              </button>
            </div>
            <ThemePicker />
          </div>
        </div>
      ) : null}
    </>
  );
}
