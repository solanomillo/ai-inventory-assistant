"""Vista de la sección Inicio: alertas y conteos (T043, RF-09).

Presenta las alertas de stock bajo y de reposición y el conteo de productos
activos e inactivos. Los datos llegan ya consultados por `app/ui/main.py`, que
es el único punto que lee los servicios de la sesión con
`InventoryService.list_products()`, `get_low_stock_products()` y
`get_replenishment_products()` (RF-04, RF-09). Este módulo no consulta el
inventario, no importa los servicios, el dominio ni la persistencia, y no
escribe.

Límites de la vista: las reglas de negocio —la pertenencia de cada alerta, el
total equivalente y el faltante— las decide el dominio antes de llegar aquí; la
vista no las recalcula ni reordena las listas, y conserva el orden en que las
devuelve el servicio (D-3). Cada lista vacía informa su propio estado, con
independencia de la otra (D-2, C4), y un catálogo vacío se informa de forma
explícita sin presentar conteos cero (D-5). El formateo unificado de T042 queda
fuera de esta vista (D-6): los enteros se muestran tal cual. La presentación
usa solo componentes nativos —métricas en dos columnas y un contenedor con
borde por lista— sin CSS ni HTML (T057). La vinculación de C22 con sus pruebas
se completa en la trazabilidad de la Fase 7.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import streamlit as st

HOME_HEADER: str = "Inicio"
# Encabezado de la sección de inicio (T043).

ACTIVE_PRODUCTS_LABEL: str = "Productos activos"
# Etiqueta del conteo de productos activos (T043, C3).

INACTIVE_PRODUCTS_LABEL: str = "Productos inactivos"
# Etiqueta del conteo de productos inactivos (T043, C3).

EMPTY_CATALOG_MESSAGE: str = (
    "El catálogo no tiene productos registrados."
)
# Estado de un catálogo vacío: no se muestran conteos cero (T043, D-5, C4).

LOW_STOCK_HEADER: str = "Stock bajo"
# Encabezado de la lista de productos con stock bajo (T043, C3).

EMPTY_LOW_STOCK_MESSAGE: str = "No hay productos con stock bajo."
# Estado vacío de la lista de stock bajo, independiente de la otra (D-2, C4).

REPLENISHMENT_HEADER: str = "Requieren reposición"
# Encabezado de la lista de productos que requieren reposición (T043, C3).

EMPTY_REPLENISHMENT_MESSAGE: str = (
    "No hay productos que requieran reposición."
)
# Estado vacío de la lista de reposición, independiente de la otra (D-2, C4).


class ProductView(Protocol):
    """Contrato mínimo de un producto para esta vista (T043)."""

    name: str
    sku: str
    active: bool


class StockAlertView(Protocol):
    """Contrato mínimo de una alerta de inventario para esta vista (T043)."""

    product: ProductView
    total_equivalent: int
    shortage: int


def describe_stock_alert(alert: StockAlertView) -> str:
    """
    Describe una alerta con el producto, el total y el faltante (T043, C3).

    Los enteros se muestran tal cual, sin unificar formatos con otras vistas
    (D-6), y la alerta no se reinterpreta: los valores llegan derivados por el
    dominio.

    Args:
        alert: Alerta ya consultada por el servicio de inventario.

    Returns:
        El texto con el nombre y el SKU del producto, su total equivalente y
        su faltante.
    """
    return (
        f"{alert.product.name} (SKU {alert.product.sku}): "
        f"total equivalente {str(alert.total_equivalent)}, "
        f"faltante {str(alert.shortage)}"
    )


def _render_alert_list(
    header: str,
    empty_message: str,
    alerts: Sequence[StockAlertView],
) -> None:
    """
    Muestra una lista de alertas o su estado vacío (T043, C3, C4).

    El orden de las alertas es el que devolvió el servicio: la vista no las
    reordena (D-3). Una lista vacía muestra su propio mensaje, sin depender de
    que la otra lista tenga datos (D-2). La lista se agrupa en un contenedor
    con borde (T057), sin alterar su contenido.

    Args:
        header: Encabezado de la lista.
        empty_message: Mensaje explícito para la lista vacía.
        alerts: Alertas ya consultadas, en el orden del servicio.
    """
    with st.container(border=True):
        st.subheader(header)
        if not alerts:
            st.info(empty_message)
            return

        for alert in alerts:
            st.markdown(f"- {describe_stock_alert(alert)}")


def render_home(
    *,
    products: Sequence[ProductView],
    low_stock: Sequence[StockAlertView],
    replenishment: Sequence[StockAlertView],
) -> None:
    """
    Muestra las alertas y los conteos de la sección Inicio (T043, C3, C4).

    Los productos llegan de una única lectura pública del servicio de
    inventario y los conteos se derivan de esa misma lectura (D-4). Cuando el
    catálogo está vacío se informa explícitamente y no se muestran conteos cero
    (D-5). Los conteos se presentan como métricas nativas en dos columnas, con
    los valores enteros reales (T057). Las alertas se presentan con nombre,
    SKU, total equivalente y faltante (D-7), sin deduplicar los productos que
    aparecen en ambas listas y sin recalcular sus valores.

    Args:
        products: Productos del catálogo, activos e inactivos, en el orden del
            servicio.
        low_stock: Alertas de stock bajo, en el orden del servicio.
        replenishment: Alertas de reposición, en el orden del servicio.
    """
    st.header(HOME_HEADER)
    if not products:
        st.info(EMPTY_CATALOG_MESSAGE)
    else:
        active = sum(1 for product in products if product.active)
        inactive = len(products) - active
        active_column, inactive_column = st.columns(2)
        with active_column:
            st.metric(ACTIVE_PRODUCTS_LABEL, active)
        with inactive_column:
            st.metric(INACTIVE_PRODUCTS_LABEL, inactive)

    _render_alert_list(
        LOW_STOCK_HEADER,
        EMPTY_LOW_STOCK_MESSAGE,
        low_stock,
    )
    _render_alert_list(
        REPLENISHMENT_HEADER,
        EMPTY_REPLENISHMENT_MESSAGE,
        replenishment,
    )
