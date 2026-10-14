"""Pruebas de integración del servicio de compensaciones (T014).

Cubren los criterios de aceptación de T014 sobre RF-03: la compensación parcial
y total de una entrada cerrada con su contador FIFO, las compensaciones
sucesivas y el rechazo del exceso acumulado, las compensaciones de entradas y
salidas sueltas, el rechazo por stock insuficiente, el rechazo de las salidas
cerradas, las aperturas y las compensaciones encadenadas, las compensaciones
históricas de productos desactivados, las validaciones de entrada, los campos
del movimiento compensatorio, la inmutabilidad del movimiento original y la
reversión completa ante un fallo intermedio.

Las pruebas usan la base SQLite en memoria con el esquema real y los
repositorios de T009; el estado de los productos se prepara con el servicio de
productos de T011 y el saldo disponible con las entradas, salidas y aperturas
del servicio de movimientos de T012 y T013.
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import partial
from typing import Any

import pytest

from app.domain.movements import (
    ApertureNotCompensableError,
    ChainedCompensationError,
    ExcessCompensationError,
    InsufficientMovementStockError,
    MovementKind,
    NotCompensableError,
)
from app.domain.quantities import QuantityError
from app.infrastructure.sqlite.connection import (
    IN_MEMORY_DATABASE,
    connect,
    transaction,
)
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.services.movements import (
    InvalidMovementReasonError,
    MovementService,
)
from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
    RecordNotFoundError,
)
from app.services.products import (
    InitialClosedStock,
    ProductRegistration,
    ProductService,
)

EARLIER: datetime = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
LATER: datetime = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)


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


def make_registration(**overrides: Any) -> ProductRegistration:
    """Construye un alta válida con los valores indicados."""
    values: dict[str, Any] = {
        "sku": "SKU-1",
        "name": "Producto",
        "base_unit": "unidad",
        "base_sale_price": Decimal("100.00"),
    }
    values.update(overrides)

    return ProductRegistration(**values)


def make_closed_presentation(
    *,
    name: str = "Caja x6",
    factor: int = 6,
    sale_price: str = "600.00",
    closed_stock: int = 0,
) -> InitialClosedStock:
    """Declara una presentación adicional con su stock inicial cerrado."""
    return InitialClosedStock(
        name=name,
        factor=factor,
        sale_price=Decimal(sale_price),
        closed_stock=closed_stock,
    )


def prepare_product(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
    *,
    sku: str = "SKU-1",
    loose_units: int = 0,
    closed_stock: int = 0,
    factor: int = 6,
    box_price: str = "600.00",
) -> tuple[ProductRecord, PresentationRecord, PresentationRecord]:
    """
    Prepara un producto con su presentación base, una caja y saldo disponible.

    El saldo se carga con entradas ordinarias del servicio, de modo que el
    producto quede con movimientos reales y una entrada cerrada compensable.
    Devuelve el producto, su presentación base y su presentación cerrada.
    """
    stored = product_service.register(
        make_registration(
            sku=sku,
            presentations=(
                make_closed_presentation(
                    factor=factor, sale_price=box_price
                ),
            ),
        )
    )
    base, box = presentations.list_by_product(stored.id)
    if loose_units:
        movement_service.register_loose_entry(
            stored.id, quantity=loose_units, reason="Compra"
        )
    if closed_stock:
        movement_service.register_closed_entry(
            stored.id,
            presentation_id=box.id,
            quantity=closed_stock,
            reason="Compra",
        )

    return stored, base, box


def add_closed_entry(
    movements: SqliteMovementRepository,
    *,
    product_id: int,
    presentation_id: int,
    quantity: int,
    reason: str,
    occurred_at: datetime,
    **extra: Any,
) -> MovementRecord:
    """
    Registra una entrada cerrada con un instante explícito.

    Permite preparar entradas con identificadores, instantes y datos
    históricos distintos, que el servicio no permite fechar ni completar.
    """
    return movements.add(
        product_id=product_id,
        presentation_id=presentation_id,
        kind=MovementKind.CLOSED_ENTRY,
        quantity=quantity,
        reason=reason,
        occurred_at=occurred_at,
        **extra,
    )


def add_two_closed_entries(
    stored: ProductRecord,
    box: PresentationRecord,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> tuple[MovementRecord, MovementRecord]:
    """
    Registra dos entradas cerradas de una presentación con instantes distintos.

    Devuelve la entrada más antigua y la más reciente, con el saldo cerrado de
    la presentación actualizado a la cantidad total de ambas.
    """
    oldest = add_closed_entry(
        movements,
        product_id=stored.id,
        presentation_id=box.id,
        quantity=2,
        reason="Compra A",
        occurred_at=EARLIER,
    )
    newest = add_closed_entry(
        movements,
        product_id=stored.id,
        presentation_id=box.id,
        quantity=1,
        reason="Compra B",
        occurred_at=LATER,
    )
    presentations.set_closed_stock(box.id, 3)

    return oldest, newest


def movement_of(
    movements: SqliteMovementRepository,
    product_id: int,
    kind: MovementKind,
) -> MovementRecord:
    """Devuelve el primer movimiento del producto con el tipo indicado."""
    records = movements.list_by_product(product_id, kind)
    assert records

    return records[0]


def stored_product(
    products: SqliteProductRepository, product_id: int
) -> ProductRecord:
    """Devuelve el producto persistido y exige que exista."""
    product = products.get(product_id)
    assert product is not None

    return product


def stored_presentation(
    presentations: SqlitePresentationRepository, presentation_id: int
) -> PresentationRecord:
    """Devuelve la presentación persistida y exige que exista."""
    presentation = presentations.get(presentation_id)
    assert presentation is not None

    return presentation


def stored_movement(
    movements: SqliteMovementRepository, movement_id: int
) -> MovementRecord:
    """Devuelve el movimiento persistido y exige que exista."""
    movement = movements.get(movement_id)
    assert movement is not None

    return movement


def stored_row(
    connection: sqlite3.Connection, movement_id: int
) -> dict[str, Any]:
    """Devuelve la fila persistida del movimiento como diccionario."""
    row = connection.execute(
        "SELECT * FROM movements WHERE id = ?", (movement_id,)
    ).fetchone()
    assert row is not None

    return dict(row)


class _FailingRepository:
    """
    Envuelve un repositorio y falla al ejecutar la operación indicada.

    La operación delegada se ejecuta primero, de modo que el error ocurra
    después de que la escritura se aplicó: así se verifica que la transacción
    revierte el movimiento y el saldo o el contador ya escritos.
    """

    def __init__(self, delegate: Any, failing_method: str) -> None:
        self._delegate = delegate
        self._failing_method = failing_method

    def __getattr__(self, name: str) -> Any:
        """Delega el atributo y hace fallar la operación indicada."""
        attribute = getattr(self._delegate, name)
        if name != self._failing_method:
            return attribute

        def failing_operation(*args: Any, **kwargs: Any) -> Any:
            attribute(*args, **kwargs)
            raise RuntimeError(f"Fallo simulado en '{name}'.")

        return failing_operation


# --- Compensación de una entrada cerrada (criterios 1, 5 y 6) ---


def test_a_partial_compensation_reduces_the_closed_stock(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica una compensación parcial de una entrada cerrada (RF-03)."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=3,
    )
    original = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)

    movement = movement_service.register_compensation(
        stored.id,
        movement_id=original.id,
        quantity=1,
        reason="  Corrección  ",
    )

    assert movement.kind is MovementKind.COMPENSATION
    assert movement.product_id == stored.id
    assert movement.presentation_id == box.id
    assert movement.quantity == 1
    assert movement.reason == "Corrección"
    assert movement.original_movement_id == original.id
    assert stored_presentation(presentations, box.id).closed_stock == 2
    stored_original = stored_movement(movements, original.id)
    assert stored_original.compensated_quantity == 1
    assert stored_original.consumed_quantity == 0
    assert stored_original.quantity == 3
    assert movements.list_by_product(stored.id)[-1] == movement


def test_a_total_compensation_leaves_no_pending_quantity(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica una compensación total de una entrada cerrada (RF-03)."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=2,
    )
    original = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)

    movement = movement_service.register_compensation(
        stored.id,
        movement_id=original.id,
        quantity=2,
        reason="Anulación",
    )

    assert movement.quantity == 2
    assert movement.original_movement_id == original.id
    assert stored_presentation(presentations, box.id).closed_stock == 0
    stored_original = stored_movement(movements, original.id)
    assert stored_original.compensated_quantity == 2
    assert stored_original.quantity == 2


def test_successive_compensations_reach_the_total_and_the_excess_is_rejected(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica las parciales sucesivas y el rechazo del exceso acumulado."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=3,
    )
    original = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)

    first = movement_service.register_compensation(
        stored.id, movement_id=original.id, quantity=1, reason="Corrección A"
    )
    second = movement_service.register_compensation(
        stored.id, movement_id=original.id, quantity=2, reason="Corrección B"
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(ExcessCompensationError):
        movement_service.register_compensation(
            stored.id,
            movement_id=original.id,
            quantity=1,
            reason="Corrección C",
        )

    assert [record.id for record in history_before] == [
        original.id,
        first.id,
        second.id,
    ]
    assert stored_movement(movements, original.id).compensated_quantity == 3
    assert stored_presentation(presentations, box.id).closed_stock == 0
    assert movements.list_by_product(stored.id) == history_before
    assert len(connection.execute("SELECT id FROM movements").fetchall()) == 3


def test_a_closed_entry_is_limited_by_its_pending_fifo_quantity(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """
    Verifica el límite FIFO del criterio de aceptación de RF-03.

    Con una entrada cerrada A de 2, una apertura de 1 y una entrada cerrada B
    de 1, la cantidad compensable de A es 1: compensar A por 2 se rechaza y
    compensar A por 1 se acepta.
    """
    stored, _, box = prepare_product(
        product_service, presentations, movement_service
    )
    oldest, newest = add_two_closed_entries(
        stored, box, presentations, movements
    )
    movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=1, reason="Apertura"
    )

    with pytest.raises(ExcessCompensationError):
        movement_service.register_compensation(
            stored.id,
            movement_id=oldest.id,
            quantity=2,
            reason="Corrección",
        )

    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_movement(movements, oldest.id).consumed_quantity == 1

    movement = movement_service.register_compensation(
        stored.id, movement_id=oldest.id, quantity=1, reason="Corrección"
    )

    assert movement.quantity == 1
    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_movement(movements, oldest.id).compensated_quantity == 1
    assert stored_movement(movements, newest.id).compensated_quantity == 0


# --- Compensación de entradas y salidas sueltas (criterio 6) ---


def test_a_loose_entry_compensation_reduces_the_loose_units(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica la compensación de una entrada suelta (RF-03)."""
    stored, base, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=5,
    )
    original = movement_of(movements, stored.id, MovementKind.LOOSE_ENTRY)

    movement = movement_service.register_compensation(
        stored.id, movement_id=original.id, quantity=2, reason="Devolución"
    )

    assert movement.kind is MovementKind.COMPENSATION
    assert movement.presentation_id == base.id
    assert movement.quantity == 2
    assert movement.original_movement_id == original.id
    assert stored_product(products, stored.id).loose_units == 3
    stored_original = stored_movement(movements, original.id)
    assert stored_original.compensated_quantity == 0
    assert stored_original.quantity == 5


