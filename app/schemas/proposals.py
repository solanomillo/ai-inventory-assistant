"""Esquemas Pydantic de las propuestas (T015 y T032, RF-05/RF-08).

Define las acciones y los estados de una propuesta, el payload de cada acción,
la propuesta y el resultado de su confirmación. Los payloads forman una unión
discriminada por la acción: cada acción tiene su propio payload tipado y su
propio conjunto de campos.

Estos esquemas validan la estructura, los tipos y las restricciones formales de
lo que se propone. Las reglas de negocio no se reimplementan aquí: las valida el
dominio y las revalida el servicio de destino al confirmar, con el estado
vigente del inventario. Los valores monetarios se representan con `Decimal`
exacto y se rechaza de forma explícita `float`, conforme al plan técnico.

Las propuestas no se persisten en este alcance: no se escribe en la tabla
`proposals` ni se modifica el inventario al crear o editar una propuesta. Los
modelos son inmutables y no dependen de estado global, de un registro interno ni
de un singleton.

Quedan fuera de estas acciones la desactivación de productos y los cambios de
factor. El cambio de precio de una presentación sí es una acción
(`cambio_precio`, T032) y su confirmación la ejecuta el servicio de
presentaciones dentro de una única transacción (T031).
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
)


class ProposalAction(str, Enum):
    """
    Acción de una propuesta del asistente (RF-05, RF-08).

    Los valores son los identificadores del tipo de acción propuesta, en
    español, y cada acción tiene un payload propio y un servicio de destino que
    la ejecuta al confirmar. Quedan fuera de este alcance la desactivación de
    productos y los cambios de factor; el cambio de precio (`cambio_precio`) sí
    forma parte de las acciones del asistente.
    """

    PRODUCT_REGISTRATION = "alta_producto"
    PRODUCT_UPDATE = "modificacion_producto"
    PRESENTATION_REGISTRATION = "alta_presentacion"
    LOOSE_ENTRY = "entrada_suelta"
    CLOSED_ENTRY = "entrada_cerrada"
    LOOSE_EXIT = "salida_suelta"
    CLOSED_EXIT = "salida_cerrada"
    APERTURE = "apertura"
    COMPENSATION = "compensacion"
    PRICE_UPDATE = "cambio_precio"


class ProposalStatus(str, Enum):
    """
    Estado de una propuesta del asistente (RF-05).

    Una propuesta nace `PENDING`: puede editarse sin efectos y no modifica el
    inventario. Pasa a `CONFIRMED` únicamente después de que la operación se
    ejecutó correctamente; un fallo al confirmar no deja la propuesta
    confirmada.
    """

    PENDING = "pendiente"
    CONFIRMED = "confirmada"


def _reject_float(value: object) -> object:
    """
    Rechaza un valor monetario expresado con `float` (plan técnico, RF-07).

    Los precios y los costos se representan con `Decimal` exacto y el plan
    técnico no admite `float` en valores monetarios. La validez del valor
    —positivo para un precio y no negativo para un costo— la decide el dominio,
    no este esquema.

    Args:
        value: Valor informado para un campo monetario.

    Returns:
        El valor sin modificar, para que Pydantic lo valide como `Decimal`.

    Raises:
        ValueError: Si el valor es un `float`.
    """
    if isinstance(value, float):
        raise ValueError(
            "Los valores monetarios deben ser un Decimal exacto, no un float."
        )

    return value


MonetaryValue = Annotated[Decimal, BeforeValidator(_reject_float)]
"""Valor monetario exacto que rechaza los `float` (precio o costo)."""


class ProposalPayloadModel(BaseModel):
    """
    Base inmutable de los payloads de propuesta (RF-05).

    Un payload no se modifica una vez construido y no admite campos
    desconocidos: la estructura y los tipos se validan al construirlo, antes de
    cualquier confirmación.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class InitialPresentationPayload(ProposalPayloadModel):
    """
    Presentación adicional declarada en el alta de un producto (RF-07).

    `closed_stock` es el stock cerrado inicial de esa presentación: cuando es
    cero no genera ningún movimiento de stock inicial, regla que aplica el
    servicio de productos.
    """

    name: str
    factor: int = Field(ge=1)
    sale_price: MonetaryValue
    closed_stock: int = Field(default=0, ge=0)


class ProductRegistrationPayload(ProposalPayloadModel):
    """
    Payload del alta de un producto (RF-01, RF-02 y RF-07).

    Declara el SKU, el nombre y la unidad base del producto, el precio de la
    presentación base y, de forma opcional, el stock inicial suelto y cerrado.
    Los textos se validan en el dominio: quedan vacíos si solo tienen espacios y
    se conservan con su texto original recortado.
    """

    action: Literal["alta_producto"] = "alta_producto"
    sku: str
    name: str
    base_unit: str
    base_sale_price: MonetaryValue
    minimum_stock: int = Field(default=0, ge=0)
    initial_loose_units: int = Field(default=0, ge=0)
    presentations: tuple[InitialPresentationPayload, ...] = ()
    base_presentation_name: str | None = None


