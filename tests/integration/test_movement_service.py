"""Pruebas de integración del servicio de movimientos (T012).

Cubren los criterios de aceptación de T012 sobre RF-02, RF-03 y RF-07: las
entradas y salidas cerradas y sueltas con confirmación transaccional, el
rechazo de cantidades, costos y motivos inválidos, el costo opcional de las
entradas, el precio aplicado de las salidas, la actualización del saldo suelto
o cerrado, el rechazo de salidas sin stock suficiente, el rechazo de
operaciones ordinarias sobre un producto desactivado y el consumo FIFO de las
entradas cerradas al salir de presentaciones cerradas.

Las pruebas usan la base SQLite en memoria con el esquema real y los
repositorios de T009; el estado de los productos se prepara con el servicio de
productos de T011 y las modificaciones de precio con el servicio de
presentaciones de T011.
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import partial
from typing import Any

import pytest

from app.domain.movements import MovementKind
from app.domain.quantities import QuantityError
from app.domain.stock import InsufficientClosedStockError
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
    BasePresentationOperationError,
    InsufficientLooseUnitsError,
    InvalidMovementReasonError,
    MovementService,
    MovementServiceError,
)
from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
    RecordNotFoundError,
)
from app.services.presentations import PresentationService
from app.services.products import (
    InactiveProductError,
    InitialClosedStock,
    ProductRegistration,
    ProductService,
)

EARLIER: datetime = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
LATER: datetime = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)

OPERATIONS: tuple[str, ...] = (
    "loose_entry",
    "closed_entry",
    "loose_exit",
    "closed_exit",
)


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
    base_price: str = "100.00",
    box_price: str = "600.00",
) -> tuple[ProductRecord, PresentationRecord, PresentationRecord]:
    """
    Prepara un producto con su presentación base, una caja y saldo disponible.

    El saldo se carga con entradas ordinarias del servicio, de modo que el
    producto quede con movimientos reales. Devuelve el producto, su
    presentación base y su presentación cerrada.
    """
    stored = product_service.register(
        make_registration(
            sku=sku,
            base_sale_price=Decimal(base_price),
            presentations=(make_closed_presentation(sale_price=box_price),),
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


def run_operation(
    service: MovementService,
    operation: str,
    *,
    product_id: int,
    presentation_id: int,
    quantity: Any,
    reason: str = "Movimiento",
    cost: Any = None,
) -> MovementRecord:
    """Ejecuta la operación ordinaria indicada sobre el servicio."""
    if operation == "loose_entry":
        return service.register_loose_entry(
            product_id, quantity=quantity, reason=reason, cost=cost
        )
    if operation == "closed_entry":
        return service.register_closed_entry(
            product_id,
            presentation_id=presentation_id,
            quantity=quantity,
            reason=reason,
            cost=cost,
        )
    if operation == "loose_exit":
        return service.register_loose_exit(
            product_id, quantity=quantity, reason=reason
        )
    if operation == "closed_exit":
        return service.register_closed_exit(
            product_id,
            presentation_id=presentation_id,
            quantity=quantity,
            reason=reason,
        )

    raise AssertionError(f"Operación desconocida: {operation}.")


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


class _FailingRepository:
    """
    Envuelve un repositorio y falla al ejecutar la operación indicada.

    La operación delegada se ejecuta primero, de modo que el error ocurra
    después de que la escritura se aplicó: así se verifica que la transacción
    revierte el movimiento, el saldo y los contadores ya escritos.
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


# --- Entradas válidas (criterio 1) ---


