import type { Department } from "@/types";
import DepartmentSelect from "@/components/DepartmentSelect";
import FormSearchInput from "./FormSearchInput";
import FormSelect from "./FormSelect";
import Switch from "./Switch";
import styles from "./form-controls.module.css";

const showOnlyCurrentLabel = "Показывать только актуальные";

export default function SourceFilterBar({
  search,
  onSearch,
  typeFilter,
  onTypeFilter,
  typeOptions,
  departmentFilter,
  onDepartmentFilter,
  departments,
  statusFilter,
  onStatusFilter,
  statusOptions,
  showOnlyCurrent,
  onShowOnlyCurrent,
  selectedCount
}: {
  search: string;
  onSearch: (value: string) => void;
  typeFilter: string;
  onTypeFilter: (value: string) => void;
  typeOptions: { value: string; label: string }[];
  departmentFilter: string;
  onDepartmentFilter: (value: string) => void;
  departments: Department[];
  statusFilter: string;
  onStatusFilter: (value: string) => void;
  statusOptions: { value: string; label: string }[];
  showOnlyCurrent: boolean;
  onShowOnlyCurrent: (value: boolean) => void;
  selectedCount: number;
}) {
  return (
    <div className={styles.filterBarStack}>
      <div className={styles.filterBarPrimary}>
        <FormSearchInput
          compact
          className={styles.filterBarSearchWide}
          value={search}
          onChange={onSearch}
          placeholder="Найти документ по названию"
        />
        <span className={styles.filterBarSwitchLabel}>{showOnlyCurrentLabel}</span>
        <Switch
          aria-label={showOnlyCurrentLabel}
          checked={showOnlyCurrent}
          onChange={onShowOnlyCurrent}
        />
      </div>

      <div className={styles.filterBarSecondary}>
        <FormSelect
          compact
          value={typeFilter}
          onChange={onTypeFilter}
          placeholder="Тип документа"
          options={typeOptions}
          ariaLabel="Тип документа"
        />
        <DepartmentSelect
          compact
          className={styles.filterBarDepartmentWide}
          value={departmentFilter}
          onChange={onDepartmentFilter}
          departments={departments}
          allowEmpty={false}
          allValue="all"
          allLabel="Подразделение"
          ariaLabel="Подразделение"
        />
        <FormSelect
          compact
          value={statusFilter}
          onChange={onStatusFilter}
          placeholder="Статус"
          options={statusOptions}
          ariaLabel="Статус"
        />
        <span className={`${styles.selectedBadge} ${styles.filterBarBadge}`}>Выбрано: {selectedCount}</span>
      </div>
    </div>
  );
}
