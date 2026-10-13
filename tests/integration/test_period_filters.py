"""Pruebas de integración de los filtros temporales por período (T010).

Cubren los criterios de aceptación de T010 sobre RF-03: los instantes se
almacenan en UTC con una representación ISO 8601 uniforme y comparable, los
períodos se interpretan en la hora local de Argentina
(`America/Argentina/Buenos_Aires`) y se convierten a UTC antes de consultar,
la semántica del período es `[inicio, fin)` (inicio inclusivo y final
exclusivo), los filtros de producto, tipo y período se combinan mediante AND,
las consultas sin período mantienen su comportamiento y el contrato de los
límites inválidos, ambiguos o inexistentes queda explicitado.

Las pruebas de ambigüedad e inexistencia usan cambios de hora históricos de
Argentina (2007-12-30 y 2008-03-15), registrados de forma estable en la base de
datos de zonas horarias `tzdata`.
"""

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.domain.movements import MovementKind
from app.infrastructure.sqlite.connection import IN_MEMORY_DATABASE, connect
from app.infrastructure.sqlite.instants import (
    InvalidPeriodError,
    from_utc_text,
    resolve_period,
)
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
)

LOCAL_TIMEZONE: ZoneInfo = ZoneInfo("America/Argentina/Buenos_Aires")

# Período de un día completo en hora local argentina y su equivalente en UTC.
LOCAL_DAY_START: datetime = datetime(2026, 3, 1, 0, 0)
LOCAL_DAY_END: datetime = datetime(2026, 3, 2, 0, 0)
DAY_START_UTC: datetime = datetime(2026, 3, 1, 3, 0, tzinfo=timezone.utc)
DAY_END_UTC: datetime = datetime(2026, 3, 2, 3, 0, tzinfo=timezone.utc)

# Longitud de `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`, la forma canónica almacenada.
CANONICAL_INSTANT_LENGTH: int = 32


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


def register_product(
    products: SqliteProductRepository,
    *,
    sku: str = "SKU-1",
) -> ProductRecord:
    """Registra un producto válido y devuelve su versión persistida."""
    return products.add(
        sku=sku,
        name=f"Producto {sku}",
        base_unit="unidad",
    )


def register_presentation(
    presentations: SqlitePresentationRepository,
    product_id: int,
    *,
    name: str = "Unidad",
    factor: int = 1,
    sale_price: str = "30.00",
) -> PresentationRecord:
    """Registra una presentación válida y devuelve su versión persistida."""
    return presentations.add(
        product_id=product_id,
        name=name,
        factor=factor,
        sale_price=Decimal(sale_price),
    )


def register_movement(
    movements: SqliteMovementRepository,
    product_id: int,
    presentation_id: int,
    *,
    kind: MovementKind = MovementKind.CLOSED_ENTRY,
    occurred_at: datetime,
    **extra: Any,
) -> MovementRecord:
    """Registra un movimiento válido y devuelve su versión persistida."""
    return movements.add(
        product_id=product_id,
        presentation_id=presentation_id,
        kind=kind,
        quantity=1,
        reason="Compra",
        occurred_at=occurred_at,
        **extra,
    )


def movement_ids(records: list[MovementRecord]) -> list[int]:
    """Devuelve los identificadores de los movimientos indicados."""
    return [record.id for record in records]


# --- Conversión de los límites locales a UTC (criterio 1) ---


def test_period_bounds_use_local_argentina_time_converted_to_utc() -> None:
    """Verifica la conversión de los límites locales de Argentina a UTC."""
    local_bound = LOCAL_DAY_START.replace(tzinfo=LOCAL_TIMEZONE)

    start_text, end_text = resolve_period(LOCAL_DAY_START, LOCAL_DAY_END)

    assert local_bound.utcoffset() == timedelta(hours=-3)
    assert start_text == "2026-03-01T03:00:00.000000+00:00"
    assert end_text == "2026-03-02T03:00:00.000000+00:00"
    assert from_utc_text(str(start_text)) == DAY_START_UTC
    assert from_utc_text(str(end_text)) == DAY_END_UTC


def test_bounds_with_timezone_are_converted_without_reinterpreting() -> None:
    """Verifica que un límite con zona horaria no se reinterpreta."""
    utc_bound, _ = resolve_period(DAY_START_UTC, None)
    local_bound, _ = resolve_period(
        datetime(2026, 3, 1, 0, 0, tzinfo=LOCAL_TIMEZONE), None
    )

    assert utc_bound == "2026-03-01T03:00:00.000000+00:00"
    assert local_bound == utc_bound


# --- Inicio inclusivo y final exclusivo (criterios 2 y 3) ---


def test_a_movement_exactly_at_the_period_start_is_included(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el movimiento del inicio del período se incluye."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_START_UTC - timedelta(microseconds=1),
    )
    start = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_START_UTC,
    )

    result = movements.list_by_product(
        product.id,
        period_start=LOCAL_DAY_START,
        period_end=LOCAL_DAY_END,
    )

    assert movement_ids(result) == [start.id]


def test_a_movement_exactly_at_the_period_end_is_excluded(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el movimiento del final del período se excluye."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    inside = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_END_UTC - timedelta(microseconds=1),
    )
    register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_END_UTC,
    )

    result = movements.list_by_product(
        product.id,
        period_start=LOCAL_DAY_START,
        period_end=LOCAL_DAY_END,
    )

    assert movement_ids(result) == [inside.id]


def test_local_period_boundaries_are_not_interpreted_as_utc(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el período local no se aplica como si fuera UTC."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_START_UTC - timedelta(hours=1),
    )
    inside = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_START_UTC + timedelta(hours=1),
    )
    register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_END_UTC + timedelta(hours=1),
    )

    result = movements.list_by_product(
        product.id,
        period_start=LOCAL_DAY_START,
        period_end=LOCAL_DAY_END,
    )

    assert movement_ids(result) == [inside.id]


def test_an_empty_period_returns_no_movements(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que un período vacío no coincide con ningún movimiento."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_START_UTC,
    )

    result = movements.list_by_product(
        product.id,
        period_start=LOCAL_DAY_START,
        period_end=LOCAL_DAY_START,
    )

    assert result == []


def test_a_period_with_a_single_bound_filters_only_that_side(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que un período con un solo límite acota un único extremo."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    before = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_START_UTC - timedelta(hours=1),
    )
    first_inside = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_START_UTC,
    )
    second_inside = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_END_UTC + timedelta(hours=1),
    )

    from_start = movements.list_by_product(
        product.id, period_start=LOCAL_DAY_START
    )
    until_end = movements.list_by_product(
        product.id, period_end=LOCAL_DAY_END
    )

    assert movement_ids(from_start) == [first_inside.id, second_inside.id]
    assert movement_ids(until_end) == [before.id, first_inside.id]


# --- Combinación mediante AND de los filtros (criterio 4) ---


def test_product_kind_and_period_filters_are_combined_with_and(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que los tres filtros del historial se combinan con AND."""
    first_product = register_product(products, sku="SKU-1")
    second_product = register_product(products, sku="SKU-2")
    first_presentation = register_presentation(presentations, first_product.id)
    second_presentation = register_presentation(
        presentations, second_product.id
    )
    at_start = register_movement(
        movements,
        first_product.id,
        first_presentation.id,
        occurred_at=DAY_START_UTC,
    )
    register_movement(
        movements,
        first_product.id,
        first_presentation.id,
        kind=MovementKind.LOOSE_ENTRY,
        occurred_at=DAY_START_UTC + timedelta(hours=1),
    )
    register_movement(
        movements,
        first_product.id,
        first_presentation.id,
        occurred_at=DAY_START_UTC + timedelta(hours=2),
    )
    register_movement(
        movements,
        first_product.id,
        first_presentation.id,
        occurred_at=DAY_START_UTC + timedelta(hours=3),
    )
    register_movement(
        movements,
        second_product.id,
        second_presentation.id,
        occurred_at=DAY_START_UTC + timedelta(hours=1),
    )
    period_start = datetime(2026, 3, 1, 0, 0)
    period_end = datetime(2026, 3, 1, 2, 0)

    combined = movements.list_by_product(
        first_product.id,
        MovementKind.CLOSED_ENTRY,
        period_start=period_start,
        period_end=period_end,
    )

    assert movement_ids(combined) == [at_start.id]
    assert all(
        movement.product_id == first_product.id
        and movement.kind is MovementKind.CLOSED_ENTRY
        for movement in combined
    )
    assert (
        len(
            movements.list_by_product(
                first_product.id,
                period_start=period_start,
                period_end=period_end,
            )
        )
        == 2
    )
    assert (
        len(
            movements.list_by_product(
                first_product.id, MovementKind.CLOSED_ENTRY
            )
        )
        == 3
    )
    assert (
        len(
            movements.list_by_product(
                second_product.id,
                MovementKind.CLOSED_ENTRY,
                period_start=period_start,
                period_end=period_end,
            )
        )
        == 1
    )


def test_presentation_kind_and_period_filters_are_combined_with_and(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica el AND de los filtros del historial por presentación."""
    product = register_product(products)
    base = register_presentation(presentations, product.id, name="Unidad")
    box = register_presentation(
        presentations, product.id, name="Caja x6", factor=6
    )
    base_entry = register_movement(
        movements,
        product.id,
        base.id,
        kind=MovementKind.LOOSE_ENTRY,
        occurred_at=DAY_START_UTC,
    )
    register_movement(
        movements,
        product.id,
        box.id,
        occurred_at=DAY_START_UTC + timedelta(hours=1),
    )

    result = movements.list_by_presentation(
        base.id,
        MovementKind.LOOSE_ENTRY,
        period_start=LOCAL_DAY_START,
        period_end=LOCAL_DAY_END,
    )

    assert movement_ids(result) == [base_entry.id]


# --- Compatibilidad con las consultas sin período (criterio 5) ---


def test_queries_without_period_return_the_full_history(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que las consultas sin período conservan su resultado."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    first = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=datetime(2020, 1, 1, 12, 0, tzinfo=timezone.utc),
    )
    second = register_movement(
        movements,
        product.id,
        presentation.id,
        kind=MovementKind.LOOSE_ENTRY,
        occurred_at=DAY_START_UTC,
    )
    third = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc),
    )

    history = movements.list_by_product(product.id)
    explicit_none = movements.list_by_product(
        product.id, period_start=None, period_end=None
    )
    by_kind = movements.list_by_presentation(
        presentation.id, MovementKind.CLOSED_ENTRY
    )
    by_kind_with_none = movements.list_by_presentation(
        presentation.id,
        MovementKind.CLOSED_ENTRY,
        period_start=None,
        period_end=None,
    )

    assert movement_ids(history) == [first.id, second.id, third.id]
    assert explicit_none == history
    assert movement_ids(by_kind) == [first.id, third.id]
    assert by_kind_with_none == by_kind


# --- Límites inválidos, ambiguos o inexistentes (criterio 6) ---


def test_a_reversed_period_is_rejected(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que un período con el final anterior al inicio se rechaza."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_START_UTC,
    )
    register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=DAY_END_UTC,
    )

    with pytest.raises(InvalidPeriodError):
        movements.list_by_product(
            product.id,
            period_start=LOCAL_DAY_END,
            period_end=LOCAL_DAY_START,
        )

    assert len(movements.list_by_product(product.id)) == 2


def test_a_nonexistent_local_bound_is_rejected() -> None:
    """Verifica que una hora local inexistente se rechaza de forma explícita.

    El 2007-12-30 la hora local de Argentina pasó de las 00:00 a la 01:00 por
    un cambio de hora, por lo que las 00:30 nunca existieron.
    """
    nonexistent = datetime(2007, 12, 30, 0, 30)

    with pytest.raises(InvalidPeriodError):
        resolve_period(nonexistent, None)

    with pytest.raises(InvalidPeriodError):
        resolve_period(None, nonexistent)


def test_an_ambiguous_local_bound_uses_the_first_occurrence() -> None:
    """Verifica el contrato de las horas locales ambiguas.

    El 2008-03-15 las 23:30 ocurrieron dos veces en Argentina; sin indicación
    explícita se usa la primera ocurrencia (`fold = 0`).
    """
    ambiguous = datetime(2008, 3, 15, 23, 30)

    first_occurrence, _ = resolve_period(ambiguous, None)
    second_occurrence, _ = resolve_period(
        ambiguous.replace(fold=1), None
    )

    assert first_occurrence == "2008-03-16T01:30:00.000000+00:00"
    assert second_occurrence == "2008-03-16T02:30:00.000000+00:00"
    assert str(first_occurrence) < str(second_occurrence)


@pytest.mark.parametrize("bound", ["2026-03-01", 1])
def test_a_bound_that_is_not_a_datetime_is_rejected(bound: object) -> None:
    """Verifica que un límite que no es `datetime` se rechaza."""
    with pytest.raises(TypeError):
        resolve_period(bound, None)  # type: ignore[arg-type]


# --- Representación uniforme y comparable de los instantes en UTC ---


def test_stored_instants_have_a_uniform_comparable_utc_representation(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica la representación ISO 8601 uniforme y su orden textual."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    third = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=datetime(2026, 3, 1, 13, 0, tzinfo=timezone.utc),
    )
    first = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=datetime(
            2026, 3, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3))
        ),
    )
    second = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=datetime(
            2026, 3, 1, 12, 0, 0, 500000, tzinfo=timezone.utc
        ),
    )

    stored_texts = [
        str(row["occurred_at"])
        for row in connection.execute(
            "SELECT occurred_at FROM movements ORDER BY id"
        )
    ]

    assert stored_texts == [
        "2026-03-01T13:00:00.000000+00:00",
        "2026-03-01T12:00:00.000000+00:00",
        "2026-03-01T12:00:00.500000+00:00",
    ]
    assert all(
        len(text) == CANONICAL_INSTANT_LENGTH and text.endswith("+00:00")
        for text in stored_texts
    )
    assert sorted(stored_texts) == [
        "2026-03-01T12:00:00.000000+00:00",
        "2026-03-01T12:00:00.500000+00:00",
        "2026-03-01T13:00:00.000000+00:00",
    ]
    assert movement_ids(movements.list_by_product(product.id)) == [
        first.id,
        second.id,
        third.id,
    ]


def test_the_range_filter_compares_instants_with_microsecond_precision(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el rango distingue instantes por microsegundos."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    earlier = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=datetime(
            2026, 3, 1, 12, 0, 0, 0, tzinfo=timezone.utc
        ),
    )
    later = register_movement(
        movements,
        product.id,
        presentation.id,
        occurred_at=datetime(
            2026, 3, 1, 12, 0, 0, 1, tzinfo=timezone.utc
        ),
    )
    boundary = datetime(2026, 3, 1, 12, 0, 0, 1, tzinfo=timezone.utc)

    from_boundary = movements.list_by_product(
        product.id, period_start=boundary
    )
    until_boundary = movements.list_by_product(
        product.id, period_end=boundary
    )

    assert movement_ids(from_boundary) == [later.id]
    assert movement_ids(until_boundary) == [earlier.id]