def test_a_loose_exit_compensation_restores_the_loose_units(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica la compensación de una salida suelta y su límite (RF-03)."""
    stored, _, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=5,
    )
    exit_movement = movement_service.register_loose_exit(
        stored.id, quantity=2, reason="Venta"
    )
    assert stored_product(products, stored.id).loose_units == 3

    movement = movement_service.register_compensation(
        stored.id,
        movement_id=exit_movement.id,
        quantity=2,
        reason="Devolución",
    )

    assert movement.presentation_id == exit_movement.presentation_id
    assert movement.quantity == 2
    assert movement.original_movement_id == exit_movement.id
    assert stored_product(products, stored.id).loose_units == 5
    assert stored_movement(movements, exit_movement.id).quantity == 2

    with pytest.raises(ExcessCompensationError):
        movement_service.register_compensation(
            stored.id,
            movement_id=exit_movement.id,
            quantity=1,
            reason="Devolución",
        )

    assert stored_product(products, stored.id).loose_units == 5


def test_a_loose_entry_compensation_without_stock_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica el rechazo de una compensación sin stock suelto suficiente."""
    stored, _, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=5,
    )
    original = movement_of(movements, stored.id, MovementKind.LOOSE_ENTRY)
    movement_service.register_loose_exit(
        stored.id, quantity=4, reason="Venta"
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(InsufficientMovementStockError):
        movement_service.register_compensation(
            stored.id,
            movement_id=original.id,
            quantity=5,
            reason="Corrección",
        )

    assert stored_product(products, stored.id).loose_units == 1
    assert movements.list_by_product(stored.id) == history_before


def test_a_loose_entry_pending_quantity_only_counts_its_own_compensations(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """
    Verifica que la pendiente de una entrada suelta no compute otras
    compensaciones del producto (RF-03).

    Con dos entradas sueltas del mismo producto (4 y 3 unidades), compensar 2
    unidades de la primera no reduce la pendiente de la segunda: la segunda
    admite una compensación por su cantidad completa. La pendiente se calcula
    solo con las compensaciones vinculadas al original mediante
    `original_movement_id`; contar las compensaciones de otro movimiento del
    producto reduciría indebidamente el límite compensable.
    """
    stored, _, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=4,
    )
    original = movement_of(movements, stored.id, MovementKind.LOOSE_ENTRY)
    other = movement_service.register_loose_entry(
        stored.id, quantity=3, reason="Compra B"
    )
    assert stored_product(products, stored.id).loose_units == 7

    movement_service.register_compensation(
        stored.id, movement_id=original.id, quantity=2, reason="Devolución A"
    )
    assert stored_product(products, stored.id).loose_units == 5

    second = movement_service.register_compensation(
        stored.id, movement_id=other.id, quantity=3, reason="Devolución B"
    )

    assert second.original_movement_id == other.id
    assert second.quantity == 3
    assert stored_product(products, stored.id).loose_units == 2
    assert stored_movement(movements, original.id) == original
    assert stored_movement(movements, other.id) == other

    history_before = movements.list_by_product(stored.id)

    with pytest.raises(ExcessCompensationError):
        movement_service.register_compensation(
            stored.id, movement_id=other.id, quantity=1, reason="Devolución C"
        )

    assert stored_product(products, stored.id).loose_units == 2
    assert movements.list_by_product(stored.id) == history_before
    assert stored_movement(movements, original.id) == original
    assert stored_movement(movements, other.id) == other
    assert len(history_before) == 4
    assert (
        len(connection.execute("SELECT id FROM movements").fetchall()) == 4
    )


# --- Rechazo por tipo de movimiento original (criterios 2 y 3) ---


def test_a_closed_exit_compensation_is_rejected_without_changes(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """
    Verifica que la compensación de una salida cerrada se rechaza (MVP).

    El rechazo no crea movimientos ni modifica el stock cerrado ni los
    contadores FIFO de las entradas cerradas consumidas.
    """
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=3,
    )
    entry = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)
    closed_exit = movement_service.register_closed_exit(
        stored.id, presentation_id=box.id, quantity=1, reason="Venta"
    )
    entry_before = stored_movement(movements, entry.id)
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(NotCompensableError):
        movement_service.register_compensation(
            stored.id,
            movement_id=closed_exit.id,
            quantity=1,
            reason="Corrección",
        )

    assert entry_before.consumed_quantity == 1
    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_movement(movements, entry.id) == entry_before
    stored_exit = stored_movement(movements, closed_exit.id)
    assert stored_exit.compensated_quantity == 0
    assert stored_exit.consumed_quantity == 0
    assert movements.list_by_product(stored.id) == history_before


