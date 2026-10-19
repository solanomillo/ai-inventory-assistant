"""Supervisor del asistente de inventario (T022, RF-05).

Define el nodo Supervisor del grafo: clasifica la solicitud y la deriva al
Inventory Agent. El MVP tiene un único agente de destino, así que la derivación
es determinista y no consulta al LLM para enrutar: la misma entrada produce
siempre el mismo destino.

El Supervisor no atiende al usuario, no consulta ni modifica el inventario y no
ejecuta operaciones: solo decide el destino. La respuesta y las propuestas las
produce el Inventory Agent con sus herramientas.
"""

from __future__ import annotations

from collections.abc import Callable

from langgraph.types import Command

from app.graph.state import GraphState

INVENTORY_AGENT_NODE: str = "inventory_agent"
"""Nodo de destino del Supervisor: el Inventory Agent (T022)."""


def create_supervisor() -> Callable[[GraphState], Command]:
    """
    Crea el nodo Supervisor: un router determinista sin llamada al LLM (D2).

    El Supervisor clasifica la solicitud y la deriva al Inventory Agent. Como
    el MVP tiene un único agente de destino, la decisión es constante: no se
    consulta al LLM para enrutar y la derivación no depende del contenido del
    estado.

    Returns:
        El nodo del Supervisor, que dirige el flujo al Inventory Agent.
    """
    def supervisor(state: GraphState) -> Command:
        """
        Deriva la solicitud al Inventory Agent (RF-05).

        Args:
            state: Estado del grafo. Se recibe por el contrato de los nodos de
                LangGraph; la derivación no depende de su contenido.

        Returns:
            El comando que dirige el flujo al nodo del Inventory Agent.
        """
        return Command(goto=INVENTORY_AGENT_NODE)

    return supervisor
