"""Pruebas de integración de los contadores FIFO (corrección I2 de T009).

Cubren la actualización controlada de `consumed_quantity` de una entrada
cerrada (RF-03): la operación solo reemplaza el contador de consumo, sin editar
los datos históricos del movimiento confirmado ni su contador de
compensaciones, y respeta el límite `consumida + compensada <= cantidad` del
esquema. También verifican el orden FIFO que el dominio define para el consumo,
los rechazos y la reversión de una operación fallida.
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.domain.movements import (
    ClosedEntry,
    MovementKind,
    consume_closed_entries_fifo,
)
from app.infrastructure.sqlite.connection import (
    IN_MEMORY_DATABASE,
    connect,
    transaction,
)
from app.infrastructure.sqlite.repositories import (
    NotAClosedEntryError,
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
    RecordNotFoundError,
)

START: datetime = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    """Abre una base en memoria con el esquema creado desde cero."""
    opened_connection = connect(IN_MEMORY_DATABASE)
    create_schema(opened_connection)
    try:
        yield opened_connection
    finally:
        opened_connection.close()


@pytest.fixture
def movements(connection: sqlite3.Connection) -> SqliteMovementRepository:
    """Repositorio de movimientos sobre la base en memoria."""
    return SqliteMovementRepository(connection)


def register_product_and_presentation(
    connection: sqlite3.Connection,
) -> tuple[ProductRecord, PresentationRecord]:
    """Registra un producto con una presentación cerrada."""
    products = SqliteProductRepository(connection)
    presentations = SqlitePresentationRepository(connection)
    product = products.add(sku="SKU-1", name="Producto", base_unit="unidad")
    presentation = presentations.add(
        product_id=product.id,
        name="Caja x6",
        factor=6,
        sale_price=Decimal("1200.00"),
    )

    return product, presentation


def register_closed_entry(
    movements: SqliteMovementRepository,
    product_id: int,
    presentation_id: int,
    *,
    quantity: int = 1,
    reason: str = "Compra",
    occurred_at: datetime = START,
    **extra: Any,
) -> MovementRecord:
    """Registra una entrada cerrada válida y devuelve su versión persistida."""
    return movements.add(
        product_id=product_id,
        presentation_id=presentation_id,
        kind=MovementKind.CLOSED_ENTRY,
        quantity=quantity,
        reason=reason,
        occurred_at=occurred_at,
        **extra,
    )


def set_compensated_quantity(
    connection: sqlite3.Connection, movement_id: int, compensated_quantity: int
) -> None:
    """
    Simula una compensación previa escribiendo el contador de compensaciones.

    El servicio de compensaciones corresponde a T014 y todavía no existe; la
    escritura directa permite preparar el límite que `set_consumed_quantity`
    debe respetar junto con la cantidad original de la entrada.
    """
    connection.execute(
        "UPDATE movements SET compensated_quantity = ? WHERE id = ?",
        (compensated_quantity, movement_id),
    )


def stored_row(
    connection: sqlite3.Connection, movement_id: int
) -> dict[str, Any]:
    """Devuelve la fila persistida del movimiento como diccionario."""
    row = connection.execute(
        "SELECT * FROM movements WHERE id = ?", (movement_id,)
    ).fetchone()
    assert row is not None

    return dict(row)


def closed_entries(
    movements: SqliteMovementRepository, product_id: int
) -> list[ClosedEntry]:
    """Reconstruye las entradas cerradas vigentes del producto."""
    return [
        ClosedEntry(
            entry_id=movement.id,
            presentation_id=movement.presentation_id,
            quantity=movement.quantity,
            consumed_quantity=movement.consumed_quantity,
            compensated_quantity=movement.compensated_quantity,
        )
        for movement in movements.list_by_product(
            product_id, kind=MovementKind.CLOSED_ENTRY
        )
    ]


# --- Actualización válida y preservación de los datos históricos ---


def test_update_consumed_quantity_persists_the_new_value(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el contador de consumo se actualiza y se persiste."""
    product, presentation = register_product_and_presentation(connection)
    entry = register_closed_entry(
        movements, product.id, presentation.id, quantity=5
    )

    updated = movements.set_consumed_quantity(entry.id, 2)

    assert updated.consumed_quantity == 2
    assert stored_row(connection, entry.id)["consumed_quantity"] == 2
    assert movements.get(entry.id) == updated


