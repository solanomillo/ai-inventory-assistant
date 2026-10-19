"""Pruebas de integración del servicio de presentaciones (T011 y T031).

Cubren los criterios de aceptación de T011 sobre RF-07: el alta y la
modificación de presentaciones según las reglas del dominio, el rechazo de
operaciones sobre productos desactivados, la presentación nueva con stock
cerrado cero sin alterar otros saldos y la inmutabilidad del factor cuando la
presentación tiene stock cerrado o historial.

Cubren además el cambio de precio atómico de T031 sobre RF-08: cada cambio se
registra en el historial con el precio vigente antes de la actualización,
dentro de la misma transacción; un fallo al registrar el historial revierte el
precio completo sin dejar cambios parciales.

Las pruebas usan la base SQLite en memoria con el esquema real, los
repositorios de T009 y T030 y el servicio de productos de T011 para preparar el
estado.
"""

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
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
    SqlitePriceHistoryRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.services.ports import (
    PRICE_CHANGE_EVENT_TYPE,
    PriceChangeRecord,
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
def price_history(
    connection: sqlite3.Connection,
) -> SqlitePriceHistoryRepository:
    """Repositorio del historial de precios sobre la base en memoria."""
    return SqlitePriceHistoryRepository(connection)


class _FailingPriceHistoryRepository:
    """Doble local del historial que falla al registrar el cambio (T031)."""

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


def test_change_sale_price_updates_the_price_and_records_the_change(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica la modificación del precio y la fila del historial (RF-08)."""
    stored, box = register_product_with_box(product_service, presentations)

    updated = presentation_service.change_sale_price(
        box.id, Decimal("650.50")
    )
    changes = price_history.list_by_presentation(box.id)

    assert updated.sale_price == Decimal("650.50")
    assert updated.product_id == box.product_id
    assert presentations.get(box.id) == updated
    assert len(changes) == 1
    change = changes[0]
    assert isinstance(change, PriceChangeRecord)
    assert change.product_id == stored.id
    assert change.presentation_id == box.id
    assert change.previous_price == box.sale_price
    assert change.new_price == Decimal("650.50")
    assert change.event_type == PRICE_CHANGE_EVENT_TYPE


def test_change_sale_price_records_the_price_before_each_change(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica el precio anterior de cada fila en una secuencia (RF-08)."""
    _, box = register_product_with_box(product_service, presentations)

    presentation_service.change_sale_price(box.id, Decimal("700"))
    presentation_service.change_sale_price(box.id, Decimal("800"))

    changes = price_history.list_by_presentation(box.id)

    assert [
        (change.previous_price, change.new_price) for change in changes
    ] == [
        (Decimal("600.00"), Decimal("700")),
        (Decimal("700"), Decimal("800")),
    ]


def test_change_sale_price_records_the_instant_in_utc(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que el instante del historial está en UTC y es actual."""
    _, box = register_product_with_box(product_service, presentations)
    before = datetime.now(timezone.utc)

    presentation_service.change_sale_price(box.id, Decimal("650"))

    after = datetime.now(timezone.utc)
    change = price_history.list_by_presentation(box.id)[0]

    assert change.occurred_at.tzinfo is not None
    assert change.occurred_at.utcoffset() == timedelta(0)
    assert before <= change.occurred_at <= after


def test_change_sale_price_records_the_base_presentation(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que el cambio de la presentación base también se registra."""
    stored = product_service.register(make_registration())
    base = presentations.list_by_product(stored.id)[0]

    presentation_service.change_sale_price(base.id, Decimal("150"))

    changes = price_history.list_by_presentation(base.id)

    assert base.factor == 1
    assert len(changes) == 1
    assert changes[0].previous_price == base.sale_price
    assert changes[0].new_price == Decimal("150")


def test_change_sale_price_records_a_change_to_the_same_price(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que un precio igual al vigente también se registra (RF-08)."""
    _, box = register_product_with_box(product_service, presentations)

    updated = presentation_service.change_sale_price(box.id, box.sale_price)

    changes = price_history.list_by_presentation(box.id)

    assert updated.sale_price == box.sale_price
    assert len(changes) == 1
    assert changes[0].previous_price == box.sale_price
    assert changes[0].new_price == box.sale_price


def test_registration_and_presentation_changes_record_no_price_change(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que el alta y el cambio de factor no generan historial."""
    stored, _ = register_product_with_box(
        product_service, presentations, closed_stock=1
    )
    added = presentation_service.add(
        stored.id,
        name="Caja x12",
        factor=12,
        sale_price=Decimal("1200"),
    )

    presentation_service.change_factor(added.id, 24)

    assert price_history.list_by_product(stored.id) == []


def test_change_sale_price_does_not_touch_balances_or_history(
    presentation_service: PresentationService,
    product_service: ProductService,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el cambio de precio no altera saldos ni movimientos."""
    stored, box = register_product_with_box(
        product_service, presentations, closed_stock=2
    )
    exit_movement = movements.add(
        product_id=stored.id,
        presentation_id=box.id,
        kind=MovementKind.CLOSED_EXIT,
        quantity=1,
        reason="Venta",
        occurred_at=START,
        applied_price=Decimal("900.00"),
    )
    balances_before = (
        products.get(stored.id).loose_units,
        presentations.get(box.id).closed_stock,
    )
    history_before = movements.list_by_product(stored.id)

    presentation_service.change_sale_price(box.id, Decimal("650"))

    reloaded_product = products.get(stored.id)
    reloaded_box = presentations.get(box.id)

    assert reloaded_product is not None
    assert reloaded_box is not None
    assert (
        reloaded_product.loose_units,
        reloaded_box.closed_stock,
    ) == balances_before
    assert movements.list_by_product(stored.id) == history_before
    assert movements.get(exit_movement.id).applied_price == Decimal("900.00")


@pytest.mark.parametrize(
    "sale_price", [Decimal("0"), Decimal("-5"), Decimal("NaN"), 10.5, "650"]
)
def test_change_sale_price_rejects_an_invalid_price(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
    sale_price: Any,
) -> None:
    """Verifica el rechazo de un precio inválido, sin cambio ni historial."""
    _, box = register_product_with_box(product_service, presentations)

    with pytest.raises(QuantityError):
        presentation_service.change_sale_price(box.id, sale_price)

    assert presentations.get(box.id).sale_price == Decimal("600.00")
    assert price_history.list_by_presentation(box.id) == []


def test_change_sale_price_rolls_back_when_the_history_fails(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
) -> None:
    """Verifica la reversión completa si el historial no se registra (T031)."""
    _, box = register_product_with_box(product_service, presentations)
    failing_service = PresentationService(
        products=products,
        presentations=presentations,
        movements=movements,
        price_history=_FailingPriceHistoryRepository(),
        transaction=partial(transaction, connection),
    )

    with pytest.raises(RuntimeError):
        failing_service.change_sale_price(box.id, Decimal("650.00"))

    reloaded = presentations.get(box.id)

    assert reloaded is not None
    assert reloaded.sale_price == box.sale_price
    assert connection.execute(
        "SELECT COUNT(*) AS total FROM price_changes"
    ).fetchone()["total"] == 0


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


def test_change_sale_price_records_the_immediately_previous_price(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """
    Verifica que el precio anterior es el vigente al aplicar el cambio (T036).

    Entre el alta de la presentación y el cambio hay una actualización externa
    del precio con el puerto público: el historial debe registrar 620 —el valor
    inmediatamente anterior, leído antes del `UPDATE`— y no 600, el precio que
    tenía la presentación cuando se registró.
    """
    _, box = register_product_with_box(product_service, presentations)
    presentations.set_sale_price(box.id, Decimal("620"))

    presentation_service.change_sale_price(box.id, Decimal("700"))

    changes = price_history.list_by_presentation(box.id)

    assert len(changes) == 1
    assert changes[0].previous_price == Decimal("620")
    assert changes[0].previous_price != Decimal("600")
    assert changes[0].new_price == Decimal("700")
    assert presentations.get(box.id).sale_price == Decimal("700")


def test_change_sale_price_of_a_missing_presentation_records_nothing(
    connection: sqlite3.Connection,
    presentation_service: PresentationService,
) -> None:
    """Verifica el rechazo de una presentación faltante sin filas (T036)."""
    with pytest.raises(RecordNotFoundError):
        presentation_service.change_sale_price(999, Decimal("650.00"))

    assert connection.execute(
        "SELECT COUNT(*) AS total FROM price_changes"
    ).fetchone()["total"] == 0


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
