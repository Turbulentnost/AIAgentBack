import { useId } from "react";
import styles from "./form-controls.module.css";

export default function FormCheckbox({
  checked,
  onChange,
  label,
  disabled = false,
  id,
  className,
  "aria-label": ariaLabel
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  disabled?: boolean;
  id?: string;
  className?: string;
  "aria-label"?: string;
}) {
  const generatedId = useId();
  const checkboxId = id ?? generatedId;

  const control = (
    <>
      <input
        id={checkboxId}
        type="checkbox"
        className={styles.checkboxInput}
        checked={checked}
        disabled={disabled}
        aria-label={label ? undefined : ariaLabel}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className={styles.checkboxBox} aria-hidden="true">
        <svg className={styles.checkboxMark} viewBox="0 0 14 14" focusable="false">
          <path d="M2.75 7.1 5.65 9.95 11.2 4.35" pathLength="1" />
        </svg>
      </span>
    </>
  );

  if (label) {
    return (
      <label
        className={`${styles.checkboxField} ${disabled ? styles.checkboxFieldDisabled : ""} ${className ?? ""}`.trim()}
        htmlFor={checkboxId}
      >
        {control}
        <span className={styles.checkboxLabel}>{label}</span>
      </label>
    );
  }

  return (
    <span className={`${styles.checkboxStandalone} ${disabled ? styles.checkboxFieldDisabled : ""} ${className ?? ""}`.trim()}>
      {control}
    </span>
  );
}
