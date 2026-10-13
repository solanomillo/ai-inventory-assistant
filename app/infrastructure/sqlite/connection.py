"""Conexión SQLite de la capa de infraestructura (T008).

Centraliza la apertura de conexiones para que la persistencia del proyecto use
siempre la misma configuración: acceso a las columnas por nombre, claves
foráneas activadas y control explícito de las transacciones.

En SQLite las claves foráneas se verifican solo si se activan en cada conexión;
por eso este módulo ejecuta `PRAGMA foreign_keys = ON` al abrir la base.
"""

import sqlite3
from pathlib import Path

IN_MEMORY_DATABASE: str = ":memory:"


def connect(
    database: str | Path = IN_MEMORY_DATABASE,
) -> sqlite3.Connection:
    """
    Abre una conexión SQLite configurada para la aplicación.

    La conexión se abre en modo autocommit (`isolation_level = None`) para que
    las transacciones se declaren de forma explícita: las confirmaciones del
    plan técnico se ejecutan con `BEGIN IMMEDIATE`, aplican los cambios y
    finalizan con `COMMIT` o `ROLLBACK`.

    Args:
        database: Ruta del archivo de base de datos o `:memory:` para una base
            en memoria (valor predeterminado).

    Returns:
        La conexión abierta, con acceso a las columnas por nombre y claves
        foráneas verificadas.

    Raises:
        sqlite3.Error:
            Si no se puede abrir la base de datos indicada.
    """
    connection = sqlite3.connect(str(database), isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection
