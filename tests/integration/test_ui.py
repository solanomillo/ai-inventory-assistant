"""Pruebas de integración de la interfaz con la persistencia real (T023).

Ejercitan una herramienta real a través del grafo del asistente con la conexión
SQLite real del proyecto (`connect`), que permite el uso desde los hilos de
trabajo del `ToolNode`: si la conexión no admitiera esos hilos, la consulta
fallaría con `sqlite3.ProgrammingError`. La base es en memoria y el modelo es
falso, de modo que la prueba no usa red ni DeepSeek y no toca datos
persistentes.

Queda fuera de T023 y corresponde a T024 y T025: la confirmación desde la
interfaz y la validación end-to-end de RF-01 a RF-07.
"""

import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult

from app.infrastructure.sqlite.connection import connect, transaction
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.schemas.proposals import ProposalStatus
from app.services.products import (
    InitialClosedStock,
    ProductRegistration,
    ProductService,
)
from app.ui.main import (
    ALREADY_CONFIRMED_MESSAGE,
    answer,
    build_assistant,
    confirm_pending,
)


@dataclass(frozen=True)
class Catalog:
    """Inventario controlado de la prueba (datos de prueba, no de la app)."""

    product_id: int


class RelayChatModel(BaseChatModel):
    """
    Modelo falso que solicita una herramienta y responde con su resultado.

    Sustituye únicamente al proveedor del modelo: la conexión, los
    repositorios, los servicios, las herramientas y el grafo son los reales.
    """

    tool_call: dict[str, Any]
    """Llamada a herramienta que el modelo emite una vez."""

    emitted: bool = False
    """Indica si la llamada a herramienta ya se emitió."""

    @property
    def _llm_type(self) -> str:
        """Identificador del modelo falso."""
        return "relay-chat-model"

    def bind_tools(
        self,
        tools: Sequence[Any],
        **kwargs: Any,
    ) -> "RelayChatModel":
        """Devuelve el propio modelo: la llamada del guion ya está fija."""
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Emite la llamada pendiente o responde con el resultado real."""
        if self.emitted:
            message = AIMessage(content=self._tool_results(messages))
        else:
            self.emitted = True
            message = AIMessage(content="", tool_calls=[self.tool_call])

        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _tool_results(messages: Sequence[BaseMessage]) -> str:
        """Devuelve el contenido real de los mensajes de herramienta."""
        return "\n".join(
            str(message.content)
            for message in messages
            if isinstance(message, ToolMessage)
        )


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    """
    Abre la conexión real del proyecto sobre una base en memoria (T023).

    No ajusta `check_same_thread` en la prueba: usa el valor de `connect`, que
    es lo que se está verificando.
    """
    opened_connection = connect()
    create_schema(opened_connection)
    try:
        yield opened_connection
    finally:
        opened_connection.close()


@pytest.fixture
def catalog(connection: sqlite3.Connection) -> Catalog:
    """Registra un producto con stock suelto, cerrado y mínimo reales."""
    service = ProductService(
        products=SqliteProductRepository(connection),
        presentations=SqlitePresentationRepository(connection),
        movements=SqliteMovementRepository(connection),
        transaction=partial(transaction, connection),
    )
    stored = service.register(
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

    return Catalog(product_id=stored.id)


def movement_count(connection: sqlite3.Connection) -> int:
    """Devuelve la cantidad de movimientos persistidos."""
    row = connection.execute(
        "SELECT COUNT(*) AS total FROM movements"
    ).fetchone()

    return int(row["total"])


def loose_units_of(connection: sqlite3.Connection, product_id: int) -> int:
    """Devuelve las unidades sueltas persistidas del producto."""
    row = connection.execute(
        "SELECT loose_units FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()

    return int(row["loose_units"])


def test_confirming_a_real_proposal_changes_the_database_once(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica una confirmación real y la conexión del proyecto (T024)."""
    model = RelayChatModel(
        tool_call={
            "name": "propose_loose_entry",
            "args": {
                "product_id": catalog.product_id,
                "quantity": 3,
                "reason": "Compra",
            },
            "id": "call-1",
            "type": "tool_call",
        }
    )
    assistant = build_assistant(model=model, connection=connection)
    turn = answer(assistant, "Propone una entrada suelta")
    movements_before = movement_count(connection)
    changes_before = connection.total_changes

    error = confirm_pending(assistant, turn)

    result = turn.state["execution_result"]

    assert error is None
    assert result is not None
    assert result.status is ProposalStatus.CONFIRMED
    assert movement_count(connection) == movements_before + 1
    assert loose_units_of(connection, catalog.product_id) == 7
    assert connection.total_changes > changes_before

    changes_after = connection.total_changes
    assert confirm_pending(assistant, turn) == ALREADY_CONFIRMED_MESSAGE
    assert movement_count(connection) == movements_before + 1
    assert loose_units_of(connection, catalog.product_id) == 7
    assert connection.total_changes == changes_after


def test_a_real_tool_runs_through_the_graph_with_the_real_connection(
    connection: sqlite3.Connection,
    catalog: Catalog,
) -> None:
    """Verifica una herramienta real en el ToolNode con `connect()` (T023)."""
    model = RelayChatModel(
        tool_call={
            "name": "get_product_stock",
            "args": {"product_id": catalog.product_id},
            "id": "call-1",
            "type": "tool_call",
        }
    )
    assistant = build_assistant(model=model, connection=connection)

    turn = answer(assistant, "¿Cuánto stock hay?")

    response = turn.state["response"]
    assert response is not None
    assert "SKU-1" in response
    assert "Caja x6" in response
    assert "total_equivalent=16" in response
    assert turn.state["execution_result"] is None
    assert turn.state["pending_proposal"] is None
