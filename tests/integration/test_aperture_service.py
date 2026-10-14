"""Pruebas de integración del servicio de apertura (T013).

Cubren los criterios de aceptación de T013 sobre RF-02 y RF-03: la apertura
atómica de presentaciones cerradas, la conservación del total equivalente, el
movimiento de apertura con su presentación, cantidad y motivo, el rechazo de la
presentación base (factor 1) y del stock cerrado insuficiente, el rechazo sobre
productos desactivados, la validación de cantidades y motivos, el consumo FIFO
por presentación y la reversión completa ante un fallo intermedio.

Las pruebas usan la base SQLite en memoria con el esquema real y los
repositorios de T009; el estado de los productos se prepara con el servicio de
productos de T011 y el saldo disponible con las entradas ordinarias del
servicio de movimientos de T012.
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import partial
from typing import Any

import pytest

from app.domain.movements import (
    ApertureNotCompensableError,
    MovementKind,
    opposite_kind,
    validate_compensable_kind,
)
from app.domain.quantities import QuantityError
from app.domain.stock import (
    BasePresentationApertureError,
    ClosedStock,
    InsufficientClosedStockError,
    total_equivalent,
)
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
    InactiveProductError,
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
    producto quede con movimientos reales y una entrada cerrada consumible.
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
) -> MovementRecord:
    """
    Registra una entrada cerrada con un instante explícito.

    Permite preparar entradas con identificadores e instantes distintos para
    verificar el orden FIFO del consumo, que el servicio no permite fechar.
    """
    return movements.add(
        product_id=product_id,
        presentation_id=presentation_id,
        kind=MovementKind.CLOSED_ENTRY,
        quantity=quantity,
        reason=reason,
        occurred_at=occurred_at,
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


def product_total(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    product_id: int,
) -> int:
    """Calcula el total equivalente del producto persistido (RF-02)."""
    product = stored_product(products, product_id)
    closed_stocks = [
        ClosedStock(
            factor=presentation.factor,
            quantity=presentation.closed_stock,
        )
        for presentation in presentations.list_by_product(product_id)
    ]

    return total_equivalent(product.loose_units, closed_stocks)


class _FailingRepository:
    """
    Envuelve un repositorio y falla al ejecutar la operación indicada.

    La operación delegada se ejecuta primero, de modo que el error ocurra
    después de que la escritura se aplicó: así se verifica que la transacción
    revierte el movimiento, los saldos y los contadores ya escritos.
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


# --- Apertura válida (criterio 1) ---


def test_an_aperture_updates_the_balances_and_keeps_the_total_equivalent(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica la apertura válida y la conservación del total equivalente."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=3,
    )
    total_before = product_total(products, presentations, stored.id)

    movement = movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=2, reason="Apertura"
    )

    assert total_before == 18
    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_product(products, stored.id).loose_units == 12
    assert product_total(products, presentations, stored.id) == total_before
    assert movement.kind is MovementKind.APERTURE
    assert movement.presentation_id == box.id
    assert movement.quantity == 2
    assert movement.reason == "Apertura"
    assert movements.list_by_product(stored.id)[-1] == movement


def test_an_aperture_consumes_the_closed_entry_first(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que la apertura consume la entrada cerrada más antigua."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=2
    )
    entry = movements.list_by_product(
        stored.id, MovementKind.CLOSED_ENTRY
    )[0]

    movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=1, reason="Apertura"
    )

    assert stored_movement(movements, entry.id).consumed_quantity == 1
    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_product(products, stored.id).loose_units == 6


def test_a_valid_reason_is_trimmed_in_the_aperture_movement(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que el motivo válido se conserva recortado."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=1
    )

    movement = movement_service.register_aperture(
        stored.id,
        presentation_id=box.id,
        quantity=1,
        reason="  Apertura manual  ",
    )

    assert movement.reason == "Apertura manual"


# --- Presentación base (criterio 6) ---


