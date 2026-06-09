from __future__ import annotations


class DocumentEditingError(RuntimeError):
    pass


class SourceDocumentNotFoundError(DocumentEditingError):
    pass


class ChangeLocationNotFoundError(DocumentEditingError):
    pass


class UnsupportedEditableFormatError(DocumentEditingError):
    pass
