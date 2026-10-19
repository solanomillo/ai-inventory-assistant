"""Pruebas de las herramientas de propuesta del asistente (T020).

Cubren los contratos de las once herramientas de RF-05: las diez creaciones
—una por acción de `ProposalAction`, incluida la apertura y el cambio de
precio— y la edición de una propuesta pendiente. Las pruebas usan la base
SQLite en memoria con el esquema real, los repositorios de T009 y los servicios
de T011 a T015, e invocan las herramientas con `tool.invoke({...})`: no
requieren el LLM, la API de DeepSeek, los agentes ni el grafo.

Se verifica que crear y editar no modifican el inventario ni la tabla
`proposals`, que toda propuesta queda `PENDING`, que el campo constante
`action` no puede cambiarse, que los errores tipados del servicio y de
Pydantic se propagan y que ninguna herramienta confirma propuestas.
"""

import inspect
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from typing import Any

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ValidationError

from app.domain.movements import MovementKind
from app.domain.quantities import QuantityError
from app.infrastructure.sqlite.connection import (
    IN_MEMORY_DATABASE,
    connect,
    transaction,
)
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqlitePriceHistoryRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.schemas.proposals import (
    AperturePayload,
    ClosedEntryPayload,
    ClosedExitPayload,
    CompensationPayload,
    LooseEntryPayload,
    LooseExitPayload,
    PresentationRegistrationPayload,
    PriceUpdatePayload,
    ProductRegistrationPayload,
    ProductUpdatePayload,
    Proposal,
    ProposalAction,
    ProposalStatus,
)
from app.services.movements import MovementService
from app.services.ports import (
    PRICE_CHANGE_EVENT_TYPE,
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
from app.tools import proposal_tools
from app.tools.proposal_tools import (
    EditProposalInput,
    create_proposal_tools,
)

CREATION_TOOL_NAMES: tuple[str, ...] = (
    "propose_product_registration",
    "propose_product_update",
    "propose_presentation_registration",
    "propose_loose_entry",
    "propose_closed_entry",
    "propose_loose_exit",
    "propose_closed_exit",
    "propose_aperture",
    "propose_compensation",
    "propose_price_update",
)
"""Nombres de las diez herramientas de creación, una por acción."""


@dataclass(frozen=True)
class Catalog:
    """Inventario controlado de las pruebas (datos de prueba, no de la app)."""

    product: ProductRecord
    base: PresentationRecord
    box: PresentationRecord
    entry: MovementRecord


@dataclass(frozen=True)
class CreationCase:
    """Caso de prueba de una herramienta de creación."""

    arguments: dict[str, Any]
    action: ProposalAction
    payload_type: type[BaseModel]
    fields: dict[str, Any]


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
        price_history=SqlitePriceHistoryRepository(connection),
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


@pytest.fixture
def tools(proposal_service: ProposalService) -> dict[str, BaseTool]:
    """Herramientas de propuesta indexadas por su nombre."""
    return {
        created.name: created
        for created in create_proposal_tools(proposal_service)
    }


@pytest.fixture
def catalog(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    movement_service: MovementService,
) -> Catalog:
    """Registra el inventario controlado con los servicios reales."""
    stored = product_service.register(
        ProductRegistration(
            sku="SKU-1",
            name="Producto",
            base_unit="botella",
            base_sale_price=Decimal("100"),
            presentations=(
                InitialClosedStock(
                    name="Caja x6",
                    factor=6,
                    sale_price=Decimal("600"),
                ),
            ),
        )
    )
    base, box = presentations.list_by_product(stored.id)
    movement_service.register_loose_entry(
        stored.id, quantity=5, reason="Compra"
    )
    movement_service.register_closed_entry(
        stored.id, presentation_id=box.id, quantity=2, reason="Compra"
    )
    entry = movements.list_by_product(
        stored.id, MovementKind.CLOSED_ENTRY
    )[0]

    return Catalog(
        product=stored,
        base=base,
        box=box,
        entry=entry,
    )


def creation_case(tool_name: str, catalog: Catalog) -> CreationCase:
    """
    Construye el caso de prueba de cada herramienta de creación.

    Los argumentos referencian los identificadores reales del inventario de
    prueba, de modo que las propuestas creadas sean coherentes con los datos
    registrados.
    """
    product_id = catalog.product.id
    box_id = catalog.box.id
    movement_id = catalog.entry.id
    cases: dict[str, CreationCase] = {
        "propose_product_registration": CreationCase(
            arguments={
                "sku": "SKU-2",
                "name": "Otro producto",
                "base_unit": "botella",
                "base_sale_price": Decimal("50"),
            },
            action=ProposalAction.PRODUCT_REGISTRATION,
            payload_type=ProductRegistrationPayload,
            fields={
                "sku": "SKU-2",
                "name": "Otro producto",
                "base_unit": "botella",
                "minimum_stock": 0,
                "initial_loose_units": 0,
            },
        ),
        "propose_product_update": CreationCase(
            arguments={
                "product_id": product_id,
                "name": "Nombre nuevo",
                "minimum_stock": 9,
            },
            action=ProposalAction.PRODUCT_UPDATE,
            payload_type=ProductUpdatePayload,
            fields={
                "product_id": product_id,
                "name": "Nombre nuevo",
                "minimum_stock": 9,
            },
        ),
        "propose_presentation_registration": CreationCase(
            arguments={
                "product_id": product_id,
                "name": "Caja x12",
                "factor": 12,
                "sale_price": Decimal("1200"),
            },
            action=ProposalAction.PRESENTATION_REGISTRATION,
            payload_type=PresentationRegistrationPayload,
            fields={
                "product_id": product_id,
                "name": "Caja x12",
                "factor": 12,
                "sale_price": Decimal("1200"),
            },
        ),
        "propose_loose_entry": CreationCase(
            arguments={
                "product_id": product_id,
                "quantity": 3,
                "reason": "Compra",
                "cost": Decimal("10.50"),
            },
            action=ProposalAction.LOOSE_ENTRY,
            payload_type=LooseEntryPayload,
            fields={
                "product_id": product_id,
                "quantity": 3,
                "reason": "Compra",
                "cost": Decimal("10.50"),
            },
        ),
        "propose_closed_entry": CreationCase(
            arguments={
                "product_id": product_id,
                "presentation_id": box_id,
                "quantity": 2,
                "reason": "Compra",
                "cost": Decimal("100"),
            },
            action=ProposalAction.CLOSED_ENTRY,
            payload_type=ClosedEntryPayload,
            fields={
                "product_id": product_id,
                "presentation_id": box_id,
                "quantity": 2,
                "cost": Decimal("100"),
            },
        ),
        "propose_loose_exit": CreationCase(
            arguments={
                "product_id": product_id,
                "quantity": 2,
                "reason": "Venta",
            },
            action=ProposalAction.LOOSE_EXIT,
            payload_type=LooseExitPayload,
            fields={
                "product_id": product_id,
                "quantity": 2,
                "reason": "Venta",
            },
        ),
        "propose_closed_exit": CreationCase(
            arguments={
                "product_id": product_id,
                "presentation_id": box_id,
                "quantity": 1,
                "reason": "Venta",
            },
            action=ProposalAction.CLOSED_EXIT,
            payload_type=ClosedExitPayload,
            fields={
                "product_id": product_id,
                "presentation_id": box_id,
                "quantity": 1,
                "reason": "Venta",
            },
        ),
        "propose_aperture": CreationCase(
            arguments={
                "product_id": product_id,
                "presentation_id": box_id,
                "quantity": 1,
                "reason": "Apertura",
            },
            action=ProposalAction.APERTURE,
            payload_type=AperturePayload,
            fields={
                "product_id": product_id,
                "presentation_id": box_id,
                "quantity": 1,
                "reason": "Apertura",
            },
        ),
        "propose_compensation": CreationCase(
            arguments={
                "product_id": product_id,
                "movement_id": movement_id,
                "quantity": 2,
                "reason": "Corrección",
            },
            action=ProposalAction.COMPENSATION,
            payload_type=CompensationPayload,
            fields={
                "product_id": product_id,
                "movement_id": movement_id,
                "quantity": 2,
                "reason": "Corrección",
            },
        ),
        "propose_price_update": CreationCase(
            arguments={
                "presentation_id": box_id,
                "new_price": Decimal("700"),
            },
            action=ProposalAction.PRICE_UPDATE,
            payload_type=PriceUpdatePayload,
            fields={
                "presentation_id": box_id,
                "new_price": Decimal("700"),
            },
        ),
    }

    return cases[tool_name]


def snapshot(
    connection: sqlite3.Connection,
) -> dict[str, list[tuple[Any, ...]]]:
    """Captura el contenido de las tablas, ordenado, para comparar."""
    tables = (
        "products",
        "presentations",
        "movements",
        "proposals",
        "price_changes",
    )

    return {
        table: [
            tuple(row)
            for row in connection.execute(
                f"SELECT * FROM {table} ORDER BY id"
            ).fetchall()
        ]
        for table in tables
    }


# --- Las diez creaciones (criterio 1) ---


def test_the_creation_tools_cover_every_action(catalog: Catalog) -> None:
    """Verifica que cada acción de `ProposalAction` tiene su herramienta."""
    actions = {
        creation_case(tool_name, catalog).action
        for tool_name in CREATION_TOOL_NAMES
    }

    assert len(CREATION_TOOL_NAMES) == 10
    assert len(actions) == 10
    assert actions == set(ProposalAction)


@pytest.mark.parametrize("tool_name", CREATION_TOOL_NAMES)
def test_each_creation_tool_builds_a_pending_proposal(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    tool_name: str,
) -> None:
    """Verifica la propuesta de cada una de las diez acciones."""
    case = creation_case(tool_name, catalog)

    proposal = tools[tool_name].invoke(case.arguments)

    assert isinstance(proposal, Proposal)
    assert proposal.status is ProposalStatus.PENDING
    assert proposal.action is case.action
    assert isinstance(proposal.payload, case.payload_type)
    assert proposal.payload.action == case.action.value
    for field, value in case.fields.items():
        assert getattr(proposal.payload, field) == value, field


@pytest.mark.parametrize("tool_name", CREATION_TOOL_NAMES)
def test_a_creation_tool_rejects_another_action(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    tool_name: str,
) -> None:
    """Verifica que la acción de la herramienta no puede cambiarse."""
    case = creation_case(tool_name, catalog)
    wrong_action = next(
        action.value for action in ProposalAction if action is not case.action
    )
    arguments = dict(case.arguments)
    arguments["action"] = wrong_action

    with pytest.raises(ValidationError):
        tools[tool_name].invoke(arguments)


def test_proposing_a_product_registration_validates_its_presentations(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica el payload anidado de las presentaciones iniciales."""
    proposal = tools["propose_product_registration"].invoke(
        {
            "sku": "SKU-2",
            "name": "Otro producto",
            "base_unit": "botella",
            "base_sale_price": "50",
            "initial_loose_units": 2,
            "presentations": [
                {
                    "name": "Caja x6",
                    "factor": 6,
                    "sale_price": "600",
                    "closed_stock": 2,
                }
            ],
        }
    )

    assert isinstance(proposal, Proposal)
    assert proposal.status is ProposalStatus.PENDING
    assert proposal.payload.initial_loose_units == 2
    assert len(proposal.payload.presentations) == 1
    assert proposal.payload.presentations[0].name == "Caja x6"
    assert proposal.payload.presentations[0].closed_stock == 2


# --- Cambio de precio (T032) ---


def test_the_price_update_action_is_available() -> None:
    """Verifica la acción de cambio de precio y su lugar en la unión (T032)."""
    proposal = Proposal(
        action=ProposalAction.PRICE_UPDATE,
        payload={
            "action": "cambio_precio",
            "presentation_id": 1,
            "new_price": "700",
        },
    )

    assert ProposalAction("cambio_precio") is ProposalAction.PRICE_UPDATE
    assert isinstance(proposal.payload, PriceUpdatePayload)
    assert proposal.payload.presentation_id == 1
    assert proposal.payload.new_price == Decimal("700")
    assert proposal.status is ProposalStatus.PENDING


@pytest.mark.parametrize(
    "arguments",
    [
        {"presentation_id": 0, "new_price": Decimal("10")},
        {"presentation_id": -1, "new_price": Decimal("10")},
        {"presentation_id": 1, "new_price": Decimal("0")},
        {"presentation_id": 1, "new_price": Decimal("-1")},
        {"presentation_id": 1, "new_price": 10.5},
        {"presentation_id": 1, "new_price": Decimal("10"), "product_id": 1},
    ],
)
def test_price_update_payload_rejects_invalid_arguments(
    arguments: dict[str, Any],
) -> None:
    """Verifica las restricciones formales del payload de cambio de precio."""
    with pytest.raises(ValidationError):
        PriceUpdatePayload(**arguments)


def test_a_price_update_proposal_is_pending_without_writing(
    connection: sqlite3.Connection,
    proposal_service: ProposalService,
    catalog: Catalog,
) -> None:
    """Verifica la propuesta de precio pendiente y sin escrituras (T032)."""
    before = snapshot(connection)
    changes_before = connection.total_changes

    proposal = proposal_service.create(
        PriceUpdatePayload(
            presentation_id=catalog.box.id, new_price=Decimal("700")
        )
    )

    assert isinstance(proposal, Proposal)
    assert proposal.status is ProposalStatus.PENDING
    assert proposal.action is ProposalAction.PRICE_UPDATE
    assert isinstance(proposal.payload, PriceUpdatePayload)
    assert proposal.payload.presentation_id == catalog.box.id
    assert proposal.payload.new_price == Decimal("700")

    edited = proposal_service.edit(
        proposal,
        PriceUpdatePayload(
            presentation_id=catalog.box.id, new_price=Decimal("750")
        ),
    )

    assert edited.status is ProposalStatus.PENDING
    assert edited.action is ProposalAction.PRICE_UPDATE
    assert edited.payload.new_price == Decimal("750")
    assert proposal.payload.new_price == Decimal("700")

    assert snapshot(connection) == before
    assert connection.total_changes == changes_before


# --- Edición de propuestas (criterios 2, 3 y 4) ---


def test_editing_a_pending_proposal_replaces_the_payload(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    proposal_service: ProposalService,
) -> None:
    """Verifica la edición de una propuesta pendiente y su inmutabilidad."""
    original = proposal_service.create(
        LooseEntryPayload(
            product_id=catalog.product.id, quantity=2, reason="Compra"
        )
    )

    edited = tools["edit_proposal"].invoke(
        {
            "proposal": original,
            "payload": {
                "action": "entrada_suelta",
                "product_id": catalog.product.id,
                "quantity": 7,
                "reason": "Compra corregida",
            },
        }
    )

    assert isinstance(edited, Proposal)
    assert edited is not original
    assert edited.status is ProposalStatus.PENDING
    assert edited.action is ProposalAction.LOOSE_ENTRY
    assert isinstance(edited.payload, LooseEntryPayload)
    assert edited.payload.quantity == 7
    assert edited.payload.reason == "Compra corregida"
    assert original.status is ProposalStatus.PENDING
    assert original.payload.quantity == 2
    assert original.payload.reason == "Compra"


def test_editing_a_confirmed_proposal_is_rejected(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    proposal_service: ProposalService,
) -> None:
    """Verifica que una propuesta confirmada no se edita."""
    payload = LooseEntryPayload(
        product_id=catalog.product.id, quantity=2, reason="Compra"
    )
    confirmed = proposal_service.confirm(
        proposal_service.create(payload)
    )

    with pytest.raises(ProposalAlreadyConfirmedError):
        tools["edit_proposal"].invoke(
            {"proposal": confirmed.proposal, "payload": payload}
        )

    assert confirmed.proposal.status is ProposalStatus.CONFIRMED


def test_editing_with_a_payload_of_another_action_is_rejected(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    proposal_service: ProposalService,
) -> None:
    """Verifica que la edición no puede cambiar la acción de la propuesta."""
    proposal = proposal_service.create(
        LooseEntryPayload(
            product_id=catalog.product.id, quantity=2, reason="Compra"
        )
    )

    with pytest.raises(ProposalActionMismatchError):
        tools["edit_proposal"].invoke(
            {
                "proposal": proposal,
                "payload": {
                    "action": "salida_suelta",
                    "product_id": catalog.product.id,
                    "quantity": 2,
                    "reason": "Venta",
                },
            }
        )


# --- Entradas inválidas (criterio 5) ---


def test_invalid_inputs_are_rejected_by_pydantic(
    tools: dict[str, BaseTool],
    catalog: Catalog,
) -> None:
    """Verifica el rechazo de las entradas inválidas en el límite."""
    product_id = catalog.product.id

    with pytest.raises(ValidationError):
        tools["propose_loose_entry"].invoke(
            {"product_id": product_id, "quantity": 1}
        )

    with pytest.raises(ValidationError):
        tools["propose_loose_entry"].invoke(
            {"product_id": product_id, "quantity": 0, "reason": "Compra"}
        )

    with pytest.raises(ValidationError):
        tools["propose_loose_entry"].invoke(
            {
                "product_id": product_id,
                "quantity": 1,
                "reason": "Compra",
                "cost": 10.5,
            }
        )

    with pytest.raises(ValidationError):
        tools["propose_presentation_registration"].invoke(
            {
                "product_id": product_id,
                "name": "Caja x12",
                "factor": 12,
                "sale_price": 1200.5,
            }
        )

    with pytest.raises(ValidationError):
        tools["propose_product_registration"].invoke(
            {
                "sku": "SKU-2",
                "name": "Otro producto",
                "base_unit": "botella",
                "base_sale_price": "50",
                "presentations": [
                    {"name": "Caja x6", "factor": 6, "sale_price": 600.5}
                ],
            }
        )

    with pytest.raises(ValidationError):
        tools["propose_loose_exit"].invoke(
            {
                "product_id": product_id,
                "quantity": 1,
                "reason": "Venta",
                "price": "10",
            }
        )

    with pytest.raises(ValidationError):
        tools["propose_product_update"].invoke({"product_id": product_id})

    with pytest.raises(ValidationError):
        tools["edit_proposal"].invoke(
            {
                "proposal": {
                    "action": "entrada_suelta",
                    "payload": {
                        "action": "entrada_suelta",
                        "product_id": product_id,
                        "quantity": 1,
                        "reason": "Compra",
                    },
                },
                "payload": {
                    "action": "desactivacion_producto",
                    "product_id": product_id,
                },
            }
        )


# --- Confirmación del cambio de precio (T033) ---


def test_confirming_a_price_update_applies_it_and_records_the_history(
    connection: sqlite3.Connection,
    catalog: Catalog,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
    proposal_service: ProposalService,
) -> None:
    """
    Verifica que confirmar el precio actualiza la presentación y el historial.
    """
    history = SqlitePriceHistoryRepository(connection)
    balances_before = (
        products.get(catalog.product.id).loose_units,
        presentations.get(catalog.box.id).closed_stock,
    )
    movements_before = movements.list_by_product(catalog.product.id)
    proposal = proposal_service.create(
        PriceUpdatePayload(
            presentation_id=catalog.box.id, new_price=Decimal("700")
        )
    )

    confirmation = proposal_service.confirm(proposal)

    assert confirmation.status is ProposalStatus.CONFIRMED
    assert confirmation.action is ProposalAction.PRICE_UPDATE
    assert isinstance(confirmation.result, PresentationRecord)
    assert confirmation.result.id == catalog.box.id
    assert confirmation.result.sale_price == Decimal("700")
    assert presentations.get(catalog.box.id).sale_price == Decimal("700")
    changes = history.list_by_presentation(catalog.box.id)
    assert len(changes) == 1
    change = changes[0]
    assert change.product_id == catalog.product.id
    assert change.previous_price == catalog.box.sale_price
    assert change.new_price == Decimal("700")
    assert change.event_type == PRICE_CHANGE_EVENT_TYPE
    assert change.occurred_at.tzinfo is not None
    assert change.occurred_at.year >= 2026
    assert (
        products.get(catalog.product.id).loose_units,
        presentations.get(catalog.box.id).closed_stock,
    ) == balances_before
    assert movements.list_by_product(catalog.product.id) == movements_before


def test_confirming_a_price_update_of_a_missing_presentation_is_rejected(
    connection: sqlite3.Connection,
    proposal_service: ProposalService,
) -> None:
    """Verifica el rechazo cuando la presentación no existe (T033)."""
    history = SqlitePriceHistoryRepository(connection)
    proposal = proposal_service.create(
        PriceUpdatePayload(presentation_id=999, new_price=Decimal("700"))
    )

    with pytest.raises(RecordNotFoundError):
        proposal_service.confirm(proposal)

    assert proposal.status is ProposalStatus.PENDING
    assert history.list_by_presentation(999) == []


def test_confirming_a_price_update_of_a_deactivated_product_is_rejected(
    connection: sqlite3.Connection,
    catalog: Catalog,
    product_service: ProductService,
    movements: SqliteMovementRepository,
    proposal_service: ProposalService,
) -> None:
    """Verifica el rechazo sobre un producto desactivado (T033)."""
    history = SqlitePriceHistoryRepository(connection)
    movements_before = movements.list_by_product(catalog.product.id)
    proposal = proposal_service.create(
        PriceUpdatePayload(
            presentation_id=catalog.box.id, new_price=Decimal("700")
        )
    )
    product_service.deactivate(catalog.product.id)
    before = snapshot(connection)

    with pytest.raises(InactiveProductError):
        proposal_service.confirm(proposal)

    assert proposal.status is ProposalStatus.PENDING
    assert snapshot(connection) == before
    assert history.list_by_presentation(catalog.box.id) == []
    assert movements.list_by_product(catalog.product.id) == movements_before


def test_a_price_update_with_a_non_finite_price_cannot_be_confirmed(
    connection: sqlite3.Connection,
    catalog: Catalog,
    presentations: SqlitePresentationRepository,
    proposal_service: ProposalService,
) -> None:
    """
    Verifica que un precio no finito no se puede proponer ni confirmar (T033).

    El esquema de la propuesta ya rechaza `Decimal("Infinity")` porque Pydantic
    exige un número finito, así que el asistente no puede crearlo. Para
    comprobar además la defensa del dominio se construye un payload sin
    validación y se confirma: el servicio lo rechaza con `QuantityError`, sin
    escribir el precio ni el historial.
    """
    history = SqlitePriceHistoryRepository(connection)
    before = snapshot(connection)

    with pytest.raises(ValidationError):
        PriceUpdatePayload(
            presentation_id=catalog.box.id, new_price=Decimal("Infinity")
        )

    pending = proposal_service.create(
        PriceUpdatePayload.model_construct(
            presentation_id=catalog.box.id, new_price=Decimal("Infinity")
        )
    )

    with pytest.raises(QuantityError):
        proposal_service.confirm(pending)

    assert pending.status is ProposalStatus.PENDING
    assert presentations.get(catalog.box.id).sale_price == (
        catalog.box.sale_price
    )
    assert snapshot(connection) == before
    assert history.list_by_presentation(catalog.box.id) == []


# --- Contrato de las herramientas y límite de la confirmación (criterio 7) ---


def test_the_tools_expose_the_expected_names(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica las once herramientas y la ausencia de confirmación."""
    assert set(tools) == set(CREATION_TOOL_NAMES) | {"edit_proposal"}
    assert len(tools) == 11
    assert [name for name in tools if "confirm" in name] == []
    assert all(created.description for created in tools.values())


@pytest.mark.parametrize("tool_name", CREATION_TOOL_NAMES)
def test_each_creation_tool_declares_its_payload_as_the_input_schema(
    tools: dict[str, BaseTool],
    catalog: Catalog,
    tool_name: str,
) -> None:
    """Verifica que cada herramienta valida su entrada con su payload."""
    case = creation_case(tool_name, catalog)

    assert tools[tool_name].args_schema is case.payload_type


def test_the_edit_tool_declares_the_proposal_and_the_payload_union(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica el esquema de entrada de la edición (Ajuste 2)."""
    assert tools["edit_proposal"].args_schema is EditProposalInput


def test_the_constant_action_is_declared_in_the_input_schema(
    tools: dict[str, BaseTool],
) -> None:
    """Verifica que el esquema expone la acción constante del payload."""
    schema = tools["propose_loose_entry"].args_schema.model_json_schema()
    action = schema["properties"]["action"]

    assert action["const"] == "entrada_suelta"


def test_the_module_does_not_offer_the_confirmation() -> None:
    """Verifica que el módulo no invoca la operación de confirmación."""
    source = inspect.getsource(proposal_tools)

    assert "confirm(" not in source
    assert ".confirm" not in source


def test_the_module_does_not_depend_on_infrastructure_or_agents() -> None:
    """Verifica que el módulo no importa persistencia ni agentes."""
    source = inspect.getsource(proposal_tools)

    forbidden = (
        "app.infrastructure",
        "import sqlite3",
        "app.agents",
        "app.graph",
        "app.services.llm",
        "ChatDeepSeek",
        "get_llm",
        "DEEPSEEK",
    )
    for token in forbidden:
        assert token not in source, f"El módulo no debe contener `{token}`."


# --- Ausencia de escrituras (criterio 6) ---


def test_the_tools_do_not_write_to_the_database(
    connection: sqlite3.Connection,
    tools: dict[str, BaseTool],
    catalog: Catalog,
    proposal_service: ProposalService,
) -> None:
    """Verifica que crear y editar no modifican la base ni persisten."""
    pending = proposal_service.create(
        LooseEntryPayload(
            product_id=catalog.product.id, quantity=2, reason="Compra"
        )
    )
    before = snapshot(connection)
    changes_before = connection.total_changes

    for tool_name in CREATION_TOOL_NAMES:
        tools[tool_name].invoke(creation_case(tool_name, catalog).arguments)
    edited = tools["edit_proposal"].invoke(
        {
            "proposal": pending,
            "payload": {
                "action": "entrada_suelta",
                "product_id": catalog.product.id,
                "quantity": 7,
                "reason": "Compra",
            },
        }
    )

    assert edited.status is ProposalStatus.PENDING
    assert snapshot(connection) == before
    assert snapshot(connection)["proposals"] == []
    assert connection.total_changes == changes_before
