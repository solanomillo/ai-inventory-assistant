"""Herramientas LangChain de propuesta del asistente (T020 y T033).

Expone las herramientas con las que el Inventory Agent prepara y edita
propuestas de RF-05 sin ejecutar ninguna operación: diez herramientas de
creación —una por cada acción de `ProposalAction`, incluida la apertura y el
cambio de precio de venta (RF-08)— y una herramienta de edición de una
propuesta pendiente.

Cada herramienta es un adaptador delgado: valida su entrada con el esquema
Pydantic de la acción, delega en `app.services.proposals.ProposalService` y
devuelve el contrato `Proposal` existente, sin crear modelos de salida ni
duplicar payloads. Las herramientas no acceden a la persistencia, no importan
ni instancian repositorios de infraestructura y no capturan los errores
tipados: un `ValidationError`, un `ProposalAlreadyConfirmedError` o un
`ProposalActionMismatchError` se propagan tal como los define Pydantic o el
servicio.

Crear y editar una propuesta no la persiste ni modifica el inventario. La tabla
`proposals` no se usa: los productos, las presentaciones, los movimientos y
las existencias quedan intactos. Toda propuesta nace `PENDING` y permanece
así hasta que el usuario la confirme.

Este alcance excluye la confirmación: confirmar es una acción explícita del
usuario en la interfaz y no una decisión autónoma del LLM (RF-05), de modo que
estas herramientas no la ofrecen ni invocan la operación de confirmación del
servicio. Ninguna herramienta de este módulo ejecuta la operación propuesta.

Convención del campo `action`: los payloads declaran `action` como un valor
constante validado con `Literal`, así que el modelo no puede proponer una
acción distinta de la de la herramienta; un valor distinto se rechaza con
`ValidationError`. Cada herramienta de creación recibe ese campo —porque el
esquema del payload lo incluye— y construye internamente su payload sin
reenviarlo, por lo que la acción de la propuesta resultante es siempre la
acción de la herramienta.

Las herramientas se construyen con `create_proposal_tools(service)`, que
inyecta el servicio de propuestas; se invocan sin LLM, sin API externa y sin
grafo.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict

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
)
from app.services.proposals import ProposalService


class EditProposalInput(BaseModel):
    """
    Entrada de la edición de una propuesta pendiente (RF-05).

    Compone los contratos existentes sin duplicarlos: la propuesta que se edita
    y su payload nuevo, que es la unión de los diez payloads discriminada por
    `action`. La edición conserva la acción de la propuesta, así que un payload
    de otra acción se rechaza en el servicio con
    `ProposalActionMismatchError`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal: Proposal
    payload: ProposalPayload


