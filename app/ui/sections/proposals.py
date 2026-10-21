"""Vista de las propuestas pendientes de la interfaz (T041, T050, T051, RF-09).

Renderiza la sección «Propuestas pendientes» de la navegación lateral: un
encabezado, un estado vacío explícito y, por cada propuesta, su acción en
lenguaje legible y sus campos relevantes (C13). Para un cambio de precio
muestra la presentación afectada, su precio vigente y el precio nuevo; la
presentación llega ya resuelta por `app/ui/main.py`, que es el único punto que
consulta su precio.

Una propuesta pendiente se puede **editar** desde su propio formulario (T051):
los campos se prellenan con los valores actuales y el envío construye un
payload nuevo con la misma acción y los mismos destinos. Editar no escribe el
inventario: la vista solo entrega el payload a un callback y no invoca el
servicio de propuestas. La confirmación explícita del usuario permanece en el
**único** punto de `app/ui/main.py` (D-A): este módulo no confirma, no
descarta, no escribe en el estado de la sesión y no abre conexiones ni
transacciones.

La vista no accede a la persistencia, a los servicios ni al dominio: recibe las
propuestas ya preparadas por la sesión —esquemas Pydantic de
`app/schemas/proposals.py`— y, para el cambio de precio, su precio vigente ya
leído. Un valor ausente se informa como `no informado` y nunca como `None` ni
como un cero inventado; un cero real se conserva (C4). Las etiquetas de las
acciones y los parsers de los formularios se reutilizan de
`app.ui.sections.operations` —una dependencia unidireccional— y los valores se
formatean con `app.ui.formatting`, sin duplicar ninguno de esos contratos.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

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
    Proposal,
    ProposalPayload,
    ProposalStatus,
)
from app.ui.formatting import (
    ABSENT_MARKER,
    format_money,
    format_quantity,
    single_line,
)
from app.ui.sections.operations import (
    ACTION_LABELS,
    UPDATE_WITHOUT_CHANGE_MESSAGE,
    OperationValidationError,
    parse_factor,
    parse_money,
    parse_optional_count,
    parse_optional_money,
    parse_optional_text,
    parse_quantity,
    parse_required_text,
)

PENDING_PROPOSALS_HEADER: str = "Propuestas pendientes"
# Encabezado de la sección de propuestas pendientes (T041).

PENDING_PROPOSALS_CAPTION: str = (
    "Las propuestas se pueden editar antes de confirmarlas."
)
# Nota breve que aclara el alcance de la sección (T051).

EMPTY_PROPOSALS_MESSAGE: str = (
    "No hay propuestas pendientes: todavía no se preparó ninguna operación."
)
# Estado vacío explícito de la sección, sin inventar valores (T041, C4).

PENDING_PROPOSAL_NOTE: str = (
    "La propuesta queda pendiente: no se ejecutó ninguna operación y la "
    "confirmación es tuya."
)
# Aviso de la propuesta pendiente: nada se ejecutó todavía (D5). Vive aquí para
# que la conversación y la sección compartan un único texto (T050).

NO_INITIAL_PRESENTATIONS_MESSAGE: str = (
    "Presentaciones iniciales: ninguna declarada"
)
# Estado explícito del alta sin presentaciones iniciales: no se muestra un 0
# que podría confundirse con una presentación declarada (T050, C4).

MISSING_PRESENTATION_TEMPLATE: str = (
    "no existe la presentación con id {presentation_id}"
)
# Motivo explícito del cambio de precio sin precio vigente inventado (T050).

ACTION_FIELD_LABEL: str = "Acción"
# Etiqueta del nombre legible de la acción propuesta.

STATUS_FIELD_LABEL: str = "Estado"
# Etiqueta del estado real de la propuesta.

PRODUCT_ID_TEMPLATE: str = "Producto (id {product_id})"
# Identificador etiquetado del producto, sin resolver su nombre (C13).

PRESENTATION_ID_TEMPLATE: str = "Presentación (id {presentation_id})"
# Identificador etiquetado de la presentación, sin resolver su nombre (C13).

MOVEMENT_ID_TEMPLATE: str = "Movimiento (id {movement_id})"
# Identificador etiquetado del movimiento compensado (C13).

PRESENTATION_DETAIL_TEMPLATE: str = (
    "{name} (id {presentation_id}, factor {factor})"
)
# Presentación del cambio de precio con su nombre, su id y su factor.

INITIAL_PRESENTATION_TEMPLATE: str = (
    "{name} (factor {factor}, precio {price}, stock cerrado {stock})"
)
# Presentación inicial declarada en el alta, con sus valores exactos.

SKU_FIELD_LABEL: str = "SKU"
# Etiqueta del SKU del alta de producto.

NAME_FIELD_LABEL: str = "Nombre"
# Etiqueta del nombre del producto o de la presentación.

BASE_UNIT_FIELD_LABEL: str = "Unidad base"
# Etiqueta de la unidad base del alta de producto.

BASE_PRICE_FIELD_LABEL: str = "Precio de venta base"
# Etiqueta del precio de la presentación base.

MINIMUM_STOCK_FIELD_LABEL: str = "Stock mínimo"
# Etiqueta del stock mínimo del producto.

INITIAL_LOOSE_UNITS_FIELD_LABEL: str = "Unidades sueltas iniciales"
# Etiqueta del stock suelto inicial del alta de producto.

BASE_PRESENTATION_NAME_FIELD_LABEL: str = "Nombre de la presentación base"
# Etiqueta del nombre opcional de la presentación base.

INITIAL_PRESENTATIONS_FIELD_LABEL: str = "Presentación inicial"
# Etiqueta de cada presentación declarada en el alta.

NEW_NAME_FIELD_LABEL: str = "Nombre nuevo"
# Etiqueta del nombre que propone la modificación de producto.

NEW_MINIMUM_STOCK_FIELD_LABEL: str = "Stock mínimo nuevo"
# Etiqueta del stock mínimo que propone la modificación.

FACTOR_FIELD_LABEL: str = "Factor"
# Etiqueta del factor de una presentación.

SALE_PRICE_FIELD_LABEL: str = "Precio de venta"
# Etiqueta del precio de venta de una presentación nueva.

QUANTITY_FIELD_LABEL: str = "Cantidad"
# Etiqueta de la cantidad de un movimiento o de una apertura.

REASON_FIELD_LABEL: str = "Motivo"
# Etiqueta del motivo de un movimiento o de una apertura.

COST_FIELD_LABEL: str = "Costo"
# Etiqueta del costo opcional de una entrada.

PRICE_PRESENTATION_FIELD_LABEL: str = "presentación"
# Etiqueta de la presentación afectada por el cambio de precio; se conserva en
# minúsculas porque la descripción de la conversación ya la usa así (T035).

CURRENT_PRICE_FIELD_LABEL: str = "precio vigente"
# Etiqueta del precio vigente antes del cambio.

NEW_PRICE_FIELD_LABEL: str = "precio nuevo"
# Etiqueta del precio propuesto por el cambio.

CLOSED_STOCK_FIELD_LABEL: str = "Stock cerrado"
# Etiqueta del stock cerrado de una presentación inicial editable (T051).

EDIT_EXPANDER_TEMPLATE: str = "Editar propuesta {index}"
# Rótulo del formulario de edición de cada propuesta pendiente (T051).

EDIT_SUBMIT_LABEL: str = "Guardar cambios"
# Botón que envía el formulario de edición de una propuesta (T051).

PROPOSAL_EDIT_ERROR_PREFIX: str = "No se pudo editar la propuesta: "
# Prefijo de los errores devueltos por la edición de una propuesta (T051).


@dataclass(frozen=True)
class PresentationPriceView:
    """
    Presentación afectada por un cambio de precio, ya resuelta (T050).

    Es un valor de presentación: la vista no consulta el inventario. El precio
    vigente llega como `Decimal` exacto y solo se formatea al mostrarlo.
    """

    id: int
    """Identificador de la presentación."""

    name: str
    """Nombre de la presentación, tal como lo devolvió la lectura."""

    factor: int
    """Equivalencia de la presentación en unidades base."""

    sale_price: Decimal
    """Precio de venta vigente, sin formatear."""


@dataclass(frozen=True)
class ProposalReview:
    """
    Revisión legible de una propuesta pendiente (T050, C13).

    La presentación solo se informa para el cambio de precio; en las demás
    acciones queda en `None` porque la vista no resuelve nombres ni registros
    adicionales. `key` identifica la propuesta por objeto, no por igualdad de
    Pydantic: dos propuestas idénticas conservan claves de formulario distintas
    (T051).
    """

    proposal: Proposal
    """Propuesta real, pendiente o confirmada, ya preparada por la sesión."""

    key: int
    """Identidad de la propuesta, asignada por `id(proposal)` en la sesión."""

    presentation: PresentationPriceView | None = None
    """Precio vigente de la presentación afectada, o `None` si no aplica."""


ProposalEditCallback = Callable[[int, ProposalPayload], str | None]
"""Entrega la identidad y el payload editado; devuelve un error o `None`."""


def describe_price_update_fields(
    proposal: Proposal,
    presentation: PresentationPriceView | None,
) -> tuple[str, ...]:
    """
    Describe el detalle de un cambio de precio con sus precios (T050, C13).

    Las líneas se comparten con la descripción de la conversación (T035), de
    modo que ambos puntos muestren el mismo detalle. Si la presentación no se
    pudo resolver, se informa el motivo explícito y **no** se muestra un precio
    vigente inventado.

    Args:
        proposal: Propuesta de cambio de precio.
        presentation: Presentación afectada ya resuelta, o `None` si no existe.

    Returns:
        Las líneas `presentación`, `precio vigente` y `precio nuevo`, sin
        viñeta, listas para presentarse.

    Raises:
        TypeError: Si la propuesta no es un cambio de precio.
    """
    payload = proposal.payload
    if not isinstance(payload, PriceUpdatePayload):
        raise TypeError(
            "El detalle del cambio de precio requiere una propuesta de "
            "cambio de precio."
        )

    lines: list[str] = []
    if presentation is None:
        lines.append(
            f"{PRICE_PRESENTATION_FIELD_LABEL}: "
            + MISSING_PRESENTATION_TEMPLATE.format(
                presentation_id=payload.presentation_id
            )
        )
    else:
        lines.append(
            f"{PRICE_PRESENTATION_FIELD_LABEL}: "
            + PRESENTATION_DETAIL_TEMPLATE.format(
                name=_text(presentation.name),
                presentation_id=presentation.id,
                factor=format_quantity(presentation.factor),
            )
        )
        lines.append(
            f"{CURRENT_PRICE_FIELD_LABEL}: "
            f"{format_money(presentation.sale_price)}"
        )
    lines.append(
        f"{NEW_PRICE_FIELD_LABEL}: {format_money(payload.new_price)}"
    )

    return tuple(lines)


def describe_proposal_review(
    review: ProposalReview,
) -> tuple[str, ...]:
    """
    Describe una propuesta pendiente con su acción y sus campos (T050, C13).

    La acción se presenta con su nombre legible y su identificador, y cada
    campo con su etiqueta: los valores ausentes se muestran como ausentes y los
    ceros reales se conservan. Para el cambio de precio el detalle se delega en
    `describe_price_update_fields`.

    Args:
        review: Revisión con la propuesta y, si corresponde, la presentación
            afectada ya resuelta.

    Returns:
        Las líneas legibles de la propuesta, empezando por la acción y el
        estado.
    """
    proposal = review.proposal
    lines = [
        f"{ACTION_FIELD_LABEL}: {_action_label(proposal.action.value)} "
        f"(`{proposal.action.value}`)",
        f"{STATUS_FIELD_LABEL}: `{proposal.status.value}`",
    ]
    if isinstance(proposal.payload, PriceUpdatePayload):
        lines.extend(
            describe_price_update_fields(proposal, review.presentation)
        )
    else:
        lines.extend(_payload_lines(proposal.payload))

    return tuple(lines)


def render_pending_proposals(
    reviews: Sequence[ProposalReview],
    *,
    on_edit: ProposalEditCallback,
) -> None:
    """
    Muestra las propuestas pendientes con su revisión y su edición (T041,
    T050, T051).

    Cada propuesta pendiente conserva su revisión legible y, debajo, ofrece un
    formulario de edición con los valores actuales. El formulario no cambia la
    acción ni los destinos: solo entrega el payload editado al callback
    `on_edit`. Una propuesta confirmada se muestra sin formulario, porque no se
    puede editar (C14).

    Args:
        reviews: Revisiones ya preparadas por la sesión, en el orden en que se
            crearon. La vista no las transforma, no las confirma y no escribe
            en el inventario.
        on_edit: Callback que aplica la edición en la sesión y devuelve el
            motivo de un error, o `None` si la propuesta se reemplazó.
    """
    st.header(PENDING_PROPOSALS_HEADER)
    with st.container(border=True):
        st.caption(PENDING_PROPOSALS_CAPTION)
        if not reviews:
            st.info(EMPTY_PROPOSALS_MESSAGE)
            return

        for index, review in enumerate(reviews, start=1):
            lines = describe_proposal_review(review)
            st.markdown(f"{index}. {lines[0]}")
            for line in lines[1:]:
                st.markdown(f"- {line}")

            if review.proposal.status is not ProposalStatus.PENDING:
                continue

            with st.expander(
                EDIT_EXPANDER_TEMPLATE.format(index=index)
            ):
                _render_edit_form(review, on_edit)


def _render_edit_form(
    review: ProposalReview,
    on_edit: ProposalEditCallback,
) -> None:
    """
    Renderiza el formulario de edición y aplica el payload enviado (T051).

    La validación del formulario se resuelve antes del callback: un valor
    inválido se muestra con `st.error` y no reemplaza la propuesta. Un error
    devuelto por el callback —por ejemplo, una propuesta que dejó de estar
    pendiente— también se muestra y no reemplaza nada. En éxito, la vista pide
    un rerun para presentar la propuesta actualizada.

    Args:
        review: Revisión de la propuesta que se edita.
        on_edit: Callback que aplica la edición en la sesión.
    """
    try:
        payload = _edited_payload(review.key, review.proposal.payload)
    except OperationValidationError as error:
        st.error(str(error))
        return

    if payload is None:
        return

    error_message = on_edit(review.key, payload)
    if error_message is not None:
        st.error(f"{PROPOSAL_EDIT_ERROR_PREFIX}{error_message}")
        return

    st.rerun()


def _edited_payload(
    key: int,
    payload: ProposalPayload,
) -> ProposalPayload | None:
    """
    Muestra el formulario propio del payload y devuelve su edición (T051).

    Cada acción conserva sus destinos: el producto, la presentación y el
    movimiento se toman del payload original y no se ofrecen como campos. El
    formulario solo se construye al enviarlo; mientras tanto devuelve `None`.

    Args:
        key: Identidad de la propuesta, usada para claves únicas de widgets.
        payload: Payload vigente de la propuesta pendiente.

    Returns:
        El payload editado y validado, o `None` si el formulario no se envió.

    Raises:
        OperationValidationError: Si algún campo del formulario no es válido.
        TypeError: Si la acción no tiene un formulario de edición.
    """
    if isinstance(payload, ProductRegistrationPayload):
        return _edit_product_registration(key, payload)
    if isinstance(payload, ProductUpdatePayload):
        return _edit_product_update(key, payload)
    if isinstance(payload, PresentationRegistrationPayload):
        return _edit_presentation_registration(key, payload)
    if isinstance(payload, LooseEntryPayload):
        return _edit_loose_entry(key, payload)
    if isinstance(payload, ClosedEntryPayload):
        return _edit_closed_entry(key, payload)
    if isinstance(payload, LooseExitPayload):
        return _edit_loose_exit(key, payload)
    if isinstance(payload, ClosedExitPayload):
        return _edit_closed_exit(key, payload)
    if isinstance(payload, AperturePayload):
        return _edit_aperture(key, payload)
    if isinstance(payload, CompensationPayload):
        return _edit_compensation(key, payload)
    if isinstance(payload, PriceUpdatePayload):
        return _edit_price_update(key, payload)

    raise TypeError(
        "La acción de la propuesta no tiene un formulario de edición: "
        f"{payload.action}."
    )


def _edit_product_registration(
    key: int,
    payload: ProductRegistrationPayload,
) -> ProductRegistrationPayload | None:
    """Edita los campos del alta de producto, incluidas sus presentaciones."""
    with st.form(key=_form_key(key), clear_on_submit=False):
        sku = st.text_input(
            SKU_FIELD_LABEL,
            value=payload.sku,
            key=_field_key(key, "sku"),
        )
        name = st.text_input(
            NAME_FIELD_LABEL,
            value=payload.name,
            key=_field_key(key, "name"),
        )
        base_unit = st.text_input(
            BASE_UNIT_FIELD_LABEL,
            value=payload.base_unit,
            key=_field_key(key, "base_unit"),
        )
        base_price = st.text_input(
            BASE_PRICE_FIELD_LABEL,
            value=_money_text(payload.base_sale_price),
            key=_field_key(key, "base_sale_price"),
        )
        minimum_stock = st.number_input(
            MINIMUM_STOCK_FIELD_LABEL,
            min_value=0,
            value=payload.minimum_stock,
            step=1,
            key=_field_key(key, "minimum_stock"),
        )
        initial_loose_units = st.number_input(
            INITIAL_LOOSE_UNITS_FIELD_LABEL,
            min_value=0,
            value=payload.initial_loose_units,
            step=1,
            key=_field_key(key, "initial_loose_units"),
        )
        base_presentation_name = st.text_input(
            BASE_PRESENTATION_NAME_FIELD_LABEL,
            value=payload.base_presentation_name or "",
            key=_field_key(key, "base_presentation_name"),
        )
        presentation_fields = [
            _edit_initial_presentation_fields(key, position, item)
            for position, item in enumerate(
                payload.presentations, start=1
            )
        ]
        submitted = st.form_submit_button(
            EDIT_SUBMIT_LABEL,
            key=_submit_key(key),
        )

    if not submitted:
        return None

    presentations = tuple(
        InitialPresentationPayload(
            name=parse_required_text(
                INITIAL_PRESENTATIONS_FIELD_LABEL,
                field_name,
            ),
            factor=parse_factor(factor),
            sale_price=parse_money(SALE_PRICE_FIELD_LABEL, price),
            closed_stock=closed_stock,
        )
        for field_name, factor, price, closed_stock in presentation_fields
    )

    return ProductRegistrationPayload(
        sku=parse_required_text(SKU_FIELD_LABEL, sku),
        name=parse_required_text(NAME_FIELD_LABEL, name),
        base_unit=parse_required_text(BASE_UNIT_FIELD_LABEL, base_unit),
        base_sale_price=parse_money(BASE_PRICE_FIELD_LABEL, base_price),
        minimum_stock=minimum_stock,
        initial_loose_units=initial_loose_units,
        presentations=presentations,
        base_presentation_name=parse_optional_text(base_presentation_name),
    )


def _edit_initial_presentation_fields(
    key: int,
    position: int,
    item: InitialPresentationPayload,
) -> tuple[str, int, str, int]:
    """
    Renderiza los campos de una presentación inicial existente (T051).

    Edita solo los campos de la presentación y conserva la cantidad y el orden:
    no se añaden ni se eliminan presentaciones desde aquí.

    Args:
        key: Identidad de la propuesta.
        position: Posición de la presentación dentro del alta, desde 1.
        item: Presentación declarada en el payload vigente.

    Returns:
        Los valores sin validar: nombre, factor, precio como texto y stock
        cerrado.
    """
    st.caption(f"{INITIAL_PRESENTATIONS_FIELD_LABEL} {position}")
    name = st.text_input(
        NAME_FIELD_LABEL,
        value=item.name,
        key=_presentation_field_key(key, position, "name"),
    )
    factor = st.number_input(
        FACTOR_FIELD_LABEL,
        min_value=0,
        value=item.factor,
        step=1,
        key=_presentation_field_key(key, position, "factor"),
    )
    sale_price = st.text_input(
        SALE_PRICE_FIELD_LABEL,
        value=_money_text(item.sale_price),
        key=_presentation_field_key(key, position, "sale_price"),
    )
    closed_stock = st.number_input(
        CLOSED_STOCK_FIELD_LABEL,
        min_value=0,
        value=item.closed_stock,
        step=1,
        key=_presentation_field_key(key, position, "closed_stock"),
    )

    return name, factor, sale_price, closed_stock


def _edit_product_update(
    key: int,
    payload: ProductUpdatePayload,
) -> ProductUpdatePayload | None:
    """Edita el nombre y el stock mínimo, exigiendo al menos un cambio."""
    with st.form(key=_form_key(key), clear_on_submit=False):
        new_name = st.text_input(
            NEW_NAME_FIELD_LABEL,
            value=payload.name or "",
            key=_field_key(key, "name"),
        )
        new_minimum_stock = st.text_input(
            NEW_MINIMUM_STOCK_FIELD_LABEL,
            value=_optional_count_text(payload.minimum_stock),
            key=_field_key(key, "minimum_stock"),
        )
        submitted = st.form_submit_button(
            EDIT_SUBMIT_LABEL,
            key=_submit_key(key),
        )

    if not submitted:
        return None

    name = parse_optional_text(new_name)
    minimum_stock = parse_optional_count(
        MINIMUM_STOCK_FIELD_LABEL,
        new_minimum_stock,
    )
    if name is None and minimum_stock is None:
        raise OperationValidationError(UPDATE_WITHOUT_CHANGE_MESSAGE)

    return ProductUpdatePayload(
        product_id=payload.product_id,
        name=name,
        minimum_stock=minimum_stock,
    )


def _edit_presentation_registration(
    key: int,
    payload: PresentationRegistrationPayload,
) -> PresentationRegistrationPayload | None:
    """Edita el nombre, el factor y el precio de una presentación nueva."""
    with st.form(key=_form_key(key), clear_on_submit=False):
        name = st.text_input(
            NAME_FIELD_LABEL,
            value=payload.name,
            key=_field_key(key, "name"),
        )
        factor = st.number_input(
            FACTOR_FIELD_LABEL,
            min_value=0,
            value=payload.factor,
            step=1,
            key=_field_key(key, "factor"),
        )
        sale_price = st.text_input(
            SALE_PRICE_FIELD_LABEL,
            value=_money_text(payload.sale_price),
            key=_field_key(key, "sale_price"),
        )
        submitted = st.form_submit_button(
            EDIT_SUBMIT_LABEL,
            key=_submit_key(key),
        )

    if not submitted:
        return None

    return PresentationRegistrationPayload(
        product_id=payload.product_id,
        name=parse_required_text(NAME_FIELD_LABEL, name),
        factor=parse_factor(factor),
        sale_price=parse_money(SALE_PRICE_FIELD_LABEL, sale_price),
    )


def _edit_loose_entry(
    key: int,
    payload: LooseEntryPayload,
) -> LooseEntryPayload | None:
    """Edita la cantidad, el motivo y el costo de una entrada suelta."""
    with st.form(key=_form_key(key), clear_on_submit=False):
        quantity = _quantity_input(key, payload.quantity)
        reason = st.text_input(
            REASON_FIELD_LABEL,
            value=payload.reason,
            key=_field_key(key, "reason"),
        )
        cost = st.text_input(
            COST_FIELD_LABEL,
            value=_money_text(payload.cost),
            key=_field_key(key, "cost"),
        )
        submitted = st.form_submit_button(
            EDIT_SUBMIT_LABEL,
            key=_submit_key(key),
        )

    if not submitted:
        return None

    return LooseEntryPayload(
        product_id=payload.product_id,
        quantity=parse_quantity(QUANTITY_FIELD_LABEL, quantity),
        reason=parse_required_text(REASON_FIELD_LABEL, reason),
        cost=parse_optional_money(COST_FIELD_LABEL, cost),
    )


def _edit_closed_entry(
    key: int,
    payload: ClosedEntryPayload,
) -> ClosedEntryPayload | None:
    """Edita una entrada cerrada conservando su presentación."""
    with st.form(key=_form_key(key), clear_on_submit=False):
        quantity = _quantity_input(key, payload.quantity)
        reason = st.text_input(
            REASON_FIELD_LABEL,
            value=payload.reason,
            key=_field_key(key, "reason"),
        )
        cost = st.text_input(
            COST_FIELD_LABEL,
            value=_money_text(payload.cost),
            key=_field_key(key, "cost"),
        )
        submitted = st.form_submit_button(
            EDIT_SUBMIT_LABEL,
            key=_submit_key(key),
        )

    if not submitted:
        return None

    return ClosedEntryPayload(
        product_id=payload.product_id,
        presentation_id=payload.presentation_id,
        quantity=parse_quantity(QUANTITY_FIELD_LABEL, quantity),
        reason=parse_required_text(REASON_FIELD_LABEL, reason),
        cost=parse_optional_money(COST_FIELD_LABEL, cost),
    )


def _edit_loose_exit(
    key: int,
    payload: LooseExitPayload,
) -> LooseExitPayload | None:
    """Edita la cantidad y el motivo de una salida suelta."""
    fields = _edit_movement_fields(key, payload.quantity, payload.reason)
    if fields is None:
        return None

    quantity, reason = fields

    return LooseExitPayload(
        product_id=payload.product_id,
        quantity=parse_quantity(QUANTITY_FIELD_LABEL, quantity),
        reason=parse_required_text(REASON_FIELD_LABEL, reason),
    )


def _edit_closed_exit(
    key: int,
    payload: ClosedExitPayload,
) -> ClosedExitPayload | None:
    """Edita una salida cerrada conservando su presentación."""
    fields = _edit_movement_fields(key, payload.quantity, payload.reason)
    if fields is None:
        return None

    quantity, reason = fields

    return ClosedExitPayload(
        product_id=payload.product_id,
        presentation_id=payload.presentation_id,
        quantity=parse_quantity(QUANTITY_FIELD_LABEL, quantity),
        reason=parse_required_text(REASON_FIELD_LABEL, reason),
    )


def _edit_aperture(
    key: int,
    payload: AperturePayload,
) -> AperturePayload | None:
    """Edita una apertura conservando su presentación."""
    fields = _edit_movement_fields(key, payload.quantity, payload.reason)
    if fields is None:
        return None

    quantity, reason = fields

    return AperturePayload(
        product_id=payload.product_id,
        presentation_id=payload.presentation_id,
        quantity=parse_quantity(QUANTITY_FIELD_LABEL, quantity),
        reason=parse_required_text(REASON_FIELD_LABEL, reason),
    )


def _edit_compensation(
    key: int,
    payload: CompensationPayload,
) -> CompensationPayload | None:
    """Edita una compensación conservando su movimiento original."""
    fields = _edit_movement_fields(key, payload.quantity, payload.reason)
    if fields is None:
        return None

    quantity, reason = fields

    return CompensationPayload(
        product_id=payload.product_id,
        movement_id=payload.movement_id,
        quantity=parse_quantity(QUANTITY_FIELD_LABEL, quantity),
        reason=parse_required_text(REASON_FIELD_LABEL, reason),
    )


def _edit_movement_fields(
    key: int,
    quantity: int,
    reason: str,
) -> tuple[int, str] | None:
    """
    Renderiza los campos comunes de un movimiento y devuelve sus valores.

    Args:
        key: Identidad de la propuesta.
        quantity: Cantidad vigente del payload.
        reason: Motivo vigente del payload.

    Returns:
        La cantidad y el motivo sin validar, o `None` si el formulario no se
        envió.
    """
    with st.form(key=_form_key(key), clear_on_submit=False):
        edited_quantity = _quantity_input(key, quantity)
        edited_reason = st.text_input(
            REASON_FIELD_LABEL,
            value=reason,
            key=_field_key(key, "reason"),
        )
        submitted = st.form_submit_button(
            EDIT_SUBMIT_LABEL,
            key=_submit_key(key),
        )

    if not submitted:
        return None

    return edited_quantity, edited_reason


def _edit_price_update(
    key: int,
    payload: PriceUpdatePayload,
) -> PriceUpdatePayload | None:
    """Edita el precio nuevo conservando la presentación afectada."""
    with st.form(key=_form_key(key), clear_on_submit=False):
        new_price = st.text_input(
            NEW_PRICE_FIELD_LABEL,
            value=_money_text(payload.new_price),
            key=_field_key(key, "new_price"),
        )
        submitted = st.form_submit_button(
            EDIT_SUBMIT_LABEL,
            key=_submit_key(key),
        )

    if not submitted:
        return None

    return PriceUpdatePayload(
        presentation_id=payload.presentation_id,
        new_price=parse_money(NEW_PRICE_FIELD_LABEL, new_price),
    )


def _quantity_input(key: int, value: int) -> int:
    """
    Renderiza una cantidad entera dentro del formulario de edición (T051).

    Args:
        key: Identidad de la propuesta.
        value: Cantidad vigente del payload.

    Returns:
        La cantidad informada, sin validar todavía.
    """
    return st.number_input(
        QUANTITY_FIELD_LABEL,
        min_value=0,
        value=value,
        step=1,
        key=_field_key(key, "quantity"),
    )


def _form_key(key: int) -> str:
    """Devuelve la clave única del formulario de una propuesta (T051)."""
    return f"proposal_edit_{key}_form"


def _submit_key(key: int) -> str:
    """Devuelve la clave única del botón de edición de una propuesta (T051)."""
    return f"proposal_edit_{key}_submit"


def _field_key(key: int, field: str) -> str:
    """Devuelve la clave única de un campo del formulario (T051)."""
    return f"proposal_edit_{key}_{field}"


def _presentation_field_key(
    key: int,
    position: int,
    field: str,
) -> str:
    """Devuelve la clave única de un campo de presentación inicial (T051)."""
    return f"proposal_edit_{key}_presentation_{position}_{field}"


def _money_text(value: Decimal | None) -> str:
    """
    Presenta un importe en el texto editable del formulario (T051).

    Un importe ausente queda vacío para conservar la diferencia con un cero
    real; el valor exacto no se redondea ni se convierte a `float`.
    """
    if value is None:
        return ""

    return str(value)


def _optional_count_text(value: int | None) -> str:
    """
    Presenta un entero opcional en el texto editable del formulario (T051).

    Un valor ausente queda vacío, de modo que volver a enviarlo lo conserve
    ausente en lugar de convertirlo en cero.
    """
    if value is None:
        return ""

    return str(value)


def _payload_lines(payload: ProposalPayload) -> tuple[str, ...]:
    """
    Describe los campos de una propuesta distinta del cambio de precio (T050).

    Solo se presentan los valores del payload: la vista no consulta nombres de
    productos, presentaciones ni movimientos, y muestra los identificadores
    etiquetados tal como llegaron.

    Args:
        payload: Payload tipado de la propuesta.

    Returns:
        Las líneas etiquetadas de sus campos.

    Raises:
        TypeError: Si el payload corresponde a una acción sin revisión legible.
    """
    if isinstance(payload, ProductRegistrationPayload):
        lines = [
            f"{SKU_FIELD_LABEL}: {_text(payload.sku)}",
            f"{NAME_FIELD_LABEL}: {_text(payload.name)}",
            f"{BASE_UNIT_FIELD_LABEL}: {_text(payload.base_unit)}",
            f"{BASE_PRICE_FIELD_LABEL}: "
            f"{format_money(payload.base_sale_price)}",
            f"{MINIMUM_STOCK_FIELD_LABEL}: "
            f"{format_quantity(payload.minimum_stock)}",
            f"{INITIAL_LOOSE_UNITS_FIELD_LABEL}: "
            f"{format_quantity(payload.initial_loose_units)}",
            f"{BASE_PRESENTATION_NAME_FIELD_LABEL}: "
            f"{_text(payload.base_presentation_name)}",
        ]
        if payload.presentations:
            lines.extend(
                f"{INITIAL_PRESENTATIONS_FIELD_LABEL}: "
                + INITIAL_PRESENTATION_TEMPLATE.format(
                    name=_text(item.name),
                    factor=format_quantity(item.factor),
                    price=format_money(item.sale_price),
                    stock=format_quantity(item.closed_stock),
                )
                for item in payload.presentations
            )
        else:
            lines.append(NO_INITIAL_PRESENTATIONS_MESSAGE)

        return tuple(lines)

    if isinstance(payload, ProductUpdatePayload):
        return (
            _product_line(payload.product_id),
            f"{NEW_NAME_FIELD_LABEL}: {_text(payload.name)}",
            f"{NEW_MINIMUM_STOCK_FIELD_LABEL}: "
            f"{format_quantity(payload.minimum_stock)}",
        )

    if isinstance(payload, PresentationRegistrationPayload):
        return (
            _product_line(payload.product_id),
            f"{NAME_FIELD_LABEL}: {_text(payload.name)}",
            f"{FACTOR_FIELD_LABEL}: {format_quantity(payload.factor)}",
            f"{SALE_PRICE_FIELD_LABEL}: "
            f"{format_money(payload.sale_price)}",
        )

    if isinstance(payload, LooseEntryPayload):
        return (
            _product_line(payload.product_id),
            f"{QUANTITY_FIELD_LABEL}: {format_quantity(payload.quantity)}",
            f"{REASON_FIELD_LABEL}: {_text(payload.reason)}",
            f"{COST_FIELD_LABEL}: {format_money(payload.cost)}",
        )

    if isinstance(payload, ClosedEntryPayload):
        return (
            _product_line(payload.product_id),
            _presentation_line(payload.presentation_id),
            f"{QUANTITY_FIELD_LABEL}: {format_quantity(payload.quantity)}",
            f"{REASON_FIELD_LABEL}: {_text(payload.reason)}",
            f"{COST_FIELD_LABEL}: {format_money(payload.cost)}",
        )

    if isinstance(payload, LooseExitPayload):
        return (
            _product_line(payload.product_id),
            f"{QUANTITY_FIELD_LABEL}: {format_quantity(payload.quantity)}",
            f"{REASON_FIELD_LABEL}: {_text(payload.reason)}",
        )

    if isinstance(payload, (ClosedExitPayload, AperturePayload)):
        return (
            _product_line(payload.product_id),
            _presentation_line(payload.presentation_id),
            f"{QUANTITY_FIELD_LABEL}: {format_quantity(payload.quantity)}",
            f"{REASON_FIELD_LABEL}: {_text(payload.reason)}",
        )

    if isinstance(payload, CompensationPayload):
        return (
            _product_line(payload.product_id),
            MOVEMENT_ID_TEMPLATE.format(movement_id=payload.movement_id),
            f"{QUANTITY_FIELD_LABEL}: {format_quantity(payload.quantity)}",
            f"{REASON_FIELD_LABEL}: {_text(payload.reason)}",
        )

    raise TypeError(
        "La acción de la propuesta no tiene una revisión legible: "
        f"{payload.action}."
    )


def _action_label(action: str) -> str:
    """Devuelve el nombre legible de una acción, o su valor si falta (T050)."""
    return ACTION_LABELS.get(action, action)


def _product_line(product_id: int) -> str:
    """Etiqueta el producto por su identificador, sin resolver su nombre."""
    return PRODUCT_ID_TEMPLATE.format(product_id=product_id)


def _presentation_line(presentation_id: int) -> str:
    """Etiqueta la presentación por su identificador, sin resolver su nombre."""
    return PRESENTATION_ID_TEMPLATE.format(
        presentation_id=presentation_id
    )


def _text(value: str | None) -> str:
    """
    Presenta un texto opcional como legible y nunca como `None` (T050, C4).

    Args:
        value: Texto del payload, o `None` si no se informó.

    Returns:
        El texto en una sola línea, o `ABSENT_MARKER` si falta o está vacío.
    """
    if value is None:
        return ABSENT_MARKER

    text = single_line(value).strip()

    return text or ABSENT_MARKER
