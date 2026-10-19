"""Pruebas de integración de los repositorios SQLite (T009).

Cubren los criterios de aceptación de T009 sobre RF-01, RF-03 y RF-07: la
unicidad sin distinguir mayúsculas de minúsculas, el rechazo de movimientos sin
motivo, la ausencia de operaciones para editar o eliminar movimientos
confirmados, los filtros por producto y tipo, las búsquedas por SKU y por
nombre y la recuperación de los datos necesarios para reconstruir saldos y
consultar el historial de movimientos.
"""

import ast
import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from inspect import getmembers, getsource, isfunction
from typing import Any

import pytest

from app.domain.movements import ClosedEntry, MovementKind
from app.domain.presentations import DuplicatePresentationNameError
from app.domain.products import DuplicateSkuError
from app.domain.stock import ClosedStock, total_equivalent
from app.infrastructure.sqlite import repositories as repositories_module
from app.infrastructure.sqlite.connection import IN_MEMORY_DATABASE, connect
from app.infrastructure.sqlite.repositories import (
    NotAClosedEntryError,
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqlitePriceHistoryRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.services.ports import (
    PRICE_CHANGE_EVENT_TYPE,
    MovementRecord,
    MovementRepository,
    PriceChangeRecord,
    PriceHistoryRepository,
    ProductRecord,
    PresentationRecord,
    RecordNotFoundError,
)

START: datetime = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

EARLIER: datetime = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)

LATER: datetime = datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc)

# Operaciones que la persistencia no debe exponer sobre un movimiento
# confirmado: los movimientos solo se registran y se consultan (RF-03, RF-06).
FORBIDDEN_MOVEMENT_OPERATIONS: tuple[str, ...] = (
    "update",
    "delete",
    "remove",
    "edit",
    "replace",
    "truncate",
    "drop",
)

# Operaciones que la persistencia no debe exponer sobre el historial de
# precios: el historial solo se anexa y se consulta (RF-08, T030).
FORBIDDEN_PRICE_CHANGE_OPERATIONS: tuple[str, ...] = (
    "update",
    "delete",
    "remove",
    "edit",
    "replace",
    "truncate",
    "drop",
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
def price_history(
    connection: sqlite3.Connection,
) -> SqlitePriceHistoryRepository:
    """Repositorio del historial de precios sobre la base en memoria."""
    return SqlitePriceHistoryRepository(connection)


def register_product(
    products: SqliteProductRepository,
    *,
    sku: str = "SKU-1",
    name: str = "Producto",
    base_unit: str = "unidad",
    minimum_stock: int = 0,
    loose_units: int = 0,
    active: bool = True,
) -> ProductRecord:
    """Registra un producto válido y devuelve su versión persistida."""
    return products.add(
        sku=sku,
        name=name,
        base_unit=base_unit,
        minimum_stock=minimum_stock,
        loose_units=loose_units,
        active=active,
    )


def register_presentation(
    presentations: SqlitePresentationRepository,
    product_id: int,
    *,
    name: str = "Caja x6",
    factor: int = 6,
    sale_price: str = "1200.00",
    closed_stock: int = 0,
) -> PresentationRecord:
    """Registra una presentación válida y devuelve su versión persistida."""
    return presentations.add(
        product_id=product_id,
        name=name,
        factor=factor,
        sale_price=Decimal(sale_price),
        closed_stock=closed_stock,
    )


def register_movement(
    movements: SqliteMovementRepository,
    product_id: int,
    presentation_id: int,
    *,
    kind: MovementKind = MovementKind.CLOSED_ENTRY,
    quantity: int = 1,
    reason: str = "Compra",
    occurred_at: datetime = START,
    **extra: Any,
) -> MovementRecord:
    """Registra un movimiento válido y devuelve su versión persistida."""
    return movements.add(
        product_id=product_id,
        presentation_id=presentation_id,
        kind=kind,
        quantity=quantity,
        reason=reason,
        occurred_at=occurred_at,
        **extra,
    )


def public_operations(repository: type) -> set[str]:
    """Devuelve los nombres de las operaciones públicas de un repositorio."""
    return {
        name
        for name, _ in getmembers(repository, isfunction)
        if not name.startswith("_")
    }


def register_price_change(
    price_history: SqlitePriceHistoryRepository,
    product_id: int,
    presentation_id: int,
    *,
    previous_price: Decimal | str = "10.5",
    new_price: Decimal | str = "12",
    occurred_at: datetime = START,
) -> PriceChangeRecord:
    """Registra un cambio de precio válido y devuelve su versión persistida."""
    return price_history.add(
        product_id=product_id,
        presentation_id=presentation_id,
        previous_price=Decimal(previous_price),
        new_price=Decimal(new_price),
        occurred_at=occurred_at,
    )


def stored_price_change(
    connection: sqlite3.Connection, price_change_id: int
) -> dict[str, Any]:
    """Devuelve la fila persistida del cambio de precio como diccionario."""
    row = connection.execute(
        "SELECT * FROM price_changes WHERE id = ?", (price_change_id,)
    ).fetchone()
    assert row is not None

    return dict(row)


def sql_fragments_mentioning(source: str, table: str) -> list[str]:
    """Devuelve los textos del módulo que mencionan la tabla indicada."""
    tree = ast.parse(source)

    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and table in node.value
    ]


