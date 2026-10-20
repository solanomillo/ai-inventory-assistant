"""Vista de la sección Productos: listado, búsqueda y filtro (T044, RF-09).

Presenta el catálogo de productos con sus siete campos de C5 —SKU, nombre,
unidad base, stock mínimo, unidades sueltas, stock cerrado y estado— y ofrece
los controles de búsqueda y de filtro de estado. Los datos llegan ya
consultados por `app/ui/main.py`, que es el único punto que lee los servicios
de la sesión con `InventoryService.list_products()`,
`find_products_by_name()`, `find_product_by_sku()` y
`get_product_presentations()` (RF-01, RF-09). Este módulo no consulta el
inventario, no importa los servicios, el dominio, la persistencia ni el punto
de entrada, no abre conexiones ni transacciones y no escribe.

Límites de la vista (G1): no usa el estado de la sesión de Streamlit; los
controles son widgets con claves estables y devuelven su valor en
`ProductQuery`, de modo que la composición conserve el estado. La búsqueda la
resuelve `main.py` con las lecturas existentes del servicio: la coincidencia es
exacta y no distingue mayúsculas de minúsculas, y una entrada vacía o solo con
espacios no invoca los métodos de búsqueda (B1). El filtro de estado es de
presentación y se aplica después de la búsqueda (C1). El stock cerrado llega ya
sumado por `main.py` como la suma de `closed_stock` de las presentaciones del
producto (A1); la vista no lo recalcula. El listado no se pagina: se muestra el
número real de productos presentados (D1), y cada fila se muestra en una tabla
nativa (`st.dataframe`) con las siete columnas de C5, que no interpreta
Markdown y conserva los valores de negocio tal como llegan (F1, T057). Los
controles y el listado se agrupan en contenedores con borde, y el aviso de no
paginación se conserva. La vinculación de C22 con sus pruebas se completa en la
trazabilidad de la Fase 7.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import streamlit as st

PRODUCTS_HEADER: str = "Productos"
# Encabezado de la sección de productos (T044, C5).

SEARCH_LABEL: str = "Buscar producto"
# Etiqueta del campo de texto de búsqueda (T044, B1).

SEARCH_FIELD_LABEL: str = "Buscar por"
# Etiqueta del selector del campo de búsqueda (T044, B1).

NAME_FIELD: str = "Nombre"
# Campo de búsqueda por nombre, elegido por defecto (B1, C6).

SKU_FIELD: str = "SKU"
# Campo de búsqueda por SKU (B1, C6).

SEARCH_FIELDS: tuple[str, ...] = (NAME_FIELD, SKU_FIELD)
# Campos de búsqueda admitidos, con «Nombre» primero (B1).

STATUS_FILTER_LABEL: str = "Estado"
# Etiqueta del selector de filtro de estado (T044, C1).

ALL_STATUS_FILTER: str = "Todos"
# Filtro que incluye productos activos e inactivos; es el valor por defecto
# (C1).

ACTIVE_STATUS_FILTER: str = "Activos"
# Filtro que conserva únicamente los productos activos (C1).

INACTIVE_STATUS_FILTER: str = "Inactivos"
# Filtro que conserva únicamente los productos inactivos (C1).

STATUS_FILTERS: tuple[str, ...] = (
    ALL_STATUS_FILTER,
    ACTIVE_STATUS_FILTER,
    INACTIVE_STATUS_FILTER,
)
# Filtros de estado admitidos, con «Todos» primero (C1).

SEARCH_TEXT_KEY: str = "products_search_text"
# Clave estable del campo de texto de búsqueda (E1).

SEARCH_FIELD_KEY: str = "products_search_field"
# Clave estable del selector del campo de búsqueda (E1).

STATUS_FILTER_KEY: str = "products_status_filter"
# Clave estable del selector de filtro de estado (E1).

SEARCH_NOTICE: str = (
    "La búsqueda es exacta y no distingue mayúsculas de minúsculas."
)
# Advertencia sobre la semántica real de la búsqueda (C6).

EMPTY_CATALOG_MESSAGE: str = (
    "El catálogo no tiene productos registrados."
)
# Estado de un catálogo vacío, sin inventar valores (C4).

EMPTY_SEARCH_MESSAGE: str = (
    "La búsqueda no encontró coincidencias exactas."
)
# Estado de una búsqueda sin resultados (C4, C6).

EMPTY_FILTER_MESSAGE: str = (
    "Ningún producto coincide con el filtro de estado seleccionado."
)
# Estado de un filtro que excluye todos los productos disponibles (C1, C4).

NON_PAGINATED_NOTICE_TEMPLATE: str = (
    "El listado no se pagina: se muestran los {count} productos."
)
# Aviso del listado completo con su número real de productos (C8, D1).

SKU_LABEL: str = "SKU"
# Etiqueta del campo SKU de cada fila (C5).

NAME_LABEL: str = "Nombre"
# Etiqueta del campo nombre de cada fila (C5).

BASE_UNIT_LABEL: str = "Unidad base"
# Etiqueta del campo unidad base de cada fila (C5).

MINIMUM_STOCK_LABEL: str = "Stock mínimo"
# Etiqueta del campo stock mínimo de cada fila (C5).

LOOSE_UNITS_LABEL: str = "Unidades sueltas"
# Etiqueta del campo unidades sueltas de cada fila (C5).

CLOSED_STOCK_LABEL: str = "Stock cerrado"
# Etiqueta del campo stock cerrado de cada fila (C5, A1).

STATUS_LABEL: str = "Estado"
# Etiqueta del campo estado de cada fila (C5).

ACTIVE_STATUS: str = "Activo"
# Texto del estado de un producto activo (C5).

INACTIVE_STATUS: str = "Inactivo"
# Texto del estado de un producto inactivo (C5).


@dataclass(frozen=True)
class ProductQuery:
    """
    Valores elegidos en los controles de la sección Productos (T044, B1, C1).

    `text` es el texto tal como se escribió; `main.py` decide si está vacío o
    solo tiene espacios antes de invocar los métodos de búsqueda. `field` es
    uno de `SEARCH_FIELDS` y `status` uno de `STATUS_FILTERS`.
    """

    text: str
    field: str
    status: str

    @property
    def search_text(self) -> str:
        """Devuelve el texto de búsqueda recortado (B1)."""
        return self.text.strip()

    @property
    def is_search(self) -> bool:
        """Indica si hay un texto de búsqueda efectivo (B1)."""
        return bool(self.search_text)


@dataclass(frozen=True)
class ProductRow:
    """
    Fila de producto que la vista presenta (T044, C5).

    Reúne los siete campos de C5 que llegan ya consultados desde `main.py`. El
    stock cerrado es la suma de `closed_stock` de las presentaciones del
    producto (A1), no su equivalente en unidades base.
    """

    sku: str
    name: str
    base_unit: str
    minimum_stock: int
    loose_units: int
    closed_stock: int
    active: bool

    @property
    def status(self) -> str:
        """Devuelve el texto de estado real del producto (C5)."""
        return ACTIVE_STATUS if self.active else INACTIVE_STATUS


def non_paginated_notice(count: int) -> str:
    """
    Construye el aviso del listado completo con su número real (C8, D1).

    Args:
        count: Cantidad real de productos presentados.

    Returns:
        El aviso de que el listado no se pagina y cuántos productos muestra.
    """
    return NON_PAGINATED_NOTICE_TEMPLATE.format(count=count)


def render_product_controls() -> ProductQuery:
    """
    Muestra los controles de búsqueda y filtro y devuelve sus valores (B1, C1).

    El campo de búsqueda es texto libre con «Nombre» por defecto y el filtro de
    estado es «Todos» por defecto. Los widgets tienen claves estables y no
    escriben en el estado de la sesión directamente (E1, G1), y se agrupan en
    un contenedor con borde (T057).

    Returns:
        Los valores elegidos por el usuario, sin recortar el texto.
    """
    with st.container(border=True):
        st.caption(SEARCH_NOTICE)
        text = st.text_input(SEARCH_LABEL, key=SEARCH_TEXT_KEY)
        field = st.selectbox(
            SEARCH_FIELD_LABEL,
            SEARCH_FIELDS,
            index=SEARCH_FIELDS.index(NAME_FIELD),
            key=SEARCH_FIELD_KEY,
        )
        status = st.selectbox(
            STATUS_FILTER_LABEL,
            STATUS_FILTERS,
            index=STATUS_FILTERS.index(ALL_STATUS_FILTER),
            key=STATUS_FILTER_KEY,
        )

    return ProductQuery(text=text, field=field, status=status)


def product_record(row: ProductRow) -> dict[str, str | int]:
    """
    Compone el registro de una fila con los siete campos de C5 (T044, T057).

    El registro conserva los valores tal como llegan —sin normalizar los saltos
    de línea ni interpretar Markdown— y usa las etiquetas de C5 como nombres de
    columna del listado.

    Args:
        row: Producto ya consultado y con su stock cerrado sumado.

    Returns:
        El registro de la fila, con las siete columnas en el orden de C5.
    """
    return {
        SKU_LABEL: row.sku,
        NAME_LABEL: row.name,
        BASE_UNIT_LABEL: row.base_unit,
        MINIMUM_STOCK_LABEL: row.minimum_stock,
        LOOSE_UNITS_LABEL: row.loose_units,
        CLOSED_STOCK_LABEL: row.closed_stock,
        STATUS_LABEL: row.status,
    }


def render_products(
    rows: Sequence[ProductRow],
    *,
    empty_message: str | None = None,
) -> None:
    """
    Muestra el listado de productos o el estado vacío correspondiente (T044).

    El listado conserva el orden recibido de `main.py` —que a su vez preserva
    el orden del servicio (D1)— y no se pagina ni se trunca: el aviso informa
    el número real de productos presentados (C8). El listado se muestra en un
    `st.dataframe` con las siete columnas de C5, que no interpreta Markdown y
    conserva los valores reales de cada fila (F1, T057). El aviso y el listado
    se agrupan en un contenedor con borde.

    Args:
        rows: Productos ya filtrados y con sus siete campos consultados, en el
            orden del servicio.
        empty_message: Mensaje explícito para un listado vacío, o `None` si no
            corresponde mostrarlo.
    """
    st.header(PRODUCTS_HEADER)
    with st.container(border=True):
        if not rows:
            if empty_message is not None:
                st.info(empty_message)
            return

        st.info(non_paginated_notice(len(rows)))
        st.dataframe(
            [product_record(row) for row in rows],
            hide_index=True,
            width="stretch",
        )
