"""Pruebas de integración end-to-end del asistente de inventario (T025).

Recorren el flujo completo de RF-05 sobre la conexión SQLite real del proyecto
(`connect`, con `check_same_thread=False`), el grafo real y un modelo falso
determinista: consulta, propuesta pendiente sin efectos, confirmación explícita
del usuario con el botón de la interfaz, apertura, rechazo sin cambios
parciales y consulta final coherente.

El segundo escenario emite dos llamadas a herramientas en el mismo mensaje del
modelo para comprobar que el `ToolNode` las ejecuta en sus hilos de trabajo
sobre la misma conexión. `check_same_thread=False` habilita ese uso, pero
**no** garantiza por sí solo la seguridad concurrente: las lecturas y las
escrituras siguen serializadas por SQLite y por el bloqueo de escritura de
`BEGIN IMMEDIATE`, y el proyecto mantiene una conexión por sesión.

Todo el estado de las pruebas vive en este archivo (sin `conftest.py` ni
fixtures compartidos): la base es `:memory:` y se crea por prueba, el modelo es
falso y no hay red, ni DeepSeek real, ni datos persistentes.

Cubren además, para T037 sobre RF-08, el flujo del cambio de precio con base en
archivo y ruta configurada —propuesta sin efectos, confirmación de la caja y de
la presentación base, y consulta del historial—, la persistencia del precio y
del historial al cerrar y reabrir la base, y la atomicidad ante un fallo del
registro del historial, con la base utilizable después.

El «reinicio» de T037 es el cierre y la reapertura de la conexión y de la
composición de la aplicación sobre la misma base en archivo: no se reinicia el
proceso de Python, no hay otro proceso y no hay llamadas reales a DeepSeek.

Queda fuera de T025 y corresponde a T026: la matriz de trazabilidad de RF-01 a
RF-07.
"""

import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
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
from app.infrastructure.sqlite.schema import (
    SCHEMA_VERSION,
    create_schema,
    schema_version,
)
from app.schemas.proposals import PriceUpdatePayload, ProposalStatus
from app.services.movements import MovementService
from app.services.ports import MovementRecord, PriceChangeRecord
from app.services.presentations import PresentationService
from app.services.products import (
    InitialClosedStock,
    ProductRegistration,
    ProductService,
)
from app.services.proposals import ProposalService
from app.ui.formatting import format_unit_cost_reference
from app.ui.main import (
    ASSISTANT_KEY,
    CONFIRM_LABEL,
    Assistant,
    answer,
    build_assistant,
)

APP_PATH: Path = (
    Path(__file__).resolve().parents[2] / "app" / "ui" / "main.py"
)
"""Ruta absoluta de la aplicación Streamlit que se ejecuta con `AppTest`."""

INITIAL_LOOSE_UNITS: int = 4
"""Unidades sueltas con las que se registra el producto de prueba."""

INITIAL_CLOSED_STOCK: int = 2
"""Presentaciones cerradas con las que se registra la caja de prueba."""

BOX_FACTOR: int = 6
"""Factor de equivalencia de la presentación cerrada de prueba."""


class ScriptedToolModel(BaseChatModel):
    """
    Modelo falso que emite llamadas a herramientas y responde con los datos
    reales que estas devuelven.

    Cada elemento de `batches` es el conjunto de llamadas que el modelo emite
    en un mismo mensaje, en orden; cuando no quedan, responde con el contenido
    real de los mensajes de herramienta que recibió. Sustituye únicamente al
    proveedor del modelo: las herramientas, los servicios, el grafo y la base
    de datos son los reales.
    """

    batches: list[list[dict[str, Any]]] = Field(default_factory=list)
    """Llamadas a herramientas que el modelo emite, agrupadas por mensaje."""

    received: list[list[BaseMessage]] = Field(default_factory=list)
    """Mensajes recibidos en cada llamada al modelo, en orden."""

    emitted: int = 0
    """Cantidad de mensajes con llamadas ya emitidos."""

    @property
    def _llm_type(self) -> str:
        """Identificador del modelo falso."""
        return "scripted-tool-model"

    def bind_tools(
        self,
        tools: Sequence[Any],
        **kwargs: Any,
    ) -> "ScriptedToolModel":
        """Devuelve el propio modelo: las llamadas del guion ya están fijas."""
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """
        Emite el próximo lote de llamadas o responde con datos reales.

        El modelo emite un lote por turno: cuando el último mensaje es el
        resultado de una herramienta, responde con ese contenido real en lugar
        de pedir otra herramienta.
        """
        self.received.append(list(messages))

        pending_batch = (
            not isinstance(messages[-1], ToolMessage)
            and self.emitted < len(self.batches)
        )
        if pending_batch:
            calls = self.batches[self.emitted]
            self.emitted += 1
            message = AIMessage(content="", tool_calls=list(calls))
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
    """Inventario inicial controlado de la prueba (datos de prueba)."""

    product_id: int
    base_id: int
    box_id: int


