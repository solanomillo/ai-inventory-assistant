"""Estado compartido del grafo del asistente (T018, RF-05).

Define el estado mínimo que circula entre los nodos del grafo LangGraph: el
mensaje del usuario, la propuesta pendiente (si existe), el resultado de la
confirmación (si existe) y la respuesta textual del Inventory Agent. El estado
es un contenedor pasivo: no valida reglas de negocio, no confirma operaciones,
no escribe en SQLite, no invoca al LLM y no fabrica datos.

La propuesta pendiente y el resultado de la confirmación reutilizan los
contratos de `app.schemas.proposals`: `Proposal` representa una propuesta
real y `ConfirmationResult` un resultado real de confirmación. No se introducen
tipos duplicados ni se transforman los objetos que se guardan: cada campo
conserva el valor tal como lo producen los servicios y los agentes.
"""

from __future__ import annotations

from typing import TypedDict

from app.schemas.proposals import ConfirmationResult, Proposal


class GraphState(TypedDict):
    """
    Estado compartido mínimo del grafo del asistente (T018, T022, RF-05).

    Contenedor pasivo: transporta los objetos sin validar reglas de negocio,
    sin confirmar operaciones y sin inventar resultados. La propuesta pendiente
    (`PENDING`) y la ejecución confirmada (`CONFIRMED`) se mantienen separadas
    en campos distintos, y `response` conserva la respuesta textual del
    Inventory Agent sin implicar que ninguna propuesta se haya ejecutado.
    """

    user_message: str
    pending_proposal: Proposal | None
    execution_result: ConfirmationResult | None
    response: str | None


def create_initial_state(user_message: str) -> GraphState:
    """
    Construye el estado inicial del grafo a partir del mensaje del usuario.

    El estado inicial no fabrica ninguna propuesta, resultado ni respuesta: la
    propuesta pendiente, el resultado de la confirmación y la respuesta nacen
    en `None` y solo se completan cuando los agentes o los servicios producen
    valores reales. Esta función es una factory pura y no toca la persistencia
    ni el LLM.

    Args:
        user_message: Mensaje recibido del usuario, sin transformar.

    Returns:
        El estado inicial con `user_message` y los campos opcionales en `None`.
    """
    return {
        "user_message": user_message,
        "pending_proposal": None,
        "execution_result": None,
        "response": None,
    }
