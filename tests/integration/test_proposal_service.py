"""Pruebas de integración del servicio de propuestas (T015).

Cubren los criterios de aceptación de T015 sobre RF-05: las nueve acciones
crean propuestas válidas sin tocar el inventario ni la tabla `proposals`, la
edición no modifica los datos existentes, la confirmación ejecuta la operación
en el servicio de destino y devuelve el resultado real, la revalidación al
confirmar rechaza las reglas incumplidas sin cambios parciales, y se rechazan
las ediciones que cambian la acción y las operaciones sobre una propuesta ya
confirmada.

Las pruebas usan la base SQLite en memoria con el esquema real y los
repositorios de T009; el estado del inventario se prepara con los servicios de
T011 a T014. El estado persistido se verifica siempre con lecturas nuevas:
repositorios y consultas `SELECT` directas sobre la conexión.
"""

import sqlite3
from collections.abc import Callable, Iterator
from decimal import Decimal
from functools import partial
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.movements import (
    ExcessCompensationError,
    MovementKind,
    NotCompensableError,
)
from app.domain.presentations import DuplicatePresentationNameError
from app.domain.products import DuplicateSkuError
from app.domain.quantities import QuantityError
from app.domain.stock import (
    BasePresentationApertureError,
    InsufficientClosedStockError,
)
from app.infrastructure.sqlite.connection import (
    IN_MEMORY_DATABASE,
    connect,
    transaction,
)
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.schemas.proposals import (
    AperturePayload,
    ClosedEntryPayload,
    ClosedExitPayload,
    CompensationPayload,
    InitialPresentationPayload,
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
from app.services.movements import (
    BasePresentationOperationError,
    InsufficientLooseUnitsError,
    InvalidMovementReasonError,
    MovementService,
)
from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
    RecordNotFoundError,
)
from app.services.presentations import PresentationService
from app.services.products import (
    InactiveProductError,
    InitialClosedStock,
    ProductRegistration,
    ProductService,
)
from app.services.proposals import (
    ProposalActionMismatchError,
    ProposalAlreadyConfirmedError,
    ProposalService,
)


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    """Abre una base en memoria con el esquema creado desde cero."""
    opened_connection = connect(IN_MEMORY_DATABASE)
    create_schema(opened_connection)
    try:
        yield opened_connection
    finally:
        opened_connection.close()


@pytest.fixture
def products(connection: sqlite3.Connection) -> SqliteProductRepository:
    """Repositorio de productos sobre la base en memoria."""
    return SqliteProductRepository(connection)


@pytest.fixture
def presentations(
    connection: sqlite3.Connection,
) -> SqlitePresentationRepository:
    """Repositorio de presentaciones sobre la base en memoria."""
    return SqlitePresentationRepository(connection)


@pytest.fixture
def movements(connection: sqlite3.Connection) -> SqliteMovementRepository:
    """Repositorio de movimientos sobre la base en memoria."""
    return SqliteMovementRepository(connection)


