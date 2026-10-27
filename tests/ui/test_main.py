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
factor, precio de venta y stock cerrado, las unidades sueltas y el total
equivalente derivado, los movimientos con fecha local y campos opcionales, el
historial de precios en orden cronológico, la consulta de un producto inactivo,
los vacíos diferenciados, los avisos C8 sin truncamiento, una lectura de cada
servicio sin escrituras, el funcionamiento sin modelo y el error de lectura que
no se presenta como historial vacío.

Cubren la sección de operaciones de T049 sobre RF-09: los formularios de las
diez acciones admitidas —sin desactivación de productos ni cambio de factor de
una presentación existente—, la creación de propuestas pendientes sin escribir,
la colección ordenada de la sesión sin deduplicar, la validación explícita y la
precisión monetaria con `Decimal`, el funcionamiento sin modelo y los límites
arquitectónicos de la vista.

Cubren la confirmación centralizada de T053 sobre RF-09: la confirmación de
propuestas manuales y del asistente desde «Propuestas pendientes» a través de
un único punto de `main.py`, la ausencia del control una vez confirmada, el
segundo intento sin ejecución, la protección de reenvío mientras hay una
confirmación en curso y la comprobación estructural de una sola aparición de
`.confirm(` en `app/ui/**`.

Cubren los estados transversales de T055 sobre RF-09: la carga explícita de las
secciones que leen servicios, la validación local distinta del rechazo del
servicio y de la confirmación, el rechazo con su motivo real y la presentación
consistente de las fechas locales sobre el almacenamiento en UTC.

Queda fuera de este alcance y corresponde a T025: la validación end-to-end de
RF-01 a RF-07.
"""

import ast
import re
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import Field
from streamlit.testing.v1 import AppTest

from app import config
from app.domain.movements import MovementKind
from app.graph.state import create_initial_state
from app.infrastructure.sqlite.connection import connect, transaction
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqlitePriceHistoryRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.schemas.proposals import (
    AperturePayload,
    ClosedEntryPayload,
    ClosedExitPayload,
    CompensationPayload,
    ConfirmationResult,
    InitialPresentationPayload,
    LooseEntryPayload,
    LooseExitPayload,
    PresentationRegistrationPayload,
    PriceUpdatePayload,
    ProductRegistrationPayload,
    ProductUpdatePayload,
    Proposal,
    ProposalAction,
    ProposalStatus,
)
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
from app.ui import main as main_module
from app.ui import session as session_module
from app.ui.formatting import ABSENT_MARKER, format_money
from app.ui.main import (
    ALREADY_CONFIRMED_MESSAGE,
    ASSISTANT_KEY,
    CONFIRMATION_ERROR_PREFIX,
    CONFIRMATION_IN_PROGRESS_MESSAGE,
    CONFIRMATION_NOTICE_KEY,
    CONFIRMATION_NOT_AVAILABLE_MESSAGE,
    CONFIRM_LABEL,
    DISCARDED_PROPOSAL_MESSAGE,
    LOADING_MESSAGE,
    DISCARDED_PROPOSALS_KEY,
    MANUAL_PROPOSALS_KEY,
    MAX_CONVERSATION_MESSAGES,
    NO_PENDING_PROPOSAL_MESSAGE,
    NO_RESPONSE_MESSAGE,
    PENDING_PROPOSAL_NOTE,
    PROCESSING_KEY,
    PROPOSAL_NOT_AVAILABLE_MESSAGE,
    SECTION_LABELS,
    SECTION_PENDING_MESSAGE,
    Assistant,
    Turn,
    answer,
    build_assistant,
    confirm_pending,
    confirm_proposal,
    describe_price_history,
    describe_price_update,
    describe_proposal,
    describe_result,
    edit_proposal,
    locate_proposal,
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
from app.ui.sections.proposals import (
    DISCARD_LABEL,
    EDIT_SUBMIT_LABEL,
    EMPTY_PROPOSALS_MESSAGE,
    INITIAL_LOOSE_COST_FIELD_LABEL,
    INITIAL_PRESENTATION_COST_FIELD_LABEL,
    NO_INITIAL_PRESENTATIONS_MESSAGE,
    UNIT_COST_REFERENCE_FIELD_LABEL,
    PresentationPriceView,
    ProposalReview,
    describe_price_update_fields,
    describe_proposal_review,
)
from app.ui.sections.product_detail import (
    APPLIED_PRICE_LABEL,
    BASE_COLUMN_LABEL,
    COST_LABEL,
    DATE_LABEL,
    EMPTY_MOVEMENTS_MESSAGE,
    EMPTY_PRICE_HISTORY_MESSAGE,
    EMPTY_PRESENTATIONS_MESSAGE,
    EVENT_LABEL,
    FACTOR_HELP_CAPTION,
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
    SALE_PRICE_LABEL,
    TABLE_YES_TEXT,
    TOTAL_EQUIVALENT_LABEL,
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
from app.ui.sections.operations import (
    ACTION_SELECTOR_KEY,
    ACTION_SELECTOR_LABEL,
    APERTURE_QUANTITY_LABEL,
    BASE_PRICE_FIELD_LABEL,
    BASE_UNIT_FIELD_LABEL,
    CLOSED_ENTRY_PRESENTATION_LABEL,
    CLOSED_PRESENTATION_NO_SELECTION_LABEL,
    CLOSED_QUANTITY_LABEL,
    COMPENSABLE_QUANTITY_TEMPLATE,
    COMPENSATION_QUANTITY_LABEL,
    COMPENSATION_NOTE,
    COMPENSATION_UNAVAILABLE_MESSAGE,
    COST_FIELD_LABEL,
    EMPTY_CATALOG_TEMPLATE as EMPTY_OPERATIONS_CATALOG_TEMPLATE,
    EMPTY_MOVEMENTS_MESSAGE as EMPTY_OPERATIONS_MOVEMENTS_MESSAGE,
    FACTOR_FIELD_LABEL,
    INITIAL_CLOSED_STOCK_LABEL,
    INITIAL_LOOSE_COST_LABEL,
    INITIAL_LOOSE_UNITS_LABEL,
    INITIAL_PRESENTATION_COST_LABEL,
    INITIAL_PRESENTATION_NAME_LABEL,
    LOOSE_QUANTITY_LABEL,
    MOVEMENT_SELECTOR_KEY_TEMPLATE,
    MOVEMENT_SELECTOR_LABEL,
    NAME_FIELD_LABEL,
    NEW_MINIMUM_STOCK_LABEL,
    NEW_NAME_LABEL,
    NEW_PRICE_LABEL,
    NO_CLOSED_EXIT_PRESENTATIONS_MESSAGE,
    NO_CLOSED_PRESENTATIONS_MESSAGE,
    PRESENTATION_NAME_FIELD_LABEL,
    PRESENTATION_PRICE_LABEL,
    PRESENTATION_SELECTOR_KEY_TEMPLATE,
    PRODUCT_SELECTOR_KEY,
    REASON_FIELD_LABEL,
    SKU_FIELD_LABEL,
    SUBMIT_LABEL,
    UPDATE_WITHOUT_CHANGE_MESSAGE,
    OperationValidationError,
    parse_money,
    parse_optional_money,
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

THEME_CONFIG_PATH: Path = (
    Path(__file__).resolve().parents[2] / ".streamlit" / "config.toml"
)
"""Ruta del tema nativo de Streamlit configurado para la aplicación (T057)."""

SESSION_MODULE_PATH: Path = (
    Path(__file__).resolve().parents[2] / "app" / "ui" / "session.py"
)
"""Ruta absoluta del módulo que compone la lectura de la sesión (T039)."""

SECTIONS_PATH: Path = APP_PATH.parent / "sections"
"""Directorio de las secciones de la navegación lateral (T041)."""

PROPOSALS_SECTION_PATH: Path = SECTIONS_PATH / "proposals.py"
"""Módulo de la vista de la sección «Propuestas pendientes» (T041)."""

ASSISTANT_SECTION_PATH: Path = SECTIONS_PATH / "assistant.py"
"""Módulo de la presentación interactiva de la sección «Asistente» (T048)."""

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

OPERATIONS_SECTION_PATH: Path = SECTIONS_PATH / "operations.py"
"""Módulo de la vista de la sección «Operaciones» (T049)."""

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

PENDING_SECTIONS: tuple[str, ...] = ()
"""Secciones de RF-09 todavía pendientes en la Fase 7 (T041, T049).

«Operaciones» dejó de estar pendiente al implementarse T049: las siete
secciones de la navegación presentan sus datos o su estado explícito.
"""

OPERATION_ACTION_LABELS: tuple[tuple[str, str], ...] = (
    ("alta_producto", "Alta de producto"),
    ("modificacion_producto", "Modificación de producto"),
    ("alta_presentacion", "Alta de presentación"),
    ("entrada_suelta", "Entrada de unidades sueltas"),
    ("entrada_cerrada", "Entrada de presentaciones cerradas"),
    ("salida_suelta", "Salida de unidades sueltas"),
    ("salida_cerrada", "Salida de presentaciones cerradas"),
    ("apertura", "Apertura de presentaciones"),
    ("compensacion", "Compensación de un movimiento"),
    ("cambio_precio", "Cambio de precio"),
)
"""Las diez acciones admitidas por las propuestas, con su nombre en la vista.

Se declaran como tupla literal —no desde las constantes de la aplicación— para
que la prueba verifique el alcance de RF-09 y no una comprobación tautológica
(C10, T049).
"""

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


def metric_values(app: AppTest) -> dict[str, str]:
    """Devuelve el valor mostrado de cada métrica, por etiqueta (T057)."""
    return {metric.label: metric.value for metric in app.metric}


def product_records(app: AppTest) -> list[dict[str, Any]]:
    """Devuelve las filas del listado de Productos como registros (T057)."""
    assert len(app.dataframe) == 1

    return app.dataframe[0].value.to_dict("records")


DETAIL_PRESENTATION_COLUMNS: tuple[str, ...] = (
    PRESENTATION_LABEL,
    FACTOR_LABEL,
    SALE_PRICE_LABEL,
    CLOSED_STOCK_LABEL,
    BASE_COLUMN_LABEL,
)
"""Columnas de la tabla de presentaciones del detalle (T057)."""

DETAIL_MOVEMENT_COLUMNS: tuple[str, ...] = (
    MOVEMENT_TYPE_LABEL,
    QUANTITY_LABEL,
    REASON_LABEL,
    DATE_LABEL,
    PRESENTATION_LABEL,
    COST_LABEL,
    APPLIED_PRICE_LABEL,
    INITIAL_STOCK_LABEL,
)
"""Columnas de la tabla de movimientos del detalle (T057)."""

DETAIL_PRICE_HISTORY_COLUMNS: tuple[str, ...] = (
    PRESENTATION_LABEL,
    PRICE_PREVIOUS_LABEL,
    PRICE_NEW_LABEL,
    DATE_LABEL,
    EVENT_LABEL,
)
"""Columnas de la tabla del historial de precios del detalle (T057)."""


def detail_records(
    app: AppTest,
    columns: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Devuelve la tabla del detalle que tiene exactamente esas columnas."""
    for dataframe in app.dataframe:
        records = dataframe.value.to_dict("records")
        if records and tuple(records[0]) == columns:
            return records

    raise AssertionError(f"No se encontró la tabla con columnas {columns}.")


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


# --- Contexto conversacional (T063) ---


def conversation_turn(user_message: str, response: str | None) -> Turn:
    """Construye un turno previo de la conversación (T063)."""
    state = create_initial_state(user_message)
    state["response"] = response

    return Turn(user_message=user_message, state=state)


def conversation_context(model: RelayChatModel) -> list[str]:
    """Devuelve los mensajes de conversación que recibió el modelo (T063)."""
    return [
        message.content
        for message in model.received[-1]
        if isinstance(message, (HumanMessage, AIMessage))
    ]


