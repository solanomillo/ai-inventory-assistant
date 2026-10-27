"""Pruebas de las herramientas de consulta del inventario (T019).

Cubren los contratos de las siete herramientas de solo lectura sobre RF-01,
RF-02, RF-03, RF-04 y RF-05: búsqueda parcial por nombre o SKU sin distinguir
mayúsculas, búsqueda por SKU, existencias con el total equivalente derivado,
presentaciones, stock bajo, reposición e historial con filtros combinados.

Las pruebas usan la base SQLite en memoria con el esquema real, los
repositorios de T009 y un inventario controlado, e invocan las herramientas con
`tool.invoke({...})`: no requieren el LLM, la API de DeepSeek, los agentes ni el
grafo. Además, comprueban con una instantánea de la base antes y después de
invocar todas las herramientas que las consultas no escriben nada.
"""

import inspect
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.domain.movements import MovementError, MovementKind
from app.domain.products import ProductError
from app.domain.stock import ClosedStock, calculate_shortage, total_equivalent
from app.infrastructure.sqlite.connection import (
    IN_MEMORY_DATABASE,
    connect,
)
from app.infrastructure.sqlite.instants import InvalidPeriodError
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
    RecordNotFoundError,
)
from app.tools.inventory_tools import (
    ProductStockSummary,
    ProductSummary,
    create_inventory_tools,
)

EARLY: datetime = datetime(2026, 3, 5, 12, 0, tzinfo=timezone.utc)
"""Instante temprano de los movimientos controlados."""
LATE: datetime = datetime(2026, 3, 25, 12, 0, tzinfo=timezone.utc)
"""Instante tardío de los movimientos controlados."""
PERIOD_START: datetime = datetime(2026, 3, 1, tzinfo=timezone.utc)
"""Inicio de un período que contiene solo los movimientos tempranos."""
PERIOD_END: datetime = datetime(2026, 3, 20, tzinfo=timezone.utc)
"""Final exclusivo de un período que contiene solo los tempranos."""


@dataclass(frozen=True)
class Catalog:
    """
    Inventario controlado de las pruebas (datos de prueba, no de la app).

    Se registra con los repositorios reales antes de invocar las herramientas.
    `cola` tiene existencias suficientes, `duplicate` tiene stock bajo con
    faltante positivo, `empty` tiene stock bajo con faltante cero, `inactive`
    está desactivado y `enough` no tiene stock bajo.
    """

    cola: ProductRecord
    cola_base: PresentationRecord
    cola_box: PresentationRecord
    duplicate: ProductRecord
    duplicate_base: PresentationRecord
    empty: ProductRecord
    inactive: ProductRecord
    enough: ProductRecord
    purchase: MovementRecord
    initial_entry: MovementRecord
    aperture: MovementRecord
    sale: MovementRecord
    other_entry: MovementRecord


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
def tools(service: InventoryService) -> dict[str, BaseTool]:
    """Herramientas de consulta indexadas por su nombre."""
    return {
        created.name: created for created in create_inventory_tools(service)
    }