class ProductUpdatePayload(ProposalPayloadModel):
    """
    Payload de la modificación de un producto (RF-01).

    Solo admite el nombre y el stock mínimo: el SKU es inmutable y no se
    propone, y la desactivación queda fuera del alcance. La propuesta debe
    indicar al menos uno de los dos campos.
    """

    action: Literal["modificacion_producto"] = "modificacion_producto"
    product_id: int = Field(gt=0)
    name: str | None = None
    minimum_stock: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_a_change(self) -> "ProductUpdatePayload":
        """
        Exige que la modificación proponga algún cambio (RF-01).

        Raises:
            ValueError: Si no se indica ni el nombre ni el stock mínimo.
        """
        if self.name is None and self.minimum_stock is None:
            raise ValueError(
                "La modificación debe indicar el nombre, el stock mínimo o "
                "ambos."
            )

        return self


class PresentationRegistrationPayload(ProposalPayloadModel):
    """
    Payload del alta de una presentación (RF-07).

    La presentación nueva nace con stock cerrado cero. La unicidad del nombre
    dentro del producto, el factor y el precio los valida el servicio de
    presentaciones con las reglas del dominio.
    """

    action: Literal["alta_presentacion"] = "alta_presentacion"
    product_id: int = Field(gt=0)
    name: str
    factor: int = Field(ge=1)
    sale_price: MonetaryValue


class LooseEntryPayload(ProposalPayloadModel):
    """Payload de una entrada de unidades sueltas (RF-02, RF-03)."""

    action: Literal["entrada_suelta"] = "entrada_suelta"
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reason: str
    cost: MonetaryValue | None = None


class ClosedEntryPayload(ProposalPayloadModel):
    """Payload de una entrada de presentaciones cerradas (RF-02, RF-03)."""

    action: Literal["entrada_cerrada"] = "entrada_cerrada"
    product_id: int = Field(gt=0)
    presentation_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reason: str
    cost: MonetaryValue | None = None


class LooseExitPayload(ProposalPayloadModel):
    """Payload de una salida de unidades sueltas (RF-02, RF-03)."""

    action: Literal["salida_suelta"] = "salida_suelta"
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reason: str


class ClosedExitPayload(ProposalPayloadModel):
    """Payload de una salida de presentaciones cerradas (RF-02, RF-03)."""

    action: Literal["salida_cerrada"] = "salida_cerrada"
    product_id: int = Field(gt=0)
    presentation_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reason: str


class AperturePayload(ProposalPayloadModel):
    """Payload de la apertura de presentaciones cerradas (RF-03)."""

    action: Literal["apertura"] = "apertura"
    product_id: int = Field(gt=0)
    presentation_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reason: str


class CompensationPayload(ProposalPayloadModel):
    """Payload de la compensación de un movimiento confirmado (RF-03)."""

    action: Literal["compensacion"] = "compensacion"
    product_id: int = Field(gt=0)
    movement_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    reason: str


class PriceUpdatePayload(ProposalPayloadModel):
    """
    Payload del cambio de precio de venta de una presentación (RF-08, T032).

    `presentation_id` identifica la presentación cuyo precio se propone cambiar
    y `new_price` es el precio nuevo. No se declara el producto: se deriva de
    la presentación al confirmar, de modo que la coherencia entre ambos no
    pueda proponerse de forma contradictoria.

    La confirmación la ejecuta el servicio de presentaciones (T031) dentro de
    una única transacción, que actualiza el precio y registra el cambio en el
    historial; es ese servicio el que valida que la presentación exista y que
    su producto esté activo, y el que revalida el precio con el estado vigente
    (T033). Crear o editar esta propuesta no modifica el inventario.
    """

    action: Literal["cambio_precio"] = "cambio_precio"
    presentation_id: int = Field(gt=0)
    new_price: MonetaryValue = Field(gt=0)


ProposalPayload = Annotated[
    ProductRegistrationPayload
    | ProductUpdatePayload
    | PresentationRegistrationPayload
    | LooseEntryPayload
    | ClosedEntryPayload
    | LooseExitPayload
    | ClosedExitPayload
    | AperturePayload
    | CompensationPayload
    | PriceUpdatePayload,
    Field(discriminator="action"),
]
"""Unión discriminada de los payloads: la acción selecciona el payload."""


class Proposal(BaseModel):
    """
    Propuesta del asistente, pendiente o confirmada (RF-05).

    La acción de la propuesta es la de su payload: construir una propuesta con
    una acción que no corresponda a su payload se rechaza. Una propuesta es un
    objeto inmutable y no se persiste; editarla produce una propuesta nueva y su
    confirmación devuelve una copia confirmada sin modificar la propuesta
    recibida.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ProposalAction
    payload: ProposalPayload
    status: ProposalStatus = ProposalStatus.PENDING

    @model_validator(mode="after")
    def _require_the_action_of_the_payload(self) -> "Proposal":
        """
        Exige que la acción declarada coincida con la del payload (RF-05).

        Raises:
            ValueError: Si la acción declarada no corresponde al payload.
        """
        if self.action is not ProposalAction(self.payload.action):
            raise ValueError(
                "La acción de la propuesta no corresponde a la acción de su "
                "payload."
            )

        return self


ProposalOutcome = ProductRecord | PresentationRecord | MovementRecord
"""Registro real que persiste el servicio de destino al confirmar (RF-05)."""


class ConfirmationResult(BaseModel):
    """
    Resultado real de la confirmación de una propuesta (RF-05).

    Solo se construye después de que la operación se ejecutó correctamente:
    `status` es `CONFIRMED` y `result` es el registro real que persiste el
    servicio de destino. `proposal` es la copia confirmada de la propuesta,
    porque la propuesta recibida no se modifica.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ProposalAction
    status: ProposalStatus
    proposal: Proposal
    result: ProposalOutcome
