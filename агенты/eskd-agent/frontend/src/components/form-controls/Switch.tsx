import styles from "./form-controls.module.css";

export default function Switch({
  checked,
  onChange,
  label,
  disabled = false,
  id,
  "aria-label": ariaLabel
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  disabled?: boolean;
  id?: string;
  "aria-label"?: string;
}) {
  const switchId = id ?? `switch-${label?.replace(/\s+/g, "-").toLowerCase() ?? "control"}`;
  const accessibleName = label ?? ariaLabel;

  return (
    <label
      className={`${styles.switchField} ${disabled ? styles.switchFieldDisabled : ""}`}
      htmlFor={switchId}
    >
      {label ? <span className={styles.switchLabel}>{label}</span> : null}
      <button
        id={switchId}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={accessibleName}
        disabled={disabled}
        className={`${styles.switch} ${checked ? styles.switchChecked : ""}`}
        onClick={() => !disabled && onChange(!checked)}
      >
        <span className={styles.switchThumb} aria-hidden="true" />
      </button>
    </label>
  );
}
