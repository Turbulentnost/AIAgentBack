from __future__ import annotations

import pytest

from app.eskd.validation.rules import EskdValidationContext
from app.models.document import Document, DocumentVersion
from app.models.document_card import QmsDocumentCard
from app.models.eskd_registration import EskdDocumentRegistration
from tests.eskd import mocks


@pytest.fixture
def eskd_valid_drawing_context() -> EskdValidationContext:
    return mocks.build_valid_drawing_context()


@pytest.fixture
def eskd_invalid_designation_context() -> EskdValidationContext:
    return mocks.build_invalid_designation_context()


@pytest.fixture
def eskd_specification_context() -> EskdValidationContext:
    return mocks.build_specification_context()


@pytest.fixture
def eskd_bundle() -> tuple[EskdDocumentRegistration, Document, DocumentVersion, QmsDocumentCard]:
    registration, document, version, card = mocks.make_eskd_bundle()
    assert card is not None
    return registration, document, version, card