def test_an_aperture_compensation_is_rejected_without_changes(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que una apertura no puede compensarse (RF-03)."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=2,
    )
    entry = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)
    aperture = movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=1, reason="Apertura"
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(ApertureNotCompensableError):
        movement_service.register_compensation(
            stored.id,
            movement_id=aperture.id,
            quantity=1,
            reason="Corrección",
        )

    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_product(products, stored.id).loose_units == 6
    assert stored_movement(movements, entry.id).consumed_quantity == 1
    assert stored_movement(movements, entry.id).compensated_quantity == 0
    assert movements.list_by_product(stored.id) == history_before


def test_a_compensation_cannot_compensate_another_compensation(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que las compensaciones no pueden encadenarse (RF-03)."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=2,
    )
    original = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)
    compensation = movement_service.register_compensation(
        stored.id, movement_id=original.id, quantity=1, reason="Corrección"
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(ChainedCompensationError):
        movement_service.register_compensation(
            stored.id,
            movement_id=compensation.id,
            quantity=1,
            reason="Contracorrección",
        )

    stored_compensation = stored_movement(movements, compensation.id)
    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_movement(movements, original.id).compensated_quantity == 1
    assert stored_compensation.compensated_quantity == 0
    assert movements.list_by_product(stored.id) == history_before


# --- Producto desactivado (criterio 7) ---


def test_a_deactivated_product_accepts_a_valid_historical_compensation(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que un producto desactivado admite una compensación (RF-03)."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=2,
    )
    original = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)
    product_service.deactivate(stored.id)

    movement = movement_service.register_compensation(
        stored.id, movement_id=original.id, quantity=1, reason="Corrección"
    )

    assert stored_product(products, stored.id).active is False
    assert movement.original_movement_id == original.id
    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_movement(movements, original.id).compensated_quantity == 1


