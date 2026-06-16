from app.utils.department_classification import is_position_like_department_name, normalize_position_name


def test_position_like_names() -> None:
    assert is_position_like_department_name("Зам. директора по производству")
    assert is_position_like_department_name("ЗАМ. ДИРЕКТОРА ПО ЭКОНОМИЧЕСКОЙ БЕЗОПАСНОСТИ")
    assert is_position_like_department_name("ТЕХНИЧЕСКИЙ ДИРЕКТОР")
    assert is_position_like_department_name("Заместитель технического директора по качеству")
    assert is_position_like_department_name("Специалист по товарным запасам")


def test_department_names() -> None:
    assert not is_position_like_department_name("Отдел информационных технологий")
    assert not is_position_like_department_name("Конструкторское бюро")
    assert not is_position_like_department_name("Служба технического директора")
    assert not is_position_like_department_name("Бухгалтерия")
    assert not is_position_like_department_name("Конструкторско-технологический отдел")


def test_normalize_position_name() -> None:
    assert normalize_position_name("Зам. директора по производству") == "Заместитель директора по производству"