def test_a_loose_entry_updates_the_loose_units_and_records_the_movement(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica una entrada suelta válida y su movimiento."""
    stored = product_service.register(make_registration())
    base = presentations.list_by_product(stored.id)[0]

    movement = movement_service.register_loose_entry(
        stored.id, quantity=5, reason="  Compra  ", cost=Decimal("10.50")
    )

    assert stored_product(products, stored.id).loose_units == 5
    assert movement.product_id == stored.id
    assert movement.presentation_id == base.id
    assert movement.kind is MovementKind.LOOSE_ENTRY
    assert movement.quantity == 5
    assert movement.reason == "Compra"
    assert movement.cost == Decimal("10.50")
    assert movement.applied_price is None
    assert movement.is_initial_stock is False
    assert movements.list_by_product(stored.id) == [movement]


def test_a_loose_entry_without_cost_records_no_cost(
    product_service: ProductService,
    movement_service: MovementService,
) -> None:
    """Verifica que el costo de una entrada es opcional."""
    stored = product_service.register(make_registration())

    movement = movement_service.register_loose_entry(
        stored.id, quantity=1, reason="Compra"
    )

    assert movement.cost is None


def test_a_closed_entry_updates_the_closed_stock_and_records_the_movement(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica una entrada cerrada válida y su movimiento."""
    stored, base, box = prepare_product(
        product_service, presentations, movement_service
    )

    movement = movement_service.register_closed_entry(
        stored.id,
        presentation_id=box.id,
        quantity=3,
        reason="Compra",
        cost=Decimal("0"),
    )

    assert stored_presentation(presentations, box.id).closed_stock == 3
    assert stored_presentation(presentations, base.id).closed_stock == 0
    assert movement.presentation_id == box.id
    assert movement.kind is MovementKind.CLOSED_ENTRY
    assert movement.quantity == 3
    assert movement.cost == Decimal("0")
    assert movement.applied_price is None
    assert movements.list_by_product(stored.id) == [movement]


def test_entries_do_not_alter_the_other_balance(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que cada entrada afecta solo su tipo de stock."""
    stored, base, box = prepare_product(
        product_service, presentations, movement_service
    )

    movement_service.register_loose_entry(
        stored.id, quantity=4, reason="Compra"
    )
    movement_service.register_closed_entry(
        stored.id, presentation_id=box.id, quantity=2, reason="Compra"
    )

    assert stored_product(products, stored.id).loose_units == 4
    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_presentation(presentations, base.id).closed_stock == 0


# --- Salidas válidas (criterios 3, 4, 5 y 6) ---


def test_a_loose_exit_updates_the_loose_units_and_captures_the_base_price(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movement_service: MovementService,
) -> None:
    """Verifica una salida suelta válida y el precio de la base."""
    stored, base, box = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )

    movement = movement_service.register_loose_exit(
        stored.id, quantity=2, reason="Venta"
    )

    assert stored_product(products, stored.id).loose_units == 3
    assert movement.presentation_id == base.id
    assert movement.kind is MovementKind.LOOSE_EXIT
    assert movement.quantity == 2
    assert movement.applied_price == Decimal("100.00")
    assert movement.cost is None
    assert stored_presentation(presentations, box.id).closed_stock == 0


def test_a_closed_exit_updates_the_closed_stock_and_captures_the_price(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movement_service: MovementService,
) -> None:
    """Verifica una salida cerrada válida y el precio de su presentación."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=1,
        closed_stock=3,
        box_price="600.00",
    )

    movement = movement_service.register_closed_exit(
        stored.id, presentation_id=box.id, quantity=2, reason="Venta"
    )

    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_product(products, stored.id).loose_units == 1
    assert movement.presentation_id == box.id
    assert movement.kind is MovementKind.CLOSED_EXIT
    assert movement.quantity == 2
    assert movement.applied_price == Decimal("600.00")
    assert movement.cost is None


def test_a_closed_exit_keeps_the_consumed_entry_history_intact(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que el consumo solo cambia el contador de la entrada."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=2
    )
    entry = movements.list_by_product(
        stored.id, MovementKind.CLOSED_ENTRY
    )[0]

    movement_service.register_closed_exit(
        stored.id, presentation_id=box.id, quantity=1, reason="Venta"
    )

    assert stored_movement(movements, entry.id) == replace(
        entry, consumed_quantity=1
    )


def test_exits_can_leave_a_balance_at_zero_but_never_negative(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que ninguna salida deja stock negativo."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=2,
        closed_stock=1,
    )

    movement_service.register_loose_exit(
        stored.id, quantity=2, reason="Venta"
    )
    movement_service.register_closed_exit(
        stored.id, presentation_id=box.id, quantity=1, reason="Venta"
    )

    assert stored_product(products, stored.id).loose_units == 0
    assert stored_presentation(presentations, box.id).closed_stock == 0

    with pytest.raises(InsufficientLooseUnitsError):
        movement_service.register_loose_exit(
            stored.id, quantity=1, reason="Venta"
        )
    with pytest.raises(InsufficientClosedStockError):
        movement_service.register_closed_exit(
            stored.id, presentation_id=box.id, quantity=1, reason="Venta"
        )

    assert stored_product(products, stored.id).loose_units == 0
    assert stored_presentation(presentations, box.id).closed_stock == 0


# --- Validaciones de la operación ---


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("quantity", [0, -1, 2.5, True, "2"])
def test_an_invalid_quantity_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
    operation: str,
    quantity: Any,
) -> None:
    """Verifica el rechazo de cantidades nulas, negativas o no enteras."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=5,
        closed_stock=2,
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(QuantityError):
        run_operation(
            movement_service,
            operation,
            product_id=stored.id,
            presentation_id=box.id,
            quantity=quantity,
        )

    assert movements.list_by_product(stored.id) == history_before
    assert stored_product(products, stored.id).loose_units == 5
    assert stored_presentation(presentations, box.id).closed_stock == 2


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("reason", ["", "   ", "\t", "\n", " \t\n ", 123])
def test_an_invalid_reason_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
    operation: str,
    reason: Any,
) -> None:
    """Verifica el rechazo de un motivo vacío, en blanco o no textual."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=5,
        closed_stock=2,
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(InvalidMovementReasonError):
        run_operation(
            movement_service,
            operation,
            product_id=stored.id,
            presentation_id=box.id,
            quantity=1,
            reason=reason,
        )

    assert movements.list_by_product(stored.id) == history_before
    assert stored_product(products, stored.id).loose_units == 5
    assert stored_presentation(presentations, box.id).closed_stock == 2


