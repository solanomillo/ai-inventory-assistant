"""Sección Operaciones: formularios de propuestas sin escritura (T049, RF-09).

Ofrece un formulario por cada acción admitida por las propuestas vigentes
(`app/schemas/proposals.py`) para preparar una operación: el formulario
construye el payload tipado y **no** ejecuta nada (C10). Enviarlo no escribe el
inventario ni los movimientos: la propuesta queda pendiente y solo la
confirmación explícita del usuario, concentrada en el único punto de
`app/ui/main.py` (D-A), ejecuta la operación (C11, C12).

Este módulo es una **vista**: no consulta el inventario, no importa los
servicios, el dominio ni la persistencia, no abre conexiones ni transacciones,
no usa el estado de la sesión de Streamlit, no confirma y no escribe. Los
productos, las presentaciones y los movimientos que ofrecen los selectores
llegan ya leídos por `app/ui/main.py`, que es el único punto que invoca
`ProposalService.create` y guarda la propuesta pendiente en la sesión. La
creación ocurre solo al enviar un formulario (`st.form_submit_button` con
`clear_on_submit=True`), nunca al mostrar la sección.

Fuente de datos (C22): las lecturas públicas de la sesión
(`InventoryService.list_products()`, `get_product_presentations()` y
`get_movement_history()`), la lectura pública de compensabilidad de
`MovementService.get_compensation_availability()` —coordinada en
`app/ui/main.py`, nunca desde esta vista— y el esquema de propuestas, que
define los campos y los tipos de cada payload.

Límites de la vista:

- Las acciones ofrecidas son exactamente las diez acciones de las propuestas.
  No hay ningún formulario ni control para desactivar productos ni para
  cambiar el factor de una presentación existente: el alta de una presentación
  nueva con su factor sí se admite (C10).
- El alta de producto cubre todos sus campos base y admite una presentación
  inicial opcional; la modificación de producto exige el nombre, el stock
  mínimo o ambos.
- Los importes se introducen como texto y se convierten con `Decimal` exacto,
  sin `float` y sin `st.number_input` para precios ni costos. Las cantidades
  son enteras y mayores que cero; el stock mínimo es mayor o igual que cero;
  los costos son opcionales y no negativos.
- La compensación solo construye el payload y presenta la cantidad compensable
  pendiente que `app/ui/main.py` obtuvo de una lectura pública del servicio de
  movimientos (T054, C18). La vista no la calcula, no consulta el stock y no
  afirma que la compensación sea posible: la revalidación queda al confirmar.
  El selector ofrece únicamente movimientos cuyo tipo admiten las reglas
  vigentes de compensación (entrada cerrada, entrada suelta y salida suelta);
  las aperturas, las compensaciones y las salidas cerradas quedan fuera.
- Los estados de catálogo vacío, producto sin presentaciones, producto sin
  movimientos compensables, lectura de compensabilidad no disponible y
  validación son explícitos y no inventan datos (C4, C21).
- No se duplican reglas de negocio: el stock disponible, el SKU duplicado, el
  producto inactivo y la compensabilidad se validan al confirmar.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

import streamlit as st

from app.schemas.proposals import (
    AperturePayload,
    ClosedEntryPayload,
    ClosedExitPayload,
    CompensationPayload,
    InitialPresentationPayload,
    LooseEntryPayload,
    LooseExitPayload,
    PresentationRegistrationPayload,
    PriceUpdatePayload,
    ProductRegistrationPayload,
    ProductUpdatePayload,
    ProposalAction,
)
from app.ui.formatting import format_instant, single_line
from app.ui.sections.product_detail import (
    MOVEMENT_KIND_LABELS,
    NO_SELECTION_LABEL,
    PresentationView,
    ProductView,
    product_option_label,
)

OPERATIONS_HEADER: str = "Operaciones"
# Encabezado de la sección de operaciones (T049, C10).

OPERATIONS_CAPTION: str = (
    "Cada formulario prepara una propuesta pendiente: no escribe el "
    "inventario ni ejecuta la operación."
)
# Aviso permanente del alcance de la sección (C11, C12).

OPERATIONS_SCOPE_NOTE: str = (
    "No se ofrece desactivar productos ni cambiar el factor de una "
    "presentación existente."
)
# Alcance excluido por RF-09, explícito en la sección (C10).

ACTION_SELECTOR_LABEL: str = "Acción a preparar"
# Etiqueta del selector que elige el formulario de la acción (T049).

ACTION_SELECTOR_HELP: str = "Operación que se va a preparar."
# Ayuda del selector de acción: contexto de la operación (RF-09).

ACTION_SELECTOR_KEY: str = "operations_action_selection"
# Clave propia del selector de acción de la sección (T049).

ACTION_LABELS: dict[str, str] = {
    ProposalAction.PRODUCT_REGISTRATION.value: "Alta de producto",
    ProposalAction.PRODUCT_UPDATE.value: "Modificación de producto",
    ProposalAction.PRESENTATION_REGISTRATION.value: "Alta de presentación",
    ProposalAction.LOOSE_ENTRY.value: "Entrada de unidades sueltas",
    ProposalAction.CLOSED_ENTRY.value: "Entrada de presentaciones cerradas",
    ProposalAction.LOOSE_EXIT.value: "Salida de unidades sueltas",
    ProposalAction.CLOSED_EXIT.value: "Salida de presentaciones cerradas",
    ProposalAction.APERTURE.value: "Apertura de presentaciones",
    ProposalAction.COMPENSATION.value: "Compensación de un movimiento",
    ProposalAction.PRICE_UPDATE.value: "Cambio de precio",
}
# Nombre legible de cada una de las diez acciones admitidas (C10).

ACTION_OPTIONS: tuple[str, ...] = tuple(ACTION_LABELS.values())
# Opciones del selector en el orden de las acciones de las propuestas.

ACTION_VALUES_BY_LABEL: dict[str, str] = {
    label: value for value, label in ACTION_LABELS.items()
}
# Traducción inversa de la opción elegida al valor de la acción.

PRODUCT_SELECTOR_LABEL: str = "Producto"
# Etiqueta del selector de producto de los formularios (T049).

PRODUCT_SELECTOR_HELP: str = "Producto afectado por la operación."
# Ayuda del selector de producto: contexto del producto afectado (RF-09).

PRODUCT_SELECTOR_KEY: str = "operations_product_selection"
# Clave propia del selector de producto: no comparte estado con otras vistas.

PRESENTATION_SELECTOR_LABEL: str = "Presentación"
# Etiqueta del selector de presentación de los formularios (T049).

CLOSED_ENTRY_PRESENTATION_LABEL: str = "Presentación cerrada recibida"
# Etiqueta contextual del selector de una entrada de presentaciones cerradas.

CLOSED_PRESENTATION_NO_SELECTION_LABEL: str = (
    "Elegí una presentación cerrada"
)
# Opción centinela propia del selector de una salida de presentaciones
# cerradas: no hay presentación cerrada elegida (C4).

PRESENTATION_SELECTOR_HELP: str = (
    "Presentación afectada por la operación."
)
# Ayuda del selector de presentación: contexto de la presentación (RF-09).

PRESENTATION_SELECTOR_KEY_TEMPLATE: str = (
    "operations_presentation_selection_{product_id}"
)
# Clave del selector de presentación, distinta por producto para no conservar
# una presentación de otro producto (T047, D2).

MOVEMENT_SELECTOR_LABEL: str = "Movimiento a compensar"
# Etiqueta del selector de movimiento de la compensación (T049).

MOVEMENT_SELECTOR_HELP: str = "Movimiento que se va a compensar."
# Ayuda del selector de movimiento: contexto del movimiento afectado (RF-09).

MOVEMENT_SELECTOR_KEY_TEMPLATE: str = (
    "operations_movement_selection_{product_id}"
)
# Clave del selector de movimiento, distinta por producto (T046, D1).

FORM_KEY_TEMPLATE: str = "operations_form_{action}"
# Clave del formulario de cada acción; no comparte estado con otros.

SUBMIT_LABEL: str = "Crear propuesta"
# Etiqueta del control que envía el formulario (T049).

COMPENSABLE_MOVEMENT_KINDS: tuple[str, ...] = (
    "entrada_cerrada",
    "entrada_suelta",
    "salida_suelta",
)
# Tipos de movimiento que admiten una compensación en el MVP (RF-03): las
# aperturas y las compensaciones no son compensables y la compensación de una
# salida cerrada queda fuera del alcance.

COMPENSATION_NOTE: str = (
    "La cantidad compensable proviene de una lectura del servicio y se "
    "revalida al confirmar: esta vista no la calcula."
)
# Aviso que aclara el origen de la cantidad compensable (C18, RF-09).

COMPENSABLE_QUANTITY_TEMPLATE: str = (
    "Cantidad compensable pendiente: {quantity}."
)
# Formato de la cantidad compensable ya leída por `app/ui/main.py` (T054).

COMPENSATION_UNAVAILABLE_MESSAGE: str = (
    "No se pudo leer la cantidad compensable pendiente: no se afirma que "
    "exista."
)
# Estado de una lectura pública no disponible: no se inventa ni se afirma una
# cantidad compensable (T054, D-D).

SKU_FIELD_LABEL: str = "SKU"
# Etiqueta del SKU del alta de producto (RF-01).

SKU_FIELD_PLACEHOLDER: str = "Ej.: COCA-500"
# Ejemplo orientativo del SKU; no es una validación (RF-01).

SKU_FIELD_HELP: str = (
    "Código único; no se modifica después del alta."
)
# Ayuda del SKU: unicidad e inmutabilidad tras el alta (RF-01).

NAME_FIELD_LABEL: str = "Nombre del producto"
# Etiqueta del nombre del producto en el alta y la modificación (RF-01).

NAME_FIELD_PLACEHOLDER: str = "Ej.: Coca-Cola 500 ml"
# Ejemplo orientativo del nombre del producto; no es una validación.

PRESENTATION_NAME_FIELD_LABEL: str = "Nombre de la presentación"
# Etiqueta del nombre de una presentación nueva (RF-07).

PRESENTATION_NAME_FIELD_PLACEHOLDER: str = "Ej.: Fardo x 6"
# Ejemplo orientativo del nombre de la presentación; no es una validación.

BASE_UNIT_FIELD_LABEL: str = "Unidad base"
# Etiqueta de la unidad base del alta de producto (RF-01).

BASE_UNIT_FIELD_PLACEHOLDER: str = "Ej.: botella"
# Ejemplo orientativo de la unidad base; no es una validación.

BASE_UNIT_FIELD_HELP: str = (
    "Unidad mínima con la que se mide el stock."
)
# Ayuda de la unidad base: unidad mínima de medida del stock (RF-01).

BASE_PRICE_FIELD_LABEL: str = "Precio de venta de la unidad base"
# Etiqueta del precio de venta de la presentación base (RF-01).

BASE_PRICE_FIELD_PLACEHOLDER: str = "Ej.: 1500.00"
# Ejemplo orientativo del precio de la unidad base; no es una validación.

BASE_PRICE_FIELD_HELP: str = (
    "Precio de una unidad base; no es el precio de un fardo."
)
# Ayuda del precio base: distingue la unidad base de una presentación (RF-01).

MINIMUM_STOCK_FIELD_LABEL: str = "Stock mínimo"
# Etiqueta del stock mínimo del producto (RF-01).

MINIMUM_STOCK_FIELD_HELP: str = (
    "Umbral de alerta en unidades base. Ej.: 6."
)
# Ayuda del stock mínimo: umbral expresado en unidades base (RF-04).

INITIAL_LOOSE_UNITS_LABEL: str = "Unidades sueltas iniciales"
# Etiqueta del stock suelto inicial del alta de producto (RF-01).

INITIAL_LOOSE_UNITS_HELP: str = (
    "Stock inicial no agrupado en presentaciones cerradas. Ej.: 2."
)
# Ayuda de las unidades sueltas iniciales: stock fuera de presentaciones.

INITIAL_LOOSE_COST_LABEL: str = (
    "Costo total de las unidades sueltas iniciales (opcional)"
)
# Etiqueta del costo total opcional del grupo de unidades sueltas (T058).

INITIAL_LOOSE_COST_PLACEHOLDER: str = "Ej.: 1000.00"
# Ejemplo orientativo del costo total suelto; no es una validación.

INITIAL_LOOSE_COST_HELP: str = (
    "Importe total de esas unidades; no es un costo unitario ni se deriva "
    "del precio de venta."
)
# Ayuda del costo suelto: importe total del grupo, sin prorrateo (T058).

BASE_PRESENTATION_NAME_LABEL: str = (
    "Nombre visible de la unidad base (opcional)"
)
# Etiqueta del nombre opcional de la presentación base (RF-07).

BASE_PRESENTATION_NAME_PLACEHOLDER: str = "Ej.: Botella 500 ml"
# Ejemplo orientativo del nombre visible de la unidad base.

BASE_PRESENTATION_NAME_HELP: str = (
    "Solo cambia el nombre de la presentación de factor 1; si se omite, "
    "se usa la unidad base."
)
# Ayuda del nombre visible: afecta solo a la presentación base (RF-07).

INITIAL_PRESENTATION_CAPTION: str = (
    "Presentación adicional (opcional): empaque opcional además de la "
    "unidad base. Informá su nombre y su precio para declararla en el "
    "alta; si los dejás vacíos, no se declara."
)
# Aviso del bloque opcional de presentación adicional del alta (RF-07).

INITIAL_PRESENTATION_NAME_LABEL: str = (
    "Nombre de la presentación adicional"
)
# Etiqueta del nombre de la presentación adicional opcional (RF-07).

INITIAL_PRESENTATION_NAME_PLACEHOLDER: str = "Ej.: Fardo x 6"
# Ejemplo orientativo del nombre de la presentación adicional.

INITIAL_PRESENTATION_HELP: str = (
    "Empaque opcional además de la unidad base."
)
# Ayuda de la presentación adicional: no sustituye a la unidad base (RF-07).

FACTOR_FIELD_LABEL: str = "Equivalencia (unidades base)"
# Etiqueta de la equivalencia de la presentación nueva (RF-07).

FACTOR_FIELD_HELP: str = (
    "Cantidad de unidades base que contiene la presentación; un fardo "
    "con equivalencia 6 contiene 6 botellas. Ej.: 6."
)
# Ayuda de la equivalencia: unidades base contenidas en la presentación.

PRESENTATION_PRICE_LABEL: str = "Precio de venta de la presentación"
# Etiqueta del precio de venta de la presentación nueva (RF-07).

PRESENTATION_PRICE_PLACEHOLDER: str = "Ej.: 7500.00"
# Ejemplo orientativo del precio de la presentación; no es una validación.

PRESENTATION_PRICE_HELP: str = (
    "Precio de una presentación completa, no de cada unidad base."
)
# Ayuda del precio de la presentación: precio del empaque completo (RF-07).

INITIAL_CLOSED_STOCK_LABEL: str = (
    "Cantidad inicial de presentaciones cerradas"
)
# Etiqueta de las presentaciones cerradas iniciales de la presentación nueva.

INITIAL_CLOSED_STOCK_HELP: str = (
    "Presentaciones cerradas existentes al dar de alta. Ej.: 1."
)
# Ayuda de las presentaciones cerradas iniciales (RF-07).

INITIAL_PRESENTATION_COST_LABEL: str = (
    "Costo total de la presentación adicional (opcional)"
)
# Etiqueta del costo total opcional de la presentación adicional (T058).

INITIAL_PRESENTATION_COST_PLACEHOLDER: str = "Ej.: 7500.00"
# Ejemplo orientativo del costo total de la presentación; no es una validación.

INITIAL_PRESENTATION_COST_HELP: str = (
    "Importe total de las presentaciones cerradas iniciales; no se distribuye "
    "entre unidades ni se deriva del precio de venta."
)
# Ayuda del costo de la presentación: importe total del grupo (T058).

NEW_NAME_LABEL: str = "Nombre nuevo (opcional)"
# Etiqueta del nombre que propone la modificación de producto (RF-01).

NEW_NAME_PLACEHOLDER: str = "Ej.: Coca-Cola 500 ml"
# Ejemplo orientativo del nombre nuevo; no es una validación.

NEW_MINIMUM_STOCK_LABEL: str = "Nuevo stock mínimo (opcional)"
# Etiqueta del stock mínimo que propone la modificación (RF-01).

NEW_MINIMUM_STOCK_PLACEHOLDER: str = "Ej.: 10"
# Ejemplo orientativo del nuevo stock mínimo; no es una validación.

QUANTITY_FIELD_LABEL: str = "Cantidad"
# Etiqueta genérica de la cantidad, conservada para las descripciones (RF-03).

LOOSE_QUANTITY_LABEL: str = "Cantidad de unidades sueltas"
# Etiqueta contextual de una entrada o salida suelta (RF-03).

CLOSED_QUANTITY_LABEL: str = "Cantidad de presentaciones cerradas"
# Etiqueta contextual de una entrada o salida cerrada (RF-03).

APERTURE_QUANTITY_LABEL: str = "Cantidad de presentaciones a abrir"
# Etiqueta contextual de una apertura de presentaciones (RF-03).

COMPENSATION_QUANTITY_LABEL: str = "Cantidad a compensar"
# Etiqueta contextual de la cantidad de una compensación (RF-03).

QUANTITY_FIELD_HELP: str = "Cantidad entera de la operación. Ej.: 2."
# Ayuda de una cantidad: ejemplo orientativo, sin cambiar la validación.

REASON_FIELD_LABEL: str = "Motivo"
# Etiqueta del motivo obligatorio de un movimiento o de una apertura (RF-03).

REASON_FIELD_HELP: str = "Queda registrado en el historial."
# Ayuda del motivo: se conserva en el historial (RF-03).

REASON_ENTRY_PLACEHOLDER: str = "Ej.: Compra de mercadería"
# Ejemplo orientativo del motivo de una entrada; no es una validación.

REASON_EXIT_PLACEHOLDER: str = "Ej.: Venta"
# Ejemplo orientativo del motivo de una salida; no es una validación.

REASON_APERTURE_PLACEHOLDER: str = "Ej.: Apertura para venta"
# Ejemplo orientativo del motivo de una apertura; no es una validación.

REASON_COMPENSATION_PLACEHOLDER: str = "Ej.: Corrección de inventario"
# Ejemplo orientativo del motivo de una compensación; no es una validación.

COST_FIELD_LABEL: str = "Costo de ingreso (opcional)"
# Etiqueta del costo opcional de una entrada (RF-02, RF-03).

COST_FIELD_PLACEHOLDER: str = "Ej.: 1200.00"
# Ejemplo orientativo del costo de ingreso; no es una validación.

COST_FIELD_HELP: str = (
    "Importe total informado; no se distribuye entre unidades; solo en "
    "entradas."
)
# Ayuda del costo: importe total de la entrada, sin prorrateo (RF-02, RF-03).

NEW_PRICE_LABEL: str = "Nuevo precio de venta"
# Etiqueta del precio nuevo del cambio de precio (RF-08).

NEW_PRICE_PLACEHOLDER: str = "Ej.: 1600.00"
# Ejemplo orientativo del nuevo precio de venta; no es una validación.

NEW_PRICE_FIELD_HELP: str = (
    "Precio que tendrá la presentación después de confirmar."
)
# Ayuda del nuevo precio: precio posterior a la confirmación (RF-08).

EMPTY_CATALOG_TEMPLATE: str = (
    "El catálogo no tiene productos registrados: no hay datos para preparar "
    "«{action}». El alta de un producto no requiere un catálogo previo."
)
# Estado de catálogo vacío: explícito y sin datos inventados (C4).

NO_SELECTION_MESSAGE: str = (
    "Elegí un producto para preparar la operación."
)
# Estado explícito mientras no hay producto seleccionado (C4).

NO_MOVEMENT_SELECTION_MESSAGE: str = (
    "Elegí un movimiento para preparar la compensación."
)
# Estado explícito mientras no hay movimiento elegido para compensar (C4).

EMPTY_PRESENTATIONS_MESSAGE: str = (
    "El producto no tiene presentaciones registradas."
)
# Estado de un producto sin presentaciones, sin formulario inventado (C4).

NO_CLOSED_PRESENTATIONS_MESSAGE: str = (
    "El producto no tiene presentaciones cerradas registradas. Usá la "
    "entrada de unidades sueltas para la unidad base."
)
# Estado de un producto cuyas únicas presentaciones son la base: no hay una
# presentación cerrada que ofrecer en la entrada (C4).

NO_CLOSED_EXIT_PRESENTATIONS_MESSAGE: str = (
    "El producto no tiene presentaciones cerradas disponibles para retirar."
)
# Estado de una salida cerrada cuyas únicas presentaciones son la base: no hay
# ninguna presentación cerrada que retirar, sin sugerir una entrada de unidades
# sueltas (C4).

EMPTY_MOVEMENTS_MESSAGE: str = (
    "El producto no tiene movimientos compensables registrados."
)
# Estado de un producto sin movimientos que admitan compensación (C4).

MISSING_TEXT_TEMPLATE: str = "El campo «{field}» es obligatorio."
# Validación de un texto obligatorio vacío tras recortar (C21).

INVALID_AMOUNT_TEMPLATE: str = (
    "El campo «{field}» debe ser un importe decimal válido."
)
# Validación de un importe que no puede representarse con `Decimal` (C21).

NON_POSITIVE_AMOUNT_TEMPLATE: str = (
    "El campo «{field}» debe ser mayor que cero."
)
# Validación de un precio o un costo obligatorio no positivo (C21).

NEGATIVE_AMOUNT_TEMPLATE: str = "El campo «{field}» no puede ser negativo."
# Validación de un costo opcional negativo (C21).

INVALID_COUNT_TEMPLATE: str = (
    "El campo «{field}» debe ser un número entero."
)
# Validación de una cantidad o un stock que no es un entero (C21).

NEGATIVE_COUNT_TEMPLATE: str = "El campo «{field}» no puede ser negativo."
# Validación de un stock mínimo negativo (C21).

NON_POSITIVE_COUNT_TEMPLATE: str = (
    "El campo «{field}» debe ser mayor que cero."
)
# Validación de una cantidad que no es positiva (C21).

INVALID_FACTOR_MESSAGE: str = (
    "La equivalencia debe ser un número entero mayor o igual que 1."
)
# Validación formal de la equivalencia: la regla del dominio se revalida al
# confirmar.

UPDATE_WITHOUT_CHANGE_MESSAGE: str = (
    "La modificación debe indicar el nombre, el stock mínimo o ambos."
)
# Validación de una modificación sin ningún cambio propuesto (RF-01).

PROPOSAL_CREATED_TEMPLATE: str = (
    "Propuesta pendiente creada: «{action}». No se escribió ningún cambio."
)
# Aviso de la propuesta creada al enviar un formulario válido (C11).


class OperationValidationError(ValueError):
    """Error de validación de un formulario de operación (T049, C21)."""


class MovementOption(Protocol):
    """Contrato mínimo de un movimiento para el selector de compensación."""

    id: int
    kind: str
    quantity: int
    occurred_at: datetime


def parse_required_text(field: str, value: str) -> str:
    """
    Valida un texto obligatorio y devuelve su valor recortado (T049).

    Args:
        field: Etiqueta del campo, para el mensaje de validación.
        value: Texto informado en el formulario.

    Returns:
        El texto sin espacios iniciales ni finales.

    Raises:
        OperationValidationError: Si el texto queda vacío tras recortarlo.
    """
    text = value.strip()
    if not text:
        raise OperationValidationError(
            MISSING_TEXT_TEMPLATE.format(field=field)
        )

    return text


def parse_optional_text(value: str) -> str | None:
    """
    Convierte un texto opcional en `None` cuando queda vacío (T049).

    Args:
        value: Texto informado en el formulario.

    Returns:
        El texto recortado, o `None` si solo tenía espacios.
    """
    text = value.strip()

    return text or None


def parse_quantity(field: str, value: int) -> int:
    """
    Valida una cantidad entera mayor que cero (T049, RF-03).

    Args:
        field: Etiqueta del campo, para el mensaje de validación.
        value: Cantidad elegida en el formulario.

    Returns:
        La cantidad validada.

    Raises:
        OperationValidationError: Si la cantidad no es un entero o no es
            positiva.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise OperationValidationError(
            INVALID_COUNT_TEMPLATE.format(field=field)
        )
    if value <= 0:
        raise OperationValidationError(
            NON_POSITIVE_COUNT_TEMPLATE.format(field=field)
        )

    return value


