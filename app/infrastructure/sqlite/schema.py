"""Esquema SQLite del núcleo de inventario (RF-02, RF-03, RF-07 y RF-08).

Define el esquema persistente del MVP, su creación sobre una base nueva y la
migración versionada de las bases existentes (T028).

Los nombres de tablas y columnas están en inglés, conforme al código en inglés
de la constitución, y los valores persistidos de `movements.kind` conservan los
identificadores del dominio en español (`entrada_cerrada`, `entrada_suelta`,
`salida_cerrada`, `salida_suelta`, `apertura` y `compensacion`).

Representación persistida:

- Cantidades, factores, saldos y contadores FIFO: `INTEGER`.
- Precios y costos: `TEXT` con el texto canónico de un `Decimal` exacto, porque
  la especificación no define una precisión decimal y no se admiten `float`.
- Instantes: `TEXT` en UTC.

Historial de precios (RF-08, T029):

- La tabla `price_changes` y su índice
  `idx_price_changes_presentation_occurred_at` llegan como la migración 1, no
  en el esquema base: una base existente se actualiza a `user_version = 1` sin
  perder filas.
- Cada cambio guarda el producto, la presentación, el precio anterior y el
  nuevo, el instante y el tipo de evento, que no puede quedar vacío.
  `product_id` se denormaliza junto a `presentation_id` (D5); su coherencia con
  la presentación la valida de forma transaccional el servicio que registra el
  cambio (T031), porque el esquema no puede expresarla sin triggers (D3), como
  en `movements`.
- Los precios del historial solo admiten dígitos con, a lo sumo, un punto
  decimal interno y un valor mayor que cero: se rechazan la notación
  científica, los signos, el punto inicial o final, los espacios y cualquier
  otro carácter.
- El historial es de solo anexado: no se edita ni se borra desde la aplicación,
  y esa garantía vive en el contrato del repositorio (T030), no en triggers
  (D3). No hay índice por producto (D4) ni `CHECK` que exija precios distintos
  (D6).

Migración versionada (R4, R5 y R6):

- La versión del esquema se registra en `PRAGMA user_version` y la versión que
  implementa este módulo es `SCHEMA_VERSION`. Las migraciones se declaran en
  `MIGRATIONS`, en orden, con sus sentencias individuales.
- Una base vacía recibe el esquema base y sus migraciones. Una base existente
  **sin versión** se adopta solo si su estructura coincide con el esquema
  legado conocido (`LEGACY_SCHEMA`: las cuatro tablas del esquema base, sin
  `price_changes`, que es anterior a esta tarea); cualquier otra estructura se
  rechaza con `IncompatibleSchemaError` sin borrar ni recrear datos. Una base
  con una versión **más nueva** que la implementada también se rechaza: la
  aplicación no puede migrarla hacia atrás.
- Las sentencias se ejecutan una por una —sin `executescript`— dentro de una
  única transacción `BEGIN IMMEDIATE`, y la actualización de `user_version`
  ocurre en la misma transacción: un fallo revierte todo y no deja la versión
  adelantada respecto del esquema ni cambios parciales.
- La operación es idempotente: repetirla sobre una base preparada no falla y no
  modifica ninguna fila.
"""

import sqlite3

from app.infrastructure.sqlite.connection import transaction


class SchemaError(ValueError):
    """Error relacionado con el esquema de la base de datos."""


class IncompatibleSchemaError(SchemaError):
    """
    Error que indica una base con una estructura o una versión desconocida.

    La base no se modifica: se rechaza sin borrar, recrear ni migrar datos.
    """


