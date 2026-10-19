"""Pruebas de la interfaz de conversación (T023, T024 y T035).

Verifican la lógica extraíble del render —composición del asistente, respuesta
del grafo y descripción de las propuestas— y la aplicación Streamlit completa
con `AppTest`, con un modelo falso inyectado y herramientas reales sobre SQLite
en memoria: sin red, sin DeepSeek y sin tocar datos persistentes.

Cubren los cuatro criterios de aceptación de T023 con datos reales: stock
suelto, cerrado y total equivalente; stock bajo; reposición; e historial de
movimientos.

Cubren además la confirmación de propuestas desde la interfaz (T024: control de
UI, sin pasar por el LLM, con los resultados reales) y la presentación del
cambio de precio de T035 sobre RF-08: presentación afectada, precio vigente,
precio nuevo e historial de precios con datos reales.

Cubren la composición dual de T039 sobre RF-09: la lectura de la sesión —una
conexión y un conjunto de servicios por sesión— se compone y se consulta sin
modelo, la aplicación informa la falta de configuración del asistente sin
abandonar la lectura, y la lectura no abre conexiones propias ni escribe. El
asistente reutiliza el contenedor de lectura recibido —sin abrir una conexión
ni componer servicios adicionales— cuando se le pasa `session`.

Cubren la navegación lateral de T041 sobre RF-09: las siete secciones en el
sidebar —con «Asistente» por defecto—, el enrutado de una sola sección, la
vista mínima de «Propuestas pendientes» sin confirmación, el aviso honesto de
las secciones todavía no implementadas y la conservación del punto de entrada
único, sin multipágina.

Cubren la sección de inicio de T043 sobre RF-09: las dos listas de alertas con
nombre, SKU, total equivalente y faltante; los conteos de activos e inactivos
derivados de una única lectura pública del catálogo; el orden del servicio; la
exclusión de los inactivos de las alertas; el cero real; los estados vacíos
independientes de cada lista; el catálogo vacío sin conteos cero; la ausencia
de escrituras; y el funcionamiento sin modelo configurado.

Cubren la sección de productos de T044 sobre RF-09: los siete campos del
listado, el catálogo con activos e inactivos, la búsqueda exacta por nombre y
por SKU con los métodos públicos correctos, la entrada vacía que no busca, los
mensajes diferenciados de búsqueda y filtro, el orden del filtro respecto de la
búsqueda y de las presentaciones, el stock cerrado sumado, el orden del
servicio, el aviso de no paginación con el número real, la ausencia de
escrituras, el funcionamiento sin modelo y la separación de la vista.

Cubren el detalle de producto de T045 sobre RF-09: las presentaciones con
factor y stock cerrado, las unidades sueltas y el total equivalente derivado,
los movimientos con fecha local y campos opcionales, el historial de precios
en orden cronológico, la consulta de un producto inactivo, los vacíos
diferenciados, los avisos C8 sin truncamiento, una lectura de cada servicio sin
escrituras, el funcionamiento sin modelo y el error de lectura que no se
presenta como historial vacío.

Queda fuera de este alcance y corresponde a T025: la validación end-to-end de
RF-01 a RF-07.
"""

import ast
import re
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import Field
from streamlit.testing.v1 import AppTest

from app import config
from app.domain.movements import MovementKind
from app.infrastructure.sqlite.connection import connect, transaction
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqlitePriceHistoryRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.schemas.proposals import Proposal, ProposalStatus
from app.services import llm as llm_module
from app.services.inventory import InventoryService
from app.services.llm import LLMConfigurationError
from app.services.movements import MovementService
from app.services.presentations import PresentationService
from app.services.price_history import PriceHistoryService
from app.services.products import (
    InitialClosedStock,
    ProductRegistration,
    ProductService,
)
from app.services.ports import (
    PRICE_CHANGE_EVENT_TYPE,
    MovementRecord,
    PresentationRecord,
    PriceChangeRecord,
    ProductRecord,
    RecordNotFoundError,
)
from app.services.proposals import ProposalService
from app.tools.inventory_tools import create_inventory_tools
from app.ui import session as session_module
from app.ui.formatting import ABSENT_MARKER
from app.ui.main import (
    ALREADY_CONFIRMED_MESSAGE,
    ASSISTANT_KEY,
    CONFIRMATION_ERROR_PREFIX,
    CONFIRM_LABEL,
    NO_PENDING_PROPOSAL_MESSAGE,
    NO_RESPONSE_MESSAGE,
    PENDING_PROPOSAL_NOTE,
    PROCESSING_KEY,
    SECTION_LABELS,
    SECTION_PENDING_MESSAGE,
    Assistant,
    answer,
    build_assistant,
    confirm_pending,
    describe_price_update,
    describe_proposal,
    section_renderers,
)
from app.ui.sections.home import (
    ACTIVE_PRODUCTS_LABEL,
    EMPTY_CATALOG_MESSAGE,
    EMPTY_LOW_STOCK_MESSAGE,
    EMPTY_REPLENISHMENT_MESSAGE,
    HOME_HEADER,
    INACTIVE_PRODUCTS_LABEL,
    LOW_STOCK_HEADER,
    REPLENISHMENT_HEADER,
)
from app.ui.sections.proposals import EMPTY_PROPOSALS_MESSAGE
from app.ui.sections.product_detail import (
    APPLIED_PRICE_LABEL,
    BASE_PRESENTATION_LABEL,
    COST_LABEL,
    DATE_LABEL,
    EMPTY_MOVEMENTS_MESSAGE,
    EMPTY_PRICE_HISTORY_MESSAGE,
    EMPTY_PRESENTATIONS_MESSAGE,
    EVENT_LABEL,
    FACTOR_LABEL,
    INITIAL_STOCK_LABEL,
    MOVEMENTS_HEADER,
    MOVEMENTS_NOTICE_TEMPLATE,
    MOVEMENT_TYPE_LABEL,
    NO_PRODUCT_SELECTED_MESSAGE,
    NO_SELECTION_LABEL,
    PRICE_HISTORY_HEADER,
    PRICE_HISTORY_NOTICE_TEMPLATE,
    PRICE_NEW_LABEL,
    PRICE_PREVIOUS_LABEL,
    PRESENTATION_LABEL,
    PRESENTATIONS_HEADER,
    PRESENTATIONS_NOTICE_TEMPLATE,
    PRODUCT_DETAIL_ERROR_TEMPLATE,
    PRODUCT_DETAIL_HEADER,
    PRODUCT_SELECTOR_LABEL,
    QUANTITY_LABEL,
    REASON_LABEL,
    TOTAL_EQUIVALENT_LABEL,
    YES_TEXT,
    ProductView,
    movement_kind_label,
    product_option_label,
)
from app.ui.sections.price_history import (
    ALL_PRESENTATIONS_LABEL,
    EMPTY_CATALOG_MESSAGE as EMPTY_PRICE_HISTORY_CATALOG_MESSAGE,
    EMPTY_PRESENTATION_HISTORY_MESSAGE,
    EMPTY_PRODUCT_HISTORY_MESSAGE,
    NO_SELECTION_MESSAGE as NO_PRICE_HISTORY_PRODUCT_MESSAGE,
    PRESENTATION_FILTER_LABEL,
    PRICE_HISTORY_ERROR_TEMPLATE,
    PRICE_HISTORY_SECTION_HEADER,
    PRICE_HISTORY_NOTICE_TEMPLATE as PRICE_HISTORY_SECTION_NOTICE_TEMPLATE,
    PRODUCT_FILTER_LABEL as PRICE_HISTORY_PRODUCT_LABEL,
)
from app.ui.sections.movements import (
    ALL_KINDS_LABEL,
    EMPTY_CATALOG_MESSAGE as EMPTY_MOVEMENTS_CATALOG_MESSAGE,
    EMPTY_FILTERED_MOVEMENTS_MESSAGE,
    EMPTY_MOVEMENTS_MESSAGE as EMPTY_MOVEMENTS_HISTORY_MESSAGE,
    END_DATE_LABEL,
    INVALID_PERIOD_MESSAGE,
    KIND_FILTER_LABEL,
    KIND_FILTER_OPTIONS,
    MOVEMENT_KIND_VALUES,
    MOVEMENTS_ERROR_TEMPLATE,
    MOVEMENTS_HEADER as MOVEMENTS_SECTION_HEADER,
    NO_SELECTION_MESSAGE as NO_MOVEMENT_SELECTED_MESSAGE,
    PRODUCT_FILTER_LABEL,
    START_DATE_LABEL,
)
from app.ui.sections.products import (
    ACTIVE_STATUS,
    ACTIVE_STATUS_FILTER,
    ALL_STATUS_FILTER,
    BASE_UNIT_LABEL,
    CLOSED_STOCK_LABEL,
    EMPTY_CATALOG_MESSAGE as EMPTY_PRODUCTS_CATALOG_MESSAGE,
    EMPTY_FILTER_MESSAGE as EMPTY_PRODUCTS_FILTER_MESSAGE,
    EMPTY_SEARCH_MESSAGE as EMPTY_PRODUCTS_SEARCH_MESSAGE,
    INACTIVE_STATUS,
    INACTIVE_STATUS_FILTER,
    LOOSE_UNITS_LABEL,
    MINIMUM_STOCK_LABEL,
    NAME_LABEL,
    NON_PAGINATED_NOTICE_TEMPLATE,
    PRODUCTS_HEADER,
    SEARCH_NOTICE,
    SKU_FIELD,
    SKU_LABEL,
    STATUS_LABEL,
)
from app.ui.session import SESSION_KEY, SessionResources, build_session

APP_PATH: Path = (
    Path(__file__).resolve().parents[2] / "app" / "ui" / "main.py"
)
"""Ruta absoluta de la aplicación Streamlit que se ejecuta con `AppTest`."""

SESSION_MODULE_PATH: Path = (
    Path(__file__).resolve().parents[2] / "app" / "ui" / "session.py"
)
"""Ruta absoluta del módulo que compone la lectura de la sesión (T039)."""

SECTIONS_PATH: Path = APP_PATH.parent / "sections"
"""Directorio de las secciones de la navegación lateral (T041)."""

PROPOSALS_SECTION_PATH: Path = SECTIONS_PATH / "proposals.py"
"""Módulo de la vista de la sección «Propuestas pendientes» (T041)."""

HOME_SECTION_PATH: Path = SECTIONS_PATH / "home.py"
"""Módulo de la vista de la sección «Inicio» (T043)."""

PRODUCTS_SECTION_PATH: Path = SECTIONS_PATH / "products.py"
"""Módulo de la vista de la sección «Productos» (T044)."""

PRODUCT_DETAIL_SECTION_PATH: Path = SECTIONS_PATH / "product_detail.py"
"""Módulo de la vista del detalle de un producto (T045)."""

MOVEMENTS_SECTION_PATH: Path = SECTIONS_PATH / "movements.py"
"""Módulo de la vista de la sección «Movimientos» (T046)."""

PRICE_HISTORY_SECTION_PATH: Path = SECTIONS_PATH / "price_history.py"
"""Módulo de la vista de la sección «Historial de precios» (T047)."""

SECTION_LABELS_EXPECTED: tuple[str, ...] = (
    "Inicio",
    "Productos",
    "Operaciones",
    "Propuestas pendientes",
    "Movimientos",
    "Historial de precios",
    "Asistente",
)
"""Secciones de RF-09 que la navegación lateral debe ofrecer (C1, T041)."""

DEFAULT_SECTION_LABEL: str = "Asistente"
"""Sección que la interfaz muestra al abrirse, sin elegir otra (T041, C20)."""

PENDING_SECTIONS: tuple[str, ...] = ("Operaciones",)
"""Secciones de RF-09 todavía pendientes en la Fase 7 (T041, T047)."""

FORBIDDEN_SECTION_IMPORTS: tuple[str, ...] = (
    "app.infrastructure",
    "app.domain",
    "app.services",
    "app.graph",
    "app.tools",
    "app.ui.main",
    "sqlite3",
)
"""Dependencias que una sección de la interfaz no debe importar (T041, C1)."""

FORBIDDEN_SECTION_CALLS: frozenset[str] = frozenset(
    {
        "get_llm",
        "build_workflow",
        "build_session",
        "open_connection",
        "transaction",
    }
)
"""Llamadas que romperían la separación de las secciones (T041, C1)."""

CRITERIA: tuple[str, ...] = (
    "stock",
    "low_stock",
    "replenishment",
    "history",
)
"""Los cuatro criterios de consulta de T023, con datos reales."""

CRITERIA_MARKERS: dict[str, tuple[str, ...]] = {
    "stock": ("Caja x6", "total_equivalent="),
    "low_stock": ("SKU-1",),
    "replenishment": ("SKU-1",),
    "history": ("Stock inicial",),
}
"""Datos reales que cada criterio debe mostrar en la conversación."""

MISSING_KEY_MESSAGE: str = "La API Key de DeepSeek no está configurada."
"""Mensaje de configuración ausente con el que se prueba el aviso de la app."""


class RelayChatModel(BaseChatModel):
    """
    Modelo falso que solicita herramientas y responde con sus resultados.

    Emite las llamadas indicadas, en orden, y cuando ya no quedan responde con
    el contenido real de los mensajes de herramienta que recibió, de modo que
    la interfaz muestre los datos reales del inventario. Sustituye únicamente
    al proveedor del modelo: las herramientas, los servicios y el grafo son
    reales.
    """

    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    """Llamadas a herramientas que el modelo emite, en orden."""

    received: list[list[BaseMessage]] = Field(default_factory=list)
    """Mensajes recibidos en cada llamada al modelo, en orden."""

    emitted: int = 0
    """Cantidad de llamadas a herramientas ya emitidas."""

    @property
    def _llm_type(self) -> str:
        """Identificador del modelo falso."""
        return "relay-chat-model"

    def bind_tools(
        self,
        tools: Sequence[Any],
        **kwargs: Any,
    ) -> "RelayChatModel":
        """Devuelve el propio modelo: las llamadas del guion ya están fijas."""
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Emite la próxima llamada pendiente o responde con datos reales."""
        self.received.append(list(messages))

        if self.emitted < len(self.tool_calls):
            call = self.tool_calls[self.emitted]
            self.emitted += 1
            message = AIMessage(content="", tool_calls=[call])
        else:
            message = AIMessage(content=self._tool_results(messages))

        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _tool_results(messages: Sequence[BaseMessage]) -> str:
        """Devuelve el contenido real de los mensajes de herramienta."""
        return "\n".join(
            str(message.content)
            for message in messages
            if isinstance(message, ToolMessage)
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


def tool_call(
    name: str,
    arguments: dict[str, Any],
    call_id: str = "call-1",
) -> dict[str, Any]:
    """Construye la llamada a herramienta que emite el modelo falso."""
    return {
        "name": name,
        "args": arguments,
        "id": call_id,
        "type": "tool_call",
    }


def criteria_arguments(
    criterion: str,
    catalog: Catalog,
) -> tuple[dict[str, Any], str]:
    """Devuelve los argumentos y la herramienta de cada criterio."""
    product_id = catalog.product.id
    cases = {
        "stock": ({"product_id": product_id}, "get_product_stock"),
        "low_stock": ({}, "get_low_stock_products"),
        "replenishment": ({}, "get_replenishment_products"),
        "history": ({"product_id": product_id}, "get_movement_history"),
    }

    return cases[criterion]


def make_assistant(
    connection: sqlite3.Connection,
    *calls: dict[str, Any],
) -> Assistant:
    """Compone el asistente con el modelo falso y la conexión de prueba."""
    return build_assistant(
        model=RelayChatModel(tool_calls=list(calls)),
        connection=connection,
    )


def app_for(assistant: Assistant) -> AppTest:
    """Crea la aplicación de prueba con el asistente inyectado en la sesión."""
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.session_state[ASSISTANT_KEY] = assistant

    return app


class CountingInventoryService:
    """
    Espía del servicio de inventario que cuenta las lecturas del catálogo.

    Delega el resto de las lecturas en el servicio real y cuenta las llamadas
    a `list_products()` (T043, D-4) y a los métodos de búsqueda y de
    presentaciones (T044), para verificar qué lectura ejecuta la sección en
    cada caso.
    """

    def __init__(self, service: InventoryService) -> None:
        """Envuelve el servicio real, sin modificarlo."""
        self._service = service
        self.list_products_calls = 0
        self.find_products_by_name_calls = 0
        self.find_product_by_sku_calls = 0
        self.get_product_presentations_calls = 0

    def list_products(self) -> list[ProductRecord]:
        """Cuenta la lectura y delega en el servicio real."""
        self.list_products_calls += 1

        return self._service.list_products()

    def find_products_by_name(self, name: str) -> list[ProductRecord]:
        """Cuenta la búsqueda por nombre y delega en el servicio real."""
        self.find_products_by_name_calls += 1

        return self._service.find_products_by_name(name)

    def find_product_by_sku(self, sku: str) -> ProductRecord | None:
        """Cuenta la búsqueda por SKU y delega en el servicio real."""
        self.find_product_by_sku_calls += 1

        return self._service.find_product_by_sku(sku)

    def get_product_presentations(
        self,
        product_id: int,
    ) -> tuple[PresentationRecord, ...]:
        """Cuenta la lectura de presentaciones y delega en el servicio real."""
        self.get_product_presentations_calls += 1

        return self._service.get_product_presentations(product_id)

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra lectura o atributo en el servicio real."""
        return getattr(self._service, name)


def register_low_stock_product(
    product_service: ProductService,
    *,
    sku: str,
    name: str,
    minimum_stock: int,
    loose_units: int,
) -> ProductRecord:
    """Registra un producto activo con los saldos indicados (T043)."""
    return product_service.register(
        ProductRegistration(
            sku=sku,
            name=name,
            base_unit="unidad",
            base_sale_price=Decimal("10"),
            minimum_stock=minimum_stock,
            initial_loose_units=loose_units,
        )
    )


def rendered_markdown(app: AppTest) -> str:
    """Devuelve el texto de todos los elementos Markdown de la aplicación."""
    return "\n".join(element.value for element in app.markdown)


def rendered_text(app: AppTest) -> str:
    """Devuelve el texto de todos los elementos de texto de la aplicación."""
    return "\n".join(element.value for element in app.text)


def ui_sources() -> list[Path]:
    """Devuelve los fuentes Python de la interfaz (`app/ui/**`), ordenados."""
    return sorted(APP_PATH.parent.rglob("*.py"))


def section_sources() -> list[Path]:
    """Devuelve los fuentes Python de las secciones (`app/ui/sections/**`)."""
    return sorted(SECTIONS_PATH.rglob("*.py"))


def imported_modules(tree: ast.AST) -> set[str]:
    """Devuelve los módulos que importa un árbol sintáctico."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    return imported


def called_names(tree: ast.AST) -> set[str]:
    """Devuelve los nombres invocados directamente por un árbol sintáctico."""
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def snapshot(
    connection: sqlite3.Connection,
) -> dict[str, list[tuple[Any, ...]]]:
    """Captura el contenido de las tablas, ordenado, para comparar."""
    tables = (
        "products",
        "presentations",
        "movements",
        "price_changes",
        "proposals",
    )

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
    Abre una base en memoria con el esquema del proyecto (T023).

    Usa la conexión real del proyecto (`connect`), que habilita el uso desde
    los hilos del `ToolNode`; cada prueba abre su propia base en memoria y no
    toca datos persistentes.
    """
    opened_connection = connect()
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
def inventory_tools(
    inventory_service: InventoryService,
) -> dict[str, BaseTool]:
    """Herramientas de consulta reales, indexadas por nombre."""
    return {
        tool.name: tool for tool in create_inventory_tools(inventory_service)
    }