@pytest.mark.parametrize("operation", ["loose_entry", "closed_entry"])
@pytest.mark.parametrize(
    "cost",
    [
        Decimal("-0.01"),
        Decimal("-1"),
        Decimal("NaN"),
        Decimal("Infinity"),
        10,
        "10",
    ],
)
def test_an_invalid_entry_cost_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
    operation: str,
    cost: Any,
) -> None:
    """Verifica el rechazo de un costo negativo, no finito o no Decimal."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(QuantityError):
        run_operation(
            movement_service,
            operation,
            product_id=stored.id,
            presentation_id=box.id,
            quantity=1,
            cost=cost,
        )

    assert movements.list_by_product(stored.id) == history_before


def test_a_zero_cost_is_accepted_in_an_entry(
    product_service: ProductService,
    movement_service: MovementService,
) -> None:
    """Verifica que un costo igual a cero es válido."""
    stored = product_service.register(make_registration())

    movement = movement_service.register_loose_entry(
        stored.id, quantity=1, reason="Compra", cost=Decimal("0.00")
    )

    assert movement.cost == Decimal("0")


@pytest.mark.parametrize("operation", OPERATIONS)
def test_an_ordinary_operation_on_a_deactivated_product_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
    operation: str,
) -> None:
    """Verifica que un producto desactivado no admite movimientos nuevos."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=5,
        closed_stock=2,
    )
    product_service.deactivate(stored.id)
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(InactiveProductError):
        run_operation(
            movement_service,
            operation,
            product_id=stored.id,
            presentation_id=box.id,
            quantity=1,
        )

    assert movements.list_by_product(stored.id) == history_before
    assert stored_product(products, stored.id).loose_units == 5
    assert stored_presentation(presentations, box.id).closed_stock == 2


@pytest.mark.parametrize("operation", OPERATIONS)
def test_an_operation_requires_an_existing_product(
    movement_service: MovementService,
    operation: str,
) -> None:
    """Verifica que una operación sobre un producto inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        run_operation(
            movement_service,
            operation,
            product_id=999,
            presentation_id=999,
            quantity=1,
        )


def test_a_closed_entry_requires_a_presentation_of_the_product(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que no se registran entradas de otro producto."""
    first, _, _ = prepare_product(
        product_service, presentations, movement_service, sku="SKU-1"
    )
    _, _, other_box = prepare_product(
        product_service, presentations, movement_service, sku="SKU-2"
    )
    history_before = movements.list_by_product(first.id)

    with pytest.raises(RecordNotFoundError):
        movement_service.register_closed_entry(
            first.id,
            presentation_id=other_box.id,
            quantity=1,
            reason="Compra",
        )

    assert movements.list_by_product(first.id) == history_before


