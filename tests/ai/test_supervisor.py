"""Pruebas del Supervisor y del grafo del asistente (T022).

Verifican el flujo `START → supervisor → inventory_agent → END` sobre el grafo
real de LangGraph con un modelo falso determinista y herramientas reales sobre
SQLite en memoria: la derivación determinista del Supervisor, la consulta con
datos reales, el puente de estado (respuesta textual y propuesta capturada como
objeto `Proposal`), la ausencia de escrituras y la semántica de errores
heredada de T021. Cubren además el contexto conversacional de T063: la
conversación reciente se traduce a `HumanMessage`/`AIMessage` y el mensaje
actual se envía una sola vez, sin duplicarse con el historial.

Queda fuera de T022 y corresponde a T023, T024 y T025: la interfaz de
conversación, la presentación de propuestas y su confirmación explícita desde
la UI, y la validación end-to-end RF-01 a RF-07. Tampoco se prueba aquí la
extracción de la propuesta hacia un registro persistente ni ningún camino de
confirmación, que este grafo no ofrece.
"""

import inspect
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatResult
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import Field

from app.agents import supervisor as supervisor_module
from app.agents.supervisor import (
    INVENTORY_AGENT_NODE,
    create_supervisor,
)
from app.graph import workflow as workflow_module
from app.graph.state import GraphState, create_initial_state
from app.graph.workflow import build_workflow
from app.infrastructure.sqlite.connection import (
    IN_MEMORY_DATABASE,
    transaction,
)
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqlitePriceHistoryRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.schemas.proposals import (
    LooseEntryPayload,
    Proposal,
    ProposalAction,
    ProposalStatus,
)
from app.services.inventory import InventoryService
from app.services.movements import MovementService
from app.services.ports import (
    PresentationRecord,
    ProductRecord,
    RecordNotFoundError,
)
from app.services.presentations import PresentationService
from app.services.products import (
    InitialClosedStock,
    ProductRegistration,
    ProductService,
)
from app.services.proposals import ProposalService
from app.tools.inventory_tools import create_inventory_tools
from app.tools.proposal_tools import create_proposal_tools

EXPECTED_INVENTORY_TOOLS: frozenset[str] = frozenset(
    {
        "find_products_by_name",
        "find_product_by_sku",
        "get_product_stock",
        "get_product_presentations",
        "get_low_stock_products",
        "get_replenishment_products",
        "get_movement_history",
    }
)
"""Las siete herramientas de consulta, que el flujo no envuelve."""

EXPECTED_PROPOSAL_TOOLS: frozenset[str] = frozenset(
    {
        "propose_product_registration",
        "propose_product_update",
        "propose_presentation_registration",
        "propose_loose_entry",
        "propose_closed_entry",
        "propose_loose_exit",
        "propose_closed_exit",
        "propose_aperture",
        "propose_compensation",
        "propose_price_update",
        "edit_proposal",
    }
)
"""Las once herramientas de propuesta, que el flujo envuelve."""

EXPECTED_TOOL_NAMES: frozenset[str] = (
    EXPECTED_INVENTORY_TOOLS | EXPECTED_PROPOSAL_TOOLS
)
"""Las 18 herramientas que el agente enlaza en el flujo."""


