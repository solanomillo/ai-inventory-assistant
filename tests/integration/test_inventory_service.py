"""Pruebas de integración del servicio de inventario (T040).

Cubren la lectura pública del listado de productos añadida a
`InventoryService` para RF-09 (`list_products`), de forma aditiva y sin alterar
las lecturas existentes: incluye activos e inactivos con su estado, hereda el
orden de registro del repositorio y no escribe en la base.

También verifican la regresión de las lecturas existentes (búsqueda por nombre
y por SKU, existencias, presentaciones e historial), que las alertas siguen
excluyendo los productos inactivos y que la fábrica de herramientas del asistente
sigue devolviendo siete herramientas.

Las pruebas usan la base SQLite en memoria con el esquema real y los
repositorios `Sqlite*`: no requieren el LLM, la API de DeepSeek, los agentes ni
el grafo.
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.domain.movements import MovementKind
from app.infrastructure.sqlite.connection import (
    IN_MEMORY_DATABASE,
    connect,
)
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.services.inventory import InventoryService
from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
)
from app.tools.inventory_tools import create_inventory_tools

EARLY: datetime = datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc)
"""Instante de un movimiento controlado."""


@dataclass(frozen=True)
class Catalog:
    """
    Inventario controlado de las pruebas (datos de prueba, no de la app).

    Se registra con los repositorios reales antes de ejercitar el servicio.
    `active_low` es un producto activo con stock bajo y faltante positivo,
    `inactive_low` está desactivado y también tiene stock bajo, y `active_enough`
    no tiene stock bajo.
    """

    active_low: ProductRecord
    inactive_low: ProductRecord
    active_enough: ProductRecord
    movement: MovementRecord


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
def service(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> InventoryService:
    """Servicio de consultas con los repositorios reales."""
    return InventoryService(
        products=products,
        presentations=presentations,
        movements=movements,
    )


@pytest.fixture
def catalog(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> Catalog:
    """Registra el inventario controlado con los repositorios reales."""
    active_low = products.add(
        sku="SKU-1",
        name="Activo con stock bajo",
        base_unit="unidad",
        minimum_stock=10,
        loose_units=3,
    )
    active_low_base = presentations.add(
        product_id=active_low.id,
        name="unidad",
        factor=1,
        sale_price=Decimal("100"),
    )

    inactive_low = products.add(
        sku="SKU-2",
        name="Inactivo con stock bajo",
        base_unit="unidad",
        minimum_stock=10,
        loose_units=0,
        active=False,
    )
    presentations.add(
        product_id=inactive_low.id,
        name="unidad",
        factor=1,
        sale_price=Decimal("50"),
    )

    active_enough = products.add(
        sku="SKU-3",
        name="Activo con stock suficiente",
        base_unit="unidad",
        minimum_stock=5,
        loose_units=9,
    )
    presentations.add(
        product_id=active_enough.id,
        name="unidad",
        factor=1,
        sale_price=Decimal("200"),
    )

    movement = movements.add(
        product_id=active_low.id,
        presentation_id=active_low_base.id,
        kind=MovementKind.LOOSE_ENTRY,
        quantity=3,
        reason="Stock inicial",
        occurred_at=EARLY,
        is_initial_stock=True,
    )

    return Catalog(
        active_low=active_low,
        inactive_low=inactive_low,
        active_enough=active_enough,
        movement=movement,
    )


def snapshot(
    connection: sqlite3.Connection,
) -> dict[str, list[tuple[Any, ...]]]:
    """Captura el contenido de las tablas del inventario, ordenado."""
    tables = ("products", "presentations", "movements", "proposals")

    return {
        table: [
            tuple(row)
            for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY id"
            ).fetchall()
        ]
        for table in tables
    }


# --- Listado público de productos (RF-09) ---


def test_list_products_includes_active_and_inactive_with_their_status(
    service: InventoryService,
    catalog: Catalog,
) -> None:
    """Verifica que el listado incluye activos e inactivos con su estado."""
    listed = service.list_products()

    assert [product.id for product in listed] == [
        catalog.active_low.id,
        catalog.inactive_low.id,
        catalog.active_enough.id,
    ]
    assert [product.name for product in listed] == [
        "Activo con stock bajo",
        "Inactivo con stock bajo",
        "Activo con stock suficiente",
    ]
    assert [product.active for product in listed] == [True, False, True]
    assert all(isinstance(product, ProductRecord) for product in listed)


def test_list_products_inherits_the_repository_order(
    service: InventoryService,
    products: SqliteProductRepository,
    catalog: Catalog,
) -> None:
    """Verifica que el listado hereda el orden de registro por `id`."""
    listed = service.list_products()

    assert [product.id for product in listed] == [
        product.id for product in products.list_all()
    ]
    assert [product.id for product in listed] == sorted(
        product.id for product in listed
    )


def test_list_products_returns_an_empty_list_on_an_empty_catalog(
    service: InventoryService,
) -> None:
    """Verifica que un catálogo vacío devuelve una lista vacía."""
    assert service.list_products() == []


def test_list_products_does_not_write_to_the_database(
    connection: sqlite3.Connection,
    service: InventoryService,
    catalog: Catalog,
) -> None:
    """Verifica que la lectura pública no modifica la base."""
    before = snapshot(connection)
    changes_before = connection.total_changes

    listed = service.list_products()

    assert [product.id for product in listed] == [
        catalog.active_low.id,
        catalog.inactive_low.id,
        catalog.active_enough.id,
    ]
    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


# --- Alertas solo de activos (RF-04) ---


def test_low_stock_alerts_exclude_the_inactive_product(
    service: InventoryService,
    catalog: Catalog,
) -> None:
    """Verifica que el stock bajo solo considera productos activos."""
    alerts = service.get_low_stock_products()
    alert_ids = {alert.product.id for alert in alerts}

    assert alert_ids == {catalog.active_low.id}
    assert catalog.inactive_low.id not in alert_ids


def test_replenishment_alerts_exclude_the_inactive_product(
    service: InventoryService,
    catalog: Catalog,
) -> None:
    """Verifica que la reposición solo considera productos activos."""
    alerts = service.get_replenishment_products()
    alert_ids = {alert.product.id for alert in alerts}

    assert alert_ids == {catalog.active_low.id}
    assert catalog.inactive_low.id not in alert_ids


# --- Regresión de las lecturas existentes ---


def test_existing_reads_still_work(
    service: InventoryService,
    catalog: Catalog,
) -> None:
    """Verifica que las lecturas existentes conservan su contrato."""
    by_name = service.find_products_by_name("Activo con stock bajo")
    by_sku = service.find_product_by_sku("SKU-1")
    stock = service.get_product_stock(catalog.active_low.id)
    presented = service.get_product_presentations(catalog.active_low.id)
    history = service.get_movement_history(catalog.active_low.id)

    assert [product.id for product in by_name] == [catalog.active_low.id]
    assert by_sku is not None
    assert by_sku.id == catalog.active_low.id
    assert stock.product.id == catalog.active_low.id
    assert stock.total_equivalent == 3
    assert [presentation.id for presentation in presented] == [
        presentation.id
        for presentation in presented
        if presentation.product_id == catalog.active_low.id
    ]
    assert [movement.id for movement in history] == [catalog.movement.id]


def test_create_inventory_tools_still_returns_seven_tools(
    service: InventoryService,
) -> None:
    """Verifica que la fábrica del asistente sigue devolviendo siete."""
    created = create_inventory_tools(service)

    assert len(created) == 7