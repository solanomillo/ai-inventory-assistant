"""Pruebas de integración del esquema SQLite (T008, RF-02, RF-03 y RF-07).

Verifican que el esquema se crea desde una base vacía con las tablas del MVP,
que las cantidades y contadores usan `INTEGER`, que los precios y costos se
almacenan como texto decimal exacto y que las restricciones de integridad del
esquema rechazan los datos inválidos.
"""

import sqlite3
from collections.abc import Iterator

import pytest

from app.infrastructure.sqlite.connection import IN_MEMORY_DATABASE, connect
from app.infrastructure.sqlite.schema import create_schema

MOVEMENT_KINDS: tuple[str, ...] = (
    "entrada_cerrada",
    "entrada_suelta",
    "salida_cerrada",
    "salida_suelta",
    "apertura",
    "compensacion",
)

EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "products": (
        "id",
        "sku",
        "name",
        "base_unit",
        "minimum_stock",
        "loose_units",
        "active",
    ),
    "presentations": (
        "id",
        "product_id",
        "name",
        "factor",
        "sale_price",
        "closed_stock",
    ),
    "movements": (
        "id",
        "product_id",
        "presentation_id",
        "kind",
        "quantity",
        "reason",
        "occurred_at",
        "cost",
        "applied_price",
        "is_initial_stock",
        "original_movement_id",
        "consumed_quantity",
        "compensated_quantity",
    ),
    "proposals": ("id", "action_type", "data", "status", "created_at"),
}

INTEGER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("products", "minimum_stock"),
    ("products", "loose_units"),
    ("presentations", "factor"),
    ("presentations", "closed_stock"),
    ("movements", "quantity"),
    ("movements", "consumed_quantity"),
    ("movements", "compensated_quantity"),
)

DECIMAL_TEXT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("presentations", "sale_price"),
    ("movements", "cost"),
    ("movements", "applied_price"),
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


def column_names(
    connection: sqlite3.Connection, table: str
) -> tuple[str, ...]:
    """Devuelve los nombres de columna de una tabla, en su orden."""
    return tuple(
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})")
    )


def column_type(
    connection: sqlite3.Connection, table: str, column: str
) -> str:
    """Devuelve el tipo declarado de una columna."""
    declared_types = {
        str(row["name"]): str(row["type"])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }
    assert column in declared_types, f"Falta la columna {table}.{column}."

    return declared_types[column]


def insert_product(
    connection: sqlite3.Connection,
    sku: str = "SKU-1",
    name: str = "Producto",
    base_unit: str = "unidad",
) -> int:
    """Inserta un producto mínimo válido y devuelve su identificador."""
    cursor = connection.execute(
        "INSERT INTO products (sku, name, base_unit) VALUES (?, ?, ?)",
        (sku, name, base_unit),
    )

    return int(cursor.lastrowid)


def insert_presentation(
    connection: sqlite3.Connection,
    product_id: int,
    name: str = "Caja x6",
    factor: int = 6,
    sale_price: str = "1200.00",
) -> int:
    """Inserta una presentación válida y devuelve su identificador."""
    cursor = connection.execute(
        """
        INSERT INTO presentations (product_id, name, factor, sale_price)
        VALUES (?, ?, ?, ?)
        """,
        (product_id, name, factor, sale_price),
    )

    return int(cursor.lastrowid)


