"""Re-export для агента nd_control_agent. CLI: python -m app.services.onec_departments_fetcher"""

from app.services.onec_departments_fetcher import (
    EnterpriseDepartment,
    departments_to_json,
    fetch_all_departments_from_1c,
    filter_departments,
    format_departments_text,
    main,
    run_cli,
    save_departments_report,
)

__all__ = [
    "EnterpriseDepartment",
    "departments_to_json",
    "fetch_all_departments_from_1c",
    "filter_departments",
    "format_departments_text",
    "main",
    "run_cli",
    "save_departments_report",
]

if __name__ == "__main__":
    main()
