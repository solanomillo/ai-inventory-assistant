"""Pruebas de integración del esquema SQLite (T008, T028, RF-02, RF-03, RF-07).

Verifican que el esquema se crea desde una base vacía con las tablas del MVP,
que las cantidades y contadores usan `INTEGER`, que los precios y costos se
almacenan como texto decimal exacto, que el índice único parcial de la
presentación base existe y restringe como corresponde y que las restricciones
de integridad del esquema rechazan los datos inválidos.

Cubren además la persistencia duradera y la migración versionada de T028: la
ruta configurable e independiente del directorio de trabajo, la creación del
esquema en una base en archivo con su versión registrada, la idempotencia, la
adopción de una base legada con datos, la persistencia al cerrar y reabrir, el
soporte de `:memory:` y el rechazo seguro de bases desconocidas o más nuevas,
incluida la reversión completa ante el fallo de una migración.
"""

import importlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from app import config
from app.infrastructure.sqlite import schema as schema_module
from app.infrastructure.sqlite.connection import IN_MEMORY_DATABASE, connect
from app.infrastructure.sqlite.schema import (
    IncompatibleSchemaError,
    create_schema,
    schema_version,
    user_tables,
)

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
    "price_changes": (
        "id",
        "product_id",
        "presentation_id",
        "previous_price",
        "new_price",
        "occurred_at",
        "event_type",
    ),
}

LEGACY_TABLES: tuple[str, ...] = (
    "products",
    "presentations",
    "movements",
    "proposals",
)
"""Tablas del esquema legado: el esquema base sin el historial de precios."""

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
    ("price_changes", "previous_price"),
    ("price_changes", "new_price"),
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


def row_count(connection: sqlite3.Connection, table: str) -> int:
    """Devuelve la cantidad de filas de una tabla."""
    return int(
        connection.execute(
            f"SELECT COUNT(*) AS total FROM {table}"
        ).fetchone()["total"]
    )


def build_legacy_schema(connection: sqlite3.Connection) -> None:
    """
    Crea el esquema legado: el esquema base sin el historial de precios (T029).

    Es la estructura que deja una base anterior a T029: las sentencias base,
    ejecutadas una por una, con `user_version` en 0.
    """
    for statement in schema_module.SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 0")