# --- Validaciones de la operación ---


def test_a_compensation_requires_an_existing_movement(
    movement_service: MovementService,
) -> None:
    """Verifica que compensar un movimiento inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        movement_service.register_compensation(
            999, movement_id=999, quantity=1, reason="Corrección"
        )


def test_a_compensation_requires_a_movement_of_the_product(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que no se compensan movimientos de otro producto."""
    first, _, first_box = prepare_product(
        product_service,
        presentations,
        movement_service,
        sku="SKU-1",
        closed_stock=2,
    )
    second, _, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        sku="SKU-2",
        closed_stock=2,
    )
    other = movement_of(movements, second.id, MovementKind.CLOSED_ENTRY)
    history_before = movements.list_by_product(first.id)

    with pytest.raises(RecordNotFoundError):
        movement_service.register_compensation(
            first.id, movement_id=other.id, quantity=1, reason="Corrección"
        )

    assert stored_presentation(presentations, first_box.id).closed_stock == 2
    assert movements.list_by_product(first.id) == history_before
    assert stored_movement(movements, other.id).compensated_quantity == 0


@pytest.mark.parametrize("reason", ["", "   ", "\t", "\n", " \t\n ", 123])
def test_an_invalid_reason_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
    reason: Any,
) -> None:
    """Verifica el rechazo de un motivo vacío, en blanco o no textual."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=2,
    )
    original = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(InvalidMovementReasonError):
        movement_service.register_compensation(
            stored.id, movement_id=original.id, quantity=1, reason=reason
        )

    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_movement(movements, original.id).compensated_quantity == 0
    assert movements.list_by_product(stored.id) == history_before


@pytest.mark.parametrize("quantity", [0, -1, 2.5, True, "2"])
def test_an_invalid_quantity_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
    quantity: Any,
) -> None:
    """Verifica el rechazo de cantidades nulas, negativas o no enteras."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=2,
    )
    original = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(QuantityError):
        movement_service.register_compensation(
            stored.id,
            movement_id=original.id,
            quantity=quantity,
            reason="Corrección",
        )

    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_movement(movements, original.id).compensated_quantity == 0
    assert movements.list_by_product(stored.id) == history_before


