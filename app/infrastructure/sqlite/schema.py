"""Esquema SQLite del núcleo de inventario (RF-02, RF-03 y RF-07).

Define el esquema persistente del MVP y su creación sobre una base vacía. No
incluye migraciones de datos históricos: el esquema se crea íntegro desde una
base nueva, tal como establece `plan.md`.

Los nombres de tablas y columnas están en inglés, conforme al código en inglés
de la constitución, y los valores persistidos de `movements.kind` conservan los
identificadores del dominio en español (`entrada_cerrada`, `entrada_suelta`,
`salida_cerrada`, `salida_suelta`, `apertura` y `compensacion`).

Representación persistida:

- Cantidades, factores, saldos y contadores FIFO: `INTEGER`.
- Precios y costos: `TEXT` con el texto canónico de un `Decimal` exacto, porque
  la especificación no define una precisión decimal y no se admiten `float`.
- Instantes: `TEXT` en UTC.
"""

import sqlite3

SCHEMA_SQL: str = """
-- Productos: identificación, unidad base y saldos sueltos (RF-01 y RF-02).
CREATE TABLE products (
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
);

-- Presentaciones: equivalencia en unidades base y precio de venta (RF-07).
CREATE TABLE presentations (
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
);

-- Movimientos del inventario, incluida la trazabilidad FIFO de las entradas
-- cerradas y los vínculos de las compensaciones (RF-02 y RF-03).
CREATE TABLE movements (
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
);

-- Propuestas del asistente: se registran sin modificar el inventario (RF-05).
CREATE TABLE proposals (
    id INTEGER PRIMARY KEY,
    action_type TEXT NOT NULL CHECK (trim(action_type) <> ''),
    data TEXT NOT NULL,
    status TEXT NOT NULL CHECK (trim(status) <> ''),
    created_at TEXT NOT NULL
);

-- Índices de los filtros de consulta de movimientos (RF-03).
CREATE INDEX idx_movements_product_id ON movements (product_id);
CREATE INDEX idx_movements_presentation_id ON movements (presentation_id);
CREATE INDEX idx_movements_occurred_at ON movements (occurred_at);

-- Una única presentación base (factor 1) por producto (RF-07). Refuerza en la
-- base la invariante que el dominio valida: es la defensa en profundidad de
-- T016 frente a altas y cambios de factor simultáneos.
CREATE UNIQUE INDEX idx_presentations_single_base
    ON presentations (product_id)
    WHERE factor = 1;
"""


def create_schema(connection: sqlite3.Connection) -> None:
    """
    Crea el esquema del inventario en la base de datos indicada.

    La creación es única y no hay migración de datos históricos: se espera una
    base nueva y vacía. Ejecutar la función sobre una base que ya contiene el
    esquema falla de forma explícita, porque las tablas ya existen.

    Args:
        connection: Conexión SQLite abierta sobre la base que se inicializa.

    Raises:
        sqlite3.Error:
            Si la base ya contiene el esquema o si alguna sentencia del
            esquema no puede ejecutarse.
    """
    connection.executescript(SCHEMA_SQL)