def stored_row(
    connection: sqlite3.Connection, movement_id: int
) -> dict[str, Any]:
    """Devuelve la fila persistida del movimiento como diccionario."""
    row = connection.execute(
        "SELECT * FROM movements WHERE id = ?", (movement_id,)
    ).fetchone()
    assert row is not None

    return dict(row)


# --- Unicidad sin distinguir mayúsculas de minúsculas (criterio 1) ---


def test_add_product_rejects_a_duplicate_sku(
    products: SqliteProductRepository,
) -> None:
    """Verifica que un SKU ya registrado se rechaza."""
    register_product(products, sku="SKU-1")

    with pytest.raises(DuplicateSkuError):
        register_product(products, sku="SKU-1", name="Otro producto")


@pytest.mark.parametrize("duplicated_sku", ["sku-1", "SKU-1", "Sku-1"])
def test_add_product_rejects_a_sku_that_differs_only_in_case(
    products: SqliteProductRepository, duplicated_sku: str
) -> None:
    """Verifica que el SKU es único sin distinguir mayúsculas de minúsculas."""
    register_product(products, sku="sKu-1")

    with pytest.raises(DuplicateSkuError):
        register_product(products, sku=duplicated_sku)


def test_duplicate_sku_is_not_persisted(
    products: SqliteProductRepository,
) -> None:
    """Verifica que el intento rechazado no agrega productos."""
    register_product(products, sku="SKU-1")

    with pytest.raises(DuplicateSkuError):
        register_product(products, sku="SKU-1", name="Otro producto")

    assert len(products.list_all()) == 1


def test_add_product_keeps_the_original_sku_text(
    products: SqliteProductRepository,
) -> None:
    """Verifica que el texto original del SKU no se normaliza."""
    stored = register_product(products, sku="Coca-1.5L", name="Coca Cola")

    assert stored.sku == "Coca-1.5L"
    assert products.get(stored.id) == stored


@pytest.mark.parametrize(
    "duplicated_name", ["Caja x6", "CAJA X6", "caja x6", "CaJa X6"]
)
def test_add_presentation_rejects_a_duplicate_name_in_the_product(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    duplicated_name: str,
) -> None:
    """Verifica la unicidad del nombre de presentación dentro del producto."""
    product = register_product(products)
    register_presentation(presentations, product.id, name="Caja x6")

    with pytest.raises(DuplicatePresentationNameError):
        register_presentation(presentations, product.id, name=duplicated_name)


