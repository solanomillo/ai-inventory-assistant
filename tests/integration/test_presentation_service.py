"""Pruebas de integración del servicio de presentaciones (T011).

Cubren los criterios de aceptación de T011 sobre RF-07: el alta y la
modificación de presentaciones según las reglas del dominio, el rechazo de
operaciones sobre productos desactivados, la presentación nueva con stock
cerrado cero sin alterar otros saldos y la inmutabilidad del factor cuando la
presentación tiene stock cerrado o historial.

Las pruebas usan la base SQLite en memoria con el esquema real, los
repositorios de T009 y el servicio de productos de T011 para preparar el
estado.
"""

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
from typing import Any

import pytest

from app.domain.movements import MovementKind
from app.domain.presentations import (
    DuplicatePresentationNameError,
    FactorNotMutableError,
    PresentationError,
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
from app.services.ports import (
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
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> PresentationService:
    """Servicio de presentaciones sobre la base en memoria."""
    return PresentationService(
        products=products,
        presentations=presentations,
        movements=movements,
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


def register_product_with_box(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    *,
    closed_stock: int = 0,
) -> tuple[ProductRecord, PresentationRecord]:
    """Registra un producto con una presentación adicional y la devuelve."""
    stored = product_service.register(
        make_registration(
            presentations=(
                make_closed_presentation(closed_stock=closed_stock),
            ),
        )
    )

    return stored, presentations.list_by_product(stored.id)[1]


# --- Alta de presentaciones (criterios 9 y 10) ---


def test_add_creates_a_presentation_with_zero_closed_stock(
    presentation_service: PresentationService,
    product_service: ProductService,
) -> None:
    """Verifica el alta de una presentación con stock cerrado cero."""
    stored = product_service.register(
        make_registration(initial_loose_units=4)
    )

    added = presentation_service.add(
        stored.id,
        name="Caja x6",
        factor=6,
        sale_price=Decimal("600.00"),
    )

    assert added.product_id == stored.id
    assert added.name == "Caja x6"
    assert added.factor == 6
    assert added.sale_price == Decimal("600.00")
    assert added.closed_stock == 0


def test_add_does_not_change_other_balances(
    presentation_service: PresentationService,
    product_service: ProductService,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que una presentación nueva no altera otros saldos."""
    stored = product_service.register(
        make_registration(
            initial_loose_units=4,
            presentations=(make_closed_presentation(closed_stock=2),),
        )
    )
    product_before = products.get(stored.id)
    presentations_before = presentations.list_by_product(stored.id)
    history_before = movements.list_by_product(stored.id)

    presentation_service.add(
        stored.id,
        name="Fardo x12",
        factor=12,
        sale_price=Decimal("1200.00"),
    )

    assert products.get(stored.id) == product_before
    after = presentations.list_by_product(stored.id)
    assert after[: len(presentations_before)] == presentations_before
    assert len(after) == len(presentations_before) + 1
    assert movements.list_by_product(stored.id) == history_before


@pytest.mark.parametrize("duplicated_name", ["unidad", "UNIDAD", "Unidad"])
def test_add_rejects_a_duplicate_name(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    duplicated_name: str,
) -> None:
    """Verifica el rechazo del nombre duplicado sin distinguir mayúsculas."""
    stored = product_service.register(make_registration(base_unit="unidad"))

    with pytest.raises(DuplicatePresentationNameError):
        presentation_service.add(
            stored.id,
            name=duplicated_name,
            factor=6,
            sale_price=Decimal("600.00"),
        )

    assert len(presentations.list_by_product(stored.id)) == 1


@pytest.mark.parametrize("factor", [0, -1, 2.5, True])
def test_add_rejects_an_invalid_factor(
    presentation_service: PresentationService,
    product_service: ProductService,
    factor: Any,
) -> None:
    """Verifica el rechazo de un factor menor que uno o no entero."""
    stored = product_service.register(make_registration())

    with pytest.raises(QuantityError):
        presentation_service.add(
            stored.id,
            name="Caja x6",
            factor=factor,
            sale_price=Decimal("600.00"),
        )


@pytest.mark.parametrize("sale_price", [Decimal("0"), Decimal("-5"), 100])
def test_add_rejects_an_invalid_price(
    presentation_service: PresentationService,
    product_service: ProductService,
    sale_price: Any,
) -> None:
    """Verifica el rechazo de un precio menor o igual que cero."""
    stored = product_service.register(make_registration())

    with pytest.raises(QuantityError):
        presentation_service.add(
            stored.id,
            name="Caja x6",
            factor=6,
            sale_price=sale_price,
        )


def test_add_rejects_a_deactivated_product(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que no se crean presentaciones de un producto desactivado."""
    stored = product_service.register(make_registration())
    product_service.deactivate(stored.id)

    with pytest.raises(InactiveProductError):
        presentation_service.add(
            stored.id,
            name="Caja x6",
            factor=6,
            sale_price=Decimal("600.00"),
        )

    assert len(presentations.list_by_product(stored.id)) == 1


def test_add_requires_an_existing_product(
    presentation_service: PresentationService,
) -> None:
    """Verifica que no se agregan presentaciones a un producto inexistente."""
    with pytest.raises(RecordNotFoundError):
        presentation_service.add(
            999,
            name="Caja x6",
            factor=6,
            sale_price=Decimal("600.00"),
        )


def test_add_rejects_a_second_base_presentation(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que no se crea una segunda presentación base (RF-07)."""
    stored = product_service.register(make_registration(base_unit="unidad"))

    with pytest.raises(PresentationError):
        presentation_service.add(
            stored.id,
            name="Unidad suelta",
            factor=1,
            sale_price=Decimal("90.00"),
        )

    factors = [
        presentation.factor
        for presentation in presentations.list_by_product(stored.id)
    ]
    assert factors == [1]


# --- Modificación del precio de venta (criterios 9 y 10) ---


def test_change_sale_price_updates_the_price(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica la modificación del precio de una presentación."""
    _, box = register_product_with_box(product_service, presentations)

    updated = presentation_service.change_sale_price(
        box.id, Decimal("650.50")
    )

    assert updated.sale_price == Decimal("650.50")
    assert updated.product_id == box.product_id
    assert presentations.get(box.id) == updated


@pytest.mark.parametrize("sale_price", [Decimal("0"), Decimal("-5")])
def test_change_sale_price_rejects_an_invalid_price(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    sale_price: Decimal,
) -> None:
    """Verifica el rechazo de un precio inválido al modificar."""
    _, box = register_product_with_box(product_service, presentations)

    with pytest.raises(QuantityError):
        presentation_service.change_sale_price(box.id, sale_price)

    assert presentations.get(box.id).sale_price == Decimal("600.00")


def test_change_sale_price_rejects_a_deactivated_product(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que un producto desactivado no admite cambios de precio."""
    stored, box = register_product_with_box(
        product_service, presentations
    )
    product_service.deactivate(stored.id)

    with pytest.raises(InactiveProductError):
        presentation_service.change_sale_price(box.id, Decimal("650.00"))

    assert presentations.get(box.id).sale_price == Decimal("600.00")


def test_change_sale_price_requires_an_existing_presentation(
    presentation_service: PresentationService,
) -> None:
    """Verifica que modificar el precio de una presentación faltante falla."""
    with pytest.raises(RecordNotFoundError):
        presentation_service.change_sale_price(999, Decimal("650.00"))


# --- Factor mutable solo sin stock cerrado ni historial (criterio 11) ---


def test_change_factor_updates_a_presentation_without_stock_or_history(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que el factor se modifica sin stock cerrado ni historial."""
    _, box = register_product_with_box(product_service, presentations)

    updated = presentation_service.change_factor(box.id, 12)

    assert updated.factor == 12
    assert presentations.get(box.id).factor == 12


def test_change_factor_rejects_a_presentation_with_closed_stock(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que el factor no se modifica con stock cerrado."""
    _, box = register_product_with_box(
        product_service, presentations, closed_stock=2
    )

    with pytest.raises(FactorNotMutableError):
        presentation_service.change_factor(box.id, 12)

    assert presentations.get(box.id).factor == 6
    assert presentations.get(box.id).closed_stock == 2


def test_change_factor_rejects_a_presentation_with_history(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el factor no se modifica con movimientos históricos."""
    stored, box = register_product_with_box(
        product_service, presentations
    )
    movements.add(
        product_id=stored.id,
        presentation_id=box.id,
        kind=MovementKind.CLOSED_ENTRY,
        quantity=1,
        reason="Compra",
        occurred_at=START,
    )
    assert presentations.get(box.id).closed_stock == 0

    with pytest.raises(FactorNotMutableError):
        presentation_service.change_factor(box.id, 12)

    assert presentations.get(box.id).factor == 6


def test_change_factor_rejects_the_base_presentation(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que la presentación base (factor 1) no cambia de factor."""
    stored = product_service.register(make_registration())
    base = presentations.list_by_product(stored.id)[0]

    with pytest.raises(FactorNotMutableError):
        presentation_service.change_factor(base.id, 2)

    assert presentations.get(base.id).factor == 1


def test_change_factor_rejects_a_deactivated_product(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que un producto desactivado no admite cambios de factor."""
    stored, box = register_product_with_box(
        product_service, presentations
    )
    product_service.deactivate(stored.id)

    with pytest.raises(InactiveProductError):
        presentation_service.change_factor(box.id, 12)

    assert presentations.get(box.id).factor == 6


def test_change_factor_requires_an_existing_presentation(
    presentation_service: PresentationService,
) -> None:
    """Verifica que modificar el factor de una presentación faltante falla."""
    with pytest.raises(RecordNotFoundError):
        presentation_service.change_factor(999, 12)


def test_change_factor_rejects_a_second_base_presentation(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica el rechazo de un factor 1 que crearía una segunda base."""
    stored, box = register_product_with_box(product_service, presentations)

    with pytest.raises(PresentationError):
        presentation_service.change_factor(box.id, 1)

    assert presentations.get(box.id).factor == 6
    factors = [
        presentation.factor
        for presentation in presentations.list_by_product(stored.id)
    ]
    assert factors.count(1) == 1


# --- Regresión de los contratos existentes ---


def test_add_stores_the_price_with_the_canonical_decimal_text(
    presentation_service: PresentationService,
    product_service: ProductService,
    connection: sqlite3.Connection,
) -> None:
    """Verifica la serialización canónica del precio de la presentación."""
    stored = product_service.register(make_registration())

    added = presentation_service.add(
        stored.id,
        name="Caja x6",
        factor=6,
        sale_price=Decimal("1200.00"),
    )

    price_text = connection.execute(
        "SELECT sale_price FROM presentations WHERE id = ?",
        (added.id,),
    ).fetchone()["sale_price"]
    assert price_text == "1200"


def test_presentation_changes_do_not_touch_the_movement_history(
    presentation_service: PresentationService,
    product_service: ProductService,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que las presentaciones no alteran los movimientos."""
    stored = product_service.register(
        make_registration(initial_loose_units=2)
    )
    history_before = movements.list_by_product(stored.id)

    added = presentation_service.add(
        stored.id,
        name="Caja x6",
        factor=6,
        sale_price=Decimal("600.00"),
    )
    presentation_service.change_sale_price(added.id, Decimal("650.00"))

    assert movements.list_by_product(stored.id) == history_before
