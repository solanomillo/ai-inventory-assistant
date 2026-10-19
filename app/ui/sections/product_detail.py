"""Vista del detalle de un producto (T045, RF-09).

Presenta el detalle de un producto seleccionado con sus presentaciones —factor
y stock cerrado—, sus unidades sueltas y su total equivalente, sus movimientos
y su historial de precios (C9). Los datos llegan ya consultados por
`app/ui/main.py`, que es el único punto que lee los servicios de la sesión con
`InventoryService.get_product_stock()`, `get_movement_history()` y
`PriceHistoryService.get_price_history_by_product()` (RF-02, RF-03, RF-08).
Este módulo no consulta el inventario, no importa los servicios, el dominio ni
la persistencia, no abre conexiones ni transacciones y no escribe.

Límites de la vista:

- El selector marca los productos inactivos para que sigan siendo
  consultables; el detalle también muestra su estado real (RF-01, C9).
- Las cantidades y los importes se muestran con `app.ui.formatting`: el cero
  real se conserva como cero y un valor ausente nunca se presenta como un
  dato real (C4).
- El stock cerrado de cada presentación se informa por separado; el total
  equivalente llega derivado por el servicio y no se recalcula.
- Los campos opcionales de un movimiento —costo y precio aplicado— se muestran
  solo cuando existen, y los contadores FIFO no forman parte de la vista.
- Los listados no se paginan: cada uno informa su número real de registros sin
  truncarlos (C8).
- Un error de lectura se presenta como error explícito y nunca como un
  historial vacío (C4, C21).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Protocol

import streamlit as st

from app.ui.formatting import format_instant, format_money, format_quantity
from app.ui.sections.products import (
    ACTIVE_STATUS,
    BASE_UNIT_LABEL,
    CLOSED_STOCK_LABEL,
    INACTIVE_STATUS,
    LOOSE_UNITS_LABEL,
    MINIMUM_STOCK_LABEL,
    NAME_LABEL,
    SKU_LABEL,
    STATUS_LABEL,
)

PRODUCT_DETAIL_HEADER: str = "Detalle de producto"
# Encabezado de la vista del detalle (T045, C9).

PRODUCT_SELECTOR_LABEL: str = "Seleccionar producto"
# Etiqueta del selector del producto cuyo detalle se muestra (T045).

NO_SELECTION_LABEL: str = "Elegí un producto"
# Opción centinela del selector: no hay producto seleccionado (T045).

PRODUCT_SELECTOR_KEY: str = "product_detail_selection"
# Clave estable del selector en el estado de la sesión (T045).

PRODUCT_OPTION_TEMPLATE: str = "{sku} — {name}"
# Texto de la opción de un producto; el SKU la hace única (T045).

INACTIVE_OPTION_MARKER: str = " (inactivo)"
# Marca que distingue un producto inactivo en el selector (T045).

NO_PRODUCT_SELECTED_MESSAGE: str = (
    "Elegí un producto para ver sus presentaciones, sus movimientos y su "
    "historial de precios."
)
# Estado explícito mientras no hay un producto seleccionado (T045, C4).

PRODUCT_DETAIL_ERROR_TEMPLATE: str = (
    "No se pudo leer el detalle del producto: {reason}"
)
# Error explícito de una lectura fallida; nunca se presenta como vacío (C21).

PRESENTATIONS_HEADER: str = "Presentaciones"
# Encabezado de las presentaciones del producto (T045, C9).

MOVEMENTS_HEADER: str = "Movimientos"
# Encabezado de los movimientos del producto (T045, C9).

PRICE_HISTORY_HEADER: str = "Historial de precios"
# Encabezado del historial de precios del producto (T045, C9, C19).

EMPTY_PRESENTATIONS_MESSAGE: str = (
    "El producto no tiene presentaciones registradas."
)
# Estado vacío propio de las presentaciones (T045, C4).

EMPTY_MOVEMENTS_MESSAGE: str = (
    "El producto no tiene movimientos registrados."
)
# Estado vacío propio de los movimientos (T045, C4).

EMPTY_PRICE_HISTORY_MESSAGE: str = (
    "El producto no tiene cambios de precio registrados."
)
# Estado vacío propio del historial de precios (T045, C4).

PRESENTATIONS_NOTICE_TEMPLATE: str = (
    "Las presentaciones no se paginan: se muestran las {count} "
    "presentaciones."
)
# Aviso de no paginación con el número real de presentaciones (C8).

MOVEMENTS_NOTICE_TEMPLATE: str = (
    "Los movimientos no se paginan: se muestran los {count} movimientos."
)
# Aviso de no paginación con el número real de movimientos (C8).

PRICE_HISTORY_NOTICE_TEMPLATE: str = (
    "El historial de precios no se pagina: se muestran los {count} cambios."
)
# Aviso de no paginación con el número real de cambios de precio (C8).

TOTAL_EQUIVALENT_LABEL: str = "Total equivalente"
# Etiqueta del total equivalente derivado por el servicio (T045, D4).

PRESENTATION_LABEL: str = "Presentación"
# Etiqueta de la presentación afectada o de la fila presentada (T045).

FACTOR_LABEL: str = "Factor"
# Etiqueta del factor de conversión de una presentación (T045, D3).

BASE_PRESENTATION_LABEL: str = "Presentación base"
# Etiqueta que identifica la presentación base de factor 1 (T045, D3).

YES_TEXT: str = "sí"
# Valor afirmativo de los indicadores de la vista (T045, D5).

MOVEMENT_TYPE_LABEL: str = "Tipo"
# Etiqueta del tipo traducido de un movimiento (T045, D5).

QUANTITY_LABEL: str = "Cantidad"
# Etiqueta de la cantidad de un movimiento (T045, D5).

REASON_LABEL: str = "Motivo"
# Etiqueta del motivo de un movimiento (T045, D5).

DATE_LABEL: str = "Fecha"
# Etiqueta de la fecha local de un movimiento o de un cambio de precio (D5).

COST_LABEL: str = "Costo"
# Etiqueta del costo de ingreso, visible solo si existe (T045, D5).

APPLIED_PRICE_LABEL: str = "Precio aplicado"
# Etiqueta del precio aplicado, visible solo si existe (T045, D5).

INITIAL_STOCK_LABEL: str = "Stock inicial"
# Etiqueta del indicador de stock inicial de un movimiento (T045, D5).

PRICE_PREVIOUS_LABEL: str = "Precio anterior"
# Etiqueta del precio anterior de un cambio de precio (T045, D6).

PRICE_NEW_LABEL: str = "Precio nuevo"
# Etiqueta del precio nuevo de un cambio de precio (T045, D6).

EVENT_LABEL: str = "Evento"
# Etiqueta del tipo de evento del historial de precios (T045, D6).

UNKNOWN_PRESENTATION_TEMPLATE: str = "presentación #{presentation_id}"
# Referencia explícita cuando la presentación de un registro no se resuelve.

MOVEMENT_KIND_LABELS: dict[str, str] = {
    "entrada_cerrada": "Entrada cerrada",
    "entrada_suelta": "Entrada suelta",
    "salida_cerrada": "Salida cerrada",
    "salida_suelta": "Salida suelta",
    "apertura": "Apertura",
    "compensacion": "Compensación",
}
# Traducción de los tipos de movimiento vigentes (RF-03, D5).


class ProductView(Protocol):
    """Contrato mínimo de un producto para esta vista (T045)."""

    id: int
    sku: str
    name: str
    base_unit: str
    minimum_stock: int
    loose_units: int
    active: bool


class PresentationView(Protocol):
    """Contrato mínimo de una presentación para esta vista (T045)."""

    id: int
    name: str
    factor: int
    closed_stock: int


class ProductStockView(Protocol):
    """Contrato mínimo de las existencias de un producto (T045)."""

    product: ProductView
    presentations: Sequence[PresentationView]
    total_equivalent: int


class MovementView(Protocol):
    """Contrato mínimo de un movimiento para esta vista (T045)."""

    presentation_id: int
    kind: str
    quantity: int
    reason: str
    occurred_at: datetime
    cost: Decimal | None
    applied_price: Decimal | None
    is_initial_stock: bool


class PriceChangeView(Protocol):
    """Contrato mínimo de un cambio de precio para esta vista (T045)."""

    presentation_id: int
    previous_price: Decimal
    new_price: Decimal
    occurred_at: datetime
    event_type: str


def product_option_label(product: ProductView) -> str:
    """
    Construye la opción del selector para un producto (T045).

    El SKU identifica la opción de forma única y los productos inactivos se
    marcan para que sigan siendo reconocibles y consultables.

    Args:
        product: Producto que se ofrece en el selector.

    Returns:
        El texto de la opción, con la marca de inactivo cuando corresponde.
    """
    label = PRODUCT_OPTION_TEMPLATE.format(
        sku=_single_line(product.sku),
        name=_single_line(product.name),
    )
    if not product.active:
        return f"{label}{INACTIVE_OPTION_MARKER}"

    return label


def render_product_selector(products: Sequence[ProductView]) -> int | None:
    """
    Muestra el selector de producto y devuelve su posición o `None` (T045).

    La opción centinela permite no seleccionar ningún producto. El selector
    solo presenta productos ya consultados por `main.py`; no lee servicios y no
    escribe en el estado de la sesión de Streamlit.

    Args:
        products: Productos disponibles en el listado de la sección.

    Returns:
        La posición del producto elegido dentro de `products`, o `None` si se
        mantiene la opción centinela.
    """
    options = [
        NO_SELECTION_LABEL,
        *(product_option_label(product) for product in products),
    ]
    selected = st.selectbox(
        PRODUCT_SELECTOR_LABEL,
        options,
        index=0,
        key=PRODUCT_SELECTOR_KEY,
    )
    if selected == NO_SELECTION_LABEL:
        st.info(NO_PRODUCT_SELECTED_MESSAGE)
        return None

    return options.index(selected) - 1


def non_paginated_notice(template: str, count: int) -> str:
    """
    Construye un aviso de no paginación con su número real (C8).

    Args:
        template: Plantilla del aviso, con el marcador `{count}`.
        count: Cantidad real de registros presentados.

    Returns:
        El aviso completo, sin truncar registros.
    """
    return template.format(count=count)


def _single_line(value: str) -> str:
    """
    Normaliza los saltos de línea de un valor solo para mostrarlo.

    El valor de negocio no se modifica: el recorte y la unión se aplican a una
    copia usada únicamente para dibujar la vista, de modo que un texto con
    saltos no rompa la estructura de los campos etiquetados.
    """
    return " ".join(value.splitlines())


def _status_text(active: bool) -> str:
    """Devuelve el texto de estado real de un producto (T045)."""
    return ACTIVE_STATUS if active else INACTIVE_STATUS


def _movement_kind_value(kind: object) -> str:
    """
    Devuelve el valor persistido del tipo de movimiento (T045).

    El tipo puede llegar como el valor del dominio —una enumeración de texto—
    o como su cadena persistida; la vista no depende del módulo del dominio.
    """
    return str(getattr(kind, "value", kind))


def movement_kind_label(kind: object) -> str:
    """
    Traduce el tipo de un movimiento para mostrarlo (T045, D5).

    Args:
        kind: Tipo de movimiento, del dominio o como texto persistido.

    Returns:
        La etiqueta en español, o el valor recibido si no es un tipo conocido;
        nunca se inventa una traducción.
    """
    value = _movement_kind_value(kind)

    return MOVEMENT_KIND_LABELS.get(value, value)


def _presentation_name(
    presentations: Sequence[PresentationView],
    presentation_id: int,
) -> str:
    """
    Resuelve el nombre de la presentación de un registro (T045).

    Args:
        presentations: Presentaciones del producto, ya consultadas.
        presentation_id: Presentación referenciada por el registro.

    Returns:
        El nombre de la presentación, o una referencia explícita con su
        identificador si no pertenece al producto; nunca se inventa un nombre.
    """
    for presentation in presentations:
        if presentation.id == presentation_id:
            return _single_line(presentation.name)

    return UNKNOWN_PRESENTATION_TEMPLATE.format(
        presentation_id=presentation_id
    )


def describe_product(stock: ProductStockView) -> str:
    """
    Describe el producto con su estado y sus existencias (T045, C9, D2).

    Args:
        stock: Existencias ya consultadas del producto.

    Returns:
        El texto con SKU, nombre, unidad base, stock mínimo, estado, unidades
        sueltas y total equivalente, sin recalcular el total.
    """
    product = stock.product

    return "\n".join(
        (
            f"{SKU_LABEL}: {_single_line(product.sku)}",
            f"{NAME_LABEL}: {_single_line(product.name)}",
            f"{BASE_UNIT_LABEL}: {_single_line(product.base_unit)}",
            f"{MINIMUM_STOCK_LABEL}: "
            f"{format_quantity(product.minimum_stock)}",
            f"{STATUS_LABEL}: {_status_text(product.active)}",
            f"{LOOSE_UNITS_LABEL}: {format_quantity(product.loose_units)}",
            f"{TOTAL_EQUIVALENT_LABEL}: "
            f"{format_quantity(stock.total_equivalent)}",
        )
    )


def describe_presentation(presentation: PresentationView) -> str:
    """
    Describe una presentación con su factor y su stock cerrado (T045, D3).

    El precio vigente no forma parte del detalle; la presentación base se
    indica cuando su factor es 1.

    Args:
        presentation: Presentación ya consultada.

    Returns:
        El texto etiquetado de la presentación.
    """
    lines = [
        f"{PRESENTATION_LABEL}: {_single_line(presentation.name)}",
        f"{FACTOR_LABEL}: {format_quantity(presentation.factor)}",
        f"{CLOSED_STOCK_LABEL}: "
        f"{format_quantity(presentation.closed_stock)}",
    ]
    if presentation.factor == 1:
        lines.append(f"{BASE_PRESENTATION_LABEL}: {YES_TEXT}")

    return "\n".join(lines)


def describe_movement(
    movement: MovementView,
    presentation_name: str,
) -> str:
    """
    Describe un movimiento con sus datos reales (T045, D5).

    El costo y el precio aplicado se muestran solo cuando existen; un costo
    cero es un valor real y se muestra como cero. Los contadores FIFO no
    forman parte de la vista.

    Args:
        movement: Movimiento ya consultado.
        presentation_name: Nombre resuelto de la presentación afectada.

    Returns:
        El texto etiquetado del movimiento.
    """
    lines = [
        f"{MOVEMENT_TYPE_LABEL}: {movement_kind_label(movement.kind)}",
        f"{QUANTITY_LABEL}: {format_quantity(movement.quantity)}",
        f"{REASON_LABEL}: {_single_line(movement.reason)}",
        f"{DATE_LABEL}: {format_instant(movement.occurred_at)}",
        f"{PRESENTATION_LABEL}: {presentation_name}",
    ]
    if movement.cost is not None:
        lines.append(f"{COST_LABEL}: {format_money(movement.cost)}")
    if movement.applied_price is not None:
        lines.append(
            f"{APPLIED_PRICE_LABEL}: "
            f"{format_money(movement.applied_price)}"
        )
    if movement.is_initial_stock:
        lines.append(f"{INITIAL_STOCK_LABEL}: {YES_TEXT}")

    return "\n".join(lines)


def describe_price_change(
    change: PriceChangeView,
    presentation_name: str,
) -> str:
    """
    Describe un cambio de precio del historial (T045, D6, C19).

    Args:
        change: Cambio de precio ya consultado.
        presentation_name: Nombre resuelto de la presentación afectada.

    Returns:
        El texto etiquetado con presentación, precios, fecha y evento.
    """
    return "\n".join(
        (
            f"{PRESENTATION_LABEL}: {presentation_name}",
            f"{PRICE_PREVIOUS_LABEL}: "
            f"{format_money(change.previous_price)}",
            f"{PRICE_NEW_LABEL}: {format_money(change.new_price)}",
            f"{DATE_LABEL}: {format_instant(change.occurred_at)}",
            f"{EVENT_LABEL}: {_single_line(change.event_type)}",
        )
    )


def _render_presentations(
    presentations: Sequence[PresentationView],
) -> None:
    """Muestra las presentaciones o su estado vacío (T045, D3, C8)."""
    st.subheader(PRESENTATIONS_HEADER)
    if not presentations:
        st.info(EMPTY_PRESENTATIONS_MESSAGE)
        return

    st.info(
        non_paginated_notice(
            PRESENTATIONS_NOTICE_TEMPLATE, len(presentations)
        )
    )
    for presentation in presentations:
        st.text(describe_presentation(presentation))


def _render_movements(
    presentations: Sequence[PresentationView],
    movements: Sequence[MovementView],
) -> None:
    """Muestra los movimientos o su estado vacío (T045, D5, C8)."""
    st.subheader(MOVEMENTS_HEADER)
    if not movements:
        st.info(EMPTY_MOVEMENTS_MESSAGE)
        return

    st.info(non_paginated_notice(MOVEMENTS_NOTICE_TEMPLATE, len(movements)))
    for movement in movements:
        st.text(
            describe_movement(
                movement,
                _presentation_name(
                    presentations, movement.presentation_id
                ),
            )
        )


def _render_price_history(
    presentations: Sequence[PresentationView],
    price_changes: Sequence[PriceChangeView],
) -> None:
    """Muestra el historial de precios o su estado vacío (T045, D6, C8)."""
    st.subheader(PRICE_HISTORY_HEADER)
    if not price_changes:
        st.info(EMPTY_PRICE_HISTORY_MESSAGE)
        return

    st.info(
        non_paginated_notice(
            PRICE_HISTORY_NOTICE_TEMPLATE, len(price_changes)
        )
    )
    for change in price_changes:
        st.text(
            describe_price_change(
                change,
                _presentation_name(
                    presentations, change.presentation_id
                ),
            )
        )


def render_product_detail(
    *,
    stock: ProductStockView,
    movements: Sequence[MovementView],
    price_changes: Sequence[PriceChangeView],
) -> None:
    """
    Muestra el detalle completo de un producto (T045, C9).

    Args:
        stock: Existencias ya consultadas del producto.
        movements: Movimientos del producto en el orden del servicio.
        price_changes: Historial de precios en el orden cronológico del
            servicio; la vista no lo reordena.
    """
    st.subheader(PRODUCT_DETAIL_HEADER)
    st.text(describe_product(stock))
    _render_presentations(stock.presentations)
    _render_movements(stock.presentations, movements)
    _render_price_history(stock.presentations, price_changes)


def render_product_detail_error(message: str) -> None:
    """
    Muestra el error explícito de una lectura del detalle (T045, C21).

    Args:
        message: Motivo devuelto por el servicio de lectura. Se presenta como
            error y no como un historial vacío.
    """
    st.subheader(PRODUCT_DETAIL_HEADER)
    st.error(PRODUCT_DETAIL_ERROR_TEMPLATE.format(reason=message))