def test_add_presentation_allows_the_same_name_in_another_product(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que la unicidad del nombre de presentación es por producto."""
    first_product = register_product(products, sku="SKU-1")
    second_product = register_product(products, sku="SKU-2")

    register_presentation(presentations, first_product.id, name="Caja x6")
    register_presentation(presentations, second_product.id, name="Caja x6")

    assert len(presentations.list_by_product(first_product.id)) == 1
    assert len(presentations.list_by_product(second_product.id)) == 1


def test_add_presentation_keeps_the_original_name_text(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que el nombre almacenado conserva su texto original."""
    product = register_product(products)
    stored = register_presentation(
        presentations, product.id, name="Caja X6 Grande"
    )

    assert stored.name == "Caja X6 Grande"


# --- Rechazo de movimientos sin motivo (criterio 2) ---


@pytest.mark.parametrize("reason", ["", "   "])
def test_add_movement_rejects_a_blank_reason(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    reason: str,
) -> None:
    """Verifica que un movimiento sin motivo se rechaza."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    with pytest.raises(sqlite3.IntegrityError):
        register_movement(
            movements, product.id, presentation.id, reason=reason
        )


def test_add_movement_rejects_a_missing_reason(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que la ausencia de motivo se rechaza."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    with pytest.raises(sqlite3.IntegrityError):
        register_movement(
            movements,
            product.id,
            presentation.id,
            reason=None,  # type: ignore[arg-type]
        )


def test_rejected_movement_is_not_persisted(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el intento rechazado no registra el movimiento."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    with pytest.raises(sqlite3.IntegrityError):
        register_movement(
            movements, product.id, presentation.id, reason="   "
        )

    stored = connection.execute(
        "SELECT COUNT(*) AS total FROM movements"
    ).fetchone()

    assert stored["total"] == 0


def test_add_movement_keeps_the_reason(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que un motivo válido se persiste sin alterarse."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    stored = register_movement(
        movements,
        product.id,
        presentation.id,
        reason="Compra de temporada",
    )

    assert stored.reason == "Compra de temporada"


# --- Inmutabilidad de los movimientos confirmados (criterio 3) ---


@pytest.mark.parametrize(
    "repository", [MovementRepository, SqliteMovementRepository]
)
def test_movement_repository_exposes_no_edit_or_delete_operations(
    repository: type,
) -> None:
    """
    Verifica qué escrituras admite el contrato de movimientos.

    Las actualizaciones controladas de los contadores FIFO de una entrada
    cerrada (corrección I2 y T014) son las únicas escrituras admitidas sobre un
    movimiento confirmado: no modifican sus datos históricos ni el contador que
    no se reemplaza.
    """
    operations = public_operations(repository)

    assert operations == {
        "add",
        "get",
        "list_by_product",
        "list_by_presentation",
        "set_consumed_quantity",
        "set_compensated_quantity",
    }
    for operation in operations:
        for forbidden in FORBIDDEN_MOVEMENT_OPERATIONS:
            assert forbidden not in operation


def test_read_operations_do_not_change_confirmed_movements(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que consultar el historial no modifica los movimientos."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    entry = register_movement(
        movements, product.id, presentation.id, quantity=2
    )
    exit_movement = register_movement(
        movements,
        product.id,
        presentation.id,
        kind=MovementKind.CLOSED_EXIT,
        applied_price=Decimal("1500.00"),
        occurred_at=START + timedelta(hours=1),
    )

    def snapshot() -> list[tuple[object, ...]]:
        """Devuelve el contenido actual de la tabla de movimientos."""
        return [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM movements ORDER BY id"
            )
        ]

    before = snapshot()

    movements.get(entry.id)
    movements.list_by_product(product.id)
    movements.list_by_product(product.id, kind=MovementKind.CLOSED_ENTRY)
    movements.list_by_presentation(presentation.id)
    movements.list_by_presentation(
        presentation.id, kind=MovementKind.CLOSED_EXIT
    )

    assert snapshot() == before
    assert movements.get(exit_movement.id) == exit_movement


def test_movement_records_are_immutable(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el movimiento recuperado no cambia en memoria."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    stored = register_movement(movements, product.id, presentation.id)

    with pytest.raises(FrozenInstanceError):
        stored.quantity = 99  # type: ignore[misc]


# --- Filtros por producto y tipo (criterio 4) ---


def test_list_by_product_returns_only_that_product_movements(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el filtro por producto excluye los demás productos."""
    first_product = register_product(products, sku="SKU-1")
    second_product = register_product(products, sku="SKU-2")
    first_presentation = register_presentation(presentations, first_product.id)
    second_presentation = register_presentation(
        presentations, second_product.id
    )
    first_movement = register_movement(
        movements, first_product.id, first_presentation.id
    )
    register_movement(movements, second_product.id, second_presentation.id)
    register_movement(movements, second_product.id, second_presentation.id)

    result = movements.list_by_product(first_product.id)

    assert [movement.id for movement in result] == [first_movement.id]


def test_list_by_product_filters_by_kind(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el filtro por tipo devuelve solo ese tipo de movimiento."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    first_entry = register_movement(
        movements, product.id, presentation.id, quantity=2
    )
    second_entry = register_movement(
        movements,
        product.id,
        presentation.id,
        quantity=3,
        occurred_at=START + timedelta(hours=1),
    )
    register_movement(
        movements,
        product.id,
        presentation.id,
        kind=MovementKind.APERTURE,
        occurred_at=START + timedelta(hours=2),
    )

    result = movements.list_by_product(
        product.id, kind=MovementKind.CLOSED_ENTRY
    )

    assert [movement.id for movement in result] == [
        first_entry.id,
        second_entry.id,
    ]
    assert all(
        movement.kind is MovementKind.CLOSED_ENTRY for movement in result
    )


def test_combined_product_and_kind_filters_cannot_return_other_records(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que los filtros combinados de producto y tipo se aplican."""
    first_product = register_product(products, sku="SKU-1")
    second_product = register_product(products, sku="SKU-2")
    first_presentation = register_presentation(presentations, first_product.id)
    second_presentation = register_presentation(
        presentations, second_product.id
    )
    register_movement(movements, first_product.id, first_presentation.id)
    register_movement(
        movements,
        first_product.id,
        first_presentation.id,
        kind=MovementKind.LOOSE_ENTRY,
        occurred_at=START + timedelta(hours=1),
    )
    register_movement(movements, second_product.id, second_presentation.id)

    closed_entries = movements.list_by_product(
        first_product.id, kind=MovementKind.CLOSED_ENTRY
    )

    assert [movement.kind for movement in closed_entries] == [
        MovementKind.CLOSED_ENTRY
    ]
    assert all(
        movement.product_id == first_product.id for movement in closed_entries
    )
    assert movements.list_by_product(
        first_product.id, kind=MovementKind.COMPENSATION
    ) == []


def test_list_by_presentation_filters_by_presentation_and_kind(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica el filtro por presentación y por tipo."""
    product = register_product(products)
    base = register_presentation(
        presentations, product.id, name="Unidad", factor=1, sale_price="30.00"
    )
    box = register_presentation(presentations, product.id, name="Caja x6")
    base_movement = register_movement(
        movements, product.id, base.id, kind=MovementKind.LOOSE_ENTRY
    )
    register_movement(
        movements,
        product.id,
        box.id,
        kind=MovementKind.CLOSED_ENTRY,
        occurred_at=START + timedelta(hours=1),
    )

    assert [
        movement.id for movement in movements.list_by_presentation(base.id)
    ] == [base_movement.id]
    assert (
        movements.list_by_presentation(base.id, kind=MovementKind.LOOSE_ENTRY)
        == [base_movement]
    )
    assert (
        movements.list_by_presentation(base.id, kind=MovementKind.APERTURE)
        == []
    )


def test_list_by_product_returns_the_history_in_chronological_order(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el historial se devuelve en orden cronológico."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    third = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=START + timedelta(hours=2),
    )
    first = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=START,
    )
    second = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=START + timedelta(hours=1),
    )

    result = movements.list_by_product(product.id)

    assert [movement.id for movement in result] == [
        first.id,
        second.id,
        third.id,
    ]