def test_answer_preserves_recent_conversation_context(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica que «sí, es ese» llega al modelo junto con la aclaración previa.

    El historial de la sesión aporta la consulta original y la aclaración del
    asistente; el mensaje actual se envía después de ellas y una sola vez, sin
    formar parte del historial previo (T063).
    """
    model = RelayChatModel()
    assistant = build_assistant(model=model, connection=connection)
    assistant.history.append(
        conversation_turn("Busca Coca", "¿Te refieres a coca-cola-3L?")
    )

    turn = answer(assistant, "sí, es ese")

    assert conversation_context(model) == [
        "Busca Coca",
        "¿Te refieres a coca-cola-3L?",
        "sí, es ese",
    ]
    assert conversation_context(model).count("sí, es ese") == 1
    assert turn.state["conversation"] == (
        ("human", "Busca Coca"),
        ("ai", "¿Te refieres a coca-cola-3L?"),
    )
    assert len(assistant.history) == 1


def test_answer_limits_the_recent_conversation_context(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica la ventana de 12 mensajes en la frontera 12/13 (T063).

    Con doce mensajes previos —seis turnos completos— se descarta el más
    antiguo y el modelo recibe once mensajes de historial más el actual:
    doce en total, con el mensaje actual al final.
    """
    model = RelayChatModel()
    assistant = build_assistant(model=model, connection=connection)
    for index in range(6):
        assistant.history.append(
            conversation_turn(f"consulta {index}", f"respuesta {index}")
        )

    answer(assistant, "sí, es ese")

    context = conversation_context(model)
    assert MAX_CONVERSATION_MESSAGES == 12
    assert len(context) == 12
    assert context == [
        "respuesta 0",
        "consulta 1",
        "respuesta 1",
        "consulta 2",
        "respuesta 2",
        "consulta 3",
        "respuesta 3",
        "consulta 4",
        "respuesta 4",
        "consulta 5",
        "respuesta 5",
        "sí, es ese",
    ]
    assert "consulta 0" not in context
    assert context[-1] == "sí, es ese"


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
    assert isinstance(session.movement_service, MovementService)
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
    assert "- **Cantidad:** 3" in rendered
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
    """
    Verifica el rechazo de una confirmación sin cambios parciales (T024,
    T055).

    El rechazo muestra el motivo real devuelto por el servicio —la falta de
    unidades sueltas—, no se presenta como una operación ejecutada y no deja
    cambios parciales.
    """
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
    assert "No hay unidades sueltas suficientes" in app.error[0].value
    assert app.success == []
    assert "Operación confirmada" not in rendered_markdown(app)
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
        f"- presentación: Caja x6 (id {catalog.box.id}, equivalencia 6)"
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
    Verifica la vista de las propuestas pendientes (C13, T041, T050, T053).

    La sección muestra la acción en lenguaje legible, su estado real y los
    campos de la propuesta —en lugar del volcado genérico del payload— y ofrece
    la confirmación delegada al único punto de `main.py` (D-A), además de la
    edición y el descarte.
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
    assert "Acción: Entrada de unidades sueltas" in rendered
    assert f"Producto (id {catalog.product.id})" in rendered
    assert "Cantidad: 3" in rendered
    assert "Motivo: Compra" in rendered
    assert f"Costo: {ABSENT_MARKER}" in rendered
    assert "- quantity" not in rendered
    assert "- reason" not in rendered
    assert [button.label for button in app.button] == [
        CONFIRM_LABEL,
        DISCARD_LABEL,
        EDIT_SUBMIT_LABEL,
    ]
    assert len(app.chat_input) == 0


def test_the_proposals_view_does_not_confirm_nor_touch_the_session() -> None:
    """
    Verifica la responsabilidad de vista de las propuestas (D-A, T041, T051,
    T053).

    El módulo no contiene el punto de confirmación ni invoca la edición del
    servicio, no nombra el servicio de propuestas, no escribe en la sesión y no
    importa el punto de entrada, los servicios, el dominio ni la persistencia:
    confirmar sigue siendo exclusivo de `app/ui/main.py`, y tanto la
    confirmación como la edición se delegan en callbacks con todo ya resuelto.
    La marca de confirmación en curso llega como un dato, no se lee del
    estado.
    """
    source = PROPOSALS_SECTION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert ".confirm(" not in source
    assert ".edit(" not in source
    assert "on_confirm" in source
    assert "PROCESSING_KEY" not in source
    assert "proposal_service" not in source
    assert "st.session_state" not in source
    assert "st.selectbox" not in source

    for module in imported_modules(tree):
        for prefix in FORBIDDEN_SECTION_IMPORTS:
            assert not (
                module == prefix or module.startswith(f"{prefix}.")
            ), f"proposals.py importa {module}"


def test_the_assistant_view_only_presents_and_delegates_confirmation() -> None:
    """
    Verifica que la vista del asistente no confirma ni usa servicios
    (T048).

    La sección conserva el chat y el control, pero la confirmación se delega en
    los callbacks de `main.py`: el módulo existe, no contiene el punto de
    confirmación, no nombra el servicio de propuestas y no importa el punto de
    entrada.
    """
    assert ASSISTANT_SECTION_PATH.is_file()
    source = ASSISTANT_SECTION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert ".confirm(" not in source
    assert "proposal_service" not in source
    assert "app.ui.main" not in imported_modules(tree)


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


def test_no_navigation_section_declares_a_pending_state(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica que ninguna sección de RF-09 queda pendiente (C4, T041, T049).

    «Operaciones» dejó de estar pendiente al implementarse T049: al recorrer la
    navegación, ninguna sección muestra el aviso de pendiente ni presenta datos
    inventados, y todas renderizan sin error.
    """
    app = app_for(make_assistant(connection))

    app.run()

    assert PENDING_SECTIONS == ()

    for section in SECTION_LABELS_EXPECTED:
        app.radio[0].set_value(section).run()

        shown = "\n".join(
            [info.value for info in app.info]
            + [warning.value for warning in app.warning]
            + [error.value for error in app.error]
        )

        assert not app.exception, section
        assert SECTION_PENDING_MESSAGE not in shown, section
        assert not any(
            "pendiente" in element.value.lower()
            for element in app.markdown
        ), section


def test_the_navigation_works_without_the_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Verifica la navegación lateral sin modelo configurado (C1, C2, T041).

    La aplicación no se abstiene: la sección del asistente —elegida por
    defecto— muestra el motivo de su indisponibilidad, «Operaciones» ofrece sus
    formularios y la sección de propuestas informa su vacío explícito, sin
    datos que inventar.
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
        assert tuple(app.selectbox(key=ACTION_SELECTOR_KEY).options) == tuple(
            label for _, label in OPERATION_ACTION_LABELS
        )
        assert any(button.label == SUBMIT_LABEL for button in app.button)
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
    faltante 4: aparece en ambas listas con nombre, SKU, total y faltante, y
    los conteos se muestran como métricas con los valores reales (T057).
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
    assert [metric.label for metric in app.metric] == [
        ACTIVE_PRODUCTS_LABEL,
        INACTIVE_PRODUCTS_LABEL,
    ]
    assert metric_values(app) == {
        ACTIVE_PRODUCTS_LABEL: "1",
        INACTIVE_PRODUCTS_LABEL: "0",
    }
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

    metrics = metric_values(app)

    assert counting.list_products_calls == 1
    assert metrics == {
        ACTIVE_PRODUCTS_LABEL: "1",
        INACTIVE_PRODUCTS_LABEL: "0",
    }

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
    assert metric_values(app) == {
        ACTIVE_PRODUCTS_LABEL: "0",
        INACTIVE_PRODUCTS_LABEL: "1",
    }
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

    messages = [info.value for info in app.info]

    assert not app.exception
    assert [header.value for header in app.header] == [HOME_HEADER]
    assert len(app.metric) == 0
    assert EMPTY_CATALOG_MESSAGE in messages
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
        assert metric_values(app) == {
            ACTIVE_PRODUCTS_LABEL: "1",
            INACTIVE_PRODUCTS_LABEL: "0",
        }

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
    sueltas, la suma de stock cerrado y el estado; el listado los muestra como
    las siete columnas del dataframe, en ese orden (T057), y el filtro «Todos»
    y el campo «Nombre» son los valores por defecto (B1, C1).
    """
    app = app_for(make_assistant(connection))

    open_products_section(app)

    records = product_records(app)

    assert not app.exception
    assert [header.value for header in app.header] == [PRODUCTS_HEADER]
    assert list(records[0]) == [
        SKU_LABEL,
        NAME_LABEL,
        BASE_UNIT_LABEL,
        MINIMUM_STOCK_LABEL,
        LOOSE_UNITS_LABEL,
        CLOSED_STOCK_LABEL,
        STATUS_LABEL,
    ]
    assert records == [
        {
            SKU_LABEL: "SKU-1",
            NAME_LABEL: "Producto",
            BASE_UNIT_LABEL: "botella",
            MINIMUM_STOCK_LABEL: 20,
            LOOSE_UNITS_LABEL: 4,
            CLOSED_STOCK_LABEL: 2,
            STATUS_LABEL: ACTIVE_STATUS,
        }
    ]
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

    El dataframe muestra el nombre y la unidad base tal como se registraron,
    dentro de sus propias celdas y sin interpretar Markdown; el dato de negocio
    no cambia.
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

    record = product_records(app)[0]

    assert not app.exception
    assert record[NAME_LABEL] == "Producto\ncon *#saltos*#"
    assert record[BASE_UNIT_LABEL] == "unidad\nbase"
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

    records = product_records(app)
    messages = [info.value for info in app.info]

    assert not app.exception
    assert [record[SKU_LABEL] for record in records] == [
        "SKU-ON",
        "SKU-OFF",
    ]
    assert [record[STATUS_LABEL] for record in records] == [
        ACTIVE_STATUS,
        INACTIVE_STATUS,
    ]
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

    records = product_records(app)

    assert not app.exception
    assert counting.find_products_by_name_calls == 1
    assert counting.find_product_by_sku_calls == 0
    assert counting.list_products_calls == 0
    assert [record[SKU_LABEL] for record in records] == ["SKU-1"]


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

    records = product_records(app)

    assert not app.exception
    assert counting.find_product_by_sku_calls == 1
    assert counting.find_products_by_name_calls == 0
    assert counting.list_products_calls == 0
    assert [record[SKU_LABEL] for record in records] == ["SKU-1"]


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

    records = product_records(app)

    assert not app.exception
    assert [record[SKU_LABEL] for record in records] == [
        "SKU-A",
        "SKU-B",
    ]
    assert NON_PAGINATED_NOTICE_TEMPLATE.format(count=2) in [
        info.value for info in app.info
    ]

    app.selectbox[0].set_value(SKU_FIELD).run()
    app.text_input[0].set_value("sku-b").run()

    records = product_records(app)

    assert [record[SKU_LABEL] for record in records] == ["SKU-B"]


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

    records = product_records(app)

    assert not app.exception
    assert counting.find_products_by_name_calls == 0
    assert counting.find_product_by_sku_calls == 0
    assert counting.list_products_calls == 1
    assert [record[SKU_LABEL] for record in records] == ["SKU-1"]


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
    assert len(app.dataframe) == 0


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
    assert len(app.dataframe) == 0


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
    assert len(app.dataframe) == 0
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

    record = product_records(app)[0]

    assert not app.exception
    assert record[CLOSED_STOCK_LABEL] == 5
    assert record[CLOSED_STOCK_LABEL] != 48


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

    records = product_records(app)

    assert not app.exception
    assert [record[SKU_LABEL] for record in records] == [
        "SKU-1",
        "SKU-2",
        "SKU-3",
    ]
    assert [record[NAME_LABEL] for record in records] == [
        "Zeta",
        "Alfa",
        "Beta",
    ]


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

    records = product_records(app)
    messages = [info.value for info in app.info]

    assert not app.exception
    assert NON_PAGINATED_NOTICE_TEMPLATE.format(count=total) in messages
    assert len(records) == total
    assert records[0][SKU_LABEL] == "SKU-1"
    assert records[-1][SKU_LABEL] == f"SKU-{total}"


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

        records = product_records(app)

        assert not app.exception
        assert len(app.error) == 0
        assert [header.value for header in app.header] == [PRODUCTS_HEADER]
        assert records == [
            {
                SKU_LABEL: "SKU-1",
                NAME_LABEL: "Producto",
                BASE_UNIT_LABEL: "botella",
                MINIMUM_STOCK_LABEL: 20,
                LOOSE_UNITS_LABEL: 4,
                CLOSED_STOCK_LABEL: 2,
                STATUS_LABEL: ACTIVE_STATUS,
            }
        ]
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
    Verifica presentaciones, factor, precio, stock cerrado y total (C9).

    El total equivalente es el derivado por el servicio —4 sueltas + 2 cajas
    con equivalencia 6 = 16— y no la suma de stock cerrado (2). La presentación
    base se indica y cada fila muestra el precio de venta vigente de su propia
    presentación (D3).
    """
    app = app_for(make_assistant(connection))

    open_product_detail(app, catalog.product)

    rendered = rendered_text(app)
    presentations = detail_records(app, DETAIL_PRESENTATION_COLUMNS)
    by_name = {
        row[PRESENTATION_LABEL]: row for row in presentations
    }

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
    assert tuple(by_name) == ("botella", "Caja x6")
    assert "Equivalencia" in by_name["Caja x6"]
    assert by_name["Caja x6"][FACTOR_LABEL] == "6"
    assert by_name["Caja x6"][SALE_PRICE_LABEL] == "600.00"
    assert by_name["Caja x6"][CLOSED_STOCK_LABEL] == "2"
    assert by_name["Caja x6"][BASE_COLUMN_LABEL] == ""
    assert by_name["botella"][FACTOR_LABEL] == "1"
    assert by_name["botella"][SALE_PRICE_LABEL] == "100.00"
    assert by_name["botella"][CLOSED_STOCK_LABEL] == "0"
    assert by_name["botella"][BASE_COLUMN_LABEL] == TABLE_YES_TEXT
    assert FACTOR_HELP_CAPTION in [caption.value for caption in app.caption]
    assert f"{LOOSE_UNITS_LABEL}: 4" in rendered
    assert f"{TOTAL_EQUIVALENT_LABEL}: 16" in rendered
    assert f"{TOTAL_EQUIVALENT_LABEL}: 6" not in rendered
    assert ":green-badge[Activo]" in rendered_markdown(app)


def test_product_detail_shows_each_presentation_sale_price(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
) -> None:
    """
    Verifica el precio de venta vigente de cada presentación (D3).

    Los precios reales almacenados son distintos —100 para la botella y 600
    para la caja—, de modo que una fila que reutilizara el precio de otra
    presentación no pasaría. Los importes se comparan con `format_money`, que
    fija dos decimales.
    """
    app = app_for(make_assistant(connection))

    open_product_detail(app, catalog.product)

    stored = {
        presentation.name: presentation
        for presentation in presentations.list_by_product(catalog.product.id)
    }
    by_name = {
        row[PRESENTATION_LABEL]: row
        for row in detail_records(app, DETAIL_PRESENTATION_COLUMNS)
    }

    assert not app.exception
    assert set(by_name) == set(stored)
    for name, presentation in stored.items():
        assert by_name[name][SALE_PRICE_LABEL] == format_money(
            presentation.sale_price
        )
    assert by_name["botella"][SALE_PRICE_LABEL] == "100.00"
    assert by_name["Caja x6"][SALE_PRICE_LABEL] == "600.00"
    assert all(
        re.fullmatch(r"\d+\.\d{2}", row[SALE_PRICE_LABEL])
        for row in by_name.values()
    )


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
    assert len(app.text) == 0
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
    presentations = detail_records(app, DETAIL_PRESENTATION_COLUMNS)
    base_row = next(
        row
        for row in presentations
        if row[PRESENTATION_LABEL] == "botella"
    )

    assert not app.exception
    assert base_row[CLOSED_STOCK_LABEL] == "0"
    assert all(
        ABSENT_MARKER not in str(value)
        for row in presentations
        for value in row.values()
    )
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

    movements = detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    by_reason = {row[REASON_LABEL]: row for row in movements}

    assert not app.exception
    assert by_reason["Compra"][MOVEMENT_TYPE_LABEL] == "Entrada suelta"
    assert by_reason["Venta"][MOVEMENT_TYPE_LABEL] == "Salida suelta"
    assert by_reason["Compra"][QUANTITY_LABEL] == "2"
    assert by_reason["Compra"][COST_LABEL] == "15.50"
    assert by_reason["Compra"][APPLIED_PRICE_LABEL] == ""
    assert by_reason["Venta"][COST_LABEL] == ""
    assert by_reason["Venta"][APPLIED_PRICE_LABEL] == "100.00"
    assert by_reason["Compra"][PRESENTATION_LABEL] == "botella"
    assert by_reason["Venta"][PRESENTATION_LABEL] == "botella"
    assert any(
        row[INITIAL_STOCK_LABEL] == TABLE_YES_TEXT for row in movements
    )
    assert all(
        DETAIL_INSTANT_PATTERN.search(
            f"{DATE_LABEL}: {row[DATE_LABEL]}"
        )
        for row in movements
    )


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

    changes = detail_records(app, DETAIL_PRICE_HISTORY_COLUMNS)
    messages = [info.value for info in app.info]

    assert not app.exception
    assert len(changes) == 2
    assert changes[0][PRESENTATION_LABEL] == "Caja x6"
    assert changes[0][PRICE_PREVIOUS_LABEL] == "600.00"
    assert changes[0][PRICE_NEW_LABEL] == "650.00"
    assert changes[1][PRESENTATION_LABEL] == "Caja x6"
    assert changes[1][PRICE_PREVIOUS_LABEL] == "650.00"
    assert changes[1][PRICE_NEW_LABEL] == "700.50"
    assert all(
        change[EVENT_LABEL] == PRICE_CHANGE_EVENT_TYPE
        for change in changes
    )
    assert all(
        DETAIL_INSTANT_PATTERN.search(
            f"{DATE_LABEL}: {change[DATE_LABEL]}"
        )
        for change in changes
    )
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

    changes = detail_records(app, DETAIL_PRICE_HISTORY_COLUMNS)
    new_prices = [change[PRICE_NEW_LABEL] for change in changes]

    assert not app.exception
    assert new_prices == ["650.00", "700.50"]


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
    presentation_names = [
        row[PRESENTATION_LABEL]
        for row in detail_records(app, DETAIL_PRESENTATION_COLUMNS)
    ]
    movement_reasons = [
        row[REASON_LABEL]
        for row in detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    ]
    price_changes = detail_records(app, DETAIL_PRICE_HISTORY_COLUMNS)

    assert not app.exception
    assert f"{STATUS_LABEL}: {INACTIVE_STATUS}" in rendered
    assert f"{NAME_LABEL}: Producto inactivo" in rendered
    assert ":gray-badge[Inactivo]" in rendered_markdown(app)
    assert "Caja x6" in presentation_names
    assert "Compra histórica" in movement_reasons
    assert price_changes[0][EVENT_LABEL] == PRICE_CHANGE_EVENT_TYPE
    assert price_changes[0][PRICE_NEW_LABEL] == "65.00"


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
    assert len(app.dataframe) == 0


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

    presentation_names = [
        row[PRESENTATION_LABEL]
        for row in detail_records(app, DETAIL_PRESENTATION_COLUMNS)
    ]
    movement_reasons = [
        row[REASON_LABEL]
        for row in detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    ]
    messages = [info.value for info in app.info]

    assert not app.exception
    assert (
        PRESENTATIONS_NOTICE_TEMPLATE.format(count=3) in messages
    )
    assert MOVEMENTS_NOTICE_TEMPLATE.format(count=12) in messages
    assert (
        PRICE_HISTORY_NOTICE_TEMPLATE.format(count=2) in messages
    )
    assert "Fardo x12" in presentation_names
    assert "Movimiento 10" in movement_reasons


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

    movements = detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    costs = [row[COST_LABEL] for row in movements if row[COST_LABEL]]
    applied_prices = [
        row[APPLIED_PRICE_LABEL]
        for row in movements
        if row[APPLIED_PRICE_LABEL]
    ]

    assert not app.exception
    assert costs == ["0.00"]
    assert applied_prices == []
    assert all(
        "None" not in str(value)
        for row in movements
        for value in row.values()
    )


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

    records = product_records(app)
    selector = next(
        selectbox
        for selectbox in app.selectbox
        if selectbox.label == PRODUCT_SELECTOR_LABEL
    )

    assert not app.exception
    assert selector.value == NO_SELECTION_LABEL
    assert [row[SKU_LABEL] for row in records] == ["SKU-B"]
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
        presentation_names = [
            row[PRESENTATION_LABEL]
            for row in detail_records(app, DETAIL_PRESENTATION_COLUMNS)
        ]
        price_changes = detail_records(
            app, DETAIL_PRICE_HISTORY_COLUMNS
        )

        assert not app.exception
        assert len(app.error) == 0
        assert f"{TOTAL_EQUIVALENT_LABEL}: 16" in rendered
        assert "Caja x6" in presentation_names
        assert price_changes[0][PRICE_NEW_LABEL] == "650.00"
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

    records = detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    movement_reasons = [row[REASON_LABEL] for row in records]

    assert not app.exception
    assert movement_reasons == ["Alfa primero", "Alfa último"]


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

    records = detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    movement_reasons = [row[REASON_LABEL] for row in records]

    assert not app.exception
    assert movement_reasons == [reason]
    assert counting_inventory.get_movement_history_calls == 1
    assert counting_inventory.history_calls[0][1] == kind.value
    for other_kind, other_reason in reasons.items():
        if other_kind is not kind:
            assert other_reason not in movement_reasons


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

    records = detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    movement_reasons = [row[REASON_LABEL] for row in records]

    assert not app.exception
    assert included[0] in movement_reasons
    assert included[1] in movement_reasons
    assert excluded[0] not in movement_reasons
    assert excluded[1] not in movement_reasons
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

    records = detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    movement_reasons = [row[REASON_LABEL] for row in records]

    assert not app.exception
    assert movement_reasons == ["Alfa entrada del día"]
    assert "Alfa salida del día" not in movement_reasons
    assert "Alfa entrada de otro día" not in movement_reasons
    assert "Beta entrada del día" not in movement_reasons
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

    without_period = [
        row[REASON_LABEL]
        for row in detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    ]

    assert not app.exception
    assert without_period == ["Día anterior", "Día elegido", "Día siguiente"]

    set_movement_period(
        app, date(2026, 3, 10), date(2026, 3, 10)
    )

    same_day = [
        row[REASON_LABEL]
        for row in detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    ]

    assert not app.exception
    assert same_day == ["Día elegido"]


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

    from_only = [
        row[REASON_LABEL]
        for row in detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    ]

    assert not app.exception
    assert from_only == ["Límite elegido", "Límite posterior"]

    movement_date_input(app, START_DATE_LABEL).set_value(None)
    movement_date_input(app, END_DATE_LABEL).set_value(date(2026, 3, 10))
    app.run()

    until_only = [
        row[REASON_LABEL]
        for row in detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    ]

    assert not app.exception
    assert until_only == ["Límite anterior", "Límite elegido"]


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
    assert len(app.dataframe) == 0
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

    movement_reasons = [
        row[REASON_LABEL]
        for row in detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    ]

    assert not app.exception
    assert MOVEMENTS_NOTICE_TEMPLATE.format(count=12) in [
        info.value for info in app.info
    ]
    assert movement_reasons == reasons


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

    movement_reasons = [
        row[REASON_LABEL]
        for row in detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    ]

    assert not app.exception
    assert label == "SKU-OFF-MOV — Inactivo (inactivo)"
    assert label in movement_selectbox(
        app, PRODUCT_FILTER_LABEL
    ).options
    assert "Movimiento histórico inactivo" in movement_reasons


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

    records = detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    movement_reasons = [row[REASON_LABEL] for row in records]
    movement_types = [row[MOVEMENT_TYPE_LABEL] for row in records]

    assert not app.exception
    assert "Entrada original" in movement_reasons
    assert "Compensación histórica" in movement_reasons
    assert "Compensación" in movement_types
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

        movement_reasons = [
            row[REASON_LABEL]
            for row in detail_records(app, DETAIL_MOVEMENT_COLUMNS)
        ]

        assert not app.exception
        assert len(app.error) == 0
        assert "Movimiento real sin modelo" in movement_reasons
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

    records = detail_records(app, DETAIL_MOVEMENT_COLUMNS)
    by_reason = {row[REASON_LABEL]: row for row in records}

    assert not app.exception
    assert (
        by_reason["Con costo"][DATE_LABEL]
        == "2026-03-10 10:15:00 -03:00"
    )
    assert by_reason["Con costo"][COST_LABEL] == "12.50"
    assert by_reason["Con costo"][APPLIED_PRICE_LABEL] == ""
    assert by_reason["Con precio aplicado"][COST_LABEL] == ""
    assert by_reason["Con precio aplicado"][APPLIED_PRICE_LABEL] == "99.90"
    assert by_reason["Con costo cero"][COST_LABEL] == "0.00"
    assert by_reason["Sin opcionales"][COST_LABEL] == ""
    assert by_reason["Sin opcionales"][APPLIED_PRICE_LABEL] == ""
    assert all(row[INITIAL_STOCK_LABEL] == "" for row in records)
    assert all(
        "None" not in str(value) and ABSENT_MARKER not in str(value)
        for row in records
        for value in row.values()
    )


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

    records = detail_records(app, DETAIL_MOVEMENT_COLUMNS)

    assert not app.exception
    assert records[0][PRESENTATION_LABEL] == "presentación #9999"
    assert records[0][REASON_LABEL] == "Presentación ajena"


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

    records = detail_records(app, DETAIL_PRICE_HISTORY_COLUMNS)
    infos = [info.value for info in app.info]
    by_presentation = {
        row[PRESENTATION_LABEL]: row for row in records
    }

    assert not app.exception
    assert PRICE_HISTORY_SECTION_NOTICE_TEMPLATE.format(count=2) in infos
    assert by_presentation["botella"][PRICE_PREVIOUS_LABEL] == "100.00"
    assert by_presentation["botella"][PRICE_NEW_LABEL] == "110.00"
    assert by_presentation["Caja x6"][PRICE_PREVIOUS_LABEL] == "600.00"
    assert by_presentation["Caja x6"][PRICE_NEW_LABEL] == "660.00"
    assert all(
        row[EVENT_LABEL] == PRICE_CHANGE_EVENT_TYPE for row in records
    )
    assert [row[PRICE_NEW_LABEL] for row in records] == [
        "110.00",
        "660.00",
    ]
    for row in records:
        assert DETAIL_INSTANT_PATTERN.search(
            f"{DATE_LABEL}: {row[DATE_LABEL]}"
        )
        assert "T" not in row[DATE_LABEL]
        assert "+00:00" not in row[DATE_LABEL]


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

    records = detail_records(app, DETAIL_PRICE_HISTORY_COLUMNS)
    infos = [info.value for info in app.info]

    assert not app.exception
    assert PRICE_HISTORY_SECTION_NOTICE_TEMPLATE.format(count=1) in infos
    assert len(records) == 1
    assert records[0][PRESENTATION_LABEL] == "Caja x6"
    assert records[0][PRICE_NEW_LABEL] == "660.00"
    assert "110.00" not in [
        value for row in records for value in row.values()
    ]
    assert "botella" not in [
        row[PRESENTATION_LABEL] for row in records
    ]


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

    records = detail_records(app, DETAIL_PRICE_HISTORY_COLUMNS)

    assert not app.exception
    assert records[0][PRESENTATION_LABEL] == "presentación #9999"
    assert records[0][PRICE_NEW_LABEL] == "12.00"


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
    assert len(app.dataframe) == 0


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
    assert len(app.dataframe) == 0


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
    assert len(app.dataframe) == 0


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

    records = detail_records(app, DETAIL_PRICE_HISTORY_COLUMNS)

    assert not app.exception
    assert label == "SKU-PRICE-OFF — Inactivo con historial (inactivo)"
    assert label in price_history_selectbox(
        app, PRICE_HISTORY_PRODUCT_LABEL
    ).options
    assert records[0][PRICE_NEW_LABEL] == "17.00"


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

    records = detail_records(app, DETAIL_PRICE_HISTORY_COLUMNS)
    infos = [info.value for info in app.info]

    assert not app.exception
    assert PRICE_HISTORY_SECTION_NOTICE_TEMPLATE.format(count=12) in infos
    assert [row[PRICE_NEW_LABEL] for row in records] == [
        f"{101 + index}.00" for index in range(1, 13)
    ]
    assert all(
        DETAIL_INSTANT_PATTERN.search(
            f"{DATE_LABEL}: {row[DATE_LABEL]}"
        )
        for row in records
    )


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
    assert len(app.dataframe) == 0


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

        records = detail_records(app, DETAIL_PRICE_HISTORY_COLUMNS)

        assert not app.exception
        assert len(app.error) == 0
        assert records[0][PRICE_NEW_LABEL] == "15.00"
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
    Verifica que la vista usa el formateo compartido y no helpers propios
    (T047, T057).

    Las filas se componen con `price_change_row`, compartido con el detalle de
    producto, que aplica `format_money` y `format_instant` de
    `app.ui.formatting`; la sección no usa `describe_price_change`,
    `_format_price` ni `isoformat()`.
    """
    source = PRICE_HISTORY_SECTION_PATH.read_text(encoding="utf-8")

    assert "price_change_row" in source
    assert "describe_price_change" not in source
    assert "_format_price" not in source
    assert "isoformat(" not in source


# --- Sección Operaciones: formularios de propuestas (T049, RF-09) ---


def open_operations_section(app: AppTest) -> None:
    """Ejecuta la aplicación y abre la sección Operaciones (T049)."""
    app.run()
    app.radio[0].set_value("Operaciones").run()


def choose_operation(app: AppTest, action: str) -> None:
    """Elige la acción cuyo formulario se va a completar (T049)."""
    labels = dict(OPERATION_ACTION_LABELS)
    app.selectbox(key=ACTION_SELECTOR_KEY).set_value(labels[action]).run()


def choose_operation_product(app: AppTest, product: ProductRecord) -> None:
    """Elige el producto del formulario de operación (T049)."""
    app.selectbox(key=PRODUCT_SELECTOR_KEY).set_value(
        product_option_label(product)
    ).run()


def choose_operation_presentation(app: AppTest, catalog: Catalog) -> None:
    """Elige la presentación cerrada del producto en el formulario (T049)."""
    selector = app.selectbox(
        key=PRESENTATION_SELECTOR_KEY_TEMPLATE.format(
            product_id=catalog.product.id
        )
    )
    option = next(
        label for label in selector.options if catalog.box.name in label
    )
    selector.set_value(option)


def choose_operation_movement(app: AppTest, catalog: Catalog) -> None:
    """Elige el primer movimiento compensable en el formulario (T049)."""
    selector = app.selectbox(
        key=MOVEMENT_SELECTOR_KEY_TEMPLATE.format(
            product_id=catalog.product.id
        )
    )
    selector.set_value(selector.options[1]).run()


def operation_text_input(app: AppTest, label: str) -> Any:
    """Devuelve el campo de texto con la etiqueta dada (T049)."""
    return next(
        element for element in app.text_input if element.label == label
    )


def operation_number_input(app: AppTest, label: str) -> Any:
    """Devuelve el campo numérico del formulario con esa etiqueta (T049)."""
    return next(
        element for element in app.number_input if element.label == label
    )


def submit_operation(app: AppTest) -> None:
    """Envía el formulario de la acción elegida (T049)."""
    next(
        button for button in app.button if button.label == SUBMIT_LABEL
    ).click().run()


def manual_proposals_of(app: AppTest) -> list[Proposal]:
    """Devuelve las propuestas creadas con los formularios (T049)."""
    stored = app.session_state.get(MANUAL_PROPOSALS_KEY)

    return [] if stored is None else list(stored)


def fill_operation_form(
    app: AppTest,
    action: str,
    catalog: Catalog,
) -> None:
    """Completa el formulario de la acción con valores válidos (T049)."""
    if action == "alta_producto":
        operation_text_input(app, SKU_FIELD_LABEL).set_value("SKU-T049")
        operation_text_input(app, NAME_FIELD_LABEL).set_value("Producto T049")
        operation_text_input(app, BASE_UNIT_FIELD_LABEL).set_value("unidad")
        operation_text_input(app, BASE_PRICE_FIELD_LABEL).set_value("150.50")
    elif action == "modificacion_producto":
        operation_text_input(app, NEW_NAME_LABEL).set_value(
            "Producto T049 modificado"
        )
        operation_text_input(app, NEW_MINIMUM_STOCK_LABEL).set_value("25")
    elif action == "alta_presentacion":
        operation_text_input(app, PRESENTATION_NAME_FIELD_LABEL).set_value(
            "Caja x12"
        )
        operation_number_input(app, FACTOR_FIELD_LABEL).set_value(12)
        operation_text_input(app, PRESENTATION_PRICE_LABEL).set_value(
            "1200.75"
        )
    elif action == "entrada_suelta":
        operation_number_input(app, LOOSE_QUANTITY_LABEL).set_value(3)
        operation_text_input(app, REASON_FIELD_LABEL).set_value("Compra")
        operation_text_input(app, COST_FIELD_LABEL).set_value("10.25")
    elif action == "entrada_cerrada":
        choose_operation_presentation(app, catalog)
        operation_number_input(app, CLOSED_QUANTITY_LABEL).set_value(2)
        operation_text_input(app, REASON_FIELD_LABEL).set_value("Compra")
        operation_text_input(app, COST_FIELD_LABEL).set_value("100")
    elif action == "salida_suelta":
        operation_number_input(app, LOOSE_QUANTITY_LABEL).set_value(1)
        operation_text_input(app, REASON_FIELD_LABEL).set_value("Venta")
    elif action == "salida_cerrada":
        choose_operation_presentation(app, catalog)
        operation_number_input(app, CLOSED_QUANTITY_LABEL).set_value(1)
        operation_text_input(app, REASON_FIELD_LABEL).set_value("Venta")
    elif action == "apertura":
        choose_operation_presentation(app, catalog)
        operation_number_input(app, APERTURE_QUANTITY_LABEL).set_value(1)
        operation_text_input(app, REASON_FIELD_LABEL).set_value("Apertura")
    elif action == "compensacion":
        choose_operation_movement(app, catalog)
        operation_number_input(app, COMPENSATION_QUANTITY_LABEL).set_value(1)
        operation_text_input(app, REASON_FIELD_LABEL).set_value("Ajuste")
    elif action == "cambio_precio":
        choose_operation_presentation(app, catalog)
        operation_text_input(app, NEW_PRICE_LABEL).set_value("110.5")
    else:
        raise AssertionError(f"Acción sin formulario en la prueba: {action}")


def test_operations_offers_only_the_admitted_actions(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica las diez acciones y la ausencia de controles excluidos (C10).

    El selector ofrece exactamente las diez acciones de las propuestas, en su
    orden, sin ninguna opción para desactivar productos ni para cambiar el
    factor de una presentación existente. La modificación de producto no tiene
    ningún control de factor ni de baja, mientras que el alta de una
    presentación nueva sí admite un factor.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)

    options = tuple(app.selectbox(key=ACTION_SELECTOR_KEY).options)

    assert not app.exception
    assert [header.value for header in app.header] == ["Operaciones"]
    assert app.selectbox(key=ACTION_SELECTOR_KEY).label == (
        "Acción a preparar"
    )
    assert options == tuple(label for _, label in OPERATION_ACTION_LABELS)
    assert not any("desactiv" in option.lower() for option in options)
    assert not any("factor" in option.lower() for option in options)

    choose_operation(app, "modificacion_producto")
    choose_operation_product(app, catalog.product)

    labels = [
        element.label
        for element in (
            [*app.text_input, *app.number_input, *app.selectbox]
            + [*app.checkbox, *app.button]
        )
    ]

    assert not app.exception
    assert not any("desactiv" in label.lower() for label in labels)
    assert not any("factor" in label.lower() for label in labels)

    choose_operation(app, "alta_presentacion")
    choose_operation_product(app, catalog.product)

    factor_input = next(
        element
        for element in app.number_input
        if element.label == "Equivalencia (unidades base)"
    )

    assert not app.exception
    assert factor_input.label == FACTOR_FIELD_LABEL
    assert "unidades base" in factor_input.help


def test_product_and_presentation_forms_show_the_approved_labels_and_help(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica las etiquetas, placeholders y ayudas de los formularios (RF-09).

    Las etiquetas visibles describen la unidad base, la presentación adicional
    y la equivalencia; los ejemplos de los `text_input` son orientativos y las
    ayudas aportan el contexto que el placeholder no repite. Los campos
    numéricos no usan placeholder: su ayuda incluye un ejemplo visible.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)

    action_selector = app.selectbox(key=ACTION_SELECTOR_KEY)

    assert not app.exception
    assert action_selector.label == "Acción a preparar"
    assert action_selector.help == "Operación que se va a preparar."

    sku = operation_text_input(app, "SKU")
    name = operation_text_input(app, "Nombre del producto")
    base_unit = operation_text_input(app, "Unidad base")
    base_price = operation_text_input(app, "Precio de venta de la unidad base")
    minimum_stock = operation_number_input(app, "Stock mínimo")
    initial_loose_units = operation_number_input(
        app, "Unidades sueltas iniciales"
    )
    base_name = operation_text_input(
        app, "Nombre visible de la unidad base (opcional)"
    )
    additional_name = operation_text_input(
        app, "Nombre de la presentación adicional"
    )
    factor = operation_number_input(app, "Equivalencia (unidades base)")
    presentation_price = operation_text_input(
        app, "Precio de venta de la presentación"
    )
    initial_closed_stock = operation_number_input(
        app, "Cantidad inicial de presentaciones cerradas"
    )
    captions = [caption.value for caption in app.caption]

    assert not app.exception
    assert sku.placeholder == "Ej.: COCA-500"
    assert sku.help == "Código único; no se modifica después del alta."
    assert name.placeholder == "Ej.: Coca-Cola 500 ml"
    assert base_unit.placeholder == "Ej.: botella"
    assert base_unit.help == "Unidad mínima con la que se mide el stock."
    assert base_price.placeholder == "Ej.: 1500.00"
    assert base_price.help == (
        "Precio de una unidad base; no es el precio de un fardo."
    )
    assert minimum_stock.help == (
        "Umbral de alerta en unidades base. Ej.: 6."
    )
    assert initial_loose_units.help == (
        "Stock inicial no agrupado en presentaciones cerradas. Ej.: 2."
    )
    assert base_name.placeholder == "Ej.: Botella 500 ml"
    assert base_name.help == (
        "Solo cambia el nombre de la presentación de factor 1; si se "
        "omite, se usa la unidad base."
    )
    assert any("Presentación adicional" in value for value in captions)
    assert additional_name.placeholder == "Ej.: Fardo x 6"
    assert additional_name.help == "Empaque opcional además de la unidad base."
    assert factor.help == (
        "Cantidad de unidades base que contiene la presentación; un fardo "
        "con equivalencia 6 contiene 6 botellas. Ej.: 6."
    )
    assert presentation_price.placeholder == "Ej.: 7500.00"
    assert presentation_price.help == (
        "Precio de una presentación completa, no de cada unidad base."
    )
    assert initial_closed_stock.help == (
        "Presentaciones cerradas existentes al dar de alta. Ej.: 1."
    )

    choose_operation(app, "alta_presentacion")
    choose_operation_product(app, catalog.product)

    product_selector = app.selectbox(key=PRODUCT_SELECTOR_KEY)
    presentation_name = operation_text_input(
        app, "Nombre de la presentación"
    )
    presentation_factor = operation_number_input(
        app, "Equivalencia (unidades base)"
    )
    presentation_sale_price = operation_text_input(
        app, "Precio de venta de la presentación"
    )

    assert not app.exception
    assert product_selector.label == "Producto"
    assert product_selector.help == "Producto afectado por la operación."
    assert presentation_name.placeholder == "Ej.: Fardo x 6"
    assert presentation_factor.help == (
        "Cantidad de unidades base que contiene la presentación; un fardo "
        "con equivalencia 6 contiene 6 botellas. Ej.: 6."
    )
    assert presentation_sale_price.placeholder == "Ej.: 7500.00"
    assert presentation_sale_price.help == (
        "Precio de una presentación completa, no de cada unidad base."
    )

    choose_operation(app, "cambio_precio")
    choose_operation_product(app, catalog.product)

    presentation_selector = app.selectbox(
        key=PRESENTATION_SELECTOR_KEY_TEMPLATE.format(
            product_id=catalog.product.id
        )
    )
    new_price = operation_text_input(app, "Nuevo precio de venta")

    assert not app.exception
    assert presentation_selector.label == "Presentación"
    assert presentation_selector.help == (
        "Presentación afectada por la operación."
    )
    assert new_price.placeholder == "Ej.: 1600.00"
    assert new_price.help == (
        "Precio que tendrá la presentación después de confirmar."
    )


def test_movement_forms_use_contextual_labels_and_reason_examples(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica las etiquetas contextuales de cantidad y motivos (RF-09)."""
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "entrada_suelta")
    choose_operation_product(app, catalog.product)

    loose_quantity = operation_number_input(
        app, "Cantidad de unidades sueltas"
    )
    reason = operation_text_input(app, "Motivo")
    cost = operation_text_input(app, "Costo de ingreso (opcional)")

    assert not app.exception
    assert loose_quantity.label == LOOSE_QUANTITY_LABEL
    assert loose_quantity.help == "Cantidad entera de la operación. Ej.: 2."
    assert reason.placeholder == "Ej.: Compra de mercadería"
    assert reason.help == "Queda registrado en el historial."
    assert cost.placeholder == "Ej.: 1200.00"
    assert cost.help == (
        "Importe total informado; no se distribuye entre unidades; solo en "
        "entradas."
    )

    choose_operation(app, "salida_suelta")
    choose_operation_product(app, catalog.product)

    assert not app.exception
    loose_exit_quantity = operation_number_input(
        app, "Cantidad de unidades sueltas"
    )
    assert loose_exit_quantity.label == LOOSE_QUANTITY_LABEL
    assert loose_exit_quantity.help == (
        "Cantidad entera de la operación. Ej.: 2."
    )
    assert operation_text_input(app, "Motivo").placeholder == "Ej.: Venta"

    choose_operation(app, "entrada_cerrada")
    choose_operation_product(app, catalog.product)

    assert not app.exception
    closed_entry_quantity = operation_number_input(
        app, "Cantidad de presentaciones cerradas"
    )
    assert closed_entry_quantity.label == CLOSED_QUANTITY_LABEL
    assert closed_entry_quantity.help == (
        "Cantidad entera de la operación. Ej.: 2."
    )
    assert operation_text_input(app, "Motivo").placeholder == (
        "Ej.: Compra de mercadería"
    )

    choose_operation(app, "salida_cerrada")
    choose_operation_product(app, catalog.product)

    assert not app.exception
    closed_exit_quantity = operation_number_input(
        app, "Cantidad de presentaciones cerradas"
    )
    assert closed_exit_quantity.label == CLOSED_QUANTITY_LABEL
    assert closed_exit_quantity.help == (
        "Cantidad entera de la operación. Ej.: 2."
    )
    assert operation_text_input(app, "Motivo").placeholder == "Ej.: Venta"

    choose_operation(app, "apertura")
    choose_operation_product(app, catalog.product)

    assert not app.exception
    aperture_quantity = operation_number_input(
        app, "Cantidad de presentaciones a abrir"
    )
    assert aperture_quantity.label == APERTURE_QUANTITY_LABEL
    assert aperture_quantity.help == (
        "Cantidad entera de la operación. Ej.: 2."
    )
    assert operation_text_input(app, "Motivo").placeholder == (
        "Ej.: Apertura para venta"
    )

    choose_operation(app, "compensacion")
    choose_operation_product(app, catalog.product)

    movement_selector = app.selectbox(
        key=MOVEMENT_SELECTOR_KEY_TEMPLATE.format(
            product_id=catalog.product.id
        )
    )

    assert not app.exception
    assert movement_selector.label == "Movimiento a compensar"
    assert movement_selector.help == "Movimiento que se va a compensar."

    choose_operation_movement(app, catalog)

    assert not app.exception
    compensation_quantity = operation_number_input(
        app, "Cantidad a compensar"
    )
    assert compensation_quantity.label == COMPENSATION_QUANTITY_LABEL
    assert compensation_quantity.help == (
        "Cantidad entera de la operación. Ej.: 2."
    )
    assert operation_text_input(app, "Motivo").placeholder == (
        "Ej.: Corrección de inventario"
    )


def test_closed_entry_selector_offers_only_closed_presentations(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la etiqueta y las opciones del selector de entrada cerrada.

    La presentación base (equivalencia 1) no es una presentación cerrada: el
    selector usa la etiqueta contextual aprobada y solo ofrece la caja x6 del
    catálogo de prueba.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "entrada_cerrada")
    choose_operation_product(app, catalog.product)

    selector = app.selectbox(
        key=PRESENTATION_SELECTOR_KEY_TEMPLATE.format(
            product_id=catalog.product.id
        )
    )

    assert not app.exception
    assert selector.label == CLOSED_ENTRY_PRESENTATION_LABEL
    assert selector.label == "Presentación cerrada recibida"
    assert selector.options == [
        NO_SELECTION_LABEL,
        f"{catalog.box.name} (equivalencia {catalog.box.factor})",
    ]
    assert "botella (equivalencia 1)" not in selector.options


def test_closed_entry_submits_the_chosen_closed_presentation(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
) -> None:
    """
    Verifica que la entrada cerrada conserva la presentación elegida (RF-03).

    El payload identifica la caja x6 del catálogo y no la presentación base,
    aunque la base exista con factor 1 en el mismo producto.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "entrada_cerrada")
    choose_operation_product(app, catalog.product)
    choose_operation_presentation(app, catalog)
    operation_number_input(app, CLOSED_QUANTITY_LABEL).set_value(2)
    operation_text_input(app, REASON_FIELD_LABEL).set_value("Compra")
    operation_text_input(app, COST_FIELD_LABEL).set_value("100")

    submit_operation(app)

    payload = manual_proposals_of(app)[0].payload
    base = next(
        presentation
        for presentation in presentations.list_by_product(
            catalog.product.id
        )
        if presentation.factor == 1
    )

    assert not app.exception
    assert isinstance(payload, ClosedEntryPayload)
    assert payload.presentation_id == catalog.box.id
    assert payload.presentation_id != base.id


def test_closed_entry_without_closed_presentations_is_informed(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica el estado de un producto que solo tiene la unidad base (C4).

    Sin presentaciones de factor mayor que 1 no se muestra el selector ni el
    formulario, se informa el mensaje exacto y no se puede crear una propuesta
    de entrada cerrada.
    """
    stored = zero_stock_product(
        product_service,
        sku="SKU-SOLO-BASE",
        name="Solo unidad base",
    )
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "entrada_cerrada")
    choose_operation_product(app, stored)

    infos = [info.value for info in app.info]

    assert not app.exception
    assert infos == [NO_CLOSED_PRESENTATIONS_MESSAGE]
    assert NO_CLOSED_PRESENTATIONS_MESSAGE == (
        "El producto no tiene presentaciones cerradas registradas. Usá la "
        "entrada de unidades sueltas para la unidad base."
    )
    assert [selectbox.label for selectbox in app.selectbox] == [
        ACTION_SELECTOR_LABEL,
        "Producto",
    ]
    assert not any(button.label == SUBMIT_LABEL for button in app.button)
    assert manual_proposals_of(app) == []


def test_closed_exit_selector_uses_its_own_sentinel_and_only_closed_presentations(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica el selector de la salida de presentaciones cerradas (T049, C4).

    La presentación base no se ofrece y el selector usa un centinela propio,
    distinto del de los demás formularios, para no confundir la ausencia de
    selección con la de un producto.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "salida_cerrada")
    choose_operation_product(app, catalog.product)

    selector = app.selectbox(
        key=PRESENTATION_SELECTOR_KEY_TEMPLATE.format(
            product_id=catalog.product.id
        )
    )

    assert not app.exception
    assert selector.label == "Presentación"
    assert selector.options == [
        CLOSED_PRESENTATION_NO_SELECTION_LABEL,
        f"{catalog.box.name} (equivalencia {catalog.box.factor})",
    ]
    assert selector.value == CLOSED_PRESENTATION_NO_SELECTION_LABEL
    assert CLOSED_PRESENTATION_NO_SELECTION_LABEL == (
        "Elegí una presentación cerrada"
    )
    assert f"{catalog.product.base_unit} (equivalencia 1)" not in (
        selector.options
    )


def test_closed_exit_without_closed_presentations_is_informed(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica el estado de una salida cerrada sin presentaciones cerradas (C4).

    Si el producto solo tiene la unidad base no se muestra el selector ni el
    formulario de salida cerrada, se informa el mensaje exacto y no se puede
    crear una propuesta.
    """
    stored = zero_stock_product(
        product_service,
        sku="SKU-SALIDA-SOLO-BASE",
        name="Salida solo unidad base",
    )
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "salida_cerrada")
    choose_operation_product(app, stored)

    infos = [info.value for info in app.info]

    assert not app.exception
    assert infos == [NO_CLOSED_EXIT_PRESENTATIONS_MESSAGE]
    assert NO_CLOSED_EXIT_PRESENTATIONS_MESSAGE == (
        "El producto no tiene presentaciones cerradas disponibles para "
        "retirar."
    )
    assert [selectbox.label for selectbox in app.selectbox] == [
        ACTION_SELECTOR_LABEL,
        "Producto",
    ]
    assert not any(button.label == SUBMIT_LABEL for button in app.button)
    assert manual_proposals_of(app) == []


def test_closed_exit_submits_the_chosen_closed_presentation(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
) -> None:
    """
    Verifica que la salida cerrada conserva la presentación elegida (RF-03).

    El payload identifica la caja x6 del catálogo y no la presentación base,
    aunque la base exista con factor 1 en el mismo producto, y conserva el
    producto afectado.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "salida_cerrada")
    choose_operation_product(app, catalog.product)
    choose_operation_presentation(app, catalog)
    operation_number_input(app, CLOSED_QUANTITY_LABEL).set_value(1)
    operation_text_input(app, REASON_FIELD_LABEL).set_value("Venta")

    submit_operation(app)

    payload = manual_proposals_of(app)[0].payload
    base = next(
        presentation
        for presentation in presentations.list_by_product(
            catalog.product.id
        )
        if presentation.factor == 1
    )

    assert not app.exception
    assert isinstance(payload, ClosedExitPayload)
    assert payload.product_id == catalog.product.id
    assert payload.presentation_id == catalog.box.id
    assert payload.presentation_id != base.id


def test_price_update_presentation_selector_keeps_the_base_presentation(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que el filtro de entrada cerrada no afecta a otros formularios.

    El cambio de precio conserva el selector compartido con la etiqueta
    «Presentación» y sigue ofreciendo la presentación base del producto.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "cambio_precio")
    choose_operation_product(app, catalog.product)

    selector = app.selectbox(
        key=PRESENTATION_SELECTOR_KEY_TEMPLATE.format(
            product_id=catalog.product.id
        )
    )

    assert not app.exception
    assert selector.label == "Presentación"
    assert f"{catalog.box.name} (equivalencia {catalog.box.factor})" in (
        selector.options
    )
    assert "botella (equivalencia 1)" in selector.options


def test_the_equivalencia_is_sent_as_the_unchanged_factor(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que la etiqueta de equivalencia conserva el payload (RF-07).

    Informar 6 en «Equivalencia (unidades base)» sigue construyendo el mismo
    `PresentationRegistrationPayload` con `factor=6`: solo cambia el texto
    visible, no el contrato de la propuesta.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "alta_presentacion")
    choose_operation_product(app, catalog.product)
    operation_text_input(app, PRESENTATION_NAME_FIELD_LABEL).set_value(
        "Fardo x 6"
    )
    operation_number_input(app, FACTOR_FIELD_LABEL).set_value(6)
    operation_text_input(app, PRESENTATION_PRICE_LABEL).set_value("7500.00")

    submit_operation(app)

    payload = manual_proposals_of(app)[0].payload

    assert not app.exception
    assert payload == PresentationRegistrationPayload(
        product_id=catalog.product.id,
        name="Fardo x 6",
        factor=6,
        sale_price=Decimal("7500.00"),
    )


@pytest.mark.parametrize(
    "action", tuple(value for value, _ in OPERATION_ACTION_LABELS)
)
def test_sending_each_form_creates_one_pending_proposal(
    connection: sqlite3.Connection,
    catalog: Catalog,
    action: str,
) -> None:
    """
    Verifica la creación de una propuesta por formulario enviado (C11, C12).

    Cada uno de los diez formularios válidos crea exactamente una propuesta
    pendiente en la colección de la sesión y no altera ninguna tabla ni el
    contador de cambios de SQLite: enviar un formulario nunca escribe.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, action)
    if action != "alta_producto":
        choose_operation_product(app, catalog.product)
    fill_operation_form(app, action, catalog)
    before = snapshot(connection)
    changes_before = connection.total_changes

    submit_operation(app)

    proposals = manual_proposals_of(app)
    messages = [success.value for success in app.success]

    assert not app.exception
    assert len(proposals) == 1
    assert proposals[0].action.value == action
    assert proposals[0].payload.action == action
    assert proposals[0].status is ProposalStatus.PENDING
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before
    assert len(messages) == 1
    assert "No se escribió ningún cambio" in messages[0]


def test_a_rerun_without_submission_does_not_create_proposals(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que un rerun sin envío no crea propuestas (C11).

    Completar los campos y volver a ejecutar la aplicación sin enviar el
    formulario no agrega ninguna propuesta a la colección ni escribe.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "entrada_suelta")
    choose_operation_product(app, catalog.product)
    fill_operation_form(app, "entrada_suelta", catalog)
    before = snapshot(connection)

    app.run()
    app.run()

    assert not app.exception
    assert manual_proposals_of(app) == []
    assert len(app.success) == 0
    assert snapshot(connection) == before


def test_two_deliberate_submissions_create_two_proposals(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que dos envíos iguales no se deduplican (C11, T049).

    Las propuestas no tienen identificador y la interfaz no las deduplica por
    igualdad: enviar dos veces el mismo payload produce dos propuestas
    pendientes, en el orden en que se crearon.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "entrada_suelta")
    choose_operation_product(app, catalog.product)
    fill_operation_form(app, "entrada_suelta", catalog)
    submit_operation(app)
    fill_operation_form(app, "entrada_suelta", catalog)
    submit_operation(app)

    proposals = manual_proposals_of(app)

    assert not app.exception
    assert len(proposals) == 2
    assert [proposal.action.value for proposal in proposals] == [
        "entrada_suelta",
        "entrada_suelta",
    ]
    assert proposals[0].payload == proposals[1].payload


def test_manual_and_assistant_proposals_are_concatenated_without_dedup(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la concatenación de ambas colecciones sin deduplicar (T049).

    Una propuesta del asistente y una propuesta manual con el mismo payload se
    conservan por separado y la vista las muestra ambas, en dos colecciones
    ordenadas que solo se concatenan.
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
    assistant_proposal = assistant.history[-1].state["pending_proposal"]

    app.radio[0].set_value("Operaciones").run()
    choose_operation(app, "entrada_suelta")
    choose_operation_product(app, catalog.product)
    operation_number_input(app, LOOSE_QUANTITY_LABEL).set_value(3)
    operation_text_input(app, REASON_FIELD_LABEL).set_value("Compra")
    submit_operation(app)

    manual = manual_proposals_of(app)
    app.radio[0].set_value("Propuestas pendientes").run()
    rendered = rendered_markdown(app)

    assert not app.exception
    assert assistant_proposal is not None
    assert len(manual) == 1
    assert manual[0].payload == assistant_proposal.payload
    assert rendered.count("Acción: Entrada de unidades sueltas") == 2
    assert "1. Acción" in rendered
    assert "2. Acción" in rendered


@pytest.mark.parametrize(
    ("action", "expected"),
    (
        ("alta_producto", "El campo «SKU» es obligatorio."),
        ("modificacion_producto", UPDATE_WITHOUT_CHANGE_MESSAGE),
        ("alta_presentacion", "debe ser mayor que cero"),
        (
            "entrada_suelta",
            "El campo «Cantidad de unidades sueltas» debe ser mayor que "
            "cero.",
        ),
        ("salida_suelta", "El campo «Motivo» es obligatorio."),
        ("cambio_precio", "importe decimal válido"),
    ),
)
def test_an_invalid_form_shows_a_validation_without_a_proposal(
    connection: sqlite3.Connection,
    catalog: Catalog,
    action: str,
    expected: str,
) -> None:
    """
    Verifica la validación explícita de los formularios (C21, T049, T055).

    Un formulario inválido muestra el motivo como validación —no como rechazo
    del servicio ni como confirmación—, no crea ninguna propuesta y no escribe:
    la validación de los campos del payload es previa y explícita.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, action)
    if action != "alta_producto":
        choose_operation_product(app, catalog.product)
    if action == "alta_producto":
        operation_text_input(app, NAME_FIELD_LABEL).set_value("Producto")
        operation_text_input(app, BASE_UNIT_FIELD_LABEL).set_value("unidad")
        operation_text_input(app, BASE_PRICE_FIELD_LABEL).set_value("10")
    elif action == "alta_presentacion":
        operation_text_input(app, PRESENTATION_NAME_FIELD_LABEL).set_value(
            "Caja"
        )
        operation_number_input(app, FACTOR_FIELD_LABEL).set_value(2)
        operation_text_input(app, PRESENTATION_PRICE_LABEL).set_value("0")
    elif action == "entrada_suelta":
        operation_text_input(app, REASON_FIELD_LABEL).set_value("Compra")
    elif action == "salida_suelta":
        operation_number_input(app, LOOSE_QUANTITY_LABEL).set_value(1)
    elif action == "cambio_precio":
        choose_operation_presentation(app, catalog)
        operation_text_input(app, NEW_PRICE_LABEL).set_value("10,50")
    before = snapshot(connection)

    submit_operation(app)

    warnings = [warning.value for warning in app.warning]

    assert not app.exception
    assert any(expected in value for value in warnings)
    assert app.error == []
    assert app.success == []
    assert manual_proposals_of(app) == []
    assert snapshot(connection) == before


def test_operation_amounts_are_exact_decimals() -> None:
    """
    Verifica que los importes se convierten con `Decimal` exacto (T049).

    La conversión parte del texto, conserva la precisión informada y rechaza
    los valores no decimales, los precios no positivos y los costos negativos,
    sin pasar por `float`.
    """
    assert parse_money("Precio", "10.005") == Decimal("10.005")
    assert str(parse_money("Precio", "10.005")) == "10.005"
    assert parse_optional_money("Costo", " 0 ") == Decimal("0")
    assert parse_optional_money("Costo", "   ") is None

    with pytest.raises(OperationValidationError):
        parse_money("Precio", "abc")
    with pytest.raises(OperationValidationError):
        parse_money("Precio", "0")
    with pytest.raises(OperationValidationError):
        parse_optional_money("Costo", "-0.5")


def test_a_product_registration_keeps_the_exact_price(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica la precisión monetaria del alta de producto (T049).

    El precio se informa con tres decimales y la propuesta conserva el
    `Decimal` exacto, sin redondearlo ni convertirlo a `float`.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    operation_text_input(app, SKU_FIELD_LABEL).set_value("SKU-DECIMAL")
    operation_text_input(app, NAME_FIELD_LABEL).set_value("Producto decimal")
    operation_text_input(app, BASE_UNIT_FIELD_LABEL).set_value("unidad")
    operation_text_input(app, BASE_PRICE_FIELD_LABEL).set_value("10.005")

    submit_operation(app)

    payload = manual_proposals_of(app)[0].payload

    assert not app.exception
    assert isinstance(payload.base_sale_price, Decimal)
    assert payload.base_sale_price == Decimal("10.005")
    assert payload.sku == "SKU-DECIMAL"
    assert payload.name == "Producto decimal"
    assert payload.base_unit == "unidad"
    assert payload.presentations == ()


def test_a_product_registration_admits_an_initial_presentation(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica la presentación adicional opcional del alta de producto (T049).

    El bloque opcional del formulario declara la presentación adicional con su
    nombre, su equivalencia, su precio exacto y su stock cerrado inicial; sin
    esos datos, el payload no declara ninguna presentación adicional.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    operation_text_input(app, SKU_FIELD_LABEL).set_value("SKU-INICIAL")
    operation_text_input(app, NAME_FIELD_LABEL).set_value("Producto inicial")
    operation_text_input(app, BASE_UNIT_FIELD_LABEL).set_value("unidad")
    operation_text_input(app, BASE_PRICE_FIELD_LABEL).set_value("10")
    operation_text_input(app, INITIAL_PRESENTATION_NAME_LABEL).set_value(
        "Caja x6"
    )
    operation_number_input(app, FACTOR_FIELD_LABEL).set_value(6)
    operation_text_input(app, PRESENTATION_PRICE_LABEL).set_value("1200.75")
    operation_number_input(app, INITIAL_CLOSED_STOCK_LABEL).set_value(3)

    submit_operation(app)

    payload = manual_proposals_of(app)[0].payload
    presentation = payload.presentations[0]

    assert not app.exception
    assert len(payload.presentations) == 1
    assert presentation.name == "Caja x6"
    assert presentation.factor == 6
    assert presentation.sale_price == Decimal("1200.75")
    assert presentation.closed_stock == 3


def test_the_price_update_form_builds_the_new_exact_price(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica el payload del cambio de precio (T049, RF-08).

    El payload identifica la presentación elegida y conserva el precio nuevo
    exacto; no declara el producto ni escribe el historial de precios.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "cambio_precio")
    choose_operation_product(app, catalog.product)
    choose_operation_presentation(app, catalog)
    operation_text_input(app, NEW_PRICE_LABEL).set_value("110.555")
    before = snapshot(connection)

    submit_operation(app)

    payload = manual_proposals_of(app)[0].payload

    assert not app.exception
    assert payload.presentation_id == catalog.box.id
    assert payload.new_price == Decimal("110.555")
    assert snapshot(connection) == before


def test_the_compensation_form_only_builds_the_payload(
    connection: sqlite3.Connection,
    catalog: Catalog,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica el alcance de la compensación en la interfaz (T049, T054).

    El formulario solo construye el payload y muestra la cantidad compensable
    que recibe de la lectura pública: no la calcula, no afirma que la
    compensación sea posible y solo ofrece movimientos de los tipos que admiten
    compensación en el MVP.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "compensacion")
    choose_operation_product(app, catalog.product)

    selector = app.selectbox(
        key=MOVEMENT_SELECTOR_KEY_TEMPLATE.format(
            product_id=catalog.product.id
        )
    )
    captions = [caption.value for caption in app.caption]

    assert not app.exception
    assert selector.label == MOVEMENT_SELECTOR_LABEL
    assert selector.options[0] == NO_SELECTION_LABEL
    assert len(selector.options) == 3
    assert not any(
        "Apertura" in option
        or "Compensación" in option
        or "Salida cerrada" in option
        for option in selector.options
    )
    assert COMPENSATION_NOTE in captions
    assert "no la calcula" in COMPENSATION_NOTE

    choose_operation_movement(app, catalog)
    operation_number_input(app, COMPENSATION_QUANTITY_LABEL).set_value(1)
    operation_text_input(app, REASON_FIELD_LABEL).set_value("Ajuste")

    submit_operation(app)

    errors = [error.value for error in app.error]

    assert not app.exception, errors
    assert not errors, errors
    payload = manual_proposals_of(app)[0].payload
    compensable = [
        movement
        for movement in movements.list_by_product(catalog.product.id)
        if movement.kind
        in ("entrada_cerrada", "entrada_suelta", "salida_suelta")
    ]

    assert not app.exception
    assert len(compensable) == 2
    assert payload.movement_id == compensable[0].id
    assert payload.quantity == 1
    assert payload.reason == "Ajuste"


def test_operations_reports_an_empty_catalog_without_inventing_data(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica el estado de catálogo vacío de Operaciones (C4, T049).

    El alta de producto sigue disponible sin catálogo; las demás acciones
    informan que no hay datos para prepararlas y no muestran ningún formulario
    ni producto inventado.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)

    assert not app.exception
    assert any(button.label == SUBMIT_LABEL for button in app.button)
    assert len(app.selectbox) == 1

    choose_operation(app, "entrada_suelta")

    infos = [info.value for info in app.info]

    assert not app.exception
    assert infos == [
        EMPTY_OPERATIONS_CATALOG_TEMPLATE.format(
            action="Entrada de unidades sueltas"
        )
    ]
    assert not any(button.label == SUBMIT_LABEL for button in app.button)


def test_the_compensation_of_a_product_without_movements_is_informed(
    connection: sqlite3.Connection,
    product_service: ProductService,
) -> None:
    """
    Verifica el estado de un producto sin movimientos compensables (C4).

    Un producto recién registrado no tiene movimientos compensables: la vista
    lo informa y no ofrece un formulario de compensación.
    """
    stored = zero_stock_product(
        product_service,
        sku="SKU-SIN-MOV",
        name="Sin movimientos",
    )
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "compensacion")
    choose_operation_product(app, stored)

    infos = [info.value for info in app.info]

    assert not app.exception
    assert infos == [EMPTY_OPERATIONS_MOVEMENTS_MESSAGE]
    assert not any(button.label == SUBMIT_LABEL for button in app.button)


# --- Compensación condicional por lectura pública (T054, RF-09) ---


class UnavailableCompensationReadService:
    """
    Servicio que simula una lectura de compensabilidad no disponible (T054).

    El resto de las operaciones delega en el servicio real; solo la lectura
    pública de compensación falla, para verificar que la interfaz no afirma una
    cantidad compensable cuando no puede leerla.
    """

    def __init__(self, service: MovementService) -> None:
        """Envuelve el servicio real y conserva la lectura a fallar."""
        self._service = service

    def get_compensation_availability(
        self,
        product_id: int,
        movement_id: int,
    ) -> Any:
        """Induce el fallo de la lectura pública de compensabilidad."""
        raise RecordNotFoundError(
            "No se pudo leer la compensabilidad del movimiento "
            f"{movement_id} del producto {product_id}."
        )

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra operación en el servicio real."""
        return getattr(self._service, name)


class FixedCompensationReadService:
    """
    Servicio que devuelve una cantidad compensable fija (T054).

    Registra las llamadas a la lectura pública para comprobar que la interfaz
    usa ese valor y no recalcula la cantidad a partir de los movimientos.
    """

    def __init__(self, service: MovementService, quantity: int) -> None:
        """Envuelve el servicio real y conserva la cantidad a devolver."""
        self._service = service
        self._quantity = quantity
        self.calls: list[tuple[int, int]] = []

    def get_compensation_availability(
        self,
        product_id: int,
        movement_id: int,
    ) -> Any:
        """Registra la consulta y devuelve la cantidad fija."""
        self.calls.append((product_id, movement_id))

        return SimpleNamespace(
            compensable_quantity=self._quantity,
            available_stock=0,
        )

    def __getattr__(self, name: str) -> Any:
        """Delega cualquier otra operación en el servicio real."""
        return getattr(self._service, name)


def product_with_closed_entry(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
    *,
    sku: str,
    quantity: int,
) -> tuple[ProductRecord, PresentationRecord, MovementRecord]:
    """
    Registra un producto con una única entrada cerrada real (T054).

    El producto no tiene stock inicial ni presenta movimientos de stock
    inicial, de modo que la entrada cerrada es el único movimiento compensable
    y su cantidad compensable es exactamente la indicada.
    """
    stored = product_service.register(
        ProductRegistration(
            sku=sku,
            name=f"Producto {sku}",
            base_unit="unidad",
            base_sale_price=Decimal("100"),
            minimum_stock=0,
            initial_loose_units=0,
            presentations=(
                InitialClosedStock(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("600"),
                    closed_stock=0,
                ),
            ),
        )
    )
    _, box = presentations.list_by_product(stored.id)
    entry = movement_service.register_closed_entry(
        stored.id,
        presentation_id=box.id,
        quantity=quantity,
        reason="Compra",
    )

    return stored, box, entry


def select_compensation_movement(
    app: AppTest,
    product_id: int,
) -> None:
    """Elige el primer movimiento compensable del producto (T054)."""
    selector = app.selectbox(
        key=MOVEMENT_SELECTOR_KEY_TEMPLATE.format(product_id=product_id)
    )
    selector.set_value(selector.options[1]).run()


def compensation_captions(app: AppTest) -> list[str]:
    """Devuelve los textos de ayuda de la sección de compensación (T054)."""
    return [caption.value for caption in app.caption]


def test_compensation_shows_the_quantity_of_the_public_read(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """
    Verifica una entrada cerrada con cantidad compensable pendiente (T054).

    La cantidad que muestra el formulario procede de la lectura pública del
    servicio de movimientos, no de un cálculo de la vista.
    """
    stored, _, _ = product_with_closed_entry(
        product_service,
        presentations,
        movement_service,
        sku="SKU-T054-PEND",
        quantity=4,
    )
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "compensacion")
    choose_operation_product(app, stored)
    select_compensation_movement(app, stored.id)

    captions = compensation_captions(app)

    assert not app.exception
    assert COMPENSABLE_QUANTITY_TEMPLATE.format(quantity=4) in captions
    assert COMPENSATION_NOTE in captions


def test_compensation_shows_zero_when_no_compensation_is_pending(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """
    Verifica una entrada cerrada sin cantidad compensable pendiente (T054).

    La salida cerrada consume toda la entrada; la lectura pública devuelve cero
    y la interfaz muestra ese cero real, sin inventar una cantidad pendiente.
    """
    stored, box, _ = product_with_closed_entry(
        product_service,
        presentations,
        movement_service,
        sku="SKU-T054-CERO",
        quantity=3,
    )
    movement_service.register_closed_exit(
        stored.id,
        presentation_id=box.id,
        quantity=3,
        reason="Venta",
    )
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "compensacion")
    choose_operation_product(app, stored)
    select_compensation_movement(app, stored.id)

    captions = compensation_captions(app)

    assert not app.exception
    assert COMPENSABLE_QUANTITY_TEMPLATE.format(quantity=0) in captions


def test_compensation_does_not_affirm_quantity_without_the_public_read(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """
    Verifica una lectura pública no disponible (T054, D-D).

    Si la lectura de compensabilidad falla, el formulario informa el motivo y
    no muestra ni afirma una cantidad compensable.
    """
    stored, _, _ = product_with_closed_entry(
        product_service,
        presentations,
        movement_service,
        sku="SKU-T054-SIN-LECTURA",
        quantity=2,
    )
    assistant = make_assistant(connection)
    assistant.session.movement_service = UnavailableCompensationReadService(
        assistant.session.movement_service
    )
    app = app_for(assistant)

    open_operations_section(app)
    choose_operation(app, "compensacion")
    choose_operation_product(app, stored)
    select_compensation_movement(app, stored.id)

    captions = compensation_captions(app)

    assert not app.exception
    assert COMPENSATION_UNAVAILABLE_MESSAGE in captions
    assert not any(
        caption.startswith("Cantidad compensable pendiente:")
        for caption in captions
    )


def test_compensation_uses_the_public_read_instead_of_recalculating(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """
    Verifica que la interfaz no recalcula la cantidad compensable (T054).

    El servicio se sustituye por uno que devuelve una cantidad distinta de la
    que resultaría de los movimientos y se comprueba que la interfaz muestra
    ese valor y que consultó la lectura pública una sola vez.
    """
    stored, _, entry = product_with_closed_entry(
        product_service,
        presentations,
        movement_service,
        sku="SKU-T054-LECTURA",
        quantity=4,
    )
    assistant = make_assistant(connection)
    read_service = FixedCompensationReadService(
        assistant.session.movement_service,
        41,
    )
    assistant.session.movement_service = read_service
    app = app_for(assistant)

    open_operations_section(app)
    choose_operation(app, "compensacion")
    choose_operation_product(app, stored)
    select_compensation_movement(app, stored.id)

    captions = compensation_captions(app)

    assert not app.exception
    assert COMPENSABLE_QUANTITY_TEMPLATE.format(quantity=41) in captions
    assert COMPENSABLE_QUANTITY_TEMPLATE.format(quantity=4) not in captions
    assert read_service.calls == [(stored.id, entry.id)]


def test_reading_the_compensable_quantity_does_not_write(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """
    Verifica que la consulta de compensabilidad no escribe (T054).

    Mostrar el formulario y consultar la cantidad compensable no altera ninguna
    tabla ni el contador de cambios de SQLite.
    """
    stored, _, _ = product_with_closed_entry(
        product_service,
        presentations,
        movement_service,
        sku="SKU-T054-LECTURA-SIN-ESCRITURA",
        quantity=4,
    )
    before = snapshot(connection)
    changes_before = connection.total_changes
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    choose_operation(app, "compensacion")
    choose_operation_product(app, stored)
    select_compensation_movement(app, stored.id)

    assert not app.exception
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_get_compensation_availability_reads_without_writing(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """
    Verifica la lectura pública de compensabilidad (T054, C18).

    La lectura reutiliza la regla de compensación vigente, devuelve la cantidad
    pendiente y el stock afectado, y no altera el inventario.
    """
    stored, _, entry = product_with_closed_entry(
        product_service,
        presentations,
        movement_service,
        sku="SKU-T054-SERVICIO",
        quantity=5,
    )
    before = snapshot(connection)
    changes_before = connection.total_changes

    availability = movement_service.get_compensation_availability(
        stored.id,
        entry.id,
    )

    assert availability.compensable_quantity == 5
    assert availability.available_stock == 5
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_the_compensation_ui_delegates_to_the_public_read() -> None:
    """
    Verifica los límites estructurales de la compensación (T054, C18).

    `main.py` consulta la lectura pública de compensabilidad y no usa
    `register_compensation`; la vista solo recibe la cantidad ya leída y no
    recalcula contadores de compensación ni de consumo.
    """
    main_source = APP_PATH.read_text(encoding="utf-8")
    operations_source = OPERATIONS_SECTION_PATH.read_text(encoding="utf-8")
    main_tree = ast.parse(main_source)
    operations_tree = ast.parse(operations_source)
    main_calls = {
        node.func.attr
        for node in ast.walk(main_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    operations_calls = {
        node.func.attr
        for node in ast.walk(operations_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "get_compensation_availability" in main_calls
    assert "register_compensation" not in main_calls
    assert "get_compensation_availability" not in operations_calls
    assert "register_compensation" not in operations_calls
    assert "compensated_quantity" not in operations_source
    assert "consumed_quantity" not in operations_source


def test_operations_works_without_the_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Verifica Operaciones y sus propuestas sin modelo configurado (C2, T049).

    La sección crea propuestas manuales aunque el asistente no esté
    disponible, y la sección de propuestas pendientes las muestra sin la
    conversación.
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
        stored = zero_stock_product(
            seed_products,
            sku="SKU-OP",
            name="Operaciones sin modelo",
        )
    finally:
        seed_connection.close()
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)

    app.run()

    session = app.session_state.get(SESSION_KEY)

    try:
        assert len(app.error) == 1
        assert MISSING_KEY_MESSAGE in app.error[0].value

        app.radio[0].set_value("Operaciones").run()
        choose_operation(app, "entrada_suelta")
        choose_operation_product(app, stored)
        operation_number_input(app, LOOSE_QUANTITY_LABEL).set_value(2)
        operation_text_input(app, REASON_FIELD_LABEL).set_value(
            "Compra sin modelo"
        )
        submit_operation(app)

        assert not app.exception
        assert len(app.error) == 0
        actions = [
            proposal.action.value for proposal in manual_proposals_of(app)
        ]

        assert actions == ["entrada_suelta"]

        app.radio[0].set_value("Propuestas pendientes").run()

        assert "Acción: Entrada de unidades sueltas" in rendered_markdown(app)
    finally:
        if session is not None:
            session.connection.close()


def test_the_operations_view_only_builds_payloads_without_writing() -> None:
    """
    Verifica los límites arquitectónicos de la vista (C12, T049).

    La sección no importa los servicios, el dominio ni la persistencia, no usa
    `st.session_state`, no contiene el punto de confirmación ni invoca métodos
    de escritura o de lectura de servicios.
    """
    source = OPERATIONS_SECTION_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    assert OPERATIONS_SECTION_PATH.is_file()
    assert "st.session_state" not in source
    assert "proposal_service" not in source
    for marker in (
        ".confirm(",
        ".create(",
        ".register(",
        ".edit(",
        ".commit(",
        ".execute(",
    ):
        assert marker not in source, marker

    for module in imported_modules(tree):
        for prefix in FORBIDDEN_SECTION_IMPORTS:
            assert not (
                module == prefix or module.startswith(f"{prefix}.")
            ), f"operations.py importa {module}"

    assert not called_names(tree) & {
        "register",
        "create",
        "edit",
        "confirm",
        "list_products",
        "get_product_presentations",
        "get_movement_history",
        "get_product_stock",
        "build_session",
        "open_connection",
        "transaction",
        "get_llm",
        "build_workflow",
    }


def test_the_interface_keeps_a_single_confirmation_point() -> None:
    """
    Verifica que la confirmación sigue en un único punto (C12, T049).

    Ninguna vista nueva agrega un punto de confirmación: en `app/ui/**` sigue
    habiendo una sola aparición de `.confirm(` y está en `main.py`.
    """
    occurrences = {
        path.name: path.read_text(encoding="utf-8").count(".confirm(")
        for path in ui_sources()
    }

    assert sum(occurrences.values()) == 1
    assert occurrences["main.py"] == 1


# --- Revisión legible de las propuestas pendientes (T050, RF-09) ---


T050_REVIEW_PAYLOADS: tuple[tuple[str, Any], ...] = (
    (
        "alta_producto",
        ProductRegistrationPayload(
            sku="SKU-1",
            name="Producto",
            base_unit="botella",
            base_sale_price=Decimal("150.50"),
            minimum_stock=0,
            initial_loose_units=0,
        ),
    ),
    (
        "modificacion_producto",
        ProductUpdatePayload(product_id=3, name="Producto nuevo"),
    ),
    (
        "alta_presentacion",
        PresentationRegistrationPayload(
            product_id=3,
            name="Caja x12",
            factor=12,
            sale_price=Decimal("1200.75"),
        ),
    ),
    (
        "entrada_suelta",
        LooseEntryPayload(
            product_id=3,
            quantity=3,
            reason="Compra",
            cost=Decimal("10.25"),
        ),
    ),
    (
        "entrada_cerrada",
        ClosedEntryPayload(
            product_id=3,
            presentation_id=2,
            quantity=2,
            reason="Compra",
        ),
    ),
    (
        "salida_suelta",
        LooseExitPayload(product_id=3, quantity=1, reason="Venta"),
    ),
    (
        "salida_cerrada",
        ClosedExitPayload(
            product_id=3,
            presentation_id=2,
            quantity=1,
            reason="Venta",
        ),
    ),
    (
        "apertura",
        AperturePayload(
            product_id=3,
            presentation_id=2,
            quantity=1,
            reason="Apertura",
        ),
    ),
    (
        "compensacion",
        CompensationPayload(
            product_id=3,
            movement_id=7,
            quantity=1,
            reason="Ajuste",
        ),
    ),
    (
        "cambio_precio",
        PriceUpdatePayload(presentation_id=2, new_price=Decimal("110.5")),
    ),
)
"""Un payload por cada una de las diez acciones, con valores legibles (T050).

Se declaran como tupla literal —no desde las constantes de la aplicación— para
que la prueba verifique el alcance de C13 y no una comprobación tautológica.
"""

T050_FIELD_MARKERS: dict[str, str] = {
    "alta_producto": "SKU: SKU-1",
    "modificacion_producto": "Producto (id 3)",
    "alta_presentacion": "Equivalencia: 12",
    "entrada_suelta": "Costo: 10.25",
    "entrada_cerrada": "Presentación (id 2)",
    "salida_suelta": "Motivo: Venta",
    "salida_cerrada": "Presentación (id 2)",
    "apertura": "Motivo: Apertura",
    "compensacion": "Movimiento (id 7)",
    "cambio_precio": "precio nuevo: 110.50",
}
"""Campo legible que cada acción debe mostrar en su revisión (T050)."""

T050_PRICE_VIEW: PresentationPriceView = PresentationPriceView(
    id=2,
    name="Caja x6",
    factor=6,
    sale_price=Decimal("600"),
)
"""Presentación ya resuelta que usa la revisión del cambio de precio (T050)."""


def proposal_review(
    payload: Any,
    presentation: PresentationPriceView | None = None,
) -> ProposalReview:
    """Construye la revisión de una propuesta a partir de su payload (T050)."""
    proposal = Proposal(
        action=ProposalAction(payload.action),
        payload=payload,
    )

    return ProposalReview(
        proposal=proposal,
        key=id(proposal),
        presentation=presentation,
    )


@pytest.mark.parametrize(
    ("action", "payload"),
    T050_REVIEW_PAYLOADS,
    ids=[action for action, _ in T050_REVIEW_PAYLOADS],
)
def test_each_action_shows_its_readable_label_and_fields(
    action: str,
    payload: Any,
) -> None:
    """
    Verifica la revisión legible de las diez acciones (C13, T050).

    Cada acción se presenta con su nombre legible y sus campos etiquetados, sin
    el volcado genérico del payload ni valores `None`.
    """
    presentation = (
        T050_PRICE_VIEW if action == "cambio_precio" else None
    )

    described = "\n".join(
        describe_proposal_review(proposal_review(payload, presentation))
    )

    assert dict(OPERATION_ACTION_LABELS)[action] in described
    assert T050_FIELD_MARKERS[action] in described
    assert "None" not in described


def test_the_readable_labels_cover_the_ten_actions() -> None:
    """
    Verifica que las etiquetas legibles cubren las diez acciones (C13, T050).

    La primera línea de cada revisión nombra la acción con su etiqueta de la
    sección Operaciones y con su identificador, sin depender del valor crudo.
    """
    expected = dict(OPERATION_ACTION_LABELS)
    headlines = [
        describe_proposal_review(proposal_review(payload))[0]
        for _, payload in T050_REVIEW_PAYLOADS
    ]

    assert [action for action, _ in T050_REVIEW_PAYLOADS] == list(expected)
    for headline, (action, _) in zip(headlines, T050_REVIEW_PAYLOADS):
        assert expected[action] in headline
        assert action in headline


def test_a_price_update_shows_the_presentation_and_both_prices() -> None:
    """
    Verifica el detalle del cambio de precio en la revisión (C13, T050).

    La revisión muestra la presentación afectada con su nombre, su id y su
    factor, el precio vigente y el precio nuevo, con el mismo detalle que la
    conversación.
    """
    review = proposal_review(
        PriceUpdatePayload(presentation_id=2, new_price=Decimal("700")),
        T050_PRICE_VIEW,
    )

    described = "\n".join(describe_proposal_review(review))

    assert "presentación: Caja x6 (id 2, equivalencia 6)" in described
    assert "precio vigente: 600.00" in described
    assert "precio nuevo: 700.00" in described
    detail = describe_price_update_fields(
        review.proposal, review.presentation
    )
    assert detail == (
        "presentación: Caja x6 (id 2, equivalencia 6)",
        "precio vigente: 600.00",
        "precio nuevo: 700.00",
    )


def test_a_missing_presentation_shows_the_reason_without_a_price() -> None:
    """
    Verifica la presentación inexistente en la revisión (C4, T050).

    Se muestra el motivo explícito, no aparece `None` y no se inventa un precio
    vigente: la línea del precio vigente no se dibuja.
    """
    review = proposal_review(
        PriceUpdatePayload(presentation_id=99, new_price=Decimal("700"))
    )

    described = "\n".join(describe_proposal_review(review))

    assert "presentación: no existe la presentación con id 99" in described
    assert "precio vigente" not in described
    assert "precio nuevo: 700.00" in described
    assert "None" not in described


def test_absent_optional_values_are_informed_as_absent() -> None:
    """
    Verifica los valores ausentes de la revisión (C4, T050).

    Un costo `None`, un nombre o un stock mínimo no informados y una
    presentación base ausente se muestran como `no informado`, nunca como
    `None` ni como cero.
    """
    reviews = (
        proposal_review(
            LooseEntryPayload(product_id=1, quantity=1, reason="Compra")
        ),
        proposal_review(
            ProductUpdatePayload(product_id=1, minimum_stock=25)
        ),
        proposal_review(
            ProductRegistrationPayload(
                sku="SKU-1",
                name="Producto",
                base_unit="botella",
                base_sale_price=Decimal("10"),
            )
        ),
    )

    described = "\n".join(
        line
        for review in reviews
        for line in describe_proposal_review(review)
    )

    assert f"Costo: {ABSENT_MARKER}" in described
    assert f"Nombre nuevo: {ABSENT_MARKER}" in described
    assert (
        f"Nombre visible de la unidad base: {ABSENT_MARKER}" in described
    )
    assert "None" not in described


def test_real_zeros_are_not_presented_as_absent() -> None:
    """
    Verifica que los ceros reales se conservan en la revisión (C4, T050).

    Un stock mínimo cero, un stock cerrado inicial cero y un costo cero se
    muestran como cero y no como un valor ausente.
    """
    reviews = (
        proposal_review(
            ProductRegistrationPayload(
                sku="SKU-1",
                name="Producto",
                base_unit="botella",
                base_sale_price=Decimal("10"),
                minimum_stock=0,
                initial_loose_units=0,
                presentations=(
                    InitialPresentationPayload(
                        name="Caja x6",
                        factor=6,
                        sale_price=Decimal("60"),
                        closed_stock=0,
                    ),
                ),
            )
        ),
        proposal_review(
            LooseEntryPayload(
                product_id=1,
                quantity=1,
                reason="Compra",
                cost=Decimal("0"),
            )
        ),
    )

    described = "\n".join(
        line
        for review in reviews
        for line in describe_proposal_review(review)
    )

    assert "Stock mínimo: 0" in described
    assert "Unidades sueltas iniciales: 0" in described
    assert "stock cerrado 0" in described
    assert "Costo: 0.00" in described


def test_a_product_registration_without_presentations_says_so() -> None:
    """
    Verifica el alta sin presentaciones adicionales en la revisión (C4, T050).

    `presentations=()` se informa con un mensaje explícito y no con un cero que
    podría leerse como una presentación declarada.
    """
    review = proposal_review(
        ProductRegistrationPayload(
            sku="SKU-1",
            name="Producto",
            base_unit="botella",
            base_sale_price=Decimal("10"),
            presentations=(),
        )
    )

    described = "\n".join(describe_proposal_review(review))

    assert NO_INITIAL_PRESENTATIONS_MESSAGE in described
    assert "Presentaciones adicionales: 0" not in described


def test_the_section_shows_only_pending_proposals(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que una propuesta confirmada sale de la sección (C13, T050).

    La propuesta confirmada no se ofrece como pendiente, pero permanece en
    `assistant.history` con su resultado real y la confirmación siguió
    funcionando desde su único punto.
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
    app.radio[0].set_value("Propuestas pendientes").run()

    assert "Acción: Entrada de unidades sueltas" in rendered_markdown(app)

    app.radio[0].set_value(DEFAULT_SECTION_LABEL).run()
    app.button[0].set_value(True).run()

    state = assistant.history[-1].state

    assert state["pending_proposal"].status is ProposalStatus.CONFIRMED
    assert state["execution_result"] is not None

    app.radio[0].set_value("Propuestas pendientes").run()

    assert not app.exception
    assert [info.value for info in app.info] == [EMPTY_PROPOSALS_MESSAGE]
    assert "Acción: Entrada de unidades sueltas" not in rendered_markdown(app)
    assert len(assistant.history) == 1


def test_a_manual_price_proposal_is_reviewed_without_the_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """
    Verifica la revisión de una propuesta manual sin modelo (C2, T050).

    El cambio de precio preparado con el formulario se revisa con el servicio
    de presentaciones de la sesión —sin asistente— y muestra el precio vigente
    y el precio nuevo reales.
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
        stored = register_low_stock_product(
            seed_products,
            sku="SKU-T050",
            name="Producto T050",
            minimum_stock=0,
            loose_units=0,
        )
    finally:
        seed_connection.close()
    monkeypatch.setattr(llm_module, "get_llm", failing_get_llm)
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)

    app.run()

    session = app.session_state.get(SESSION_KEY)

    try:
        assert app.session_state.get(ASSISTANT_KEY) is None

        app.radio[0].set_value("Operaciones").run()
        choose_operation(app, "cambio_precio")
        choose_operation_product(app, stored)
        selector = app.selectbox(
            key=PRESENTATION_SELECTOR_KEY_TEMPLATE.format(
                product_id=stored.id
            )
        )
        selector.set_value(selector.options[1])
        operation_text_input(app, NEW_PRICE_LABEL).set_value("110.5")
        submit_operation(app)

        assert not app.exception
        assert [
            proposal.action.value for proposal in manual_proposals_of(app)
        ] == ["cambio_precio"]

        app.radio[0].set_value("Propuestas pendientes").run()

        rendered = rendered_markdown(app)

        assert not app.exception
        assert "presentación: unidad (id " in rendered
        assert "equivalencia 1)" in rendered
        assert "precio vigente: 10.00" in rendered
        assert "precio nuevo: 110.50" in rendered
    finally:
        if session is not None:
            session.connection.close()


def test_the_review_does_not_write(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que revisar las propuestas no escribe (C12, T050).

    Mostrar la revisión de un cambio de precio —con su precio vigente leído—
    no altera ninguna tabla ni el contador de cambios de SQLite.
    """
    assistant = make_assistant(
        connection, price_call(catalog.box.id, "700")
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Cambia el precio a 700").run()
    before = snapshot(connection)
    changes_before = connection.total_changes

    app.radio[0].set_value("Propuestas pendientes").run()

    assert not app.exception
    assert "precio vigente: 600.00" in rendered_markdown(app)
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


# --- Edición de propuestas pendientes (T051, RF-09) ---


T051_PREFILLED_FIELDS: tuple[tuple[str, str, str, Any], ...] = (
    ("alta_producto", "text_input", "sku", "SKU-1"),
    ("modificacion_producto", "text_input", "name", "Producto nuevo"),
    ("alta_presentacion", "number_input", "factor", 12),
    ("entrada_suelta", "number_input", "quantity", 3),
    ("entrada_cerrada", "number_input", "quantity", 2),
    ("salida_suelta", "text_input", "reason", "Venta"),
    ("salida_cerrada", "number_input", "quantity", 1),
    ("apertura", "text_input", "reason", "Apertura"),
    ("compensacion", "number_input", "quantity", 1),
    ("cambio_precio", "text_input", "new_price", "110.5"),
)
"""Campo prellenado que se comprueba por acción en el formulario (T051)."""


def proposal_for(payload: Any) -> Proposal:
    """Construye una propuesta pendiente a partir de su payload (T051)."""
    return Proposal(action=ProposalAction(payload.action), payload=payload)


def proposal_edit_key(proposal: Proposal, field: str) -> str:
    """Devuelve la clave del campo de edición de una propuesta (T051)."""
    return f"proposal_edit_{id(proposal)}_{field}"


def proposal_edit_presentation_key(
    proposal: Proposal,
    position: int,
    field: str,
) -> str:
    """Devuelve la clave de un campo de presentación adicional (T051)."""
    return f"proposal_edit_{id(proposal)}_presentation_{position}_{field}"


def proposal_edit_submit(app: AppTest, proposal: Proposal) -> None:
    """Envía el formulario de edición de la propuesta indicada (T051)."""
    app.button(key=f"proposal_edit_{id(proposal)}_submit").click().run()


def open_pending_proposals(
    app: AppTest,
    proposals: Sequence[Proposal] | None = None,
) -> None:
    """Ejecuta la aplicación y abre Propuestas pendientes (T051)."""
    if proposals is not None:
        app.session_state[MANUAL_PROPOSALS_KEY] = list(proposals)
    app.run()
    app.radio[0].set_value("Propuestas pendientes").run()


def test_each_action_shows_an_edit_form_with_its_current_values(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica el formulario de edición de las diez acciones (T051, C14).

    Cada propuesta pendiente ofrece un formulario con sus valores actuales, sin
    selector de acción, producto, presentación ni movimiento: la acción y los
    destinos no se pueden cambiar desde la edición.
    """
    proposals = [
        proposal_for(payload) for _, payload in T050_REVIEW_PAYLOADS
    ]
    app = app_for(make_assistant(connection))

    open_pending_proposals(app, proposals)

    assert not app.exception
    assert len(app.selectbox) == 0
    assert [
        button.label
        for button in app.button
        if button.label == EDIT_SUBMIT_LABEL
    ] == [EDIT_SUBMIT_LABEL] * len(proposals)
    assert [
        button.label
        for button in app.button
        if button.label == DISCARD_LABEL
    ] == [DISCARD_LABEL] * len(proposals)
    for action, widget, field, expected in T051_PREFILLED_FIELDS:
        proposal = next(
            proposal
            for proposal in proposals
            if proposal.action.value == action
        )
        element = getattr(app, widget)(key=proposal_edit_key(proposal, field))
        assert element.value == expected, action
    assert [
        expander.label for expander in app.expander
    ] == [
        f"Editar propuesta {index}"
        for index in range(1, len(proposals) + 1)
    ]


def test_presentation_additional_label_is_shared_by_both_surfaces(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica que la presentación adicional usa una única etiqueta (RF-07).

    El alta de producto y la edición de esa misma propuesta muestran el nombre
    de la presentación adicional con la misma etiqueta visible en ambos
    formularios.
    """
    proposal = proposal_for(
        ProductRegistrationPayload(
            sku="SKU-T051",
            name="Producto T051",
            base_unit="unidad",
            base_sale_price=Decimal("150"),
            minimum_stock=0,
            initial_loose_units=0,
            presentations=(
                InitialPresentationPayload(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("60"),
                    closed_stock=2,
                ),
            ),
        )
    )
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    registration_field = operation_text_input(
        app, INITIAL_PRESENTATION_NAME_LABEL
    )

    assert not app.exception
    assert registration_field.label == INITIAL_PRESENTATION_NAME_LABEL

    open_pending_proposals(app, [proposal])
    edition_field = app.text_input(
        key=proposal_edit_presentation_key(proposal, 1, "name")
    )

    assert not app.exception
    assert edition_field.label == registration_field.label
    assert edition_field.label == INITIAL_PRESENTATION_NAME_LABEL


def test_the_edit_form_number_inputs_show_an_example_in_their_help(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica las ayudas de los campos numéricos editables (T051, RF-09).

    Los campos equivalentes de la edición de propuestas muestran en su ayuda un
    ejemplo visible, sin depender de un placeholder que Streamlit no muestra
    para los `number_input` con valor precargado.
    """
    registration = proposal_for(
        ProductRegistrationPayload(
            sku="SKU-T051",
            name="Producto T051",
            base_unit="unidad",
            base_sale_price=Decimal("150"),
            minimum_stock=4,
            initial_loose_units=2,
            presentations=(
                InitialPresentationPayload(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("60"),
                    closed_stock=1,
                ),
            ),
        )
    )
    loose_entry = proposal_for(
        LooseEntryPayload(product_id=3, quantity=2, reason="Compra")
    )
    presentation_registration = proposal_for(
        PresentationRegistrationPayload(
            product_id=3,
            name="Caja x12",
            factor=12,
            sale_price=Decimal("1200.75"),
        )
    )
    app = app_for(make_assistant(connection))

    open_pending_proposals(
        app,
        [registration, presentation_registration, loose_entry],
    )

    assert not app.exception
    assert "Ej.: 6" in app.number_input(
        key=proposal_edit_key(registration, "minimum_stock")
    ).help
    assert "Ej.: 2" in app.number_input(
        key=proposal_edit_key(registration, "initial_loose_units")
    ).help
    assert "Ej.: 6" in app.number_input(
        key=proposal_edit_presentation_key(registration, 1, "factor")
    ).help
    assert "Ej.: 1" in app.number_input(
        key=proposal_edit_presentation_key(registration, 1, "closed_stock")
    ).help
    assert "Ej.: 6" in app.number_input(
        key=proposal_edit_key(presentation_registration, "factor")
    ).help
    assert "Ej.: 2" in app.number_input(
        key=proposal_edit_key(loose_entry, "quantity")
    ).help


def test_a_manual_proposal_is_edited_in_place_without_writing(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la edición de una propuesta manual (T051, C14).

    La propuesta se reemplaza solo en la colección manual, conserva su acción y
    su producto y sigue pendiente; editar dos veces no la duplica ni la pierde
    y no modifica la base ni el contador de cambios de SQLite.
    """
    proposal = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra",
            cost=Decimal("10.25"),
        )
    )
    app = app_for(make_assistant(connection))
    before = snapshot(connection)
    changes_before = connection.total_changes

    open_pending_proposals(app, [proposal])
    app.number_input(
        key=proposal_edit_key(proposal, "quantity")
    ).set_value(5)
    app.text_input(
        key=proposal_edit_key(proposal, "reason")
    ).set_value("Compra editada")
    proposal_edit_submit(app, proposal)

    stored = manual_proposals_of(app)

    assert not app.exception
    assert len(stored) == 1
    edited = stored[0]
    assert edited is not proposal
    assert edited.action is ProposalAction.LOOSE_ENTRY
    assert edited.status is ProposalStatus.PENDING
    assert edited.payload.product_id == catalog.product.id
    assert edited.payload.quantity == 5
    assert edited.payload.reason == "Compra editada"
    assert edited.payload.cost == Decimal("10.25")

    app.text_input(
        key=proposal_edit_key(edited, "reason")
    ).set_value("Compra editada otra vez")
    proposal_edit_submit(app, edited)

    stored_again = manual_proposals_of(app)

    assert not app.exception
    assert len(stored_again) == 1
    assert stored_again[0] is not edited
    assert stored_again[0].payload.reason == "Compra editada otra vez"
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_an_assistant_proposal_is_edited_in_its_turn_without_writing(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la edición de una propuesta del asistente (T051, C14).

    Se reemplaza solo el `pending_proposal` del turno: el historial no crece,
    la propuesta conserva su acción y su producto, sigue pendiente y no se
    ejecuta ni se escribe nada.
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
    before = snapshot(connection)
    changes_before = connection.total_changes

    app.radio[0].set_value("Propuestas pendientes").run()
    app.text_input(
        key=proposal_edit_key(proposal, "reason")
    ).set_value("Compra del asistente editada")
    proposal_edit_submit(app, proposal)

    state = assistant.history[-1].state
    edited = state["pending_proposal"]

    assert not app.exception
    assert len(assistant.history) == 1
    assert edited is not proposal
    assert edited.action is ProposalAction.LOOSE_ENTRY
    assert edited.status is ProposalStatus.PENDING
    assert edited.payload.product_id == catalog.product.id
    assert edited.payload.quantity == 3
    assert edited.payload.reason == "Compra del asistente editada"
    assert state["execution_result"] is None
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_two_identical_manual_proposals_are_edited_independently(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la identidad por objeto entre propuestas idénticas (T051).

    Dos propuestas manuales con el mismo payload tienen claves de formulario
    distintas: editar una no modifica la otra.
    """
    payload = LooseEntryPayload(
        product_id=catalog.product.id,
        quantity=3,
        reason="Compra",
    )
    first = proposal_for(payload)
    second = proposal_for(payload)
    app = app_for(make_assistant(connection))

    assert first == second
    assert first is not second

    open_pending_proposals(app, [first, second])

    assert not app.exception
    assert len(
        [
            button
            for button in app.button
            if button.label == EDIT_SUBMIT_LABEL
        ]
    ) == 2
    assert proposal_edit_key(first, "reason") != proposal_edit_key(
        second, "reason"
    )

    app.text_input(
        key=proposal_edit_key(first, "reason")
    ).set_value("Solo la primera")
    proposal_edit_submit(app, first)

    stored = manual_proposals_of(app)

    assert not app.exception
    assert len(stored) == 2
    assert stored[0].payload.reason == "Solo la primera"
    assert stored[1] is second
    assert stored[1].payload == payload


def test_a_manual_and_an_assistant_proposal_are_independent(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la identidad entre una propuesta manual y una del asistente
    (T051).

    Las dos propuestas comparten payload pero tienen claves distintas: editar
    una no modifica la otra y cada una se reemplaza en su propio origen.
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
    assistant_proposal = assistant.history[-1].state["pending_proposal"]
    manual_proposal = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra",
        )
    )
    app.session_state[MANUAL_PROPOSALS_KEY] = [manual_proposal]

    app.radio[0].set_value("Propuestas pendientes").run()

    assert not app.exception
    assert assistant_proposal.payload == manual_proposal.payload
    assert assistant_proposal is not manual_proposal
    assert proposal_edit_key(
        assistant_proposal, "reason"
    ) != proposal_edit_key(manual_proposal, "reason")

    app.text_input(
        key=proposal_edit_key(manual_proposal, "reason")
    ).set_value("Compra manual editada")
    proposal_edit_submit(app, manual_proposal)

    manual = manual_proposals_of(app)
    assert manual[0].payload.reason == "Compra manual editada"
    assert (
        assistant.history[-1].state["pending_proposal"]
        is assistant_proposal
    )
    assert (
        assistant.history[-1].state["pending_proposal"].payload.reason
        == "Compra"
    )

    current_assistant = assistant.history[-1].state["pending_proposal"]
    app.text_input(
        key=proposal_edit_key(current_assistant, "reason")
    ).set_value("Compra del asistente editada")
    proposal_edit_submit(app, current_assistant)

    assert not app.exception
    assert (
        manual_proposals_of(app)[0].payload.reason == "Compra manual editada"
    )
    assert (
        assistant.history[-1].state["pending_proposal"].payload.reason
        == "Compra del asistente editada"
    )


def test_a_confirmed_proposal_is_not_offered_for_editing(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que una propuesta confirmada no se edite (T051, C14).

    La sección solo reúne propuestas pendientes: una confirmada no muestra
    formulario de edición y la base no cambia al abrir la sección.
    """
    confirmed = Proposal(
        action=ProposalAction.LOOSE_ENTRY,
        payload=LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra",
        ),
        status=ProposalStatus.CONFIRMED,
    )
    app = app_for(make_assistant(connection))
    before = snapshot(connection)
    changes_before = connection.total_changes

    open_pending_proposals(app, [confirmed])

    assert not app.exception
    assert [info.value for info in app.info] == [EMPTY_PROPOSALS_MESSAGE]
    assert not any(
        button.label == EDIT_SUBMIT_LABEL for button in app.button
    )
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_edit_proposal_rejects_a_confirmed_proposal(
    connection: sqlite3.Connection,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifica el rechazo de la edición de una propuesta confirmada (T051, C14).

    La interfaz entrega la propuesta al servicio, que exige el estado
    pendiente: devuelve su motivo y no reemplaza la referencia ni escribe.
    """
    confirmed = Proposal(
        action=ProposalAction.LOOSE_ENTRY,
        payload=LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra",
        ),
        status=ProposalStatus.CONFIRMED,
    )
    manual = [confirmed]
    monkeypatch.setattr("app.ui.main.manual_proposals", lambda: manual)
    session = build_session(connection=connection)
    before = snapshot(connection)
    changes_before = connection.total_changes

    error = edit_proposal(
        None,
        session,
        id(confirmed),
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=5,
            reason="Compra editada",
        ),
    )

    assert error is not None
    assert "confirmada" in error
    assert "pendientes" in error
    assert manual == [confirmed]
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_edit_proposal_reports_a_proposal_that_is_no_longer_available(
    connection: sqlite3.Connection,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifica la clave inexistente al editar una propuesta (T051).

    Si la propuesta se confirmó o desapareció entre el render y el envío, la
    interfaz devuelve un motivo explícito sin modificar nada ni escribir.
    """
    payload = LooseEntryPayload(
        product_id=catalog.product.id,
        quantity=3,
        reason="Compra",
    )
    manual: list[Proposal] = []
    monkeypatch.setattr("app.ui.main.manual_proposals", lambda: manual)
    session = build_session(connection=connection)
    before = snapshot(connection)
    changes_before = connection.total_changes

    assert locate_proposal(None, 999999) is None
    error = edit_proposal(None, session, 999999, payload)

    assert error == PROPOSAL_NOT_AVAILABLE_MESSAGE
    assert manual == []
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_edit_proposal_rejects_a_withdrawn_proposal(
    connection: sqlite3.Connection,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifica el rechazo al editar una propuesta retirada (T051, C14).

    La retirada local elimina la propuesta de su colección de sesión, sin
    asignarle un estado adicional en el dominio. El callback no la reemplaza ni
    modifica el objeto retirado y tampoco escribe inventario o movimientos.
    """
    withdrawn = Proposal(
        action=ProposalAction.LOOSE_ENTRY,
        payload=LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra",
        ),
    )
    original = withdrawn.model_copy(deep=True)
    manual = [withdrawn]
    monkeypatch.setattr("app.ui.main.manual_proposals", lambda: manual)
    session = build_session(connection=connection)
    before = snapshot(connection)
    changes_before = connection.total_changes

    manual.remove(withdrawn)
    error = edit_proposal(
        None,
        session,
        id(withdrawn),
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=5,
            reason="Compra editada",
        ),
    )

    assert error == PROPOSAL_NOT_AVAILABLE_MESSAGE
    assert manual == []
    assert withdrawn == original
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_an_invalid_edit_shows_the_reason_and_keeps_the_proposal(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la validación del formulario de edición (T051, C21, T055).

    Un motivo vacío se muestra como advertencia de validación —no como rechazo
    ni como confirmación—, no se entrega al callback y la propuesta original
    sigue en la sesión sin reemplazarse ni escribirse.
    """
    proposal = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra",
        )
    )
    app = app_for(make_assistant(connection))
    before = snapshot(connection)
    changes_before = connection.total_changes

    open_pending_proposals(app, [proposal])
    app.text_input(key=proposal_edit_key(proposal, "reason")).set_value("")
    proposal_edit_submit(app, proposal)

    assert not app.exception
    assert any(
        "Motivo" in warning.value and "obligatorio" in warning.value
        for warning in app.warning
    )
    assert app.error == []
    assert app.success == []
    assert manual_proposals_of(app)[0] is proposal
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_editing_conserves_absent_values_and_real_zeros(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica los valores opcionales al editar (T051, C4).

    Un costo ausente sigue ausente, un costo cero sigue siendo cero y el stock
    mínimo distingue `None` de `0` al editar el nombre.
    """
    without_cost = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=1,
            reason="Compra",
        )
    )
    zero_cost = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=1,
            reason="Compra",
            cost=Decimal("0"),
        )
    )
    zero_minimum = proposal_for(
        ProductUpdatePayload(
            product_id=catalog.product.id,
            name="Producto",
            minimum_stock=0,
        )
    )
    absent_minimum = proposal_for(
        ProductUpdatePayload(
            product_id=catalog.product.id,
            name="Producto",
        )
    )
    app = app_for(make_assistant(connection))

    open_pending_proposals(
        app,
        [without_cost, zero_cost, zero_minimum, absent_minimum],
    )

    app.text_input(
        key=proposal_edit_key(without_cost, "reason")
    ).set_value("Compra sin costo")
    proposal_edit_submit(app, without_cost)
    app.text_input(
        key=proposal_edit_key(zero_cost, "reason")
    ).set_value("Compra con costo cero")
    proposal_edit_submit(app, zero_cost)
    app.text_input(
        key=proposal_edit_key(zero_minimum, "name")
    ).set_value("Producto cero")
    proposal_edit_submit(app, zero_minimum)
    app.text_input(
        key=proposal_edit_key(absent_minimum, "name")
    ).set_value("Producto ausente")
    proposal_edit_submit(app, absent_minimum)

    stored = manual_proposals_of(app)

    assert not app.exception
    assert stored[0].payload.cost is None
    assert stored[1].payload.cost == Decimal("0")
    assert stored[2].payload.minimum_stock == 0
    assert stored[3].payload.minimum_stock is None


def test_editing_the_initial_presentations_conserves_their_count_and_order(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica la edición de las presentaciones adicionales (T051).

    Se editan los campos de las presentaciones existentes conservando su
    cantidad y su orden; el formulario no permite añadir ni eliminar
    presentaciones.
    """
    proposal = proposal_for(
        ProductRegistrationPayload(
            sku="SKU-T051",
            name="Producto T051",
            base_unit="unidad",
            base_sale_price=Decimal("150"),
            minimum_stock=0,
            initial_loose_units=0,
            presentations=(
                InitialPresentationPayload(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("60"),
                    closed_stock=2,
                ),
                InitialPresentationPayload(
                    name="Bolsa x10",
                    factor=10,
                    sale_price=Decimal("100"),
                    closed_stock=0,
                ),
            ),
        )
    )
    app = app_for(make_assistant(connection))
    before = snapshot(connection)

    open_pending_proposals(app, [proposal])
    app.text_input(
        key=proposal_edit_presentation_key(proposal, 1, "name")
    ).set_value("Caja x12")
    app.number_input(
        key=proposal_edit_presentation_key(proposal, 1, "factor")
    ).set_value(12)
    app.text_input(
        key=proposal_edit_presentation_key(proposal, 1, "sale_price")
    ).set_value("75.5")
    app.number_input(
        key=proposal_edit_presentation_key(proposal, 1, "closed_stock")
    ).set_value(3)
    app.number_input(
        key=proposal_edit_presentation_key(proposal, 2, "closed_stock")
    ).set_value(7)
    proposal_edit_submit(app, proposal)

    edited = manual_proposals_of(app)[0].payload.presentations

    assert not app.exception
    assert len(edited) == 2
    assert edited[0].name == "Caja x12"
    assert edited[0].factor == 12
    assert edited[0].sale_price == Decimal("75.5")
    assert edited[0].closed_stock == 3
    assert edited[1].name == "Bolsa x10"
    assert edited[1].factor == 10
    assert edited[1].sale_price == Decimal("100")
    assert edited[1].closed_stock == 7
    assert snapshot(connection) == before


def test_editing_a_price_update_keeps_the_presentation_and_does_not_write(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la edición del cambio de precio (T051, C14).

    La revisión muestra el precio vigente y el nuevo; la edición conserva la
    presentación afectada y no registra historial ni modifica la base.
    """
    proposal = proposal_for(
        PriceUpdatePayload(
            presentation_id=catalog.box.id,
            new_price=Decimal("500"),
        )
    )
    app = app_for(make_assistant(connection))
    before = snapshot(connection)
    changes_before = connection.total_changes

    open_pending_proposals(app, [proposal])

    rendered = rendered_markdown(app)

    assert not app.exception
    assert "precio vigente: 600.00" in rendered
    assert "precio nuevo: 500.00" in rendered

    app.text_input(
        key=proposal_edit_key(proposal, "new_price")
    ).set_value("700.5")
    proposal_edit_submit(app, proposal)

    edited = manual_proposals_of(app)[0]

    assert edited.action is ProposalAction.PRICE_UPDATE
    assert edited.payload.presentation_id == catalog.box.id
    assert edited.payload.new_price == Decimal("700.5")
    assert snapshot(connection) == before
    assert snapshot(connection)["price_changes"] == []
    assert connection.total_changes == changes_before


# --- Descarte local de propuestas pendientes (T052, RF-09) ---


def proposal_discard_key(proposal: Proposal) -> str:
    """Devuelve la clave del botón de descarte de una propuesta (T052)."""
    return f"proposal_discard_{id(proposal)}"


def discard_pending_proposal(app: AppTest, proposal: Proposal) -> None:
    """Descarta la propuesta indicada desde la sección (T052)."""
    app.button(key=proposal_discard_key(proposal)).click().run()


def test_discarding_a_manual_proposal_hides_only_that_proposal(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica el descarte local de una propuesta manual (T052, C15).

    La propuesta descartada deja de ofrecerse, las demás siguen disponibles, la
    colección de la sesión y la propuesta original no cambian y no se escribe
    en la base ni se incrementa el contador de cambios de SQLite.
    """
    first = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Primera",
        )
    )
    second = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=5,
            reason="Segunda",
        )
    )
    original = first.model_copy(deep=True)
    before = snapshot(connection)
    changes_before = connection.total_changes
    app = app_for(make_assistant(connection))

    open_pending_proposals(app, [first, second])
    discard_pending_proposal(app, first)

    rendered = rendered_markdown(app)
    stored = manual_proposals_of(app)

    assert not app.exception
    assert "Motivo: Primera" not in rendered
    assert "Motivo: Segunda" in rendered
    assert len(stored) == 2
    assert stored[0] is first
    assert stored[1] is second
    assert first == original
    assert [
        button.label
        for button in app.button
        if button.label == DISCARD_LABEL
    ] == [DISCARD_LABEL]
    assert app.session_state[DISCARDED_PROPOSALS_KEY] == {id(first)}
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_discarding_an_assistant_proposal_keeps_its_turn_and_the_manual_one(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica el descarte de una propuesta del asistente (T052, C15).

    Se oculta solo la propuesta descartada: el turno conserva su propuesta
    pendiente original, las demás propuestas siguen disponibles y no se
    confirma, edita ni escribe nada.
    """
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_entry",
            {
                "product_id": catalog.product.id,
                "quantity": 3,
                "reason": "Del asistente",
            },
        ),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Propone una entrada suelta").run()
    assistant_proposal = assistant.history[0].state["pending_proposal"]
    manual = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=7,
            reason="Manual",
        )
    )
    original = assistant_proposal.model_copy(deep=True)
    app.session_state[MANUAL_PROPOSALS_KEY] = [manual]
    before = snapshot(connection)
    changes_before = connection.total_changes

    app.radio[0].set_value("Propuestas pendientes").run()
    discard_pending_proposal(app, assistant_proposal)

    rendered = rendered_markdown(app)

    assert not app.exception
    assert "Motivo: Del asistente" not in rendered
    assert "Motivo: Manual" in rendered
    assert len(assistant.history) == 1
    assert (
        assistant.history[0].state["pending_proposal"] is assistant_proposal
    )
    assert assistant.history[0].state["execution_result"] is None
    assert assistant_proposal == original
    assert manual_proposals_of(app)[0] is manual
    assert app.session_state[DISCARDED_PROPOSALS_KEY] == {
        id(assistant_proposal)
    }
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_discarding_an_assistant_proposal_hides_it_from_the_assistant_section(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que el descarte alcanza también la sección Asistente (T052, C15).

    La propuesta descartada desde «Propuestas pendientes» deja de ofrecerse en
    la conversación y no muestra su control de confirmación; la propuesta manual
    sigue disponible en la sección de propuestas. La propuesta original no
    cambia, el turno conserva su estado y no se escribe en la base.
    """
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_entry",
            {
                "product_id": catalog.product.id,
                "quantity": 3,
                "reason": "Del asistente",
            },
        ),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Propone una entrada suelta").run()
    assistant_proposal = assistant.history[0].state["pending_proposal"]
    manual = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=5,
            reason="Manual",
        )
    )
    original = assistant_proposal.model_copy(deep=True)
    app.session_state[MANUAL_PROPOSALS_KEY] = [manual]
    before = snapshot(connection)
    changes_before = connection.total_changes

    app.radio[0].set_value("Propuestas pendientes").run()
    discard_pending_proposal(app, assistant_proposal)

    pending_rendered = rendered_markdown(app)

    assert "Motivo: Del asistente" not in pending_rendered
    assert "Motivo: Manual" in pending_rendered

    app.radio[0].set_value(DEFAULT_SECTION_LABEL).run()

    rendered = rendered_markdown(app)

    assert not app.exception
    assert "- reason: Del asistente" not in rendered
    assert DISCARDED_PROPOSAL_MESSAGE in rendered
    assert [button.label for button in app.button] == []
    assert manual_proposals_of(app)[0] is manual
    assert (
        assistant.history[0].state["pending_proposal"] is assistant_proposal
    )
    assert assistant.history[0].state["execution_result"] is None
    assert assistant_proposal == original
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_confirm_pending_rejects_a_discarded_proposal_without_the_service(
    connection: sqlite3.Connection,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifica que una propuesta descartada no se confirma (T052, C15).

    `confirm_pending` rechaza la propuesta descartada antes de invocar al
    servicio, sin escribir, sin cambiar el estado de la propuesta y sin mutar
    el turno.
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
    turn = answer(assistant, "Propone una entrada")
    proposal = turn.state["pending_proposal"]
    original = proposal.model_copy(deep=True)
    calls: list[Proposal] = []
    monkeypatch.setattr(
        assistant.session.proposal_service,
        "confirm",
        lambda discarded_proposal: calls.append(discarded_proposal),
    )
    before = snapshot(connection)
    changes_before = connection.total_changes

    reason = confirm_pending(assistant, turn, discarded={id(proposal)})

    assert reason == DISCARDED_PROPOSAL_MESSAGE
    assert calls == []
    assert proposal.status is ProposalStatus.PENDING
    assert proposal == original
    assert turn.state["pending_proposal"] is proposal
    assert turn.state["execution_result"] is None
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_a_discarded_proposal_stays_hidden_between_section_changes(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que el descarte se conserva en la sesión (T052, C15).

    Al descartar todas las propuestas pendientes la sección muestra su estado
    vacío, y las descartadas no reaparecen al cambiar de sección y volver.
    """
    first = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Primera",
        )
    )
    second = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=5,
            reason="Segunda",
        )
    )
    app = app_for(make_assistant(connection))

    open_pending_proposals(app, [first, second])
    discard_pending_proposal(app, first)
    discard_pending_proposal(app, second)

    assert not app.exception
    assert [info.value for info in app.info] == [EMPTY_PROPOSALS_MESSAGE]

    app.radio[0].set_value(DEFAULT_SECTION_LABEL).run()
    app.radio[0].set_value("Propuestas pendientes").run()

    assert not app.exception
    assert [info.value for info in app.info] == [EMPTY_PROPOSALS_MESSAGE]
    assert app.session_state[DISCARDED_PROPOSALS_KEY] == {
        id(first),
        id(second),
    }
    assert len(manual_proposals_of(app)) == 2


def test_the_discard_is_local_and_keeps_the_proposal_contract() -> None:
    """
    Verifica el alcance local del descarte (T052, C15).

    La vista solo delega en su callback: no usa `st.session_state`, no nombra el
    servicio de propuestas ni invoca creación, edición o confirmación. Los
    estados de la propuesta siguen siendo solo pendiente y confirmada.
    """
    source = PROPOSALS_SECTION_PATH.read_text(encoding="utf-8")

    assert DISCARD_LABEL in source
    assert "on_discard" in source
    for marker in (
        "st.session_state",
        "proposal_service",
        ".create(",
        ".edit(",
        ".confirm(",
    ):
        assert marker not in source, marker

    assert [status.value for status in ProposalStatus] == [
        "pendiente",
        "confirmada",
    ]


# --- Confirmación centralizada y doble ejecución (T053, RF-09) ---


def proposal_confirm_key(proposal: Proposal) -> str:
    """Devuelve la clave del botón de confirmación de una propuesta (T053)."""
    return f"proposal_confirm_{id(proposal)}"


def confirm_pending_proposal(app: AppTest, proposal: Proposal) -> None:
    """Confirma la propuesta indicada desde «Propuestas pendientes» (T053)."""
    app.button(key=proposal_confirm_key(proposal)).click().run()


def test_confirming_a_manual_proposal_executes_it_once(
    connection: sqlite3.Connection,
    catalog: Catalog,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica la confirmación de una propuesta manual desde la sección (T053).

    La sección ofrece el control de confirmación para la propuesta creada por
    los formularios de «Operaciones», la confirmación la ejecuta el único punto
    de `main.py` y, al completarse, el resultado real se muestra una vez y la
    propuesta confirmada deja de ofrecerse.
    """
    manual = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra manual",
        )
    )
    assistant = make_assistant(connection)
    app = app_for(assistant)

    open_pending_proposals(app, [manual])

    assert [button.label for button in app.button] == [
        CONFIRM_LABEL,
        DISCARD_LABEL,
        EDIT_SUBMIT_LABEL,
    ]
    assert manual.status is ProposalStatus.PENDING

    confirm_pending_proposal(app, manual)

    confirmed = manual_proposals_of(app)[0]

    assert not app.exception
    assert confirmed.status is ProposalStatus.CONFIRMED
    assert manual.status is ProposalStatus.PENDING
    assert products.get(catalog.product.id).loose_units == 7
    assert len(movements.list_by_product(catalog.product.id)) == 3
    assert len(app.button) == 0
    assert CONFIRM_LABEL not in [button.label for button in app.button]
    assert [info.value for info in app.info] == [EMPTY_PROPOSALS_MESSAGE]
    assert any(
        "Operación confirmada" in success.value for success in app.success
    )
    assert app.session_state.get(CONFIRMATION_NOTICE_KEY) is None


def test_a_confirmed_manual_proposal_is_not_executed_twice(
    connection: sqlite3.Connection,
    catalog: Catalog,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifica el segundo intento sobre una propuesta manual confirmada (T053).

    La propuesta confirmada no vuelve a ejecutarse: la confirmación por
    identidad rechaza su estado confirmado antes de invocar al servicio, y una
    clave que ya no está en la sesión devuelve un motivo explícito. La base y
    el contador de cambios de SQLite no cambian.
    """
    manual = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra manual",
        )
    )
    assistant = make_assistant(connection)
    app = app_for(assistant)

    open_pending_proposals(app, [manual])
    confirm_pending_proposal(app, manual)
    confirmed = manual_proposals_of(app)[0]
    monkeypatch.setattr(
        "app.ui.main.manual_proposals",
        lambda: [confirmed],
    )
    before = snapshot(connection)
    changes_before = connection.total_changes

    repeated = confirm_proposal(
        assistant,
        assistant.session,
        id(confirmed),
    )
    stale = confirm_proposal(
        assistant,
        assistant.session,
        id(manual),
    )

    assert repeated == ALREADY_CONFIRMED_MESSAGE
    assert stale == CONFIRMATION_NOT_AVAILABLE_MESSAGE
    assert confirmed.status is ProposalStatus.CONFIRMED
    assert products.get(catalog.product.id).loose_units == 7
    assert len(movements.list_by_product(catalog.product.id)) == 3
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_confirming_an_assistant_proposal_from_the_proposals_section(
    connection: sqlite3.Connection,
    catalog: Catalog,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica la confirmación de una propuesta del asistente desde la sección
    (T053).

    La confirmación actualiza el turno del asistente —copia confirmada y
    resultado real—, deja de ofrecerse en «Propuestas pendientes» y tampoco
    ofrece el control en la conversación. La operación se ejecuta una sola vez.
    """
    assistant = make_assistant(
        connection,
        tool_call(
            "propose_loose_entry",
            {
                "product_id": catalog.product.id,
                "quantity": 3,
                "reason": "Compra del asistente",
            },
        ),
    )
    app = app_for(assistant)

    app.run()
    app.chat_input[0].set_value("Propone una entrada suelta").run()
    proposal = assistant.history[0].state["pending_proposal"]

    app.radio[0].set_value("Propuestas pendientes").run()
    confirm_pending_proposal(app, proposal)

    state = assistant.history[0].state
    result = state["execution_result"]

    assert not app.exception
    assert result is not None
    assert result.status is ProposalStatus.CONFIRMED
    assert isinstance(result.result, MovementRecord)
    assert state["pending_proposal"].status is ProposalStatus.CONFIRMED
    assert len(movements.list_by_product(catalog.product.id)) == 3
    assert len(app.button) == 0
    assert any(
        "Operación confirmada" in success.value for success in app.success
    )

    app.radio[0].set_value(DEFAULT_SECTION_LABEL).run()

    rendered = rendered_markdown(app)

    assert not app.exception
    assert "Operación confirmada" in rendered
    assert [button.label for button in app.button] == []


def test_the_confirm_control_is_disabled_while_processing_in_the_section(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica la protección de reenvío de la sección de propuestas (T053).

    Con una confirmación en curso en la sesión, el control de confirmación de
    cada propuesta pendiente se deshabilita y la propuesta no se ejecuta. La
    marca es local a la interfaz y **no** una idempotencia durable.
    """
    manual = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra manual",
        )
    )
    assistant = make_assistant(connection)
    app = app_for(assistant)
    app.session_state[PROCESSING_KEY] = True
    before = snapshot(connection)
    changes_before = connection.total_changes

    open_pending_proposals(app, [manual])

    confirm_buttons = [
        button for button in app.button if button.label == CONFIRM_LABEL
    ]

    assert not app.exception
    assert [button.proto.disabled for button in confirm_buttons] == [True]
    assert manual.status is ProposalStatus.PENDING
    assert manual_proposals_of(app)[0] is manual
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_confirm_from_section_returns_early_while_a_confirmation_is_active(
    connection: sqlite3.Connection,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifica la guarda de reenvío de `_confirm_from_section` (T053, C16).

    Con `PROCESSING_KEY` activo, la confirmación desde la sección devuelve el
    motivo sin invocar `proposal_service.confirm`, sin modificar la propuesta
    ni la persistencia y sin registrar un aviso de resultado. El estado
    controlado sustituye solo la sesión de Streamlit que lee el helper, de modo
    que la prueba ejerce la guarda directamente sin depender del runtime.
    """
    manual = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra manual",
        )
    )
    original = manual.model_copy(deep=True)
    assistant = make_assistant(connection)
    calls: list[Proposal] = []
    monkeypatch.setattr(
        assistant.session.proposal_service,
        "confirm",
        lambda proposal: calls.append(proposal),
    )
    controlled_state: dict[str, Any] = {
        PROCESSING_KEY: True,
        MANUAL_PROPOSALS_KEY: [manual],
    }
    monkeypatch.setattr(
        main_module,
        "st",
        SimpleNamespace(session_state=controlled_state),
    )
    before = snapshot(connection)
    changes_before = connection.total_changes

    reason = main_module._confirm_from_section(
        assistant,
        assistant.session,
        id(manual),
        set(),
    )

    assert reason == CONFIRMATION_IN_PROGRESS_MESSAGE
    assert calls == []
    assert manual.status is ProposalStatus.PENDING
    assert manual == original
    assert controlled_state[MANUAL_PROPOSALS_KEY] == [manual]
    assert controlled_state[MANUAL_PROPOSALS_KEY][0] is manual
    assert controlled_state[PROCESSING_KEY] is True
    assert CONFIRMATION_NOTICE_KEY not in controlled_state
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_the_confirmation_is_centralized_in_a_single_helper() -> None:
    """
    Verifica el único punto de confirmación de la interfaz (T053, C12, D-A).

    En todo `app/ui/**` sigue habiendo una sola aparición de `.confirm(` y su
    función contenedora es `_apply_confirmation`; las dos rutas de confirmación
    —el turno del asistente y la sección de propuestas— delegan en ella.
    """
    source = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    owners = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and ".confirm(" in (ast.get_source_segment(source, node) or "")
    ]

    assert owners == ["_apply_confirmation"]
    assert (
        sum(
            path.read_text(encoding="utf-8").count(".confirm(")
            for path in ui_sources()
        )
        == 1
    )


# --- Rediseño visual (T057) ---


def test_theme_config_uses_the_approved_palette() -> None:
    """
    Verifica el tema oscuro azul y la paleta aprobada (T064, D2).

    Sustituye la paleta clara/neutra de T057 por la convención vigente: base
    oscura, acentos turquesa y paneles azulados. La comprobación es textual
    sobre el tema nativo declarado en `.streamlit/config.toml`.
    """
    source = THEME_CONFIG_PATH.read_text(encoding="utf-8")

    assert "[theme]" in source
    assert 'base = "dark"' in source
    assert 'base = "light"' not in source
    for declaration in (
        'primaryColor = "#0F766E"',
        'backgroundColor = "#061826"',
        'secondaryBackgroundColor = "#0D2B45"',
        'textColor = "#E8F4FF"',
        'linkColor = "#5EEAD4"',
        'borderColor = "#1E4E6B"',
        'codeBackgroundColor = "#08243A"',
        'codeTextColor = "#D8F3F0"',
        'showWidgetBorder = true',
        'showSidebarBorder = true',
        'baseRadius = "medium"',
        'buttonRadius = "medium"',
        'font = "sans-serif"',
    ):
        assert declaration in source, declaration


def test_theme_config_defines_the_sidebar_and_dataframe_palette() -> None:
    """
    Verifica la paleta del sidebar y de las tablas (T064, D2).

    La sección `[theme.sidebar]` se declara después de `[theme]`; por eso se
    separa el texto en la cabecera de la sección anidada: las claves de tabla
    pertenecen al tema general y las del sidebar, a su propia sección.
    """
    source = THEME_CONFIG_PATH.read_text(encoding="utf-8")
    general, separator, sidebar = source.partition("[theme.sidebar]")

    assert separator == "[theme.sidebar]"

    for declaration in (
        'backgroundColor = "#04121F"',
        'secondaryBackgroundColor = "#0A2236"',
        'textColor = "#E8F4FF"',
        'borderColor = "#1E4E6B"',
        'primaryColor = "#0F766E"',
    ):
        assert declaration in sidebar, declaration

    for declaration in (
        'dataframeBorderColor = "#1E4E6B"',
        'dataframeHeaderBackgroundColor = "#0B3A52"',
        'dataframeHeaderTextColor = "#E8F4FF"',
    ):
        assert declaration in general, declaration


def test_the_ui_does_not_use_custom_css() -> None:
    """Verifica que la interfaz usa solo componentes nativos (T057, D1)."""
    sources = ui_sources()

    assert sources

    for path in sources:
        source = path.read_text(encoding="utf-8")
        assert "<style" not in source, path.name
        assert "unsafe_allow_html" not in source, path.name
        assert "st.html" not in source, path.name


# --- Estados de interfaz y zona horaria (T055) ---


def test_the_data_sections_show_an_explicit_loading_state(
    monkeypatch: pytest.MonkeyPatch,
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica el estado de carga real de cada sección que lee servicios (T055).

    `st.spinner` es transitorio y no queda en el árbol de `AppTest`; por eso se
    reemplaza por un registrador que conserva cada llamada real. Se abre cada
    sección de lectura, se elige el formulario que consulta el catálogo y se
    envía un mensaje al asistente: en todos esos casos la interfaz muestra el
    mismo estado de carga compartido.
    """
    calls: list[str] = []

    def record_spinner(message: str) -> Any:
        """Registra el estado de carga y continúa el render sin spinner."""
        calls.append(message)

        return nullcontext()

    monkeypatch.setattr(main_module.st, "spinner", record_spinner)
    app = app_for(make_assistant(connection))
    app.run()

    for section in (
        "Inicio",
        "Productos",
        "Movimientos",
        "Historial de precios",
        "Propuestas pendientes",
    ):
        before = len(calls)
        app.radio[0].set_value(section).run()
        assert len(calls) > before, section

    before = len(calls)
    app.radio[0].set_value("Operaciones").run()
    choose_operation(app, "entrada_suelta")
    assert len(calls) > before

    before = len(calls)
    app.radio[0].set_value("Asistente").run()
    app.chat_input[0].set_value("¿Cuánto stock hay?").run()
    assert len(calls) > before

    assert not app.exception
    assert calls
    assert set(calls) == {LOADING_MESSAGE}


def test_the_conversation_history_uses_local_dates_and_keeps_utc_storage(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
) -> None:
    """
    Verifica la fecha local del historial de la conversación (T055, C4, C19).

    El registro se almacena en UTC; la presentación de la conversación usa el
    mismo formato local que las secciones, sin ISO 8601 ni desplazamiento UTC.
    La consulta y el render no modifican el almacenamiento.
    """
    base = base_presentation(presentations, catalog.product.id)
    occurred_at = datetime(2026, 3, 10, 15, 30, 45, tzinfo=timezone.utc)
    record = SqlitePriceHistoryRepository(connection).add(
        product_id=catalog.product.id,
        presentation_id=base.id,
        previous_price=Decimal("100"),
        new_price=Decimal("110"),
        occurred_at=occurred_at,
    )
    before = snapshot(connection)
    changes_before = connection.total_changes

    rendered = describe_price_history([record])
    stored = connection.execute(
        "SELECT occurred_at FROM price_changes"
    ).fetchone()[0]

    assert "2026-03-10 12:30:45 -03:00" in rendered
    assert "2026-03-10T15:30:45" not in rendered
    assert "+00:00" not in rendered
    assert str(stored).startswith("2026-03-10T15:30:45")
    assert str(stored).endswith("+00:00")
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


def test_the_conversation_and_price_history_section_share_the_local_instant(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
) -> None:
    """
    Verifica la consistencia de fechas entre la conversación y la sección
    (T055, C4, C19).

    El mismo cambio de precio se presenta con idéntico formato local en el
    historial del asistente y en la tabla de «Historial de precios»; ninguna de
    las dos superficies muestra ISO 8601 ni UTC.
    """
    base = base_presentation(presentations, catalog.product.id)
    occurred_at = datetime(2026, 3, 10, 15, 30, tzinfo=timezone.utc)
    record = SqlitePriceHistoryRepository(connection).add(
        product_id=catalog.product.id,
        presentation_id=base.id,
        previous_price=Decimal("100"),
        new_price=Decimal("110"),
        occurred_at=occurred_at,
    )
    app = app_for(make_assistant(connection))

    app.run()
    app.radio[0].set_value("Historial de precios").run()
    choose_price_history_product(app, catalog.product)
    records = detail_records(app, DETAIL_PRICE_HISTORY_COLUMNS)
    shown = next(
        row[DATE_LABEL] for row in records if row[PRESENTATION_LABEL] == "botella"
    )
    conversation = describe_price_history([record])

    assert not app.exception
    assert "2026-03-10 12:30:00 -03:00" in conversation
    assert shown == "2026-03-10 12:30:00 -03:00"
    assert "T" not in shown
    assert "+00:00" not in shown


def test_describe_result_returns_a_readable_markdown_list() -> None:
    """
    Verifica la composición visual del resultado de una confirmación (T024).

    El resultado se compone como una lista Markdown: el encabezado en negrita,
    una línea en blanco y un ítem `- **Etiqueta:** valor` por campo, sin
    exponer `None`, booleanos crudos, nombres snake_case ni valores de
    enumeraciones internas.
    """
    result = ConfirmationResult(
        action=ProposalAction.LOOSE_ENTRY,
        status=ProposalStatus.CONFIRMED,
        proposal=proposal_for(
            LooseEntryPayload(product_id=3, quantity=3, reason="Compra")
        ),
        result=MovementRecord(
            id=7,
            product_id=3,
            presentation_id=1,
            kind=MovementKind.LOOSE_ENTRY,
            quantity=3,
            reason="Compra",
            occurred_at=datetime(
                2026, 3, 10, 15, 30, 45, tzinfo=timezone.utc
            ),
            cost=Decimal("12.50"),
            applied_price=Decimal("99.90"),
        ),
    )

    described = describe_result(result)
    lines = described.split("\n")

    assert lines[0] == "**Operación confirmada:** Entrada de unidades sueltas"
    assert lines[1] == ""
    assert lines[2] == "- **Estado:** Confirmada"
    for line in lines[2:]:
        assert re.fullmatch(r"- \*\*[^*]+:\*\* .+", line), line
    for raw in (
        "None",
        "True",
        "False",
        "MovementKind",
        "ProposalStatus",
        "entrada_suelta",
        "occurred_at",
        "applied_price",
        "is_initial_stock",
    ):
        assert raw not in described


def test_describe_result_formats_the_utc_instant_in_local_time() -> None:
    """
    Verifica la fecha local del resultado de una confirmación (T055, B1).

    El resultado real se describe en hora local argentina, sin exponer el
    instante UTC crudo ni su desplazamiento, y presenta el movimiento con
    etiquetas en español: sin nombres snake_case, sin el valor interno de
    `MovementKind`, sin `None` y sin `False`.
    """
    result = ConfirmationResult(
        action=ProposalAction.LOOSE_ENTRY,
        status=ProposalStatus.CONFIRMED,
        proposal=proposal_for(
            LooseEntryPayload(product_id=3, quantity=3, reason="Compra")
        ),
        result=MovementRecord(
            id=7,
            product_id=3,
            presentation_id=1,
            kind=MovementKind.LOOSE_ENTRY,
            quantity=3,
            reason="Compra",
            occurred_at=datetime(
                2026, 3, 10, 15, 30, 45, tzinfo=timezone.utc
            ),
            cost=Decimal("12.50"),
            applied_price=Decimal("99.90"),
        ),
    )

    described = describe_result(result)

    assert "**Operación confirmada:** Entrada de unidades sueltas" in described
    assert "- **Estado:** Confirmada" in described
    assert "- **Tipo:** Entrada suelta" in described
    assert "- **Cantidad:** 3" in described
    assert "- **Motivo:** Compra" in described
    assert "- **Fecha:** 2026-03-10 12:30:45 -03:00" in described
    assert "- **Costo:** 12.50" in described
    assert "- **Precio aplicado:** 99.90" in described
    assert "- **Stock inicial:** No" in described
    assert "- **Movimiento original:** no informado" in described
    assert "- **Cantidad consumida:** 0" in described
    assert "- **Cantidad compensada:** 0" in described
    assert "2026-03-10T15:30:45" not in described
    assert "+00:00" not in described
    assert "MovementKind" not in described
    assert "entrada_suelta" not in described
    assert "occurred_at" not in described
    assert "applied_price" not in described
    assert "is_initial_stock" not in described
    assert "None" not in described
    assert "False" not in described


def test_describe_result_presents_a_registered_product_in_spanish() -> None:
    """
    Verifica la presentación legible del alta de un producto (T024, T055).

    El registro persistido se describe con etiquetas en español y el estado
    real del producto como `Activo`, sin nombres snake_case ni valores
    booleanos internos.
    """
    record = ProductRecord(
        id=1,
        sku="SKU-1",
        name="Producto",
        base_unit="botella",
        minimum_stock=20,
        loose_units=4,
        active=True,
    )
    result = ConfirmationResult(
        action=ProposalAction.PRODUCT_REGISTRATION,
        status=ProposalStatus.CONFIRMED,
        proposal=proposal_for(
            ProductRegistrationPayload(
                sku="SKU-1",
                name="Producto",
                base_unit="botella",
                base_sale_price=Decimal("100"),
                minimum_stock=20,
                initial_loose_units=4,
            )
        ),
        result=record,
    )

    described = describe_result(result)

    assert "**Operación confirmada:** Alta de producto" in described
    assert "- **Estado:** Confirmada" in described
    assert "- **SKU:** SKU-1" in described
    assert "- **Nombre:** Producto" in described
    assert "- **Unidad base:** botella" in described
    assert "- **Stock mínimo:** 20" in described
    assert "- **Unidades sueltas:** 4" in described
    assert "- **Estado:** Activo" in described
    for raw in (
        "base_unit",
        "minimum_stock",
        "loose_units",
        "active",
        "None",
        "True",
        "False",
    ):
        assert raw not in described


def test_describe_result_presents_a_registered_presentation_in_spanish() -> None:
    """
    Verifica la presentación legible del alta de una presentación (T024, T055).

    La presentación persistida se describe con su nombre, su equivalencia, su
    precio de venta formateado y su stock cerrado, sin nombres snake_case ni
    valores ausentes.
    """
    record = PresentationRecord(
        id=2,
        product_id=1,
        name="Caja x6",
        factor=6,
        sale_price=Decimal("600"),
        closed_stock=2,
    )
    result = ConfirmationResult(
        action=ProposalAction.PRESENTATION_REGISTRATION,
        status=ProposalStatus.CONFIRMED,
        proposal=proposal_for(
            PresentationRegistrationPayload(
                product_id=1,
                name="Caja x6",
                factor=6,
                sale_price=Decimal("600"),
            )
        ),
        result=record,
    )

    described = describe_result(result)

    assert "**Operación confirmada:** Alta de presentación" in described
    assert "- **Estado:** Confirmada" in described
    assert "- **Nombre:** Caja x6" in described
    assert "- **Equivalencia:** 6" in described
    assert "- **Precio de venta:** 600.00" in described
    assert "- **Stock cerrado:** 2" in described
    for raw in ("product_id", "sale_price", "closed_stock", "None", "False"):
        assert raw not in described


def test_the_assistant_turn_detail_shows_the_local_instant_and_keeps_utc_storage(
    connection: sqlite3.Connection,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifica la fecha local del detalle del turno del asistente (T055, B1).

    La confirmación de una propuesta del asistente mantiene el instante en UTC
    en la persistencia y el detalle del turno lo presenta en hora local, sin
    ISO 8601 ni desplazamiento UTC.
    """
    fixed_instant = datetime(2026, 3, 10, 15, 30, 45, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.movements._utc_now",
        lambda: fixed_instant,
    )
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
    app.button[0].set_value(True).run()

    rendered = rendered_markdown(app)
    stored = connection.execute(
        "SELECT occurred_at FROM movements"
    ).fetchone()[0]
    result = assistant.history[0].state["execution_result"]

    assert not app.exception
    assert "- **Fecha:** 2026-03-10 12:30:45 -03:00" in rendered
    assert "2026-03-10T15:30:45" not in rendered
    assert "+00:00" not in rendered
    assert "**Operación confirmada:** Entrada de unidades sueltas" in rendered
    assert (
        "**Operación confirmada:** Entrada de unidades sueltas\n\n"
        "- **Estado:** Confirmada"
    ) in rendered
    assert "- **Tipo:** Entrada suelta" in rendered
    assert "- **Cantidad:** 3" in rendered
    assert "- **Motivo:** Compra" in rendered
    assert "occurred_at" not in rendered
    described = describe_result(result)
    assert "entrada_suelta" not in described
    assert "MovementKind" not in described
    assert result.result.occurred_at == fixed_instant
    assert str(stored).startswith("2026-03-10T15:30:45")
    assert str(stored).endswith("+00:00")


def test_the_proposals_confirmation_notice_shows_the_local_instant(
    connection: sqlite3.Connection,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifica la fecha local del aviso de «Propuestas pendientes» (T055, B1).

    El aviso de la última confirmación presenta el instante en hora local, sin
    desplazamiento UTC, y conserva el resto del resultado real.
    """
    fixed_instant = datetime(2026, 3, 10, 15, 30, 45, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.services.movements._utc_now",
        lambda: fixed_instant,
    )
    manual = proposal_for(
        LooseEntryPayload(
            product_id=catalog.product.id,
            quantity=3,
            reason="Compra manual",
        )
    )
    app = app_for(make_assistant(connection))

    open_pending_proposals(app, [manual])
    confirm_pending_proposal(app, manual)

    notices = [success.value for success in app.success]
    stored = connection.execute(
        "SELECT occurred_at FROM movements"
    ).fetchone()[0]

    assert not app.exception
    assert len(notices) == 1
    assert "- **Fecha:** 2026-03-10 12:30:45 -03:00" in notices[0]
    assert "2026-03-10T15:30:45" not in notices[0]
    assert "+00:00" not in notices[0]
    assert "**Operación confirmada:** Entrada de unidades sueltas" in notices[0]
    assert (
        "**Operación confirmada:** Entrada de unidades sueltas\n\n"
        "- **Estado:** Confirmada"
    ) in notices[0]
    assert "- **Cantidad:** 3" in notices[0]
    assert "- **Motivo:** Compra manual" in notices[0]
    assert "occurred_at" not in notices[0]
    assert "entrada_suelta" not in notices[0]
    assert "None" not in notices[0]
    assert "False" not in notices[0]
    assert str(stored).startswith("2026-03-10T15:30:45")
    assert str(stored).endswith("+00:00")
    assert app.session_state.get(CONFIRMATION_NOTICE_KEY) is None


def test_a_rejected_proposals_confirmation_is_not_marked_as_success(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """
    Verifica que un rechazo no se presenta como un resultado confirmado
    (T055, B1).

    El servicio rechaza la salida por falta de stock: la sección muestra el
    motivo real, no registra un aviso de confirmación exitosa y la propuesta
    sigue pendiente.
    """
    manual = proposal_for(
        LooseExitPayload(
            product_id=catalog.product.id,
            quantity=9,
            reason="Venta",
        )
    )
    app = app_for(make_assistant(connection))

    open_pending_proposals(app, [manual])
    confirm_pending_proposal(app, manual)

    assert not app.exception
    assert len(app.error) == 1
    assert CONFIRMATION_ERROR_PREFIX in app.error[0].value
    assert "No hay unidades sueltas suficientes" in app.error[0].value
    assert app.success == []
    assert app.session_state.get(CONFIRMATION_NOTICE_KEY) is None
    assert manual.status is ProposalStatus.PENDING


# --- Costos iniciales por grupo (T058) ---


def test_the_product_registration_form_uses_text_inputs_for_the_costs(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica los campos de costo total del alta de producto (T058).

    Los costos se informan como texto y `Decimal`, nunca con un
    `st.number_input`, y cada campo identifica su grupo.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)

    loose_cost = operation_text_input(app, INITIAL_LOOSE_COST_LABEL)
    presentation_cost = operation_text_input(
        app, INITIAL_PRESENTATION_COST_LABEL
    )
    number_labels = {element.label for element in app.number_input}

    assert not app.exception
    assert loose_cost.label == INITIAL_LOOSE_COST_LABEL
    assert loose_cost.placeholder == "Ej.: 1000.00"
    assert "no es un costo unitario" in loose_cost.help
    assert presentation_cost.label == INITIAL_PRESENTATION_COST_LABEL
    assert presentation_cost.placeholder == "Ej.: 7500.00"
    assert "no se distribuye" in presentation_cost.help
    assert INITIAL_LOOSE_COST_LABEL not in number_labels
    assert INITIAL_PRESENTATION_COST_LABEL not in number_labels


def test_the_product_registration_form_keeps_the_initial_costs(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica que el alta declara ambos costos sin escribir (T058).

    El costo suelto y el de la presentación viajan como importes exactos en el
    payload de la propuesta pendiente.
    """
    app = app_for(make_assistant(connection))

    open_operations_section(app)
    operation_text_input(app, SKU_FIELD_LABEL).set_value("SKU-COSTOS")
    operation_text_input(app, NAME_FIELD_LABEL).set_value(
        "Producto con costos"
    )
    operation_text_input(app, BASE_UNIT_FIELD_LABEL).set_value("botella")
    operation_text_input(app, BASE_PRICE_FIELD_LABEL).set_value("100")
    operation_number_input(app, INITIAL_LOOSE_UNITS_LABEL).set_value(3)
    operation_text_input(app, INITIAL_LOOSE_COST_LABEL).set_value("1000")
    operation_text_input(app, INITIAL_PRESENTATION_NAME_LABEL).set_value(
        "Caja x6"
    )
    operation_number_input(app, FACTOR_FIELD_LABEL).set_value(6)
    operation_text_input(app, PRESENTATION_PRICE_LABEL).set_value("7500")
    operation_number_input(app, INITIAL_CLOSED_STOCK_LABEL).set_value(1)
    operation_text_input(app, INITIAL_PRESENTATION_COST_LABEL).set_value(
        "7500"
    )
    before = snapshot(connection)

    submit_operation(app)

    payload = manual_proposals_of(app)[0].payload

    assert not app.exception
    assert payload.initial_loose_cost == Decimal("1000")
    assert payload.presentations[0].cost == Decimal("7500")
    assert snapshot(connection) == before


def test_the_review_shows_initial_costs_and_their_unit_references() -> None:
    """
    Verifica el costo total y la referencia unitaria en la revisión (T058).

    Las unidades sueltas muestran `1000 / 3 = 333.33` y la caja de factor 6
    muestra `7500 / 6 = 1250.00`; la referencia es solo de presentación.
    """
    review = proposal_review(
        ProductRegistrationPayload(
            sku="SKU-COSTOS",
            name="Producto con costos",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            initial_loose_units=3,
            initial_loose_cost=Decimal("1000"),
            presentations=(
                InitialPresentationPayload(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("7500"),
                    closed_stock=1,
                    cost=Decimal("7500"),
                ),
            ),
        )
    )

    lines = describe_proposal_review(review)
    described = "\n".join(lines)
    references = [
        line for line in lines
        if line.startswith(f"{UNIT_COST_REFERENCE_FIELD_LABEL}:")
    ]

    assert f"{INITIAL_LOOSE_COST_FIELD_LABEL}: 1000.00" in described
    assert (
        f"{INITIAL_PRESENTATION_COST_FIELD_LABEL} «Caja x6»: 7500.00"
        in described
    )
    assert references == [
        f"{UNIT_COST_REFERENCE_FIELD_LABEL}: 333.33",
        f"{UNIT_COST_REFERENCE_FIELD_LABEL}: 1250.00",
    ]


def test_the_review_omits_the_reference_without_cost_or_quantity() -> None:
    """
    Verifica que la referencia no se muestra sin costo o cantidad (T058).

    Un costo ausente, una cantidad inicial cero y un stock cerrado cero no
    producen una referencia unitaria; el costo total informado sí se muestra.
    """
    payloads = (
        ProductRegistrationPayload(
            sku="SKU-SIN-COSTO",
            name="Producto sin costos",
            base_unit="botella",
            base_sale_price=Decimal("100"),
        ),
        ProductRegistrationPayload(
            sku="SKU-SIN-CANTIDAD",
            name="Producto sin cantidad",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            initial_loose_units=0,
            initial_loose_cost=Decimal("1000"),
        ),
        ProductRegistrationPayload(
            sku="SKU-CAJA-VACIA",
            name="Producto con caja vacía",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            presentations=(
                InitialPresentationPayload(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("600"),
                    closed_stock=0,
                    cost=Decimal("7500"),
                ),
            ),
        ),
    )

    for payload in payloads:
        described = "\n".join(
            describe_proposal_review(proposal_review(payload))
        )
        assert UNIT_COST_REFERENCE_FIELD_LABEL not in described

    assert "Costo de las unidades sueltas iniciales: no informado" in (
        "\n".join(describe_proposal_review(proposal_review(payloads[0])))
    )
    assert "Costo de las unidades sueltas iniciales: 1000.00" in (
        "\n".join(describe_proposal_review(proposal_review(payloads[1])))
    )
    assert "Costo de la presentación adicional «Caja x6»: 7500.00" in (
        "\n".join(describe_proposal_review(proposal_review(payloads[2])))
    )


def test_editing_a_registration_proposal_updates_the_initial_costs(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica la edición de los dos costos de una propuesta (T058, D3).

    La propuesta conserva su acción y sigue pendiente; editar no escribe.
    """
    proposal = proposal_for(
        ProductRegistrationPayload(
            sku="SKU-COSTOS",
            name="Producto con costos",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            initial_loose_units=2,
            initial_loose_cost=Decimal("100"),
            presentations=(
                InitialPresentationPayload(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("600"),
                    closed_stock=1,
                    cost=Decimal("200"),
                ),
            ),
        )
    )
    app = app_for(make_assistant(connection))
    before = snapshot(connection)

    open_pending_proposals(app, [proposal])
    app.text_input(
        key=proposal_edit_key(proposal, "initial_loose_cost")
    ).set_value("1111.50")
    app.text_input(
        key=proposal_edit_presentation_key(proposal, 1, "cost")
    ).set_value("2222.25")
    proposal_edit_submit(app, proposal)

    edited = manual_proposals_of(app)[0]

    assert not app.exception
    assert edited.action is ProposalAction.PRODUCT_REGISTRATION
    assert edited.status is ProposalStatus.PENDING
    assert edited.payload.initial_loose_cost == Decimal("1111.50")
    assert edited.payload.presentations[0].cost == Decimal("2222.25")
    assert snapshot(connection) == before


def test_a_registration_without_costs_keeps_them_absent_in_the_edit_form(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica la compatibilidad de la edición con propuestas sin costos (T058).

    Los campos nuevos quedan vacíos y volver a enviarlos conserva los costos
    ausentes, en lugar de convertirlos en cero.
    """
    proposal = proposal_for(
        ProductRegistrationPayload(
            sku="SKU-SIN-COSTO",
            name="Producto sin costos",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            initial_loose_units=2,
            presentations=(
                InitialPresentationPayload(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("600"),
                    closed_stock=1,
                ),
            ),
        )
    )
    app = app_for(make_assistant(connection))

    open_pending_proposals(app, [proposal])
    loose_cost = app.text_input(
        key=proposal_edit_key(proposal, "initial_loose_cost")
    )
    presentation_cost = app.text_input(
        key=proposal_edit_presentation_key(proposal, 1, "cost")
    )

    assert not app.exception
    assert loose_cost.value == ""
    assert presentation_cost.value == ""

    proposal_edit_submit(app, proposal)

    edited = manual_proposals_of(app)[0]

    assert edited.payload.initial_loose_cost is None
    assert edited.payload.presentations[0].cost is None


def test_discarding_a_registration_with_costs_does_not_write(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica que el descarte no persiste movimientos ni costos (T058).

    La propuesta descartada sigue en la colección de la sesión y la base no
    cambia; no se crea el producto ni sus movimientos iniciales.
    """
    proposal = proposal_for(
        ProductRegistrationPayload(
            sku="SKU-DESCARTADO",
            name="Producto descartado",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            initial_loose_units=2,
            initial_loose_cost=Decimal("1000"),
            presentations=(
                InitialPresentationPayload(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("600"),
                    closed_stock=1,
                    cost=Decimal("7500"),
                ),
            ),
        )
    )
    app = app_for(make_assistant(connection))
    before = snapshot(connection)
    changes_before = connection.total_changes

    open_pending_proposals(app, [proposal])
    discard_pending_proposal(app, proposal)

    assert not app.exception
    assert manual_proposals_of(app) == [proposal]
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before
