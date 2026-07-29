import { ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import styles from "./form-controls.module.css";

export default function FormSelect({
  value,
  onChange,
  options,
  placeholder,
  className,
  ariaLabel,
  compact = false
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
  className?: string;
  ariaLabel?: string;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const allOptions = placeholder ? [{ value: "all", label: placeholder }, ...options] : options;
  const selected = allOptions.find((option) => option.value === value) ?? allOptions[0];

  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  return (
    <div
      ref={rootRef}
      className={`${styles.selectField} ${styles.selectDropdown} ${compact ? styles.compact : ""} ${open ? styles.selectDropdownOpen : ""} ${className ?? ""}`}
    >
      <button
        type="button"
        className={styles.control}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((current) => !current)}
      >
        <span className={styles.selectValue}>{selected?.label}</span>
      </button>
      <ChevronDown
        className={`${styles.selectChevron} ${open ? styles.selectChevronOpen : ""}`}
        size={16}
        strokeWidth={2}
        aria-hidden="true"
      />
      {open ? (
        <ul id={listId} className={styles.selectMenu} role="listbox" aria-label={ariaLabel}>
          {allOptions.map((option) => {
            const active = option.value === value;
            return (
              <li key={option.value} role="none">
                <button
                  type="button"
                  role="option"
                  aria-selected={active}
                  className={`${styles.selectOption} ${active ? styles.selectOptionActive : ""}`}
                  onClick={() => {
                    onChange(option.value);
                    setOpen(false);
                  }}
                >
                  {option.label}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