def parse_optional_count(field: str, value: str) -> int | None:
    """
    Valida un entero opcional y no negativo informado como texto (T049).

    Args:
        field: Etiqueta del campo, para el mensaje de validación.
        value: Texto informado en el formulario; vacío significa ausente.

    Returns:
        El entero validado, o `None` si el texto quedó vacío.

    Raises:
        OperationValidationError: Si el texto no es un entero o es negativo.
    """
    text = value.strip()
    if not text:
        return None

    try:
        count = int(text)
    except ValueError as error:
        raise OperationValidationError(
            INVALID_COUNT_TEMPLATE.format(field=field)
        ) from error
    if count < 0:
        raise OperationValidationError(
            NEGATIVE_COUNT_TEMPLATE.format(field=field)
        )

    return count


def parse_factor(value: int) -> int:
    """
    Valida el factor de una presentación nueva (T049, RF-07).

    Solo comprueba la restricción formal del payload —entero mayor o igual que
    1—; la equivalencia real y el resto de las reglas las valida el dominio al
    confirmar.

    Args:
        value: Factor elegido en el formulario.

    Returns:
        El factor validado.

    Raises:
        OperationValidationError: Si el factor no es un entero mayor o igual
            que 1.
    """
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OperationValidationError(INVALID_FACTOR_MESSAGE)

    return value


