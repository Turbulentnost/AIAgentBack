import { ChevronDown, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import styles from "./form-controls.module.css";

export type FormAutocompleteOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

export default function FormAutocomplete({
  value,
  onChange,
  options,
  placeholder = "Выберите значение",
  ariaLabel,
  compact = false,
  className,
  showSearchIcon = true,
  emptyValue,
  emptyLabel,
  footerOptions = [],
  onFooterSelect,
  noResultsText = "Ничего не найдено",
  onQueryChange
}: {
  value: string;
  onChange: (value: string) => void;
  options: FormAutocompleteOption[];
  placeholder?: string;
  ariaLabel?: string;
  compact?: boolean;
  className?: string;
  showSearchIcon?: boolean;
  emptyValue?: string;
  emptyLabel?: string;
  footerOptions?: FormAutocompleteOption[];
  onFooterSelect?: (value: string) => void;
  noResultsText?: string;
  onQueryChange?: (query: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  const selectableOptions = useMemo(() => {
    const list: FormAutocompleteOption[] = [];
    if (emptyValue !== undefined) {
      list.push({ value: emptyValue, label: emptyLabel || placeholder });
    }
    return [...list, ...options];
  }, [emptyLabel, emptyValue, options, placeholder]);

  const selected = selectableOptions.find((option) => option.value === value);

  const filteredOptions = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return selectableOptions;
    return selectableOptions.filter(
      (option) =>
        option.label.toLowerCase().includes(normalized) ||
        option.value.toLowerCase().includes(normalized)
    );
  }, [query, selectableOptions]);

  useEffect(() => {
    if (!open) return;
    function handlePointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    }
    function handleEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        setQuery("");
        inputRef.current?.blur();
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  function openList() {
    setOpen(true);
    // Не подставляем label пустой опции («Выберите…») в поиск — иначе фильтр скрывает всех кандидатов.
    const hasSelection = Boolean(value) && (emptyValue === undefined || value !== emptyValue);
    setQuery(hasSelection ? selected?.label ?? "" : "");
    window.requestAnimationFrame(() => inputRef.current?.select());
  }

  function closeList() {
    setOpen(false);
    setQuery("");
  }

  function handleSelect(option: FormAutocompleteOption) {
    if (option.disabled) return;
    onChange(option.value);
    closeList();
    inputRef.current?.blur();
  }

  function handleFooterSelect(option: FormAutocompleteOption) {
    if (option.disabled) return;
    onFooterSelect?.(option.value);
    closeList();
    inputRef.current?.blur();
  }

  const hasRealSelection = Boolean(value) && (emptyValue === undefined || value !== emptyValue);
  const inputValue = open ? query : hasRealSelection ? selected?.label ?? "" : "";

  return (
    <div
      ref={rootRef}
      className={`${styles.selectField} ${styles.autocompleteField} ${compact ? styles.compact : ""} ${open ? styles.selectDropdownOpen : ""} ${className ?? ""}`.trim()}
    >
      {showSearchIcon ? <Search className={styles.selectSearch} size={compact ? 14 : 16} strokeWidth={2} aria-hidden="true" /> : null}
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        className={styles.control}
        value={inputValue}
        placeholder={placeholder}
        onFocus={openList}
        onClick={openList}
        onChange={(event) => {
          setQuery(event.target.value);
          onQueryChange?.(event.target.value);
          setOpen(true);
        }}
      />
      <button
        type="button"
        className={styles.autocompleteToggle}
        aria-label={open ? "Свернуть список" : "Развернуть список"}
        aria-expanded={open}
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => {
          if (open) {
            closeList();
            inputRef.current?.blur();
          } else {
            openList();
            inputRef.current?.focus();
          }
        }}
      >
        <ChevronDown className={`${styles.selectChevron} ${open ? styles.selectChevronOpen : ""}`} size={16} strokeWidth={2} aria-hidden="true" />
      </button>
      {open ? (
        <ul id={listId} className={styles.selectMenu} role="listbox" aria-label={ariaLabel}>
          {filteredOptions.length ? (
            filteredOptions.map((option) => {
              const active = option.value === value;
              return (
                <li key={option.value} role="none">
                  <button
                    type="button"
                    role="option"
                    aria-selected={active}
                    disabled={option.disabled}
                    className={`${styles.selectOption} ${active ? styles.selectOptionActive : ""} ${option.disabled ? styles.selectOptionDisabled : ""}`}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => handleSelect(option)}
                  >
                    {option.label}
                  </button>
                </li>
              );
            })
          ) : (
            <li className={styles.selectEmpty} role="none">
              {noResultsText}
            </li>
          )}
          {footerOptions.length ? (
            <li className={styles.selectMenuFooter} role="none">
              {footerOptions.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  disabled={option.disabled}
                  className={`${styles.selectOption} ${styles.selectOptionFooter} ${option.disabled ? styles.selectOptionDisabled : ""}`}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => handleFooterSelect(option)}
                >
                  {option.label}
                </button>
              ))}
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}