@pytest.fixture
def catalog(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> Catalog:
    """Registra el inventario controlado con los repositorios reales."""
    cola = products.add(
        sku="SKU-1",
        name="Coca Cola",
        base_unit="botella",
        minimum_stock=10,
        loose_units=4,
    )
    cola_base = presentations.add(
        product_id=cola.id,
        name="botella",
        factor=1,
        sale_price=Decimal("100"),
    )
    cola_box = presentations.add(
        product_id=cola.id,
        name="Caja x6",
        factor=6,
        sale_price=Decimal("600"),
        closed_stock=2,
    )

    duplicate = products.add(
        sku="SKU-2",
        name="coca cola",
        base_unit="unidad",
        minimum_stock=10,
        loose_units=3,
    )
    duplicate_base = presentations.add(
        product_id=duplicate.id,
        name="unidad",
        factor=1,
        sale_price=Decimal("50"),
    )
    presentations.add(
        product_id=duplicate.id,
        name="Pack x3",
        factor=3,
        sale_price=Decimal("120"),
        closed_stock=1,
    )

    empty = products.add(
        sku="SKU-3",
        name="Agua",
        base_unit="botella",
        minimum_stock=0,
    )
    presentations.add(
        product_id=empty.id,
        name="botella",
        factor=1,
        sale_price=Decimal("80"),
    )

    inactive = products.add(
        sku="SKU-4",
        name="Inactivo",
        base_unit="unidad",
        minimum_stock=50,
        active=False,
    )
    presentations.add(
        product_id=inactive.id,
        name="unidad",
        factor=1,
        sale_price=Decimal("10"),
    )

    enough = products.add(
        sku="SKU-5",
        name="Aceite",
        base_unit="botella",
        minimum_stock=5,
        loose_units=9,
    )
    presentations.add(
        product_id=enough.id,
        name="botella",
        factor=1,
        sale_price=Decimal("200"),
    )

    purchase = movements.add(
        product_id=cola.id,
        presentation_id=cola_box.id,
        kind=MovementKind.CLOSED_ENTRY,
        quantity=2,
        reason="Compra",
        occurred_at=EARLY,
        cost=Decimal("120"),
        is_initial_stock=True,
    )
    initial_entry = movements.add(
        product_id=cola.id,
        presentation_id=cola_base.id,
        kind=MovementKind.LOOSE_ENTRY,
        quantity=4,
        reason="Stock inicial",
        occurred_at=EARLY,
        is_initial_stock=True,
    )
    aperture = movements.add(
        product_id=cola.id,
        presentation_id=cola_box.id,
        kind=MovementKind.APERTURE,
        quantity=1,
        reason="Apertura",
        occurred_at=LATE,
    )
    sale = movements.add(
        product_id=cola.id,
        presentation_id=cola_base.id,
        kind=MovementKind.LOOSE_EXIT,
        quantity=1,
        reason="Venta",
        occurred_at=LATE,
        applied_price=Decimal("150"),
    )
    other_entry = movements.add(
        product_id=duplicate.id,
        presentation_id=duplicate_base.id,
        kind=MovementKind.LOOSE_ENTRY,
        quantity=3,
        reason="Stock inicial",
        occurred_at=EARLY,
        is_initial_stock=True,
    )

    return Catalog(
        cola=cola,
        cola_base=cola_base,
        cola_box=cola_box,
        duplicate=duplicate,
        duplicate_base=duplicate_base,
        empty=empty,
        inactive=inactive,
        enough=enough,
        purchase=purchase,
        initial_entry=initial_entry,
        aperture=aperture,
        sale=sale,
        other_entry=other_entry,
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


# --- Búsqueda parcial por nombre o SKU (RF-01) ---


@pytest.mark.parametrize("query", ["Coca Cola", "coca cola", "COCA COLA"])
def test_find_products_by_name_matches_without_case(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    query: str,
) -> None:
    """Verifica la coincidencia sin distinguir mayúsculas."""
    found = tools["find_products_by_name"].invoke({"name": query})

    assert [product.id for product in found] == [
        catalog.cola.id,
        catalog.duplicate.id,
    ]
    assert [product.name for product in found] == ["Coca Cola", "coca cola"]
    assert all(isinstance(product, ProductSummary) for product in found)
    assert [product.sku for product in found] == ["SKU-1", "SKU-2"]


def test_find_products_by_name_returns_every_match(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que se devuelven todas las coincidencias, sin seleccionar."""
    found = tools["find_products_by_name"].invoke({"name": "coca cola"})

    assert len(found) == 2
    assert {product.name for product in found} == {"Coca Cola", "coca cola"}


def test_find_products_by_name_returns_no_match_as_empty_list(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que una consulta sin coincidencias devuelve una lista vacía."""
    assert tools["find_products_by_name"].invoke({"name": "Inexistente"}) == []


def test_find_products_by_name_rejects_a_blank_name(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica que un nombre en blanco se rechaza con el error del dominio."""
    with pytest.raises(ProductError):
        tools["find_products_by_name"].invoke({"name": "   "})


def test_find_products_by_name_validates_its_input_schema(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica que la entrada de la herramienta se valida con Pydantic."""
    with pytest.raises(ValidationError):
        tools["find_products_by_name"].invoke({"name": ""})

    with pytest.raises(ValidationError):
        tools["find_products_by_name"].invoke({"nombre": "Coca Cola"})


@pytest.mark.parametrize("query", ["Coca", "Coca-Cola", "coca_cola"])
def test_find_products_by_name_matches_a_partial_name(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    query: str,
) -> None:
    """Verifica la coincidencia parcial por nombre normalizado."""
    found = tools["find_products_by_name"].invoke({"name": query})

    assert [product.id for product in found] == [
        catalog.cola.id,
        catalog.duplicate.id,
    ]
    assert all(isinstance(product, ProductSummary) for product in found)


@pytest.mark.parametrize("query", ["KU-2", "sku_2", "Sku 2"])
def test_find_products_by_name_matches_a_partial_sku(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    query: str,
) -> None:
    """Verifica la coincidencia parcial por SKU normalizado."""
    found = tools["find_products_by_name"].invoke({"name": query})

    assert [product.id for product in found] == [catalog.duplicate.id]
    assert found[0].sku == "SKU-2"


def test_find_products_by_name_returns_all_the_matches_without_selecting(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que varias coincidencias se devuelven todas, sin elegir una."""
    found = tools["find_products_by_name"].invoke({"name": "sku"})

    assert isinstance(found, list)
    assert {product.id for product in found} == {
        catalog.cola.id,
        catalog.duplicate.id,
        catalog.empty.id,
        catalog.inactive.id,
        catalog.enough.id,
    }
    assert len(found) == 5


def test_find_products_by_name_prioritizes_an_exact_sku(
    tools: dict[str, BaseTool],
    products: SqliteProductRepository,
) -> None:
    """Verifica que el SKU exacto precede al nombre exacto."""
    name_exact = products.add(
        sku="SKU-10",
        name="Agua 2L",
        base_unit="botella",
    )
    sku_exact = products.add(
        sku="Agua-2L",
        name="Agua dos",
        base_unit="botella",
    )

    found = tools["find_products_by_name"].invoke({"name": "Agua 2L"})

    assert [product.id for product in found] == [
        sku_exact.id,
        name_exact.id,
    ]
    assert found[0].sku == "Agua-2L"
    assert found[1].name == "Agua 2L"


def test_find_products_by_name_includes_inactive_products(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que los productos desactivados también son coincidencias."""
    found = tools["find_products_by_name"].invoke({"name": "SKU-4"})

    assert [product.id for product in found] == [catalog.inactive.id]
    assert found[0].active is False


def test_find_products_by_name_keeps_the_exact_name_match(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica la regresión de la coincidencia exacta por nombre."""
    found = tools["find_products_by_name"].invoke({"name": "Coca Cola"})

    assert [product.id for product in found] == [
        catalog.cola.id,
        catalog.duplicate.id,
    ]
    assert [product.name for product in found] == ["Coca Cola", "coca cola"]


def test_find_products_by_name_description_announces_the_partial_search(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica que la descripción ya no anuncia una coincidencia exacta."""
    description = (tools["find_products_by_name"].description or "").lower()

    assert "parcial" in description
    assert "sku" in description
    assert "coincide exactamente" not in description


# --- Búsqueda por SKU (RF-01) ---


@pytest.mark.parametrize("sku", ["SKU-1", "sku-1", "Sku-1"])
def test_find_product_by_sku_matches_without_case(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    sku: str,
) -> None:
    """Verifica la búsqueda directa por SKU sin distinguir mayúsculas."""
    found = tools["find_product_by_sku"].invoke({"sku": sku})

    assert isinstance(found, ProductSummary)
    assert found.id == catalog.cola.id
    assert found.sku == "SKU-1"
    assert found.name == "Coca Cola"


def test_find_product_by_sku_returns_none_when_it_does_not_exist(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica que un SKU inexistente devuelve `None`."""
    assert tools["find_product_by_sku"].invoke({"sku": "SKU-99"}) is None


# --- Existencias (RF-02) ---


def test_get_product_stock_derives_the_total_equivalent(
    connection: sqlite3.Connection,
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica el total equivalente contra el cálculo del dominio."""
    stock = tools["get_product_stock"].invoke(
        {"product_id": catalog.cola.id}
    )
    rows = connection.execute(
        "SELECT factor, closed_stock FROM presentations "
        "WHERE product_id = ? ORDER BY id",
        (catalog.cola.id,),
    ).fetchall()
    loose_units = connection.execute(
        "SELECT loose_units FROM products WHERE id = ?",
        (catalog.cola.id,),
    ).fetchone()["loose_units"]
    expected_closed = [
        ClosedStock(factor=row["factor"], quantity=row["closed_stock"])
        for row in rows
    ]

    assert isinstance(stock, ProductStockSummary)
    assert stock.loose_units == loose_units == 4
    assert stock.total_equivalent == total_equivalent(
        loose_units, expected_closed
    )
    assert stock.total_equivalent == 16
    assert [entry.closed_stock for entry in stock.closed_stocks] == [0, 2]
    assert [entry.factor for entry in stock.closed_stocks] == [1, 6]
    assert [entry.equivalent_units for entry in stock.closed_stocks] == [0, 12]
    assert [entry.name for entry in stock.closed_stocks] == [
        "botella",
        "Caja x6",
    ]
    assert stock.product.sku == "SKU-1"


def test_get_product_stock_requires_an_existing_product(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica que un `product_id` inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        tools["get_product_stock"].invoke({"product_id": 999})


# --- Presentaciones (RF-07) ---


def test_get_product_presentations_informs_the_factor_and_the_stock(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica las presentaciones con su factor y su stock cerrado."""
    found = tools["get_product_presentations"].invoke(
        {"product_id": catalog.cola.id}
    )

    assert [presentation.id for presentation in found] == [
        catalog.cola_base.id,
        catalog.cola_box.id,
    ]
    assert [presentation.name for presentation in found] == [
        "botella",
        "Caja x6",
    ]
    assert [presentation.factor for presentation in found] == [1, 6]
    assert [presentation.closed_stock for presentation in found] == [0, 2]
    assert [presentation.sale_price for presentation in found] == [
        Decimal("100"),
        Decimal("600"),
    ]


def test_get_product_presentations_requires_an_existing_product(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica que un `product_id` inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        tools["get_product_presentations"].invoke({"product_id": 999})


# --- Stock bajo y reposición (RF-04) ---


def test_get_low_stock_products_excludes_inactive_products(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica el stock bajo de los activos y la exclusión del inactivo."""
    alerts = tools["get_low_stock_products"].invoke({})
    by_id = {alert.product.id: alert for alert in alerts}

    assert [alert.product.id for alert in alerts] == [
        catalog.duplicate.id,
        catalog.empty.id,
    ]
    assert catalog.inactive.id not in by_id
    assert catalog.cola.id not in by_id
    assert catalog.enough.id not in by_id
    assert by_id[catalog.duplicate.id].total_equivalent == 6
    assert by_id[catalog.duplicate.id].shortage == 4
    assert by_id[catalog.empty.id].total_equivalent == 0
    assert by_id[catalog.empty.id].shortage == 0
    assert by_id[catalog.duplicate.id].minimum_stock == 10


def test_get_replenishment_products_requires_a_positive_shortage(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica la reposición de los activos con faltante mayor que cero."""
    alerts = tools["get_replenishment_products"].invoke({})

    assert [alert.product.id for alert in alerts] == [catalog.duplicate.id]
    assert alerts[0].shortage == calculate_shortage(
        loose_units=catalog.duplicate.loose_units,
        closed_stocks=(ClosedStock(factor=3, quantity=1),),
        minimum_stock=catalog.duplicate.minimum_stock,
    )
    assert alerts[0].shortage == 4


def test_get_replenishment_products_excludes_the_inactive_product(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que un producto desactivado no se propone para reposición."""
    alerts = tools["get_replenishment_products"].invoke({})

    assert catalog.inactive.id not in {
        alert.product.id for alert in alerts
    }


# --- Historial de movimientos (RF-03) ---


def test_get_movement_history_returns_the_full_history_in_order(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica el historial completo, en orden cronológico."""
    history = tools["get_movement_history"].invoke(
        {"product_id": catalog.cola.id}
    )

    assert [movement.id for movement in history] == [
        catalog.purchase.id,
        catalog.initial_entry.id,
        catalog.aperture.id,
        catalog.sale.id,
    ]
    assert [movement.kind for movement in history] == [
        MovementKind.CLOSED_ENTRY,
        MovementKind.LOOSE_ENTRY,
        MovementKind.APERTURE,
        MovementKind.LOOSE_EXIT,
    ]
    assert history[0].cost == Decimal("120")
    assert history[0].is_initial_stock is True
    assert history[3].applied_price == Decimal("150")


def test_get_movement_history_filters_by_product(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que cada producto solo devuelve sus propios movimientos."""
    history = tools["get_movement_history"].invoke(
        {"product_id": catalog.duplicate.id}
    )

    assert [movement.id for movement in history] == [catalog.other_entry.id]


def test_get_movement_history_filters_by_kind(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica el filtro por tipo de movimiento."""
    history = tools["get_movement_history"].invoke(
        {
            "product_id": catalog.cola.id,
            "kind": "apertura",
        }
    )

    assert [movement.id for movement in history] == [catalog.aperture.id]


def test_get_movement_history_excludes_the_period_end(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica la semántica `[inicio, fin)` del período."""
    history = tools["get_movement_history"].invoke(
        {
            "product_id": catalog.cola.id,
            "period_start": EARLY.isoformat(),
            "period_end": LATE.isoformat(),
        }
    )

    assert [movement.id for movement in history] == [
        catalog.purchase.id,
        catalog.initial_entry.id,
    ]
    assert catalog.sale.id not in {
        movement.id for movement in history
    }


def test_get_movement_history_combines_the_filters_with_and(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que los filtros de tipo y período se combinan."""
    included = tools["get_movement_history"].invoke(
        {
            "product_id": catalog.cola.id,
            "kind": "entrada_cerrada",
            "period_start": PERIOD_START.isoformat(),
            "period_end": PERIOD_END.isoformat(),
        }
    )
    excluded = tools["get_movement_history"].invoke(
        {
            "product_id": catalog.cola.id,
            "kind": "salida_suelta",
            "period_start": PERIOD_START.isoformat(),
            "period_end": PERIOD_END.isoformat(),
        }
    )

    assert [movement.id for movement in included] == [catalog.purchase.id]
    assert excluded == []


def test_get_movement_history_requires_an_existing_product(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica que un `product_id` inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        tools["get_movement_history"].invoke({"product_id": 999})


def test_get_movement_history_rejects_an_invalid_period(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que un período inválido se rechaza con el error existente."""
    with pytest.raises(InvalidPeriodError):
        tools["get_movement_history"].invoke(
            {
                "product_id": catalog.cola.id,
                "period_start": PERIOD_END.isoformat(),
                "period_end": PERIOD_START.isoformat(),
            }
        )


def test_get_movement_history_rejects_an_unknown_kind(
    service: InventoryService,
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica el rechazo del tipo de movimiento desconocido."""
    with pytest.raises(MovementError):
        service.get_movement_history(catalog.cola.id, "desconocido")

    with pytest.raises(ValidationError):
        tools["get_movement_history"].invoke(
            {
                "product_id": catalog.cola.id,
                "kind": "desconocido",
            }
        )


# --- Contrato de las herramientas ---


def test_the_tools_expose_the_expected_names(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica el nombre y la descripción de cada herramienta."""
    assert set(tools) == {
        "find_products_by_name",
        "find_product_by_sku",
        "get_product_stock",
        "get_product_presentations",
        "get_low_stock_products",
        "get_replenishment_products",
        "get_movement_history",
    }
    assert all(created.description for created in tools.values())


def test_inventory_tools_do_not_need_the_llm_configuration(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que las herramientas no necesitan el LLM ni su clave."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    stock = tools["get_product_stock"].invoke({"product_id": catalog.cola.id})

    assert stock.total_equivalent == 16


def test_inventory_modules_do_not_depend_on_infrastructure_or_agents() -> None:
    """Verifica que los módulos no importan persistencia ni agentes."""
    import app.services.inventory as service_module
    import app.tools.inventory_tools as tools_module

    forbidden = (
        "app.infrastructure",
        "import sqlite3",
        "app.agents",
        "app.graph",
        "app.services.llm",
        "ChatDeepSeek",
        "get_llm",
        "DEEPSEEK",
    )
    for module in (tools_module, service_module):
        source = inspect.getsource(module)
        for token in forbidden:
            assert token not in source, (
                f"El módulo {module.__name__} no debe contener `{token}`."
            )


# --- Solo lectura ---


def test_inventory_tools_do_not_write_to_the_database(
    connection: sqlite3.Connection,
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica que invocar todas las herramientas no modifica la base."""
    before = snapshot(connection)
    changes_before = connection.total_changes

    tools["find_products_by_name"].invoke({"name": "Coca Cola"})
    tools["find_products_by_name"].invoke({"name": "Inexistente"})
    tools["find_product_by_sku"].invoke({"sku": "SKU-1"})
    tools["find_product_by_sku"].invoke({"sku": "SKU-99"})
    tools["get_product_stock"].invoke({"product_id": catalog.cola.id})
    tools["get_product_stock"].invoke({"product_id": catalog.duplicate.id})
    tools["get_product_presentations"].invoke(
        {"product_id": catalog.cola.id}
    )
    tools["get_low_stock_products"].invoke({})
    tools["get_replenishment_products"].invoke({})
    tools["get_movement_history"].invoke({"product_id": catalog.cola.id})
    tools["get_movement_history"].invoke(
        {
            "product_id": catalog.cola.id,
            "kind": "entrada_cerrada",
            "period_start": PERIOD_START.isoformat(),
            "period_end": PERIOD_END.isoformat(),
        }
    )
    tools["get_movement_history"].invoke(
        {"product_id": catalog.inactive.id}
    )

    assert snapshot(connection) == before
    assert connection.total_changes == changes_before

    balances = {
        row["id"]: row["loose_units"]
        for row in connection.execute(
            "SELECT id, loose_units FROM products"
        )
    }
    assert balances == {
        catalog.cola.id: catalog.cola.loose_units,
        catalog.duplicate.id: catalog.duplicate.loose_units,
        catalog.empty.id: catalog.empty.loose_units,
        catalog.inactive.id: catalog.inactive.loose_units,
        catalog.enough.id: catalog.enough.loose_units,
    }
    closed_stocks = {
        row["id"]: row["closed_stock"]
        for row in connection.execute(
            "SELECT id, closed_stock FROM presentations"
        )
    }
    assert closed_stocks[catalog.cola_box.id] == 2
    assert closed_stocks[catalog.cola_box.id] == (
        catalog.cola_box.closed_stock
    )
    assert closed_stocks[catalog.cola_base.id] == 0
