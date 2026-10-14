"""Conexión SQLite de la capa de infraestructura (T008, T011 y T016).

Centraliza la apertura de conexiones para que la persistencia del proyecto use
siempre la misma configuración: acceso a las columnas por nombre, claves
foráneas activadas y control explícito de las transacciones.

En SQLite las claves foráneas se verifican solo si se activan en cada conexión;
por eso este módulo ejecuta `PRAGMA foreign_keys = ON` al abrir la base.

El helper `transaction` ejecuta la escritura de un caso de uso dentro de una
única transacción. Las conexiones se abren en modo autocommit
(`isolation_level = None`), de modo que la transacción se declara de forma
explícita y se confirma o se revierte de forma completa: un fallo a mitad de la
operación no deja escrituras parciales (T011).

La transacción es `BEGIN IMMEDIATE` (T016): SQLite adquiere el bloqueo de
escritura al abrirla, antes de leer, así que las lecturas y las validaciones
del bloque se hacen sobre el estado vigente y dos operaciones simultáneas no
pueden confirmar sobre una disponibilidad obsoleta. La instantánea diferida
(`BEGIN`) solo bloquea al escribir y no protege la lectura previa. El helper
tampoco anida transacciones: una segunda llamada sobre una conexión con una
transacción ya abierta falla de forma explícita.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

IN_MEMORY_DATABASE: str = ":memory:"

DEFAULT_BUSY_TIMEOUT_SECONDS: float = 5.0
"""Espera máxima ante un bloqueo de la base, en segundos (T016)."""


def connect(
    database: str | Path = IN_MEMORY_DATABASE,
    *,
    timeout: float = DEFAULT_BUSY_TIMEOUT_SECONDS,
) -> sqlite3.Connection:
    """
    Abre una conexión SQLite configurada para la aplicación.

    La conexión se abre en modo autocommit (`isolation_level = None`) para que
    las transacciones se declaren de forma explícita: las confirmaciones del
    plan técnico se ejecutan con `BEGIN IMMEDIATE`, aplican los cambios y
    finalizan con `COMMIT` o `ROLLBACK`.

    `timeout` es el tiempo máximo, en segundos, que SQLite espera a que se
    libere un bloqueo antes de fallar con `sqlite3.OperationalError` («database
    is locked», código `SQLITE_BUSY`). No hay reintentos automáticos ni modo
    WAL: agotado el tiempo, el error de SQLite se propaga tal cual.

    Args:
        database: Ruta del archivo de base de datos o `:memory:` para una base
            en memoria (valor predeterminado).
        timeout: Tiempo máximo de espera ante un bloqueo, en segundos. El valor
            predeterminado es 5 segundos.

    Returns:
        La conexión abierta, con acceso a las columnas por nombre y claves
        foráneas verificadas.

    Raises:
        sqlite3.Error:
            Si no se puede abrir la base de datos indicada.
    """
    connection = sqlite3.connect(
        str(database),
        isolation_level=None,
        timeout=timeout,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """
    Ejecuta un bloque dentro de una única transacción SQLite (T011 y T016).

    La transacción comienza con `BEGIN IMMEDIATE`: SQLite toma el bloqueo de
    escritura al abrirla, de modo que las lecturas y las validaciones del
    bloque se hacen sobre el estado vigente y dos operaciones simultáneas no
    pueden confirmar sobre una disponibilidad obsoleta. Termina con `COMMIT`
    cuando el bloque finaliza sin errores. Ante cualquier excepción, se ejecuta
    `ROLLBACK` y la excepción se propaga, de modo que todas las escrituras del
    bloque se revierten juntas y no quedan cambios parciales.

    El helper no anida transacciones: si la conexión ya tiene una transacción
    abierta, `BEGIN IMMEDIATE` falla de forma explícita y no se abre una
    segunda. Al agotarse el `timeout` de la conexión por un bloqueo, el error
    de SQLite (`sqlite3.OperationalError`, `SQLITE_BUSY`) se propaga sin
    reintentos automáticos.

    Args:
        connection: Conexión SQLite abierta en modo autocommit, como la que
            devuelve `connect`.

    Yields:
        `None`; el bloque opera sobre la misma conexión recibida.

    Raises:
        sqlite3.Error:
            Si la transacción no puede iniciarse, confirmarse o revertirse, o
            si se agota el `timeout` de la conexión por un bloqueo.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK")
        raise

    connection.execute("COMMIT")
