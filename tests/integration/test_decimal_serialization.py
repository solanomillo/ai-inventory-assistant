"""Pruebas de la representación canónica de `Decimal` (hallazgo I3).

Verifican el contrato de `_decimal_to_text`: notación fija sin notación
científica, sin ceros finales, sin signo en el cero y con el valor exacto del
`Decimal`. Además comprueban, mediante un viaje de ida y vuelta por SQLite, que
el texto almacenado es compatible con las restricciones `CHECK` del esquema.

Las pruebas del contrato importan `_decimal_to_text` porque es la unidad bajo
prueba: los valores cero y negativos no son observables desde la API pública,
ya que las restricciones `CHECK` de precios y costos los rechazan.
"""

import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.domain.movements import MovementKind
from app.infrastructure.sqlite.connection import IN_MEMORY_DATABASE, connect
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqliteProductRepository,
    _decimal_to_text,
)
from app.infrastructure.sqlite.schema import create_schema
from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
)

START: datetime = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

# Casos canónicos de valores positivos, que el esquema admite como precio.
CANONICAL_CASES: tuple[tuple[str, str], ...] = (
    ("10.50", "10.5"),
    ("1E+2", "100"),
    ("0.300", "0.3"),
    ("100", "100"),
    ("1200.00", "1200"),
    ("0.5", "0.5"),
    ("0.0001", "0.0001"),
    ("1E-30", "0." + "0" * 29 + "1"),
    ("1E+30", "1" + "0" * 30),
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


def register_product(
    products: SqliteProductRepository,
    *,
    sku: str = "SKU-1",
    name: str = "Producto",
    base_unit: str = "unidad",
) -> ProductRecord:
    """Registra un producto válido y devuelve su versión persistida."""
    return products.add(sku=sku, name=name, base_unit=base_unit)


def register_presentation(
    presentations: SqlitePresentationRepository,
    product_id: int,
    *,
    name: str = "Caja x6",
    factor: int = 6,
    sale_price: str = "1200.00",
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


def stored_text(
    connection: sqlite3.Connection,
    column: str,
    table: str,
    record_id: int,
) -> str:
    """Devuelve el texto almacenado de una columna del registro."""
    row = connection.execute(
        f"SELECT {column} FROM {table} WHERE id = ?",
        (record_id,),
    ).fetchone()

    return str(row[column])


# --- Contrato de la serialización canónica ---


@pytest.mark.parametrize(("value", "expected"), CANONICAL_CASES)
def test_decimal_to_text_uses_the_canonical_representation(
    value: str, expected: str
) -> None:
    """Verifica el texto canónico de cada valor de referencia."""
    assert _decimal_to_text(Decimal(value)) == expected


@pytest.mark.parametrize(("value", "expected"), CANONICAL_CASES)
def test_decimal_to_text_preserves_the_exact_value_without_exponent(
    value: str, expected: str
) -> None:
    """Verifica que el texto conserva el valor exacto y no usa exponente."""
    text = _decimal_to_text(Decimal(value))

    assert Decimal(text) == Decimal(value)
    assert "e" not in text.lower()


@pytest.mark.parametrize("value", ["-0", "-0.0", "-0.000", "0", "0.000"])
def test_decimal_to_text_writes_zero_without_sign(value: str) -> None:
    """Verifica que el cero se serializa como `0`, sin signo ni ceros."""
    assert _decimal_to_text(Decimal(value)) == "0"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("-5", "-5"),
        ("-0.50", "-0.5"),
        ("-1E+2", "-100"),
        ("-1200.00", "-1200"),
    ],
)
def test_decimal_to_text_preserves_the_sign_of_negative_values(
    value: str, expected: str
) -> None:
    """Verifica que un valor negativo se serializa de forma fiel."""
    assert _decimal_to_text(Decimal(value)) == expected


def test_decimal_to_text_does_not_round_with_the_context_precision() -> None:
    """Verifica que la serialización no aplica la precisión del contexto."""
    value = Decimal("123456789012345678901234567890.5")

    assert _decimal_to_text(value) == "123456789012345678901234567890.5"
    assert Decimal(_decimal_to_text(value)) == value


def test_equivalent_decimals_share_the_same_canonical_text() -> None:
    """Verifica que valores equivalentes tienen una única representación."""
    assert _decimal_to_text(Decimal("10.50")) == _decimal_to_text(
        Decimal("10.5")
    )
    assert _decimal_to_text(Decimal("1E+2")) == _decimal_to_text(
        Decimal("100")
    )
    assert _decimal_to_text(Decimal("1200.00")) == _decimal_to_text(
        Decimal("1.2E+3")
    )


# --- Viaje de ida y vuelta por SQLite (compatibilidad con los CHECK) ---


@pytest.mark.parametrize(("value", "expected"), CANONICAL_CASES)
def test_sale_price_round_trip_keeps_the_canonical_text(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    value: str,
    expected: str,
) -> None:
    """Verifica que el precio se almacena canónico y vuelve con su valor."""
    product = register_product(products)
    stored = presentations.add(
        product_id=product.id,
        name="Unidad",
        factor=1,
        sale_price=Decimal(value),
    )
    reloaded = presentations.get(stored.id)

    assert stored_text(
        connection, "sale_price", "presentations", stored.id
    ) == expected
    assert reloaded is not None
    assert reloaded.sale_price == Decimal(value)


def test_applied_price_round_trip_keeps_the_canonical_text(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el precio aplicado de una salida se almacena canónico."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    stored = register_movement(
        movements,
        product.id,
        presentation.id,
        kind=MovementKind.LOOSE_EXIT,
        applied_price=Decimal("1500.00"),
    )
    reloaded = movements.get(stored.id)

    assert stored_text(
        connection, "applied_price", "movements", stored.id
    ) == "1500"
    assert reloaded is not None
    assert reloaded.applied_price == Decimal("1500.00")


def test_cost_round_trip_keeps_the_canonical_text(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el costo de una entrada se almacena canónico."""
    product = register_product(products)
    presentation = register_presentation(presentations, product.id)
    stored = register_movement(
        movements,
        product.id,
        presentation.id,
        kind=MovementKind.LOOSE_ENTRY,
        cost=Decimal("-0"),
    )
    reloaded = movements.get(stored.id)

    assert stored_text(connection, "cost", "movements", stored.id) == "0"
    assert reloaded is not None
    assert reloaded.cost == Decimal("0")


def test_price_check_rejects_the_values_invalid_for_the_domain(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que el rechazo de cero y negativos es del CHECK."""
    product = register_product(products)

    for value in ("-5", "0", "0.00"):
        with pytest.raises(sqlite3.IntegrityError):
            presentations.add(
                product_id=product.id,
                name=f"Precio {value}",
                factor=1,
                sale_price=Decimal(value),
            )

    assert presentations.list_by_product(product.id) == []