def tool_call(
    name: str,
    arguments: dict[str, Any],
    call_id: str,
) -> dict[str, Any]:
    """Construye una llamada a herramienta del modelo falso."""
    return {
        "name": name,
        "args": arguments,
        "id": call_id,
        "type": "tool_call",
    }


def build_scripted_assistant(
    connection: sqlite3.Connection,
    *batches: list[dict[str, Any]],
) -> Assistant:
    """Compone el asistente con el modelo falso y la conexión real."""
    return build_assistant(
        model=ScriptedToolModel(batches=list(batches)),
        connection=connection,
    )


def app_for(assistant: Assistant) -> AppTest:
    """Crea la aplicación de prueba con el asistente inyectado en la sesión."""
    app = AppTest.from_file(str(APP_PATH), default_timeout=30)
    app.session_state[ASSISTANT_KEY] = assistant

    return app


def send_message(app: AppTest, message: str) -> None:
    """Envía un mensaje por el chat de la aplicación y espera el resultado."""
    app.chat_input[0].set_value(message).run()


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


def rendered_markdown(app: AppTest) -> str:
    """Devuelve el texto de todos los elementos Markdown de la aplicación."""
    return "\n".join(element.value for element in app.markdown)


def sale_price_of(
    connection: sqlite3.Connection,
    presentation_id: int,
) -> str:
    """Devuelve el precio de venta persistido de una presentación, en texto."""
    row = connection.execute(
        "SELECT sale_price FROM presentations WHERE id = ?",
        (presentation_id,),
    ).fetchone()

    return str(row["sale_price"])


def price_changes_count(connection: sqlite3.Connection) -> int:
    """Devuelve la cantidad de cambios de precio persistidos."""
    row = connection.execute(
        "SELECT COUNT(*) AS total FROM price_changes"
    ).fetchone()

    return int(row["total"])


def path_state(path: Path) -> tuple[bool, int, float]:
    """Describe una ruta con su existencia, su tamaño y su modificación."""
    if not path.exists():
        return (False, 0, 0.0)

    stat = path.stat()

    return (True, stat.st_size, stat.st_mtime)