class ScriptedChatModel(FakeMessagesListChatModel):
    """
    Modelo falso con un guion fijo de respuestas (T022).

    `FakeMessagesListChatModel` no implementa `bind_tools`, así que esta
    subclase lo anula devolviendo el propio modelo y registrando las
    herramientas enlazadas. Además registra los mensajes de cada llamada, de
    modo que las pruebas puedan inspeccionar el historial que el agente envía
    al modelo (incluidos los mensajes de herramienta).
    """

    received: list[list[BaseMessage]] = Field(default_factory=list)
    """Mensajes recibidos en cada llamada al modelo, en orden."""

    bound_tools: list[Any] = Field(default_factory=list)
    """Herramientas enlazadas por el agente en su última llamada."""

    def bind_tools(
        self,
        tools: Sequence[Any],
        **kwargs: Any,
    ) -> "ScriptedChatModel":
        """Registra las herramientas enlazadas y devuelve el propio modelo."""
        self.bound_tools = list(tools)

        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Registra los mensajes recibidos y responde con el guion."""
        self.received.append(list(messages))

        return super()._generate(
            messages,
            stop=stop,
            run_manager=run_manager,
            **kwargs,
        )


@dataclass(frozen=True)
class Catalog:
    """Inventario controlado de las pruebas (datos de prueba, no de la app)."""

    product: ProductRecord
    box: PresentationRecord

    @property
    def total_equivalent(self) -> int:
        """Total equivalente del producto, derivado de sus saldos reales."""
        return (
            self.product.loose_units
            + self.box.closed_stock * self.box.factor
        )


def tool_call_message(
    name: str,
    arguments: dict[str, Any],
    call_id: str,
) -> AIMessage:
    """Construye la respuesta del modelo que solicita una herramienta."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": arguments,
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def run_workflow(
    *,
    responses: Sequence[BaseMessage],
    user_message: str,
    inventory_tools: Sequence[BaseTool],
    proposal_tools: Sequence[BaseTool],
    conversation: tuple[tuple[str, str], ...] = (),
) -> tuple[dict[str, Any], ScriptedChatModel]:
    """Compila el grafo, lo invoca con el guion y devuelve el modelo."""
    model = ScriptedChatModel(responses=list(responses))
    graph = build_workflow(
        model=model,
        inventory_tools=inventory_tools,
        proposal_tools=proposal_tools,
    )

    return (
        graph.invoke(
            create_initial_state(user_message, conversation=conversation)
        ),
        model,
    )


def tool_messages_of(model: ScriptedChatModel) -> list[ToolMessage]:
    """Devuelve los mensajes de herramienta del último estado del modelo."""
    if not model.received:
        return []

    return [
        message
        for message in model.received[-1]
        if isinstance(message, ToolMessage)
    ]


def snapshot(
    connection: sqlite3.Connection,
) -> dict[str, list[tuple[Any, ...]]]:
    """Captura el contenido de las tablas, ordenado, para comparar."""
    tables = ("products", "presentations", "movements", "proposals")

    return {
        table: [
            tuple(row)
            for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY id"
            ).fetchall()
        ]
        for table in tables
    }


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    """
    Abre una base en memoria que puede usarse desde varios hilos (T022).

    El nodo de herramientas de LangGraph ejecuta cada herramienta en un hilo de
    trabajo, así que la conexión se abre con `check_same_thread=False` como
    configuración de prueba; el módulo `sqlite3` está compilado en modo
    serializado (`sqlite3.threadsafety == 3`), de modo que compartir una única
    conexión es seguro. El resto de la configuración replica la conexión del
    proyecto (`app/infrastructure/sqlite/connection.py`), que no expone ese
    ajuste. Cada prueba abre su propia base en memoria: no toca datos
    persistentes.
    """
    assert sqlite3.threadsafety == 3
    opened_connection = sqlite3.connect(
        IN_MEMORY_DATABASE,
        isolation_level=None,
        check_same_thread=False,
    )
    opened_connection.row_factory = sqlite3.Row
    opened_connection.execute("PRAGMA foreign_keys = ON")
    create_schema(opened_connection)
    try:
        yield opened_connection
    finally:
        opened_connection.close()


@pytest.fixture
def products(connection: sqlite3.Connection) -> SqliteProductRepository:
    """Repositorio de productos sobre la base en memoria."""
    return SqliteProductRepository(connection)


@pytest.fixture
def presentations(
    connection: sqlite3.Connection,
) -> SqlitePresentationRepository:
    """Repositorio de presentaciones sobre la base en memoria."""
    return SqlitePresentationRepository(connection)


@pytest.fixture
def movements(connection: sqlite3.Connection) -> SqliteMovementRepository:
    """Repositorio de movimientos sobre la base en memoria."""
    return SqliteMovementRepository(connection)