def test_update_preserves_every_other_field_and_the_compensated_counter(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que solo cambia `consumed_quantity` entre los campos."""
    product, presentation = register_product_and_presentation(connection)
    entry = register_closed_entry(
        movements,
        product.id,
        presentation.id,
        quantity=5,
        reason="Carga inicial",
        cost=Decimal("0.30"),
        is_initial_stock=True,
    )
    set_compensated_quantity(connection, entry.id, 1)
    before_record = movements.get(entry.id)
    before_row = stored_row(connection, entry.id)

    updated = movements.set_consumed_quantity(entry.id, 2)

    assert before_record is not None
    assert stored_row(connection, entry.id) == {
        **before_row,
        "consumed_quantity": 2,
    }
    assert updated == replace(before_record, consumed_quantity=2)
    assert updated.product_id == product.id
    assert updated.presentation_id == presentation.id
    assert updated.kind is MovementKind.CLOSED_ENTRY
    assert updated.quantity == 5
    assert updated.reason == "Carga inicial"
    assert updated.occurred_at == START
    assert updated.cost == Decimal("0.30")
    assert updated.applied_price is None
    assert updated.is_initial_stock is True
    assert updated.original_movement_id is None
    assert updated.compensated_quantity == 1


def test_update_accepts_the_same_consumed_quantity(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que volver a informar el mismo contador no se rechaza."""
    product, presentation = register_product_and_presentation(connection)
    entry = register_closed_entry(
        movements, product.id, presentation.id, quantity=3
    )
    movements.set_consumed_quantity(entry.id, 1)

    updated = movements.set_consumed_quantity(entry.id, 1)

    assert updated.consumed_quantity == 1
    assert stored_row(connection, entry.id)["consumed_quantity"] == 1


# --- Orden FIFO del consumo (integración con el dominio) ---


def test_fifo_consumption_is_persisted_oldest_first(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el consumo FIFO se persiste desde la más antigua."""
    product, presentation = register_product_and_presentation(connection)
    oldest = register_closed_entry(
        movements, product.id, presentation.id, quantity=2
    )
    newest = register_closed_entry(
        movements,
        product.id,
        presentation.id,
        quantity=3,
        occurred_at=START + timedelta(hours=1),
    )

    result = consume_closed_entries_fifo(
        closed_entries(movements, product.id),
        presentation_id=presentation.id,
        quantity=3,
    )
    for entry in result.entries:
        movements.set_consumed_quantity(
            entry.entry_id, entry.consumed_quantity
        )

    assert oldest.id < newest.id
    assert [consumption.entry_id for consumption in result.consumptions] == [
        oldest.id,
        newest.id,
    ]
    assert [consumption.quantity for consumption in result.consumptions] == [
        2,
        1,
    ]
    oldest_stored = movements.get(oldest.id)
    newest_stored = movements.get(newest.id)
    assert oldest_stored is not None
    assert newest_stored is not None
    assert oldest_stored.consumed_quantity == 2
    assert newest_stored.consumed_quantity == 1


# --- Límites del contador ---


def test_update_rejects_a_consumed_quantity_that_exceeds_the_entry(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que se rechaza superar `consumida + compensada <= cantidad`."""
    product, presentation = register_product_and_presentation(connection)
    entry = register_closed_entry(
        movements, product.id, presentation.id, quantity=3
    )
    set_compensated_quantity(connection, entry.id, 1)

    with pytest.raises(ValueError):
        movements.set_consumed_quantity(entry.id, 3)

    assert stored_row(connection, entry.id)["consumed_quantity"] == 0


def test_update_keeps_the_entry_limit_after_a_valid_update(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el límite se conserva tras una actualización válida."""
    product, presentation = register_product_and_presentation(connection)
    entry = register_closed_entry(
        movements, product.id, presentation.id, quantity=3
    )
    set_compensated_quantity(connection, entry.id, 1)

    updated = movements.set_consumed_quantity(entry.id, 2)

    assert updated.consumed_quantity == 2
    with pytest.raises(ValueError):
        movements.set_consumed_quantity(entry.id, 3)
    assert stored_row(connection, entry.id)["consumed_quantity"] == 2


# --- Rechazos ---


def test_update_requires_an_existing_movement(
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que actualizar un movimiento inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        movements.set_consumed_quantity(999, 1)


@pytest.mark.parametrize(
    "kind",
    [
        MovementKind.CLOSED_EXIT,
        MovementKind.LOOSE_ENTRY,
        MovementKind.LOOSE_EXIT,
        MovementKind.APERTURE,
    ],
)
def test_update_rejects_a_movement_that_is_not_a_closed_entry(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
    kind: MovementKind,
) -> None:
    """Verifica que solo las entradas cerradas admiten consumo FIFO."""
    product, presentation = register_product_and_presentation(connection)
    movement = movements.add(
        product_id=product.id,
        presentation_id=presentation.id,
        kind=kind,
        quantity=2,
        reason="Movimiento",
        occurred_at=START,
    )

    with pytest.raises(NotAClosedEntryError):
        movements.set_consumed_quantity(movement.id, 1)

    assert stored_row(connection, movement.id)["consumed_quantity"] == 0


def test_update_rejects_a_compensation(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que una compensación no admite consumo FIFO."""
    product, presentation = register_product_and_presentation(connection)
    original = register_closed_entry(
        movements, product.id, presentation.id, quantity=2
    )
    compensation = movements.add(
        product_id=product.id,
        presentation_id=presentation.id,
        kind=MovementKind.COMPENSATION,
        quantity=2,
        reason="Compensación",
        occurred_at=START + timedelta(hours=1),
        original_movement_id=original.id,
    )

    with pytest.raises(NotAClosedEntryError):
        movements.set_consumed_quantity(compensation.id, 1)

    assert stored_row(connection, compensation.id)["consumed_quantity"] == 0
    assert stored_row(connection, original.id)["consumed_quantity"] == 0


@pytest.mark.parametrize("consumed_quantity", [-1, -5])
def test_update_rejects_a_negative_consumed_quantity(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
    consumed_quantity: int,
) -> None:
    """Verifica que una cantidad consumida negativa se rechaza."""
    product, presentation = register_product_and_presentation(connection)
    entry = register_closed_entry(
        movements, product.id, presentation.id, quantity=3
    )

    with pytest.raises(ValueError):
        movements.set_consumed_quantity(entry.id, consumed_quantity)

    assert stored_row(connection, entry.id)["consumed_quantity"] == 0


@pytest.mark.parametrize("consumed_quantity", [1.5, "1", None, True, [1]])
def test_update_rejects_a_consumed_quantity_that_is_not_an_integer(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
    consumed_quantity: object,
) -> None:
    """Verifica que una cantidad consumida que no es entero se rechaza."""
    product, presentation = register_product_and_presentation(connection)
    entry = register_closed_entry(
        movements, product.id, presentation.id, quantity=3
    )

    with pytest.raises(ValueError):
        movements.set_consumed_quantity(
            entry.id,
            consumed_quantity,  # type: ignore[arg-type]
        )

    assert stored_row(connection, entry.id)["consumed_quantity"] == 0


def test_ineligible_movement_error_is_a_value_error() -> None:
    """Verifica que el rechazo por tipo se informa como `ValueError`."""
    assert issubclass(NotAClosedEntryError, ValueError)


# --- Reversión de una operación fallida ---


def test_failed_transaction_rolls_back_the_counter_update(
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que un fallo posterior revierte el contador actualizado."""
    product, presentation = register_product_and_presentation(connection)
    entry = register_closed_entry(
        movements, product.id, presentation.id, quantity=4
    )

    with pytest.raises(RuntimeError):
        with transaction(connection):
            movements.set_consumed_quantity(entry.id, 3)
            assert stored_row(connection, entry.id)["consumed_quantity"] == 3
            raise RuntimeError("Fallo simulado tras la actualización.")

    assert stored_row(connection, entry.id)["consumed_quantity"] == 0
    assert movements.get(entry.id) == entry
