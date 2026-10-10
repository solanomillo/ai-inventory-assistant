"""Pruebas para el servicio de modelos de lenguaje."""

from unittest.mock import Mock, patch

import pytest

from app.services import llm


def test_get_llm_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que la API Key sea obligatoria."""

    monkeypatch.setattr(llm, "DEEPSEEK_API_KEY", None)
    monkeypatch.setattr(llm, "DEEPSEEK_MODEL", "deepseek-chat")

    with pytest.raises(llm.LLMConfigurationError):
        llm.get_llm()


def test_get_llm_requires_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que el identificador del modelo sea obligatorio."""

    monkeypatch.setattr(llm, "DEEPSEEK_API_KEY", "test-api-key")
    monkeypatch.setattr(llm, "DEEPSEEK_MODEL", None)

    with pytest.raises(llm.LLMConfigurationError):
        llm.get_llm()


def test_get_llm_creates_deepseek_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica la construcción del cliente sin invocar el modelo."""

    monkeypatch.setattr(llm, "DEEPSEEK_API_KEY", "test-api-key")
    monkeypatch.setattr(llm, "DEEPSEEK_MODEL", "deepseek-chat")

    fake_client = Mock()

    with patch("app.services.llm.ChatDeepSeek", return_value=fake_client) as mock_cls:
        model = llm.get_llm()

    assert model is fake_client

    mock_cls.assert_called_once_with(
        model="deepseek-chat",
        api_key="test-api-key",
        temperature=0,
    )