@pytest.fixture
def catalog(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> Catalog:
    """Registra un producto con stock suelto, cerrado y mínimo reales."""
    stored = product_service.register(
        ProductRegistration(
            sku="SKU-1",
            name="Producto",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            minimum_stock=20,
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


# --- PASO 0: entorno de hilos de SQLite ---


def test_threadsafety_is_serialized() -> None:
    """
    Verifica el modo de hilos de `sqlite3` antes de compartir la conexión.

    Con `sqlite3.threadsafety == 3` (modo serializado) es seguro usar la misma
    conexión desde los hilos del `ToolNode`; con un valor menor, compartir la
    conexión no estaría garantizado y la tarea debe detenerse.
    """
    assert sqlite3.threadsafety == 3


# --- Lógica testeable sin runtime de Streamlit ---


def test_build_assistant_composes_the_graph_with_injected_dependencies(
    connection: sqlite3.Connection,
) -> None:
    """Verifica la composición del asistente con dependencias inyectadas."""
    assistant = make_assistant(connection)

    assert isinstance(assistant, Assistant)
    assert assistant.connection is connection
    assert assistant.graph is not None
    assert assistant.history == []


def test_answer_returns_the_real_data_and_keeps_the_result_in_none(
    connection: sqlite3.Connection,
    catalog: Catalog,
    inventory_tools: dict[str, BaseTool],
) -> None:
    """Verifica la respuesta real de una consulta y el estado sin ejecución."""
    arguments = {"product_id": catalog.product.id}
    assistant = make_assistant(
        connection, tool_call("get_product_stock", arguments)
    )

    turn = answer(assistant, "¿Cuánto stock hay?")

    expected = str(inventory_tools["get_product_stock"].invoke(arguments))
    assert turn.user_message == "¿Cuánto stock hay?"
    assert turn.state["response"] == expected
    assert "SKU-1" in turn.state["response"]
    assert turn.state["pending_proposal"] is None
    assert turn.state["execution_result"] is None
    assert assistant.history == []


def test_answer_bridges_a_pending_proposal_without_executing_it(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica la propuesta pendiente y la ausencia de escrituras (D5)."""
    before = snapshot(connection)
    changes_before = connection.total_changes
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_entry",
            {
                "product_id": catalog.product.id,
                "quantity": 3,
                "reason": "Compra",
            },
        ),
    )

    turn = answer(assistant, "Propone una entrada suelta")

    proposal = turn.state["pending_proposal"]
    assert isinstance(proposal, Proposal)
    assert proposal.status is ProposalStatus.PENDING
    assert proposal.payload.quantity == 3
    assert turn.state["execution_result"] is None
    assert snapshot(connection) == before
    assert snapshot(connection)["proposals"] == []
    assert connection.total_changes == changes_before


def test_describe_proposal_lists_the_action_and_its_data(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica la descripción de una propuesta pendiente (D5)."""
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_entry",
            {
                "product_id": catalog.product.id,
                "quantity": 3,
                "reason": "Compra",
            },
        ),
    )
    proposal = answer(assistant, "Propone una entrada").state[
        "pending_proposal"
    ]

    described = describe_proposal(proposal)

    assert "entrada_suelta" in described
    assert "pendiente" in described
    assert "- quantity: 3" in described
    assert "- reason: Compra" in described
    assert PENDING_PROPOSAL_NOTE in described


# --- Aplicación Streamlit ---


@pytest.mark.parametrize("criterion", CRITERIA)
def test_the_app_shows_the_real_data_of_each_query(
    connection: sqlite3.Connection,
    catalog: Catalog,
    inventory_tools: dict[str, BaseTool],
    criterion: str,
) -> None:
    """Verifica los cuatro criterios de consulta sobre la interfaz (T023)."""
    arguments, tool_name = criteria_arguments(criterion, catalog)
    assistant = make_assistant(
        connection, tool_call(tool_name, arguments)
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Consulta de inventario").run()

    assert not app.exception
    assert len(app.chat_message) == 2
    rendered = rendered_markdown(app)
    assert str(inventory_tools[tool_name].invoke(arguments)) in rendered
    for marker in CRITERIA_MARKERS[criterion]:
        assert marker in rendered, marker


def test_the_app_shows_a_pending_proposal_without_confirming(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica la propuesta pendiente mostrada sin ejecutar nada (D5)."""
    before = snapshot(connection)
    changes_before = connection.total_changes
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_entry",
            {
                "product_id": catalog.product.id,
                "quantity": 3,
                "reason": "Compra",
            },
        ),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Propone una entrada suelta").run()

    rendered = rendered_markdown(app)
    state = app.session_state[ASSISTANT_KEY].history[-1].state

    assert "Propuesta pendiente" in rendered
    assert "- quantity: 3" in rendered
    assert PENDING_PROPOSAL_NOTE in rendered
    assert state["execution_result"] is None
    assert isinstance(state["pending_proposal"], Proposal)
    assert state["pending_proposal"].status is ProposalStatus.PENDING
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_the_app_shows_a_fallback_when_the_model_is_silent(
    connection: sqlite3.Connection,
) -> None:
    """Verifica el aviso cuando el asistente no produce ninguna respuesta."""
    assistant = make_assistant(connection)
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Hola").run()

    assert not app.exception
    rendered = rendered_markdown(app)
    assert rendered.split("\n") == ["Hola", NO_RESPONSE_MESSAGE]
    assert assistant.history[0].state["pending_proposal"] is None


def test_the_app_keeps_the_conversation_between_runs(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica que la conversación se conserva en la sesión (D7)."""
    assistant = make_assistant(
        connection,
        tool_call("get_product_stock", {"product_id": catalog.product.id}),
        tool_call("get_replenishment_products", {}),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("¿Cuánto stock hay?").run()
    app.chat_input[0].set_value("¿Qué reponer?").run()

    rendered = rendered_markdown(app)
    assert len(app.chat_message) == 4
    assert "¿Cuánto stock hay?" in rendered
    assert "¿Qué reponer?" in rendered
    assert len(assistant.history) == 2


# --- Lectura sin modelo y contenedor de sesión (T039) ---


def failing_get_llm() -> BaseChatModel:
    """Reemplaza al modelo real con un fallo de configuración (T039)."""
    raise LLMConfigurationError(MISSING_KEY_MESSAGE)


def configured_database_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    """Configura una base temporal y devuelve su ruta (T039).

    Las pruebas de la lectura sin modelo abren una base en archivo propia: no
    usan la base durable del proyecto ni la ruta predeterminada.
    """
    path = tmp_path / "inventario.sqlite3"
    monkeypatch.setattr(config, "INVENTORY_DB_PATH", str(path))

    return path


def test_the_app_reports_a_missing_llm_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Verifica el aviso cuando el modelo falta y la lectura disponible (T039).

    Conserva la cobertura del asistente: el motivo de configuración se muestra,
    la conversación no se habilita y el asistente no queda en la sesión; ya no
    exige que la aplicación se abstenga por completo, porque la lectura de la
    sesión se compone y puede consultar datos reales sin modelo.
    """
    database_path = configured_database_path(monkeypatch, tmp_path)
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)

    app.run()

    session = app.session_state.get(SESSION_KEY)

    assert not app.exception
    assert len(app.error) == 1
    assert MISSING_KEY_MESSAGE in app.error[0].value
    assert len(app.chat_input) == 0
    assert app.session_state.get(ASSISTANT_KEY) is None
    assert rendered_markdown(app) == ""
    assert isinstance(session, SessionResources)
    assert session.inventory_service.find_product_by_sku("SKU-1") is None
    assert database_path.is_file()
    session.connection.close()


def test_build_session_composes_and_reads_without_the_model(
    monkeypatch: pytest.MonkeyPatch,
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica la lectura compuesta y consultada sin modelo (T039)."""
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    with pytest.raises(LLMConfigurationError):
        llm_module.get_llm()

    session = build_session(connection=connection)

    stock = session.inventory_service.get_product_stock(catalog.product.id)

    assert isinstance(session, SessionResources)
    assert session.connection is connection
    assert isinstance(session.proposal_service, ProposalService)
    assert stock.product.sku == "SKU-1"
    assert stock.total_equivalent == catalog.total_equivalent
    assert session.presentation_service.get_presentation(
        catalog.box.id
    ).sale_price == Decimal("600")
    assert (
        session.price_history_service.get_price_history_by_presentation(
            catalog.box.id
        )
        == []
    )


def test_the_read_composition_does_not_resolve_the_model() -> None:
    """Verifica que la lectura no invoca el modelo ni el grafo (T039)."""
    tree = ast.parse(SESSION_MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert not any(
        module.startswith(prefix)
        for module in imported
        for prefix in ("app.services.llm", "app.graph", "app.tools")
    )
    assert not called & {"get_llm", "build_workflow"}


def test_the_app_keeps_a_single_session_connection_between_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verifica una conexión y un juego de servicios por sesión (T039, D-G)."""
    configured_database_path(monkeypatch, tmp_path)
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    opened: list[sqlite3.Connection] = []
    original_open_connection = session_module.open_connection

    def counting_open_connection() -> sqlite3.Connection:
        """Cuenta las conexiones abiertas al componer la lectura."""
        opened_connection = original_open_connection()
        opened.append(opened_connection)

        return opened_connection

    monkeypatch.setattr(
        session_module, "open_connection", counting_open_connection
    )
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)

    app.run()
    first_run = app.session_state.get(SESSION_KEY)
    app.run()
    second_run = app.session_state.get(SESSION_KEY)

    try:
        assert len(opened) == 1
        assert first_run is second_run
        assert isinstance(first_run, SessionResources)
        assert first_run.connection is opened[0]
        assert first_run.inventory_service is second_run.inventory_service
    finally:
        if opened:
            opened[0].close()


def test_the_app_reuses_an_injected_assistant_without_opening_the_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    connection: sqlite3.Connection,
) -> None:
    """Verifica que un asistente inyectado no abre la base durable (T039)."""
    database_path = configured_database_path(monkeypatch, tmp_path)
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    assistant = make_assistant(connection)
    app = app_for(assistant)

    app.run()

    assert not app.exception
    assert len(app.chat_input) == 1
    assert app.session_state[ASSISTANT_KEY] is assistant
    assert app.session_state[SESSION_KEY] is assistant.session
    assert assistant.connection is connection
    assert not database_path.exists()


def test_build_assistant_reuses_the_received_session(
    monkeypatch: pytest.MonkeyPatch,
    connection: sqlite3.Connection,
    catalog: Catalog,
    inventory_tools: dict[str, BaseTool],
) -> None:
    """
    Verifica que el asistente reutiliza la sesión recibida (T039, D-G).

    Componer el asistente sobre una sesión válida no debe abrir otra conexión
    ni recomponer servicios: si `build_assistant` ignorara `session` y
    compusiera una lectura nueva, `open_connection` se invocaría y la
    sustitución de esta prueba lo detectaría. La conexión la cierra la fixture,
    que es la única que la abre.
    """
    session = build_session(connection=connection)

    def fail_open_connection() -> sqlite3.Connection:
        """Falla si la composición abre una conexión adicional (D-G)."""
        raise AssertionError(
            "build_assistant abrió una conexión en lugar de reutilizar la "
            "sesión recibida"
        )

    monkeypatch.setattr(
        session_module, "open_connection", fail_open_connection
    )
    arguments = {"product_id": catalog.product.id}
    assistant = build_assistant(
        model=RelayChatModel(
            tool_calls=[tool_call("get_product_stock", arguments)]
        ),
        session=session,
    )

    stock = assistant.session.inventory_service.get_product_stock(
        catalog.product.id
    )
    response = answer(assistant, "¿Cuánto stock hay?").state["response"]

    assert assistant.session is session
    assert assistant.connection is session.connection
    assert assistant.connection is connection
    assert stock.product.sku == "SKU-1"
    assert stock.total_equivalent == catalog.total_equivalent
    assert response == str(
        inventory_tools["get_product_stock"].invoke(arguments)
    )
    assert f"total_equivalent={catalog.total_equivalent}" in response


def test_the_read_composition_does_not_write(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica que componer y consultar la lectura no escribe (T039)."""
    before = snapshot(connection)
    changes_before = connection.total_changes

    session = build_session(connection=connection)
    session.inventory_service.get_product_stock(catalog.product.id)
    session.inventory_service.get_low_stock_products()
    session.inventory_service.get_replenishment_products()
    session.inventory_service.get_movement_history(catalog.product.id)
    session.presentation_service.get_presentation(catalog.box.id)
    session.price_history_service.get_price_history_by_presentation(
        catalog.box.id
    )

    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_the_interface_confirms_only_through_the_proposal_service() -> None:
    """
    Verifica que la confirmación es explícita y usa el servicio (RF-05).

    Conserva el único punto de confirmación de toda la interfaz: el conteo
    abarca todos los fuentes de `app/ui/**`, incluidas las secciones de la
    navegación lateral (T041, D-A). Ninguna sección confirma por su cuenta.
    """
    sources = ui_sources()
    main_source = APP_PATH.read_text(encoding="utf-8")

    assert sources
    assert (
        sum(
            path.read_text(encoding="utf-8").count(".confirm(")
            for path in sources
        )
        == 1
    )
    assert "proposal_service.confirm(proposal)" in main_source
    assert "graph.invoke" in main_source


# --- Confirmación de propuestas (T024) ---


def test_confirming_a_pending_proposal_executes_it_once(
    connection: sqlite3.Connection,
    catalog: Catalog,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica la confirmación explícita del usuario en la interfaz (T024)."""
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_entry",
            {
                "product_id": catalog.product.id,
                "quantity": 3,
                "reason": "Compra",
            },
        ),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Propone una entrada suelta").run()

    assert [button.label for button in app.button] == [CONFIRM_LABEL]

    app.button[0].set_value(True).run()

    state = assistant.history[0].state
    result = state["execution_result"]
    rendered = rendered_markdown(app)

    assert not app.exception
    assert result is not None
    assert result.status is ProposalStatus.CONFIRMED
    assert isinstance(result.result, MovementRecord)
    assert result.result.quantity == 3
    assert state["pending_proposal"].status is ProposalStatus.CONFIRMED
    assert products.get(catalog.product.id).loose_units == 7
    assert len(movements.list_by_product(catalog.product.id)) == 3
    assert "Operación confirmada" in rendered
    assert "- quantity: 3" in rendered
    assert len(app.button) == 0


def test_confirming_an_aperture_converts_the_closed_stock(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
) -> None:
    """Verifica la confirmación de una apertura desde la interfaz (T024)."""
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_aperture",
            {
                "product_id": catalog.product.id,
                "presentation_id": catalog.box.id,
                "quantity": 1,
                "reason": "Apertura",
            },
        ),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Abrí una caja").run()
    app.button[0].set_value(True).run()

    result = assistant.history[0].state["execution_result"]

    assert not app.exception
    assert result is not None
    assert result.status is ProposalStatus.CONFIRMED
    assert result.result.kind is MovementKind.APERTURE
    assert presentations.get(catalog.box.id).closed_stock == 1
    assert products.get(catalog.product.id).loose_units == 4 + 6
    assert len(app.button) == 0


def test_a_rejected_confirmation_shows_the_error_without_changes(
    connection: sqlite3.Connection,
    catalog: Catalog,
    products: SqliteProductRepository,
) -> None:
    """Verifica el rechazo de una confirmación sin cambios parciales (T024)."""
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_exit",
            {
                "product_id": catalog.product.id,
                "quantity": 9,
                "reason": "Venta",
            },
        ),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Vende nueve unidades").run()
    before = snapshot(connection)
    changes_before = connection.total_changes

    app.button[0].set_value(True).run()

    state = assistant.history[0].state

    assert not app.exception
    assert len(app.error) == 1
    assert CONFIRMATION_ERROR_PREFIX in app.error[0].value
    assert len(app.error[0].value) > len(CONFIRMATION_ERROR_PREFIX)
    assert state["execution_result"] is None
    assert state["pending_proposal"].status is ProposalStatus.PENDING
    assert products.get(catalog.product.id).loose_units == 4
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before
    assert len(app.button) == 1


def test_a_missing_record_confirmation_shows_the_error(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica el rechazo cuando el movimiento original no existe (T024)."""
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_compensation",
            {
                "product_id": catalog.product.id,
                "movement_id": 999,
                "quantity": 1,
                "reason": "Corrección",
            },
        ),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Corrige el movimiento").run()
    app.button[0].set_value(True).run()

    state = assistant.history[0].state

    assert not app.exception
    assert len(app.error) == 1
    assert CONFIRMATION_ERROR_PREFIX in app.error[0].value
    assert state["execution_result"] is None
    assert state["pending_proposal"].status is ProposalStatus.PENDING


def test_a_confirmed_proposal_is_not_confirmed_again(
    connection: sqlite3.Connection,
    catalog: Catalog,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que una propuesta confirmada no se vuelve a ejecutar (T024)."""
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_entry",
            {
                "product_id": catalog.product.id,
                "quantity": 3,
                "reason": "Compra",
            },
        ),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Propone una entrada").run()
    app.button[0].set_value(True).run()

    turn = assistant.history[0]
    result = turn.state["execution_result"]
    before = snapshot(connection)
    changes_before = connection.total_changes

    assert len(app.button) == 0

    reason = confirm_pending(assistant, turn)

    assert reason == ALREADY_CONFIRMED_MESSAGE
    assert turn.state["execution_result"] is result
    assert turn.state["pending_proposal"].status is ProposalStatus.CONFIRMED
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before
    assert len(movements.list_by_product(catalog.product.id)) == 3


def test_the_confirmation_control_is_disabled_while_processing(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica que el control se deshabilita con una confirmación en curso."""
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_entry",
            {
                "product_id": catalog.product.id,
                "quantity": 3,
                "reason": "Compra",
            },
        ),
    )
    app = app_for(assistant)
    app.session_state[PROCESSING_KEY] = True

    app.run()
    app.chat_input[0].set_value("Propone una entrada").run()

    assert [button.proto.disabled for button in app.button] == [True]
    assert assistant.history[0].state["execution_result"] is None


def test_confirming_a_turn_without_a_pending_proposal_is_rejected(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica el guard cuando el turno no tiene propuesta pendiente."""
    assistant = make_assistant(
        connection,
        tool_call("get_product_stock", {"product_id": catalog.product.id}),
    )
    turn = answer(assistant, "¿Cuánto stock hay?")

    reason = confirm_pending(assistant, turn)

    assert reason == NO_PENDING_PROPOSAL_MESSAGE
    assert turn.state["execution_result"] is None
    assert turn.state["pending_proposal"] is None


# --- Precio vigente, precio nuevo y confirmación (T035) ---


def price_call(presentation_id: int, new_price: str) -> dict[str, Any]:
    """Construye la llamada al cambio de precio para el modelo falso."""
    return tool_call(
        "propose_price_update",
        {"presentation_id": presentation_id, "new_price": new_price},
    )


def test_the_app_shows_the_price_update_with_its_prices(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica la presentación, el precio vigente y el nuevo (T035)."""
    assistant = make_assistant(
        connection, price_call(catalog.box.id, "700")
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Cambia el precio a 700").run()

    rendered = rendered_markdown(app)
    state = assistant.history[0].state
    expected_presentation = (
        f"- presentación: Caja x6 (id {catalog.box.id}, factor 6)"
    )

    assert not app.exception
    assert "Propuesta de cambio de precio" in rendered
    assert expected_presentation in rendered
    assert "- precio vigente: 600.00" in rendered
    assert "- precio nuevo: 700.00" in rendered
    assert PENDING_PROPOSAL_NOTE in rendered
    assert "sin cambios registrados" in rendered
    assert state["pending_proposal"].status is ProposalStatus.PENDING


def test_the_app_shows_the_price_proposal_without_writing(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que mostrar la propuesta de precio no escribe nada (T035)."""
    before = snapshot(connection)
    changes_before = connection.total_changes
    assistant = make_assistant(
        connection, price_call(catalog.box.id, "700")
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Cambia el precio a 700").run()

    state = assistant.history[0].state

    assert not app.exception
    assert state["pending_proposal"].status is ProposalStatus.PENDING
    assert state["execution_result"] is None
    assert presentations.get(catalog.box.id).sale_price == Decimal("600")
    assert snapshot(connection) == before
    assert snapshot(connection)["price_changes"] == []
    assert connection.total_changes == changes_before


def test_confirming_a_price_update_applies_it_once(
    connection: sqlite3.Connection,
    catalog: Catalog,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica la confirmación del cambio de precio en la interfaz (T035)."""
    assistant = make_assistant(
        connection, price_call(catalog.box.id, "700")
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Cambia el precio a 700").run()

    balances_before = (
        products.get(catalog.product.id).loose_units,
        presentations.get(catalog.box.id).closed_stock,
    )
    movements_before = movements.list_by_product(catalog.product.id)

    assert [button.label for button in app.button] == [CONFIRM_LABEL]

    app.button[0].set_value(True).run()

    state = assistant.history[0].state
    result = state["execution_result"]
    changes = SqlitePriceHistoryRepository(connection).list_by_presentation(
        catalog.box.id
    )

    assert not app.exception
    assert result is not None
    assert result.status is ProposalStatus.CONFIRMED
    assert isinstance(result.result, PresentationRecord)
    assert result.result.sale_price == Decimal("700")
    assert state["pending_proposal"].status is ProposalStatus.CONFIRMED
    assert presentations.get(catalog.box.id).sale_price == Decimal("700")
    assert len(changes) == 1
    assert changes[0].previous_price == Decimal("600")
    assert changes[0].new_price == Decimal("700")
    assert changes[0].event_type == PRICE_CHANGE_EVENT_TYPE
    assert (
        products.get(catalog.product.id).loose_units,
        presentations.get(catalog.box.id).closed_stock,
    ) == balances_before
    assert movements.list_by_product(catalog.product.id) == movements_before
    assert "Operación confirmada" in rendered_markdown(app)
    assert len(app.button) == 0


def test_the_price_history_shows_the_confirmed_change(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentation_service: PresentationService,
) -> None:
    """Verifica el historial real antes y después de confirmar (T035)."""
    presentation_service.change_sale_price(catalog.box.id, Decimal("650"))
    assistant = make_assistant(
        connection, price_call(catalog.box.id, "700")
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Cambia el precio a 700").run()

    rendered = rendered_markdown(app)

    assert not app.exception
    assert "Historial de precios de la presentación (1):" in rendered
    assert "- 600.00 → 650.00 (" in rendered
    assert PRICE_CHANGE_EVENT_TYPE in rendered

    app.button[0].set_value(True).run()

    rendered = rendered_markdown(app)

    assert "Historial de precios de la presentación (2):" in rendered
    assert "- 650.00 → 700.00 (" in rendered
    assert "- precio vigente: 700.00" in rendered
    assert len(app.button) == 0


def test_the_price_description_does_not_print_none(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica que la descripción del cambio de precio no imprime None."""
    assistant = make_assistant(
        connection, price_call(catalog.box.id, "700")
    )
    proposal = answer(assistant, "Cambia el precio a 700").state[
        "pending_proposal"
    ]

    described = describe_price_update(assistant, proposal)

    assert "None" not in described
    assert "- presentación: Caja x6" in described
    assert "- precio vigente: 600.00" in described
    assert "- precio nuevo: 700.00" in described


def test_a_price_update_of_a_missing_presentation_shows_the_reason(
    connection: sqlite3.Connection,
) -> None:
    """Verifica el motivo explícito cuando la presentación no existe (T035)."""
    assistant = make_assistant(connection, price_call(999, "700"))
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Cambia el precio a 700").run()

    state = assistant.history[0].state
    described = describe_price_update(
        assistant, state["pending_proposal"]
    )
    rendered = rendered_markdown(app)

    assert not app.exception
    assert "- presentación: no existe la presentación con id 999" in rendered
    assert "- precio nuevo: 700.00" in rendered
    assert "presentación no existe" in rendered
    assert "None" not in described
    assert "- precio vigente" not in described


def test_a_deactivated_product_rejects_the_price_confirmation(
    connection: sqlite3.Connection,
    catalog: Catalog,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica el rechazo del cambio de precio sin cambios parciales."""
    product_service.deactivate(catalog.product.id)
    assistant = make_assistant(
        connection, price_call(catalog.box.id, "700")
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Cambia el precio a 700").run()
    rendered = rendered_markdown(app)
    before = snapshot(connection)
    changes_before = connection.total_changes

    app.button[0].set_value(True).run()

    state = assistant.history[0].state

    assert not app.exception
    assert "- precio vigente: 600.00" in rendered
    assert len(app.error) == 1
    assert CONFIRMATION_ERROR_PREFIX in app.error[0].value
    assert len(app.error[0].value) > len(CONFIRMATION_ERROR_PREFIX)
    assert state["execution_result"] is None
    assert state["pending_proposal"].status is ProposalStatus.PENDING
    assert presentations.get(catalog.box.id).sale_price == Decimal("600")
    assert snapshot(connection) == before
    assert snapshot(connection)["price_changes"] == []
    assert connection.total_changes == changes_before
    assert len(app.button) == 1


# --- Navegación lateral y enrutado de secciones (T041, RF-09) ---


def test_the_app_offers_the_navigation_with_the_rf09_sections(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica las siete secciones de la navegación lateral (C1, T041).

    Las etiquetas se comparan con la tupla literal esperada —no con la
    constante de la aplicación, que sería una comprobación tautológica— y la
    navegación debe ser un único control del sidebar, con «Asistente» elegido
    por defecto para conservar el comportamiento anterior (C20).
    """
    app = app_for(make_assistant(connection))

    app.run()

    navigation = app.radio

    assert not app.exception
    assert len(navigation) == 1
    assert len(app.sidebar.radio) == 1
    assert tuple(navigation[0].options) == SECTION_LABELS_EXPECTED
    assert navigation[0].value == DEFAULT_SECTION_LABEL


def test_every_navigation_section_has_its_own_renderer(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el registro cubre exactamente las secciones (C1, T041)."""
    renderers = section_renderers(
        assistant=None,
        unavailable_reason=None,
        session=build_session(connection=connection),
    )

    assert tuple(renderers) == SECTION_LABELS
    assert all(callable(render) for render in renderers.values())


def test_the_navigation_renders_only_the_selected_section(
    connection: sqlite3.Connection,
) -> None:
    """Verifica el enrutado de la sección elegida en la navegación (C1)."""
    app = app_for(make_assistant(connection))

    app.run()

    assert len(app.chat_input) == 1

    app.radio[0].set_value("Propuestas pendientes").run()

    assert not app.exception
    assert [header.value for header in app.header] == [
        "Propuestas pendientes"
    ]
    assert [info.value for info in app.info] == [EMPTY_PROPOSALS_MESSAGE]
    assert len(app.chat_input) == 0

    app.radio[0].set_value(DEFAULT_SECTION_LABEL).run()

    assert not app.exception
    assert [header.value for header in app.header] == []
    assert len(app.info) == 0
    assert len(app.chat_input) == 1


def test_the_pending_proposals_section_shows_the_action_and_the_status(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la vista mínima de las propuestas sin confirmar (C13, T041).

    La sección muestra la acción y el estado reales de la propuesta preparada
    por el asistente, sin ofrecer la confirmación —que sigue siendo exclusiva
    del único punto de `main.py` (D-A)— y sin volcar el payload (D-C).
    """
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_entry",
            {
                "product_id": catalog.product.id,
                "quantity": 3,
                "reason": "Compra",
            },
        ),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Propone una entrada suelta").run()
    proposal = assistant.history[-1].state["pending_proposal"]

    app.radio[0].set_value("Propuestas pendientes").run()

    rendered = rendered_markdown(app)

    assert not app.exception
    assert proposal.status is ProposalStatus.PENDING
    assert [header.value for header in app.header] == [
        "Propuestas pendientes"
    ]
    assert f"`{proposal.action.value}`" in rendered
    assert f"`{proposal.status.value}`" in rendered
    assert "- quantity" not in rendered
    assert len(app.button) == 0
    assert len(app.chat_input) == 0


def test_the_proposals_view_does_not_confirm_nor_touch_the_session() -> None:
    """
    Verifica la responsabilidad de vista de las propuestas (D-A, T041).

    El módulo no contiene el punto de confirmación, no nombra el servicio de
    propuestas y no escribe en la sesión: confirmar sigue siendo exclusivo de
    `app/ui/main.py`.
    """
    source = PROPOSALS_SECTION_PATH.read_text(encoding="utf-8")

    assert ".confirm(" not in source
    assert "proposal_service" not in source
    assert "st.session_state" not in source


def test_the_app_keeps_a_single_entry_point_without_multipage(
    connection: sqlite3.Connection,
) -> None:
    """Verifica el punto de entrada único y la ausencia de multipágina (C1)."""
    source = APP_PATH.read_text(encoding="utf-8")

    assert APP_PATH.name == "main.py"
    assert not (APP_PATH.parent / "pages").exists()
    for forbidden in (
        "st.navigation",
        "st.Page",
        "st.switch_page",
        "page_link",
    ):
        assert forbidden not in source, forbidden

    app = app_for(make_assistant(connection))

    app.run()

    assert not app.exception


def test_the_sections_do_not_depend_on_persistence_nor_domain() -> None:
    """
    Verifica que las secciones usan solo servicios de aplicación (C1, T041).

    La interfaz no debe acceder directamente a la base de datos, a los
    repositorios de persistencia ni al dominio: ninguna sección importa
    infraestructura, dominio ni el punto de entrada, y ninguna abre conexiones,
    sesiones o transacciones por su cuenta.
    """
    sources = section_sources()

    assert sources

    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module in imported_modules(tree):
            for prefix in FORBIDDEN_SECTION_IMPORTS:
                assert not (
                    module == prefix or module.startswith(f"{prefix}.")
                ), f"{path.name} importa {module}"

        assert not called_names(tree) & FORBIDDEN_SECTION_CALLS, path.name


@pytest.mark.parametrize("section", PENDING_SECTIONS)
def test_each_unimplemented_section_declares_its_pending_state(
    connection: sqlite3.Connection,
    section: str,
) -> None:
    """
    Verifica el aviso honesto de cada sección pendiente (C4, T041).

    Una sección que todavía no consulta sus servicios muestra su aviso
    explícito y ningún dato: no inventa valores, no presenta un vacío como un
    cero y no ofrece controles de la conversación ni de confirmación.
    """
    app = app_for(make_assistant(connection))

    app.run()
    app.radio[0].set_value(section).run()

    assert not app.exception
    assert [header.value for header in app.header] == [section]
    assert [info.value for info in app.info] == [
        f"«{section}»: {SECTION_PENDING_MESSAGE}"
    ]
    assert rendered_markdown(app) == ""
    assert len(app.chat_input) == 0
    assert len(app.button) == 0


def test_the_navigation_works_without_the_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Verifica la navegación lateral sin modelo configurado (C1, C2, T041).

    La aplicación no se abstiene: la sección del asistente —elegida por
    defecto— muestra el motivo de su indisponibilidad, las secciones pendientes
    muestran su aviso sin datos y la sección de propuestas informa su vacío
    explícito, porque no hay asistente que las prepare ni datos que inventar.
    """
    configured_database_path(monkeypatch, tmp_path)
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)

    app.run()

    session = app.session_state.get(SESSION_KEY)
    navigation = app.radio

    try:
        assert not app.exception
        assert len(navigation) == 1
        assert tuple(navigation[0].options) == SECTION_LABELS_EXPECTED
        assert navigation[0].value == DEFAULT_SECTION_LABEL
        assert len(app.error) == 1
        assert MISSING_KEY_MESSAGE in app.error[0].value
        assert len(app.chat_input) == 0
        assert rendered_markdown(app) == ""

        app.radio[0].set_value("Operaciones").run()

        assert not app.exception
        assert len(app.error) == 0
        assert [header.value for header in app.header] == ["Operaciones"]
        assert [info.value for info in app.info] == [
            f"«Operaciones»: {SECTION_PENDING_MESSAGE}"
        ]
        assert len(app.chat_input) == 0

        app.radio[0].set_value("Propuestas pendientes").run()

        assert not app.exception
        assert [info.value for info in app.info] == [EMPTY_PROPOSALS_MESSAGE]
        assert rendered_markdown(app) == ""
    finally:
        if session is not None:
            session.connection.close()


# --- Sección Inicio: alertas y conteos (T043, RF-09) ---


def test_home_shows_both_alert_lists_with_their_values(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica las alertas de stock bajo y reposición con sus valores (C3).

    El catálogo tiene un producto activo con total equivalente 16, mínimo 20 y
    faltante 4: aparece en ambas listas con nombre, SKU, total y faltante.
    """
    app = app_for(make_assistant(connection))

    app.run()
    app.radio[0].set_value(HOME_HEADER).run()

    rendered = rendered_markdown(app)
    expected = "- Producto (SKU SKU-1): total equivalente 16, faltante 4"

    assert not app.exception
    assert [header.value for header in app.header] == [HOME_HEADER]
    assert [subheader.value for subheader in app.subheader] == [
        LOW_STOCK_HEADER,
        REPLENISHMENT_HEADER,
    ]
    assert rendered.count(expected) == 2
    assert EMPTY_LOW_STOCK_MESSAGE not in [
        info.value for info in app.info
    ]
    assert EMPTY_REPLENISHMENT_MESSAGE not in [
        info.value for info in app.info
    ]


def test_home_reads_the_catalog_once_per_render(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la lectura perezosa y única del catálogo por render (D-4).

    Construir el registro de secciones o mostrar el asistente no lee el
    catálogo; mostrar «Inicio» lo lee exactamente una vez y de esa lectura
    derivan los conteos de activos e inactivos.
    """
    assistant = make_assistant(connection)
    counting = CountingInventoryService(
        assistant.session.inventory_service
    )
    assistant.session.inventory_service = counting
    app = app_for(assistant)

    app.run()

    assert counting.list_products_calls == 0

    app.radio[0].set_value(HOME_HEADER).run()

    rendered = rendered_markdown(app)

    assert counting.list_products_calls == 1
    assert f"{ACTIVE_PRODUCTS_LABEL}: 1" in rendered
    assert f"{INACTIVE_PRODUCTS_LABEL}: 0" in rendered

    app.radio[0].set_value(HOME_HEADER).run()

    assert counting.list_products_calls == 2


def test_home_excludes_inactive_products_from_alerts_but_counts_them(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica la exclusión de inactivos en alertas y su conteo (C3, C7).

    El dominio excluye los productos desactivados de las consultas globales de
    alertas, pero la lectura pública del catálogo los conserva para el conteo.
    """
    stored = register_low_stock_product(
        product_service,
        sku="SKU-OFF",
        name="Inactivo con stock bajo",
        minimum_stock=10,
        loose_units=2,
    )
    product_service.deactivate(stored.id)
    app = app_for(make_assistant(connection))

    app.run()
    app.radio[0].set_value(HOME_HEADER).run()

    rendered = rendered_markdown(app)
    messages = [info.value for info in app.info]

    assert not app.exception
    assert "SKU-OFF" not in rendered
    assert f"{ACTIVE_PRODUCTS_LABEL}: 0" in rendered
    assert f"{INACTIVE_PRODUCTS_LABEL}: 1" in rendered
    assert EMPTY_LOW_STOCK_MESSAGE in messages
    assert EMPTY_REPLENISHMENT_MESSAGE in messages


def test_home_shows_a_zero_shortage_in_low_stock_only(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica el cero real de un producto con mínimo y total cero (C3).

    El producto activo con stock mínimo 0 y total 0 tiene stock bajo con
    faltante 0 y no requiere reposición: el cero se muestra como un valor real
    y no se confunde con la ausencia de datos.
    """
    register_low_stock_product(
        product_service,
        sku="SKU-ZERO",
        name="Sin stock",
        minimum_stock=0,
        loose_units=0,
    )
    app = app_for(make_assistant(connection))

    app.run()
    app.radio[0].set_value(HOME_HEADER).run()

    rendered = rendered_markdown(app)
    messages = [info.value for info in app.info]
    expected = (
        "- Sin stock (SKU SKU-ZERO): total equivalente 0, faltante 0"
    )

    assert not app.exception
    assert rendered.count(expected) == 1
    assert EMPTY_LOW_STOCK_MESSAGE not in messages
    assert EMPTY_REPLENISHMENT_MESSAGE in messages


def test_each_empty_alert_list_shows_its_own_message(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """Verifica los dos estados vacíos independientes (D-2, C4)."""
    register_low_stock_product(
        product_service,
        sku="SKU-1",
        name="Producto con stock suficiente",
        minimum_stock=0,
        loose_units=5,
    )
    app = app_for(make_assistant(connection))

    app.run()
    app.radio[0].set_value(HOME_HEADER).run()

    messages = [info.value for info in app.info]

    assert not app.exception
    assert EMPTY_LOW_STOCK_MESSAGE in messages
    assert EMPTY_REPLENISHMENT_MESSAGE in messages


def test_an_empty_alert_list_keeps_its_message_with_the_other_list_filled(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica el caso mixto: una lista con datos y la otra vacía (D-2).

    El producto tiene total igual al mínimo y faltante 0: aparece en stock bajo
    y no en reposición, cuya lista conserva su mensaje propio.
    """
    register_low_stock_product(
        product_service,
        sku="SKU-1",
        name="Producto justo",
        minimum_stock=5,
        loose_units=5,
    )
    app = app_for(make_assistant(connection))

    app.run()
    app.radio[0].set_value(HOME_HEADER).run()

    rendered = rendered_markdown(app)
    messages = [info.value for info in app.info]
    expected = (
        "- Producto justo (SKU SKU-1): total equivalente 5, faltante 0"
    )

    assert not app.exception
    assert rendered.count(expected) == 1
    assert EMPTY_LOW_STOCK_MESSAGE not in messages
    assert EMPTY_REPLENISHMENT_MESSAGE in messages


def test_home_reports_an_empty_catalog_without_zero_counts(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica el catálogo vacío sin conteos 0/0 ni datos inventados (D-5, C4).
    """
    app = app_for(make_assistant(connection))

    app.run()
    app.radio[0].set_value(HOME_HEADER).run()

    rendered = rendered_markdown(app)
    messages = [info.value for info in app.info]

    assert not app.exception
    assert [header.value for header in app.header] == [HOME_HEADER]
    assert EMPTY_CATALOG_MESSAGE in messages
    assert f"{ACTIVE_PRODUCTS_LABEL}:" not in rendered
    assert f"{INACTIVE_PRODUCTS_LABEL}:" not in rendered
    assert "0/0" not in rendered
    assert EMPTY_LOW_STOCK_MESSAGE in messages
    assert EMPTY_REPLENISHMENT_MESSAGE in messages


def test_home_preserves_the_order_returned_by_the_service(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica que la vista no reordena las alertas (D-3).

    Los productos se registran en orden inverso al alfabético: si la vista los
    ordenara por nombre, el orden observado dejaría de coincidir.
    """
    for index, name in enumerate(("Zeta", "Alfa", "Beta"), start=1):
        register_low_stock_product(
            product_service,
            sku=f"SKU-{index}",
            name=name,
            minimum_stock=10,
            loose_units=1,
        )
    app = app_for(make_assistant(connection))

    app.run()
    app.radio[0].set_value(HOME_HEADER).run()

    rendered = rendered_markdown(app)
    first = rendered.index("- Zeta (SKU SKU-1):")
    second = rendered.index("- Alfa (SKU SKU-2):")
    third = rendered.index("- Beta (SKU SKU-3):")

    assert not app.exception
    assert first < second < third


def test_home_does_not_write(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica que mostrar Inicio no modifica la base (T043)."""
    app = app_for(make_assistant(connection))

    app.run()
    before = snapshot(connection)
    changes_before = connection.total_changes

    app.radio[0].set_value(HOME_HEADER).run()

    assert not app.exception
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_home_works_without_the_model_with_real_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Verifica Inicio sin modelo, con datos reales (C2, C3).

    La aplicación compone la lectura aunque el asistente no esté disponible;
    Inicio muestra los datos reales de la base configurada y el asistente sigue
    informando el motivo de su indisponibilidad al volver a su sección.
    """
    database_path = configured_database_path(monkeypatch, tmp_path)
    seed_connection = connect(database_path)
    try:
        create_schema(seed_connection)
        seed_products = ProductService(
            products=SqliteProductRepository(seed_connection),
            presentations=SqlitePresentationRepository(seed_connection),
            movements=SqliteMovementRepository(seed_connection),
            transaction=partial(transaction, seed_connection),
        )
        register_low_stock_product(
            seed_products,
            sku="SKU-1",
            name="Producto",
            minimum_stock=20,
            loose_units=4,
        )
    finally:
        seed_connection.close()
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)

    app.run()

    session = app.session_state.get(SESSION_KEY)

    try:
        assert not app.exception
        assert len(app.error) == 1
        assert MISSING_KEY_MESSAGE in app.error[0].value

        app.radio[0].set_value(HOME_HEADER).run()

        rendered = rendered_markdown(app)

        assert not app.exception
        assert len(app.error) == 0
        assert [header.value for header in app.header] == [HOME_HEADER]
        assert (
            "- Producto (SKU SKU-1): total equivalente 4, faltante 16"
            in rendered
        )
        assert f"{ACTIVE_PRODUCTS_LABEL}: 1" in rendered
        assert f"{INACTIVE_PRODUCTS_LABEL}: 0" in rendered

        app.radio[0].set_value(DEFAULT_SECTION_LABEL).run()

        assert not app.exception
        assert len(app.error) == 1
        assert MISSING_KEY_MESSAGE in app.error[0].value
    finally:
        if session is not None:
            session.connection.close()


def test_the_home_view_only_presents_the_received_data() -> None:
    """
    Verifica que Inicio no consulta ni escribe por su cuenta (T043).

    La vista recibe los datos ya consultados desde `main.py`: no importa los
    servicios, no lee el catálogo, no usa el estado de la sesión y no contiene
    el punto de confirmación.
    """
    source = HOME_SECTION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "st.session_state" not in source
    assert ".confirm(" not in source
    assert not any(
        module.startswith("app.services")
        for module in imported_modules(tree)
    )
    assert not called_names(tree) & {
        "list_products",
        "get_low_stock_products",
        "get_replenishment_products",
        "build_session",
        "open_connection",
        "transaction",
    }


# --- Sección Productos: listado, búsqueda y filtro (T044, RF-09) ---


def open_products_section(app: AppTest) -> None:
    """Ejecuta la aplicación y abre la sección Productos (T044)."""
    app.run()
    app.radio[0].set_value(PRODUCTS_HEADER).run()


def counted_assistant(
    connection: sqlite3.Connection,
) -> tuple[Assistant, CountingInventoryService]:
    """Compone el asistente y sustituye su lectura por el espía (T044)."""
    assistant = make_assistant(connection)
    counting = CountingInventoryService(assistant.session.inventory_service)
    assistant.session.inventory_service = counting

    return assistant, counting


def test_products_lists_the_seven_fields_of_c5(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica los siete campos etiquetados del listado (C5, T044).

    El catálogo real aporta SKU, nombre, unidad base, stock mínimo, unidades
    sueltas, la suma de stock cerrado y el estado; el filtro «Todos» y el campo
    «Nombre» son los valores por defecto (B1, C1).
    """
    app = app_for(make_assistant(connection))

    open_products_section(app)

    rendered = rendered_text(app)

    assert not app.exception
    assert [header.value for header in app.header] == [PRODUCTS_HEADER]
    assert f"{SKU_LABEL}: SKU-1" in rendered
    assert f"{NAME_LABEL}: Producto" in rendered
    assert f"{BASE_UNIT_LABEL}: botella" in rendered
    assert f"{MINIMUM_STOCK_LABEL}: 20" in rendered
    assert f"{LOOSE_UNITS_LABEL}: 4" in rendered
    assert f"{CLOSED_STOCK_LABEL}: 2" in rendered
    assert f"{STATUS_LABEL}: {ACTIVE_STATUS}" in rendered
    assert app.selectbox[0].value == "Nombre"
    assert app.selectbox[1].value == ALL_STATUS_FILTER
    assert SEARCH_NOTICE in [caption.value for caption in app.caption]
    assert (
        NON_PAGINATED_NOTICE_TEMPLATE.format(count=1)
        in [info.value for info in app.info]
    )


def test_products_keeps_a_multiline_name_inside_its_field(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica que un nombre con saltos no rompe la estructura (F1, T044).

    La vista normaliza los saltos solo para mostrarlos y usa texto
    preformateado que no interpreta Markdown; el nombre de negocio no cambia.
    """
    stored = product_service.register(
        ProductRegistration(
            sku="SKU-RARO",
            name="Producto\ncon *#saltos*#",
            base_unit="unidad\nbase",
            base_sale_price=Decimal("10"),
        )
    )
    app = app_for(make_assistant(connection))

    open_products_section(app)

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{NAME_LABEL}: Producto con *#saltos*#" in rendered
    assert f"{BASE_UNIT_LABEL}: unidad base" in rendered
    assert stored.name == "Producto\ncon *#saltos*#"
    assert stored.base_unit == "unidad\nbase"


def test_products_shows_active_and_inactive_products_by_default(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """Verifica que el filtro «Todos» incluye activos e inactivos (C1, C5)."""
    register_low_stock_product(
        product_service,
        sku="SKU-ON",
        name="Activo",
        minimum_stock=0,
        loose_units=1,
    )
    inactive = register_low_stock_product(
        product_service,
        sku="SKU-OFF",
        name="Inactivo",
        minimum_stock=0,
        loose_units=1,
    )
    product_service.deactivate(inactive.id)
    app = app_for(make_assistant(connection))

    open_products_section(app)

    rendered = rendered_text(app)
    messages = [info.value for info in app.info]

    assert not app.exception
    assert "SKU: SKU-ON" in rendered
    assert "SKU: SKU-OFF" in rendered
    assert f"{STATUS_LABEL}: {ACTIVE_STATUS}" in rendered
    assert f"{STATUS_LABEL}: {INACTIVE_STATUS}" in rendered
    assert NON_PAGINATED_NOTICE_TEMPLATE.format(count=2) in messages


def test_products_searches_by_name_with_the_service_method(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica la búsqueda por nombre con el método público (C6, B1)."""
    assistant, counting = counted_assistant(connection)
    app = app_for(assistant)

    open_products_section(app)
    counting.list_products_calls = 0
    app.text_input[0].set_value("Producto").run()

    rendered = rendered_text(app)

    assert not app.exception
    assert counting.find_products_by_name_calls == 1
    assert counting.find_product_by_sku_calls == 0
    assert counting.list_products_calls == 0
    assert "SKU: SKU-1" in rendered


def test_products_searches_by_sku_with_the_service_method(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica la búsqueda por SKU con el método público (C6, B1)."""
    assistant, counting = counted_assistant(connection)
    app = app_for(assistant)

    open_products_section(app)
    app.selectbox[0].set_value(SKU_FIELD).run()
    counting.find_products_by_name_calls = 0
    counting.find_product_by_sku_calls = 0
    counting.list_products_calls = 0
    app.text_input[0].set_value("SKU-1").run()

    rendered = rendered_text(app)

    assert not app.exception
    assert counting.find_product_by_sku_calls == 1
    assert counting.find_products_by_name_calls == 0
    assert counting.list_products_calls == 0
    assert "SKU: SKU-1" in rendered


def test_products_search_is_exact_even_ignoring_case(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica la coincidencia exacta y sin distinguir mayúsculas (C6).

    Dos productos con nombres que difieren solo en el caso se devuelven ambos
    al buscar con otro caso; una búsqueda por SKU con otro caso encuentra el
    producto sin normalizar el texto en la interfaz.
    """
    register_low_stock_product(
        product_service,
        sku="SKU-A",
        name="Producto",
        minimum_stock=0,
        loose_units=1,
    )
    register_low_stock_product(
        product_service,
        sku="SKU-B",
        name="producto",
        minimum_stock=0,
        loose_units=1,
    )
    register_low_stock_product(
        product_service,
        sku="SKU-C",
        name="Producto extra",
        minimum_stock=0,
        loose_units=1,
    )
    app = app_for(make_assistant(connection))

    open_products_section(app)
    app.text_input[0].set_value("PRODUCTO").run()

    rendered = rendered_text(app)

    assert not app.exception
    assert "SKU: SKU-A" in rendered
    assert "SKU: SKU-B" in rendered
    assert "SKU: SKU-C" not in rendered
    assert NON_PAGINATED_NOTICE_TEMPLATE.format(count=2) in [
        info.value for info in app.info
    ]

    app.selectbox[0].set_value(SKU_FIELD).run()
    app.text_input[0].set_value("sku-b").run()

    rendered = rendered_text(app)

    assert "SKU: SKU-B" in rendered
    assert "SKU: SKU-A" not in rendered


def test_products_does_not_search_with_an_empty_or_blank_text(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que una entrada vacía o solo con espacios no busca (B1).

    Se muestra el catálogo completo y no se invoca ninguno de los métodos de
    búsqueda del servicio.
    """
    assistant, counting = counted_assistant(connection)
    app = app_for(assistant)

    open_products_section(app)
    assert counting.find_products_by_name_calls == 0
    assert counting.find_product_by_sku_calls == 0
    assert counting.list_products_calls == 1

    counting.find_products_by_name_calls = 0
    counting.find_product_by_sku_calls = 0
    counting.list_products_calls = 0
    app.text_input[0].set_value("   ").run()

    rendered = rendered_text(app)

    assert not app.exception
    assert counting.find_products_by_name_calls == 0
    assert counting.find_product_by_sku_calls == 0
    assert counting.list_products_calls == 1
    assert "SKU: SKU-1" in rendered


def test_products_distinguishes_a_search_without_results(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica el mensaje propio de una búsqueda sin resultados (C4, C6)."""
    app = app_for(make_assistant(connection))

    open_products_section(app)
    app.text_input[0].set_value("No existe").run()

    messages = [info.value for info in app.info]

    assert not app.exception
    assert EMPTY_PRODUCTS_SEARCH_MESSAGE in messages
    assert EMPTY_PRODUCTS_FILTER_MESSAGE not in messages
    assert EMPTY_PRODUCTS_CATALOG_MESSAGE not in messages
    assert rendered_text(app) == ""


def test_products_distinguishes_a_filter_without_results(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica el mensaje propio de un filtro que excluye todo (C1, C4)."""
    app = app_for(make_assistant(connection))

    open_products_section(app)
    app.selectbox[1].set_value(INACTIVE_STATUS_FILTER).run()

    messages = [info.value for info in app.info]

    assert not app.exception
    assert EMPTY_PRODUCTS_FILTER_MESSAGE in messages
    assert EMPTY_PRODUCTS_SEARCH_MESSAGE not in messages
    assert EMPTY_PRODUCTS_CATALOG_MESSAGE not in messages
    assert rendered_text(app) == ""


def test_products_applies_the_filter_after_the_search(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica que el filtro se aplica después de buscar (T044, C1).

    La búsqueda encuentra el producto activo y el filtro de inactivos lo
    excluye: se informa el filtro —no la búsqueda— y no se consultan las
    presentaciones de un producto que no se va a mostrar.
    """
    register_low_stock_product(
        product_service,
        sku="SKU-ON",
        name="Alfa",
        minimum_stock=0,
        loose_units=1,
    )
    assistant, counting = counted_assistant(connection)
    app = app_for(assistant)

    open_products_section(app)
    app.text_input[0].set_value("Alfa").run()
    counting.get_product_presentations_calls = 0
    app.selectbox[1].set_value(INACTIVE_STATUS_FILTER).run()

    messages = [info.value for info in app.info]

    assert not app.exception
    assert counting.find_products_by_name_calls >= 1
    assert counting.get_product_presentations_calls == 0
    assert EMPTY_PRODUCTS_FILTER_MESSAGE in messages
    assert EMPTY_PRODUCTS_SEARCH_MESSAGE not in messages


def test_products_sums_the_closed_stock_of_all_presentations(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica que «Stock cerrado» es la suma de presentaciones (A1, C5).

    Dos presentaciones con stock cerrado 2 y 3 muestran 5, no su equivalente
    ponderado por factor (que sería 48) ni el stock de una sola presentación.
    """
    product_service.register(
        ProductRegistration(
            sku="SKU-CERRADO",
            name="Con presentaciones",
            base_unit="unidad",
            base_sale_price=Decimal("10"),
            presentations=(
                InitialClosedStock(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("60"),
                    closed_stock=2,
                ),
                InitialClosedStock(
                    name="Fardo x12",
                    factor=12,
                    sale_price=Decimal("120"),
                    closed_stock=3,
                ),
            ),
        )
    )
    app = app_for(make_assistant(connection))

    open_products_section(app)

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{CLOSED_STOCK_LABEL}: 5" in rendered
    assert f"{CLOSED_STOCK_LABEL}: 48" not in rendered


def test_products_preserves_the_order_returned_by_the_service(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """Verifica que la vista no reordena el listado (T044)."""
    for index, name in enumerate(("Zeta", "Alfa", "Beta"), start=1):
        register_low_stock_product(
            product_service,
            sku=f"SKU-{index}",
            name=name,
            minimum_stock=0,
            loose_units=1,
        )
    app = app_for(make_assistant(connection))

    open_products_section(app)

    rendered = rendered_text(app)
    first = rendered.index("SKU: SKU-1")
    second = rendered.index("SKU: SKU-2")
    third = rendered.index("SKU: SKU-3")

    assert not app.exception
    assert first < second < third
    assert rendered.index(f"{NAME_LABEL}: Zeta") < rendered.index(
        f"{NAME_LABEL}: Alfa"
    )


def test_products_reports_the_real_count_without_truncating(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica el aviso de no paginación con el N real y sin truncar (C8, D1).

    Un listado de doce productos informa los doce y muestra también el último,
    de modo que ningún límite arbitrario oculte registros.
    """
    total = 12
    for index in range(1, total + 1):
        register_low_stock_product(
            product_service,
            sku=f"SKU-{index}",
            name=f"Producto {index}",
            minimum_stock=0,
            loose_units=1,
        )
    app = app_for(make_assistant(connection))

    open_products_section(app)

    rendered = rendered_text(app)
    messages = [info.value for info in app.info]

    assert not app.exception
    assert NON_PAGINATED_NOTICE_TEMPLATE.format(count=total) in messages
    assert len(app.text) == total
    assert "SKU: SKU-1" in rendered
    assert f"SKU: SKU-{total}" in rendered


def test_products_does_not_write(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica que listar y buscar no modifica la base (T044)."""
    app = app_for(make_assistant(connection))

    app.run()
    before = snapshot(connection)
    changes_before = connection.total_changes

    app.radio[0].set_value(PRODUCTS_HEADER).run()

    assert not app.exception
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_products_works_without_the_model_with_real_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Verifica Productos sin modelo, con datos reales (C2, C5).

    La lectura de la sesión se compone aunque el asistente no esté disponible;
    la sección muestra el producto real de la base configurada y la aplicación
    sigue informando la indisponibilidad del asistente.
    """
    database_path = configured_database_path(monkeypatch, tmp_path)
    seed_connection = connect(database_path)
    try:
        create_schema(seed_connection)
        seed_products = ProductService(
            products=SqliteProductRepository(seed_connection),
            presentations=SqlitePresentationRepository(seed_connection),
            movements=SqliteMovementRepository(seed_connection),
            transaction=partial(transaction, seed_connection),
        )
        seed_products.register(
            ProductRegistration(
                sku="SKU-1",
                name="Producto",
                base_unit="botella",
                base_sale_price=Decimal("100"),
                minimum_stock=20,
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
    finally:
        seed_connection.close()
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)

    app.run()

    session = app.session_state.get(SESSION_KEY)

    try:
        assert not app.exception
        assert len(app.error) == 1
        assert MISSING_KEY_MESSAGE in app.error[0].value

        app.radio[0].set_value(PRODUCTS_HEADER).run()

        rendered = rendered_text(app)

        assert not app.exception
        assert len(app.error) == 0
        assert [header.value for header in app.header] == [PRODUCTS_HEADER]
        assert f"{SKU_LABEL}: SKU-1" in rendered
        assert f"{NAME_LABEL}: Producto" in rendered
        assert f"{BASE_UNIT_LABEL}: botella" in rendered
        assert f"{MINIMUM_STOCK_LABEL}: 20" in rendered
        assert f"{LOOSE_UNITS_LABEL}: 4" in rendered
        assert f"{CLOSED_STOCK_LABEL}: 2" in rendered
        assert f"{STATUS_LABEL}: {ACTIVE_STATUS}" in rendered
    finally:
        if session is not None:
            session.connection.close()


def test_the_products_view_only_presents_the_received_data() -> None:
    """
    Verifica que Productos no consulta ni escribe por su cuenta (T044, G1).

    La vista recibe los datos ya consultados desde `main.py`: no importa los
    servicios, el dominio ni la persistencia, no llama a sus lecturas, no usa
    `st.session_state` y no contiene el punto de confirmación.
    """
    source = PRODUCTS_SECTION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert "st.session_state" not in source
    assert ".confirm(" not in source
    for module in imported_modules(tree):
        for prefix in FORBIDDEN_SECTION_IMPORTS:
            assert not (
                module == prefix or module.startswith(f"{prefix}.")
            ), f"products.py importa {module}"
    assert not called_names(tree) & {
        "list_products",
        "find_products_by_name",
        "find_product_by_sku",
        "get_product_presentations",
        "build_session",
        "open_connection",
        "transaction",
        "get_llm",
        "build_workflow",
    }


# --- Detalle de producto (T045, RF-09) ---

DETAIL_INSTANT_PATTERN: re.Pattern[str] = re.compile(
    rf"{DATE_LABEL}: \d{{4}}-\d{{2}}-\d{{2}} "
    r"\d{2}:\d{2}:\d{2} -03:00"
)
"""Fecha local del detalle: formato de T042 con el desplazamiento argentino."""


EMPTY_DETAIL_SCRIPT: str = """
from dataclasses import dataclass

from app.ui.sections.product_detail import render_product_detail


@dataclass(frozen=True)
class Product:
    id: int
    sku: str
    name: str
    base_unit: str
    minimum_stock: int
    loose_units: int
    active: bool


@dataclass(frozen=True)
class Stock:
    product: Product
    presentations: tuple
    total_equivalent: int


render_product_detail(
    stock=Stock(
        product=Product(1, "SKU-VACIO", "Sin datos", "unidad", 0, 0, True),
        presentations=(),
        total_equivalent=0,
    ),
    movements=(),
    price_changes=(),
)
"""
"""Script mínimo para comprobar los vacíos de la vista con datos vacíos."""


class CountingDetailInventoryService:
    """Espía de las lecturas que usa el detalle de producto (T045)."""

    def __init__(self, service: InventoryService) -> None:
        """Envuelve el servicio real, sin modificarlo."""
        self._service = service
        self.get_product_stock_calls = 0
        self.get_movement_history_calls = 0

    def reset(self) -> None:
        """Reinicia los contadores de las lecturas del detalle."""
        self.get_product_stock_calls = 0
        self.get_movement_history_calls = 0

    def get_product_stock(self, product_id: int) -> Any:
        """Cuenta y delega la lectura de existencias."""
        self.get_product_stock_calls += 1

        return self._service.get_product_stock(product_id)

    def get_movement_history(
        self,
        product_id: int,
        kind: MovementKind | str | None = None,
        *,
        period_start: Any = None,
        period_end: Any = None,
    ) -> list[MovementRecord]:
        """Cuenta y delega la lectura del historial de movimientos."""
        self.get_movement_history_calls += 1

        return self._service.get_movement_history(
            product_id,
            kind,
            period_start=period_start,
            period_end=period_end,
        )

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra lectura en el servicio real."""
        return getattr(self._service, name)


class CountingPriceHistoryService:
    """Espía de las lecturas del historial de precios (T045, T047)."""

    def __init__(self, service: PriceHistoryService) -> None:
        """Envuelve el servicio real, sin modificarlo."""
        self._service = service
        self.get_price_history_by_product_calls = 0
        self.get_price_history_by_presentation_calls = 0

    def reset(self) -> None:
        """Reinicia los contadores de las lecturas del historial."""
        self.get_price_history_by_product_calls = 0
        self.get_price_history_by_presentation_calls = 0

    def get_price_history_by_product(self, product_id: int) -> Any:
        """Cuenta y delega la lectura del historial del producto."""
        self.get_price_history_by_product_calls += 1

        return self._service.get_price_history_by_product(product_id)

    def get_price_history_by_presentation(
        self, presentation_id: int
    ) -> Any:
        """Cuenta y delega la lectura del historial de la presentación."""
        self.get_price_history_by_presentation_calls += 1

        return self._service.get_price_history_by_presentation(
            presentation_id
        )

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra lectura en el servicio real."""
        return getattr(self._service, name)


class FailingStockInventoryService:
    """Servicio que simula un producto inexistente al leer su detalle."""

    def __init__(self, service: InventoryService, error: Exception) -> None:
        """Envuelve el servicio real y conserva el error a inducir."""
        self._service = service
        self._error = error

    def get_product_stock(self, product_id: int) -> Any:
        """Induce el error de lectura del detalle del producto."""
        raise self._error

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra lectura en el servicio real."""
        return getattr(self._service, name)


def detail_assistant(
    connection: sqlite3.Connection,
) -> tuple[
    Assistant,
    CountingDetailInventoryService,
    CountingPriceHistoryService,
]:
    """Compone el asistente con espías del detalle de producto (T045)."""
    assistant = make_assistant(connection)
    counting_inventory = CountingDetailInventoryService(
        assistant.session.inventory_service
    )
    counting_history = CountingPriceHistoryService(
        assistant.session.price_history_service
    )
    assistant.session.inventory_service = counting_inventory
    assistant.session.price_history_service = counting_history

    return assistant, counting_inventory, counting_history


def open_product_detail(app: AppTest, product: ProductView) -> None:
    """Abre la sección Productos y elige el producto indicado (T045)."""
    open_products_section(app)
    app.selectbox[2].set_value(product_option_label(product)).run()


def test_product_detail_shows_presentations_and_the_derived_total(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica presentaciones, factor, stock cerrado y total equivalente (C9).

    El total equivalente es el derivado por el servicio —4 sueltas + 2 cajas
    de factor 6 = 16— y no la suma de stock cerrado (2). La presentación base
    se indica y el precio vigente no forma parte del detalle (D3).
    """
    app = app_for(make_assistant(connection))

    open_product_detail(app, catalog.product)

    rendered = rendered_text(app)

    assert not app.exception
    assert [subheader.value for subheader in app.subheader] == [
        PRODUCT_DETAIL_HEADER,
        PRESENTATIONS_HEADER,
        MOVEMENTS_HEADER,
        PRICE_HISTORY_HEADER,
    ]
    assert PRODUCT_SELECTOR_LABEL in [
        selectbox.label for selectbox in app.selectbox
    ]
    assert f"{PRESENTATION_LABEL}: Caja x6" in rendered
    assert f"{FACTOR_LABEL}: 6" in rendered
    assert f"{CLOSED_STOCK_LABEL}: 2" in rendered
    assert f"{LOOSE_UNITS_LABEL}: 4" in rendered
    assert f"{TOTAL_EQUIVALENT_LABEL}: 16" in rendered
    assert f"{TOTAL_EQUIVALENT_LABEL}: 6" not in rendered
    assert f"{BASE_PRESENTATION_LABEL}: {YES_TEXT}" in rendered
    assert "600.00" not in rendered
    assert "100.00" not in rendered


def test_product_detail_does_not_read_without_a_selection(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que el detalle no consulta servicios sin producto elegido (C4).

    El selector conserva su opción centinela, se informa el estado y no se
    ejecuta ninguna lectura del detalle ni se presenta texto de detalle.
    """
    assistant, counting_inventory, counting_history = detail_assistant(
        connection
    )
    app = app_for(assistant)

    open_products_section(app)
    counting_inventory.reset()
    counting_history.reset()
    app.run()

    assert not app.exception
    assert app.selectbox[2].value == NO_SELECTION_LABEL
    assert NO_PRODUCT_SELECTED_MESSAGE in [
        info.value for info in app.info
    ]
    assert [subheader.value for subheader in app.subheader] == []
    assert len(app.text) == 1
    assert counting_inventory.get_product_stock_calls == 0
    assert counting_inventory.get_movement_history_calls == 0
    assert counting_history.get_price_history_by_product_calls == 0


def test_product_detail_shows_a_real_zero_instead_of_the_absent_marker(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica que un cero real se muestra como `0` y no como ausente (D4)."""
    app = app_for(make_assistant(connection))

    open_product_detail(app, catalog.product)

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{CLOSED_STOCK_LABEL}: 0" in rendered
    assert ABSENT_MARKER not in rendered


def test_product_detail_shows_the_movement_data_with_cost_and_price(
    connection: sqlite3.Connection,
    catalog: Catalog,
    movement_service: MovementService,
) -> None:
    """
    Verifica tipo, cantidad, motivo, fecha local, costo y precio (D5).

    El costo y el precio aplicado existen en los movimientos reales: la
    entrada con costo y la salida con la copia del precio de venta vigente.
    """
    movement_service.register_loose_entry(
        catalog.product.id,
        quantity=2,
        reason="Compra",
        cost=Decimal("15.50"),
    )
    movement_service.register_loose_exit(
        catalog.product.id,
        quantity=1,
        reason="Venta",
    )
    app = app_for(make_assistant(connection))

    open_product_detail(app, catalog.product)

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{MOVEMENT_TYPE_LABEL}: Entrada suelta" in rendered
    assert f"{MOVEMENT_TYPE_LABEL}: Salida suelta" in rendered
    assert f"{QUANTITY_LABEL}: 2" in rendered
    assert f"{REASON_LABEL}: Compra" in rendered
    assert f"{COST_LABEL}: 15.50" in rendered
    assert f"{APPLIED_PRICE_LABEL}: 100.00" in rendered
    assert f"{PRESENTATION_LABEL}: botella" in rendered
    assert DETAIL_INSTANT_PATTERN.search(rendered)
    assert f"{INITIAL_STOCK_LABEL}: {YES_TEXT}" in rendered


def test_product_detail_shows_the_price_history_with_two_decimals(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentation_service: PresentationService,
) -> None:
    """
    Verifica precios anterior y nuevo, presentación, fecha y evento (D6).

    El historial se consulta por producto y los importes se formatean con dos
    decimales fijos, sin mostrar el precio de alta de la presentación.
    """
    presentation_service.change_sale_price(
        catalog.box.id, Decimal("650")
    )
    presentation_service.change_sale_price(
        catalog.box.id, Decimal("700.5")
    )
    app = app_for(make_assistant(connection))

    open_product_detail(app, catalog.product)

    rendered = rendered_text(app)
    messages = [info.value for info in app.info]

    assert not app.exception
    assert f"{PRICE_PREVIOUS_LABEL}: 600.00" in rendered
    assert f"{PRICE_NEW_LABEL}: 650.00" in rendered
    assert f"{PRICE_PREVIOUS_LABEL}: 650.00" in rendered
    assert f"{PRICE_NEW_LABEL}: 700.50" in rendered
    assert f"{PRESENTATION_LABEL}: Caja x6" in rendered
    assert f"{EVENT_LABEL}: {PRICE_CHANGE_EVENT_TYPE}" in rendered
    assert DETAIL_INSTANT_PATTERN.search(rendered)
    assert (
        PRICE_HISTORY_NOTICE_TEMPLATE.format(count=2) in messages
    )


def test_product_detail_keeps_the_chronological_order_of_the_history(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentation_service: PresentationService,
) -> None:
    """Verifica que el historial conserva el orden cronológico del servicio."""
    presentation_service.change_sale_price(
        catalog.box.id, Decimal("650")
    )
    presentation_service.change_sale_price(
        catalog.box.id, Decimal("700.5")
    )
    app = app_for(make_assistant(connection))

    open_product_detail(app, catalog.product)

    rendered = rendered_text(app)
    first = rendered.index(f"{PRICE_NEW_LABEL}: 650.00")
    second = rendered.index(f"{PRICE_NEW_LABEL}: 700.50")

    assert not app.exception
    assert first < second


def test_an_inactive_product_keeps_its_detail_available(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentation_service: PresentationService,
    movement_service: MovementService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
) -> None:
    """
    Verifica que un producto inactivo sigue siendo consultable (C9, RF-01).

    Se selecciona con el filtro «Inactivos» y el detalle muestra su estado,
    sus presentaciones, sus movimientos y su historial de precios. La consulta
    no rehabilita el producto ni lo excluye.
    """
    stored = product_service.register(
        ProductRegistration(
            sku="SKU-OFF",
            name="Producto inactivo",
            base_unit="unidad",
            base_sale_price=Decimal("10"),
            presentations=(
                InitialClosedStock(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("60"),
                    closed_stock=2,
                ),
            ),
        )
    )
    _, box = presentations.list_by_product(stored.id)
    presentation_service.change_sale_price(box.id, Decimal("65"))
    movement_service.register_loose_entry(
        stored.id,
        quantity=2,
        reason="Compra histórica",
    )
    product_service.deactivate(stored.id)
    inactive = products.get(stored.id)
    label = product_option_label(inactive)
    app = app_for(make_assistant(connection))

    open_products_section(app)
    app.selectbox[1].set_value(ACTIVE_STATUS_FILTER).run()
    assert PRODUCT_SELECTOR_LABEL not in [
        selectbox.label for selectbox in app.selectbox
    ]

    app.selectbox[1].set_value(INACTIVE_STATUS_FILTER).run()
    assert label in app.selectbox[2].options

    app.selectbox[2].set_value(label).run()

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{STATUS_LABEL}: {INACTIVE_STATUS}" in rendered
    assert f"{PRESENTATION_LABEL}: Caja x6" in rendered
    assert f"{REASON_LABEL}: Compra histórica" in rendered
    assert f"{EVENT_LABEL}: {PRICE_CHANGE_EVENT_TYPE}" in rendered
    assert f"{PRICE_NEW_LABEL}: 65.00" in rendered


def test_product_detail_distinguishes_the_three_empty_states() -> None:
    """
    Verifica los vacíos propios de presentaciones, movimientos e historial.

    La vista muestra un mensaje distinto para cada lista vacía, sin presentar
    ninguno de ellos como un error de lectura ni inventar valores.
    """
    app = AppTest.from_string(EMPTY_DETAIL_SCRIPT).run()

    messages = [info.value for info in app.info]

    assert not app.exception
    assert [subheader.value for subheader in app.subheader] == [
        PRODUCT_DETAIL_HEADER,
        PRESENTATIONS_HEADER,
        MOVEMENTS_HEADER,
        PRICE_HISTORY_HEADER,
    ]
    assert messages == [
        EMPTY_PRESENTATIONS_MESSAGE,
        EMPTY_MOVEMENTS_MESSAGE,
        EMPTY_PRICE_HISTORY_MESSAGE,
    ]
    assert len(set(messages)) == 3
    assert len(app.error) == 0


def test_product_detail_reports_the_real_counts_without_truncating(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentation_service: PresentationService,
    movement_service: MovementService,
) -> None:
    """
    Verifica los avisos C8 con N real y sin truncar (D8).

    Tres presentaciones, doce movimientos y dos cambios de precio se informan
    con su número exacto; el último movimiento se muestra completo.
    """
    presentation_service.add(
        catalog.product.id,
        name="Fardo x12",
        factor=12,
        sale_price=Decimal("120"),
    )
    for index in range(1, 11):
        movement_service.register_loose_entry(
            catalog.product.id,
            quantity=1,
            reason=f"Movimiento {index}",
        )
    presentation_service.change_sale_price(
        catalog.box.id, Decimal("650")
    )
    presentation_service.change_sale_price(
        catalog.box.id, Decimal("700.5")
    )
    app = app_for(make_assistant(connection))

    open_product_detail(app, catalog.product)

    rendered = rendered_text(app)
    messages = [info.value for info in app.info]

    assert not app.exception
    assert (
        PRESENTATIONS_NOTICE_TEMPLATE.format(count=3) in messages
    )
    assert MOVEMENTS_NOTICE_TEMPLATE.format(count=12) in messages
    assert (
        PRICE_HISTORY_NOTICE_TEMPLATE.format(count=2) in messages
    )
    assert f"{PRESENTATION_LABEL}: Fardo x12" in rendered
    assert f"{REASON_LABEL}: Movimiento 10" in rendered


def test_product_detail_omits_absent_optional_fields_but_keeps_zero(
    connection: sqlite3.Connection,
    catalog: Catalog,
    movement_service: MovementService,
) -> None:
    """
    Verifica que los campos opcionales ausentes no se muestran (D5, C4).

    Solo la entrada con costo cero tiene costo: se muestra `0.00`, no se
    muestra ningún precio aplicado y no aparece `None`.
    """
    movement_service.register_loose_entry(
        catalog.product.id,
        quantity=1,
        reason="Entrada sin costo adicional",
        cost=Decimal("0"),
    )
    app = app_for(make_assistant(connection))

    open_product_detail(app, catalog.product)

    rendered = rendered_text(app)

    assert not app.exception
    assert rendered.count(f"{COST_LABEL}:") == 1
    assert f"{COST_LABEL}: 0.00" in rendered
    assert APPLIED_PRICE_LABEL not in rendered
    assert "None" not in rendered


def test_product_detail_reads_each_service_once_and_does_not_write(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica una lectura de cada método para el producto elegido (T045).

    La selección no escribe: la base y el contador de cambios quedan iguales
    antes y después de abrir el detalle.
    """
    assistant, counting_inventory, counting_history = detail_assistant(
        connection
    )
    app = app_for(assistant)

    open_products_section(app)
    counting_inventory.reset()
    counting_history.reset()
    before = snapshot(connection)
    changes_before = connection.total_changes

    app.selectbox[2].set_value(
        product_option_label(catalog.product)
    ).run()

    assert not app.exception
    assert counting_inventory.get_product_stock_calls == 1
    assert counting_inventory.get_movement_history_calls == 1
    assert counting_history.get_price_history_by_product_calls == 1
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_an_empty_catalog_does_not_render_the_detail_selector(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica que sin catálogo no se dibuja el selector del detalle (D8, C4).

    Prevalece el mensaje de catálogo vacío de T044 y no aparecen ni el selector
    del detalle ni el aviso de falta de selección.
    """
    app = app_for(make_assistant(connection))

    open_products_section(app)

    messages = [info.value for info in app.info]
    labels = [selectbox.label for selectbox in app.selectbox]
    subheaders = [subheader.value for subheader in app.subheader]

    assert not app.exception
    assert EMPTY_PRODUCTS_CATALOG_MESSAGE in messages
    assert PRODUCT_SELECTOR_LABEL not in labels
    assert NO_PRODUCT_SELECTED_MESSAGE not in messages
    assert PRODUCT_DETAIL_HEADER not in subheaders
    assert rendered_text(app) == ""


def test_a_search_without_results_does_not_render_the_detail_selector(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que una búsqueda sin resultados oculta el detalle (D8, C4).

    El listado informa su mensaje propio y no se ofrece el selector del detalle
    ni se muestra el encabezado del producto.
    """
    app = app_for(make_assistant(connection))

    open_products_section(app)
    app.text_input[0].set_value("No existe").run()

    messages = [info.value for info in app.info]
    labels = [selectbox.label for selectbox in app.selectbox]
    subheaders = [subheader.value for subheader in app.subheader]

    assert not app.exception
    assert EMPTY_PRODUCTS_SEARCH_MESSAGE in messages
    assert PRODUCT_SELECTOR_LABEL not in labels
    assert NO_PRODUCT_SELECTED_MESSAGE not in messages
    assert PRODUCT_DETAIL_HEADER not in subheaders
    assert rendered_text(app) == ""


def test_a_filter_without_results_does_not_render_the_detail_selector(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que un filtro sin resultados oculta el detalle (D8, C4).

    El listado informa su mensaje propio y no se ofrece el selector del detalle
    ni se muestra el encabezado del producto.
    """
    app = app_for(make_assistant(connection))

    open_products_section(app)
    app.selectbox[1].set_value(INACTIVE_STATUS_FILTER).run()

    messages = [info.value for info in app.info]
    labels = [selectbox.label for selectbox in app.selectbox]
    subheaders = [subheader.value for subheader in app.subheader]

    assert not app.exception
    assert EMPTY_PRODUCTS_FILTER_MESSAGE in messages
    assert PRODUCT_SELECTOR_LABEL not in labels
    assert NO_PRODUCT_SELECTED_MESSAGE not in messages
    assert PRODUCT_DETAIL_HEADER not in subheaders
    assert rendered_text(app) == ""


def test_the_detail_is_not_read_after_the_selected_product_is_excluded(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que excluir el producto elegido retira su detalle (D8, C4).

    Tras seleccionar el producto y ver su detalle, una búsqueda sin resultados
    deja la lista vacía: no se conserva el detalle anterior, no se dibuja el
    selector y no se ejecuta ninguna lectura nueva del detalle.
    """
    assistant, counting_inventory, counting_history = detail_assistant(
        connection
    )
    app = app_for(assistant)

    open_products_section(app)
    app.selectbox[2].set_value(
        product_option_label(catalog.product)
    ).run()

    assert not app.exception
    assert PRODUCT_DETAIL_HEADER in [
        subheader.value for subheader in app.subheader
    ]
    assert f"{TOTAL_EQUIVALENT_LABEL}: 16" in rendered_text(app)

    counting_inventory.reset()
    counting_history.reset()
    app.text_input[0].set_value("No existe").run()

    messages = [info.value for info in app.info]
    labels = [selectbox.label for selectbox in app.selectbox]
    subheaders = [subheader.value for subheader in app.subheader]

    assert not app.exception
    assert EMPTY_PRODUCTS_SEARCH_MESSAGE in messages
    assert PRODUCT_SELECTOR_LABEL not in labels
    assert PRODUCT_DETAIL_HEADER not in subheaders
    assert TOTAL_EQUIVALENT_LABEL not in rendered_text(app)
    assert counting_inventory.get_product_stock_calls == 0
    assert counting_inventory.get_movement_history_calls == 0
    assert counting_history.get_price_history_by_product_calls == 0


def test_the_detail_selection_is_discarded_after_an_empty_list(
    connection: sqlite3.Connection,
    catalog: Catalog,
    product_service: ProductService,
) -> None:
    """
    Verifica el tránsito lista no vacía → vacía → no vacía (D8, C4).

    Streamlit descarta el estado del selector que deja de renderizarse: al
    volver a una lista con productos la opción vuelve al centinela, no se
    muestra ningún detalle anterior ni se ejecuta una lectura nueva. Al elegir
    un producto presente, su detalle es el correcto.
    """
    beta = register_low_stock_product(
        product_service,
        sku="SKU-B",
        name="Beta",
        minimum_stock=0,
        loose_units=3,
    )
    assistant, counting_inventory, counting_history = detail_assistant(
        connection
    )
    app = app_for(assistant)

    open_products_section(app)
    app.selectbox[2].set_value(
        product_option_label(catalog.product)
    ).run()

    assert not app.exception
    assert f"{TOTAL_EQUIVALENT_LABEL}: 16" in rendered_text(app)

    counting_inventory.reset()
    counting_history.reset()
    app.selectbox[1].set_value(INACTIVE_STATUS_FILTER).run()

    assert not app.exception
    assert EMPTY_PRODUCTS_FILTER_MESSAGE in [
        info.value for info in app.info
    ]
    assert PRODUCT_SELECTOR_LABEL not in [
        selectbox.label for selectbox in app.selectbox
    ]
    assert PRODUCT_DETAIL_HEADER not in [
        subheader.value for subheader in app.subheader
    ]
    assert TOTAL_EQUIVALENT_LABEL not in rendered_text(app)
    assert counting_inventory.get_product_stock_calls == 0
    assert counting_inventory.get_movement_history_calls == 0
    assert counting_history.get_price_history_by_product_calls == 0

    app.selectbox[1].set_value(ALL_STATUS_FILTER).run()

    selector = next(
        selectbox
        for selectbox in app.selectbox
        if selectbox.label == PRODUCT_SELECTOR_LABEL
    )

    assert not app.exception
    assert selector.value == NO_SELECTION_LABEL
    assert selector.value in selector.options
    assert PRODUCT_DETAIL_HEADER not in [
        subheader.value for subheader in app.subheader
    ]
    assert TOTAL_EQUIVALENT_LABEL not in rendered_text(app)
    assert counting_inventory.get_product_stock_calls == 0
    assert counting_inventory.get_movement_history_calls == 0
    assert counting_history.get_price_history_by_product_calls == 0

    app.text_input[0].set_value("Beta").run()

    rendered = rendered_text(app)
    selector = next(
        selectbox
        for selectbox in app.selectbox
        if selectbox.label == PRODUCT_SELECTOR_LABEL
    )

    assert not app.exception
    assert selector.value == NO_SELECTION_LABEL
    assert f"{SKU_LABEL}: SKU-B" in rendered
    assert f"{SKU_LABEL}: SKU-1" not in rendered
    assert PRODUCT_DETAIL_HEADER not in [
        subheader.value for subheader in app.subheader
    ]

    app.selectbox[2].set_value(product_option_label(beta)).run()

    rendered = rendered_text(app)

    assert not app.exception
    assert PRODUCT_DETAIL_HEADER in [
        subheader.value for subheader in app.subheader
    ]
    assert f"{NAME_LABEL}: Beta" in rendered
    assert f"{TOTAL_EQUIVALENT_LABEL}: 3" in rendered
    assert f"{SKU_LABEL}: SKU-1" not in rendered
    assert counting_inventory.get_product_stock_calls == 1
    assert counting_inventory.get_movement_history_calls == 1
    assert counting_history.get_price_history_by_product_calls == 1


def test_product_detail_works_without_the_model_with_real_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Verifica el detalle sin modelo configurado (C2, C9).

    La lectura de la sesión se compone con la base configurada y el detalle
    muestra datos reales aunque el asistente no esté disponible.
    """
    database_path = configured_database_path(monkeypatch, tmp_path)
    seed_connection = connect(database_path)
    try:
        create_schema(seed_connection)
        seed_products = ProductService(
            products=SqliteProductRepository(seed_connection),
            presentations=SqlitePresentationRepository(seed_connection),
            movements=SqliteMovementRepository(seed_connection),
            transaction=partial(transaction, seed_connection),
        )
        seed_presentations = SqlitePresentationRepository(seed_connection)
        seed_presentation_service = PresentationService(
            products=SqliteProductRepository(seed_connection),
            presentations=seed_presentations,
            movements=SqliteMovementRepository(seed_connection),
            price_history=SqlitePriceHistoryRepository(seed_connection),
            transaction=partial(transaction, seed_connection),
        )
        stored = seed_products.register(
            ProductRegistration(
                sku="SKU-1",
                name="Producto",
                base_unit="botella",
                base_sale_price=Decimal("100"),
                minimum_stock=20,
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
        _, box = seed_presentations.list_by_product(stored.id)
        seed_presentation_service.change_sale_price(
            box.id, Decimal("650")
        )
    finally:
        seed_connection.close()
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)

    app.run()

    session = app.session_state.get(SESSION_KEY)

    try:
        assert not app.exception
        assert len(app.error) == 1
        assert MISSING_KEY_MESSAGE in app.error[0].value

        open_products_section(app)
        app.selectbox[2].set_value(
            product_option_label(stored)
        ).run()

        rendered = rendered_text(app)

        assert not app.exception
        assert len(app.error) == 0
        assert f"{TOTAL_EQUIVALENT_LABEL}: 16" in rendered
        assert f"{PRESENTATION_LABEL}: Caja x6" in rendered
        assert f"{PRICE_NEW_LABEL}: 650.00" in rendered
    finally:
        if session is not None:
            session.connection.close()


def test_a_detail_read_error_is_not_presented_as_an_empty_history(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que un `RecordNotFoundError` se muestra como error (C21).

    La lectura fallida del detalle no se confunde con una presentación, un
    movimiento o un historial vacío: el error se informa con su motivo.
    """
    assistant = make_assistant(connection)
    reason = f"No existe el producto con id {catalog.product.id}."
    assistant.session.inventory_service = FailingStockInventoryService(
        assistant.session.inventory_service,
        RecordNotFoundError(reason),
    )
    app = app_for(assistant)

    open_product_detail(app, catalog.product)

    messages = [info.value for info in app.info]

    assert not app.exception
    assert [error.value for error in app.error] == [
        PRODUCT_DETAIL_ERROR_TEMPLATE.format(reason=reason)
    ]
    assert [subheader.value for subheader in app.subheader] == [
        PRODUCT_DETAIL_HEADER
    ]
    assert EMPTY_PRESENTATIONS_MESSAGE not in messages
    assert EMPTY_MOVEMENTS_MESSAGE not in messages
    assert EMPTY_PRICE_HISTORY_MESSAGE not in messages


@pytest.mark.parametrize(
    ("kind", "expected"),
    (
        (MovementKind.CLOSED_ENTRY, "Entrada cerrada"),
        (MovementKind.LOOSE_ENTRY, "Entrada suelta"),
        (MovementKind.CLOSED_EXIT, "Salida cerrada"),
        (MovementKind.LOOSE_EXIT, "Salida suelta"),
        (MovementKind.APERTURE, "Apertura"),
        (MovementKind.COMPENSATION, "Compensación"),
    ),
)
def test_movement_kind_label_translates_each_persisted_kind(
    kind: MovementKind,
    expected: str,
) -> None:
    """Verifica la traducción de los seis tipos vigentes (D5)."""
    assert movement_kind_label(kind) == expected


def test_the_product_detail_view_only_presents_the_received_data() -> None:
    """
    Verifica que el detalle no consulta, no escribe ni confirma (T045, D9).

    La vista recibe los datos ya consultados desde `main.py`: no importa los
    servicios, el dominio ni la persistencia, no llama a sus lecturas, no usa
    `st.session_state` y no contiene el punto de confirmación.
    """
    source = PRODUCT_DETAIL_SECTION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert PRODUCT_DETAIL_SECTION_PATH.exists()
    assert "st.session_state" not in source
    assert ".confirm(" not in source
    for module in imported_modules(tree):
        for prefix in FORBIDDEN_SECTION_IMPORTS:
            assert not (
                module == prefix or module.startswith(f"{prefix}.")
            ), f"product_detail.py importa {module}"
    assert not called_names(tree) & {
        "list_products",
        "find_products_by_name",
        "find_product_by_sku",
        "get_product_presentations",
        "get_product_stock",
        "get_movement_history",
        "get_price_history_by_product",
        "build_session",
        "open_connection",
        "transaction",
        "get_llm",
        "build_workflow",
    }


# --- Sección Movimientos: filtros y período local (T046, RF-09) ---

LOCAL_TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")
"""Zona horaria con la que se construyen los instantes de prueba (RF-03)."""

MOVEMENT_KIND_LABELS_EXPECTED: tuple[str, ...] = (
    "Entrada cerrada",
    "Entrada suelta",
    "Salida cerrada",
    "Salida suelta",
    "Apertura",
    "Compensación",
)
"""Etiquetas de los seis tipos vigentes, en el orden del selector (D3)."""

MOVEMENT_KIND_VALUE_LITERALS: tuple[str, ...] = (
    "entrada_cerrada",
    "entrada_suelta",
    "salida_cerrada",
    "salida_suelta",
    "apertura",
    "compensacion",
)
"""Valores persistidos de los seis tipos vigentes (RF-03, D3)."""


class CountingMovementsInventoryService:
    """Espía de las lecturas usadas por la sección Movimientos (T046)."""

    def __init__(self, service: InventoryService) -> None:
        """Envuelve el servicio real, sin modificarlo."""
        self._service = service
        self.list_products_calls = 0
        self.get_movement_history_calls = 0
        self.get_product_presentations_calls = 0
        self.history_calls: list[
            tuple[int, str | None, Any, Any]
        ] = []

    def reset(self) -> None:
        """Reinicia los contadores y los argumentos registrados."""
        self.list_products_calls = 0
        self.get_movement_history_calls = 0
        self.get_product_presentations_calls = 0
        self.history_calls.clear()

    def list_products(self) -> list[ProductRecord]:
        """Cuenta la lectura del catálogo y delega en el servicio real."""
        self.list_products_calls += 1

        return self._service.list_products()

    def get_movement_history(
        self,
        product_id: int,
        kind: str | None = None,
        *,
        period_start: Any = None,
        period_end: Any = None,
    ) -> list[MovementRecord]:
        """Cuenta la consulta del historial y registra sus argumentos."""
        self.get_movement_history_calls += 1
        self.history_calls.append(
            (product_id, kind, period_start, period_end)
        )

        return self._service.get_movement_history(
            product_id,
            kind,
            period_start=period_start,
            period_end=period_end,
        )

    def get_product_presentations(
        self,
        product_id: int,
    ) -> tuple[PresentationRecord, ...]:
        """Cuenta la lectura de presentaciones y delega en el servicio real."""
        self.get_product_presentations_calls += 1

        return self._service.get_product_presentations(product_id)

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra lectura en el servicio real."""
        return getattr(self._service, name)


class FixedMovementInventoryService:
    """Servicio que devuelve un movimiento con una presentación ajena."""

    def __init__(
        self,
        service: InventoryService,
        movement: MovementRecord,
    ) -> None:
        """Envuelve el servicio real y conserva el movimiento a devolver."""
        self._service = service
        self._movement = movement

    def get_movement_history(
        self,
        product_id: int,
        kind: str | None = None,
        *,
        period_start: Any = None,
        period_end: Any = None,
    ) -> list[MovementRecord]:
        """Devuelve el movimiento fijado, sin consultar el historial real."""
        return [self._movement]

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra lectura en el servicio real."""
        return getattr(self._service, name)


class FailingMovementInventoryService:
    """Servicio que simula un error al leer el historial (T046, C21)."""

    def __init__(
        self,
        service: InventoryService,
        error: Exception,
    ) -> None:
        """Envuelve el servicio real y conserva el error a inducir."""
        self._service = service
        self._error = error

    def get_movement_history(
        self,
        product_id: int,
        kind: str | None = None,
        *,
        period_start: Any = None,
        period_end: Any = None,
    ) -> list[MovementRecord]:
        """Induce el error de lectura del historial."""
        raise self._error

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra lectura en el servicio real."""
        return getattr(self._service, name)


def movements_assistant(
    connection: sqlite3.Connection,
) -> tuple[Assistant, CountingMovementsInventoryService]:
    """Compone el asistente con el espía de la sección Movimientos (T046)."""
    assistant = make_assistant(connection)
    counting_inventory = CountingMovementsInventoryService(
        assistant.session.inventory_service
    )
    assistant.session.inventory_service = counting_inventory

    return assistant, counting_inventory


def open_movements_section(app: AppTest) -> None:
    """Abre la sección Movimientos desde la navegación lateral (T046)."""
    app.run()
    app.radio[0].set_value("Movimientos").run()


def movement_selectbox(app: AppTest, label: str) -> Any:
    """Devuelve el selector de la sección Movimientos con la etiqueta dada."""
    return next(
        selectbox
        for selectbox in app.selectbox
        if selectbox.label == label
    )


def movement_date_input(app: AppTest, label: str) -> Any:
    """Devuelve el control de fecha de la sección Movimientos."""
    return next(
        widget for widget in app.date_input if widget.label == label
    )


def choose_movement_product(app: AppTest, product: ProductRecord) -> None:
    """Elige el producto indicado en el selector de Movimientos (D1)."""
    movement_selectbox(app, PRODUCT_FILTER_LABEL).set_value(
        product_option_label(product)
    ).run()


def set_movement_period(
    app: AppTest,
    start_date: date | None,
    end_date: date | None,
) -> None:
    """Aplica el período indicado en los dos controles de fecha (D2)."""
    movement_date_input(app, START_DATE_LABEL).set_value(start_date)
    movement_date_input(app, END_DATE_LABEL).set_value(end_date)
    app.run()


def local_instant(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    microsecond: int = 0,
) -> datetime:
    """Construye un instante con la zona local argentina (RF-03)."""
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        second,
        microsecond,
        tzinfo=LOCAL_TIMEZONE,
    )


def add_test_movement(
    movements: SqliteMovementRepository,
    product_id: int,
    presentation_id: int,
    kind: MovementKind,
    reason: str,
    occurred_at: datetime,
    *,
    quantity: int = 1,
    cost: Decimal | None = None,
    applied_price: Decimal | None = None,
    original_movement_id: int | None = None,
) -> MovementRecord:
    """Registra un movimiento real en la base de la prueba (T046)."""
    return movements.add(
        product_id=product_id,
        presentation_id=presentation_id,
        kind=kind,
        quantity=quantity,
        reason=reason,
        occurred_at=occurred_at,
        cost=cost,
        applied_price=applied_price,
        original_movement_id=original_movement_id,
    )


def zero_stock_product(
    product_service: ProductService,
    *,
    sku: str,
    name: str,
) -> ProductRecord:
    """Registra un producto sin stock inicial, sin movimientos."""
    return register_low_stock_product(
        product_service,
        sku=sku,
        name=name,
        minimum_stock=0,
        loose_units=0,
    )


def seed_every_kind(
    movements: SqliteMovementRepository,
    presentations: SqlitePresentationRepository,
    product: ProductRecord,
) -> dict[MovementKind, str]:
    """
    Registra un movimiento de cada tipo vigente y devuelve sus motivos.

    La compensación se vincula a una entrada suelta real para cumplir la
    restricción del esquema y representar un movimiento histórico consultable.
    """
    base = presentations.list_by_product(product.id)[0]
    reasons = {
        MovementKind.CLOSED_ENTRY: "Tipo entrada cerrada",
        MovementKind.LOOSE_ENTRY: "Tipo entrada suelta",
        MovementKind.CLOSED_EXIT: "Tipo salida cerrada",
        MovementKind.LOOSE_EXIT: "Tipo salida suelta",
        MovementKind.APERTURE: "Tipo apertura",
    }
    stored: dict[MovementKind, MovementRecord] = {}
    for index, (kind, reason) in enumerate(reasons.items()):
        stored[kind] = add_test_movement(
            movements,
            product.id,
            base.id,
            kind,
            reason,
            local_instant(2026, 3, 10, 8, index),
        )

    compensation_reason = "Tipo compensación"
    add_test_movement(
        movements,
        product.id,
        base.id,
        MovementKind.COMPENSATION,
        compensation_reason,
        local_instant(2026, 3, 10, 9),
        original_movement_id=stored[MovementKind.LOOSE_ENTRY].id,
    )

    return {
        **{kind: reason for kind, reason in reasons.items()},
        MovementKind.COMPENSATION: compensation_reason,
    }


def test_movements_kind_selector_offers_all_and_the_six_current_kinds(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica las opciones del tipo y los seis valores vigentes (T046, D3).

    Las opciones se comparan con literales: «Todos los tipos» primero y las
    seis etiquetas de los `kind` de RF-03, sin incorporar valores nuevos.
    """
    product = zero_stock_product(
        product_service, sku="SKU-KINDS", name="Tipos"
    )
    app = app_for(make_assistant(connection))

    open_movements_section(app)
    choose_movement_product(app, product)

    kind_selectbox = movement_selectbox(app, KIND_FILTER_LABEL)

    assert not app.exception
    assert tuple(kind_selectbox.options) == (
        ALL_KINDS_LABEL,
        *MOVEMENT_KIND_LABELS_EXPECTED,
    )
    assert KIND_FILTER_OPTIONS == (
        ALL_KINDS_LABEL,
        *MOVEMENT_KIND_LABELS_EXPECTED,
    )
    assert MOVEMENT_KIND_VALUES == MOVEMENT_KIND_VALUE_LITERALS
    assert kind_selectbox.value == ALL_KINDS_LABEL


def test_movements_shows_only_the_chosen_product_in_service_order(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica el historial del producto elegido y su orden (T046, D6).

    Solo se muestran los movimientos del producto seleccionado y en el orden
    cronológico del servicio, sin reordenar ni mezclar otro producto.
    """
    alpha = zero_stock_product(
        product_service, sku="SKU-ALPHA", name="Alfa"
    )
    beta = zero_stock_product(
        product_service, sku="SKU-BETA", name="Beta"
    )
    alpha_base = presentations.list_by_product(alpha.id)[0]
    beta_base = presentations.list_by_product(beta.id)[0]
    add_test_movement(
        movements,
        alpha.id,
        alpha_base.id,
        MovementKind.LOOSE_ENTRY,
        "Alfa primero",
        local_instant(2026, 3, 10, 9),
    )
    add_test_movement(
        movements,
        beta.id,
        beta_base.id,
        MovementKind.LOOSE_ENTRY,
        "Beta intercalado",
        local_instant(2026, 3, 10, 10),
    )
    add_test_movement(
        movements,
        alpha.id,
        alpha_base.id,
        MovementKind.LOOSE_ENTRY,
        "Alfa último",
        local_instant(2026, 3, 10, 11),
    )
    app = app_for(make_assistant(connection))

    open_movements_section(app)
    choose_movement_product(app, alpha)

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{REASON_LABEL}: Alfa primero" in rendered
    assert f"{REASON_LABEL}: Alfa último" in rendered
    assert "Beta intercalado" not in rendered
    assert rendered.index("Alfa primero") < rendered.index("Alfa último")
    assert rendered.count(f"{REASON_LABEL}:") == 2


@pytest.mark.parametrize(
    ("kind", "label", "reason"),
    (
        (
            MovementKind.CLOSED_ENTRY,
            "Entrada cerrada",
            "Tipo entrada cerrada",
        ),
        (
            MovementKind.LOOSE_ENTRY,
            "Entrada suelta",
            "Tipo entrada suelta",
        ),
        (
            MovementKind.CLOSED_EXIT,
            "Salida cerrada",
            "Tipo salida cerrada",
        ),
        (
            MovementKind.LOOSE_EXIT,
            "Salida suelta",
            "Tipo salida suelta",
        ),
        (MovementKind.APERTURE, "Apertura", "Tipo apertura"),
        (
            MovementKind.COMPENSATION,
            "Compensación",
            "Tipo compensación",
        ),
    ),
)
def test_movements_filters_by_each_current_kind(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    kind: MovementKind,
    label: str,
    reason: str,
) -> None:
    """
    Verifica el filtro por cada uno de los seis tipos vigentes (T046, D3).

    Con un movimiento real de cada tipo, el filtro deja visible solo el tipo
    elegido y traduce la etiqueta al valor persistido del servicio.
    """
    product = zero_stock_product(
        product_service, sku=f"SKU-{kind.name}", name="Tipos"
    )
    reasons = seed_every_kind(movements, presentations, product)
    assistant, counting_inventory = movements_assistant(connection)
    app = app_for(assistant)

    open_movements_section(app)
    choose_movement_product(app, product)
    counting_inventory.reset()
    movement_selectbox(app, KIND_FILTER_LABEL).set_value(label).run()

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{REASON_LABEL}: {reason}" in rendered
    assert rendered.count(f"{REASON_LABEL}:") == 1
    assert counting_inventory.get_movement_history_calls == 1
    assert counting_inventory.history_calls[0][1] == kind.value
    for other_kind, other_reason in reasons.items():
        if other_kind is not kind:
            assert other_reason not in rendered


def test_movements_period_is_local_and_excludes_the_next_midnight(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica la semántica `[inicio, fin)` en hora local (T046, C17, D2).

    Con «Desde» y «Hasta» el 10 de marzo de 2026 se incluye el inicio, el
    último instante de ese día local y se excluyen la medianoche siguiente y un
    instante UTC que cae el día local anterior. La conversión enviada al
    servicio es única, sin zona horaria, para que la persistencia la interprete
    en la zona local argentina.
    """
    product = zero_stock_product(
        product_service, sku="SKU-PERIOD", name="Período"
    )
    base = presentations.list_by_product(product.id)[0]
    included = (
        "Inicio incluido",
        "Último instante incluido",
    )
    excluded = (
        "Medianoche siguiente excluida",
        "Instante UTC del día local anterior",
    )
    for reason, occurred_at in (
        (included[0], local_instant(2026, 3, 10, 0, 0)),
        (
            included[1],
            local_instant(2026, 3, 10, 23, 59, 59, 999999),
        ),
        (excluded[0], local_instant(2026, 3, 11, 0, 0)),
        (
            excluded[1],
            datetime(2026, 3, 10, 2, 30, tzinfo=timezone.utc),
        ),
    ):
        add_test_movement(
            movements,
            product.id,
            base.id,
            MovementKind.LOOSE_ENTRY,
            reason,
            occurred_at,
        )
    assistant, counting_inventory = movements_assistant(connection)
    app = app_for(assistant)

    open_movements_section(app)
    choose_movement_product(app, product)
    counting_inventory.reset()
    set_movement_period(
        app, date(2026, 3, 10), date(2026, 3, 10)
    )

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{REASON_LABEL}: {included[0]}" in rendered
    assert f"{REASON_LABEL}: {included[1]}" in rendered
    assert excluded[0] not in rendered
    assert excluded[1] not in rendered
    assert counting_inventory.get_movement_history_calls == 1
    product_id, kind, period_start, period_end = (
        counting_inventory.history_calls[0]
    )
    assert product_id == product.id
    assert kind is None
    assert period_start == datetime(2026, 3, 10, 0, 0)
    assert period_end == datetime(2026, 3, 11, 0, 0)
    assert period_start.tzinfo is None
    assert period_end.tzinfo is None


def test_movements_combines_product_kind_and_period_with_and(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica el AND de producto, tipo y período (T046, C17).

    Con dos productos y movimientos de entrada y salida, el resultado solo
    contiene el movimiento que cumple los tres filtros; los demás no se
    presentan.
    """
    alpha = zero_stock_product(
        product_service, sku="SKU-AND-A", name="Alfa AND"
    )
    beta = zero_stock_product(
        product_service, sku="SKU-AND-B", name="Beta AND"
    )
    alpha_base = presentations.list_by_product(alpha.id)[0]
    beta_base = presentations.list_by_product(beta.id)[0]
    add_test_movement(
        movements,
        alpha.id,
        alpha_base.id,
        MovementKind.LOOSE_ENTRY,
        "Alfa entrada del día",
        local_instant(2026, 3, 10, 10),
    )
    add_test_movement(
        movements,
        alpha.id,
        alpha_base.id,
        MovementKind.LOOSE_EXIT,
        "Alfa salida del día",
        local_instant(2026, 3, 10, 11),
    )
    add_test_movement(
        movements,
        alpha.id,
        alpha_base.id,
        MovementKind.LOOSE_ENTRY,
        "Alfa entrada de otro día",
        local_instant(2026, 3, 11, 10),
    )
    add_test_movement(
        movements,
        beta.id,
        beta_base.id,
        MovementKind.LOOSE_ENTRY,
        "Beta entrada del día",
        local_instant(2026, 3, 10, 10),
    )
    assistant, counting_inventory = movements_assistant(connection)
    app = app_for(assistant)

    open_movements_section(app)
    choose_movement_product(app, alpha)
    counting_inventory.reset()
    movement_selectbox(app, KIND_FILTER_LABEL).set_value(
        "Entrada suelta"
    )
    set_movement_period(
        app, date(2026, 3, 10), date(2026, 3, 10)
    )

    rendered = rendered_text(app)

    assert not app.exception
    assert "Alfa entrada del día" in rendered
    assert "Alfa salida del día" not in rendered
    assert "Alfa entrada de otro día" not in rendered
    assert "Beta entrada del día" not in rendered
    assert counting_inventory.get_movement_history_calls == 1
    product_id, kind, period_start, period_end = (
        counting_inventory.history_calls[0]
    )
    assert product_id == alpha.id
    assert kind == MovementKind.LOOSE_ENTRY.value
    assert period_start == datetime(2026, 3, 10, 0, 0)
    assert period_end == datetime(2026, 3, 11, 0, 0)
    assert period_start.tzinfo is None
    assert period_end.tzinfo is None


def test_movements_without_a_period_and_with_a_same_day_period(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica la ausencia de período y un período de un solo día (T046, D3).

    Sin fechas se muestran los movimientos de todos los días; con «Desde» y
    «Hasta» iguales se muestra únicamente ese día local.
    """
    product = zero_stock_product(
        product_service, sku="SKU-SAMEDAY", name="Mismo día"
    )
    base = presentations.list_by_product(product.id)[0]
    for reason, occurred_at in (
        ("Día anterior", local_instant(2026, 3, 9, 12)),
        ("Día elegido", local_instant(2026, 3, 10, 12)),
        ("Día siguiente", local_instant(2026, 3, 11, 12)),
    ):
        add_test_movement(
            movements,
            product.id,
            base.id,
            MovementKind.LOOSE_ENTRY,
            reason,
            occurred_at,
        )
    app = app_for(make_assistant(connection))

    open_movements_section(app)
    choose_movement_product(app, product)

    without_period = rendered_text(app)

    assert not app.exception
    assert "Día anterior" in without_period
    assert "Día elegido" in without_period
    assert "Día siguiente" in without_period

    set_movement_period(
        app, date(2026, 3, 10), date(2026, 3, 10)
    )

    same_day = rendered_text(app)

    assert not app.exception
    assert "Día anterior" not in same_day
    assert "Día elegido" in same_day
    assert "Día siguiente" not in same_day


def test_movements_accepts_a_single_period_bound(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica que cada límite del período puede usarse por separado (T046).

    Solo «Desde» acota el inicio y deja el final abierto; solo «Hasta» acota el
    último día local y deja el inicio abierto.
    """
    product = zero_stock_product(
        product_service, sku="SKU-ONE-BOUND", name="Un límite"
    )
    base = presentations.list_by_product(product.id)[0]
    for reason, occurred_at in (
        ("Límite anterior", local_instant(2026, 3, 9, 12)),
        ("Límite elegido", local_instant(2026, 3, 10, 12)),
        ("Límite posterior", local_instant(2026, 3, 11, 12)),
    ):
        add_test_movement(
            movements,
            product.id,
            base.id,
            MovementKind.LOOSE_ENTRY,
            reason,
            occurred_at,
        )
    app = app_for(make_assistant(connection))

    open_movements_section(app)
    choose_movement_product(app, product)

    movement_date_input(app, START_DATE_LABEL).set_value(
        date(2026, 3, 10)
    )
    app.run()

    from_only = rendered_text(app)

    assert not app.exception
    assert "Límite anterior" not in from_only
    assert "Límite elegido" in from_only
    assert "Límite posterior" in from_only

    movement_date_input(app, START_DATE_LABEL).set_value(None)
    movement_date_input(app, END_DATE_LABEL).set_value(date(2026, 3, 10))
    app.run()

    until_only = rendered_text(app)

    assert not app.exception
    assert "Límite anterior" in until_only
    assert "Límite elegido" in until_only
    assert "Límite posterior" not in until_only


def test_an_inverted_period_shows_a_validation_without_reading(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica la validación previa y la ausencia de llamada (T046, D4).

    Si «Desde» es posterior a «Hasta (incluido)», la interfaz informa el
    problema, no lanza una excepción y no consulta el historial.
    """
    product = zero_stock_product(
        product_service, sku="SKU-INVERTED", name="Invertido"
    )
    assistant, counting_inventory = movements_assistant(connection)
    app = app_for(assistant)

    open_movements_section(app)
    choose_movement_product(app, product)
    counting_inventory.reset()
    set_movement_period(
        app, date(2026, 3, 11), date(2026, 3, 10)
    )

    assert not app.exception
    assert [warning.value for warning in app.warning] == [
        INVALID_PERIOD_MESSAGE
    ]
    assert counting_inventory.get_movement_history_calls == 0
    assert counting_inventory.get_product_presentations_calls == 0


@pytest.mark.parametrize(
    "error",
    (
        RecordNotFoundError("No existe el producto con id 99."),
        ValueError("El período indicado no es válido."),
    ),
)
def test_a_movement_read_error_is_not_presented_as_an_empty_history(
    connection: sqlite3.Connection,
    product_service: ProductService,
    error: Exception,
) -> None:
    """
    Verifica que los errores de lectura se muestran como errores (T046, C21).

    Un fallo de tipo `LookupError` o `ValueError` se informa con su motivo y no
    se confunde con un producto sin movimientos ni con filtros sin resultados.
    """
    product = zero_stock_product(
        product_service, sku="SKU-ERROR", name="Error"
    )
    assistant = make_assistant(connection)
    assistant.session.inventory_service = FailingMovementInventoryService(
        assistant.session.inventory_service, error
    )
    app = app_for(assistant)

    open_movements_section(app)
    choose_movement_product(app, product)

    messages = [info.value for info in app.info]

    assert not app.exception
    assert [message.value for message in app.error] == [
        MOVEMENTS_ERROR_TEMPLATE.format(reason=str(error))
    ]
    assert EMPTY_MOVEMENTS_HISTORY_MESSAGE not in messages
    assert EMPTY_FILTERED_MOVEMENTS_MESSAGE not in messages


def test_an_empty_catalog_shows_no_movement_controls(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica el catálogo vacío sin controles ni consultas (T046, C4).

    Sin productos no se dibuja el selector ni las fechas y se informa el vacío
    explícito, sin inventar un historial ni un valor cero.
    """
    assistant, counting_inventory = movements_assistant(connection)
    app = app_for(assistant)

    open_movements_section(app)

    assert not app.exception
    assert [header.value for header in app.header] == [
        MOVEMENTS_SECTION_HEADER
    ]
    assert [info.value for info in app.info] == [
        EMPTY_MOVEMENTS_CATALOG_MESSAGE
    ]
    assert len(app.selectbox) == 0
    assert len(app.date_input) == 0
    assert rendered_text(app) == ""
    assert counting_inventory.get_movement_history_calls == 0
    assert counting_inventory.get_product_presentations_calls == 0


def test_movements_does_not_read_without_a_product(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que sin producto no se consulta el historial (T046, D1).

    El selector conserva la opción centinela, se informa el estado y no se
    ejecuta ninguna lectura de movimientos ni de presentaciones.
    """
    assistant, counting_inventory = movements_assistant(connection)
    app = app_for(assistant)

    open_movements_section(app)
    counting_inventory.reset()
    app.run()

    assert not app.exception
    assert movement_selectbox(
        app, PRODUCT_FILTER_LABEL
    ).value == NO_SELECTION_LABEL
    assert [info.value for info in app.info] == [
        NO_MOVEMENT_SELECTED_MESSAGE
    ]
    assert counting_inventory.get_movement_history_calls == 0
    assert counting_inventory.get_product_presentations_calls == 0


def test_movements_distinguishes_empty_product_from_empty_filters(
    connection: sqlite3.Connection,
    product_service: ProductService,
    catalog: Catalog,
) -> None:
    """
    Verifica los dos estados vacíos de la sección (T046, C4, D6).

    Un producto sin movimientos y sin filtros informa su propio vacío; una
    consulta con filtros que no coincide informa un mensaje distinto.
    """
    empty_product = zero_stock_product(
        product_service, sku="SKU-EMPTY", name="Sin movimientos"
    )
    app = app_for(make_assistant(connection))

    open_movements_section(app)
    choose_movement_product(app, empty_product)

    empty_history = [info.value for info in app.info]

    assert not app.exception
    assert EMPTY_MOVEMENTS_HISTORY_MESSAGE in empty_history
    assert EMPTY_FILTERED_MOVEMENTS_MESSAGE not in empty_history

    choose_movement_product(app, catalog.product)
    movement_selectbox(app, KIND_FILTER_LABEL).set_value(
        "Compensación"
    ).run()

    filtered_history = [info.value for info in app.info]

    assert not app.exception
    assert EMPTY_MOVEMENTS_HISTORY_MESSAGE not in filtered_history
    assert EMPTY_FILTERED_MOVEMENTS_MESSAGE in filtered_history


def test_movements_reports_the_real_count_without_truncating(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica el aviso C8 con N real y el último registro visible (T046, D6).

    Se registran doce movimientos y se muestran los doce, con el aviso del
    número real y sin truncar el último.
    """
    product = zero_stock_product(
        product_service, sku="SKU-COUNT", name="Conteo"
    )
    base = presentations.list_by_product(product.id)[0]
    reasons = [f"Movimiento {index:02d}" for index in range(1, 13)]
    for index, reason in enumerate(reasons):
        add_test_movement(
            movements,
            product.id,
            base.id,
            MovementKind.LOOSE_ENTRY,
            reason,
            local_instant(2026, 3, 10, 8, index),
        )
    app = app_for(make_assistant(connection))

    open_movements_section(app)
    choose_movement_product(app, product)

    rendered = rendered_text(app)

    assert not app.exception
    assert MOVEMENTS_NOTICE_TEMPLATE.format(count=12) in [
        info.value for info in app.info
    ]
    for reason in reasons:
        assert f"{REASON_LABEL}: {reason}" in rendered
    assert rendered.index("Movimiento 01") < rendered.index("Movimiento 12")


def test_an_inactive_product_is_queryable_and_marked(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    products: SqliteProductRepository,
) -> None:
    """
    Verifica la consulta de un producto inactivo y su marca (T046, D7).

    El producto inactivo sigue en el selector con la marca de inactivo y su
    historial es consultable por lectura.
    """
    stored = zero_stock_product(
        product_service, sku="SKU-OFF-MOV", name="Inactivo"
    )
    base = presentations.list_by_product(stored.id)[0]
    add_test_movement(
        movements,
        stored.id,
        base.id,
        MovementKind.LOOSE_ENTRY,
        "Movimiento histórico inactivo",
        local_instant(2026, 3, 10, 12),
    )
    product_service.deactivate(stored.id)
    inactive = products.get(stored.id)
    label = product_option_label(inactive)
    app = app_for(make_assistant(connection))

    open_movements_section(app)
    choose_movement_product(app, inactive)

    rendered = rendered_text(app)

    assert not app.exception
    assert label == "SKU-OFF-MOV — Inactivo (inactivo)"
    assert label in movement_selectbox(
        app, PRODUCT_FILTER_LABEL
    ).options
    assert "Movimiento histórico inactivo" in rendered


def test_movements_shows_a_historical_compensation_without_actions(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica la compensación histórica sin acciones de escritura (T046, D8).

    El movimiento compensatorio se consulta como cualquier otro y la sección
    no ofrece botones ni la creación de una compensación nueva.
    """
    product = zero_stock_product(
        product_service, sku="SKU-COMP", name="Compensación"
    )
    base = presentations.list_by_product(product.id)[0]
    original = add_test_movement(
        movements,
        product.id,
        base.id,
        MovementKind.LOOSE_ENTRY,
        "Entrada original",
        local_instant(2026, 3, 10, 10),
        quantity=2,
    )
    add_test_movement(
        movements,
        product.id,
        base.id,
        MovementKind.COMPENSATION,
        "Compensación histórica",
        local_instant(2026, 3, 10, 11),
        original_movement_id=original.id,
    )
    app = app_for(make_assistant(connection))

    open_movements_section(app)
    choose_movement_product(app, product)

    rendered = rendered_text(app)

    assert not app.exception
    assert "Entrada original" in rendered
    assert "Compensación histórica" in rendered
    assert "Compensación" in rendered
    assert len(app.button) == 0


def test_movements_does_not_write(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que consultar y filtrar no modifica la base (T046).

    La instantánea de las tablas y el contador de cambios de SQLite son
    idénticos antes y después de abrir la sección, elegir el producto y aplicar
    un tipo y un período.
    """
    app = app_for(make_assistant(connection))
    before = snapshot(connection)
    changes_before = connection.total_changes

    open_movements_section(app)
    choose_movement_product(app, catalog.product)
    movement_selectbox(app, KIND_FILTER_LABEL).set_value(
        "Entrada suelta"
    )
    set_movement_period(
        app, date(2026, 1, 1), date(2026, 12, 31)
    )

    assert not app.exception
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_movements_works_without_the_model_with_real_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Verifica la sección sin modelo configurado y con datos reales (T046, C2).

    La lectura de la sesión se compone con la base configurada y la sección
    muestra el historial real aunque el asistente no esté disponible.
    """
    database_path = configured_database_path(monkeypatch, tmp_path)
    seed_connection = connect(database_path)
    try:
        create_schema(seed_connection)
        seed_products = ProductService(
            products=SqliteProductRepository(seed_connection),
            presentations=SqlitePresentationRepository(seed_connection),
            movements=SqliteMovementRepository(seed_connection),
            transaction=partial(transaction, seed_connection),
        )
        stored = seed_products.register(
            ProductRegistration(
                sku="SKU-NO-MODEL",
                name="Sin modelo",
                base_unit="unidad",
                base_sale_price=Decimal("10"),
                initial_loose_units=3,
            )
        )
        seed_presentations = SqlitePresentationRepository(seed_connection)
        base = seed_presentations.list_by_product(stored.id)[0]
        add_test_movement(
            SqliteMovementRepository(seed_connection),
            stored.id,
            base.id,
            MovementKind.LOOSE_ENTRY,
            "Movimiento real sin modelo",
            local_instant(2026, 3, 10, 12),
        )
    finally:
        seed_connection.close()
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)

    app.run()

    session = app.session_state.get(SESSION_KEY)

    try:
        assert not app.exception
        assert len(app.error) == 1
        assert MISSING_KEY_MESSAGE in app.error[0].value

        open_movements_section(app)
        choose_movement_product(app, stored)

        rendered = rendered_text(app)

        assert not app.exception
        assert len(app.error) == 0
        assert "Movimiento real sin modelo" in rendered
        assert product_option_label(stored) in movement_selectbox(
            app, PRODUCT_FILTER_LABEL
        ).options
    finally:
        if session is not None:
            session.connection.close()


def test_movements_formats_local_dates_and_omits_absent_optional_fields(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica formatos locales, importes y campos opcionales (T046, C17).

    La fecha se muestra con el desplazamiento `-03:00`, los importes con dos
    decimales y el cero real como `0.00`; el costo y el precio aplicado se
    imprimen solo cuando existen, sin mostrar valores ausentes.
    """
    product = zero_stock_product(
        product_service, sku="SKU-FORMAT", name="Formatos"
    )
    base = presentations.list_by_product(product.id)[0]
    add_test_movement(
        movements,
        product.id,
        base.id,
        MovementKind.LOOSE_ENTRY,
        "Con costo",
        local_instant(2026, 3, 10, 10, 15),
        cost=Decimal("12.5"),
    )
    add_test_movement(
        movements,
        product.id,
        base.id,
        MovementKind.LOOSE_EXIT,
        "Con precio aplicado",
        local_instant(2026, 3, 10, 11, 15),
        applied_price=Decimal("99.9"),
    )
    add_test_movement(
        movements,
        product.id,
        base.id,
        MovementKind.LOOSE_ENTRY,
        "Con costo cero",
        local_instant(2026, 3, 10, 12, 15),
        cost=Decimal("0"),
    )
    add_test_movement(
        movements,
        product.id,
        base.id,
        MovementKind.LOOSE_ENTRY,
        "Sin opcionales",
        local_instant(2026, 3, 10, 13, 15),
    )
    app = app_for(make_assistant(connection))

    open_movements_section(app)
    choose_movement_product(app, product)

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{DATE_LABEL}: 2026-03-10 10:15:00 -03:00" in rendered
    assert f"{COST_LABEL}: 12.50" in rendered
    assert f"{APPLIED_PRICE_LABEL}: 99.90" in rendered
    assert f"{COST_LABEL}: 0.00" in rendered
    assert rendered.count(f"{COST_LABEL}:") == 2
    assert rendered.count(f"{APPLIED_PRICE_LABEL}:") == 1
    assert "None" not in rendered
    assert ABSENT_MARKER not in rendered


def test_movements_falls_back_to_the_presentation_id(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica que una presentación no resuelta informa su id (T046).

    El movimiento referencia una presentación que no pertenece al producto:
    la vista no inventa un nombre y usa la referencia explícita con el id.
    """
    product = zero_stock_product(
        product_service, sku="SKU-FALLBACK", name="Fallback"
    )
    foreign = MovementRecord(
        id=1,
        product_id=product.id,
        presentation_id=9999,
        kind=MovementKind.LOOSE_ENTRY,
        quantity=1,
        reason="Presentación ajena",
        occurred_at=local_instant(2026, 3, 10, 12),
    )
    assistant = make_assistant(connection)
    assistant.session.inventory_service = FixedMovementInventoryService(
        assistant.session.inventory_service, foreign
    )
    app = app_for(assistant)

    open_movements_section(app)
    choose_movement_product(app, product)

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{PRESENTATION_LABEL}: presentación #9999" in rendered
    assert "Presentación ajena" in rendered


def test_the_movements_view_only_presents_the_received_data() -> None:
    """
    Verifica que Movimientos no consulta, no escribe ni confirma (T046).

    La vista recibe los datos ya consultados desde `main.py`: no importa los
    servicios, el dominio ni la persistencia, no llama a sus lecturas, no usa
    `st.session_state`, no contiene el punto de confirmación y no ofrece
    compensaciones nuevas.
    """
    source = MOVEMENTS_SECTION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert MOVEMENTS_SECTION_PATH.exists()
    assert "st.session_state" not in source
    assert ".confirm(" not in source
    assert "register_compensation" not in source
    assert "proposal_service" not in source
    for module in imported_modules(tree):
        for prefix in FORBIDDEN_SECTION_IMPORTS:
            assert not (
                module == prefix or module.startswith(f"{prefix}.")
            ), f"movements.py importa {module}"
    assert not called_names(tree) & {
        "list_products",
        "get_movement_history",
        "get_product_presentations",
        "register_compensation",
        "build_session",
        "open_connection",
        "transaction",
        "get_llm",
        "build_workflow",
    }


# --- Sección Historial de precios (T047, RF-09) ---


class FailingPriceHistoryService:
    """Servicio que simula un error al leer el historial de precios (T047)."""

    def __init__(self, service: PriceHistoryService, error: Exception) -> None:
        """Envuelve el servicio real y conserva el error a inducir."""
        self._service = service
        self._error = error

    def get_price_history_by_product(self, product_id: int) -> Any:
        """Induce el error de lectura del historial del producto."""
        raise self._error

    def get_price_history_by_presentation(
        self, presentation_id: int
    ) -> Any:
        """Induce el error de lectura del historial de la presentación."""
        raise self._error

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra lectura en el servicio real."""
        return getattr(self._service, name)


class FixedPriceHistoryService:
    """Servicio que devuelve cambios de precio con una presentación ajena."""

    def __init__(
        self,
        service: PriceHistoryService,
        changes: Sequence[PriceChangeRecord],
    ) -> None:
        """Envuelve el servicio real y conserva los cambios a devolver."""
        self._service = service
        self._changes = changes

    def get_price_history_by_product(
        self, product_id: int
    ) -> list[PriceChangeRecord]:
        """Devuelve los cambios fijados, sin consultar el historial real."""
        return list(self._changes)

    def get_price_history_by_presentation(
        self, presentation_id: int
    ) -> list[PriceChangeRecord]:
        """Devuelve los cambios fijados, sin consultar el historial real."""
        return list(self._changes)

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra lectura en el servicio real."""
        return getattr(self._service, name)


def open_price_history_section(app: AppTest) -> None:
    """Ejecuta la aplicación y abre la sección Historial de precios (T047)."""
    app.run()
    app.radio[0].set_value(PRICE_HISTORY_SECTION_HEADER).run()


def price_history_selectbox(app: AppTest, label: str) -> Any:
    """Devuelve el selector de Historial de precios con la etiqueta dada."""
    return next(
        selectbox
        for selectbox in app.selectbox
        if selectbox.label == label
    )


def choose_price_history_product(
    app: AppTest,
    product: ProductRecord,
) -> None:
    """Elige el producto indicado en el selector del historial (T047, D1)."""
    price_history_selectbox(app, PRICE_HISTORY_PRODUCT_LABEL).set_value(
        product_option_label(product)
    ).run()


def choose_price_history_presentation(
    app: AppTest,
    presentation_name: str,
) -> None:
    """Elige la presentación indicada en el selector del historial (D2)."""
    price_history_selectbox(app, PRESENTATION_FILTER_LABEL).set_value(
        presentation_name
    ).run()


def base_presentation(
    presentations: SqlitePresentationRepository,
    product_id: int,
) -> PresentationRecord:
    """Devuelve la presentación base del producto (factor 1)."""
    return next(
        presentation
        for presentation in presentations.list_by_product(product_id)
        if presentation.factor == 1
    )


def add_test_price_change(
    connection: sqlite3.Connection,
    product: ProductRecord,
    presentation: PresentationRecord,
    *,
    previous_price: Decimal,
    new_price: Decimal,
    occurred_at: datetime,
) -> PriceChangeRecord:
    """Registra un cambio de precio real en la base de la prueba (T047)."""
    return SqlitePriceHistoryRepository(connection).add(
        product_id=product.id,
        presentation_id=presentation.id,
        previous_price=previous_price,
        new_price=new_price,
        occurred_at=occurred_at,
    )


def test_price_history_shows_names_prices_and_local_dates(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
    presentation_service: PresentationService,
) -> None:
    """
    Verifica nombres, importes, fecha local y orden cronológico (T047, C19).

    Se confirmaron dos cambios de precio por el servicio real, uno en cada
    presentación. Con «Todas las presentaciones» se muestran ambos en el orden
    del servicio, con dos decimales y la fecha local `-03:00`, sin ISO 8601 ni
    UTC.
    """
    base = base_presentation(presentations, catalog.product.id)
    presentation_service.change_sale_price(base.id, Decimal("110"))
    presentation_service.change_sale_price(catalog.box.id, Decimal("660"))
    app = app_for(make_assistant(connection))

    open_price_history_section(app)
    choose_price_history_product(app, catalog.product)

    rendered = rendered_text(app)
    infos = [info.value for info in app.info]
    dates = [
        line
        for line in rendered.splitlines()
        if line.startswith(f"{DATE_LABEL}: ")
    ]

    assert not app.exception
    assert PRICE_HISTORY_SECTION_NOTICE_TEMPLATE.format(count=2) in infos
    assert f"{PRESENTATION_LABEL}: botella" in rendered
    assert f"{PRESENTATION_LABEL}: Caja x6" in rendered
    assert f"{PRICE_PREVIOUS_LABEL}: 100.00" in rendered
    assert f"{PRICE_NEW_LABEL}: 110.00" in rendered
    assert f"{PRICE_PREVIOUS_LABEL}: 600.00" in rendered
    assert f"{PRICE_NEW_LABEL}: 660.00" in rendered
    assert f"{EVENT_LABEL}: {PRICE_CHANGE_EVENT_TYPE}" in rendered
    assert len(dates) == 2
    for line in dates:
        assert re.fullmatch(
            rf"{DATE_LABEL}: \d{{4}}-\d{{2}}-\d{{2}} "
            r"\d{2}:\d{2}:\d{2} -03:00",
            line,
        )
    assert not re.search(r"\d{4}-\d{2}-\d{2}T", rendered)
    assert "+00:00" not in rendered
    assert rendered.index(
        f"{PRICE_NEW_LABEL}: 110.00"
    ) < rendered.index(f"{PRICE_NEW_LABEL}: 660.00")


def test_price_history_filters_by_presentation(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
    presentation_service: PresentationService,
) -> None:
    """
    Verifica que una presentación concreta acota el historial (T047, D2).

    Con «Caja x6» elegida solo se muestra el cambio de esa presentación y el
    aviso C8 informa un único cambio.
    """
    base = base_presentation(presentations, catalog.product.id)
    presentation_service.change_sale_price(base.id, Decimal("110"))
    presentation_service.change_sale_price(catalog.box.id, Decimal("660"))
    app = app_for(make_assistant(connection))

    open_price_history_section(app)
    choose_price_history_product(app, catalog.product)
    assert price_history_selectbox(
        app, PRESENTATION_FILTER_LABEL
    ).value == ALL_PRESENTATIONS_LABEL

    choose_price_history_presentation(app, catalog.box.name)

    rendered = rendered_text(app)
    infos = [info.value for info in app.info]

    assert not app.exception
    assert PRICE_HISTORY_SECTION_NOTICE_TEMPLATE.format(count=1) in infos
    assert f"{PRESENTATION_LABEL}: Caja x6" in rendered
    assert f"{PRICE_NEW_LABEL}: 660.00" in rendered
    assert "110.00" not in rendered
    assert "botella" not in rendered


def test_price_history_falls_back_to_the_presentation_id(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica que una presentación no resuelta informa su id (T047).

    El cambio referencia una presentación que no pertenece al producto: la
    vista no inventa un nombre y usa la referencia explícita con el id.
    """
    product = zero_stock_product(
        product_service,
        sku="SKU-PRICE-FALLBACK",
        name="Fallback de precios",
    )
    change = PriceChangeRecord(
        id=1,
        product_id=product.id,
        presentation_id=9999,
        previous_price=Decimal("10"),
        new_price=Decimal("12"),
        occurred_at=local_instant(2026, 3, 10, 12),
        event_type=PRICE_CHANGE_EVENT_TYPE,
    )
    assistant = make_assistant(connection)
    assistant.session.price_history_service = FixedPriceHistoryService(
        assistant.session.price_history_service,
        [change],
    )
    app = app_for(assistant)

    open_price_history_section(app)
    choose_price_history_product(app, product)

    rendered = rendered_text(app)

    assert not app.exception
    assert f"{PRESENTATION_LABEL}: presentación #9999" in rendered
    assert f"{PRICE_NEW_LABEL}: 12.00" in rendered


def test_a_product_without_price_changes_informs_the_empty_state(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica el vacío explícito de un producto sin cambios (T047, C4).

    No se muestran registros, no se inventa un cero y no aparece el aviso de
    no paginación con un conteo falso.
    """
    product = zero_stock_product(
        product_service,
        sku="SKU-PRICE-EMPTY",
        name="Sin cambios",
    )
    app = app_for(make_assistant(connection))

    open_price_history_section(app)
    choose_price_history_product(app, product)

    infos = [info.value for info in app.info]

    assert not app.exception
    assert infos == [EMPTY_PRODUCT_HISTORY_MESSAGE]
    assert PRICE_HISTORY_SECTION_NOTICE_TEMPLATE.format(count=0) not in infos
    assert rendered_text(app) == ""


def test_a_presentation_without_price_changes_has_its_own_empty_state(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
    presentation_service: PresentationService,
) -> None:
    """
    Verifica el vacío propio de una presentación sin cambios (T047, D2, C4).

    El producto tiene un cambio en su presentación base, pero «Caja x6» no
    tiene ninguno: se informa el vacío de la presentación y no el del producto.
    """
    base = base_presentation(presentations, catalog.product.id)
    presentation_service.change_sale_price(base.id, Decimal("110"))
    app = app_for(make_assistant(connection))

    open_price_history_section(app)
    choose_price_history_product(app, catalog.product)
    choose_price_history_presentation(app, catalog.box.name)

    infos = [info.value for info in app.info]

    assert not app.exception
    assert EMPTY_PRESENTATION_HISTORY_MESSAGE in infos
    assert EMPTY_PRODUCT_HISTORY_MESSAGE not in infos
    assert PRICE_HISTORY_SECTION_NOTICE_TEMPLATE.format(count=0) not in infos
    assert rendered_text(app) == ""


def test_price_history_does_not_read_without_a_product(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que sin producto no se consulta ningún historial (T047, D1).

    El selector conserva la opción centinela, se informa el estado y no se
    ejecuta ninguna lectura de presentaciones ni del historial de precios.
    """
    assistant, counting_inventory = counted_assistant(connection)
    counting_history = CountingPriceHistoryService(
        assistant.session.price_history_service
    )
    assistant.session.price_history_service = counting_history
    app = app_for(assistant)

    open_price_history_section(app)

    assert not app.exception
    assert price_history_selectbox(
        app, PRICE_HISTORY_PRODUCT_LABEL
    ).value == NO_SELECTION_LABEL
    assert [info.value for info in app.info] == [
        NO_PRICE_HISTORY_PRODUCT_MESSAGE
    ]
    assert [selectbox.label for selectbox in app.selectbox] == [
        PRICE_HISTORY_PRODUCT_LABEL
    ]
    assert counting_inventory.get_product_presentations_calls == 0
    assert counting_history.get_price_history_by_product_calls == 0
    assert counting_history.get_price_history_by_presentation_calls == 0


def test_an_empty_catalog_shows_no_price_history_controls(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica el catálogo vacío sin controles ni lecturas (T047, C4).

    Sin productos se muestra el encabezado y el vacío explícito, sin selectores
    ni datos inventados.
    """
    app = app_for(make_assistant(connection))

    open_price_history_section(app)

    assert not app.exception
    assert [header.value for header in app.header] == [
        PRICE_HISTORY_SECTION_HEADER
    ]
    assert [info.value for info in app.info] == [
        EMPTY_PRICE_HISTORY_CATALOG_MESSAGE
    ]
    assert len(app.selectbox) == 0
    assert rendered_text(app) == ""


def test_an_inactive_product_is_queryable_in_price_history(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    presentation_service: PresentationService,
    products: SqliteProductRepository,
) -> None:
    """
    Verifica la consulta de un producto inactivo y su marca (T047, D3).

    El producto inactivo sigue en el selector con la marca de inactivo y su
    historial de precios es visible por lectura.
    """
    stored = zero_stock_product(
        product_service,
        sku="SKU-PRICE-OFF",
        name="Inactivo con historial",
    )
    base = base_presentation(presentations, stored.id)
    presentation_service.change_sale_price(base.id, Decimal("17"))
    product_service.deactivate(stored.id)
    inactive = products.get(stored.id)
    label = product_option_label(inactive)
    app = app_for(make_assistant(connection))

    open_price_history_section(app)
    choose_price_history_product(app, inactive)

    rendered = rendered_text(app)

    assert not app.exception
    assert label == "SKU-PRICE-OFF — Inactivo con historial (inactivo)"
    assert label in price_history_selectbox(
        app, PRICE_HISTORY_PRODUCT_LABEL
    ).options
    assert f"{PRICE_NEW_LABEL}: 17.00" in rendered


def test_price_history_reports_the_real_count_without_truncating(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """
    Verifica el aviso C8 con N real y el último cambio visible (T047, D6).

    Se registran doce cambios y se muestran los doce, con el aviso del número
    real y sin truncar el último.
    """
    product = zero_stock_product(
        product_service,
        sku="SKU-PRICE-COUNT",
        name="Conteo de precios",
    )
    base = base_presentation(presentations, product.id)
    for index in range(1, 13):
        add_test_price_change(
            connection,
            product,
            base,
            previous_price=Decimal(100 + index),
            new_price=Decimal(101 + index),
            occurred_at=local_instant(2026, 3, 10, 8, index),
        )
    app = app_for(make_assistant(connection))

    open_price_history_section(app)
    choose_price_history_product(app, product)

    rendered = rendered_text(app)
    infos = [info.value for info in app.info]

    assert not app.exception
    assert PRICE_HISTORY_SECTION_NOTICE_TEMPLATE.format(count=12) in infos
    for index in range(1, 13):
        assert f"{PRICE_NEW_LABEL}: {101 + index}.00" in rendered
    assert rendered.count(f"{DATE_LABEL}:") == 12
    assert rendered.index(
        f"{PRICE_NEW_LABEL}: 102.00"
    ) < rendered.index(f"{PRICE_NEW_LABEL}: 113.00")


def test_a_price_history_read_error_is_not_an_empty_history(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica que un error de lectura se distingue del vacío (T047, C21).

    `RecordNotFoundError` se informa con su motivo como error y no como un
    producto o una presentación sin cambios.
    """
    product = zero_stock_product(
        product_service,
        sku="SKU-PRICE-ERROR",
        name="Error de precios",
    )
    error = RecordNotFoundError("No existe el producto con id 999.")
    assistant = make_assistant(connection)
    assistant.session.price_history_service = FailingPriceHistoryService(
        assistant.session.price_history_service,
        error,
    )
    app = app_for(assistant)

    open_price_history_section(app)
    choose_price_history_product(app, product)

    infos = [info.value for info in app.info]

    assert not app.exception
    assert [element.value for element in app.error] == [
        PRICE_HISTORY_ERROR_TEMPLATE.format(reason=str(error))
    ]
    assert EMPTY_PRODUCT_HISTORY_MESSAGE not in infos
    assert EMPTY_PRESENTATION_HISTORY_MESSAGE not in infos
    assert PRICE_HISTORY_SECTION_NOTICE_TEMPLATE.format(count=0) not in infos


def test_price_history_does_not_write(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
    presentation_service: PresentationService,
) -> None:
    """
    Verifica que consultar y filtrar no modifica la base (T047).

    La instantánea de las tablas y el contador de cambios de SQLite son
    idénticos antes y después de abrir la sección, elegir el producto y cambiar
    la presentación en ambos sentidos.
    """
    base = base_presentation(presentations, catalog.product.id)
    presentation_service.change_sale_price(base.id, Decimal("110"))
    app = app_for(make_assistant(connection))
    before = snapshot(connection)
    changes_before = connection.total_changes

    open_price_history_section(app)
    choose_price_history_product(app, catalog.product)
    choose_price_history_presentation(app, catalog.box.name)
    choose_price_history_presentation(app, ALL_PRESENTATIONS_LABEL)

    assert not app.exception
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_price_history_works_without_the_model_with_real_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Verifica la sección sin modelo configurado y con datos reales (T047, C2).

    La lectura de la sesión se compone con la base configurada y el historial
    confirmado se muestra aunque el asistente no esté disponible.
    """
    database_path = configured_database_path(monkeypatch, tmp_path)
    seed_connection = connect(database_path)
    try:
        create_schema(seed_connection)
        seed_products = ProductService(
            products=SqliteProductRepository(seed_connection),
            presentations=SqlitePresentationRepository(seed_connection),
            movements=SqliteMovementRepository(seed_connection),
            transaction=partial(transaction, seed_connection),
        )
        stored = seed_products.register(
            ProductRegistration(
                sku="SKU-PRICE-NO-MODEL",
                name="Sin modelo con precios",
                base_unit="unidad",
                base_sale_price=Decimal("10"),
            )
        )
        seed_presentations = SqlitePresentationRepository(seed_connection)
        base = seed_presentations.list_by_product(stored.id)[0]
        PresentationService(
            products=SqliteProductRepository(seed_connection),
            presentations=seed_presentations,
            movements=SqliteMovementRepository(seed_connection),
            price_history=SqlitePriceHistoryRepository(seed_connection),
            transaction=partial(transaction, seed_connection),
        ).change_sale_price(base.id, Decimal("15"))
    finally:
        seed_connection.close()
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)

    app.run()

    session = app.session_state.get(SESSION_KEY)

    try:
        assert not app.exception
        assert len(app.error) == 1
        assert MISSING_KEY_MESSAGE in app.error[0].value

        open_price_history_section(app)
        choose_price_history_product(app, stored)

        rendered = rendered_text(app)

        assert not app.exception
        assert len(app.error) == 0
        assert f"{PRICE_NEW_LABEL}: 15.00" in rendered
        assert product_option_label(stored) in price_history_selectbox(
            app, PRICE_HISTORY_PRODUCT_LABEL
        ).options
    finally:
        if session is not None:
            session.connection.close()


def test_the_price_history_view_only_presents_the_received_data() -> None:
    """
    Verifica que el historial no consulta, no escribe ni confirma (T047).

    La vista recibe los datos ya consultados desde `main.py`: no importa los
    servicios, el dominio ni la persistencia, no llama a sus lecturas, no usa
    `st.session_state` y no contiene el punto de confirmación.
    """
    source = PRICE_HISTORY_SECTION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert PRICE_HISTORY_SECTION_PATH.exists()
    assert "st.session_state" not in source
    assert ".confirm(" not in source
    for module in imported_modules(tree):
        for prefix in FORBIDDEN_SECTION_IMPORTS:
            assert not (
                module == prefix or module.startswith(f"{prefix}.")
            ), f"price_history.py importa {module}"
    assert not called_names(tree) & {
        "list_products",
        "get_product_presentations",
        "get_price_history_by_product",
        "get_price_history_by_presentation",
        "change_sale_price",
        "build_session",
        "open_connection",
        "transaction",
        "get_llm",
        "build_workflow",
    }


def test_the_price_history_view_uses_the_shared_formatting() -> None:
    """
    Verifica que la vista delega el formato y no usa los helpers de main
    (T047).

    Los importes y las fechas se formatean con `describe_price_change` y
    `app.ui.formatting`; la sección no usa `_format_price` ni `isoformat()`.
    """
    source = PRICE_HISTORY_SECTION_PATH.read_text(encoding="utf-8")

    assert "describe_price_change" in source
    assert "_format_price" not in source
    assert "isoformat(" not in source