def loose_units_of(connection: sqlite3.Connection, product_id: int) -> int:
    """Devuelve las unidades sueltas persistidas del producto."""
    row = connection.execute(
        "SELECT loose_units FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()

    return int(row["loose_units"])


def closed_stock_of(
    connection: sqlite3.Connection,
    presentation_id: int,
) -> int:
    """Devuelve el stock cerrado persistido de una presentación."""
    row = connection.execute(
        "SELECT closed_stock FROM presentations WHERE id = ?",
        (presentation_id,),
    ).fetchone()

    return int(row["closed_stock"])


def total_equivalent_of(
    connection: sqlite3.Connection,
    product_id: int,
) -> int:
    """
    Calcula el total equivalente con los saldos persistidos.

    El cálculo se hace en SQL, con independencia del código de la aplicación:
    `unidades_sueltas + Σ(stock_cerrado × factor)`.
    """
    row = connection.execute(
        """
        SELECT
            p.loose_units + COALESCE(SUM(s.closed_stock * s.factor), 0)
                AS total_equivalent
        FROM products AS p
        LEFT JOIN presentations AS s ON s.product_id = p.id
        WHERE p.id = ?
        GROUP BY p.id
        """,
        (product_id,),
    ).fetchone()

    return int(row["total_equivalent"])


def movements_of(
    connection: sqlite3.Connection,
    product_id: int,
) -> list[sqlite3.Row]:
    """Devuelve los movimientos persistidos del producto, en orden."""
    return connection.execute(
        "SELECT kind, quantity, consumed_quantity FROM movements "
        "WHERE product_id = ? ORDER BY id",
        (product_id,),
    ).fetchall()


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    """
    Abre la conexión real del proyecto sobre una base en memoria (T025).

    Usa `connect()` tal como lo usa la aplicación, con su valor predeterminado
    de `check_same_thread`; cada prueba abre su propia base y no toca datos
    persistentes.
    """
    opened_connection = connect()
    create_schema(opened_connection)
    try:
        yield opened_connection
    finally:
        opened_connection.close()


@pytest.fixture
def presentations(
    connection: sqlite3.Connection,
) -> SqlitePresentationRepository:
    """Repositorio de presentaciones sobre la base en memoria."""
    return SqlitePresentationRepository(connection)


def register_catalog(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
) -> Catalog:
    """
    Registra el inventario inicial de la prueba sobre la conexión indicada.

    Lo usan tanto la base en memoria de T025 como la base en archivo de T037:
    la registración es la misma y deja el producto con sus saldos y sus
    movimientos reales.
    """
    service = ProductService(
        products=SqliteProductRepository(connection),
        presentations=presentations,
        movements=SqliteMovementRepository(connection),
        transaction=partial(transaction, connection),
    )
    stored = service.register(
        ProductRegistration(
            sku="SKU-1",
            name="Producto",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            minimum_stock=3,
            initial_loose_units=INITIAL_LOOSE_UNITS,
            presentations=(
                InitialClosedStock(
                    name="Caja x6",
                    factor=BOX_FACTOR,
                    sale_price=Decimal("600"),
                    closed_stock=INITIAL_CLOSED_STOCK,
                ),
            ),
        )
    )
    base, box = presentations.list_by_product(stored.id)

    return Catalog(product_id=stored.id, base_id=base.id, box_id=box.id)


class _FailingPriceHistoryRepository:
    """Doble local del historial que falla al registrar el cambio (T037)."""

    def add(self, **values: Any) -> PriceChangeRecord:
        """Falla siempre, dentro de la transacción del servicio."""
        raise RuntimeError("Fallo simulado al registrar el cambio de precio.")

    def list_by_presentation(
        self, presentation_id: int
    ) -> list[PriceChangeRecord]:
        """No se usa en la prueba."""
        raise AssertionError("No se esperaba consultar el historial.")

    def list_by_product(self, product_id: int) -> list[PriceChangeRecord]:
        """No se usa en la prueba."""
        raise AssertionError("No se esperaba consultar el historial.")


@pytest.fixture
def catalog(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
) -> Catalog:
    """Registra el producto y la presentación iniciales de la prueba."""
    return register_catalog(connection, presentations)


def test_the_full_flow_keeps_the_inventory_coherent(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Recorre consulta, propuesta, confirmación, apertura y rechazo."""
    product_id = catalog.product_id
    initial_total = INITIAL_LOOSE_UNITS + INITIAL_CLOSED_STOCK * BOX_FACTOR
    assistant = build_scripted_assistant(
        connection,
        [
            tool_call(
                "get_product_stock", {"product_id": product_id}, "call-1"
            )
        ],
        [
            tool_call(
                "propose_loose_entry",
                {"product_id": product_id, "quantity": 3, "reason": "Compra"},
                "call-2",
            )
        ],
        [
            tool_call(
                "propose_aperture",
                {
                    "product_id": product_id,
                    "presentation_id": catalog.box_id,
                    "quantity": 1,
                    "reason": "Apertura",
                },
                "call-3",
            )
        ],
        [
            tool_call(
                "propose_loose_exit",
                {"product_id": product_id, "quantity": 100, "reason": "Venta"},
                "call-4",
            )
        ],
        [
            tool_call(
                "get_product_stock", {"product_id": product_id}, "call-5"
            )
        ],
    )
    app = app_for(assistant)
    app.run()

    # --- Paso 1: consulta con datos reales ---
    send_message(app, "¿Cuánto stock hay?")

    response = assistant.history[-1].state["response"]

    assert not app.exception
    assert response is not None
    assert "SKU-1" in response
    assert "Caja x6" in response
    assert f"loose_units={INITIAL_LOOSE_UNITS}" in response
    assert f"closed_stock={INITIAL_CLOSED_STOCK}" in response
    assert f"total_equivalent={initial_total}" in response
    assert loose_units_of(connection, product_id) == INITIAL_LOOSE_UNITS
    assert closed_stock_of(connection, catalog.box_id) == INITIAL_CLOSED_STOCK
    assert total_equivalent_of(connection, product_id) == initial_total

    # --- Paso 2: la propuesta queda pendiente y no ejecuta nada ---
    before = snapshot(connection)
    changes_before = connection.total_changes

    send_message(app, "Propone una entrada suelta de 3 unidades")

    turn = assistant.history[-1]
    proposal = turn.state["pending_proposal"]

    assert proposal is not None
    assert proposal.status is ProposalStatus.PENDING
    assert proposal.payload.quantity == 3
    assert turn.state["execution_result"] is None
    assert [button.label for button in app.button] == [CONFIRM_LABEL]
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before

    # --- Paso 3: el usuario confirma y la operación se ejecuta ---
    app.button[0].set_value(True).run()

    turn = assistant.history[-1]
    result = turn.state["execution_result"]
    movements = movements_of(connection, product_id)

    assert not app.exception
    assert result is not None
    assert result.status is ProposalStatus.CONFIRMED
    assert isinstance(result.result, MovementRecord)
    assert result.result.kind is MovementKind.LOOSE_ENTRY
    assert result.result.quantity == 3
    assert turn.state["pending_proposal"].status is ProposalStatus.CONFIRMED
    assert loose_units_of(connection, product_id) == 7
    assert closed_stock_of(connection, catalog.box_id) == INITIAL_CLOSED_STOCK
    assert total_equivalent_of(connection, product_id) == 19
    assert [row["kind"] for row in movements] == [
        MovementKind.LOOSE_ENTRY.value,
        MovementKind.CLOSED_ENTRY.value,
        MovementKind.LOOSE_ENTRY.value,
    ]
    assert [row["quantity"] for row in movements] == [4, 2, 3]
    assert len(app.button) == 0

    # --- Paso 4: la apertura convierte stock cerrado sin cambiar el total ---
    send_message(app, "Abrí una caja")
    app.button[0].set_value(True).run()

    turn = assistant.history[-1]
    result = turn.state["execution_result"]
    movements = movements_of(connection, product_id)

    assert not app.exception
    assert result is not None
    assert result.result.kind is MovementKind.APERTURE
    assert closed_stock_of(connection, catalog.box_id) == 1
    assert loose_units_of(connection, product_id) == 7 + BOX_FACTOR
    assert total_equivalent_of(connection, product_id) == 19
    assert [row["kind"] for row in movements][-1] == (
        MovementKind.APERTURE.value
    )
    assert movements[1]["consumed_quantity"] == 1

    # --- Paso 5: el rechazo no deja cambios parciales ---
    before = snapshot(connection)
    changes_before = connection.total_changes

    send_message(app, "Vende cien unidades")
    app.button[0].set_value(True).run()

    turn = assistant.history[-1]

    assert not app.exception
    assert len(app.error) == 1
    assert turn.state["execution_result"] is None
    assert turn.state["pending_proposal"].status is ProposalStatus.PENDING
    assert len(app.button) == 1
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before

    # --- Paso 6: la consulta final refleja los saldos persistidos ---
    send_message(app, "¿Cuánto stock hay ahora?")

    response = assistant.history[-1].state["response"]
    movements = movements_of(connection, product_id)

    assert not app.exception
    assert response is not None
    assert "loose_units=13" in response
    assert "closed_stock=1" in response
    assert "total_equivalent=19" in response
    assert loose_units_of(connection, product_id) == 13
    assert closed_stock_of(connection, catalog.box_id) == 1
    assert total_equivalent_of(connection, product_id) == 19
    assert len(movements) == 4


def test_two_tools_in_one_message_run_on_the_real_connection(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica dos herramientas en el mismo mensaje sobre `connect()` (D2)."""
    product_id = catalog.product_id
    model = ScriptedToolModel(
        batches=[
            [
                tool_call(
                    "get_product_stock", {"product_id": product_id}, "call-1"
                ),
                tool_call(
                    "get_movement_history",
                    {"product_id": product_id},
                    "call-2",
                ),
            ]
        ]
    )
    assistant = build_assistant(model=model, connection=connection)
    initial_total = INITIAL_LOOSE_UNITS + INITIAL_CLOSED_STOCK * BOX_FACTOR

    turn = answer(assistant, "¿Cuánto stock hay y qué movimientos hubo?")

    tool_messages = [
        message
        for message in model.received[-1]
        if isinstance(message, ToolMessage)
    ]
    response = turn.state["response"]

    assert [message.name for message in tool_messages] == [
        "get_product_stock",
        "get_movement_history",
    ]
    assert [message.status for message in tool_messages] == [
        "success",
        "success",
    ]
    assert response is not None
    assert f"total_equivalent={initial_total}" in response
    assert "Stock inicial" in response
    assert turn.state["execution_result"] is None
    assert turn.state["pending_proposal"] is None


# --- Cambio de precio, persistencia y atomicidad (T037, RF-08) ---


def test_the_price_change_and_its_history_survive_a_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verifica el flujo del precio y la persistencia en archivo (T037)."""
    configured_path = tmp_path / "datos" / "inventario.sqlite3"
    monkeypatch.setattr(config, "INVENTORY_DB_PATH", str(configured_path))
    default_before = path_state(config.DEFAULT_DATABASE_PATH)
    seed_connection = connect(config.resolve_database_path())
    create_schema(seed_connection)
    catalog = register_catalog(
        seed_connection, SqlitePresentationRepository(seed_connection)
    )
    seed_connection.close()

    # --- Sesión 1: composición real sobre la base en archivo ---
    assistant = build_assistant(
        model=ScriptedToolModel(
            batches=[
                [
                    tool_call(
                        "get_product_stock",
                        {"product_id": catalog.product_id},
                        "call-1",
                    )
                ],
                [
                    tool_call(
                        "propose_price_update",
                        {
                            "presentation_id": catalog.box_id,
                            "new_price": "700",
                        },
                        "call-2",
                    )
                ],
                [
                    tool_call(
                        "propose_price_update",
                        {
                            "presentation_id": catalog.base_id,
                            "new_price": "120",
                        },
                        "call-3",
                    )
                ],
            ]
        ),
        connection=None,
    )
    app = app_for(assistant)
    app.run()

    # --- Paso 1: consulta inicial con los datos reales ---
    initial_total = INITIAL_LOOSE_UNITS + INITIAL_CLOSED_STOCK * BOX_FACTOR

    send_message(app, "¿Cuánto stock hay?")

    response = assistant.history[-1].state["response"]

    assert not app.exception
    assert response is not None
    assert "SKU-1" in response
    assert f"total_equivalent={initial_total}" in response

    # --- Paso 2: la propuesta de la caja queda pendiente y no escribe ---
    before = snapshot(assistant.connection)
    changes_before = assistant.connection.total_changes
    movements_before = snapshot(assistant.connection)["movements"]

    send_message(app, "Cambiá el precio de la caja a 700")

    turn = assistant.history[-1]
    proposal = turn.state["pending_proposal"]
    rendered = rendered_markdown(app)

    assert proposal is not None
    assert proposal.status is ProposalStatus.PENDING
    assert turn.state["execution_result"] is None
    assert "- precio vigente: 600.00" in rendered
    assert "- precio nuevo: 700.00" in rendered
    assert "sin cambios registrados" in rendered
    assert snapshot(assistant.connection) == before
    assert assistant.connection.total_changes == changes_before

    # --- Paso 3: el usuario confirma el cambio de la caja ---
    app.button[0].set_value(True).run()

    result = assistant.history[-1].state["execution_result"]
    box_changes = SqlitePriceHistoryRepository(
        assistant.connection
    ).list_by_presentation(catalog.box_id)

    assert not app.exception
    assert result is not None
    assert result.status is ProposalStatus.CONFIRMED
    assert sale_price_of(assistant.connection, catalog.box_id) == "700"
    assert [
        (change.previous_price, change.new_price) for change in box_changes
    ] == [(Decimal("600"), Decimal("700"))]
    assert [change.event_type for change in box_changes] == [
        "cambio_precio"
    ]
    assert loose_units_of(
        assistant.connection, catalog.product_id
    ) == INITIAL_LOOSE_UNITS
    assert closed_stock_of(
        assistant.connection, catalog.box_id
    ) == INITIAL_CLOSED_STOCK
    assert snapshot(assistant.connection)["movements"] == movements_before
    assert len(app.button) == 0

    # --- Paso 4: la presentación base (factor 1) admite el cambio ---
    send_message(app, "Cambiá el precio del suelto a 120")
    app.button[0].set_value(True).run()

    base_changes = SqlitePriceHistoryRepository(
        assistant.connection
    ).list_by_presentation(catalog.base_id)

    assert not app.exception
    assert sale_price_of(assistant.connection, catalog.base_id) == "120"
    assert [
        (change.previous_price, change.new_price)
        for change in base_changes
    ] == [(Decimal("100"), Decimal("120"))]
    assert price_changes_count(assistant.connection) == 2

    # --- Paso 5: cierre y reapertura de la misma base en archivo ---
    assistant.connection.close()

    restarted = build_assistant(
        model=ScriptedToolModel(
            batches=[
                [
                    tool_call(
                        "propose_price_update",
                        {
                            "presentation_id": catalog.box_id,
                            "new_price": "750",
                        },
                        "call-4",
                    )
                ]
            ]
        ),
        connection=None,
    )
    try:
        assert restarted.connection is not assistant.connection
        assert schema_version(restarted.connection) == SCHEMA_VERSION
        assert sale_price_of(restarted.connection, catalog.box_id) == "700"
        assert sale_price_of(restarted.connection, catalog.base_id) == "120"

        history = SqlitePriceHistoryRepository(restarted.connection)

        assert [
            (change.previous_price, change.new_price)
            for change in history.list_by_presentation(catalog.box_id)
        ] == [(Decimal("600"), Decimal("700"))]
        assert [
            (change.previous_price, change.new_price)
            for change in history.list_by_presentation(catalog.base_id)
        ] == [(Decimal("100"), Decimal("120"))]
        assert snapshot(restarted.connection)["movements"] == (
            movements_before
        )
        assert loose_units_of(
            restarted.connection, catalog.product_id
        ) == INITIAL_LOOSE_UNITS
        assert closed_stock_of(
            restarted.connection, catalog.box_id
        ) == INITIAL_CLOSED_STOCK

        # --- Paso 6: el historial persistido se consulta desde la UI ---
        restarted_app = app_for(restarted)
        restarted_app.run()
        send_message(restarted_app, "Cambiá el precio de la caja a 750")
        rendered = rendered_markdown(restarted_app)

        assert not restarted_app.exception
        assert "Historial de precios de la presentación (1):" in rendered
        assert "- 600.00 → 700.00 (" in rendered
        assert "cambio_precio" in rendered
        assert sale_price_of(restarted.connection, catalog.box_id) == "700"
        assert price_changes_count(restarted.connection) == 2
    finally:
        restarted.connection.close()

    # --- La base predeterminada del proyecto no se crea ni se modifica ---
    assert path_state(config.DEFAULT_DATABASE_PATH) == default_before
    assert configured_path.is_file()


def test_a_failing_history_rolls_back_and_the_database_stays_usable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verifica la atomicidad del precio y la base utilizable (T037)."""
    configured_path = tmp_path / "datos" / "inventario.sqlite3"
    monkeypatch.setattr(config, "INVENTORY_DB_PATH", str(configured_path))
    connection = connect(config.resolve_database_path())
    create_schema(connection)
    try:
        catalog = register_catalog(
            connection, SqlitePresentationRepository(connection)
        )
        scope = partial(transaction, connection)
        products = SqliteProductRepository(connection)
        presentations = SqlitePresentationRepository(connection)
        movements = SqliteMovementRepository(connection)
        failing_service = ProposalService(
            products=ProductService(
                products=products,
                presentations=presentations,
                movements=movements,
                transaction=scope,
            ),
            presentations=PresentationService(
                products=products,
                presentations=presentations,
                movements=movements,
                price_history=_FailingPriceHistoryRepository(),
                transaction=scope,
            ),
            movements=MovementService(
                products=products,
                presentations=presentations,
                movements=movements,
                transaction=scope,
            ),
        )
        proposal = failing_service.create(
            PriceUpdatePayload(
                presentation_id=catalog.box_id,
                new_price=Decimal("700"),
            )
        )

        with pytest.raises(RuntimeError):
            failing_service.confirm(proposal)

        assert proposal.status is ProposalStatus.PENDING
        assert sale_price_of(connection, catalog.box_id) == "600"
        assert price_changes_count(connection) == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        # --- Una confirmación posterior válida sí aplica y registra ---
        assistant = build_assistant(
            model=ScriptedToolModel(
                batches=[
                    [
                        tool_call(
                            "propose_price_update",
                            {
                                "presentation_id": catalog.box_id,
                                "new_price": "700",
                            },
                            "call-1",
                        )
                    ]
                ]
            ),
            connection=None,
        )
        try:
            app = app_for(assistant)
            app.run()
            send_message(app, "Cambiá el precio de la caja a 700")
            app.button[0].set_value(True).run()

            changes = SqlitePriceHistoryRepository(
                connection
            ).list_by_presentation(catalog.box_id)

            assert not app.exception
            assert sale_price_of(connection, catalog.box_id) == "700"
            assert price_changes_count(connection) == 1
            assert [
                (change.previous_price, change.new_price)
                for change in changes
            ] == [(Decimal("600"), Decimal("700"))]
            assert connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall() == []
        finally:
            assistant.connection.close()
    finally:
        connection.close()


def test_initial_costs_stay_on_their_movements_and_prices_remain_independent(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
) -> None:
    """
    Verifica los costos iniciales frente a la apertura y el precio (T058).

    Los costos se conservan en sus movimientos iniciales, la referencia
    unitaria no se persiste ni altera el importe total y la apertura no crea
    costos ni duplica movimientos. El cambio de precio de venta y su historial
    permanecen independientes de los costos de compra.
    """
    products = SqliteProductRepository(connection)
    movements = SqliteMovementRepository(connection)
    product_service = ProductService(
        products=products,
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )
    movement_service = MovementService(
        products=products,
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )
    presentation_service = PresentationService(
        products=products,
        presentations=presentations,
        movements=movements,
        price_history=SqlitePriceHistoryRepository(connection),
        transaction=partial(transaction, connection),
    )

    stored = product_service.register(
        ProductRegistration(
            sku="SKU-COSTOS",
            name="Producto con costos",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            initial_loose_units=3,
            initial_loose_cost=Decimal("1000"),
            presentations=(
                InitialClosedStock(
                    name="Caja x6",
                    factor=BOX_FACTOR,
                    sale_price=Decimal("600"),
                    closed_stock=1,
                    cost=Decimal("7500"),
                ),
            ),
        )
    )
    _, box = presentations.list_by_product(stored.id)
    initial_movements = movements.list_by_product(stored.id)

    assert [movement.cost for movement in initial_movements] == [
        Decimal("1000"),
        Decimal("7500"),
    ]
    assert format_unit_cost_reference(Decimal("1000"), 3) == "333.33"
    assert (
        format_unit_cost_reference(Decimal("7500"), 1, factor=BOX_FACTOR)
        == "1250.00"
    )
    assert initial_movements[1].cost == Decimal("7500")

    movement_service.register_aperture(
        stored.id,
        presentation_id=box.id,
        quantity=1,
        reason="Apertura",
    )
    after_aperture = movements.list_by_product(stored.id)

    assert len(after_aperture) == 3
    assert after_aperture[-1].kind is MovementKind.APERTURE
    assert after_aperture[-1].cost is None
    assert [movement.cost for movement in after_aperture] == [
        Decimal("1000"),
        Decimal("7500"),
        None,
    ]

    presentation_service.change_sale_price(box.id, Decimal("700"))
    changes = SqlitePriceHistoryRepository(connection).list_by_presentation(
        box.id
    )
    updated_box = presentations.get(box.id)

    assert updated_box is not None
    assert updated_box.sale_price == Decimal("700")
    assert [
        (change.previous_price, change.new_price) for change in changes
    ] == [(Decimal("600"), Decimal("700"))]
    assert [
        movement.cost
        for movement in movements.list_by_product(stored.id)
    ] == [Decimal("1000"), Decimal("7500"), None]

    columns = {
        row["name"]
        for row in connection.execute(
            "PRAGMA table_info(movements)"
        ).fetchall()
    }
    assert "unit_cost_reference" not in columns
