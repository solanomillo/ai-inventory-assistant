"""Sección Historial de precios: consulta por producto y presentación (T047).

Presenta el historial de cambios de precio de un producto y, cuando se elige
una presentación concreta, solo el de esa presentación (C19). Los datos llegan
ya consultados por `app/ui/main.py`, que es el único punto que lee los
servicios de la sesión con `InventoryService.list_products()`,
`InventoryService.get_product_presentations()`,
`PriceHistoryService.get_price_history_by_product()` y
`PriceHistoryService.get_price_history_by_presentation()` (RF-08). Este módulo
no consulta el inventario, no importa los servicios, el dominio ni la
persistencia, no abre conexiones ni transacciones y no escribe.

Fuente de datos (C22): las lecturas públicas del servicio de inventario —el
catálogo completo, que incluye los productos inactivos, y las presentaciones
del producto— y el historial del servicio de precios, cuyo orden cronológico
aporta la persistencia y la vista conserva sin reordenar.

Límites de la vista:

- El producto es obligatorio: sin una selección no se consulta ningún
  historial (D1).
- «Todas las presentaciones» es la opción por defecto del selector de
  presentación; una presentación concreta consulta solo su historial (D2).
- Los productos inactivos siguen siendo consultables y se marcan en el
  selector (D3).
- El tipo de evento se muestra tal como está almacenado, sin traducciones
  inventadas (D4).
- Cada cambio se describe con `describe_price_change`, reutilizado del detalle
  de producto, y el nombre de la presentación se resuelve con el mapa ya
  consultado (D5).
- El historial no se pagina: el aviso C8 informa el número real de cambios sin
  truncarlos (D6).
- Los estados vacíos son explícitos y diferenciados: catálogo vacío, sin
  selección, producto sin cambios y presentación sin cambios (C4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import streamlit as st

from app.ui.sections.product_detail import (
    NO_SELECTION_LABEL,
    PriceChangeView,
    PresentationView,
    ProductView,
    describe_price_change,
    non_paginated_notice,
    product_option_label,
)

PRICE_HISTORY_SECTION_HEADER: str = "Historial de precios"
# Encabezado de la sección de consulta del historial de precios (T047, C19).

PRODUCT_FILTER_LABEL: str = "Producto"
# Etiqueta del selector obligatorio de producto (T047, D1).

PRODUCT_FILTER_KEY: str = "price_history_product_selection"
# Clave propia del selector de producto: no comparte estado con otras vistas.

PRESENTATION_FILTER_LABEL: str = "Presentación"
# Etiqueta del selector que acota el historial a una presentación (T047, D2).

ALL_PRESENTATIONS_LABEL: str = "Todas las presentaciones"
# Opción del selector que consulta el historial de todo el producto (D2).

PRESENTATION_FILTER_KEY_TEMPLATE: str = (
    "price_history_presentation_selection_{product_id}"
)
# Clave propia del selector de presentación, distinta por producto para no
# conservar una presentación de otro producto al cambiar la selección (D2).

EMPTY_CATALOG_MESSAGE: str = (
    "El catálogo no tiene productos registrados: no hay historial de precios "
    "para consultar."
)
# Estado de un catálogo vacío, sin controles ni datos inventados (C4).

NO_SELECTION_MESSAGE: str = (
    "Elegí un producto para consultar su historial de precios."
)
# Estado explícito mientras no hay producto seleccionado (C4, D1).

EMPTY_PRODUCT_HISTORY_MESSAGE: str = (
    "El producto no tiene cambios de precio registrados."
)
# Estado de un producto sin cambios en todas sus presentaciones (C4, D6).

EMPTY_PRESENTATION_HISTORY_MESSAGE: str = (
    "La presentación no tiene cambios de precio registrados."
)
# Estado de una presentación concreta sin cambios (C4, D2).

PRICE_HISTORY_NOTICE_TEMPLATE: str = (
    "El historial no se pagina: se muestran {count} cambios."
)
# Aviso de no paginación con el número real de cambios (C8, D6).

PRICE_HISTORY_ERROR_TEMPLATE: str = (
    "No se pudo leer el historial de precios: {reason}"
)
# Error explícito de una lectura fallida; nunca se presenta como vacío (C21).

UNKNOWN_PRESENTATION_TEMPLATE: str = "presentación #{presentation_id}"
# Referencia explícita cuando la presentación de un cambio no se resuelve.


def render_price_history_header() -> None:
    """Muestra el encabezado de la sección de historial de precios (T047)."""
    st.header(PRICE_HISTORY_SECTION_HEADER)


def render_empty_catalog() -> None:
    """Muestra el estado de catálogo vacío, sin controles (T047, C4)."""
    st.info(EMPTY_CATALOG_MESSAGE)


def render_product_filter(products: Sequence[ProductView]) -> int | None:
    """
    Muestra el selector obligatorio de producto (T047, D1, D3).

    La opción centinela permite no seleccionar ningún producto. Los productos
    inactivos se ofrecen marcados para que sigan siendo consultables. El
    selector solo presenta los productos ya consultados por `main.py`; no lee
    servicios y no escribe en el estado de la sesión de Streamlit.

    Args:
        products: Productos ya consultados, en el orden del servicio.

    Returns:
        La posición del producto elegido dentro de `products`, o `None` si se
        mantiene la opción centinela.
    """
    options = [
        NO_SELECTION_LABEL,
        *(product_option_label(product) for product in products),
    ]
    selected = st.selectbox(
        PRODUCT_FILTER_LABEL,
        options,
        index=0,
        key=PRODUCT_FILTER_KEY,
    )
    if selected == NO_SELECTION_LABEL:
        st.info(NO_SELECTION_MESSAGE)
        return None

    return options.index(selected) - 1


def render_presentation_filter(
    presentations: Sequence[PresentationView],
    *,
    product_id: int,
) -> int | None:
    """
    Muestra el selector de presentación del producto (T047, D2).

    «Todas las presentaciones» es la opción por defecto y no filtra. Una
    presentación concreta acota la consulta a su propio historial. El selector
    solo presenta las presentaciones ya consultadas por `main.py` y usa una
    clave propia por producto.

    Args:
        presentations: Presentaciones del producto, en el orden del servicio.
        product_id: Producto cuyas presentaciones se ofrecen, para construir la
            clave del widget.

    Returns:
        La posición de la presentación elegida dentro de `presentations`, o
        `None` si se consulta el historial de todo el producto.
    """
    options = [
        ALL_PRESENTATIONS_LABEL,
        *(_single_line(presentation.name) for presentation in presentations),
    ]
    selected = st.selectbox(
        PRESENTATION_FILTER_LABEL,
        options,
        index=0,
        key=PRESENTATION_FILTER_KEY_TEMPLATE.format(product_id=product_id),
    )
    if selected == ALL_PRESENTATIONS_LABEL:
        return None

    return options.index(selected) - 1


def render_empty_product_history() -> None:
    """Muestra el vacío de un producto sin cambios de precio (T047, C4)."""
    st.info(EMPTY_PRODUCT_HISTORY_MESSAGE)


def render_empty_presentation_history() -> None:
    """Muestra el vacío de una presentación sin cambios (T047, C4)."""
    st.info(EMPTY_PRESENTATION_HISTORY_MESSAGE)


def render_price_history(
    changes: Sequence[PriceChangeView],
    presentation_names: Mapping[int, str],
) -> None:
    """
    Muestra el historial en el orden del servicio, sin truncar (T047, C8).

    Cada cambio se describe con `describe_price_change`, que aplica los
    formatos locales de fecha e importes. El nombre de la presentación se
    resuelve con el mapa ya consultado; si no se encuentra, se informa su
    identificador sin inventar un nombre. El aviso C8 incluye el número real de
    cambios.

    Args:
        changes: Cambios de precio devueltos por el servicio, en su orden.
        presentation_names: Nombres de las presentaciones indexados por su
            identificador.
    """
    st.info(non_paginated_notice(PRICE_HISTORY_NOTICE_TEMPLATE, len(changes)))
    for change in changes:
        presentation_name = presentation_names.get(change.presentation_id)
        if presentation_name is None:
            presentation_name = UNKNOWN_PRESENTATION_TEMPLATE.format(
                presentation_id=change.presentation_id
            )
        st.text(
            describe_price_change(
                change,
                _single_line(presentation_name),
            )
        )


def render_price_history_error(message: str) -> None:
    """
    Muestra el error explícito de una lectura del historial (T047, C21).

    Args:
        message: Motivo devuelto por el servicio. Se presenta como error y no
            como un historial vacío.
    """
    st.error(PRICE_HISTORY_ERROR_TEMPLATE.format(reason=message))


def _single_line(value: str) -> str:
    """
    Normaliza los saltos de línea de un nombre solo para mostrarlo.

    El valor de negocio no se modifica: la unión se aplica a una copia usada
    únicamente para dibujar la vista.
    """
    return " ".join(value.splitlines())