def test_a_closed_exit_requires_a_presentation_of_the_product(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que no se registran salidas de otro producto."""
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
        movement_service.register_closed_exit(
            first.id,
            presentation_id=other_box.id,
            quantity=1,
            reason="Venta",
        )

    assert movements.list_by_product(first.id) == history_before


def test_a_closed_operation_requires_an_existing_presentation(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que una presentación inexistente se rechaza."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service
    )

    with pytest.raises(RecordNotFoundError):
        movement_service.register_closed_entry(
            stored.id, presentation_id=999, quantity=1, reason="Compra"
        )
    with pytest.raises(RecordNotFoundError):
        movement_service.register_closed_exit(
            stored.id, presentation_id=999, quantity=1, reason="Venta"
        )


def test_a_closed_entry_on_the_base_presentation_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que la presentación base no admite entradas cerradas."""
    stored, base, box = prepare_product(
        product_service, presentations, movement_service
    )

    with pytest.raises(BasePresentationOperationError):
        movement_service.register_closed_entry(
            stored.id, presentation_id=base.id, quantity=1, reason="Compra"
        )

    assert stored_presentation(presentations, base.id).closed_stock == 0
    assert movements.list_by_product(stored.id) == []


def test_a_closed_exit_on_the_base_presentation_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que la presentación base no admite salidas cerradas."""
    stored, base, box = prepare_product(
        product_service, presentations, movement_service, loose_units=2
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(BasePresentationOperationError):
        movement_service.register_closed_exit(
            stored.id, presentation_id=base.id, quantity=1, reason="Venta"
        )

    assert stored_product(products, stored.id).loose_units == 2
    assert movements.list_by_product(stored.id) == history_before


def test_the_service_errors_are_value_errors() -> None:
    """Verifica que los rechazos del servicio se informan como `ValueError`."""
    assert issubclass(MovementServiceError, ValueError)
    assert issubclass(InvalidMovementReasonError, MovementServiceError)
    assert issubclass(BasePresentationOperationError, MovementServiceError)
    assert issubclass(InsufficientLooseUnitsError, MovementServiceError)


# --- Precio aplicado (criterio 7) ---


def test_a_loose_exit_keeps_the_applied_price_after_a_price_change(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    presentation_service: PresentationService,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica el precio histórico de una salida suelta."""
    stored, base, box = prepare_product(
        product_service, presentations, movement_service, loose_units=3
    )
    first = movement_service.register_loose_exit(
        stored.id, quantity=1, reason="Venta"
    )

    presentation_service.change_sale_price(base.id, Decimal("150.00"))

    assert stored_movement(movements, first.id).applied_price == Decimal(
        "100.00"
    )
    second = movement_service.register_loose_exit(
        stored.id, quantity=1, reason="Venta"
    )
    assert second.applied_price == Decimal("150.00")


def test_a_closed_exit_keeps_the_applied_price_after_a_price_change(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    presentation_service: PresentationService,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica el precio histórico de una salida cerrada."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=3,
        box_price="600.00",
    )
    first = movement_service.register_closed_exit(
        stored.id, presentation_id=box.id, quantity=1, reason="Venta"
    )

    presentation_service.change_sale_price(box.id, Decimal("900.00"))

    assert stored_movement(movements, first.id).applied_price == Decimal(
        "600.00"
    )
    second = movement_service.register_closed_exit(
        stored.id, presentation_id=box.id, quantity=1, reason="Venta"
    )
    assert second.applied_price == Decimal("900.00")


# --- Disponibilidad (criterios 5 y 6) ---


def test_a_loose_exit_that_exceeds_the_loose_units_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que una salida suelta sin stock suficiente se rechaza."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, loose_units=3
    )
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(InsufficientLooseUnitsError):
        movement_service.register_loose_exit(
            stored.id, quantity=4, reason="Venta"
        )

    assert stored_product(products, stored.id).loose_units == 3
    assert movements.list_by_product(stored.id) == history_before


def test_a_closed_exit_that_exceeds_the_closed_stock_is_rejected(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que una salida cerrada sin stock suficiente se rechaza."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=1
    )
    entry = movements.list_by_product(
        stored.id, MovementKind.CLOSED_ENTRY
    )[0]
    history_before = movements.list_by_product(stored.id)

    with pytest.raises(InsufficientClosedStockError):
        movement_service.register_closed_exit(
            stored.id, presentation_id=box.id, quantity=2, reason="Venta"
        )

    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_movement(movements, entry.id).consumed_quantity == 0
    assert movements.list_by_product(stored.id) == history_before


# --- Consumo FIFO de las entradas cerradas (criterio 6) ---


