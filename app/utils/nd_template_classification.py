from __future__ import annotations

from app.models.enums import NdTemplateType

ND_TEMPLATE_TYPE_LABELS: dict[NdTemplateType, str] = {
    NdTemplateType.POLICY: "Политика",
    NdTemplateType.REGULATION: "Положение",
    NdTemplateType.DEPARTMENT_REGULATION: "Положение о подразделении",
    NdTemplateType.PROCESS_REGULATION: "Регламент",
    NdTemplateType.STO: "СТО",
    NdTemplateType.INSTRUCTION: "Инструкция",
    NdTemplateType.WORK_INSTRUCTION: "Рабочая инструкция",
    NdTemplateType.JOB_DESCRIPTION: "Должностная инструкция",
    NdTemplateType.CHANGE_NOTICE: "Извещение об изменении",
    NdTemplateType.DOCUMENT_INTRODUCTION_ORDER: "Приказ о вводе документа",
    NdTemplateType.IMPLEMENTATION_PLAN: "План внедрения",
    NdTemplateType.CHANGE_REGISTRATION_SHEET: "Лист регистрации изменений",
    NdTemplateType.ISSUANCE_ACKNOWLEDGEMENT_SHEET: "Лист выдачи и ознакомления",
    NdTemplateType.TRAINING_PROTOCOL: "Протокол обучения",
    NdTemplateType.PROCESS_PASSPORT: "Паспорт процесса",
}


def get_template_type_label(template_type: NdTemplateType | str | None) -> str | None:
    if template_type is None:
        return None
    if isinstance(template_type, str):
        try:
            template_type = NdTemplateType(template_type)
        except ValueError:
            return None
    return ND_TEMPLATE_TYPE_LABELS.get(template_type)
