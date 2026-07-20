from app.services.procurement_permission import (
    is_production_preparation_engineer_position,
)


def test_engineer_position_normalization():
    assert is_production_preparation_engineer_position("Ведущий инженер по подготовке производства")
    assert is_production_preparation_engineer_position("Инженер СПП")
    assert not is_production_preparation_engineer_position("Инженер ОМТО")