def parse_money(field: str, value: str) -> Decimal:
    """
    Convierte un importe obligatorio mayor que cero con `Decimal` exacto.

    El importe se interpreta desde su texto decimal, sin pasar por `float`.

    Args:
        field: Etiqueta del campo, para el mensaje de validación.
        value: Texto informado en el formulario.

    Returns:
        El importe exacto.

    Raises:
        OperationValidationError: Si el importe está vacío, no es un decimal
            finito o no es mayor que cero.
    """
    text = value.strip()
    if not text:
        raise OperationValidationError(
            MISSING_TEXT_TEMPLATE.format(field=field)
        )

    amount = _parse_decimal(field, text)
    if amount <= 0:
        raise OperationValidationError(
            NON_POSITIVE_AMOUNT_TEMPLATE.format(field=field)
        )

    return amount


def parse_optional_money(field: str, value: str) -> Decimal | None:
    """
    Convierte un costo opcional no negativo con `Decimal` exacto (T049).

    Args:
        field: Etiqueta del campo, para el mensaje de validación.
        value: Texto informado en el formulario; vacío significa ausente.

    Returns:
        El importe exacto, o `None` si el campo quedó vacío.

    Raises:
        OperationValidationError: Si el importe no es un decimal finito o es
            negativo.
    """
    text = value.strip()
    if not text:
        return None

    amount = _parse_decimal(field, text)
    if amount < 0:
        raise OperationValidationError(
            NEGATIVE_AMOUNT_TEMPLATE.format(field=field)
        )

    return amount