@pytest.fixture
def product_service(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> ProductService:
    """Servicio de productos con una transacción sobre la base en memoria."""
    return ProductService(
        products=products,
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )


@pytest.fixture
def presentation_service(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> PresentationService:
    """Servicio de presentaciones sobre la base en memoria."""
    return PresentationService(
        products=products,
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )


@pytest.fixture
def movement_service(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> MovementService:
    """Servicio de movimientos con una transacción sobre la base en memoria."""
    return MovementService(
        products=products,
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )


@pytest.fixture
def proposal_service(
    product_service: ProductService,
    presentation_service: PresentationService,
    movement_service: MovementService,
) -> ProposalService:
    """Servicio de propuestas con sus servicios de destino inyectados."""
    return ProposalService(
        products=product_service,
        presentations=presentation_service,
        movements=movement_service,
    )


def make_registration(**overrides: Any) -> ProductRegistration:
    """Construye un alta válida con los valores indicados."""
    values: dict[str, Any] = {
        "sku": "SKU-1",
        "name": "Producto",
        "base_unit": "unidad",
        "base_sale_price": Decimal("100.00"),
    }
    values.update(overrides)

    return ProductRegistration(**values)


def make_closed_presentation(
    *,
    name: str = "Caja x6",
    factor: int = 6,
    sale_price: str = "600.00",
    closed_stock: int = 0,
) -> InitialClosedStock:
    """Declara una presentación adicional con su stock inicial cerrado."""
    return InitialClosedStock(
        name=name,
        factor=factor,
        sale_price=Decimal(sale_price),
        closed_stock=closed_stock,
    )


def prepare_product(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movement_service: MovementService,
    *,
    sku: str = "SKU-1",
    loose_units: int = 0,
    closed_stock: int = 0,
    factor: int = 6,
    box_price: str = "600.00",
) -> tuple[ProductRecord, PresentationRecord, PresentationRecord]:
    """
    Prepara un producto con su presentación base, una caja y saldo disponible.

    El saldo se carga con entradas ordinarias del servicio, de modo que el
    producto quede con movimientos reales y una entrada cerrada compensable.
    Devuelve el producto, su presentación base y su presentación cerrada.
    """
    stored = product_service.register(
        make_registration(
            sku=sku,
            presentations=(
                make_closed_presentation(
                    factor=factor, sale_price=box_price
                ),
            ),
        )
    )
    base, box = presentations.list_by_product(stored.id)
    if loose_units:
        movement_service.register_loose_entry(
            stored.id, quantity=loose_units, reason="Compra"
        )
    if closed_stock:
        movement_service.register_closed_entry(
            stored.id,
            presentation_id=box.id,
            quantity=closed_stock,
            reason="Compra",
        )

    return stored, base, box


def database_state(
    connection: sqlite3.Connection,
) -> tuple[list[tuple[Any, ...]], ...]:
    """
    Fotografía el estado persistido del inventario para comparar estados.

    Lee las tablas del inventario con consultas nuevas, incluidas las columnas
    de saldo y los contadores FIFO. La tabla `proposals` se verifica por
    separado, porque las propuestas de T015 no se persisten.
    """
    tables = ("products", "presentations", "movements")

    return tuple(
        [
            tuple(row)
            for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY id"
            ).fetchall()
        ]
        for table in tables
    )


def proposals_count(connection: sqlite3.Connection) -> int:
    """Devuelve la cantidad de filas de la tabla `proposals`."""
    row = connection.execute(
        "SELECT COUNT(*) AS total FROM proposals"
    ).fetchone()

    return int(row["total"])


def stored_product(
    products: SqliteProductRepository, product_id: int
) -> ProductRecord:
    """Devuelve el producto persistido y exige que exista."""
    product = products.get(product_id)
    assert product is not None

    return product


def stored_presentation(
    presentations: SqlitePresentationRepository, presentation_id: int
) -> PresentationRecord:
    """Devuelve la presentación persistida y exige que exista."""
    presentation = presentations.get(presentation_id)
    assert presentation is not None

    return presentation


def stored_movement(
    movements: SqliteMovementRepository, movement_id: int
) -> MovementRecord:
    """Devuelve el movimiento persistido y exige que exista."""
    movement = movements.get(movement_id)
    assert movement is not None

    return movement


# --- Las nueve acciones crean propuestas válidas (criterio 1 y 2) ---


def test_create_records_the_nine_actions_without_touching_the_inventory(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica que las nueve acciones crean propuestas pendientes sin efectos."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=5,
        closed_stock=2,
    )
    entry = movements.list_by_product(stored.id, MovementKind.LOOSE_ENTRY)[0]
    baseline = database_state(connection)

    payloads: tuple[tuple[ProposalAction, ProposalPayload], ...] = (
        (
            ProposalAction.PRODUCT_REGISTRATION,
            ProductRegistrationPayload(
                sku="SKU-2",
                name="Otro producto",
                base_unit="botella",
                base_sale_price=Decimal("50"),
            ),
        ),
        (
            ProposalAction.PRODUCT_UPDATE,
            ProductUpdatePayload(product_id=stored.id, name="Nombre nuevo"),
        ),
        (
            ProposalAction.PRESENTATION_REGISTRATION,
            PresentationRegistrationPayload(
                product_id=stored.id,
                name="Caja x12",
                factor=12,
                sale_price=Decimal("1200"),
            ),
        ),
        (
            ProposalAction.LOOSE_ENTRY,
            LooseEntryPayload(
                product_id=stored.id,
                quantity=3,
                reason="Compra",
                cost=Decimal("10.50"),
            ),
        ),
        (
            ProposalAction.CLOSED_ENTRY,
            ClosedEntryPayload(
                product_id=stored.id,
                presentation_id=box.id,
                quantity=2,
                reason="Compra",
                cost=Decimal("100"),
            ),
        ),
        (
            ProposalAction.LOOSE_EXIT,
            LooseExitPayload(
                product_id=stored.id, quantity=2, reason="Venta"
            ),
        ),
        (
            ProposalAction.CLOSED_EXIT,
            ClosedExitPayload(
                product_id=stored.id,
                presentation_id=box.id,
                quantity=1,
                reason="Venta",
            ),
        ),
        (
            ProposalAction.APERTURE,
            AperturePayload(
                product_id=stored.id,
                presentation_id=box.id,
                quantity=1,
                reason="Apertura",
            ),
        ),
        (
            ProposalAction.COMPENSATION,
            CompensationPayload(
                product_id=stored.id,
                movement_id=entry.id,
                quantity=2,
                reason="Corrección",
            ),
        ),
    )
    assert len(payloads) == len(ProposalAction)

    for action, payload in payloads:
        proposal = proposal_service.create(payload)

        assert proposal.status is ProposalStatus.PENDING
        assert proposal.action is action
        assert proposal.payload == payload

    assert proposals_count(connection) == 0
    assert database_state(connection) == baseline


def test_edit_replaces_the_payload_without_touching_the_inventory(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica que editar una propuesta no modifica los datos existentes."""
    stored, _, _ = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=5,
        closed_stock=2,
    )
    proposal = proposal_service.create(
        LooseEntryPayload(product_id=stored.id, quantity=2, reason="Compra")
    )
    baseline = database_state(connection)

    edited = proposal_service.edit(
        proposal,
        LooseEntryPayload(product_id=stored.id, quantity=7, reason="Compra"),
    )

    assert edited is not proposal
    assert edited.status is ProposalStatus.PENDING
    assert edited.action is ProposalAction.LOOSE_ENTRY
    assert isinstance(edited.payload, LooseEntryPayload)
    assert edited.payload.quantity == 7
    assert proposals_count(connection) == 0
    assert database_state(connection) == baseline
    assert stored_product(products, stored.id).loose_units == 5
    assert len(movements.list_by_product(stored.id)) == 2


def test_edit_rejects_a_payload_of_another_action(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica que la edición no puede cambiar la acción de la propuesta."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    proposal = proposal_service.create(
        LooseEntryPayload(product_id=stored.id, quantity=2, reason="Compra")
    )
    baseline = database_state(connection)

    with pytest.raises(ProposalActionMismatchError):
        proposal_service.edit(
            proposal,
            LooseExitPayload(product_id=stored.id, quantity=2, reason="Venta"),
        )

    assert proposal.status is ProposalStatus.PENDING
    assert proposals_count(connection) == 0
    assert database_state(connection) == baseline


# --- Confirmación de cada acción (criterio 3) ---


def test_confirming_a_product_registration_creates_the_product(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    proposal_service: ProposalService,
) -> None:
    """Verifica la confirmación de un alta de producto y su resultado real."""
    proposal = proposal_service.create(
        ProductRegistrationPayload(
            sku="SKU-2",
            name="Otro producto",
            base_unit="botella",
            base_sale_price=Decimal("50"),
            initial_loose_units=2,
            presentations=(
                InitialPresentationPayload(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("600"),
                    closed_stock=2,
                ),
            ),
        )
    )

    confirmation = proposal_service.confirm(proposal)

    assert confirmation.status is ProposalStatus.CONFIRMED
    assert confirmation.action is ProposalAction.PRODUCT_REGISTRATION
    assert confirmation.proposal.status is ProposalStatus.CONFIRMED
    assert proposal.status is ProposalStatus.PENDING
    assert isinstance(confirmation.result, ProductRecord)
    created = stored_product(products, confirmation.result.id)
    assert created.sku == "SKU-2"
    assert created.loose_units == 2
    assert created.active is True
    registered = presentations.list_by_product(created.id)
    assert [presentation.name for presentation in registered] == [
        "botella",
        "Caja x6",
    ]
    assert [presentation.closed_stock for presentation in registered] == [0, 2]
    assert len(movements.list_by_product(created.id)) == 2


def test_confirming_a_product_update_changes_the_name_and_the_minimum_stock(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la confirmación de una modificación de producto."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service
    )
    proposal = proposal_service.create(
        ProductUpdatePayload(
            product_id=stored.id, name="Nombre nuevo", minimum_stock=9
        )
    )

    confirmation = proposal_service.confirm(proposal)

    assert isinstance(confirmation.result, ProductRecord)
    updated = stored_product(products, stored.id)
    assert updated.name == "Nombre nuevo"
    assert updated.minimum_stock == 9
    assert confirmation.result == updated


def test_confirming_a_presentation_registration_adds_the_presentation(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la confirmación de un alta de presentación."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=3
    )
    proposal = proposal_service.create(
        PresentationRegistrationPayload(
            product_id=stored.id,
            name="Caja x12",
            factor=12,
            sale_price=Decimal("1200"),
        )
    )

    confirmation = proposal_service.confirm(proposal)

    assert isinstance(confirmation.result, PresentationRecord)
    added = stored_presentation(presentations, confirmation.result.id)
    assert added.factor == 12
    assert added.closed_stock == 0
    assert added.sale_price == Decimal("1200")
    assert stored_product(products, stored.id).loose_units == 3
    assert len(movements.list_by_product(stored.id)) == 1


def test_confirming_a_loose_entry_updates_the_loose_units(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la confirmación de una entrada suelta y su resultado real."""
    stored, base, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    proposal = proposal_service.create(
        LooseEntryPayload(
            product_id=stored.id,
            quantity=4,
            reason="Compra",
            cost=Decimal("10.50"),
        )
    )

    confirmation = proposal_service.confirm(proposal)

    assert isinstance(confirmation.result, MovementRecord)
    movement = confirmation.result
    assert movement.kind is MovementKind.LOOSE_ENTRY
    assert movement.presentation_id == base.id
    assert movement.quantity == 4
    assert movement.cost == Decimal("10.50")
    assert movement == stored_movement(movements, movement.id)
    assert stored_product(products, stored.id).loose_units == 9


def test_confirming_a_closed_entry_updates_the_closed_stock(
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la confirmación de una entrada cerrada."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=2
    )
    proposal = proposal_service.create(
        ClosedEntryPayload(
            product_id=stored.id,
            presentation_id=box.id,
            quantity=3,
            reason="Compra",
            cost=Decimal("300"),
        )
    )

    confirmation = proposal_service.confirm(proposal)

    assert isinstance(confirmation.result, MovementRecord)
    assert confirmation.result.kind is MovementKind.CLOSED_ENTRY
    assert confirmation.result.cost == Decimal("300")
    assert stored_presentation(presentations, box.id).closed_stock == 5


def test_confirming_a_loose_exit_takes_the_price_of_the_base_presentation(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la confirmación de una salida suelta y su precio aplicado."""
    stored, base, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    proposal = proposal_service.create(
        LooseExitPayload(product_id=stored.id, quantity=2, reason="Venta")
    )

    confirmation = proposal_service.confirm(proposal)

    assert isinstance(confirmation.result, MovementRecord)
    assert confirmation.result.kind is MovementKind.LOOSE_EXIT
    assert confirmation.result.applied_price == base.sale_price
    assert stored_product(products, stored.id).loose_units == 3


def test_confirming_a_closed_exit_consumes_the_closed_entry_fifo(
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la confirmación de una salida cerrada y su consumo FIFO."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=3
    )
    entry = movements.list_by_product(stored.id, MovementKind.CLOSED_ENTRY)[0]
    proposal = proposal_service.create(
        ClosedExitPayload(
            product_id=stored.id,
            presentation_id=box.id,
            quantity=2,
            reason="Venta",
        )
    )

    confirmation = proposal_service.confirm(proposal)

    assert isinstance(confirmation.result, MovementRecord)
    assert confirmation.result.kind is MovementKind.CLOSED_EXIT
    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_movement(movements, entry.id).consumed_quantity == 2


def test_confirming_an_aperture_converts_closed_stock_into_loose_units(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la confirmación de una apertura y su consumo FIFO."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=2,
        closed_stock=3,
    )
    entry = movements.list_by_product(stored.id, MovementKind.CLOSED_ENTRY)[0]
    proposal = proposal_service.create(
        AperturePayload(
            product_id=stored.id,
            presentation_id=box.id,
            quantity=1,
            reason="Apertura",
        )
    )

    confirmation = proposal_service.confirm(proposal)

    assert isinstance(confirmation.result, MovementRecord)
    assert confirmation.result.kind is MovementKind.APERTURE
    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert stored_product(products, stored.id).loose_units == 2 + 6
    assert stored_movement(movements, entry.id).consumed_quantity == 1


def test_confirming_a_compensation_reduces_the_compensable_quantity(
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la confirmación de una compensación y sus contadores."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=3
    )
    entry = movements.list_by_product(stored.id, MovementKind.CLOSED_ENTRY)[0]
    proposal = proposal_service.create(
        CompensationPayload(
            product_id=stored.id,
            movement_id=entry.id,
            quantity=2,
            reason="Corrección",
        )
    )

    confirmation = proposal_service.confirm(proposal)

    assert isinstance(confirmation.result, MovementRecord)
    compensated = confirmation.result
    assert compensated.kind is MovementKind.COMPENSATION
    assert compensated.original_movement_id == entry.id
    assert compensated.quantity == 2
    assert stored_presentation(presentations, box.id).closed_stock == 1
    assert stored_movement(movements, entry.id).compensated_quantity == 2


# --- Propuestas confirmadas: edición y confirmación (criterio 2 de RF-05) ---


def test_confirming_a_confirmed_proposal_is_rejected_without_executing(
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica que una propuesta confirmada no se vuelve a ejecutar."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    proposal = proposal_service.create(
        LooseEntryPayload(product_id=stored.id, quantity=2, reason="Compra")
    )
    confirmation = proposal_service.confirm(proposal)
    history = movements.list_by_product(stored.id)
    loose_units = stored_product(products, stored.id).loose_units

    with pytest.raises(ProposalAlreadyConfirmedError):
        proposal_service.confirm(confirmation.proposal)

    assert confirmation.proposal.status is ProposalStatus.CONFIRMED
    assert stored_product(products, stored.id).loose_units == loose_units
    assert movements.list_by_product(stored.id) == history


def test_editing_a_confirmed_proposal_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica que una propuesta confirmada no se puede editar."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    proposal = proposal_service.create(
        LooseEntryPayload(product_id=stored.id, quantity=2, reason="Compra")
    )
    confirmation = proposal_service.confirm(proposal)
    baseline = database_state(connection)

    with pytest.raises(ProposalAlreadyConfirmedError):
        proposal_service.edit(
            confirmation.proposal,
            LooseEntryPayload(
                product_id=stored.id, quantity=3, reason="Compra"
            ),
        )

    assert database_state(connection) == baseline


def test_a_failed_confirmation_leaves_the_proposal_pending(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica que un fallo al confirmar no marca la propuesta confirmada."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    proposal = proposal_service.create(
        LooseExitPayload(product_id=stored.id, quantity=9, reason="Venta")
    )
    baseline = database_state(connection)

    with pytest.raises(InsufficientLooseUnitsError):
        proposal_service.confirm(proposal)

    assert proposal.status is ProposalStatus.PENDING
    assert proposals_count(connection) == 0
    assert database_state(connection) == baseline


def test_confirming_revalidates_the_stock_that_changed_after_the_proposal(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la revalidación cuando el stock cambió antes de confirmar."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    proposal = proposal_service.create(
        LooseExitPayload(product_id=stored.id, quantity=5, reason="Venta")
    )

    movement_service.register_loose_exit(
        stored.id, quantity=3, reason="Otra venta"
    )
    baseline = database_state(connection)

    with pytest.raises(InsufficientLooseUnitsError):
        proposal_service.confirm(proposal)

    assert stored_product(products, stored.id).loose_units == 2
    assert database_state(connection) == baseline


# --- Revalidación de las reglas de negocio al confirmar (criterio 4) ---


DEACTIVATED_PAYLOADS: dict[str, Callable[[int, int], ProposalPayload]] = {
    "entrada suelta": lambda product_id, presentation_id: LooseEntryPayload(
        product_id=product_id, quantity=1, reason="Compra"
    ),
    "entrada cerrada": (
        lambda product_id, presentation_id: ClosedEntryPayload(
            product_id=product_id,
            presentation_id=presentation_id,
            quantity=1,
            reason="Compra",
        )
    ),
    "salida suelta": lambda product_id, presentation_id: LooseExitPayload(
        product_id=product_id, quantity=1, reason="Venta"
    ),
    "salida cerrada": (
        lambda product_id, presentation_id: ClosedExitPayload(
            product_id=product_id,
            presentation_id=presentation_id,
            quantity=1,
            reason="Venta",
        )
    ),
    "apertura": lambda product_id, presentation_id: AperturePayload(
        product_id=product_id,
        presentation_id=presentation_id,
        quantity=1,
        reason="Apertura",
    ),
    "alta de presentación": (
        lambda product_id, presentation_id: PresentationRegistrationPayload(
            product_id=product_id,
            name="Caja x12",
            factor=12,
            sale_price=Decimal("1200"),
        )
    ),
    "modificación de producto": (
        lambda product_id, presentation_id: ProductUpdatePayload(
            product_id=product_id, name="Nombre nuevo"
        )
    ),
}


@pytest.mark.parametrize("payload_name", sorted(DEACTIVATED_PAYLOADS))
def test_confirming_an_operation_of_a_deactivated_product_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
    payload_name: str,
) -> None:
    """Verifica el rechazo de operaciones ordinarias de un producto desactivado."""
    stored, _, box = prepare_product(
        product_service,
        presentations,
        movement_service,
        loose_units=5,
        closed_stock=2,
    )
    product_service.deactivate(stored.id)
    payload = DEACTIVATED_PAYLOADS[payload_name](stored.id, box.id)
    baseline = database_state(connection)

    with pytest.raises(InactiveProductError):
        proposal_service.confirm(proposal_service.create(payload))

    assert database_state(connection) == baseline


def test_confirming_an_aperture_of_the_base_presentation_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica el rechazo de una apertura de la presentación base (factor 1)."""
    stored, base, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    proposal = proposal_service.create(
        AperturePayload(
            product_id=stored.id,
            presentation_id=base.id,
            quantity=1,
            reason="Apertura",
        )
    )
    baseline = database_state(connection)

    with pytest.raises(BasePresentationApertureError):
        proposal_service.confirm(proposal)

    assert database_state(connection) == baseline


def test_confirming_a_closed_entry_on_the_base_presentation_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica el rechazo de una entrada cerrada sobre la presentación base."""
    stored, base, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    proposal = proposal_service.create(
        ClosedEntryPayload(
            product_id=stored.id,
            presentation_id=base.id,
            quantity=1,
            reason="Compra",
        )
    )
    baseline = database_state(connection)

    with pytest.raises(BasePresentationOperationError):
        proposal_service.confirm(proposal)

    assert database_state(connection) == baseline


def test_confirming_a_closed_exit_without_closed_stock_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la revalidación de la disponibilidad cerrada al confirmar."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service
    )
    proposal = proposal_service.create(
        ClosedExitPayload(
            product_id=stored.id,
            presentation_id=box.id,
            quantity=1,
            reason="Venta",
        )
    )
    baseline = database_state(connection)

    with pytest.raises(InsufficientClosedStockError):
        proposal_service.confirm(proposal)

    assert database_state(connection) == baseline


def test_confirming_an_aperture_without_closed_stock_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la revalidación del stock cerrado de una apertura."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service
    )
    proposal = proposal_service.create(
        AperturePayload(
            product_id=stored.id,
            presentation_id=box.id,
            quantity=1,
            reason="Apertura",
        )
    )
    baseline = database_state(connection)

    with pytest.raises(InsufficientClosedStockError):
        proposal_service.confirm(proposal)

    assert database_state(connection) == baseline


def test_confirming_a_compensation_beyond_the_compensable_quantity_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica el rechazo de una compensación que supera lo compensable."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=2
    )
    entry = movements.list_by_product(stored.id, MovementKind.CLOSED_ENTRY)[0]
    proposal = proposal_service.create(
        CompensationPayload(
            product_id=stored.id,
            movement_id=entry.id,
            quantity=3,
            reason="Corrección",
        )
    )
    baseline = database_state(connection)

    with pytest.raises(ExcessCompensationError):
        proposal_service.confirm(proposal)

    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert database_state(connection) == baseline


def test_confirming_a_compensation_of_a_closed_exit_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica que una salida cerrada no es compensable en el MVP."""
    stored, _, box = prepare_product(
        product_service, presentations, movement_service, closed_stock=3
    )
    closed_exit = movement_service.register_closed_exit(
        stored.id,
        presentation_id=box.id,
        quantity=1,
        reason="Venta",
    )
    proposal = proposal_service.create(
        CompensationPayload(
            product_id=stored.id,
            movement_id=closed_exit.id,
            quantity=1,
            reason="Corrección",
        )
    )
    baseline = database_state(connection)

    with pytest.raises(NotCompensableError) as error:
        proposal_service.confirm(proposal)

    assert type(error.value) is NotCompensableError
    assert stored_presentation(presentations, box.id).closed_stock == 2
    assert database_state(connection) == baseline


def test_confirming_a_compensation_of_a_missing_movement_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica que el movimiento original se revalida al confirmar."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service, closed_stock=2
    )
    proposal = proposal_service.create(
        CompensationPayload(
            product_id=stored.id,
            movement_id=999,
            quantity=1,
            reason="Corrección",
        )
    )
    baseline = database_state(connection)

    with pytest.raises(RecordNotFoundError):
        proposal_service.confirm(proposal)

    assert database_state(connection) == baseline


def test_confirming_a_movement_without_a_reason_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la revalidación del motivo obligatorio del movimiento."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service, loose_units=5
    )
    proposal = proposal_service.create(
        LooseEntryPayload(product_id=stored.id, quantity=1, reason="   ")
    )
    baseline = database_state(connection)

    with pytest.raises(InvalidMovementReasonError):
        proposal_service.confirm(proposal)

    assert database_state(connection) == baseline


def test_confirming_a_product_registration_with_a_duplicate_sku_is_rejected(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la revalidación del SKU único al confirmar."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service
    )
    proposal = proposal_service.create(
        ProductRegistrationPayload(
            sku="sku-1",
            name="Otro producto",
            base_unit="unidad",
            base_sale_price=Decimal("50"),
        )
    )
    baseline = database_state(connection)

    with pytest.raises(DuplicateSkuError):
        proposal_service.confirm(proposal)

    assert stored_product(products, stored.id).sku == "SKU-1"
    assert database_state(connection) == baseline


def test_confirming_a_repeated_presentation_name_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica la revalidación del nombre de presentación único."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service
    )
    proposal = proposal_service.create(
        PresentationRegistrationPayload(
            product_id=stored.id,
            name="caja x6",
            factor=6,
            sale_price=Decimal("600"),
        )
    )
    baseline = database_state(connection)

    with pytest.raises(DuplicatePresentationNameError):
        proposal_service.confirm(proposal)

    assert database_state(connection) == baseline


def test_confirming_a_presentation_with_an_invalid_price_is_rejected(
    connection: sqlite3.Connection,
    presentations: SqlitePresentationRepository,
    product_service: ProductService,
    movement_service: MovementService,
    proposal_service: ProposalService,
) -> None:
    """Verifica que el precio de venta se revalida al confirmar."""
    stored, _, _ = prepare_product(
        product_service, presentations, movement_service
    )
    proposal = proposal_service.create(
        PresentationRegistrationPayload(
            product_id=stored.id,
            name="Caja x12",
            factor=12,
            sale_price=Decimal("0"),
        )
    )
    baseline = database_state(connection)

    with pytest.raises(QuantityError):
        proposal_service.confirm(proposal)

    assert database_state(connection) == baseline


# --- Esquemas: valores monetarios, unión discriminada e inmutabilidad ---


def test_monetary_values_reject_float() -> None:
    """Verifica que los valores monetarios no admiten `float` (plan técnico)."""
    payloads: tuple[Callable[[], Any], ...] = (
        lambda: ProductRegistrationPayload(
            sku="SKU-2",
            name="Otro producto",
            base_unit="unidad",
            base_sale_price=10.5,
        ),
        lambda: PresentationRegistrationPayload(
            product_id=1, name="Caja x12", factor=12, sale_price=10.5
        ),
        lambda: LooseEntryPayload(
            product_id=1, quantity=1, reason="Compra", cost=10.5
        ),
        lambda: ClosedEntryPayload(
            product_id=1,
            presentation_id=2,
            quantity=1,
            reason="Compra",
            cost=10.5,
        ),
    )

    for build_payload in payloads:
        with pytest.raises(ValidationError):
            build_payload()


def test_monetary_values_accept_an_exact_decimal_or_text() -> None:
    """Verifica que los valores monetarios admiten `Decimal` exacto y texto."""
    decimal_cost = LooseEntryPayload(
        product_id=1, quantity=1, reason="Compra", cost=Decimal("10.50")
    )
    text_cost = LooseEntryPayload(
        product_id=1, quantity=1, reason="Compra", cost="10.50"
    )

    assert decimal_cost.cost == Decimal("10.50")
    assert text_cost.cost == Decimal("10.50")


def test_the_payload_union_is_discriminated_by_the_action() -> None:
    """Verifica que la unión resuelve el payload por su acción."""
    proposal = Proposal(
        action=ProposalAction.CLOSED_ENTRY,
        payload={
            "action": "entrada_cerrada",
            "product_id": 1,
            "presentation_id": 2,
            "quantity": 3,
            "reason": "Compra",
            "cost": "100.00",
        },
    )

    assert isinstance(proposal.payload, ClosedEntryPayload)
    assert proposal.payload.presentation_id == 2
    assert proposal.payload.cost == Decimal("100.00")


def test_the_payload_union_rejects_an_unknown_action() -> None:
    """Verifica que una acción desconocida no se valida como payload."""
    with pytest.raises(ValidationError):
        Proposal(
            action=ProposalAction.LOOSE_ENTRY,
            payload={"action": "desactivacion_producto", "product_id": 1},
        )


def test_a_proposal_requires_the_action_of_its_payload() -> None:
    """Verifica que la acción declarada corresponda al payload."""
    with pytest.raises(ValidationError):
        Proposal(
            action=ProposalAction.LOOSE_EXIT,
            payload=LooseEntryPayload(
                product_id=1, quantity=2, reason="Compra"
            ),
        )


def test_payloads_reject_unknown_fields_and_values_out_of_range() -> None:
    """Verifica el rechazo de campos desconocidos y de valores inválidos."""
    with pytest.raises(ValidationError):
        LooseEntryPayload(
            product_id=1, quantity=2, reason="Compra", price=Decimal("1")
        )

    with pytest.raises(ValidationError):
        LooseEntryPayload(product_id=1, quantity=0, reason="Compra")

    with pytest.raises(ValidationError):
        ProductUpdatePayload(product_id=1)

    with pytest.raises(ValidationError):
        ProductUpdatePayload(product_id=1, minimum_stock=-1)


def test_payloads_leave_the_mandatory_reason_to_the_domain() -> None:
    """Verifica que la obligatoriedad del motivo la valida el dominio (RF-03)."""
    payload = LooseEntryPayload(product_id=1, quantity=1, reason="   ")

    assert payload.reason == "   "


def test_proposals_and_payloads_are_immutable() -> None:
    """Verifica que los payloads y las propuestas no se modifican."""
    payload = LooseEntryPayload(product_id=1, quantity=2, reason="Compra")

    with pytest.raises(ValidationError):
        payload.quantity = 3  # type: ignore[misc]

    proposal = Proposal(
        action=ProposalAction.LOOSE_ENTRY, payload=payload
    )

    with pytest.raises(ValidationError):
        proposal.status = ProposalStatus.CONFIRMED  # type: ignore[misc]