# --- Búsquedas por SKU y por nombre (criterio 5) ---


@pytest.mark.parametrize(
    "searched_sku", ["coca-1.5l", "COCA-1.5L", "CoCa-1.5L"]
)
def test_find_by_sku_ignores_case_and_returns_the_stored_text(
    products: SqliteProductRepository, searched_sku: str
) -> None:
    """Verifica la búsqueda por SKU sin distinguir mayúsculas."""
    stored = register_product(products, sku="Coca-1.5L", name="Coca Cola")

    found = products.find_by_sku(searched_sku)

    assert found is not None
    assert found.id == stored.id
    assert found.sku == "Coca-1.5L"


def test_find_by_sku_returns_none_when_there_is_no_match(
    products: SqliteProductRepository,
) -> None:
    """Verifica que una búsqueda sin coincidencias devuelve `None`."""
    register_product(products, sku="SKU-1")

    assert products.find_by_sku("SKU-2") is None


@pytest.mark.parametrize(
    "searched_name", ["coca cola 1,5 l", "COCA COLA 1,5 L"]
)
def test_find_by_name_ignores_case_and_returns_the_stored_text(
    products: SqliteProductRepository, searched_name: str
) -> None:
    """Verifica la búsqueda por nombre sin distinguir mayúsculas."""
    stored = register_product(
        products, sku="Coca-1.5L", name="Coca Cola 1,5 L"
    )

    found = products.find_by_name(searched_name)

    assert [product.id for product in found] == [stored.id]
    assert found[0].name == "Coca Cola 1,5 L"


def test_find_by_name_returns_every_match(
    products: SqliteProductRepository,
) -> None:
    """Verifica que las coincidencias múltiples se devuelven todas."""
    first = register_product(products, sku="SKU-1", name="Coca Cola")
    second = register_product(products, sku="SKU-2", name="coca cola")

    found = products.find_by_name("COCA COLA")

    assert [product.id for product in found] == [first.id, second.id]


def test_find_by_name_returns_an_empty_list_without_matches(
    products: SqliteProductRepository,
) -> None:
    """Verifica que una búsqueda sin coincidencias devuelve una lista vacía."""
    register_product(products, sku="SKU-1", name="Coca Cola")

    assert products.find_by_name("Pepsi") == []