def render_operations_header() -> None:
    """Muestra el encabezado y el alcance de la sección (T049, C10–C12)."""
    st.header(OPERATIONS_HEADER)
    st.caption(OPERATIONS_CAPTION)
    st.caption(OPERATIONS_SCOPE_NOTE)


def render_validation(message: str) -> None:
    """
    Muestra una validación local del formulario (T055, C21).

    La validación se distingue del rechazo del servicio: no ejecutó ninguna
    operación ni afirma que la propuesta se haya creado. Los formularios de la
    sección y la edición de propuestas comparten este único punto de
    presentación.

    Args:
        message: Motivo de validación, ya redactado por el formulario.
    """
    st.warning(message)


def render_action_selector() -> ProposalAction:
    """
    Muestra el selector de la acción y devuelve la elegida (T049, C10).

    Returns:
        La acción cuyo formulario se muestra; por defecto, el alta de
        producto, que no requiere un catálogo previo.
    """
    selected = st.selectbox(
        ACTION_SELECTOR_LABEL,
        ACTION_OPTIONS,
        index=0,
        key=ACTION_SELECTOR_KEY,
        help=ACTION_SELECTOR_HELP,
    )

    return ProposalAction(ACTION_VALUES_BY_LABEL[selected])


def render_empty_catalog(action: ProposalAction) -> None:
    """
    Muestra el estado de catálogo vacío de la acción elegida (T049, C4).

    Args:
        action: Acción que se estaba preparando; se informa por su nombre.
    """
    st.info(EMPTY_CATALOG_TEMPLATE.format(action=action_label(action)))


