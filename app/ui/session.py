"""Contenedor de la sesión de lectura del inventario (T039, RF-09).

Separa la composición de la **lectura** del inventario de la inicialización del
modelo: reúne la conexión SQLite, los repositorios y los servicios de consulta
en `SessionResources` —una conexión y un conjunto de servicios por sesión
(D-G)— sin invocar `get_llm()`, de modo que una sección de consulta pueda leer
datos reales aunque el modelo no esté configurado.

La composición del **asistente** —el grafo Supervisor → Inventory Agent— vive
en `app/ui/main.py` y solo se ejecuta cuando hay modelo: este módulo no importa
el modelo, el grafo ni sus herramientas, y no depende del runtime de Streamlit,
de modo que sus funciones puedan probarse con una conexión en memoria.

La lectura no escribe: este módulo no abre transacciones de escritura y el
único punto de escritura de la interfaz sigue siendo la confirmación explícita
del usuario, que ejecuta `ProposalService.confirm` (RF-05, RF-08).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from functools import partial

from app.config import resolve_database_path
from app.infrastructure.sqlite.connection import connect, transaction
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqlitePriceHistoryRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.services.inventory import InventoryService
from app.services.movements import MovementService
from app.services.presentations import PresentationService
from app.services.price_history import PriceHistoryService
from app.services.products import ProductService
from app.services.proposals import ProposalService

SESSION_KEY: str = "inventory_session"
# Clave de `st.session_state` donde vive la lectura de la sesión (D-G).


@dataclass
class SessionResources:
    """
    Recursos de lectura de una sesión de la interfaz (T039, D-G).

    Agrupa la conexión SQLite de la sesión y los servicios que consumen las
    secciones de consulta: las lecturas del inventario (existencias, alertas e
    historial de movimientos), la lectura de compensabilidad de los movimientos
    (T054), la lectura de presentaciones y del historial de precios, y el
    servicio de propuestas que ejecutan las confirmaciones del usuario. Vive en
    `st.session_state`, no se serializa y no se persiste.
    """

    connection: sqlite3.Connection
    inventory_service: InventoryService
    movement_service: MovementService
    presentation_service: PresentationService
    price_history_service: PriceHistoryService
    proposal_service: ProposalService


def open_connection() -> sqlite3.Connection:
    """
    Abre la base durable del inventario y garantiza su esquema (T028).

    La ruta se resuelve con `resolve_database_path()` —configurable con
    `INVENTORY_DB_PATH` y, por defecto, un archivo del proyecto independiente
    del directorio de trabajo— y se crea su directorio padre si hace falta. Las
    pruebas inyectan su propia conexión en memoria.

    Returns:
        La conexión abierta con el esquema vigente.
    """
    connection = connect(resolve_database_path())
    create_schema(connection)

    return connection


def build_session(
    *,
    connection: sqlite3.Connection | None = None,
) -> SessionResources:
    """
    Compone la lectura de una sesión sin resolver el modelo (T039, D-G).

    Sobre la conexión —la inyectada o, sin ella, la base durable del proyecto—
    compone los repositorios SQLite, la transacción compartida y los servicios
    de consulta que consumen las secciones: las lecturas del inventario, la
    compensabilidad de los movimientos, las presentaciones, el historial de
    precios y las propuestas. No invoca `get_llm()` ni compone el grafo: la
    lectura funciona sin modelo.

    Args:
        connection: Conexión SQLite ya abierta, o `None` para abrir la conexión
            del MVP con su esquema.

    Returns:
        El contenedor con la conexión de la sesión y sus servicios de lectura.
    """
    resolved_connection = (
        open_connection() if connection is None else connection
    )
    scope = partial(transaction, resolved_connection)
    products = SqliteProductRepository(resolved_connection)
    presentations = SqlitePresentationRepository(resolved_connection)
    movements = SqliteMovementRepository(resolved_connection)
    price_history = SqlitePriceHistoryRepository(resolved_connection)

    product_service = ProductService(
        products=products,
        presentations=presentations,
        movements=movements,
        transaction=scope,
    )
    presentation_service = PresentationService(
        products=products,
        presentations=presentations,
        movements=movements,
        price_history=price_history,
        transaction=scope,
    )
    movement_service = MovementService(
        products=products,
        presentations=presentations,
        movements=movements,
        transaction=scope,
    )
    inventory_service = InventoryService(
        products=products,
        presentations=presentations,
        movements=movements,
    )
    price_history_service = PriceHistoryService(
        products=products,
        presentations=presentations,
        price_history=price_history,
    )
    proposal_service = ProposalService(
        products=product_service,
        presentations=presentation_service,
        movements=movement_service,
    )

    return SessionResources(
        connection=resolved_connection,
        inventory_service=inventory_service,
        movement_service=movement_service,
        presentation_service=presentation_service,
        price_history_service=price_history_service,
        proposal_service=proposal_service,
    )