def insert_price_change(
    connection: sqlite3.Connection,
    product_id: int,
    presentation_id: int,
    previous_price: str | None = "100.5",
    new_price: str | None = "120",
    occurred_at: str | None = "2026-01-01T00:00:00Z",
    event_type: str | None = "cambio_precio",
) -> int:
    """Inserta un cambio de precio y devuelve su identificador."""
    cursor = connection.execute(
        """
        INSERT INTO price_changes (
            product_id, presentation_id, previous_price, new_price,
            occurred_at, event_type
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            presentation_id,
            previous_price,
            new_price,
            occurred_at,
            event_type,
        ),
    )

    return int(cursor.lastrowid)


# --- Creación del esquema desde una base vacía ---


def test_create_schema_creates_the_five_tables(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que la base vacía queda con las cinco tablas."""
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


def test_create_schema_is_idempotent(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que crear el esquema dos veces no falla ni borra datos."""
    product_id = insert_product(connection, sku="SKU-1")

    create_schema(connection)

    assert user_tables(connection) == set(EXPECTED_COLUMNS)
    assert column_names(connection, "products") == EXPECTED_COLUMNS["products"]
    assert schema_version(connection) == schema_module.SCHEMA_VERSION
    assert [
        row["id"] for row in connection.execute("SELECT id FROM products")
    ] == [product_id]


# --- Persistencia duradera y migración versionada (T028) ---


def test_the_default_database_path_is_absolute_and_module_relative() -> None:
    """Verifica que la ruta predeterminada es absoluta."""
    expected = (
        Path(config.__file__).resolve().parent.parent
        / "data"
        / "inventory.sqlite3"
    )

    assert config.DEFAULT_DATABASE_PATH == expected
    assert config.DEFAULT_DATABASE_PATH.is_absolute()


def test_an_empty_database_path_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verifica que un valor vacío usa la ruta predeterminada (I1).

    La configuración se lee al importar el módulo, así que la prueba recarga
    `app.config` con `INVENTORY_DB_PATH` vacío y neutraliza la creación del
    directorio: se verifica la ruta resuelta, no el efecto en disco, de modo
    que la prueba no deja rastros en el proyecto. Al terminar se restaura el
    entorno y se recarga el módulo para no afectar a las demás pruebas.
    """
    created_directories: list[Path] = []
    monkeypatch.setattr(
        Path,
        "mkdir",
        lambda self, **kwargs: created_directories.append(self),
    )
    monkeypatch.setenv("INVENTORY_DB_PATH", "")
    reloaded = importlib.reload(config)

    try:
        assert reloaded.INVENTORY_DB_PATH == str(
            reloaded.DEFAULT_DATABASE_PATH
        )
        assert reloaded.resolve_database_path() == (
            reloaded.DEFAULT_DATABASE_PATH
        )
        assert created_directories == [reloaded.DEFAULT_DATABASE_PATH.parent]
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_resolve_database_path_is_independent_from_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verifica que la ruta configurada se resuelva fuera del CWD (R3, R6)."""
    configured = tmp_path / "datos" / "inventario.sqlite3"
    other_directory = tmp_path / "otro"
    other_directory.mkdir()
    monkeypatch.setattr(config, "INVENTORY_DB_PATH", str(configured))
    monkeypatch.chdir(other_directory)

    resolved = config.resolve_database_path()

    assert resolved == configured
    assert resolved.parent.is_dir()


def test_create_schema_on_a_new_file_database_stamps_the_version(
    tmp_path: Path,
) -> None:
    """Verifica el esquema y la versión de una base nueva en archivo (R4)."""
    path = tmp_path / "inventory.sqlite3"
    connection = connect(path)
    try:
        create_schema(connection)

        assert user_tables(connection) == set(EXPECTED_COLUMNS)
        assert schema_version(connection) == schema_module.SCHEMA_VERSION
    finally:
        connection.close()


def test_create_schema_adopts_a_legacy_database_with_data(
    tmp_path: Path,
) -> None:
    """
    Verifica la adopción y la migración de una base legada con datos (T029).

    La base se construye como una base anterior a T029 —las cuatro tablas del
    esquema base, sin `price_changes` y con `user_version = 0`—: la adopción la
    valida, la migra a la versión 1 y conserva todas las filas.
    """
    path = tmp_path / "legacy.sqlite3"
    connection = connect(path)
    try:
        build_legacy_schema(connection)
        product_id = insert_product(connection, sku="SKU-1")
        presentation_id = insert_presentation(connection, product_id)
        insert_movement(connection, product_id, presentation_id)
        insert_movement(
            connection, product_id, presentation_id, kind="entrada_suelta"
        )
        before = {
            table: row_count(connection, table) for table in LEGACY_TABLES
        }

        create_schema(connection)

        assert schema_version(connection) == schema_module.SCHEMA_VERSION
        assert user_tables(connection) == set(EXPECTED_COLUMNS)
        assert {
            table: row_count(connection, table) for table in LEGACY_TABLES
        } == before
        assert row_count(connection, "price_changes") == 0
        stored_skus = [
            row["sku"]
            for row in connection.execute("SELECT sku FROM products")
        ]

        assert stored_skus == ["SKU-1"]
    finally:
        connection.close()


def test_data_persists_after_closing_and_reopening_the_file(
    tmp_path: Path,
) -> None:
    """Verifica que los datos persisten al cerrar y reabrir la base (T028)."""
    path = tmp_path / "inventory.sqlite3"
    first = connect(path)
    try:
        create_schema(first)
        product_id = insert_product(first, sku="SKU-1", name="Producto")
    finally:
        first.close()

    reopened = connect(path)
    try:
        create_schema(reopened)

        stored = reopened.execute(
            "SELECT id, sku, name FROM products"
        ).fetchone()

        assert stored["id"] == product_id
        assert stored["sku"] == "SKU-1"
        assert stored["name"] == "Producto"
        assert schema_version(reopened) == schema_module.SCHEMA_VERSION
    finally:
        reopened.close()


def test_connect_still_defaults_to_an_in_memory_database() -> None:
    """Verifica que `connect()` conserva `:memory:` por defecto (R1)."""
    default_connection = connect()
    try:
        databases = default_connection.execute(
            "PRAGMA database_list"
        ).fetchall()

        assert not databases[0]["file"]
        assert user_tables(default_connection) == set()
    finally:
        default_connection.close()


def test_create_schema_still_supports_in_memory_databases() -> None:
    """Verifica que `:memory:` sigue disponible para las pruebas (R1)."""
    memory_connection = connect(IN_MEMORY_DATABASE)
    try:
        create_schema(memory_connection)
        version = schema_version(memory_connection)

        assert user_tables(memory_connection) == set(EXPECTED_COLUMNS)
        assert version == schema_module.SCHEMA_VERSION
    finally:
        memory_connection.close()


def test_create_schema_rejects_an_unknown_structure(
    tmp_path: Path,
) -> None:
    """Verifica el rechazo de una base con tablas desconocidas (R4)."""
    path = tmp_path / "desconocida.sqlite3"
    connection = connect(path)
    try:
        connection.execute("CREATE TABLE otro (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO otro (id) VALUES (1)")

        with pytest.raises(IncompatibleSchemaError):
            create_schema(connection)

        assert user_tables(connection) == {"otro"}
        assert schema_version(connection) == 0
        assert connection.execute(
            "SELECT COUNT(*) AS total FROM otro"
        ).fetchone()["total"] == 1
    finally:
        connection.close()


def test_create_schema_rejects_an_incomplete_legacy_structure(
    tmp_path: Path,
) -> None:
    """Verifica el rechazo de las tablas del esquema con columnas ausentes."""
    path = tmp_path / "incompleta.sqlite3"
    connection = connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE products (id INTEGER PRIMARY KEY, sku TEXT);
            CREATE TABLE presentations (id INTEGER PRIMARY KEY);
            CREATE TABLE movements (id INTEGER PRIMARY KEY);
            CREATE TABLE proposals (id INTEGER PRIMARY KEY);
            """
        )
        connection.execute(
            "INSERT INTO products (id, sku) VALUES (1, 'SKU-1')"
        )

        with pytest.raises(IncompatibleSchemaError):
            create_schema(connection)

        stored_skus = [
            row["sku"]
            for row in connection.execute("SELECT sku FROM products")
        ]

        assert stored_skus == ["SKU-1"]
    finally:
        connection.close()


def test_create_schema_rejects_a_newer_schema_version(
    tmp_path: Path,
) -> None:
    """Verifica el rechazo de una base con una versión más nueva (R4)."""
    path = tmp_path / "futura.sqlite3"
    newer_version = schema_module.SCHEMA_VERSION + 1
    connection = connect(path)
    try:
        create_schema(connection)
        connection.execute(f"PRAGMA user_version = {newer_version}")

        with pytest.raises(IncompatibleSchemaError):
            create_schema(connection)

        assert schema_version(connection) == newer_version
    finally:
        connection.close()


def test_a_failing_migration_rolls_back_the_version_and_the_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica la reversión completa ante el fallo de una migración (R6)."""
    path = tmp_path / "migracion.sqlite3"
    connection = connect(path)
    try:
        create_schema(connection)
        product_id = insert_product(connection, sku="SKU-1")
        monkeypatch.setattr(schema_module, "SCHEMA_VERSION", 2)
        monkeypatch.setattr(
            schema_module,
            "MIGRATIONS",
            [
                (
                    2,
                    (
                        "ALTER TABLE products ADD COLUMN note TEXT",
                        "INVALID SQL",
                    ),
                )
            ],
        )

        with pytest.raises(sqlite3.OperationalError):
            create_schema(connection)

        assert schema_version(connection) == 1
        assert "note" not in column_names(connection, "products")
        assert [
            row["id"] for row in connection.execute("SELECT id FROM products")
        ] == [product_id]
    finally:
        connection.close()


# --- Historial de precios (T029) ---


def test_create_schema_migrates_a_legacy_database_to_version_1(
    tmp_path: Path,
) -> None:
    """Verifica la migración 0→1 sin perder tablas ni filas (T029)."""
    path = tmp_path / "historial.sqlite3"
    connection = connect(path)
    try:
        build_legacy_schema(connection)
        product_id = insert_product(connection, sku="SKU-1")
        insert_presentation(connection, product_id)
        assert schema_version(connection) == 0
        assert user_tables(connection) == set(LEGACY_TABLES)

        create_schema(connection)

        assert schema_version(connection) == 1
        assert user_tables(connection) - set(LEGACY_TABLES) == {
            "price_changes"
        }
        assert user_tables(connection) == set(EXPECTED_COLUMNS)
        assert row_count(connection, "products") == 1
        assert row_count(connection, "presentations") == 1
        assert row_count(connection, "price_changes") == 0
    finally:
        connection.close()


def test_price_changes_is_declared_only_in_the_migration() -> None:
    """Verifica que el historial llega como migración 1 (D1)."""
    base_statements = "\n".join(schema_module.SCHEMA_STATEMENTS)
    migration_statements = "\n".join(
        statement
        for _, statements in schema_module.MIGRATIONS
        for statement in statements
    )

    assert schema_module.SCHEMA_VERSION == 1
    assert [version for version, _ in schema_module.MIGRATIONS] == [1]
    assert "price_changes" not in base_statements
    assert "CREATE TABLE IF NOT EXISTS price_changes" in migration_statements
    assert "idx_price_changes_presentation_occurred_at" in (
        migration_statements
    )


def test_price_changes_has_only_the_presentation_index(
    connection: sqlite3.Connection,
) -> None:
    """Verifica el índice del historial y la ausencia de otro índice."""
    indexes = {
        str(row["name"]): row
        for row in connection.execute("PRAGMA index_list(price_changes)")
    }

    assert set(indexes) == {"idx_price_changes_presentation_occurred_at"}
    assert indexes["idx_price_changes_presentation_occurred_at"]["unique"] == 0

    indexed_columns = tuple(
        str(row["name"])
        for row in connection.execute(
            "PRAGMA index_info(idx_price_changes_presentation_occurred_at)"
        )
    )

    assert indexed_columns == ("presentation_id", "occurred_at")


def test_the_schema_defines_no_triggers(
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el esquema no define triggers (D3)."""
    triggers = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'trigger'"
    ).fetchall()

    assert triggers == []