@pytest.fixture
def product_service(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> ProductService:
    """Servicio de productos con una transacción sobre la base en memoria."""
    return ProductService(
        products=products,
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )


@pytest.fixture
def presentation_service(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> PresentationService:
    """Servicio de presentaciones sobre la base en memoria."""
    return PresentationService(
        products=products,
        presentations=presentations,
        movements=movements,
        price_history=SqlitePriceHistoryRepository(connection),
        transaction=partial(transaction, connection),
    )


@pytest.fixture
def movement_service(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> MovementService:
    """Servicio de movimientos con una transacción sobre la base en memoria."""
    return MovementService(
        products=products,
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )


@pytest.fixture
def inventory_service(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> InventoryService:
    """Servicio de consultas del inventario sobre la base en memoria."""
    return InventoryService(
        products=products,
        presentations=presentations,
        movements=movements,
    )


@pytest.fixture
def proposal_service(
    product_service: ProductService,
    presentation_service: PresentationService,
    movement_service: MovementService,
) -> ProposalService:
    """Servicio de propuestas con sus servicios de destino inyectados."""
    return ProposalService(
        products=product_service,
        presentations=presentation_service,
        movements=movement_service,
    )


@pytest.fixture
def inventory_tools(
    inventory_service: InventoryService,
) -> list[BaseTool]:
    """Las siete herramientas de consulta, tal como las recibe el flujo."""
    return list(create_inventory_tools(inventory_service))


@pytest.fixture
def proposal_tools(
    proposal_service: ProposalService,
) -> list[BaseTool]:
    """Las once herramientas de propuesta, tal como las recibe el flujo."""
    return list(create_proposal_tools(proposal_service))


@pytest.fixture
def catalog(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> Catalog:
    """Registra el inventario controlado con los servicios reales."""
    stored = product_service.register(
        ProductRegistration(
            sku="SKU-1",
            name="Producto",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            initial_loose_units=4,
            presentations=(
                InitialClosedStock(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("600"),
                    closed_stock=2,
                ),
            ),
        )
    )
    _, box = presentations.list_by_product(stored.id)

    return Catalog(product=stored, box=box)


# --- Estado inicial (D1) ---


def test_create_initial_state_starts_with_the_response_in_none() -> None:
    """Verifica el campo `response` del estado inicial (D1)."""
    state = create_initial_state("Consulta de stock")

    assert set(state.keys()) == {
        "user_message",
        "conversation",
        "pending_proposal",
        "execution_result",
        "response",
    }
    assert state["user_message"] == "Consulta de stock"
    assert state["conversation"] == ()
    assert state["response"] is None
    assert state["pending_proposal"] is None
    assert state["execution_result"] is None


# --- Contexto conversacional (T063) ---


def test_conversation_context_avoids_duplicating_the_current_message(
    inventory_tools: list[BaseTool],
    proposal_tools: list[BaseTool],
) -> None:
    """
    Verifica el historial previo y el mensaje actual enviados una sola vez.

    Con una consulta y su aclaración en la conversación, «sí, es ese» llega al
    modelo después de ellas y aparece una única vez en lo enviado (T063).
    """
    conversation = (
        ("human", "Busca Coca"),
        ("ai", "¿Te refieres a coca-cola-3L?"),
    )
    state, model = run_workflow(
        responses=[AIMessage(content="Consulto ese producto.")],
        user_message="sí, es ese",
        inventory_tools=inventory_tools,
        proposal_tools=proposal_tools,
        conversation=conversation,
    )
    context = [
        (type(message), message.content)
        for message in model.received[-1]
        if isinstance(message, (HumanMessage, AIMessage))
    ]

    assert context == [
        (HumanMessage, "Busca Coca"),
        (AIMessage, "¿Te refieres a coca-cola-3L?"),
        (HumanMessage, "sí, es ese"),
    ]
    assert [content for _, content in context].count("sí, es ese") == 1
    assert state["conversation"] == conversation
    assert state["response"] == "Consulto ese producto."


def test_single_turn_still_has_no_prior_conversation(
    inventory_tools: list[BaseTool],
    proposal_tools: list[BaseTool],
) -> None:
    """Verifica que un turno único envía solo el mensaje actual (T063)."""
    _, model = run_workflow(
        responses=[AIMessage(content="Hola.")],
        user_message="Hola",
        inventory_tools=inventory_tools,
        proposal_tools=proposal_tools,
    )
    context = [
        message.content
        for message in model.received[-1]
        if isinstance(message, (HumanMessage, AIMessage))
    ]

    assert context == ["Hola"]


# --- Supervisor determinista (D2) ---


def test_the_supervisor_routes_always_to_the_inventory_agent() -> None:
    """Verifica que el Supervisor deriva siempre al Inventory Agent (D2)."""
    supervisor = create_supervisor()
    state = create_initial_state("Consulta de stock")

    first = supervisor(state)
    second = supervisor(state)

    assert isinstance(first, Command)
    assert first.goto == INVENTORY_AGENT_NODE
    assert first.update is None
    assert first == second
    assert state["pending_proposal"] is None


# --- Grafo compilado y puente de estado ---


def test_the_workflow_compiles_with_the_expected_nodes(
    inventory_tools: list[BaseTool],
    proposal_tools: list[BaseTool],
) -> None:
    """Verifica el grafo compilado y sus nodos (D2)."""
    model = ScriptedChatModel(responses=[AIMessage(content="Sin novedades.")])
    graph = build_workflow(
        model=model,
        inventory_tools=inventory_tools,
        proposal_tools=proposal_tools,
    )

    assert isinstance(graph, CompiledStateGraph)
    assert {"supervisor", "inventory_agent"} <= set(
        graph.get_graph().nodes
    )

    state = graph.invoke(create_initial_state("Hola"))

    assert state["user_message"] == "Hola"
    assert state["response"] == "Sin novedades."
    assert state["pending_proposal"] is None
    assert state["execution_result"] is None
    assert model.received != []


def test_the_agent_receives_the_eighteen_tools_with_their_contracts(
    inventory_tools: list[BaseTool],
    proposal_tools: list[BaseTool],
) -> None:
    """Verifica las 18 herramientas y los contratos de la envoltura."""
    originals = {
        tool.name: tool for tool in [*inventory_tools, *proposal_tools]
    }

    _, model = run_workflow(
        responses=[AIMessage(content="Listo.")],
        user_message="Hola",
        inventory_tools=inventory_tools,
        proposal_tools=proposal_tools,
    )
    bound = {tool.name: tool for tool in model.bound_tools}

    assert len(EXPECTED_TOOL_NAMES) == 18
    assert set(bound) == set(originals) == EXPECTED_TOOL_NAMES

    for name in EXPECTED_INVENTORY_TOOLS:
        assert bound[name] is originals[name]

    for name in EXPECTED_PROPOSAL_TOOLS:
        assert bound[name] is not originals[name]
        assert bound[name].name == name
        assert bound[name].description == originals[name].description
        assert bound[name].args_schema is originals[name].args_schema


# --- Consulta con datos reales ---


def test_a_consultation_uses_the_real_data(
    catalog: Catalog,
    inventory_tools: list[BaseTool],
    proposal_tools: list[BaseTool],
) -> None:
    """Verifica que la consulta del flujo devuelve los datos reales."""
    state, model = run_workflow(
        responses=[
            tool_call_message(
                "get_product_stock",
                {"product_id": catalog.product.id},
                "call-1",
            ),
            AIMessage(content="El stock está informado."),
        ],
        user_message="¿Cuánto stock hay?",
        inventory_tools=inventory_tools,
        proposal_tools=proposal_tools,
    )
    messages = tool_messages_of(model)
    content = str(messages[0].content)

    assert len(messages) == 1
    assert messages[0].name == "get_product_stock"
    assert messages[0].status == "success"
    assert "SKU-1" in content
    assert catalog.box.name in content
    assert str(catalog.total_equivalent) in content
    assert state["response"] == "El stock está informado."
    assert state["pending_proposal"] is None


# --- Propuesta puenteada como objeto (alternativa 1) ---


def test_a_proposal_is_bridged_as_a_proposal_object_without_writing(
    connection: sqlite3.Connection,
    catalog: Catalog,
    inventory_tools: list[BaseTool],
    proposal_tools: list[BaseTool],
) -> None:
    """Verifica la propuesta capturada como objeto y sin escrituras (T022)."""
    before = snapshot(connection)
    changes_before = connection.total_changes
    state, _ = run_workflow(
        responses=[
            tool_call_message(
                "propose_loose_entry",
                {
                    "product_id": catalog.product.id,
                    "quantity": 3,
                    "reason": "Compra",
                },
                "call-1",
            ),
            AIMessage(content="Preparé la propuesta."),
        ],
        user_message="Propone una entrada suelta",
        inventory_tools=inventory_tools,
        proposal_tools=proposal_tools,
    )
    proposal = state["pending_proposal"]

    assert isinstance(proposal, Proposal)
    assert proposal.status is ProposalStatus.PENDING
    assert proposal.action is ProposalAction.LOOSE_ENTRY
    assert isinstance(proposal.payload, LooseEntryPayload)
    assert proposal.payload.product_id == catalog.product.id
    assert proposal.payload.quantity == 3
    assert state["response"] == "Preparé la propuesta."
    assert state["execution_result"] is None

    assert snapshot(connection) == before
    assert snapshot(connection)["proposals"] == []
    assert connection.total_changes == changes_before


def test_the_bridge_keeps_the_last_proposal_prepared(
    catalog: Catalog,
    inventory_tools: list[BaseTool],
    proposal_tools: list[BaseTool],
) -> None:
    """Verifica que el puente conserva solo la última propuesta preparada."""
    state, model = run_workflow(
        responses=[
            tool_call_message(
                "propose_loose_entry",
                {
                    "product_id": catalog.product.id,
                    "quantity": 1,
                    "reason": "Compra",
                },
                "call-1",
            ),
            tool_call_message(
                "propose_loose_entry",
                {
                    "product_id": catalog.product.id,
                    "quantity": 2,
                    "reason": "Compra",
                },
                "call-2",
            ),
            AIMessage(content="Preparé dos propuestas."),
        ],
        user_message="Propone dos entradas",
        inventory_tools=inventory_tools,
        proposal_tools=proposal_tools,
    )
    proposal = state["pending_proposal"]
    messages = tool_messages_of(model)

    assert len(messages) == 2
    assert isinstance(proposal, Proposal)
    assert proposal.payload.quantity == 2
    assert state["response"] == "Preparé dos propuestas."
    assert state["execution_result"] is None


# --- Errores ---


def test_a_typed_error_aborts_the_invocation(
    inventory_tools: list[BaseTool],
    proposal_tools: list[BaseTool],
) -> None:
    """Verifica que un error tipado del servicio no se convierte en éxito."""
    model = ScriptedChatModel(
        responses=[
            tool_call_message(
                "get_product_stock", {"product_id": 999}, "call-1"
            )
        ]
    )
    graph = build_workflow(
        model=model,
        inventory_tools=inventory_tools,
        proposal_tools=proposal_tools,
    )

    with pytest.raises(RecordNotFoundError):
        graph.invoke(
            create_initial_state("¿Cuánto stock hay?"),
            config={"recursion_limit": 10},
        )


def test_invalid_arguments_return_an_error_tool_message_without_proposal(
    catalog: Catalog,
    inventory_tools: list[BaseTool],
    proposal_tools: list[BaseTool],
) -> None:
    """Verifica que una llamada inválida no prepara ninguna propuesta."""
    state, model = run_workflow(
        responses=[
            tool_call_message(
                "propose_loose_entry",
                {"product_id": catalog.product.id},
                "call-1",
            ),
            AIMessage(content="No pude prepararla."),
        ],
        user_message="Propone una entrada",
        inventory_tools=inventory_tools,
        proposal_tools=proposal_tools,
    )
    messages = tool_messages_of(model)

    assert len(messages) == 1
    assert messages[0].name == "propose_loose_entry"
    assert messages[0].status == "error"
    assert state["pending_proposal"] is None
    assert state["response"] == "No pude prepararla."
    assert state["execution_result"] is None


# --- Guardas del módulo ---


def test_the_graph_modules_do_not_import_infrastructure_or_the_llm() -> None:
    """Verifica que el grafo no importa persistencia, agentes ni LLM."""
    forbidden = (
        "app.infrastructure",
        "sqlite",
        "repositories",
        "app.services.llm",
        "get_llm",
        "langgraph.prebuilt",
        "ChatDeepSeek",
        "DEEPSEEK",
    )
    for module in (workflow_module, supervisor_module):
        source = inspect.getsource(module)
        for token in forbidden:
            assert token not in source, (
                f"El módulo {module.__name__} no debe contener `{token}`."
            )

        assert "confirm(" not in source
        assert ".confirm" not in source
