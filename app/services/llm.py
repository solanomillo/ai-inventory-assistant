"""Servicio centralizado para la interacción con el modelo de lenguaje."""

from __future__ import annotations

import logging

from langchain_deepseek import ChatDeepSeek

from app.config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL


logger = logging.getLogger(__name__)


class LLMConfigurationError(Exception):
    """Error relacionado con la configuración del modelo de lenguaje."""


def get_llm() -> ChatDeepSeek:
    """
    Construye y devuelve el cliente ChatDeepSeek configurado.

    Returns:
        Instancia de ChatDeepSeek lista para usarse en los agentes.

    Raises:
        LLMConfigurationError:
            Si falta la API Key o el identificador del modelo DeepSeek.
    """
    if not DEEPSEEK_API_KEY:
        raise LLMConfigurationError(
            "La API Key de DeepSeek no está configurada."
        )

    if not DEEPSEEK_MODEL:
        raise LLMConfigurationError(
            "El identificador del modelo DeepSeek no está configurado "
            "(decisión pendiente)."
        )

    logger.info(
        "Inicializando modelo DeepSeek: %s",
        DEEPSEEK_MODEL,
    )

    return ChatDeepSeek(
        model=DEEPSEEK_MODEL,
        api_key=DEEPSEEK_API_KEY,
        temperature=0,
    )