def test_price_changes_references_existing_product_and_presentation(
    connection: sqlite3.Connection,
) -> None:
    """Verifica las claves foráneas del historial (T029)."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_price_change(connection, product_id + 1, presentation_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_price_change(connection, product_id, presentation_id + 1)

    insert_price_change(connection, product_id, presentation_id)

    assert row_count(connection, "price_changes") == 1


@pytest.mark.parametrize("price", ["10.5", "10", "0.5", "1200"])
def test_price_changes_accepts_canonical_decimal_text(
    connection: sqlite3.Connection,
    price: str,
) -> None:
    """Verifica los precios válidos del historial; iguales también (D6)."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)
    insert_price_change(
        connection,
        product_id,
        presentation_id,
        previous_price=price,
        new_price=price,
    )

    stored = connection.execute(
        "SELECT previous_price, new_price FROM price_changes"
    ).fetchone()

    assert stored["previous_price"] == price
    assert stored["new_price"] == price


@pytest.mark.parametrize(
    "price",
    [
        "1.2.3",
        ".5",
        "5.",
        "abc",
        "1e5",
        "-1",
        "0",
        "0.0",
        " 10",
        "10 ",
        "",
        None,
    ],
)
def test_price_changes_rejects_malformed_decimal_text(
    connection: sqlite3.Connection,
    price: str | None,
) -> None:
    """Verifica que los precios malformados se rechazan (T029)."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_price_change(
            connection,
            product_id,
            presentation_id,
            previous_price=price,
            new_price="10",
        )

    with pytest.raises(sqlite3.IntegrityError):
        insert_price_change(
            connection,
            product_id,
            presentation_id,
            previous_price="10",
            new_price=price,
        )

    assert row_count(connection, "price_changes") == 0


@pytest.mark.parametrize("event_type", ["", "   ", None])
def test_price_changes_event_type_cannot_be_blank(
    connection: sqlite3.Connection,
    event_type: str | None,
) -> None:
    """Verifica que el tipo de evento del historial no queda vacío (D2)."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)

    with pytest.raises(sqlite3.IntegrityError):
        insert_price_change(
            connection,
            product_id,
            presentation_id,
            event_type=event_type,
        )

    assert row_count(connection, "price_changes") == 0