# --- Campos, instante e inmutabilidad (criterio 8) ---


def test_a_compensation_movement_has_no_cost_or_applied_price(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica los campos del movimiento compensatorio y su vínculo."""
    stored, _, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=2,
    )
    original = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)

    movement = movement_service.register_compensation(
        stored.id, movement_id=original.id, quantity=1, reason="Corrección"
    )
    row = stored_row(connection, movement.id)

    assert row["cost"] is None
    assert row["applied_price"] is None
    assert row["is_initial_stock"] == 0
    assert row["original_movement_id"] == original.id
    assert movement.cost is None
    assert movement.applied_price is None
    assert movement.is_initial_stock is False
    assert movement.original_movement_id == original.id


def test_the_compensation_instant_is_stored_in_utc(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que el instante de la compensación se almacena en UTC."""
    stored, _, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=2,
    )
    original = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)

    movement = movement_service.register_compensation(
        stored.id, movement_id=original.id, quantity=1, reason="Corrección"
    )
    row = stored_row(connection, movement.id)

    assert movement.occurred_at.tzinfo is not None
    assert movement.occurred_at.tzinfo == timezone.utc
    assert movement.occurred_at.utcoffset() == timedelta(0)
    assert row["occurred_at"].endswith("+00:00")


def test_a_compensation_keeps_the_original_movement_history(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """
    Verifica que el movimiento original conserva sus datos históricos.

    La entrada cerrada solo cambia su contador de compensaciones.
    """
    stored, _, box = prepare_product(
        product_service, presentations, movement_service
    )
    original = add_closed_entry(
        movements,
        product_id=stored.id,
        presentation_id=box.id,
        quantity=3,
        reason="Carga inicial",
        occurred_at=EARLIER,
        cost=Decimal("0.30"),
        is_initial_stock=True,
    )
    presentations.set_closed_stock(box.id, 3)
    before = stored_movement(movements, original.id)

    movement_service.register_compensation(
        stored.id, movement_id=original.id, quantity=1, reason="Corrección"
    )

    after = stored_movement(movements, original.id)
    assert before.cost == Decimal("0.30")
    assert before.is_initial_stock is True
    assert after == replace(before, compensated_quantity=1)
    assert after.kind is MovementKind.CLOSED_ENTRY
    assert after.quantity == before.quantity
    assert after.reason == before.reason
    assert after.occurred_at == before.occurred_at
    assert after.cost == before.cost
    assert after.is_initial_stock == before.is_initial_stock
    assert after.applied_price is None
    assert after.original_movement_id is None
    assert after.consumed_quantity == 0


def test_a_compensation_movement_is_immutable(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que un movimiento compensatorio confirmado no se modifica."""
    stored, _, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=2,
    )
    original = movement_of(movements, stored.id, MovementKind.CLOSED_ENTRY)

    movement = movement_service.register_compensation(
        stored.id, movement_id=original.id, quantity=1, reason="Corrección"
    )

    with pytest.raises(FrozenInstanceError):
        movement.quantity = 99  # type: ignore[misc]
    assert movements.get(movement.id) == movement


# --- Atomicidad (criterio 9) ---


def test_a_compensation_reverts_the_counter_on_failure(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que un fallo al actualizar el contador revierte el stock."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=3,
    )
    original_before = movement_of(
        movements, stored.id, MovementKind.CLOSED_ENTRY
    )
    history_before = movements.list_by_product(stored.id)
    failing_service = MovementService(
        products=products,
        presentations=presentations,
        movements=_FailingRepository(movements, "set_compensated_quantity"),
        transaction=partial(transaction, connection),
    )

    with pytest.raises(RuntimeError):
        failing_service.register_compensation(
            stored.id,
            movement_id=original_before.id,
            quantity=1,
            reason="Corrección",
        )

    assert stored_presentation(presentations, box.id).closed_stock == 3
    assert stored_movement(movements, original_before.id) == original_before
    assert movements.list_by_product(stored.id) == history_before


def test_a_loose_compensation_reverts_the_loose_units_on_failure(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que un fallo al actualizar el stock suelto revierte todo."""
    stored, _, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=5,
    )
    original = movement_of(movements, stored.id, MovementKind.LOOSE_ENTRY)
    history_before = movements.list_by_product(stored.id)
    failing_service = MovementService(
        products=_FailingRepository(products, "set_loose_units"),
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )

    with pytest.raises(RuntimeError):
        failing_service.register_compensation(
            stored.id, movement_id=original.id, quantity=2, reason="Corrección"
        )

    assert stored_product(products, stored.id).loose_units == 5
    assert stored_movement(movements, original.id) == original
    assert movements.list_by_product(stored.id) == history_before
