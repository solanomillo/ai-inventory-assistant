"""Conexión SQLite de la capa de infraestructura (T008 y T011).

Centraliza la apertura de conexiones para que la persistencia del proyecto use
siempre la misma configuración: acceso a las columnas por nombre, claves
foráneas activadas y control explícito de las transacciones.

En SQLite las claves foráneas se verifican solo si se activan en cada conexión;
por eso este módulo ejecuta `PRAGMA foreign_keys = ON` al abrir la base.

El helper `transaction` ejecuta la escritura de un caso de uso dentro de una
única transacción. Las conexiones se abren en modo autocommit
(`isolation_level = None`), de modo que la transacción se declara de forma
explícita y se confirma o se revierte de forma completa: un fallo a mitad de la
operación no deja escrituras parciales (T011). La transacción es la ordinaria
(`BEGIN`); la protección frente a operaciones simultáneas corresponde a T016 y
no se implementa aquí.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
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


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """
    Ejecuta un bloque dentro de una única transacción SQLite (T011).

    La transacción comienza con `BEGIN` y termina con `COMMIT` cuando el bloque
    finaliza sin errores. Ante cualquier excepción, se ejecuta `ROLLBACK` y la
    excepción se propaga, de modo que todas las escrituras del bloque se
    revierten juntas y no quedan cambios parciales.

    Es la transacción ordinaria del proyecto: no usa `BEGIN IMMEDIATE` ni
    implementa la protección frente a operaciones simultáneas, que
    corresponde a T016.

    Args:
        connection: Conexión SQLite abierta en modo autocommit, como la que
            devuelve `connect`.

    Yields:
        `None`; el bloque opera sobre la misma conexión recibida.

    Raises:
        sqlite3.Error:
            Si la transacción no puede iniciarse, confirmarse o revertirse.
    """
    connection.execute("BEGIN")
    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK")
        raise

    connection.execute("COMMIT")
