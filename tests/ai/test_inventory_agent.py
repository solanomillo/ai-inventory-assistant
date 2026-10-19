"""Pruebas del Inventory Agent con un modelo falso (T021).

Verifican el contrato de `create_inventory_agent` sobre LangChain y LangGraph
con un modelo falso determinista y herramientas reales sobre SQLite en memoria:
el grafo compilado se invoca con `{"messages": [...]}`, sin red, sin DeepSeek y
sin agentes reales.

Cubren la construcción del agente, el conjunto de herramientas que recibe (18,
ninguna de confirmación), una consulta que ejecuta una herramienta real y
devuelve datos reales, una propuesta que no modifica la base, la propagación de
los errores tipados y las guardas de dependencias del módulo.

Queda fuera de T021 y corresponde a T022: la derivación desde el Supervisor, el
grafo de `app/graph/workflow.py`, la extracción de la propuesta pendiente y de
la respuesta textual hacia `GraphState`, y el flujo completo con la interfaz.
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
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatResult
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from pydantic import Field

from app.agents import inventory as inventory_agent
from app.agents.inventory import create_inventory_agent
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
from app.prompts.prompts import INVENTORY_AGENT_PROMPT
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
from app.services.proposals import (
    ProposalAlreadyConfirmedError,
    ProposalService,
)
from app.tools.inventory_tools import create_inventory_tools
from app.tools.proposal_tools import create_proposal_tools

EXPECTED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "find_products_by_name",
        "find_product_by_sku",
        "get_product_stock",
        "get_product_presentations",
        "get_low_stock_products",
        "get_replenishment_products",
        "get_movement_history",
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
"""Las 18 herramientas esperadas: 7 de consulta y 11 de propuesta."""


class ScriptedChatModel(FakeMessagesListChatModel):
    """
    Modelo falso con un guion fijo de respuestas (T021).

    `FakeMessagesListChatModel` no implementa `bind_tools`, así que esta
    subclase lo anula devolviendo el propio modelo: el guion ya está fijado y
    las herramientas enlazadas no alteran la respuesta. Además registra los
    mensajes de cada llamada, de modo que la prueba pueda verificar qué le
    envía el agente al modelo.
    """

    received: list[list[BaseMessage]] = Field(default_factory=list)
    """Mensajes recibidos en cada llamada al modelo, en orden."""

    def bind_tools(
        self,
        tools: Sequence[Any],
        **kwargs: Any,
    ) -> "ScriptedChatModel":
        """Devuelve el modelo sin modificarlo: el guion ya está fijado."""
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


def build_agent(
    responses: Sequence[BaseMessage],
    tools: Sequence[BaseTool],
) -> CompiledStateGraph:
    """Construye el agente con el guion y las herramientas indicados."""
    return create_inventory_agent(
        model=ScriptedChatModel(responses=list(responses)),
        tools=tools,
    )


def tool_messages_of(result: dict[str, Any]) -> list[ToolMessage]:
    """Devuelve los mensajes de herramienta del resultado del agente."""
    return [
        message
        for message in result["messages"]
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
    Abre una base en memoria que puede usarse desde varios hilos (T021).

    El nodo de herramientas de LangGraph ejecuta cada herramienta en un hilo de
    trabajo, así que la conexión se abre con `check_same_thread=False`; el
    módulo `sqlite3` está compilado en modo serializado
    (`sqlite3.threadsafety == 3`), de modo que compartir una única conexión es
    seguro. El resto de la configuración replica la conexión del proyecto
    (`app/infrastructure/sqlite/connection.py`), que no expone ese ajuste.
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
def agent_tools(
    inventory_service: InventoryService,
    proposal_service: ProposalService,
) -> list[BaseTool]:
    """Las herramientas reales del agente: 7 de consulta y 10 de propuesta."""
    return [
        *create_inventory_tools(inventory_service),
        *create_proposal_tools(proposal_service),
    ]


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


# --- Construcción del agente ---


def test_the_factory_returns_an_invocable_compiled_graph(
    agent_tools: list[BaseTool],
) -> None:
    """Verifica que el agente devuelve un grafo compilado e invocable."""
    agent = build_agent([AIMessage(content="Sin novedades.")], agent_tools)

    assert isinstance(agent, CompiledStateGraph)

    result = agent.invoke({"messages": [HumanMessage(content="Hola")]})

    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == "Sin novedades."


def test_the_agent_receives_the_eighteen_tools_without_confirmation(
    agent_tools: list[BaseTool],
) -> None:
    """Verifica el conjunto de herramientas que recibe el agente."""
    names = [tool.name for tool in agent_tools]

    assert len(EXPECTED_TOOL_NAMES) == 18
    assert len(names) == len(set(names)) == 18
    assert set(names) == EXPECTED_TOOL_NAMES
    assert [name for name in names if "confirm" in name] == []


def test_the_agent_binds_the_received_tools_to_its_tool_node(
    agent_tools: list[BaseTool],
) -> None:
    """Verifica que el grafo enlaza exactamente las herramientas recibidas."""
    agent = build_agent([AIMessage(content="Listo.")], agent_tools)

    bound = agent.nodes["tools"].bound

    assert set(bound.tools_by_name) == EXPECTED_TOOL_NAMES


def test_the_agent_sends_the_inventory_prompt_to_the_model(
    agent_tools: list[BaseTool],
) -> None:
    """Verifica el prompt de sistema que el agente envía al modelo."""
    model = ScriptedChatModel(responses=[AIMessage(content="Listo.")])
    agent = create_inventory_agent(model=model, tools=agent_tools)

    agent.invoke({"messages": [HumanMessage(content="Hola")]})

    system_messages = [
        message
        for message in model.received[0]
        if isinstance(message, SystemMessage)
    ]

    assert len(system_messages) == 1
    assert system_messages[0].content == INVENTORY_AGENT_PROMPT
    assert agent.name == "inventory_agent"


# --- Consulta con datos reales ---


def test_a_consultation_executes_the_real_tool_with_real_data(
    agent_tools: list[BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que la consulta devuelve los datos reales del inventario."""
    agent = build_agent(
        [
            tool_call_message(
                "get_product_stock",
                {"product_id": catalog.product.id},
                "call-1",
            ),
            AIMessage(content="Consulta resuelta."),
        ],
        agent_tools,
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="¿Cuánto stock hay?")]}
    )
    messages = tool_messages_of(result)
    content = str(messages[0].content)

    assert len(messages) == 1
    assert messages[0].name == "get_product_stock"
    assert messages[0].status == "success"
    assert "SKU-1" in content
    assert catalog.box.name in content
    assert str(catalog.total_equivalent) in content
    assert result["messages"][-1].content == "Consulta resuelta."


