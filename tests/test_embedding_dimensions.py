from unittest.mock import patch

import pytest

from app.services.embedding_service import (
    EXPECTED_EMBEDDING_DIMENSIONS,
    validate_embedding_dimensions,
)


def test_expected_embedding_dimensions_constant():
    assert EXPECTED_EMBEDDING_DIMENSIONS == 1536


def test_validate_embedding_dimensions_passes_when_configured_correctly():
    with patch("app.services.embedding_service.settings.embedding_dimensions", 1536):
        validate_embedding_dimensions()  # Should not raise


def test_validate_embedding_dimensions_raises_on_mismatch():
    with patch("app.services.embedding_service.settings.embedding_dimensions", 768):
        with pytest.raises(RuntimeError) as exc_info:
            validate_embedding_dimensions()
        assert "Embedding dimension mismatch" in str(exc_info.value)
        assert "768" in str(exc_info.value)
        assert "1536" in str(exc_info.value)


def test_validate_embedding_dimensions_raises_on_1024():
    with patch("app.services.embedding_service.settings.embedding_dimensions", 1024):
        with pytest.raises(RuntimeError) as exc_info:
            validate_embedding_dimensions()
        assert "1024" in str(exc_info.value)
        assert "1536" in str(exc_info.value)