def insert_movement(
    connection: sqlite3.Connection,
    product_id: int,
    presentation_id: int,
    kind: str = "entrada_cerrada",
    quantity: int = 1,
    reason: str | None = "Compra",
    occurred_at: str = "2026-01-01T00:00:00Z",
    cost: str | None = None,
    applied_price: str | None = None,
    is_initial_stock: int = 0,
    original_movement_id: int | None = None,
    consumed_quantity: int = 0,
    compensated_quantity: int = 0,
) -> int:
    """Inserta un movimiento y devuelve su identificador."""
    cursor = connection.execute(
        """
        INSERT INTO movements (
            product_id, presentation_id, kind, quantity, reason, occurred_at,
            cost, applied_price, is_initial_stock, original_movement_id,
            consumed_quantity, compensated_quantity
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            presentation_id,
            kind,
            quantity,
            reason,
            occurred_at,
            cost,
            applied_price,
            is_initial_stock,
            original_movement_id,
            consumed_quantity,
            compensated_quantity,
        ),
    )

    return int(cursor.lastrowid)


# --- Creación del esquema desde una base vacía ---


def test_create_schema_creates_the_four_tables(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que la base vacía queda con las tablas del MVP."""
    tables = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    assert tables == set(EXPECTED_COLUMNS)


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_table_has_the_planned_columns(
    connection: sqlite3.Connection, table: str
) -> None:
    """Verifica que cada tabla expone las columnas previstas en `plan.md`."""
    assert column_names(connection, table) == EXPECTED_COLUMNS[table]


def test_schema_creation_on_an_existing_schema_fails(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que no hay migración: crear el esquema dos veces falla."""
    with pytest.raises(sqlite3.OperationalError):
        create_schema(connection)


# --- Representación persistida ---


@pytest.mark.parametrize(("table", "column"), INTEGER_COLUMNS)
def test_integer_columns_use_integer_affinity(
    connection: sqlite3.Connection, table: str, column: str
) -> None:
    """Verifica que cantidades, factores, saldos y contadores son INTEGER."""
    assert column_type(connection, table, column) == "INTEGER"


@pytest.mark.parametrize(("table", "column"), DECIMAL_TEXT_COLUMNS)
def test_decimal_columns_use_text_affinity(
    connection: sqlite3.Connection, table: str, column: str
) -> None:
    """Verifica que los precios y costos se almacenan como TEXT exacto."""
    assert column_type(connection, table, column) == "TEXT"


def test_sale_price_keeps_the_decimal_text(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el precio almacenado conserva el texto decimal exacto."""
    product_id = insert_product(connection)
    insert_presentation(
        connection, product_id, name="Unidad", factor=1, sale_price="0.30"
    )

    stored_price = connection.execute(
        "SELECT sale_price FROM presentations"
    ).fetchone()

    assert stored_price["sale_price"] == "0.30"


def test_movements_include_fifo_traceability_counters(
    connection: sqlite3.Connection,
) -> None:
    """Verifica los campos FIFO de las entradas cerradas (RF-03)."""
    movement_columns = column_names(connection, "movements")

    assert "consumed_quantity" in movement_columns
    assert "compensated_quantity" in movement_columns


# --- Unicidad sin distinguir mayúsculas de minúsculas ---


def test_sku_is_unique_without_case_sensitivity(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que un SKU que difiere en el caso se rechaza."""
    insert_product(connection, sku="coca-1.5l")

    with pytest.raises(sqlite3.IntegrityError):
        insert_product(connection, sku="COCA-1.5L")


def test_sku_keeps_the_original_text(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el texto original del SKU no se normaliza."""
    insert_product(connection, sku="Coca-1.5L")

    stored = connection.execute("SELECT sku FROM products").fetchone()

    assert stored["sku"] == "Coca-1.5L"


def test_presentation_name_is_unique_per_product_without_case(
    connection: sqlite3.Connection,
) -> None:
    """Verifica la unicidad del nombre de presentación dentro del producto."""
    product_id = insert_product(connection)
    insert_presentation(connection, product_id, name="Caja x6")

    with pytest.raises(sqlite3.IntegrityError):
        insert_presentation(connection, product_id, name="CAJA X6")


def test_presentation_name_is_not_globally_unique(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que otro producto puede reutilizar el mismo nombre."""
    first_product_id = insert_product(connection, sku="SKU-1")
    second_product_id = insert_product(connection, sku="SKU-2")

    insert_presentation(connection, first_product_id, name="Caja x6")
    insert_presentation(connection, second_product_id, name="Caja x6")

    stored_names = connection.execute(
        "SELECT name FROM presentations"
    ).fetchall()

    assert [row["name"] for row in stored_names] == ["Caja x6", "Caja x6"]


# --- Restricciones de positividad y no negatividad ---


def test_minimum_stock_cannot_be_negative(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el stock mínimo es un entero no negativo."""
    minimum_stock = -1

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO products (sku, name, base_unit, minimum_stock)
            VALUES ('SKU-1', 'Producto', 'unidad', ?)
            """,
            (minimum_stock,),
        )


def test_loose_units_cannot_be_negative(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que las unidades sueltas no pueden ser negativas."""
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO products (sku, name, base_unit, loose_units)
            VALUES ('SKU-1', 'Producto', 'unidad', -1)
            """
        )


def test_closed_stock_cannot_be_negative(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el stock cerrado no puede ser negativo."""
    product_id = insert_product(connection)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO presentations (
                product_id, name, factor, sale_price, closed_stock
            ) VALUES (?, 'Caja x6', 6, '1200.00', -1)
            """,
            (product_id,),
        )


@pytest.mark.parametrize("factor", [0, -2])
def test_presentation_factor_must_be_at_least_one(
    connection: sqlite3.Connection, factor: int
) -> None:
    """Verifica que el factor de conversión es mayor o igual que uno."""
    product_id = insert_product(connection)

    with pytest.raises(sqlite3.IntegrityError):
        insert_presentation(connection, product_id, factor=factor)


def test_presentation_factor_of_one_is_accepted(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que la presentación base con factor 1 se acepta."""
    product_id = insert_product(connection)
    insert_presentation(
        connection, product_id, name="Unidad", factor=1, sale_price="30.00"
    )

    stored = connection.execute(
        "SELECT factor FROM presentations"
    ).fetchone()

    assert stored["factor"] == 1


@pytest.mark.parametrize("sale_price", ["0", "0.00", "-1.50", "abc"])
def test_sale_price_must_be_a_positive_decimal(
    connection: sqlite3.Connection, sale_price: str
) -> None:
    """Verifica que el precio de venta es un decimal mayor que cero."""
    product_id = insert_product(connection)

    with pytest.raises(sqlite3.IntegrityError):
        insert_presentation(connection, product_id, sale_price=sale_price)


@pytest.mark.parametrize("quantity", [0, -3])
def test_movement_quantity_must_be_positive(
    connection: sqlite3.Connection, quantity: int
) -> None:
    """Verifica que la cantidad de un movimiento es mayor que cero."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection, product_id, presentation_id, quantity=quantity
        )


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_movement_reason_cannot_be_blank(
    connection: sqlite3.Connection, reason: str | None
) -> None:
    """Verifica que el motivo de un movimiento no puede quedar vacío."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection, product_id, presentation_id, reason=reason
        )


@pytest.mark.parametrize(
    ("consumed_quantity", "compensated_quantity"),
    [(2, 0), (0, 2), (-1, 0), (1, 1)],
)
def test_fifo_counters_cannot_exceed_the_original_quantity(
    connection: sqlite3.Connection,
    consumed_quantity: int,
    compensated_quantity: int,
) -> None:
    """Verifica que el consumo y la compensación no superan la cantidad."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection,
            product_id,
            presentation_id,
            quantity=1,
            consumed_quantity=consumed_quantity,
            compensated_quantity=compensated_quantity,
        )


def test_fifo_counters_accept_valid_values(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el consumo y la compensación válidos se persisten."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)
    insert_movement(
        connection,
        product_id,
        presentation_id,
        quantity=3,
        consumed_quantity=1,
        compensated_quantity=2,
    )

    stored = connection.execute(
        "SELECT quantity, consumed_quantity, compensated_quantity "
        "FROM movements"
    ).fetchone()

    assert stored["quantity"] == 3
    assert stored["consumed_quantity"] == 1
    assert stored["compensated_quantity"] == 2


def test_active_flag_accepts_only_boolean_values(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que la bandera de actividad solo admite 0 o 1."""
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO products (sku, name, base_unit, active)
            VALUES ('SKU-1', 'Producto', 'unidad', 2)
            """
        )


# --- Restricciones de los movimientos ---


@pytest.mark.parametrize("kind", MOVEMENT_KINDS)
def test_movement_kind_accepts_domain_values(
    connection: sqlite3.Connection, kind: str
) -> None:
    """Verifica que los seis tipos del dominio se aceptan (RF-03)."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)
    is_entry = kind in ("entrada_cerrada", "entrada_suelta")
    is_exit = kind in ("salida_cerrada", "salida_suelta")
    original_movement_id = None
    if kind == "compensacion":
        original_movement_id = insert_movement(
            connection, product_id, presentation_id
        )

    insert_movement(
        connection,
        product_id,
        presentation_id,
        kind=kind,
        cost="10.00" if is_entry else None,
        applied_price="30.00" if is_exit else None,
        original_movement_id=original_movement_id,
    )

    stored_kinds = [
        row["kind"]
        for row in connection.execute("SELECT kind FROM movements")
    ]

    assert stored_kinds[-1] == kind


def test_unknown_movement_kind_is_rejected(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que un tipo de movimiento desconocido se rechaza."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection, product_id, presentation_id, kind="entrada"
        )


@pytest.mark.parametrize("cost", ["-1.00", "abc"])
def test_movement_cost_cannot_be_negative_or_non_numeric(
    connection: sqlite3.Connection, cost: str
) -> None:
    """Verifica que el costo opcional no puede ser negativo."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection, product_id, presentation_id, cost=cost
        )


def test_movement_cost_of_zero_is_accepted(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que un costo igual a cero es válido (RF-07)."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)
    insert_movement(connection, product_id, presentation_id, cost="0.00")

    stored = connection.execute("SELECT cost FROM movements").fetchone()

    assert stored["cost"] == "0.00"


def test_cost_is_rejected_on_a_non_entry_movement(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el costo solo se admite en entradas (RF-03)."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection,
            product_id,
            presentation_id,
            kind="salida_cerrada",
            cost="10.00",
        )


@pytest.mark.parametrize("applied_price", ["0", "0.00", "-5.00"])
def test_applied_price_must_be_positive(
    connection: sqlite3.Connection, applied_price: str
) -> None:
    """Verifica que el precio aplicado de una salida es mayor que cero."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection,
            product_id,
            presentation_id,
            kind="salida_cerrada",
            applied_price=applied_price,
        )


def test_applied_price_is_rejected_on_an_entry_movement(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el precio aplicado solo se admite en salidas (RF-03)."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection,
            product_id,
            presentation_id,
            applied_price="30.00",
        )


def test_initial_stock_flag_only_applies_to_entries(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el stock inicial solo marca entradas (RF-02)."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection,
            product_id,
            presentation_id,
            kind="salida_suelta",
            is_initial_stock=1,
        )


def test_initial_stock_flag_is_accepted_on_entries(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que las entradas de stock inicial se marcan correctamente."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)
    insert_movement(
        connection,
        product_id,
        presentation_id,
        kind="entrada_suelta",
        is_initial_stock=1,
    )

    stored = connection.execute(
        "SELECT is_initial_stock FROM movements"
    ).fetchone()

    assert stored["is_initial_stock"] == 1


def test_compensation_requires_an_original_movement(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que una compensación se vincula a un movimiento original."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection, product_id, presentation_id, kind="compensacion"
        )


def test_original_movement_is_rejected_on_a_non_compensation(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que solo las compensaciones referencian un original."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)
    original_movement_id = insert_movement(
        connection, product_id, presentation_id
    )

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection,
            product_id,
            presentation_id,
            kind="salida_cerrada",
            original_movement_id=original_movement_id,
        )


# --- Claves foráneas ---


def test_foreign_keys_are_enforced_for_movements(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que los movimientos referencian productos y presentaciones."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(connection, product_id + 1, presentation_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(connection, product_id, presentation_id + 1)


def test_presentations_reference_an_existing_product(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que una presentación requiere un producto existente."""
    with pytest.raises(sqlite3.IntegrityError):
        insert_presentation(connection, 999)


def test_compensation_references_an_existing_movement(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que la compensación apunta a un movimiento existente."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_movement(
            connection,
            product_id,
            presentation_id,
            kind="compensacion",
            original_movement_id=999,
        )


# --- Propuestas ---


def test_proposal_stores_action_type_data_and_status(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que una propuesta válida se registra sin tocar el stock."""
    connection.execute(
        """
        INSERT INTO proposals (action_type, data, status, created_at)
        VALUES ('alta_producto', '{"sku": "SKU-1"}', 'pendiente',
                '2026-01-01T00:00:00Z')
        """
    )

    stored = connection.execute(
        "SELECT action_type, data, status FROM proposals"
    ).fetchone()

    assert stored["action_type"] == "alta_producto"
    assert stored["status"] == "pendiente"


@pytest.mark.parametrize(
    ("action_type", "data", "status"),
    [("  ", "{}", "pendiente"), ("alta", None, "pendiente")],
)
def test_proposal_requires_action_type_and_data(
    connection: sqlite3.Connection,
    action_type: str,
    data: str | None,
    status: str,
) -> None:
    """Verifica que la propuesta exige tipo de acción y datos."""
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO proposals (action_type, data, status, created_at)
            VALUES (?, ?, ?, '2026-01-01T00:00:00Z')
            """,
            (action_type, data, status),
        )


def test_proposal_status_cannot_be_blank(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el estado de la propuesta no puede quedar vacío."""
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO proposals (action_type, data, status, created_at)
            VALUES ('alta_producto', '{}', '', '2026-01-01T00:00:00Z')
            """
        )


# --- Índices de consulta ---


def test_movement_query_indexes_exist(
    connection: sqlite3.Connection,
) -> None:
    """Verifica los índices de los filtros de movimientos (RF-03)."""
    index_names = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
    }

    assert {
        "idx_movements_product_id",
        "idx_movements_presentation_id",
        "idx_movements_occurred_at",
    } <= index_names