def test_presentation_find_by_name_ignores_case_within_the_product(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica la búsqueda de presentación sin distinguir mayúsculas."""
    product = register_product(products)
    other_product = register_product(products, sku="SKU-2")
    stored = register_presentation(presentations, product.id, name="Caja x6")

    found = presentations.find_by_name(product.id, "CAJA X6")

    assert found is not None
    assert found.id == stored.id
    assert found.name == "Caja x6"
    assert presentations.find_by_name(other_product.id, "Caja x6") is None


# --- Recuperación de datos para saldos e historial (criterio 6) ---


def test_product_and_presentations_allow_rebuilding_the_balances(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que los datos recuperados reconstruyen el total equivalente."""
    product = register_product(products, minimum_stock=10, loose_units=3)
    base = register_presentation(
        presentations, product.id, name="Unidad", factor=1, sale_price="30.00"
    )
    box = register_presentation(
        presentations, product.id, name="Caja x6", factor=6, closed_stock=2
    )

    stored_product = products.get(product.id)
    stored_presentations = presentations.list_by_product(product.id)

    assert stored_product is not None
    assert [presentation.id for presentation in stored_presentations] == [
        base.id,
        box.id,
    ]
    closed_stocks = [
        ClosedStock(
            factor=presentation.factor,
            quantity=presentation.closed_stock,
        )
        for presentation in stored_presentations
    ]
    assert (
        total_equivalent(stored_product.loose_units, closed_stocks)
        == 3 + 2 * 6
    )


def test_closed_entries_allow_rebuilding_the_fifo_balances(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el historial permite reconstruir las entradas FIFO."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    first_entry = register_movement(
        movements, product.id, presentation.id, quantity=2
    )
    second_entry = register_movement(
        movements,
        product.id,
        presentation.id,
        quantity=3,
        occurred_at=START + timedelta(hours=1),
    )
    register_movement(
        movements,
        product.id,
        presentation.id,
        kind=MovementKind.APERTURE,
        occurred_at=START + timedelta(hours=2),
    )

    closed_entries = [
        ClosedEntry(
            entry_id=movement.id,
            presentation_id=movement.presentation_id,
            quantity=movement.quantity,
            consumed_quantity=movement.consumed_quantity,
            compensated_quantity=movement.compensated_quantity,
        )
        for movement in movements.list_by_product(
            product.id, kind=MovementKind.CLOSED_ENTRY
        )
    ]

    assert [entry.entry_id for entry in closed_entries] == [
        first_entry.id,
        second_entry.id,
    ]
    assert [entry.compensable_quantity for entry in closed_entries] == [2, 3]


def test_movement_keeps_the_optional_cost_and_applied_price(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica el costo opcional y el precio aplicado como Decimal exacto."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    entry = register_movement(
        movements,
        product.id,
        presentation.id,
        quantity=2,
        cost=Decimal("0.30"),
    )
    exit_movement = register_movement(
        movements,
        product.id,
        presentation.id,
        kind=MovementKind.LOOSE_EXIT,
        applied_price=Decimal("12345.6789"),
        occurred_at=START + timedelta(hours=1),
    )

    stored_entry = movements.get(entry.id)
    stored_exit = movements.get(exit_movement.id)

    assert stored_entry is not None
    assert stored_exit is not None
    assert stored_entry.cost == Decimal("0.30")
    assert str(stored_entry.cost) == "0.3"
    assert stored_entry.applied_price is None
    assert stored_exit.applied_price == Decimal("12345.6789")
    assert stored_exit.cost is None


def test_compensation_records_are_linked_to_the_original_movement(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el historial conserva el vínculo de la compensación."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    original = register_movement(
        movements, product.id, presentation.id, quantity=2
    )
    compensation = register_movement(
        movements,
        product.id,
        presentation.id,
        kind=MovementKind.COMPENSATION,
        occurred_at=START + timedelta(hours=1),
        original_movement_id=original.id,
    )

    stored = movements.get(compensation.id)

    assert stored is not None
    assert stored.kind is MovementKind.COMPENSATION
    assert stored.original_movement_id == original.id
    assert movements.get(original.id) == original


def test_movement_instants_are_stored_in_utc(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el instante se almacena en UTC con su zona horaria."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    local_instant = datetime(
        2026, 3, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3))
    )

    stored = register_movement(
        movements, product.id, presentation.id, occurred_at=local_instant
    )
    stored_text = connection.execute(
        "SELECT occurred_at FROM movements"
    ).fetchone()["occurred_at"]

    assert stored.occurred_at == local_instant.astimezone(timezone.utc)
    assert stored_text.endswith("+00:00")


