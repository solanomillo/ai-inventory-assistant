"""Servicio de aplicación de propuestas del asistente (T015, RF-05).

Implementa los casos de uso de RF-05: crear una propuesta sin efectos, editarla
mientras está pendiente y confirmarla revalidando las reglas de negocio con el
estado vigente del inventario.

El servicio no ejecuta reglas de negocio propias: al confirmar despacha la
operación al servicio de destino —productos, presentaciones o movimientos—, que
valida las reglas del dominio y aplica el cambio dentro de su propia
transacción. Los errores del servicio de destino se propagan sin ocultarlos y
la propuesta solo se informa confirmada cuando la operación se ejecutó
correctamente: un fallo no deja cambios parciales ni una propuesta confirmada.

Las propuestas no se persisten: no se usa la tabla `proposals` y no hay estado
global, registro interno ni singleton de propuestas. La revalidación al
confirmar tiene el alcance de cada servicio de destino: al confirmar, el
servicio de destino —movimientos, productos o presentaciones— valida las
reglas con el estado vigente y aplica el cambio dentro de su propia
transacción. Este servicio no abre transacciones propias ni anidadas: sigue
delegando en el destino, que abre la suya con el helper compartido
`transaction` de `app/infrastructure/sqlite/connection.py`. Ese helper
comienza con `BEGIN IMMEDIATE`, de modo que las lecturas y las validaciones no
se separan de la escritura frente a operaciones simultáneas; la protección de
concurrencia ya está implementada (T016).

Como las propuestas no se persisten, una propuesta pendiente no se puede
detectar por sí sola como obsoleta: volver a confirmar el mismo objeto
`Proposal` vuelve a ejecutar la operación. La revalidación de las reglas sí
ocurre en cada confirmación, de modo que una propuesta que dejó de cumplir una
regla se rechaza sin cambios parciales; el estado `CONFIRMED` solo existe en la
copia que devuelve `confirm()`.
"""

from __future__ import annotations

from app.schemas.proposals import (
    AperturePayload,
    ClosedEntryPayload,
    ClosedExitPayload,
    CompensationPayload,
    ConfirmationResult,
    LooseEntryPayload,
    LooseExitPayload,
    PresentationRegistrationPayload,
    ProductRegistrationPayload,
    ProductUpdatePayload,
    Proposal,
    ProposalAction,
    ProposalPayload,
    ProposalStatus,
)
from app.services.movements import MovementService
from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
)
from app.services.presentations import PresentationService
from app.services.products import (
    InitialClosedStock,
    ProductRegistration,
    ProductService,
)


class ProposalError(ValueError):
    """Error relacionado con una propuesta del asistente (RF-05)."""


class ProposalActionMismatchError(ProposalError):
    """Error que indica que una edición cambia la acción de la propuesta."""


class ProposalAlreadyConfirmedError(ProposalError):
    """Error que indica que la propuesta ya está confirmada (RF-05)."""


