"""Grafo del asistente de inventario (T022, RF-05).

Construye el flujo `START → supervisor → inventory_agent → END`: el Supervisor
deriva la solicitud y el nodo del Inventory Agent invoca el grafo del agente
(T021) y traduce su resultado al estado compartido.

Envoltura de herramientas: el flujo envuelve las diez herramientas de propuesta
antes de construir el agente. Cada envoltura conserva el nombre, la
descripción, el esquema de entrada y el modo de respuesta de la herramienta
original, delega la ejecución en su función —una única ejecución— y registra
el objeto `Proposal` que esta devuelve, sin depender de `ToolMessage.content`,
que es una representación textual. Las siete herramientas de consulta se pasan
sin envolver. La semántica de errores se conserva tal como la resolvió T021 en
el `ToolNode`, que no se reimplementa aquí: los errores de validación de
argumentos siguen volviendo como `ToolMessage(status="error")` y los errores
tipados siguen propagándose.

Puente de estado: `response` recibe el contenido del último `AIMessage` y
`pending_proposal` la **última** propuesta capturada durante la invocación.
Si el agente prepara varias propuestas en una misma invocación, las anteriores
no se conservan: el puente es determinista y no afirma que todas se conserven.
`execution_result` permanece `None`, porque confirmar es una acción explícita
del usuario y ninguna ruta de este grafo la ejecuta.

Inyección de dependencias: el flujo recibe el modelo, las herramientas de
consulta y las de propuesta por parámetro. No obtiene el modelo por su cuenta
ni accede a la persistencia: las herramientas ya construidas son la única vía
de acceso a los datos.

Limitaciones del MVP: la captura de propuestas es un contenedor del flujo, de
modo que una misma instancia del grafo compilado no debe invocarse de forma
concurrente; el puente solo conserva la última propuesta de cada invocación.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.inventory import create_inventory_agent
from app.agents.supervisor import INVENTORY_AGENT_NODE, create_supervisor
from app.graph.state import GraphState
from app.schemas.proposals import Proposal

SUPERVISOR_NODE: str = "supervisor"
"""Nodo del Supervisor en el grafo del asistente (T022)."""


@dataclass
class _ProposalCapture:
    """Propuestas capturadas por las envolturas en una invocación (T022)."""

    proposals: list[Proposal] = field(default_factory=list)

    def clear(self) -> None:
        """Descarta las propuestas capturadas en invocaciones anteriores."""
        self.proposals.clear()

    def record(self, proposal: Proposal) -> None:
        """Registra la propuesta que devolvió una herramienta envuelta."""
        self.proposals.append(proposal)

    @property
    def last(self) -> Proposal | None:
        """
        Última propuesta capturada, o `None` si no se capturó ninguna.

        Si el agente prepara varias propuestas, solo se conserva la última; las
        anteriores no se recuperan.
        """
        return self.proposals[-1] if self.proposals else None


def build_workflow(
    *,
    model: BaseChatModel,
    inventory_tools: Sequence[BaseTool],
    proposal_tools: Sequence[BaseTool],
) -> CompiledStateGraph:
    """
    Construye el grafo del asistente de inventario (T022, RF-05).

    El agente se construye aquí, con las herramientas de propuesta ya
    envueltas: el puente necesita capturar la propuesta real que devuelve cada
    herramienta y la envoltura debe existir antes de que el agente enlace sus
    herramientas.

    Args:
        model: Modelo de chat con el que el Inventory Agent decide sus llamadas
            a herramientas.
        inventory_tools: Herramientas de consulta, que se pasan sin envolver.
        proposal_tools: Herramientas de propuesta, que se envuelven para
            capturar la propuesta devuelta.

    Returns:
        El grafo compilado del asistente, invocable con el estado inicial del
        grafo (`create_initial_state`).

    Raises:
        ValueError: Si una herramienta de propuesta no expone una función
            síncrona que pueda delegarse.
    """
    capture = _ProposalCapture()
    wrapped_tools = [
        _capture_proposal_tool(tool, capture) for tool in proposal_tools
    ]
    agent = create_inventory_agent(
        model=model,
        tools=[*inventory_tools, *wrapped_tools],
    )
    builder: StateGraph = StateGraph(GraphState)
    builder.add_node(SUPERVISOR_NODE, create_supervisor())
    builder.add_node(
        INVENTORY_AGENT_NODE,
        _create_agent_node(agent, capture),
    )
    builder.add_edge(START, SUPERVISOR_NODE)
    builder.add_edge(INVENTORY_AGENT_NODE, END)

    return builder.compile()


def _capture_proposal_tool(
    tool: BaseTool,
    capture: _ProposalCapture,
) -> BaseTool:
    """
    Envuelve una herramienta de propuesta para capturar su propuesta (T022).

    La envoltura conserva el nombre, la descripción, el esquema de entrada, el
    modo de respuesta y el retorno directo de la herramienta original, y delega
    la ejecución en la función original: una única ejecución, sin duplicar
    reglas de negocio ni la semántica de errores del `ToolNode`.

    Args:
        tool: Herramienta de propuesta original.
        capture: Contenedor donde se registra la propuesta devuelta.

    Returns:
        La herramienta envuelta, con el mismo contrato de invocación.

    Raises:
        ValueError: Si la herramienta original no expone una función síncrona
            que pueda delegarse.
    """
    source = tool.func
    if source is None:
        raise ValueError(
            f"La herramienta '{tool.name}' no expone una función síncrona que "
            "pueda envolverse."
        )

    def invoke_and_capture(**arguments: Any) -> Proposal:
        """Ejecuta la herramienta original y registra la propuesta devuelta."""
        proposal = source(**arguments)
        capture.record(proposal)

        return proposal

    return StructuredTool.from_function(
        func=invoke_and_capture,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        return_direct=tool.return_direct,
        response_format=tool.response_format,
    )


def _create_agent_node(
    agent: CompiledStateGraph,
    capture: _ProposalCapture,
) -> Callable[[GraphState], dict[str, Any]]:
    """
    Crea el nodo que invoca el agente y hace el puente de estado (T022).

    Args:
        agent: Grafo del Inventory Agent (T021) ya construido.
        capture: Contenedor de las propuestas capturadas por las envolturas.

    Returns:
        El nodo del grafo, que devuelve la respuesta y la propuesta capturada.
    """
    def run_inventory_agent(state: GraphState) -> dict[str, Any]:
        """
        Invoca el Inventory Agent y traduce su resultado al estado (RF-05).

        La respuesta es el contenido del último `AIMessage` del agente y la
        propuesta pendiente es la última capturada en esta invocación: el nodo
        no confirma nada ni marca ninguna propuesta como ejecutada.

        Args:
            state: Estado del grafo, con el mensaje del usuario del turno.

        Returns:
            La actualización del estado con la respuesta y la propuesta.
        """
        capture.clear()
        result = agent.invoke(
            {"messages": [HumanMessage(content=state["user_message"])]}
        )

        return {
            "response": _last_response_text(result["messages"]),
            "pending_proposal": capture.last,
        }

    return run_inventory_agent


def _last_response_text(messages: Sequence[BaseMessage]) -> str | None:
    """
    Devuelve el contenido textual del último `AIMessage` (T022).

    Si el modelo devolviera su contenido como bloques estructurados, se
    conserva su representación textual, porque `response` es un campo de texto.
    Sin ninguna respuesta del modelo, devuelve `None`.

    Args:
        messages: Mensajes que produjo el agente en la invocación.

    Returns:
        El texto de la última respuesta del modelo, o `None` si no hay ninguna.
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return (
                message.content
                if isinstance(message.content, str)
                else str(message.content)
            )

    return None
