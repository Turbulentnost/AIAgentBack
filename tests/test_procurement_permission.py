from app.services.procurement_permission import (
    is_omto_support_manager_position,
    is_production_preparation_engineer_position,
    is_warehouse_complex_chief_position,
    is_warehouse_complex_department_name,
    is_warehouse_head_position,
)


def test_engineer_position_normalization():
    assert is_production_preparation_engineer_position("Ведущий инженер по подготовке производства")
    assert is_production_preparation_engineer_position("Инженер СПП")
    assert not is_production_preparation_engineer_position("Инженер ОМТО")
    assert not is_production_preparation_engineer_position("Менеджер по сопровождению ОМТО")


def test_omto_position_normalization():
    assert is_omto_support_manager_position("Менеджер по сопровождению ОМТО")
    assert is_omto_support_manager_position("менеджер омто")
    assert is_omto_support_manager_position("Специалист ОМТО")
    assert not is_omto_support_manager_position("Инженер по подготовке производства")
    assert not is_omto_support_manager_position("Инженер СПП")
    assert not is_omto_support_manager_position(None)
    assert not is_omto_support_manager_position("")


def test_warehouse_complex_chief_position_rules():
    assert is_warehouse_complex_chief_position("Начальник складского комплекса")
    assert is_warehouse_complex_chief_position("начальник складского комплекса")
    assert not is_warehouse_complex_chief_position("Начальник склада")
    assert not is_warehouse_complex_chief_position("Кладовщик-комплектовщик")
    assert is_warehouse_head_position("Начальник склада")
    assert is_warehouse_head_position("начальник склада участка")
    assert not is_warehouse_head_position("Начальник складского комплекса")
    assert is_warehouse_complex_department_name("Складской комплекс")
    assert is_warehouse_complex_department_name("Отдел «Складской комплекс»")
    assert not is_warehouse_complex_department_name("Склад №1")