def create_proposal_tools(service: ProposalService) -> tuple[BaseTool, ...]:
    """
    Construye las herramientas LangChain de propuesta del asistente (T020).

    Cada herramienta valida su entrada con el esquema Pydantic de la acción,
    delega en el servicio de propuestas y devuelve la propuesta resultante. Los
    errores tipados del servicio y de Pydantic se propagan sin transformarse en
    respuestas vacías.

    Las herramientas no confirman: la operación propuesta se ejecuta solo
    cuando el usuario confirma, fuera del alcance de este módulo.

    Args:
        service: Servicio de propuestas. Las herramientas solo crean y editan
            propuestas a través de él; no acceden a la persistencia.

    Returns:
        Las herramientas de propuesta, listas para enlazarse a un modelo.
    """

    @tool(args_schema=ProductRegistrationPayload)
    def propose_product_registration(
        *,
        sku: str,
        name: str,
        base_unit: str,
        base_sale_price: Decimal,
        minimum_stock: int = 0,
        initial_loose_units: int = 0,
        presentations: tuple[InitialPresentationPayload, ...] = (),
        base_presentation_name: str | None = None,
        action: Literal["alta_producto"] = "alta_producto",
    ) -> Proposal:
        """Propone el alta de un producto con su presentación base.

        Declara el SKU, el nombre, la unidad base, el precio de la presentación
        base y, de forma opcional, el stock mínimo, las unidades sueltas
        iniciales y las presentaciones adicionales con su stock inicial. No
        registra nada: el alta se ejecuta solo cuando el usuario la confirma
        (RF-01, RF-02, RF-07).
        """
        return service.create(
            ProductRegistrationPayload(
                sku=sku,
                name=name,
                base_unit=base_unit,
                base_sale_price=base_sale_price,
                minimum_stock=minimum_stock,
                initial_loose_units=initial_loose_units,
                presentations=presentations,
                base_presentation_name=base_presentation_name,
            )
        )

    @tool(args_schema=ProductUpdatePayload)
    def propose_product_update(
        *,
        product_id: int,
        name: str | None = None,
        minimum_stock: int | None = None,
        action: Literal["modificacion_producto"] = "modificacion_producto",
    ) -> Proposal:
        """Propone modificar el nombre o el stock mínimo de un producto.

        La modificación debe indicar el nombre, el stock mínimo o ambos, y el
        SKU no se propone porque es inmutable. No modifica el producto hasta
        que el usuario la confirme (RF-01).
        """
        return service.create(
            ProductUpdatePayload(
                product_id=product_id,
                name=name,
                minimum_stock=minimum_stock,
            )
        )

    @tool(args_schema=PresentationRegistrationPayload)
    def propose_presentation_registration(
        *,
        product_id: int,
        name: str,
        factor: int,
        sale_price: Decimal,
        action: Literal["alta_presentacion"] = "alta_presentacion",
    ) -> Proposal:
        """Propone el alta de una presentación de un producto.

        La presentación nueva nace con stock cerrado cero y no altera los
        saldos existentes. No crea nada hasta que el usuario la confirme
        (RF-07).
        """
        return service.create(
            PresentationRegistrationPayload(
                product_id=product_id,
                name=name,
                factor=factor,
                sale_price=sale_price,
            )
        )

    @tool(args_schema=LooseEntryPayload)
    def propose_loose_entry(
        *,
        product_id: int,
        quantity: int,
        reason: str,
        cost: Decimal | None = None,
        action: Literal["entrada_suelta"] = "entrada_suelta",
    ) -> Proposal:
        """Propone una entrada de unidades sueltas del producto.

        El motivo es obligatorio y el costo de ingreso es opcional. No modifica
        las existencias hasta que el usuario la confirme (RF-02, RF-03).
        """
        return service.create(
            LooseEntryPayload(
                product_id=product_id,
                quantity=quantity,
                reason=reason,
                cost=cost,
            )
        )

    @tool(args_schema=ClosedEntryPayload)
    def propose_closed_entry(
        *,
        product_id: int,
        presentation_id: int,
        quantity: int,
        reason: str,
        cost: Decimal | None = None,
        action: Literal["entrada_cerrada"] = "entrada_cerrada",
    ) -> Proposal:
        """Propone una entrada de presentaciones cerradas de un producto.

        La presentación debe tener factor mayor que 1; la presentación base no
        admite operaciones cerradas. No modifica el stock cerrado hasta que el
        usuario la confirme (RF-02, RF-03).
        """
        return service.create(
            ClosedEntryPayload(
                product_id=product_id,
                presentation_id=presentation_id,
                quantity=quantity,
                reason=reason,
                cost=cost,
            )
        )

    @tool(args_schema=LooseExitPayload)
    def propose_loose_exit(
        *,
        product_id: int,
        quantity: int,
        reason: str,
        action: Literal["salida_suelta"] = "salida_suelta",
    ) -> Proposal:
        """Propone una salida de unidades sueltas del producto.

        La disponibilidad se revalida al confirmar. No modifica las existencias
        hasta que el usuario la confirme (RF-02, RF-03).
        """
        return service.create(
            LooseExitPayload(
                product_id=product_id,
                quantity=quantity,
                reason=reason,
            )
        )

    @tool(args_schema=ClosedExitPayload)
    def propose_closed_exit(
        *,
        product_id: int,
        presentation_id: int,
        quantity: int,
        reason: str,
        action: Literal["salida_cerrada"] = "salida_cerrada",
    ) -> Proposal:
        """Propone una salida de presentaciones cerradas de un producto.

        La disponibilidad cerrada se revalida al confirmar. No modifica el
        stock cerrado hasta que el usuario la confirme (RF-02, RF-03).
        """
        return service.create(
            ClosedExitPayload(
                product_id=product_id,
                presentation_id=presentation_id,
                quantity=quantity,
                reason=reason,
            )
        )

    @tool(args_schema=AperturePayload)
    def propose_aperture(
        *,
        product_id: int,
        presentation_id: int,
        quantity: int,
        reason: str,
        action: Literal["apertura"] = "apertura",
    ) -> Proposal:
        """Propone la apertura de presentaciones cerradas de un producto.

        La apertura convierte presentaciones cerradas en unidades sueltas sin
        cambiar el total equivalente y solo es válida sobre una presentación
        con factor mayor que 1. No modifica los saldos hasta que el usuario la
        confirme (RF-02, RF-03).
        """
        return service.create(
            AperturePayload(
                product_id=product_id,
                presentation_id=presentation_id,
                quantity=quantity,
                reason=reason,
            )
        )

    @tool(args_schema=CompensationPayload)
    def propose_compensation(
        *,
        product_id: int,
        movement_id: int,
        quantity: int,
        reason: str,
        action: Literal["compensacion"] = "compensacion",
    ) -> Proposal:
        """Propone la compensación de un movimiento confirmado.

        La compensación se vincula al movimiento original y su cantidad
        compensable y su disponibilidad se revalidan al confirmar. No modifica
        el movimiento original ni el stock hasta que el usuario la confirme
        (RF-03).
        """
        return service.create(
            CompensationPayload(
                product_id=product_id,
                movement_id=movement_id,
                quantity=quantity,
                reason=reason,
            )
        )

    @tool(args_schema=PriceUpdatePayload)
    def propose_price_update(
        *,
        presentation_id: int,
        new_price: Decimal,
        action: Literal["cambio_precio"] = "cambio_precio",
    ) -> Proposal:
        """Propone el cambio de precio de venta de una presentación.

        Declara la presentación y el precio nuevo; el producto se deriva de la
        presentación al aplicar el cambio, así que no se declara aquí. No
        modifica el precio ni registra el cambio en el historial hasta que el
        usuario lo confirme (RF-08).
        """
        return service.create(
            PriceUpdatePayload(
                presentation_id=presentation_id,
                new_price=new_price,
            )
        )

    @tool(args_schema=EditProposalInput)
    def edit_proposal(
        *,
        proposal: Proposal,
        payload: ProposalPayload,
    ) -> Proposal:
        """Edita el payload de una propuesta pendiente.

        Devuelve una propuesta nueva, siempre `PENDING`, con la misma acción y
        el payload indicado; la propuesta editada no se modifica y tampoco se
        persiste. Editar no ejecuta la operación. Una propuesta confirmada no
        se edita y un payload de otra acción se rechaza (RF-05).
        """
        return service.edit(proposal, payload)

    return (
        propose_product_registration,
        propose_product_update,
        propose_presentation_registration,
        propose_loose_entry,
        propose_closed_entry,
        propose_loose_exit,
        propose_closed_exit,
        propose_aperture,
        propose_compensation,
        propose_price_update,
        edit_proposal,
    )