def test_an_aperture_on_the_base_presentation_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que la presentación base no puede abrirse (RF-03)."""
    stored, base, _ = prepare_product(
        product_service, presentations, movement_service
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(BasePresentationApertureError):
        movement_service.register_aperture(
            stored.id,
            presentation_id=base.id,
            quantity=1,
            reason="Apertura",
        )

    assert stored_presentation(presentations, base.id).closed_stock == 0
    assert stored_product(products, stored.id).loose_units == 0
    assert movements.list_by_product(stored.id) == history_before


# --- Stock cerrado insuficiente (criterio 5) ---


def test_an_aperture_without_enough_closed_stock_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica el rechazo de una apertura sin stock cerrado suficiente."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=1
    )
    entry = movements.list_by_product(
        stored.id, MovementKind.CLOSED_ENTRY
    )[0]
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(InsufficientClosedStockError):
        movement_service.register_aperture(
            stored.id,
            presentation_id=box.id,
            quantity=2,
            reason="Apertura",
        )

    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_product(products, stored.id).loose_units == 0
    assert stored_movement(movements, entry.id).consumed_quantity == 0
    assert movements.list_by_product(stored.id) == history_before


# --- Producto desactivado (criterio 7) ---


def test_an_aperture_on_a_deactivated_product_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que un producto desactivado no admite aperturas (RF-01)."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=2
    )
    product_service.deactivate(stored.id)
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(InactiveProductError):
        movement_service.register_aperture(
            stored.id,
            presentation_id=box.id,
            quantity=1,
            reason="Apertura",
        )

    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_product(products, stored.id).loose_units == 0
    assert movements.list_by_product(stored.id) == history_before


# --- Validaciones de la operación ---


@pytest.mark.parametrize("reason", ["", "   ", "\t", "\n", " \t\n ", 123])
def test_an_invalid_reason_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
    reason: Any,
) -> None:
    """Verifica el rechazo de un motivo vacío, en blanco o no textual."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=2
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(InvalidMovementReasonError):
        movement_service.register_aperture(
            stored.id,
            presentation_id=box.id,
            quantity=1,
            reason=reason,
        )

    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_product(products, stored.id).loose_units == 0
    assert movements.list_by_product(stored.id) == history_before


@pytest.mark.parametrize("quantity", [0, -1, 2.5, True, "2"])
def test_an_invalid_quantity_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
    quantity: Any,
) -> None:
    """Verifica el rechazo de cantidades nulas, negativas o no enteras."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=2
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(QuantityError):
        movement_service.register_aperture(
            stored.id,
            presentation_id=box.id,
            quantity=quantity,
            reason="Apertura",
        )

    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_product(products, stored.id).loose_units == 0
    assert movements.list_by_product(stored.id) == history_before


def test_an_aperture_requires_an_existing_product(
    movement_service: MovementService,
) -> None:
    """Verifica que una apertura sobre un producto inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        movement_service.register_aperture(
            999, presentation_id=999, quantity=1, reason="Apertura"
        )


def test_an_aperture_requires_an_existing_presentation(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que una presentación inexistente se rechaza."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service
    )

    with pytest.raises(RecordNotFoundError):
        movement_service.register_aperture(
            stored.id, presentation_id=999, quantity=1, reason="Apertura"
        )