def render_empty_presentations() -> None:
    """Muestra el estado de un producto sin presentaciones (T049, C4)."""
    st.info(EMPTY_PRESENTATIONS_MESSAGE)


def render_product_selector(
    products: Sequence[ProductView],
) -> ProductView | None:
    """
    Muestra el selector obligatorio de producto (T049).

    El selector solo presenta los productos ya leídos y no escribe en el
    estado de la sesión de Streamlit más allá del valor del propio widget.

    Args:
        products: Productos ya consultados, en el orden del servicio.

    Returns:
        El producto elegido, o `None` mientras se mantiene la opción
        centinela.
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
        help=PRODUCT_SELECTOR_HELP,
    )
    if selected == NO_SELECTION_LABEL:
        st.info(NO_SELECTION_MESSAGE)
        return None

    return products[options.index(selected) - 1]


def render_product_registration_form() -> ProductRegistrationPayload | None:
    """
    Muestra el formulario del alta de producto (T049, RF-01, RF-07).

    El formulario cubre todos los campos base del payload y admite una
    presentación inicial opcional con su factor, su precio y su stock cerrado.
    La propuesta solo se construye al enviar el formulario y no escribe nada.

    Returns:
        El payload validado, o `None` si no se envió o si la validación
        falló; en ese caso el motivo se muestra en la sección.
    """
    with st.form(
        key=_form_key(ProposalAction.PRODUCT_REGISTRATION),
        clear_on_submit=True,
    ):
        sku = st.text_input(
            SKU_FIELD_LABEL,
            placeholder=SKU_FIELD_PLACEHOLDER,
            help=SKU_FIELD_HELP,
        )
        name = st.text_input(
            NAME_FIELD_LABEL,
            placeholder=NAME_FIELD_PLACEHOLDER,
        )
        base_unit = st.text_input(
            BASE_UNIT_FIELD_LABEL,
            placeholder=BASE_UNIT_FIELD_PLACEHOLDER,
            help=BASE_UNIT_FIELD_HELP,
        )
        base_price = st.text_input(
            BASE_PRICE_FIELD_LABEL,
            placeholder=BASE_PRICE_FIELD_PLACEHOLDER,
            help=BASE_PRICE_FIELD_HELP,
        )
        minimum_stock = st.number_input(
            MINIMUM_STOCK_FIELD_LABEL,
            min_value=0,
            value=0,
            step=1,
            help=MINIMUM_STOCK_FIELD_HELP,
        )
        initial_loose_units = st.number_input(
            INITIAL_LOOSE_UNITS_LABEL,
            min_value=0,
            value=0,
            step=1,
            help=INITIAL_LOOSE_UNITS_HELP,
        )
        initial_loose_cost = st.text_input(
            INITIAL_LOOSE_COST_LABEL,
            placeholder=INITIAL_LOOSE_COST_PLACEHOLDER,
            help=INITIAL_LOOSE_COST_HELP,
        )
        base_presentation_name = st.text_input(
            BASE_PRESENTATION_NAME_LABEL,
            placeholder=BASE_PRESENTATION_NAME_PLACEHOLDER,
            help=BASE_PRESENTATION_NAME_HELP,
        )
        st.caption(INITIAL_PRESENTATION_CAPTION)
        initial_name = st.text_input(
            INITIAL_PRESENTATION_NAME_LABEL,
            placeholder=INITIAL_PRESENTATION_NAME_PLACEHOLDER,
            help=INITIAL_PRESENTATION_HELP,
        )
        initial_factor = st.number_input(
            FACTOR_FIELD_LABEL,
            min_value=0,
            value=1,
            step=1,
            help=FACTOR_FIELD_HELP,
        )
        initial_price = st.text_input(
            PRESENTATION_PRICE_LABEL,
            placeholder=PRESENTATION_PRICE_PLACEHOLDER,
            help=PRESENTATION_PRICE_HELP,
        )
        initial_closed_stock = st.number_input(
            INITIAL_CLOSED_STOCK_LABEL,
            min_value=0,
            value=0,
            step=1,
            help=INITIAL_CLOSED_STOCK_HELP,
        )
        initial_cost = st.text_input(
            INITIAL_PRESENTATION_COST_LABEL,
            placeholder=INITIAL_PRESENTATION_COST_PLACEHOLDER,
            help=INITIAL_PRESENTATION_COST_HELP,
        )
        submitted = st.form_submit_button(SUBMIT_LABEL)

    if not submitted:
        return None

    try:
        presentations: tuple[InitialPresentationPayload, ...] = ()
        if _requests_initial_presentation(
            name=initial_name,
            price=initial_price,
            factor=initial_factor,
            closed_stock=initial_closed_stock,
            cost=initial_cost,
        ):
            presentations = (
                InitialPresentationPayload(
                    name=parse_required_text(
                        INITIAL_PRESENTATION_NAME_LABEL,
                        initial_name,
                    ),
                    factor=parse_factor(initial_factor),
                    sale_price=parse_money(
                        PRESENTATION_PRICE_LABEL,
                        initial_price,
                    ),
                    closed_stock=initial_closed_stock,
                    cost=parse_optional_money(
                        INITIAL_PRESENTATION_COST_LABEL,
                        initial_cost,
                    ),
                ),
            )

        return ProductRegistrationPayload(
            sku=parse_required_text(SKU_FIELD_LABEL, sku),
            name=parse_required_text(NAME_FIELD_LABEL, name),
            base_unit=parse_required_text(BASE_UNIT_FIELD_LABEL, base_unit),
            base_sale_price=parse_money(
                BASE_PRICE_FIELD_LABEL,
                base_price,
            ),
            minimum_stock=minimum_stock,
            initial_loose_units=initial_loose_units,
            initial_loose_cost=parse_optional_money(
                INITIAL_LOOSE_COST_LABEL,
                initial_loose_cost,
            ),
            presentations=presentations,
            base_presentation_name=parse_optional_text(
                base_presentation_name
            ),
        )
    except OperationValidationError as error:
        render_validation(str(error))
        return None


def render_product_update_form(
    product: ProductView,
) -> ProductUpdatePayload | None:
    """
    Muestra el formulario de modificación de un producto (T049, RF-01).

    Solo propone el nombre y el stock mínimo —el SKU es inmutable y la
    desactivación queda fuera del alcance— y exige al menos uno de los dos.
    Los dos campos son opcionales: vacío significa que no se propone ese
    cambio.

    Args:
        product: Producto elegido, cuya modificación se prepara.

    Returns:
        El payload validado, o `None` si no se envió o si la validación falló.
    """
    with st.form(
        key=_form_key(ProposalAction.PRODUCT_UPDATE),
        clear_on_submit=True,
    ):
        st.caption(f"Producto: {product_option_label(product)}")
        new_name = st.text_input(
            NEW_NAME_LABEL,
            placeholder=NEW_NAME_PLACEHOLDER,
        )
        new_minimum_stock = st.text_input(
            NEW_MINIMUM_STOCK_LABEL,
            placeholder=NEW_MINIMUM_STOCK_PLACEHOLDER,
        )
        submitted = st.form_submit_button(SUBMIT_LABEL)

    if not submitted:
        return None

    try:
        name = parse_optional_text(new_name)
        minimum_stock = parse_optional_count(
            MINIMUM_STOCK_FIELD_LABEL,
            new_minimum_stock,
        )
        if name is None and minimum_stock is None:
            raise OperationValidationError(UPDATE_WITHOUT_CHANGE_MESSAGE)

        return ProductUpdatePayload(
            product_id=product.id,
            name=name,
            minimum_stock=minimum_stock,
        )
    except OperationValidationError as error:
        render_validation(str(error))
        return None


def render_presentation_registration_form(
    product: ProductView,
) -> PresentationRegistrationPayload | None:
    """
    Muestra el formulario del alta de una presentación nueva (T049, RF-07).

    El alta admite un factor nuevo para la presentación que se crea; no
    modifica el factor de una presentación existente.

    Args:
        product: Producto al que pertenece la presentación nueva.

    Returns:
        El payload validado, o `None` si no se envió o si la validación falló.
    """
    with st.form(
        key=_form_key(ProposalAction.PRESENTATION_REGISTRATION),
        clear_on_submit=True,
    ):
        st.caption(f"Producto: {product_option_label(product)}")
        name = st.text_input(
            PRESENTATION_NAME_FIELD_LABEL,
            placeholder=PRESENTATION_NAME_FIELD_PLACEHOLDER,
        )
        factor = st.number_input(
            FACTOR_FIELD_LABEL,
            min_value=0,
            value=1,
            step=1,
            help=FACTOR_FIELD_HELP,
        )
        sale_price = st.text_input(
            PRESENTATION_PRICE_LABEL,
            placeholder=PRESENTATION_PRICE_PLACEHOLDER,
            help=PRESENTATION_PRICE_HELP,
        )
        submitted = st.form_submit_button(SUBMIT_LABEL)

    if not submitted:
        return None

    try:
        return PresentationRegistrationPayload(
            product_id=product.id,
            name=parse_required_text(
                PRESENTATION_NAME_FIELD_LABEL,
                name,
            ),
            factor=parse_factor(factor),
            sale_price=parse_money(PRESENTATION_PRICE_LABEL, sale_price),
        )
    except OperationValidationError as error:
        render_validation(str(error))
        return None


def render_loose_entry_form(
    product: ProductView,
) -> LooseEntryPayload | None:
    """
    Muestra el formulario de una entrada de unidades sueltas (T049, RF-02).

    Args:
        product: Producto de la entrada propuesta.

    Returns:
        El payload validado, o `None` si no se envió o si la validación falló.
    """
    with st.form(
        key=_form_key(ProposalAction.LOOSE_ENTRY),
        clear_on_submit=True,
    ):
        st.caption(f"Producto: {product_option_label(product)}")
        quantity = st.number_input(
            LOOSE_QUANTITY_LABEL,
            min_value=0,
            value=0,
            step=1,
            help=QUANTITY_FIELD_HELP,
        )
        reason = st.text_input(
            REASON_FIELD_LABEL,
            placeholder=REASON_ENTRY_PLACEHOLDER,
            help=REASON_FIELD_HELP,
        )
        cost = st.text_input(
            COST_FIELD_LABEL,
            placeholder=COST_FIELD_PLACEHOLDER,
            help=COST_FIELD_HELP,
        )
        submitted = st.form_submit_button(SUBMIT_LABEL)

    if not submitted:
        return None

    try:
        return LooseEntryPayload(
            product_id=product.id,
            quantity=parse_quantity(LOOSE_QUANTITY_LABEL, quantity),
            reason=parse_required_text(REASON_FIELD_LABEL, reason),
            cost=parse_optional_money(COST_FIELD_LABEL, cost),
        )
    except OperationValidationError as error:
        render_validation(str(error))
        return None


def render_loose_exit_form(
    product: ProductView,
) -> LooseExitPayload | None:
    """
    Muestra el formulario de una salida de unidades sueltas (T049, RF-02).

    Args:
        product: Producto de la salida propuesta.

    Returns:
        El payload validado, o `None` si no se envió o si la validación falló.
    """
    with st.form(
        key=_form_key(ProposalAction.LOOSE_EXIT),
        clear_on_submit=True,
    ):
        st.caption(f"Producto: {product_option_label(product)}")
        quantity = st.number_input(
            LOOSE_QUANTITY_LABEL,
            min_value=0,
            value=0,
            step=1,
            help=QUANTITY_FIELD_HELP,
        )
        reason = st.text_input(
            REASON_FIELD_LABEL,
            placeholder=REASON_EXIT_PLACEHOLDER,
            help=REASON_FIELD_HELP,
        )
        submitted = st.form_submit_button(SUBMIT_LABEL)

    if not submitted:
        return None

    try:
        return LooseExitPayload(
            product_id=product.id,
            quantity=parse_quantity(LOOSE_QUANTITY_LABEL, quantity),
            reason=parse_required_text(REASON_FIELD_LABEL, reason),
        )
    except OperationValidationError as error:
        render_validation(str(error))
        return None


def render_closed_entry_form(
    product: ProductView,
    presentations: Sequence[PresentationView],
) -> ClosedEntryPayload | None:
    """
    Muestra el formulario de una entrada de presentaciones cerradas (T049).

    El selector solo ofrece presentaciones con equivalencia mayor que 1: la
    presentación base no representa una presentación cerrada. Si el producto
    solo tiene la unidad base, se informa el estado y no se muestra ningún
    formulario.

    Args:
        product: Producto de la entrada propuesta.
        presentations: Presentaciones del producto ya consultadas.

    Returns:
        El payload validado, o `None` si no se envió, si la validación falló o
        si no hay presentaciones cerradas que ofrecer.
    """
    closed_presentations = [
        presentation
        for presentation in presentations
        if presentation.factor > 1
    ]
    if not closed_presentations:
        st.info(NO_CLOSED_PRESENTATIONS_MESSAGE)
        return None

    with st.form(
        key=_form_key(ProposalAction.CLOSED_ENTRY),
        clear_on_submit=True,
    ):
        st.caption(f"Producto: {product_option_label(product)}")
        presentation = _render_presentation_selector(
            closed_presentations,
            product_id=product.id,
            label=CLOSED_ENTRY_PRESENTATION_LABEL,
        )
        quantity = st.number_input(
            CLOSED_QUANTITY_LABEL,
            min_value=0,
            value=0,
            step=1,
            help=QUANTITY_FIELD_HELP,
        )
        reason = st.text_input(
            REASON_FIELD_LABEL,
            placeholder=REASON_ENTRY_PLACEHOLDER,
            help=REASON_FIELD_HELP,
        )
        cost = st.text_input(
            COST_FIELD_LABEL,
            placeholder=COST_FIELD_PLACEHOLDER,
            help=COST_FIELD_HELP,
        )
        submitted = st.form_submit_button(SUBMIT_LABEL)

    if not submitted:
        return None

    try:
        return ClosedEntryPayload(
            product_id=product.id,
            presentation_id=_required_presentation(presentation).id,
            quantity=parse_quantity(CLOSED_QUANTITY_LABEL, quantity),
            reason=parse_required_text(REASON_FIELD_LABEL, reason),
            cost=parse_optional_money(COST_FIELD_LABEL, cost),
        )
    except OperationValidationError as error:
        render_validation(str(error))
        return None


def render_closed_exit_form(
    product: ProductView,
    presentations: Sequence[PresentationView],
) -> ClosedExitPayload | None:
    """
    Muestra el formulario de una salida de presentaciones cerradas (T049).

    El selector solo ofrece presentaciones con equivalencia mayor que 1: la
    presentación base no representa una presentación cerrada. Si el producto
    solo tiene la unidad base, se informa el estado y no se muestra ningún
    formulario. El centinela del selector es propio de este formulario.

    Args:
        product: Producto de la salida propuesta.
        presentations: Presentaciones del producto ya consultadas.

    Returns:
        El payload validado, o `None` si no se envió, si la validación falló o
        si no hay presentaciones cerradas que ofrecer.
    """
    closed_presentations = [
        presentation
        for presentation in presentations
        if presentation.factor > 1
    ]
    if not closed_presentations:
        st.info(NO_CLOSED_EXIT_PRESENTATIONS_MESSAGE)
        return None

    with st.form(
        key=_form_key(ProposalAction.CLOSED_EXIT),
        clear_on_submit=True,
    ):
        st.caption(f"Producto: {product_option_label(product)}")
        presentation = _render_presentation_selector(
            closed_presentations,
            product_id=product.id,
            no_selection_label=CLOSED_PRESENTATION_NO_SELECTION_LABEL,
        )
        quantity = st.number_input(
            CLOSED_QUANTITY_LABEL,
            min_value=0,
            value=0,
            step=1,
            help=QUANTITY_FIELD_HELP,
        )
        reason = st.text_input(
            REASON_FIELD_LABEL,
            placeholder=REASON_EXIT_PLACEHOLDER,
            help=REASON_FIELD_HELP,
        )
        submitted = st.form_submit_button(SUBMIT_LABEL)

    if not submitted:
        return None

    try:
        return ClosedExitPayload(
            product_id=product.id,
            presentation_id=_required_presentation(presentation).id,
            quantity=parse_quantity(CLOSED_QUANTITY_LABEL, quantity),
            reason=parse_required_text(REASON_FIELD_LABEL, reason),
        )
    except OperationValidationError as error:
        render_validation(str(error))
        return None


def render_aperture_form(
    product: ProductView,
    presentations: Sequence[PresentationView],
) -> AperturePayload | None:
    """
    Muestra el formulario de una apertura de presentaciones (T049, RF-03).

    Args:
        product: Producto cuya presentación se abre.
        presentations: Presentaciones del producto ya consultadas.

    Returns:
        El payload validado, o `None` si no se envió o si la validación falló.
    """
    with st.form(
        key=_form_key(ProposalAction.APERTURE),
        clear_on_submit=True,
    ):
        st.caption(f"Producto: {product_option_label(product)}")
        presentation = _render_presentation_selector(
            presentations,
            product_id=product.id,
        )
        quantity = st.number_input(
            APERTURE_QUANTITY_LABEL,
            min_value=0,
            value=0,
            step=1,
            help=QUANTITY_FIELD_HELP,
        )
        reason = st.text_input(
            REASON_FIELD_LABEL,
            placeholder=REASON_APERTURE_PLACEHOLDER,
            help=REASON_FIELD_HELP,
        )
        submitted = st.form_submit_button(SUBMIT_LABEL)

    if not submitted:
        return None

    try:
        return AperturePayload(
            product_id=product.id,
            presentation_id=_required_presentation(presentation).id,
            quantity=parse_quantity(APERTURE_QUANTITY_LABEL, quantity),
            reason=parse_required_text(REASON_FIELD_LABEL, reason),
        )
    except OperationValidationError as error:
        render_validation(str(error))
        return None


def render_compensation_selector(
    product: ProductView,
    movements: Sequence[MovementOption],
) -> MovementOption | None:
    """
    Muestra el selector del movimiento que se va a compensar (T049, T054).

    Los movimientos llegan ya leídos y esta vista solo filtra por tipo: no
    calcula la cantidad compensable ni consulta la disponibilidad. El selector
    se muestra fuera del formulario para que `app/ui/main.py` pueda leer la
    disponibilidad del movimiento elegido y pasarla al formulario.

    Args:
        product: Producto al que pertenecen los movimientos.
        movements: Movimientos del producto ya consultados, en su orden.

    Returns:
        El movimiento elegido, o `None` mientras no hay selección o el producto
        no tiene movimientos compensables.
    """
    options = compensable_movements(movements)
    if not options:
        st.info(EMPTY_MOVEMENTS_MESSAGE)
        return None

    st.caption(f"Producto: {product_option_label(product)}")
    st.caption(COMPENSATION_NOTE)
    selected = _render_movement_selector(options, product_id=product.id)
    if selected is None:
        st.caption(NO_MOVEMENT_SELECTION_MESSAGE)

    return selected


def render_compensation_form(
    product: ProductView,
    movement: MovementOption,
    *,
    compensable_quantity: int | None,
) -> CompensationPayload | None:
    """
    Muestra el formulario de una compensación de un movimiento (T049, T054).

    La cantidad compensable y la disponibilidad no se calculan aquí: llegan ya
    leídas por `app/ui/main.py` desde la lectura pública del servicio de
    movimientos. Si esa lectura no está disponible, el formulario lo informa de
    forma explícita y no afirma que exista una cantidad compensable (D-D). La
    operación se revalida al confirmar.

    Args:
        product: Producto al que pertenece el movimiento.
        movement: Movimiento ya elegido por el usuario.
        compensable_quantity: Cantidad compensable pendiente ya leída, o
            `None` si la lectura pública no está disponible.

    Returns:
        El payload validado, o `None` si no se envió o si la validación falló.
    """
    with st.form(
        key=_form_key(ProposalAction.COMPENSATION),
        clear_on_submit=True,
    ):
        st.caption(f"Movimiento: {movement_option_label(movement)}")
        if compensable_quantity is None:
            st.caption(COMPENSATION_UNAVAILABLE_MESSAGE)
        else:
            st.caption(
                COMPENSABLE_QUANTITY_TEMPLATE.format(
                    quantity=compensable_quantity
                )
            )
        quantity = st.number_input(
            COMPENSATION_QUANTITY_LABEL,
            min_value=0,
            value=0,
            step=1,
            help=QUANTITY_FIELD_HELP,
        )
        reason = st.text_input(
            REASON_FIELD_LABEL,
            placeholder=REASON_COMPENSATION_PLACEHOLDER,
            help=REASON_FIELD_HELP,
        )
        submitted = st.form_submit_button(SUBMIT_LABEL)

    if not submitted:
        return None

    try:
        return CompensationPayload(
            product_id=product.id,
            movement_id=movement.id,
            quantity=parse_quantity(COMPENSATION_QUANTITY_LABEL, quantity),
            reason=parse_required_text(REASON_FIELD_LABEL, reason),
        )
    except OperationValidationError as error:
        render_validation(str(error))
        return None


def render_price_update_form(
    product: ProductView,
    presentations: Sequence[PresentationView],
) -> PriceUpdatePayload | None:
    """
    Muestra el formulario del cambio de precio de una presentación (T049).

    Args:
        product: Producto al que pertenece la presentación afectada.
        presentations: Presentaciones del producto ya consultadas.

    Returns:
        El payload validado, o `None` si no se envió o si la validación falló.
    """
    with st.form(
        key=_form_key(ProposalAction.PRICE_UPDATE),
        clear_on_submit=True,
    ):
        st.caption(f"Producto: {product_option_label(product)}")
        presentation = _render_presentation_selector(
            presentations,
            product_id=product.id,
        )
        new_price = st.text_input(
            NEW_PRICE_LABEL,
            placeholder=NEW_PRICE_PLACEHOLDER,
            help=NEW_PRICE_FIELD_HELP,
        )
        submitted = st.form_submit_button(SUBMIT_LABEL)

    if not submitted:
        return None

    try:
        return PriceUpdatePayload(
            presentation_id=_required_presentation(presentation).id,
            new_price=parse_money(NEW_PRICE_LABEL, new_price),
        )
    except OperationValidationError as error:
        render_validation(str(error))
        return None


def render_created_proposal(action: ProposalAction) -> None:
    """
    Avisa que la propuesta se creó y que no se escribió nada (T049, C11).

    Args:
        action: Acción de la propuesta pendiente creada.
    """
    st.success(PROPOSAL_CREATED_TEMPLATE.format(action=action_label(action)))


def action_label(action: ProposalAction) -> str:
    """
    Devuelve el nombre legible de una acción de propuesta (T049).

    Args:
        action: Acción de la propuesta.

    Returns:
        El nombre con el que la sección presenta la acción.
    """
    return ACTION_LABELS.get(action.value, action.value)


def compensable_movements(
    movements: Sequence[MovementOption],
) -> list[MovementOption]:
    """
    Filtra los movimientos cuyo tipo admite una compensación (T049, RF-03).

    El filtro es estructural —el tipo del movimiento—: no evalúa la cantidad
    compensable, la disponibilidad ni el stock, que se revalidan al confirmar.

    Args:
        movements: Movimientos del producto, en su orden.

    Returns:
        Los movimientos compensables, conservando el orden recibido.
    """
    return [
        movement
        for movement in movements
        if movement.kind in COMPENSABLE_MOVEMENT_KINDS
    ]


def movement_option_label(movement: MovementOption) -> str:
    """
    Construye la opción del selector de movimiento de una compensación (T049).

    Args:
        movement: Movimiento ofrecido en el selector.

    Returns:
        El texto de la opción, con su identificador, su tipo, su cantidad y su
        fecha local.
    """
    kind = MOVEMENT_KIND_LABELS.get(movement.kind, movement.kind)

    return (
        f"#{movement.id} — {kind} — {movement.quantity} — "
        f"{format_instant(movement.occurred_at)}"
    )


def _form_key(action: ProposalAction) -> str:
    """Devuelve la clave estable del formulario de una acción (T049)."""
    return FORM_KEY_TEMPLATE.format(action=action.value)


def _requests_initial_presentation(
    *,
    name: str,
    price: str,
    factor: int,
    closed_stock: int,
    cost: str,
) -> bool:
    """
    Indica si el alta declara una presentación inicial opcional (T049).

    El bloque es opcional: se considera declarado cuando se informó el nombre,
    el precio, un costo, un factor distinto de 1 o un stock cerrado inicial.

    Args:
        name: Texto del nombre de la presentación inicial.
        price: Texto del precio de venta de la presentación inicial.
        factor: Factor elegido para la presentación inicial.
        closed_stock: Stock cerrado inicial elegido.
        cost: Texto del costo total de la presentación inicial.

    Returns:
        `True` si el bloque opcional se debe declarar en el payload.
    """
    return bool(
        name.strip()
        or price.strip()
        or cost.strip()
        or factor != 1
        or closed_stock != 0
    )


def _parse_decimal(field: str, text: str) -> Decimal:
    """
    Interpreta un texto como `Decimal` exacto y finito (T049).

    Args:
        field: Etiqueta del campo, para el mensaje de validación.
        text: Texto ya recortado que representa el importe.

    Returns:
        El importe exacto.

    Raises:
        OperationValidationError: Si el texto no es un decimal válido o no es
            finito.
    """
    try:
        amount = Decimal(text)
    except InvalidOperation as error:
        raise OperationValidationError(
            INVALID_AMOUNT_TEMPLATE.format(field=field)
        ) from error
    if not amount.is_finite():
        raise OperationValidationError(
            INVALID_AMOUNT_TEMPLATE.format(field=field)
        )

    return amount


def _presentation_option_label(presentation: PresentationView) -> str:
    """Construye la opción del selector de presentación de un formulario."""
    return (
        f"{single_line(presentation.name)} "
        f"(equivalencia {presentation.factor})"
    )


def _render_presentation_selector(
    presentations: Sequence[PresentationView],
    *,
    product_id: int,
    label: str = PRESENTATION_SELECTOR_LABEL,
    no_selection_label: str = NO_SELECTION_LABEL,
) -> PresentationView | None:
    """
    Muestra el selector de presentación del producto elegido (T049).

    Args:
        presentations: Presentaciones del producto ya consultadas.
        product_id: Producto cuyas presentaciones se ofrecen, para construir la
            clave del widget.
        label: Etiqueta visible del selector; por defecto, la compartida.
        no_selection_label: Opción centinela que representa la ausencia de
            selección; por defecto, la compartida con los demás formularios.

    Returns:
        La presentación elegida, o `None` mientras se mantiene la opción
        centinela.
    """
    options = [
        no_selection_label,
        *(_presentation_option_label(item) for item in presentations),
    ]
    selected = st.selectbox(
        label,
        options,
        index=0,
        key=PRESENTATION_SELECTOR_KEY_TEMPLATE.format(product_id=product_id),
        help=PRESENTATION_SELECTOR_HELP,
    )
    if selected == no_selection_label:
        return None

    return presentations[options.index(selected) - 1]


def _render_movement_selector(
    movements: Sequence[MovementOption],
    *,
    product_id: int,
) -> MovementOption | None:
    """
    Muestra el selector de movimiento compensable del producto (T049).

    Args:
        movements: Movimientos compensables ya leídos, en su orden.
        product_id: Producto cuyos movimientos se ofrecen, para construir la
            clave del widget.

    Returns:
        El movimiento elegido, o `None` mientras se mantiene la opción
        centinela.
    """
    options = [
        NO_SELECTION_LABEL,
        *(movement_option_label(movement) for movement in movements),
    ]
    selected = st.selectbox(
        MOVEMENT_SELECTOR_LABEL,
        options,
        index=0,
        key=MOVEMENT_SELECTOR_KEY_TEMPLATE.format(product_id=product_id),
        help=MOVEMENT_SELECTOR_HELP,
    )
    if selected == NO_SELECTION_LABEL:
        return None

    return movements[options.index(selected) - 1]


def _required_presentation(
    presentation: PresentationView | None,
) -> PresentationView:
    """
    Exige una presentación elegida en el formulario (T049).

    Raises:
        OperationValidationError: Si no se eligió ninguna presentación.
    """
    if presentation is None:
        raise OperationValidationError(
            MISSING_TEXT_TEMPLATE.format(field=PRESENTATION_SELECTOR_LABEL)
        )

    return presentation
