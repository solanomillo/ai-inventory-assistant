"""Sección Movimientos: filtros por producto, tipo y período (T046, RF-09).

Presenta el historial de movimientos de un producto con filtros de tipo y de
período local (C17). Los datos llegan ya consultados por `app/ui/main.py`, que
es el único punto que lee los servicios de la sesión con
`InventoryService.list_products()`, `get_movement_history()` y
`get_product_presentations()` (RF-03). Este módulo no consulta el inventario,
no importa los servicios, el dominio ni la persistencia, no abre conexiones
ni transacciones y no escribe.

Fuente de datos (C22): las lecturas públicas del servicio de inventario y el
período `[inicio, fin)` que la persistencia interpreta en la zona local
`America/Argentina/Buenos_Aires` cuando recibe límites sin zona horaria.

Límites de la vista:

- El producto es obligatorio: sin una selección no se consulta ningún
  historial y la interfaz no ofrece una lectura global de movimientos (D1).
- El selector presenta los productos ya consultados e incluye los inactivos,
  marcados para que sigan siendo consultables (D7).
- Los controles de período son «Desde» y «Hasta (incluido)»; el final técnico
  lo calcula `main.py` como la medianoche local del día siguiente, exclusiva,
  conservando la semántica `[inicio, fin)` (D2).
- Los tipos ofrecidos son los seis `kind` vigentes; «Todos los tipos» no
  filtra (D3). No se ofrecen acciones de escritura ni compensaciones nuevas: un
  movimiento de compensación histórico se consulta como cualquier otro (D8).
- Los resultados conservan el orden del servicio, sin reordenar ni deduplicar,
  y se muestran completos: el aviso C8 informa el número real (D6).
- Los estados son explícitos y diferenciados: catálogo vacío, sin selección,
  producto sin movimientos, filtros sin resultados, período inválido y error de
  lectura (C4, C21).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import streamlit as st

from app.ui.sections.product_detail import (
    MOVEMENT_KIND_LABELS,
    MOVEMENTS_NOTICE_TEMPLATE,
    NO_SELECTION_LABEL,
    MovementView,
    ProductView,
    describe_movement,
    movement_kind_label,
    non_paginated_notice,
    product_option_label,
)

MOVEMENTS_HEADER: str = "Movimientos"
# Encabezado de la sección de consulta de movimientos (T046, C17).

PRODUCT_FILTER_LABEL: str = "Producto"
# Etiqueta del selector obligatorio de producto (T046, D1).

PRODUCT_FILTER_KEY: str = "movements_product_selection"
# Clave propia del selector de producto: no comparte estado con el detalle
# (T046).

KIND_FILTER_LABEL: str = "Tipo"
# Etiqueta del selector de tipo de movimiento (T046, D3).

ALL_KINDS_LABEL: str = "Todos los tipos"
# Opción del selector que no filtra por tipo; es el valor por defecto (D3).

KIND_FILTER_KEY: str = "movements_kind_filter"
# Clave estable del selector de tipo de movimiento (T046).

START_DATE_LABEL: str = "Desde"
# Etiqueta del control del inicio del período, inclusivo (T046, D2).

END_DATE_LABEL: str = "Hasta (incluido)"
# Etiqueta del control del último día del período, inclusivo (T046, D2).

START_DATE_KEY: str = "movements_start_date"
# Clave estable del control de inicio del período (T046).

END_DATE_KEY: str = "movements_end_date"
# Clave estable del control de fin del período (T046).

EMPTY_CATALOG_MESSAGE: str = (
    "El catálogo no tiene productos registrados: no hay movimientos para "
    "consultar."
)
# Estado de un catálogo vacío, sin controles ni datos inventados (C4).

NO_SELECTION_MESSAGE: str = (
    "Elegí un producto para consultar sus movimientos."
)
# Estado explícito mientras no hay producto seleccionado (C4, D1).

INVALID_PERIOD_MESSAGE: str = (
    "La fecha «Desde» no puede ser posterior a la fecha «Hasta (incluido)»."
)
# Validación previa de un período invertido, sin consultar el servicio (D4).

EMPTY_MOVEMENTS_MESSAGE: str = (
    "El producto no tiene movimientos registrados."
)
# Estado de un producto sin movimientos y sin filtros activos (C4, D6).

EMPTY_FILTERED_MOVEMENTS_MESSAGE: str = (
    "Ningún movimiento coincide con los filtros seleccionados."
)
# Estado de una consulta con filtros que no devuelve resultados (C4, D6).

MOVEMENTS_ERROR_TEMPLATE: str = (
    "No se pudo leer el historial de movimientos: {reason}"
)
# Error explícito de una lectura fallida; nunca se presenta como vacío (C21).

UNKNOWN_PRESENTATION_TEMPLATE: str = "presentación #{presentation_id}"
# Referencia explícita cuando la presentación de un movimiento no se resuelve.

MOVEMENT_KIND_VALUES: tuple[str, ...] = tuple(MOVEMENT_KIND_LABELS)
# Valores persistidos de los seis tipos vigentes, en el orden de la vista.

KIND_VALUES_BY_LABEL: dict[str, str] = {
    movement_kind_label(value): value for value in MOVEMENT_KIND_VALUES
}
# Traducción inversa de la etiqueta visible al valor persistido del tipo.

KIND_FILTER_OPTIONS: tuple[str, ...] = (
    ALL_KINDS_LABEL,
    *KIND_VALUES_BY_LABEL,
)
# Opciones del selector: «Todos los tipos» y los seis tipos vigentes (D3).


@dataclass(frozen=True)
class MovementQuery:
    """
    Valores elegidos en los controles de la sección Movimientos (T046).

    `product_index` es la posición del producto dentro de la lista ya
    consultada, o `None` si se mantiene la opción centinela. `kind_value` es el
    valor persistido del tipo, o `None` cuando no se filtra. Las fechas son las
    elegidas en los controles; `None` indica que ese límite no se acotó.
    """

    product_index: int | None
    kind_value: str | None
    start_date: date | None
    end_date: date | None

    @property
    def has_filters(self) -> bool:
        """Indica si hay un filtro distinto de la selección de producto."""
        return (
            self.kind_value is not None
            or self.start_date is not None
            or self.end_date is not None
        )


def render_movements_header() -> None:
    """Muestra el encabezado de la sección de movimientos (T046)."""
    st.header(MOVEMENTS_HEADER)


def render_empty_catalog() -> None:
    """Muestra el estado de catálogo vacío, sin controles (T046, C4)."""
    st.info(EMPTY_CATALOG_MESSAGE)


def render_movement_controls(
    products: Sequence[ProductView],
) -> MovementQuery:
    """
    Muestra los filtros de producto, tipo y período (T046, D1–D3).

    El producto es obligatorio y se ofrece con la opción centinela; los
    productos inactivos se marcan y siguen siendo seleccionables. El tipo parte
    de «Todos los tipos» y las fechas no tienen valor inicial, de modo que una
    consulta sin período no acota el historial. Los widgets usan claves propias
    y no escriben directamente en el estado de la sesión.

    Args:
        products: Productos ya consultados, en el orden del servicio.

    Returns:
        Los valores elegidos, con `product_index = None` mientras no se elija
        un producto.
    """
    product_options = [
        NO_SELECTION_LABEL,
        *(product_option_label(product) for product in products),
    ]
    selected_product = st.selectbox(
        PRODUCT_FILTER_LABEL,
        product_options,
        index=0,
        key=PRODUCT_FILTER_KEY,
    )
    selected_kind = st.selectbox(
        KIND_FILTER_LABEL,
        KIND_FILTER_OPTIONS,
        index=0,
        key=KIND_FILTER_KEY,
    )
    start_value = st.date_input(
        START_DATE_LABEL,
        value=None,
        key=START_DATE_KEY,
    )
    end_value = st.date_input(
        END_DATE_LABEL,
        value=None,
        key=END_DATE_KEY,
    )

    product_index: int | None = None
    if selected_product != NO_SELECTION_LABEL:
        product_index = product_options.index(selected_product) - 1

    query = MovementQuery(
        product_index=product_index,
        kind_value=KIND_VALUES_BY_LABEL.get(selected_kind),
        start_date=_date_or_none(start_value),
        end_date=_date_or_none(end_value),
    )
    if query.product_index is None:
        st.info(NO_SELECTION_MESSAGE)

    return query


def render_invalid_period() -> None:
    """Muestra la validación de un período invertido (T046, D4)."""
    st.warning(INVALID_PERIOD_MESSAGE)


def render_empty_movements() -> None:
    """Muestra el vacío de un producto sin movimientos (T046, D6)."""
    st.info(EMPTY_MOVEMENTS_MESSAGE)


def render_empty_filtered_movements() -> None:
    """Muestra el vacío de una consulta con filtros (T046, D6)."""
    st.info(EMPTY_FILTERED_MOVEMENTS_MESSAGE)


def render_movements(
    movements: Sequence[MovementView],
    presentation_names: Mapping[int, str],
) -> None:
    """
    Muestra los movimientos en el orden del servicio, sin truncar (T046, D6).

    Cada movimiento se describe con `describe_movement`, que aplica los
    formatos locales de fecha, importes y cantidades y omite los campos
    opcionales ausentes. El nombre de la presentación se resuelve con el mapa
    ya consultado; si no se encuentra, se informa su identificador sin inventar
    un nombre. El aviso C8 incluye el número real de movimientos.

    Args:
        movements: Movimientos devueltos por el servicio, en su orden.
        presentation_names: Nombres de las presentaciones indexados por su
            identificador.
    """
    st.info(
        non_paginated_notice(MOVEMENTS_NOTICE_TEMPLATE, len(movements))
    )
    for movement in movements:
        presentation_name = presentation_names.get(
            movement.presentation_id
        )
        if presentation_name is None:
            presentation_name = UNKNOWN_PRESENTATION_TEMPLATE.format(
                presentation_id=movement.presentation_id
            )
        st.text(
            describe_movement(
                movement,
                _single_line(presentation_name),
            )
        )


def render_movements_error(message: str) -> None:
    """
    Muestra el error explícito de una lectura de movimientos (T046, C21).

    Args:
        message: Motivo devuelto por el servicio. Se presenta como error y no
            como un historial vacío.
    """
    st.error(MOVEMENTS_ERROR_TEMPLATE.format(reason=message))


def _date_or_none(value: object) -> date | None:
    """Devuelve la fecha elegida o `None` si el control no tiene valor."""
    return value if isinstance(value, date) else None


def _single_line(value: str) -> str:
    """
    Normaliza los saltos de línea de un nombre solo para mostrarlo.

    El valor de negocio no se modifica: la unión se aplica a una copia usada
    únicamente para dibujar la vista.
    """
    return " ".join(value.splitlines())