class ProposalService:
    """Casos de uso de propuestas: crear, editar y confirmar (RF-05)."""

    def __init__(
        self,
        *,
        products: ProductService,
        presentations: PresentationService,
        movements: MovementService,
    ) -> None:
        """
        Args:
            products: Servicio de productos, destino de las altas y de las
                modificaciones de productos.
            presentations: Servicio de presentaciones, destino de las altas de
                presentaciones.
            movements: Servicio de movimientos, destino de las entradas, las
                salidas, las aperturas y las compensaciones.
        """
        self._products = products
        self._presentations = presentations
        self._movements = movements

    def create(self, payload: ProposalPayload) -> Proposal:
        """
        Crea una propuesta pendiente sin ejecutar ninguna operación (RF-05).

        La propuesta es un objeto en memoria: crearla no persiste nada ni
        modifica el inventario. La operación se ejecuta solo al confirmarla.

        Args:
            payload: Payload validado de la acción propuesta.

        Returns:
            La propuesta pendiente con la acción de su payload.
        """
        return Proposal(action=ProposalAction(payload.action), payload=payload)

    def edit(self, proposal: Proposal, payload: ProposalPayload) -> Proposal:
        """
        Edita el payload de una propuesta pendiente (RF-05).

        La edición no ejecuta la operación ni modifica el inventario: devuelve
        una propuesta nueva con el payload indicado. La acción no puede cambiar,
        porque cada acción tiene su propio payload y su propio servicio de
        destino: para proponer otra acción se crea una propuesta nueva.

        Args:
            proposal: Propuesta pendiente que se edita.
            payload: Payload nuevo, con la misma acción que la propuesta.

        Returns:
            Una propuesta pendiente nueva con el payload editado.

        Raises:
            ProposalAlreadyConfirmedError: Si la propuesta no está pendiente.
            ProposalActionMismatchError: Si el payload corresponde a otra
                acción.
        """
        self._require_pending(proposal, operation="editar")

        if ProposalAction(payload.action) is not proposal.action:
            raise ProposalActionMismatchError(
                "La edición de una propuesta debe conservar su acción: la "
                f"propuesta es de acción '{proposal.action.value}' y el payload "
                f"indicado es de acción '{payload.action}'."
            )

        return Proposal(action=proposal.action, payload=payload)

    def confirm(self, proposal: Proposal) -> ConfirmationResult:
        """
        Confirma una propuesta pendiente ejecutando su operación (RF-05).

        La operación se despacha al servicio de destino, que revalida las reglas
        de negocio con el estado vigente del inventario y aplica el cambio de
        forma atómica. El resultado devuelto es el registro real que persistió
        ese servicio, y el estado `CONFIRMED` solo se informa después de que la
        operación terminó correctamente: ante un fallo, el error del destino se
        propaga y no queda una propuesta confirmada ni un cambio parcial.

        Args:
            proposal: Propuesta pendiente que se confirma.

        Returns:
            El resultado de la confirmación, con el estado confirmado, la copia
            confirmada de la propuesta y el registro real persistido.

        Raises:
            ProposalAlreadyConfirmedError: Si la propuesta no está pendiente.
            ValueError: Si el servicio de destino rechaza la operación por
                incumplir una regla de negocio o si la persistencia falla. El
                error del destino se propaga sin ocultarlo.
        """
        self._require_pending(proposal, operation="confirmar")

        result = self._dispatch(proposal.payload)

        return ConfirmationResult(
            action=proposal.action,
            status=ProposalStatus.CONFIRMED,
            proposal=Proposal(
                action=proposal.action,
                payload=proposal.payload,
                status=ProposalStatus.CONFIRMED,
            ),
            result=result,
        )

    def _dispatch(
        self, payload: ProposalPayload
    ) -> ProductRecord | PresentationRecord | MovementRecord:
        """
        Ejecuta la operación de la propuesta en su servicio de destino (RF-05).

        Las reglas de negocio las validan los servicios de destino: este método
        traduce el payload ya validado a los argumentos que cada servicio espera
        y no reimplementa ninguna validación ni afirma una garantía que el
        destino no ofrezca.

        Args:
            payload: Payload validado de la acción confirmada.

        Returns:
            El registro real que persistió el servicio de destino.

        Raises:
            ValueError: Los errores de validación o de persistencia del servicio
                de destino, que se propagan sin ocultarlos.
            ProposalError: Si el payload no corresponde a ninguna acción con
                servicio de destino.
        """
        if isinstance(payload, ProductRegistrationPayload):
            return self._products.register(_product_registration(payload))

        if isinstance(payload, ProductUpdatePayload):
            return self._products.update(
                payload.product_id,
                name=payload.name,
                minimum_stock=payload.minimum_stock,
            )

        if isinstance(payload, PresentationRegistrationPayload):
            return self._presentations.add(
                payload.product_id,
                name=payload.name,
                factor=payload.factor,
                sale_price=payload.sale_price,
            )

        if isinstance(payload, LooseEntryPayload):
            return self._movements.register_loose_entry(
                payload.product_id,
                quantity=payload.quantity,
                reason=payload.reason,
                cost=payload.cost,
            )

        if isinstance(payload, ClosedEntryPayload):
            return self._movements.register_closed_entry(
                payload.product_id,
                presentation_id=payload.presentation_id,
                quantity=payload.quantity,
                reason=payload.reason,
                cost=payload.cost,
            )

        if isinstance(payload, LooseExitPayload):
            return self._movements.register_loose_exit(
                payload.product_id,
                quantity=payload.quantity,
                reason=payload.reason,
            )

        if isinstance(payload, ClosedExitPayload):
            return self._movements.register_closed_exit(
                payload.product_id,
                presentation_id=payload.presentation_id,
                quantity=payload.quantity,
                reason=payload.reason,
            )

        if isinstance(payload, AperturePayload):
            return self._movements.register_aperture(
                payload.product_id,
                presentation_id=payload.presentation_id,
                quantity=payload.quantity,
                reason=payload.reason,
            )

        if isinstance(payload, CompensationPayload):
            return self._movements.register_compensation(
                payload.product_id,
                movement_id=payload.movement_id,
                quantity=payload.quantity,
                reason=payload.reason,
            )

        raise ProposalError(
            "La acción de la propuesta no tiene un servicio de destino."
        )

    @staticmethod
    def _require_pending(proposal: Proposal, *, operation: str) -> None:
        """
        Exige que la propuesta esté pendiente (RF-05).

        Args:
            proposal: Propuesta sobre la que se opera.
            operation: Operación solicitada, usada en el mensaje de error.

        Raises:
            ProposalAlreadyConfirmedError: Si la propuesta no está pendiente,
                porque una propuesta confirmada no se edita ni se vuelve a
                confirmar.
        """
        if proposal.status is not ProposalStatus.PENDING:
            raise ProposalAlreadyConfirmedError(
                f"No se puede {operation} una propuesta "
                f"'{proposal.status.value}': solo se admiten propuestas "
                "pendientes."
            )


def _product_registration(
    payload: ProductRegistrationPayload,
) -> ProductRegistration:
    """
    Traduce el payload del alta a los datos del servicio de productos (RF-01).

    No valida nada: el alta del producto y de sus presentaciones se valida en el
    servicio de productos, con las reglas del dominio.

    Args:
        payload: Payload validado del alta de un producto.

    Returns:
        Los datos del alta de productos, con sus presentaciones iniciales.
    """
    return ProductRegistration(
        sku=payload.sku,
        name=payload.name,
        base_unit=payload.base_unit,
        base_sale_price=payload.base_sale_price,
        minimum_stock=payload.minimum_stock,
        initial_loose_units=payload.initial_loose_units,
        presentations=tuple(
            InitialClosedStock(
                name=presentation.name,
                factor=presentation.factor,
                sale_price=presentation.sale_price,
                closed_stock=presentation.closed_stock,
            )
            for presentation in payload.presentations
        ),
        base_presentation_name=payload.base_presentation_name,
    )
