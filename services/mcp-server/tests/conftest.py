from __future__ import annotations

import pytest

from app.indexer import DEFAULT_DOCUMENT_PATH, BillingIndex, build_index


@pytest.fixture(scope="session")
def index_payload() -> dict:
    """Index built from the real guide (independent of any prebuilt index.json)."""
    return build_index(DEFAULT_DOCUMENT_PATH)


@pytest.fixture(scope="session")
def billing_index(index_payload) -> BillingIndex:
    return BillingIndex(index_payload)