def test_a_closed_exit_consumes_the_oldest_closed_entry_first(
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

    movement_service.register_closed_exit(
        stored.id, presentation_id=box.id, quantity=1, reason="Venta"
    )

    assert oldest.id < newest.id
    assert stored_movement(movements, oldest.id).consumed_quantity == 1
    assert stored_movement(movements, newest.id).consumed_quantity == 0
    assert stored_presentation(presentations, box.id).closed_stock == 2


def test_a_closed_exit_consumes_across_the_eligible_entries_in_order(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
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

    movement_service.register_closed_exit(
        stored.id, presentation_id=box.id, quantity=3, reason="Venta"
    )

    assert stored_movement(movements, oldest.id).consumed_quantity == 2
    assert stored_movement(movements, newest.id).consumed_quantity == 1
    assert stored_presentation(presentations, box.id).closed_stock == 0


def test_a_closed_exit_does_not_consume_entries_of_another_presentation(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que el FIFO se limita a la presentación de la salida."""
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

    movement_service.register_closed_exit(
        stored.id, presentation_id=box.id, quantity=2, reason="Venta"
    )

    assert stored_movement(movements, box_entry.id).consumed_quantity == 2
    assert stored_movement(movements, bundle_entry.id).consumed_quantity == 0
    assert stored_presentation(presentations, box.id).closed_stock == 0
    assert stored_presentation(presentations, bundle.id).closed_stock == 2


# --- Atomicidad (criterio 1 y decisiones de la tarea) ---


def test_a_loose_exit_reverts_the_movement_and_the_balance_on_failure(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que un fallo al actualizar el saldo revierte la salida."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    history_before = movements.list_by_product(stored.id)
    failing_service = MovementService(
        products=_FailingRepository(products, "set_loose_units"),
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )

    with pytest.raises(RuntimeError):
        failing_service.register_loose_exit(
            stored.id, quantity=2, reason="Venta"
        )

    assert stored_product(products, stored.id).loose_units == 5
    assert movements.list_by_product(stored.id) == history_before


def test_a_closed_exit_reverts_the_movement_stock_and_counters_on_failure(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
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
        products=SqliteProductRepository(connection),
        presentations=presentations,
        movements=_FailingRepository(movements, "set_consumed_quantity"),
        transaction=partial(transaction, connection),
    )

    with pytest.raises(RuntimeError):
        failing_service.register_closed_exit(
            stored.id, presentation_id=box.id, quantity=1, reason="Venta"
        )

    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_movement(movements, entry.id).consumed_quantity == 0
    assert movements.list_by_product(stored.id) == history_before


# --- Serialización e inmutabilidad (criterios 8 y 9) ---


def test_the_cost_and_the_instant_are_serialized_canonically(
    connection: sqlite3.Connection,
    product_service: ProductService,
    movement_service: MovementService,
) -> None:
    """Verifica el texto canónico del costo y el instante en UTC."""
    stored = product_service.register(make_registration())

    movement = movement_service.register_loose_entry(
        stored.id, quantity=2, reason="Compra", cost=Decimal("10.50")
    )
    row = connection.execute(
        "SELECT cost, occurred_at FROM movements WHERE id = ?",
        (movement.id,),
    ).fetchone()

    assert row["cost"] == "10.5"
    assert movement.cost == Decimal("10.50")
    assert movement.occurred_at.tzinfo is not None
    assert movement.occurred_at.utcoffset() == timedelta(0)
    assert row["occurred_at"].endswith("+00:00")
    assert movement.occurred_at.tzinfo == timezone.utc


def test_the_applied_price_is_serialized_canonically(
    connection: sqlite3.Connection,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
) -> None:
    """Verifica el texto canónico del precio aplicado."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        closed_stock=1,
        box_price="1200.00",
    )

    movement = movement_service.register_closed_exit(
        stored.id, presentation_id=box.id, quantity=1, reason="Venta"
    )
    row = connection.execute(
        "SELECT applied_price FROM movements WHERE id = ?",
        (movement.id,),
    ).fetchone()

    assert row["applied_price"] == "1200"
    assert movement.applied_price == Decimal("1200.00")


def test_a_confirmed_movement_is_immutable(
    product_service: ProductService,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> None:
    """Verifica que un movimiento confirmado no puede modificarse."""
    stored = product_service.register(make_registration())

    movement = movement_service.register_loose_entry(
        stored.id, quantity=1, reason="Compra"
    )

    with pytest.raises(FrozenInstanceError):
        movement.quantity = 99  # type: ignore[misc]
    assert movements.get(movement.id) == movement