def test_an_aperture_requires_a_presentation_of_the_product(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que no se abren presentaciones de otro producto."""
    first, _, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        sku="SKU-1",
        closed_stock=2,
    )
    _, _, other_box = prepare_product(
        product_service,
        presentations,
        movement_service,
        sku="SKU-2",
        closed_stock=2,
    )
    history_before = movements.list_by_product(first.id)

    with pytest.raises(RecordNotFoundError):
        movement_service.register_aperture(
            first.id,
            presentation_id=other_box.id,
            quantity=1,
            reason="Apertura",
        )

    assert stored_presentation(presentations, other_box.id).closed_stock == 2
    assert stored_product(products, first.id).loose_units == 0
    assert movements.list_by_product(first.id) == history_before


# --- Consumo FIFO (criterios 3 y 4) ---


def test_an_aperture_consumes_the_oldest_entry_first(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que el consumo FIFO empieza por la entrada más antigua."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service
    )
    oldest, newest = add_two_closed_entries(
        stored, box, presentations, movements
    )

    movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=1, reason="Apertura"
    )

    assert oldest.id < newest.id
    assert stored_movement(movements, oldest.id).consumed_quantity == 1
    assert stored_movement(movements, newest.id).consumed_quantity == 0
    assert stored_presentation(presentations, box.id).closed_stock == 2


def test_an_aperture_consumes_across_the_entries_in_order(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica el consumo FIFO distribuido entre varias entradas."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service
    )
    oldest, newest = add_two_closed_entries(
        stored, box, presentations, movements
    )
    total_before = product_total(products, presentations, stored.id)

    movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=3, reason="Apertura"
    )

    assert stored_movement(movements, oldest.id).consumed_quantity == 2
    assert stored_movement(movements, newest.id).consumed_quantity == 1
    assert stored_presentation(presentations, box.id).closed_stock == 0
    assert stored_product(products, stored.id).loose_units == 18
    assert product_total(products, presentations, stored.id) == total_before


def test_an_aperture_does_not_consume_entries_of_another_presentation(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que el FIFO se limita a la presentación de la apertura."""
    stored = product_service.register(
        make_registration(
            presentations=(
                make_closed_presentation(name="Caja x6", factor=6),
                make_closed_presentation(name="Fardo x12", factor=12),
            )
        )
    )
    _, box, bundle = presentations.list_by_product(stored.id)
    box_entry = add_closed_entry(
        movements,
        product_id=stored.id,
        presentation_id=box.id,
        quantity=2,
        reason="Compra A",
        occurred_at=EARLIER,
    )
    bundle_entry = add_closed_entry(
        movements,
        product_id=stored.id,
        presentation_id=bundle.id,
        quantity=2,
        reason="Compra B",
        occurred_at=EARLIER,
    )
    presentations.set_closed_stock(box.id, 2)
    presentations.set_closed_stock(bundle.id, 2)

    movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=2, reason="Apertura"
    )

    assert stored_movement(movements, box_entry.id).consumed_quantity == 2
    assert stored_movement(movements, bundle_entry.id).consumed_quantity == 0
    assert stored_presentation(presentations, box.id).closed_stock == 0
    assert stored_presentation(presentations, bundle.id).closed_stock == 2


