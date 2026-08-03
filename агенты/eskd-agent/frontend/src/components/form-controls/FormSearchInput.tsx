import { Search } from "lucide-react";
import styles from "./form-controls.module.css";

export default function FormSearchInput({
  value,
  onChange,
  placeholder,
  className,
  compact = false
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  compact?: boolean;
}) {
  return (
    <div className={`${styles.selectField} ${compact ? styles.compact : ""} ${className ?? ""}`}>
      <Search className={styles.selectSearch} size={compact ? 14 : 16} strokeWidth={2} aria-hidden="true" />
      <input
        className={styles.control}
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </div>
  );
}
