"""Inventory Agent del asistente de inventario (T021, RF-05).

Construye el agente de inventario con `create_agent` de LangChain: recibe el
modelo de chat y las herramientas ya construidas y devuelve el grafo compilado
del agente. El agente interpreta la solicitud, consulta el inventario con las
herramientas de solo lectura y prepara o edita propuestas de operaciones.

El agente es un orquestador y no un acceso a datos: no construye servicios ni
herramientas, no accede a la persistencia y no obtiene el modelo por su cuenta.
El modelo y las herramientas se inyectan por parámetro, de modo que el agente
depende solo de los contratos de LangChain y reutiliza el prompt
`INVENTORY_AGENT_PROMPT` tal como está.

El alcance excluye la confirmación: el agente solo consulta y prepara
propuestas, y ninguna de las herramientas que recibe ejecuta la operación
propuesta; la confirmación es una acción explícita del usuario, fuera del
agente y fuera de su decisión autónoma (RF-05).
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.prompts.prompts import INVENTORY_AGENT_PROMPT


def create_inventory_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
) -> CompiledStateGraph:
    """
    Construye el Inventory Agent con el modelo y las herramientas indicados.

    El agente usa el prompt del Inventory Agent y las herramientas recibidas
    —las de consulta y las de propuesta— sin fijarlas por su cuenta: quien lo
    invoque decide qué modelo y qué herramientas se enlazan.

    Args:
        model: Modelo de chat que interpreta la solicitud y decide las
            llamadas a herramientas.
        tools: Herramientas que el agente puede invocar. Se pasan como una
            lista nueva, sin modificarlas ni completarlas.

    Returns:
        El grafo compilado del agente, invocable con `{"messages": [...]}`.
    """
    return create_agent(
        model=model,
        tools=list(tools),
        system_prompt=INVENTORY_AGENT_PROMPT,
        name="inventory_agent",
    )