def test_an_aperture_respects_the_compensated_quantity_of_the_fifo_limit(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """
    Documenta el límite FIFO actual de las cantidades compensadas.

    El consumo FIFO disponible de una entrada cerrada es
    `cantidad − consumida − compensada`. Con una entrada de 2 presentaciones y
    1 ya compensada, su cantidad disponible para el FIFO es 1: aunque el stock
    cerrado vigente (2) alcance para la apertura solicitada, el servicio la
    rechaza porque el dominio no puede distribuirla. La prueba registra el
    comportamiento actual y verifica que el rechazo no deja cambios parciales;
    no propone una solución para el riesgo ligado a T014/T015.
    """
    stored, _, box = prepare_product(
        product_service, presentations, movement_service
    )
    entry = add_closed_entry(
        movements,
        product_id=stored.id,
        presentation_id=box.id,
        quantity=2,
        reason="Compra A",
        occurred_at=EARLIER,
    )
    presentations.set_closed_stock(box.id, 2)
    connection.execute(
        "UPDATE movements SET compensated_quantity = 1 WHERE id = ?",
        (entry.id,),
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(InsufficientClosedStockError):
        movement_service.register_aperture(
            stored.id,
            presentation_id=box.id,
            quantity=2,
            reason="Apertura",
        )

    stored_entry = stored_movement(movements, entry.id)
    assert stored_entry.compensated_quantity == 1
    assert stored_entry.consumed_quantity == 0
    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_product(products, stored.id).loose_units == 0
    assert movements.list_by_product(stored.id) == history_before


# --- Atomicidad (criterio 9) ---


def test_an_aperture_reverts_every_write_on_failure(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que un fallo al actualizar el saldo revierte la apertura."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=2
    )
    entry = movements.list_by_product(
        stored.id, MovementKind.CLOSED_ENTRY
    )[0]
    history_before = movements.list_by_product(stored.id)
    failing_service = MovementService(
        products=_FailingRepository(products, "set_loose_units"),
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )

    with pytest.raises(RuntimeError):
        failing_service.register_aperture(
            stored.id, presentation_id=box.id, quantity=2, reason="Apertura"
        )

    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_product(products, stored.id).loose_units == 0
    assert stored_movement(movements, entry.id).consumed_quantity == 0
    assert movements.list_by_product(stored.id) == history_before


def test_an_aperture_reverts_the_counters_on_failure(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que un fallo al consumir revierte saldo y contadores."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=2
    )
    entry = movements.list_by_product(
        stored.id, MovementKind.CLOSED_ENTRY
    )[0]
    history_before = movements.list_by_product(stored.id)
    failing_service = MovementService(
        products=products,
        presentations=presentations,
        movements=_FailingRepository(movements, "set_consumed_quantity"),
        transaction=partial(transaction, connection),
    )

    with pytest.raises(RuntimeError):
        failing_service.register_aperture(
            stored.id, presentation_id=box.id, quantity=2, reason="Apertura"
        )

    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_product(products, stored.id).loose_units == 0
    assert stored_movement(movements, entry.id).consumed_quantity == 0
    assert movements.list_by_product(stored.id) == history_before


# --- Serialización, inmutabilidad y compensabilidad (criterios 8 y 9) ---


def test_an_aperture_movement_has_no_cost_or_applied_price(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que el movimiento de apertura no lleva costo ni precio."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=1
    )

    movement = movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=1, reason="Apertura"
    )
    row = connection.execute(
        "SELECT cost, applied_price, is_initial_stock, original_movement_id "
        "FROM movements WHERE id = ?",
        (movement.id,),
    ).fetchone()

    assert row["cost"] is None
    assert row["applied_price"] is None
    assert row["is_initial_stock"] == 0
    assert row["original_movement_id"] is None
    assert movement.cost is None
    assert movement.applied_price is None
    assert movement.is_initial_stock is False
    assert movement.original_movement_id is None


def test_an_aperture_is_not_compensable(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que la apertura no puede compensarse ni encadenarse."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=1
    )
    movement = movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=1, reason="Apertura"
    )

    assert movement.kind is MovementKind.APERTURE
    with pytest.raises(ApertureNotCompensableError):
        validate_compensable_kind(movement.kind)
    with pytest.raises(ApertureNotCompensableError):
        opposite_kind(movement.kind)


def test_an_aperture_movement_is_immutable(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que un movimiento de apertura confirmado no se modifica."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=1
    )
    movement = movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=1, reason="Apertura"
    )

    with pytest.raises(FrozenInstanceError):
        movement.quantity = 99  # type: ignore[misc]
    assert movements.get(movement.id) == movement


def test_the_aperture_instant_is_stored_in_utc(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que el instante de la apertura se almacena en UTC."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=1
    )

    movement = movement_service.register_aperture(
        stored.id, presentation_id=box.id, quantity=1, reason="Apertura"
    )
    row = connection.execute(
        "SELECT occurred_at FROM movements WHERE id = ?",
        (movement.id,),
    ).fetchone()

    assert movement.occurred_at.tzinfo is not None
    assert movement.occurred_at.tzinfo == timezone.utc
    assert movement.occurred_at.utcoffset() == timedelta(0)
    assert row["occurred_at"].endswith("+00:00")