SCHEMA_VERSION: int = 1
"""Versión del esquema que implementa este módulo."""

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    -- Productos: identificación, unidad base y saldos sueltos (RF-01, RF-02).
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY,
        sku TEXT NOT NULL COLLATE NOCASE UNIQUE,
        name TEXT NOT NULL,
        base_unit TEXT NOT NULL,
        minimum_stock INTEGER NOT NULL DEFAULT 0 CHECK (minimum_stock >= 0),
        loose_units INTEGER NOT NULL DEFAULT 0 CHECK (loose_units >= 0),
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        CHECK (trim(sku) <> ''),
        CHECK (trim(name) <> ''),
        CHECK (trim(base_unit) <> '')
    )
    """,
    """
    -- Presentaciones: equivalencia en unidades base y precio (RF-07).
    CREATE TABLE IF NOT EXISTS presentations (
        id INTEGER PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products (id),
        name TEXT NOT NULL COLLATE NOCASE,
        factor INTEGER NOT NULL CHECK (factor >= 1),
        sale_price TEXT NOT NULL CHECK (
            sale_price GLOB '[0-9]*'
            AND CAST(sale_price AS NUMERIC) > 0
        ),
        closed_stock INTEGER NOT NULL DEFAULT 0 CHECK (closed_stock >= 0),
        CHECK (trim(name) <> ''),
        UNIQUE (product_id, name)
    )
    """,
    """
    -- Movimientos del inventario, con la trazabilidad FIFO de las entradas
    -- cerradas y los vínculos de las compensaciones (RF-02, RF-03).
    CREATE TABLE IF NOT EXISTS movements (
        id INTEGER PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products (id),
        presentation_id INTEGER NOT NULL REFERENCES presentations (id),
        kind TEXT NOT NULL CHECK (
            kind IN (
                'entrada_cerrada',
                'entrada_suelta',
                'salida_cerrada',
                'salida_suelta',
                'apertura',
                'compensacion'
            )
        ),
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        reason TEXT NOT NULL CHECK (trim(reason) <> ''),
        occurred_at TEXT NOT NULL,
        cost TEXT CHECK (
            cost IS NULL
            OR (
                kind IN ('entrada_cerrada', 'entrada_suelta')
                AND cost GLOB '[0-9]*'
                AND CAST(cost AS NUMERIC) >= 0
            )
        ),
        applied_price TEXT CHECK (
            applied_price IS NULL
            OR (
                kind IN ('salida_cerrada', 'salida_suelta')
                AND applied_price GLOB '[0-9]*'
                AND CAST(applied_price AS NUMERIC) > 0
            )
        ),
        is_initial_stock INTEGER NOT NULL DEFAULT 0 CHECK (
            is_initial_stock IN (0, 1)
            AND (
                is_initial_stock = 0
                OR kind IN ('entrada_cerrada', 'entrada_suelta')
            )
        ),
        original_movement_id INTEGER REFERENCES movements (id),
        consumed_quantity INTEGER NOT NULL DEFAULT 0
            CHECK (consumed_quantity >= 0),
        compensated_quantity INTEGER NOT NULL DEFAULT 0
            CHECK (compensated_quantity >= 0),
        CHECK (consumed_quantity + compensated_quantity <= quantity),
        CHECK (
            (kind = 'compensacion' AND original_movement_id IS NOT NULL)
            OR (kind <> 'compensacion' AND original_movement_id IS NULL)
        )
    )
    """,
    """
    -- Propuestas del asistente: se registran sin tocar el inventario (RF-05).
    CREATE TABLE IF NOT EXISTS proposals (
        id INTEGER PRIMARY KEY,
        action_type TEXT NOT NULL CHECK (trim(action_type) <> ''),
        data TEXT NOT NULL,
        status TEXT NOT NULL CHECK (trim(status) <> ''),
        created_at TEXT NOT NULL
    )
    """,
    """
    -- Índices de los filtros de consulta de movimientos (RF-03).
    CREATE INDEX IF NOT EXISTS idx_movements_product_id
        ON movements (product_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_movements_presentation_id
        ON movements (presentation_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_movements_occurred_at
        ON movements (occurred_at)
    """,
    """
    -- Una única presentación base (factor 1) por producto (RF-07). Refuerza en
    -- la base la invariante que valida el dominio (defensa en profundidad).
    CREATE UNIQUE INDEX IF NOT EXISTS idx_presentations_single_base
        ON presentations (product_id)
        WHERE factor = 1
    """,
)
"""Sentencias del esquema base, ejecutables una por una (R6)."""

def _positive_decimal_text_check(column: str) -> str:
    """
    Devuelve el `CHECK` de un precio como texto decimal positivo (T029, R4).

    Admite solo dígitos con, a lo sumo, un punto decimal interno y un valor
    mayor que cero: el mismo texto que escribe `_decimal_to_text` en
    `app/infrastructure/sqlite/repositories.py`. La primera regla exige que el
    texto empiece con un dígito y la segunda que termine con uno, de modo que
    `.5` y `5.` se rechazan; la tercera descarta cualquier carácter que no sea
    dígito o punto, y la cuarta admite un único punto decimal.

    Args:
        column: Nombre de la columna del precio.

    Returns:
        La condición `CHECK` correspondiente a esa columna.
    """
    return (
        f"{column} GLOB '[0-9]*'"
        f" AND {column} GLOB '*[0-9]'"
        f" AND NOT {column} GLOB '*[^0-9.]*'"
        f" AND length({column}) - length(replace({column}, '.', '')) <= 1"
        f" AND CAST({column} AS NUMERIC) > 0"
    )


_MIGRATION_1_STATEMENTS: tuple[str, ...] = (
    f"""
    -- Historial de cambios de precio de cada presentación (RF-08). Es de solo
    -- anexado: no hay triggers y la ausencia de edición o borrado la garantiza
    -- el contrato del repositorio (T030). `product_id` se denormaliza junto a
    -- `presentation_id` (D5); la coherencia entre ambos la valida de forma
    -- transaccional el servicio que registra el cambio (T031), porque el
    -- esquema no puede expresarla sin triggers (D3), igual que en `movements`.
    CREATE TABLE IF NOT EXISTS price_changes (
        id INTEGER PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products (id),
        presentation_id INTEGER NOT NULL REFERENCES presentations (id),
        previous_price TEXT NOT NULL CHECK (
            {_positive_decimal_text_check("previous_price")}
        ),
        new_price TEXT NOT NULL CHECK (
            {_positive_decimal_text_check("new_price")}
        ),
        occurred_at TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK (trim(event_type) <> '')
    )
    """,
    """
    -- Consulta cronológica del historial por presentación (RF-08). No hay
    -- índice por producto (D4): las consultas por producto se resuelven por
    -- presentación.
    CREATE INDEX IF NOT EXISTS idx_price_changes_presentation_occurred_at
        ON price_changes (presentation_id, occurred_at)
    """,
)
"""Sentencias de la migración 1 (T029), ejecutables una por una (R6)."""

MIGRATIONS: list[tuple[int, tuple[str, ...]]] = [
    (1, _MIGRATION_1_STATEMENTS),
]
"""Migraciones versionadas: versión de destino y sus sentencias (R5, D1)."""

LEGACY_SCHEMA: dict[str, frozenset[str]] = {
    "products": frozenset(
        {
            "id",
            "sku",
            "name",
            "base_unit",
            "minimum_stock",
            "loose_units",
            "active",
        }
    ),
    "presentations": frozenset(
        {"id", "product_id", "name", "factor", "sale_price", "closed_stock"}
    ),
    "movements": frozenset(
        {
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
        }
    ),
    "proposals": frozenset(
        {"id", "action_type", "data", "status", "created_at"}
    ),
}
"""
Estructura del esquema legado que la aplicación adopta (R4).

Es la estructura anterior a T029, sin versión registrada: las cuatro tablas del
esquema base —sin `price_changes`, que llega como migración— con sus columnas.
Las estructuras nuevas se incorporan como migraciones, no cambiando esta lista.
"""


def schema_version(connection: sqlite3.Connection) -> int:
    """
    Devuelve la versión del esquema registrada en la base (R4).

    Args:
        connection: Conexión SQLite abierta.

    Returns:
        El valor de `PRAGMA user_version`.
    """
    row = connection.execute("PRAGMA user_version").fetchone()

    return int(row[0])


def user_tables(connection: sqlite3.Connection) -> set[str]:
    """
    Devuelve las tablas de la aplicación, sin las internas de SQLite (R4).

    Args:
        connection: Conexión SQLite abierta.

    Returns:
        El conjunto de nombres de tablas creadas por la aplicación.
    """
    return {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def create_schema(connection: sqlite3.Connection) -> None:
    """
    Garantiza el esquema del inventario y su versión (T028, R4-R6).

    Sobre una base vacía crea el esquema base; sobre una base existente sin
    versión la adopta solo si su estructura coincide con el esquema legado
    conocido; sobre una base ya versionada aplica únicamente las migraciones
    pendientes. El esquema, las migraciones y la actualización de
    `PRAGMA user_version` se ejecutan dentro de una única transacción
    `BEGIN IMMEDIATE`, de modo que un fallo no deja la versión adelantada
    respecto del esquema ni cambios parciales.

    La operación es idempotente: repetirla sobre una base ya preparada no falla
    y no modifica ninguna fila.

    Args:
        connection: Conexión SQLite abierta sobre la base que se inicializa.

    Raises:
        IncompatibleSchemaError: Si la base registra una versión más nueva que
            la implementada, o si no tiene versión y su estructura no coincide
            con el esquema legado conocido. La base no se modifica.
        sqlite3.Error: Si una sentencia del esquema o de una migración falla;
            la transacción se revierte por completo.
    """
    version = schema_version(connection)
    if version > SCHEMA_VERSION:
        raise IncompatibleSchemaError(
            "La base registra la versión de esquema "
            f"{version}, más nueva que la que implementa la aplicación "
            f"({SCHEMA_VERSION}); no se modifica la base."
        )

    tables = user_tables(connection)
    if tables and version == 0:
        _validate_legacy_schema(connection, tables)

    with transaction(connection):
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        for target_version, statements in MIGRATIONS:
            if version < target_version <= SCHEMA_VERSION:
                for statement in statements:
                    connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")


def _validate_legacy_schema(
    connection: sqlite3.Connection,
    tables: set[str],
) -> None:
    """
    Exige que la base sin versión coincida con el esquema legado (R4).

    Args:
        connection: Conexión SQLite abierta.
        tables: Tablas de la aplicación presentes en la base.

    Raises:
        IncompatibleSchemaError: Si falta o sobra alguna tabla, o si alguna
            tabla no expone exactamente las columnas del esquema legado.
    """
    expected_tables = set(LEGACY_SCHEMA)
    missing = expected_tables - tables
    unexpected = tables - expected_tables
    if missing or unexpected:
        raise IncompatibleSchemaError(
            "La base no tiene la estructura esperada: tablas faltantes "
            f"{sorted(missing)} y tablas desconocidas {sorted(unexpected)}; "
            "no se modifica la base."
        )

    for table, expected_columns in LEGACY_SCHEMA.items():
        columns = _table_columns(connection, table)
        if columns != set(expected_columns):
            raise IncompatibleSchemaError(
                f"La tabla '{table}' no tiene las columnas del esquema "
                "legado conocido; no se modifica la base."
            )


def _table_columns(
    connection: sqlite3.Connection,
    table: str,
) -> set[str]:
    """Devuelve las columnas declaradas de una tabla (R4)."""
    return {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})")
    }
