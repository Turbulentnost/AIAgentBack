from agent_pochta.metrics.department_colors import (
    department_chart_label,
    department_color,
    grafana_department_color_overrides,
    normalize_department_key,
)


def test_normalize_department_key_collapses_whitespace_and_case() -> None:
    assert normalize_department_key("  ФИНАНСОВЫЙ  ДИРЕКТОР  ") == "финансовый директор"


def test_department_color_is_stable_for_same_name() -> None:
    assert department_color("Отдел МТО") == department_color("Отдел МТО")
    assert department_color("ФИНАНСОВЫЙ  ДИРЕКТОР") == department_color("финансовый директор")


def test_department_color_differs_for_different_names() -> None:
    assert department_color("Отдел МТО") != department_color("Бухгалтерия")


def test_department_chart_label_matches_exporter_format() -> None:
    long_name = "A" * 70
    assert department_chart_label(long_name).endswith("…")
    assert len(department_chart_label(long_name)) == 64


def test_grafana_overrides_use_by_name_and_fixed_color() -> None:
    overrides = grafana_department_color_overrides(["Отдел МТО", "Бухгалтерия", "Отдел МТО"])
    assert len(overrides) == 2
    by_name = {item["matcher"]["options"]: item for item in overrides}
    assert by_name["Бухгалтерия"]["properties"][0]["value"]["mode"] == "fixed"
    assert by_name["Бухгалтерия"]["properties"][0]["value"]["fixedColor"].startswith("#")
