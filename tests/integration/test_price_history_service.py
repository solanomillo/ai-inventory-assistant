"""Pruebas de integración del servicio de historial de precios (T034, RF-08).

Cubren los criterios de aceptación de T034 sobre RF-08: el historial se
consulta por presentación y por producto en orden cronológico, sin crear
movimientos ni modificar saldos; una entidad inexistente se informa como error;
una entidad existente sin cambios devuelve una lista vacía; y un producto
desactivado admite la consulta de su historial. También cubren que el cambio de
precio confirmado por el servicio de presentaciones (T031) aparece en el
historial consultado.

Cubren además el método de lectura `PresentationService.get_presentation` (D4):
devuelve la presentación existente sin exigir que su producto esté activo y
rechaza una presentación inexistente.

Las pruebas usan la base SQLite en memoria con el esquema real, los
repositorios de T009 y T030 y los servicios reales: el historial se siembra
con `SqlitePriceHistoryRepository.add` con un instante explícito y, para el
caso de integración, con `PresentationService.change_sale_price`.
"""

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import partial
from typing import Any

import pytest

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
from app.services.price_history import PriceHistoryService
from app.services.products import (
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
def price_history(
    connection: sqlite3.Connection,
) -> SqlitePriceHistoryRepository:
    """Repositorio del historial de precios sobre la base en memoria."""
    return SqlitePriceHistoryRepository(connection)


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
def service(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> PriceHistoryService:
    """Servicio de consulta del historial sobre la base en memoria."""
    return PriceHistoryService(
        products=products,
        presentations=presentations,
        price_history=price_history,
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


def register_product_with_box(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    *,
    sku: str = "SKU-1",
    name: str = "Producto",
    box_price: str = "600.00",
    closed_stock: int = 0,
) -> tuple[ProductRecord, PresentationRecord, PresentationRecord]:
    """Registra un producto con su base y una caja, y las devuelve."""
    stored = product_service.register(
        make_registration(
            sku=sku,
            name=name,
            presentations=(
                InitialClosedStock(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal(box_price),
                    closed_stock=closed_stock,
                ),
            ),
        )
    )
    base, box = presentations.list_by_product(stored.id)

    return stored, base, box


def seed_change(
    price_history: SqlitePriceHistoryRepository,
    product_id: int,
    presentation_id: int,
    previous_price: str,
    new_price: str,
    *,
    occurred_at: datetime = START,
) -> PriceChangeRecord:
    """Registra un cambio de precio con el instante indicado."""
    return price_history.add(
        product_id=product_id,
        presentation_id=presentation_id,
        previous_price=Decimal(previous_price),
        new_price=Decimal(new_price),
        occurred_at=occurred_at,
    )


def snapshot(
    connection: sqlite3.Connection,
) -> dict[str, list[tuple[Any, ...]]]:
    """Captura el contenido de las tablas, ordenado, para comparar."""
    tables = ("products", "presentations", "movements", "price_changes")

    return {
        table: connection.execute(
            f"SELECT * FROM {table} ORDER BY id"
        ).fetchall()
        for table in tables
    }


# --- Historial por presentación (criterio 1) ---


def test_get_price_history_by_presentation_returns_it_in_order(
    service: PriceHistoryService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica el orden cronológico y el desempate por id (RF-08)."""
    product, _base, box = register_product_with_box(
        product_service, presentations
    )
    first = seed_change(price_history, product.id, box.id, "600", "650")
    tied = seed_change(price_history, product.id, box.id, "650", "700")
    later = seed_change(
        price_history,
        product.id,
        box.id,
        "700",
        "750",
        occurred_at=START + timedelta(hours=1),
    )

    changes = service.get_price_history_by_presentation(box.id)

    assert changes == [first, tied, later]
    assert [change.id for change in changes] == [
        first.id,
        tied.id,
        later.id,
    ]
    assert [change.new_price for change in changes] == [
        Decimal("650"),
        Decimal("700"),
        Decimal("750"),
    ]


# --- Historial por producto (criterio 1) ---


def test_get_price_history_by_product_gathers_its_presentations(
    service: PriceHistoryService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que el historial por producto reúne sus presentaciones."""
    product, base, box = register_product_with_box(
        product_service, presentations
    )
    other, _other_base, other_box = register_product_with_box(
        product_service,
        presentations,
        sku="SKU-2",
        name="Otro producto",
    )
    box_change = seed_change(
        price_history,
        product.id,
        box.id,
        "600",
        "700",
        occurred_at=START + timedelta(hours=2),
    )
    base_change = seed_change(
        price_history, product.id, base.id, "100", "110"
    )
    other_change = seed_change(
        price_history, other.id, other_box.id, "600", "900"
    )

    changes = service.get_price_history_by_product(product.id)

    assert changes == [base_change, box_change]
    assert [change.product_id for change in changes] == [
        product.id,
        product.id,
    ]
    assert service.get_price_history_by_product(other.id) == [other_change]


def test_get_price_history_is_empty_without_changes(
    service: PriceHistoryService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que una entidad sin cambios devuelve una lista vacía."""
    product, _base, box = register_product_with_box(
        product_service, presentations
    )

    assert service.get_price_history_by_presentation(box.id) == []
    assert service.get_price_history_by_product(product.id) == []


# --- Errores de entidades inexistentes (criterio 4) ---


def test_get_price_history_by_presentation_requires_the_presentation(
    service: PriceHistoryService,
) -> None:
    """Verifica el error cuando la presentación no existe."""
    with pytest.raises(RecordNotFoundError, match="presentación con id 999"):
        service.get_price_history_by_presentation(999)


def test_get_price_history_by_product_requires_the_product(
    service: PriceHistoryService,
) -> None:
    """Verifica el error cuando el producto no existe."""
    with pytest.raises(RecordNotFoundError, match="producto con id 999"):
        service.get_price_history_by_product(999)


# --- Producto desactivado (criterio 5) ---


def test_get_price_history_by_product_admits_a_deactivated_product(
    service: PriceHistoryService,
    product_service: ProductService,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que un producto desactivado conserva su consulta (RF-01)."""
    product, _base, box = register_product_with_box(
        product_service, presentations
    )
    change = seed_change(price_history, product.id, box.id, "600", "700")

    product_service.deactivate(product.id)

    assert products.get(product.id).active is False
    assert service.get_price_history_by_product(product.id) == [change]
    assert service.get_price_history_by_presentation(box.id) == [change]


# --- Consulta de solo lectura (criterio 2) ---


def test_get_price_history_does_not_write(
    connection: sqlite3.Connection,
    service: PriceHistoryService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que la consulta no escribe ni altera saldos ni movimientos."""
    product, _base, box = register_product_with_box(
        product_service, presentations, closed_stock=2
    )
    seed_change(price_history, product.id, box.id, "600", "700")
    before = snapshot(connection)
    changes_before = connection.total_changes

    service.get_price_history_by_presentation(box.id)
    service.get_price_history_by_product(product.id)

    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


# --- Integración con el cambio de precio confirmado (criterio 2) ---


def test_a_confirmed_price_change_appears_in_the_history(
    service: PriceHistoryService,
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que el cambio confirmado por T031 queda en el historial."""
    product, _base, box = register_product_with_box(
        product_service, presentations
    )

    presentation_service.change_sale_price(box.id, Decimal("700.00"))

    changes = service.get_price_history_by_presentation(box.id)
    assert len(changes) == 1
    change = changes[0]
    assert change.product_id == product.id
    assert change.presentation_id == box.id
    assert change.previous_price == Decimal("600.00")
    assert change.new_price == Decimal("700.00")
    assert change.event_type == PRICE_CHANGE_EVENT_TYPE
    assert change.occurred_at.tzinfo is not None
    assert presentations.get(box.id).sale_price == Decimal("700.00")
    assert service.get_price_history_by_product(product.id) == changes


# --- Lectura de la presentación (decisión D4) ---


def test_get_presentation_returns_the_stored_presentation(
    presentation_service: PresentationService,
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que el método devuelve la presentación persistida."""
    _product, base, box = register_product_with_box(
        product_service, presentations, closed_stock=2
    )

    assert presentation_service.get_presentation(box.id) == box
    assert presentation_service.get_presentation(base.id) == base


def test_get_presentation_requires_an_existing_presentation(
    presentation_service: PresentationService,
) -> None:
    """Verifica que una presentación inexistente se informa como error."""
    with pytest.raises(RecordNotFoundError, match="presentación con id 999"):
        presentation_service.get_presentation(999)


def test_get_presentation_does_not_require_an_active_product(
    presentation_service: PresentationService,
    product_service: ProductService,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que la lectura no exige que el producto esté activo."""
    product, _base, box = register_product_with_box(
        product_service, presentations
    )

    product_service.deactivate(product.id)

    assert products.get(product.id).active is False
    assert presentation_service.get_presentation(box.id) == box