# --- Propuesta sin efectos ---


def test_a_proposal_does_not_modify_the_database(
    connection: sqlite3.Connection,
    agent_tools: list[BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que una propuesta del agente no cambia ningún dato."""
    before = snapshot(connection)
    changes_before = connection.total_changes
    agent = build_agent(
        [
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
        agent_tools,
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="Propone una entrada")]}
    )
    messages = tool_messages_of(result)
    content = str(messages[0].content)

    assert len(messages) == 1
    assert messages[0].name == "propose_loose_entry"
    assert messages[0].status == "success"
    assert "entrada_suelta" in content
    assert "pendiente" in content

    assert snapshot(connection) == before
    assert snapshot(connection)["proposals"] == []
    assert connection.total_changes == changes_before


# --- Errores tipados ---


def test_a_missing_record_aborts_the_invocation(
    agent_tools: list[BaseTool],
) -> None:
    """Verifica que un registro inexistente aborta la invocación."""
    agent = build_agent(
        [
            tool_call_message(
                "get_product_stock", {"product_id": 999}, "call-1"
            )
        ],
        agent_tools,
    )

    with pytest.raises(RecordNotFoundError):
        agent.invoke(
            {"messages": [HumanMessage(content="¿Cuánto stock hay?")]},
            config={"recursion_limit": 10},
        )


def test_editing_a_confirmed_proposal_aborts_the_invocation(
    agent_tools: list[BaseTool],
) -> None:
    """Verifica que editar una propuesta confirmada aborta la invocación."""
    payload = {
        "action": "entrada_suelta",
        "product_id": 1,
        "quantity": 2,
        "reason": "Compra",
    }
    agent = build_agent(
        [
            tool_call_message(
                "edit_proposal",
                {
                    "proposal": {
                        "action": "entrada_suelta",
                        "payload": payload,
                        "status": "confirmada",
                    },
                    "payload": payload,
                },
                "call-1",
            )
        ],
        agent_tools,
    )

    with pytest.raises(ProposalAlreadyConfirmedError):
        agent.invoke(
            {"messages": [HumanMessage(content="Edita la propuesta")]},
            config={"recursion_limit": 10},
        )


def test_invalid_tool_arguments_return_an_error_tool_message(
    agent_tools: list[BaseTool],
) -> None:
    """Verifica que una llamada inválida vuelve como mensaje de error."""
    agent = build_agent(
        [
            tool_call_message(
                "propose_loose_entry",
                {"product_id": 1},
                "call-1",
            ),
            AIMessage(content="No pude preparar la propuesta."),
        ],
        agent_tools,
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="Propone una entrada")]}
    )
    messages = tool_messages_of(result)

    assert len(messages) == 1
    assert messages[0].name == "propose_loose_entry"
    assert messages[0].status == "error"
    assert "propose_loose_entry" in str(messages[0].content)
    assert result["messages"][-1].content == (
        "No pude preparar la propuesta."
    )


# --- Guardas del módulo ---


def test_the_agent_module_exposes_only_its_factory() -> None:
    """Verifica que el módulo solo define la construcción del agente."""
    defined = {
        name
        for name, value in vars(inventory_agent).items()
        if not name.startswith("_")
        and getattr(value, "__module__", None) == inventory_agent.__name__
    }

    assert defined == {"create_inventory_agent"}


def test_the_agent_module_does_not_import_data_access_or_the_llm() -> None:
    """Verifica que el módulo no importa persistencia, agentes ni LLM."""
    source = inspect.getsource(inventory_agent)

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
    for token in forbidden:
        assert token not in source, f"El módulo no debe contener `{token}`."

    assert "confirm(" not in source
    assert ".confirm" not in source