def test_price_changes_stores_the_planned_data(
    connection: sqlite3.Connection,
) -> None:
    """Verifica los datos mínimos de un cambio de precio válido (T029)."""
    product_id = insert_product(connection)
    presentation_id = insert_presentation(connection, product_id)
    insert_price_change(
        connection,
        product_id,
        presentation_id,
        previous_price="10.5",
        new_price="1200",
        occurred_at="2026-02-01T12:00:00+00:00",
        event_type="cambio_precio",
    )

    stored = connection.execute(
        """
        SELECT product_id, presentation_id, previous_price, new_price,
               occurred_at, event_type
        FROM price_changes
        """
    ).fetchone()

    assert stored["product_id"] == product_id
    assert stored["presentation_id"] == presentation_id
    assert stored["previous_price"] == "10.5"
    assert stored["new_price"] == "1200"
    assert stored["occurred_at"] == "2026-02-01T12:00:00+00:00"
    assert stored["event_type"] == "cambio_precio"


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


# --- Índice único parcial de la presentación base ---


def test_single_base_index_is_unique_and_partial(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica que el índice de base única existe, es único y es parcial.

    El índice se inspecciona con `PRAGMA index_list` e `PRAGMA index_info`, no
    solo por su nombre en `sqlite_master`: debe declararse único, con la marca
    `partial`, sobre `presentations.product_id` y con la condición
    `factor = 1` (RF-07).
    """
    indexes = {
        str(row["name"]): row
        for row in connection.execute("PRAGMA index_list(presentations)")
    }
    assert "idx_presentations_single_base" in indexes
    index = indexes["idx_presentations_single_base"]
    assert index["unique"] == 1
    assert index["partial"] == 1

    indexed_columns = tuple(
        str(row["name"])
        for row in connection.execute(
            "PRAGMA index_info(idx_presentations_single_base)"
        )
    )
    assert indexed_columns == ("product_id",)

    definition = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        ("idx_presentations_single_base",),
    ).fetchone()
    assert definition is not None
    assert "WHERE factor = 1" in str(definition["sql"])


def test_single_base_index_rejects_a_second_base_of_the_product(
    connection: sqlite3.Connection,
) -> None:
    """
    Verifica que el índice parcial impide dos bases del mismo producto.

    El índice solo alcanza a las presentaciones con factor 1 y a cada producto
    por separado: una presentación no base y la base de otro producto se
    aceptan (RF-07).
    """
    first_product_id = insert_product(connection, sku="SKU-1")
    second_product_id = insert_product(connection, sku="SKU-2")
    insert_presentation(
        connection,
        first_product_id,
        name="Unidad",
        factor=1,
        sale_price="30.00",
    )
    insert_presentation(connection, first_product_id, name="Caja x6")
    insert_presentation(
        connection,
        second_product_id,
        name="Unidad",
        factor=1,
        sale_price="30.00",
    )

    with pytest.raises(sqlite3.IntegrityError):
        insert_presentation(
            connection,
            first_product_id,
            name="Otra unidad",
            factor=1,
            sale_price="40.00",
        )

    bases = [
        row["product_id"]
        for row in connection.execute(
            "SELECT product_id FROM presentations WHERE factor = 1 "
            "ORDER BY product_id"
        )
    ]
    assert bases == [first_product_id, second_product_id]