def test_movement_instant_without_timezone_is_rejected(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que un instante sin zona horaria se rechaza."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    with pytest.raises(ValueError):
        register_movement(
            movements,
            product.id,
            presentation.id,
            occurred_at=datetime(2026, 3, 1, 9, 0),
        )


def test_get_returns_none_for_unknown_identifiers(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que la consulta de un registro inexistente devuelve `None`."""
    assert products.get(999) is None
    assert presentations.get(999) is None
    assert movements.get(999) is None


def test_product_updates_are_persisted(
    products: SqliteProductRepository,
) -> None:
    """Verifica la actualización de los datos vigentes del producto."""
    product = register_product(products, minimum_stock=2)

    renamed = products.set_name(product.id, "Nombre nuevo")
    with_stock = products.set_minimum_stock(product.id, 15)
    with_loose_units = products.set_loose_units(product.id, 7)
    deactivated = products.set_active(product.id, False)

    assert renamed.name == "Nombre nuevo"
    assert with_stock.minimum_stock == 15
    assert with_loose_units.loose_units == 7
    assert deactivated.active is False
    assert products.get(product.id) == deactivated
    assert deactivated.sku == product.sku


@pytest.mark.parametrize(
    "operation",
    ["set_name", "set_minimum_stock", "set_loose_units", "set_active"],
)
def test_product_updates_require_an_existing_product(
    products: SqliteProductRepository, operation: str
) -> None:
    """Verifica que actualizar un producto inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        getattr(products, operation)(999, 1)


@pytest.mark.parametrize(
    "operation",
    ["set_sale_price", "set_closed_stock", "set_factor"],
)
def test_presentation_updates_require_an_existing_presentation(
    presentations: SqlitePresentationRepository, operation: str
) -> None:
    """Verifica que actualizar una presentación inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        getattr(presentations, operation)(999, 1)


def test_presentation_updates_are_persisted(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica la actualización de los datos vigentes de la presentación."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    with_price = presentations.set_sale_price(
        presentation.id, Decimal("1500.50")
    )
    with_stock = presentations.set_closed_stock(presentation.id, 4)
    with_factor = presentations.set_factor(presentation.id, 12)

    assert with_price.sale_price == Decimal("1500.50")
    assert with_stock.closed_stock == 4
    assert with_factor.factor == 12
    assert presentations.get(presentation.id) == with_factor


def test_product_repository_exposes_no_sku_update_operation() -> None:
    """Verifica que la persistencia no permite modificar el SKU (RF-01)."""
    operations = public_operations(SqliteProductRepository)
    sku_operations = {
        operation for operation in operations if "sku" in operation
    }

    assert sku_operations == {"find_by_sku"}


# --- Actualización controlada del contador de compensaciones (T014) ---


def test_update_compensated_quantity_persists_the_new_value(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica que el contador de compensaciones se actualiza y se persiste.

    Solo cambia `compensated_quantity`: los datos históricos del movimiento
    confirmado se conservan.
    """
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    entry = register_movement(
        movements,
        product.id,
        presentation.id,
        quantity=5,
        reason="Carga inicial",
        cost=Decimal("0.30"),
    )
    before_row = stored_row(connection, entry.id)

    updated = movements.set_compensated_quantity(entry.id, 2)

    assert stored_row(connection, entry.id) == {
        **before_row,
        "compensated_quantity": 2,
    }
    assert updated.compensated_quantity == 2
    assert movements.get(entry.id) == updated
    assert updated.kind is MovementKind.CLOSED_ENTRY
    assert updated.quantity == 5
    assert updated.reason == "Carga inicial"
    assert updated.cost == Decimal("0.30")
    assert updated.consumed_quantity == 0
    assert updated.is_initial_stock is False
    assert updated.original_movement_id is None


def test_update_compensated_quantity_requires_an_existing_movement(
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que compensar un movimiento inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        movements.set_compensated_quantity(999, 1)


@pytest.mark.parametrize(
    "kind",
    [
        MovementKind.CLOSED_EXIT,
        MovementKind.LOOSE_ENTRY,
        MovementKind.LOOSE_EXIT,
        MovementKind.APERTURE,
    ],
)
def test_update_compensated_quantity_requires_a_closed_entry(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    kind: MovementKind,
) -> None:
    """Verifica que solo las entradas cerradas tienen cantidad compensable."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    movement = register_movement(
        movements, product.id, presentation.id, kind=kind, quantity=2
    )
    before_row = stored_row(connection, movement.id)

    with pytest.raises(NotAClosedEntryError):
        movements.set_compensated_quantity(movement.id, 1)

    assert stored_row(connection, movement.id) == before_row


def test_update_compensated_quantity_rejects_a_quantity_that_exceeds_the_entry(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica el límite `consumida + compensada <= cantidad` de la entrada.

    El rechazo no altera el registro: la cantidad consumida y la compensada
    quedan como estaban.
    """
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    entry = register_movement(
        movements, product.id, presentation.id, quantity=3
    )
    consumed = movements.set_consumed_quantity(entry.id, 2)
    before_row = stored_row(connection, entry.id)

    with pytest.raises(ValueError):
        movements.set_compensated_quantity(entry.id, 2)

    assert stored_row(connection, entry.id) == before_row
    assert movements.get(entry.id) == consumed
    assert consumed.consumed_quantity == 2
    assert consumed.compensated_quantity == 0

    allowed = movements.set_compensated_quantity(entry.id, 1)

    assert stored_row(connection, entry.id) == {
        **before_row,
        "compensated_quantity": 1,
    }
    assert allowed.consumed_quantity == 2
    assert allowed.compensated_quantity == 1


# --- Historial de precios: contrato de solo anexado (T030, RF-08) ---


@pytest.mark.parametrize(
    "repository", [PriceHistoryRepository, SqlitePriceHistoryRepository]
)
def test_price_history_repository_exposes_only_the_append_only_contract(
    repository: type,
) -> None:
    """Verifica que el historial solo se anexa y se consulta (RF-08)."""
    operations = public_operations(repository)

    assert operations == {
        "add",
        "list_by_presentation",
        "list_by_product",
    }
    for operation in operations:
        for forbidden in FORBIDDEN_PRICE_CHANGE_OPERATIONS:
            assert forbidden not in operation


def test_the_repositories_module_never_mutates_a_price_change() -> None:
    """
    Verifica el contrato de solo anexado también en el código (T036, C13).

    Cierra la garantía en el nivel del módulo, además del contrato público que
    verifican las operaciones expuestas: ningún texto del código que menciona
    `price_changes` contiene `UPDATE`, `DELETE` ni `DROP`, y el único escrito
    es el `INSERT` del alta. No se prueba un `UPDATE` o un `DELETE` real porque
    el esquema no los impide con disparadores: la garantía es la ausencia de la
    operación en la persistencia, no un rechazo en tiempo de ejecución.
    """
    operations = public_operations(PriceHistoryRepository)
    sqlite_operations = public_operations(SqlitePriceHistoryRepository)
    fragments = sql_fragments_mentioning(
        getsource(repositories_module), "price_changes"
    )

    assert operations == sqlite_operations == {
        "add",
        "list_by_presentation",
        "list_by_product",
    }
    assert fragments
    assert any(
        "INSERT INTO price_changes" in fragment for fragment in fragments
    )
    for fragment in fragments:
        normalized = fragment.upper()
        assert "UPDATE" not in normalized, fragment
        assert "DELETE" not in normalized, fragment
        assert "DROP" not in normalized, fragment


def test_add_price_change_persists_the_canonical_decimal_text(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica la serialización decimal exacta, sin `float` (RF-08)."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    exact_price = Decimal("123456789012345678.12345")

    stored = register_price_change(
        price_history,
        product.id,
        presentation.id,
        previous_price=Decimal("10.50"),
        new_price=exact_price,
    )
    row = stored_price_change(connection, stored.id)

    assert stored.previous_price == Decimal("10.5")
    assert stored.new_price == exact_price
    assert row["previous_price"] == "10.5"
    assert row["new_price"] == "123456789012345678.12345"
    assert price_history.list_by_presentation(presentation.id) == [stored]


def test_price_change_instants_are_stored_in_utc(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que el instante se almacena y se lee en UTC (RF-08)."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    local_instant = START.astimezone(timezone(timedelta(hours=-3)))

    stored = register_price_change(
        price_history,
        product.id,
        presentation.id,
        occurred_at=local_instant,
    )

    assert stored.occurred_at == START
    assert stored.occurred_at.utcoffset() == timedelta(0)
    assert stored_price_change(connection, stored.id)["occurred_at"] == (
        "2026-03-01T12:00:00.000000+00:00"
    )


def test_price_change_instant_without_timezone_is_rejected(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica el rechazo de un instante sin zona horaria (RF-08)."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    with pytest.raises(ValueError):
        register_price_change(
            price_history,
            product.id,
            presentation.id,
            occurred_at=datetime(2026, 3, 1, 12, 0),
        )

    assert price_history.list_by_presentation(presentation.id) == []


def test_add_price_change_uses_the_constant_event_type(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que el tipo de evento lo fija el repositorio (RF-08)."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    stored = register_price_change(
        price_history, product.id, presentation.id
    )
    row = stored_price_change(connection, stored.id)

    assert stored.event_type == PRICE_CHANGE_EVENT_TYPE
    assert row["event_type"] == PRICE_CHANGE_EVENT_TYPE


def test_add_price_change_persists_the_readable_event_type(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica el valor persistido del evento, no solo la constante."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    stored = register_price_change(
        price_history, product.id, presentation.id
    )

    assert stored_price_change(connection, stored.id)["event_type"] == (
        "cambio_precio"
    )


def test_add_price_change_requires_existing_references(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica las claves foráneas del historial (RF-08)."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    with pytest.raises(sqlite3.IntegrityError):
        register_price_change(
            price_history, product.id + 1, presentation.id
        )

    with pytest.raises(sqlite3.IntegrityError):
        register_price_change(
            price_history, product.id, presentation.id + 1
        )

    assert price_history.list_by_product(product.id) == []
    assert price_history.list_by_presentation(presentation.id) == []


@pytest.mark.parametrize(
    "price", [Decimal("0"), Decimal("-1.5"), Decimal("NaN")]
)
def test_add_price_change_rejects_an_invalid_price(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
    price: Decimal,
) -> None:
    """Verifica que el esquema rechaza un precio no positivo o malformado."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    with pytest.raises(sqlite3.IntegrityError):
        register_price_change(
            price_history,
            product.id,
            presentation.id,
            previous_price=price,
        )

    assert price_history.list_by_presentation(presentation.id) == []


def test_list_by_presentation_returns_its_history_in_order(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica el orden cronológico y el desempate por `id` (RF-08)."""
    product = register_product(products)
    first = register_presentation(presentations, product.id)
    second = register_presentation(
        presentations, product.id, name="Caja x12", factor=12
    )
    earliest = register_price_change(
        price_history, product.id, first.id, occurred_at=START
    )
    tie_first = register_price_change(
        price_history, product.id, first.id, occurred_at=LATER
    )
    tie_second = register_price_change(
        price_history,
        product.id,
        first.id,
        occurred_at=LATER,
        new_price="15",
    )
    register_price_change(
        price_history, product.id, second.id, occurred_at=EARLIER
    )

    history = price_history.list_by_presentation(first.id)

    assert history == [earliest, tie_first, tie_second]


def test_list_by_product_returns_the_history_of_its_presentations(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica la consulta por producto, en orden cronológico (RF-08)."""
    product = register_product(products)
    first = register_presentation(presentations, product.id)
    second = register_presentation(
        presentations, product.id, name="Caja x12", factor=12
    )
    other_product = register_product(products, sku="SKU-2")
    other_presentation = register_presentation(
        presentations, other_product.id
    )
    latest = register_price_change(
        price_history, product.id, second.id, occurred_at=LATER
    )
    earliest = register_price_change(
        price_history, product.id, first.id, occurred_at=EARLIER
    )
    register_price_change(
        price_history, other_product.id, other_presentation.id
    )

    history = price_history.list_by_product(product.id)

    assert history == [earliest, latest]
    assert all(
        record.product_id == product.id for record in history
    )


def test_price_change_lists_are_empty_without_records(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que leer un historial vacío no es un error (RF-08)."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)

    assert price_history.list_by_presentation(presentation.id) == []
    assert price_history.list_by_product(product.id) == []
    assert price_history.list_by_presentation(999) == []
    assert price_history.list_by_product(999) == []


def test_read_operations_do_not_change_the_price_history(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que consultar el historial no modifica sus registros."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    stored = register_price_change(
        price_history, product.id, presentation.id
    )

    price_history.list_by_presentation(presentation.id)
    price_history.list_by_product(product.id)

    assert stored_price_change(connection, stored.id) == {
        "id": stored.id,
        "product_id": product.id,
        "presentation_id": presentation.id,
        "previous_price": "10.5",
        "new_price": "12",
        "occurred_at": "2026-03-01T12:00:00.000000+00:00",
        "event_type": PRICE_CHANGE_EVENT_TYPE,
    }


def test_price_change_records_are_immutable(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    price_history: SqlitePriceHistoryRepository,
) -> None:
    """Verifica que un cambio de precio persistido no se modifica (RF-08)."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    stored = register_price_change(
        price_history, product.id, presentation.id
    )

    with pytest.raises(FrozenInstanceError):
        stored.new_price = Decimal("99")  # type: ignore[misc]
