"""Pruebas de concurrencia e integridad del inventario (T016).

Verifican que las confirmaciones simultáneas respetan las reglas del dominio y
dejan el estado persistido coherente, según RF-02 y RF-03: no hay stock
negativo, no se supera la cantidad compensable ni el stock disponible, el
consumo FIFO y sus contadores quedan consistentes, existe una única
presentación base por producto y una operación rechazada o revertida no deja
cambios parciales.

Las pruebas usan **conexiones SQLite independientes sobre un archivo temporal
compartido** (nunca `:memory:`) y coordinan los hilos con `threading.Barrier`,
sin `sleep`, de modo que el intercalado sea reproducible. La transacción
compartida es `BEGIN IMMEDIATE`, así que las operaciones se serializan sobre el
bloqueo de escritura y cada una valida sobre el estado vigente. El estado final
se verifica siempre con lecturas nuevas sobre una conexión nueva.
"""

import sqlite3
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from app.domain.movements import ExcessCompensationError, MovementKind
from app.domain.presentations import PresentationError
from app.domain.products import DuplicateSkuError
from app.domain.stock import (
    ClosedStock,
    InsufficientClosedStockError,
    total_equivalent,
)
from app.infrastructure.sqlite.connection import connect, transaction
from app.infrastructure.sqlite.repositories import (
    SqliteMovementRepository,
    SqlitePresentationRepository,
    SqliteProductRepository,
)
from app.infrastructure.sqlite.schema import create_schema
from app.services.movements import (
    InsufficientLooseUnitsError,
    MovementService,
)
from app.services.presentations import PresentationService
from app.services.ports import ProductRecord
from app.services.products import (
    InactiveProductError,
    InitialClosedStock,
    ProductRegistration,
    ProductService,
)

BARRIER_TIMEOUT_SECONDS: float = 10.0
"""Espera máxima de la barrera, para no colgar la suite ante un fallo."""

REDUCED_TIMEOUT_SECONDS: float = 0.05
"""Timeout reducido para forzar `SQLITE_BUSY` sin esperas largas (T016)."""


@dataclass
class Services:
    """Servicios de aplicación abiertos sobre una conexión propia."""

    connection: sqlite3.Connection
    products: SqliteProductRepository
    presentations: SqlitePresentationRepository
    movements: SqliteMovementRepository
    product: ProductService
    presentation: PresentationService
    movement: MovementService

    def close(self) -> None:
        """Cierra la conexión asociada a los servicios."""
        self.connection.close()


def open_services(database_path: Path) -> Services:
    """Abre una conexión nueva sobre el archivo y construye sus servicios."""
    connection = connect(database_path)
    products = SqliteProductRepository(connection)
    presentations = SqlitePresentationRepository(connection)
    movements = SqliteMovementRepository(connection)
    scope = partial(transaction, connection)

    return Services(
        connection=connection,
        products=products,
        presentations=presentations,
        movements=movements,
        product=ProductService(
            products=products,
            presentations=presentations,
            movements=movements,
            transaction=scope,
        ),
        presentation=PresentationService(
            products=products,
            presentations=presentations,
            movements=movements,
            transaction=scope,
        ),
        movement=MovementService(
            products=products,
            presentations=presentations,
            movements=movements,
            transaction=scope,
        ),
    )


@contextmanager
def services_on(database_path: Path) -> Iterator[Services]:
    """Abre los servicios sobre una conexión propia y la cierra al salir."""
    services = open_services(database_path)
    try:
        yield services
    finally:
        services.close()


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    """Crea un archivo temporal con el esquema y devuelve su ruta."""
    path = tmp_path / "inventory.db"
    connection = connect(path)
    try:
        create_schema(connection)
    finally:
        connection.close()

    return path


def closed_presentation(
    *,
    name: str = "Caja x6",
    factor: int = 6,
    closed_stock: int = 0,
) -> InitialClosedStock:
    """Declara una presentación cerrada con su stock inicial."""
    return InitialClosedStock(
        name=name,
        factor=factor,
        sale_price=Decimal("600.00"),
        closed_stock=closed_stock,
    )


def seed_product(
    database_path: Path,
    *,
    sku: str = "SKU-1",
    minimum_stock: int = 0,
    initial_loose_units: int = 0,
    presentations: tuple[InitialClosedStock, ...] = (),
) -> tuple[int, tuple[int, ...]]:
    """
    Registra un producto con su stock inicial y devuelve sus identificadores.

    El primer identificador de presentación es la presentación base (factor 1)
    y los siguientes corresponden a las presentaciones cerradas declaradas.
    """
    with services_on(database_path) as services:
        stored = services.product.register(
            ProductRegistration(
                sku=sku,
                name="Producto",
                base_unit="unidad",
                base_sale_price=Decimal("100.00"),
                minimum_stock=minimum_stock,
                initial_loose_units=initial_loose_units,
                presentations=presentations,
            )
        )
        presentation_ids = tuple(
            record.id
            for record in services.presentations.list_by_product(stored.id)
        )

    return stored.id, presentation_ids


@dataclass(frozen=True)
class Outcome:
    """Resultado de un trabajador concurrente: valor o error capturado."""

    value: Any = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        """Indica si el trabajador terminó sin error."""
        return self.error is None


def attempt(operation: Callable[[], Any]) -> Outcome:
    """Ejecuta la operación y captura su resultado o su error."""
    try:
        return Outcome(value=operation())
    except Exception as error:  # noqa: BLE001 - la prueba inspecciona el error
        return Outcome(error=error)


def run_workers(*operations: Callable[[], Any]) -> list[Outcome]:
    """Ejecuta las operaciones en paralelo y devuelve sus resultados."""
    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        futures = [
            executor.submit(attempt, operation) for operation in operations
        ]

        return [future.result() for future in futures]


def run_with_services(
    database_path: Path,
    barrier: threading.Barrier,
    action: Callable[[Services], Any],
) -> Callable[[], Any]:
    """
    Construye la operación de un trabajador con su propia conexión.

    El trabajador espera en la barrera justo antes de ejecutar la acción, de
    modo que las operaciones compitan por el bloqueo de escritura a la vez sin
    usar `sleep`.
    """

    def operation() -> Any:
        with services_on(database_path) as services:
            barrier.wait(timeout=BARRIER_TIMEOUT_SECONDS)

            return action(services)

    return operation


def assert_inventory_invariants(
    services: Services, product_id: int
) -> None:
    """
    Verifica las invariantes mínimas del inventario persistido.

    Comprueba que los saldos no son negativos, que el total equivalente se
    deriva de ellos, que los contadores FIFO no superan la cantidad original,
    que las compensaciones acumuladas de un movimiento no superan su cantidad y
    que el producto conserva exactamente una presentación base (RF-02, RF-03).
    """
    product = services.products.get(product_id)
    assert product is not None
    assert product.loose_units >= 0

    presentations = services.presentations.list_by_product(product_id)
    assert sum(1 for item in presentations if item.factor == 1) == 1

    closed_stocks = []
    for presentation in presentations:
        assert presentation.closed_stock >= 0
        closed_stocks.append(
            ClosedStock(
                factor=presentation.factor,
                quantity=presentation.closed_stock,
            )
        )
    assert total_equivalent(product.loose_units, closed_stocks) >= 0

    movements = services.movements.list_by_product(product_id)
    compensated_by_original: dict[int, int] = {}
    for movement in movements:
        assert movement.consumed_quantity >= 0
        assert movement.compensated_quantity >= 0
        assert (
            movement.consumed_quantity + movement.compensated_quantity
            <= movement.quantity
        )
        if (
            movement.kind is MovementKind.COMPENSATION
            and movement.original_movement_id is not None
        ):
            original_id = movement.original_movement_id
            compensated_by_original[original_id] = (
                compensated_by_original.get(original_id, 0)
                + movement.quantity
            )

    for original_id, compensated in compensated_by_original.items():
        original = services.movements.get(original_id)
        assert original is not None
        assert compensated <= original.quantity


# --- (1) Salidas sueltas simultáneas sin stock para ambas ---


def test_simultaneous_loose_exits_with_insufficient_stock(
    database_path: Path,
) -> None:
    """Dos salidas de 5 sobre 5 unidades: solo una se confirma."""
    product_id, _ = seed_product(database_path, initial_loose_units=5)
    barrier = threading.Barrier(2)

    def exit_five(services: Services) -> Any:
        return services.movement.register_loose_exit(
            product_id, quantity=5, reason="Venta"
        )

    outcomes = run_workers(
        run_with_services(database_path, barrier, exit_five),
        run_with_services(database_path, barrier, exit_five),
    )

    successes = [outcome for outcome in outcomes if outcome.ok]
    failures = [outcome for outcome in outcomes if not outcome.ok]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0].error, InsufficientLooseUnitsError)

    with services_on(database_path) as final:
        product = final.products.get(product_id)
        assert product is not None
        assert product.loose_units == 0
        exits = final.movements.list_by_product(
            product_id, MovementKind.LOOSE_EXIT
        )
        assert len(exits) == 1
        assert exits[0].quantity == 5
        assert_inventory_invariants(final, product_id)


# --- (2) Salidas cerradas simultáneas sobre la misma presentación ---


def test_simultaneous_closed_exits_on_the_same_presentation(
    database_path: Path,
) -> None:
    """Dos salidas cerradas de 3 sobre 3 unidades cerradas: solo una pasa."""
    product_id, presentation_ids = seed_product(
        database_path,
        presentations=(closed_presentation(closed_stock=3),),
    )
    box_id = presentation_ids[1]
    barrier = threading.Barrier(2)

    def exit_three(services: Services) -> Any:
        return services.movement.register_closed_exit(
            product_id,
            presentation_id=box_id,
            quantity=3,
            reason="Venta",
        )

    outcomes = run_workers(
        run_with_services(database_path, barrier, exit_three),
        run_with_services(database_path, barrier, exit_three),
    )

    successes = [outcome for outcome in outcomes if outcome.ok]
    failures = [outcome for outcome in outcomes if not outcome.ok]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0].error, InsufficientClosedStockError)

    with services_on(database_path) as final:
        box = final.presentations.get(box_id)
        assert box is not None
        assert box.closed_stock == 0
        entries = final.movements.list_by_presentation(
            box_id, MovementKind.CLOSED_ENTRY
        )
        assert len(entries) == 1
        assert entries[0].consumed_quantity == 3
        assert entries[0].compensated_quantity == 0
        assert_inventory_invariants(final, product_id)


# --- (3) Compensaciones concurrentes de la misma entrada cerrada ---


def test_concurrent_compensations_of_the_same_closed_entry(
    database_path: Path,
) -> None:
    """
    Una compensación parcial y una total sobre la misma entrada cerrada.

    Solo una se confirma; la perdedora siempre excede la cantidad compensable
    pendiente y se rechaza con `ExcessCompensationError`.
    """
    product_id, presentation_ids = seed_product(
        database_path,
        presentations=(closed_presentation(closed_stock=3),),
    )
    box_id = presentation_ids[1]
    with services_on(database_path) as services:
        entries = services.movements.list_by_presentation(
            box_id, MovementKind.CLOSED_ENTRY
        )
        entry_id = entries[0].id

    barrier = threading.Barrier(2)

    def compensate(services: Services, quantity: int) -> Any:
        return services.movement.register_compensation(
            product_id,
            movement_id=entry_id,
            quantity=quantity,
            reason="Corrección",
        )

    outcomes = run_workers(
        run_with_services(
            database_path,
            barrier,
            lambda services: compensate(services, 2),
        ),
        run_with_services(
            database_path,
            barrier,
            lambda services: compensate(services, 3),
        ),
    )

    successes = [outcome for outcome in outcomes if outcome.ok]
    failures = [outcome for outcome in outcomes if not outcome.ok]
    assert len(successes) == 1
    assert len(failures) == 1
    # La operación perdedora siempre excede la cantidad compensable pendiente:
    # si ganó la parcial queda 1 pendiente; si ganó la total, ninguna.
    assert isinstance(failures[0].error, ExcessCompensationError)

    with services_on(database_path) as final:
        entry = final.movements.get(entry_id)
        assert entry is not None
        assert entry.compensated_quantity in (2, 3)
        assert entry.consumed_quantity + entry.compensated_quantity <= 3
        box = final.presentations.get(box_id)
        assert box is not None
        assert box.closed_stock == 3 - entry.compensated_quantity
        compensations = final.movements.list_by_product(
            product_id, MovementKind.COMPENSATION
        )
        assert len(compensations) == 1
        assert compensations[0].quantity == entry.compensated_quantity
        assert_inventory_invariants(final, product_id)


# --- (4) Compensaciones concurrentes de la misma entrada suelta ---


def test_concurrent_compensations_of_the_same_loose_entry(
    database_path: Path,
) -> None:
    """Dos compensaciones de 4 sobre una entrada suelta de 5: solo una pasa."""
    product_id, _ = seed_product(database_path, initial_loose_units=5)
    with services_on(database_path) as services:
        entries = services.movements.list_by_product(
            product_id, MovementKind.LOOSE_ENTRY
        )
        entry_id = entries[0].id

    barrier = threading.Barrier(2)

    def compensate_four(services: Services) -> Any:
        return services.movement.register_compensation(
            product_id,
            movement_id=entry_id,
            quantity=4,
            reason="Corrección",
        )

    outcomes = run_workers(
        run_with_services(database_path, barrier, compensate_four),
        run_with_services(database_path, barrier, compensate_four),
    )

    successes = [outcome for outcome in outcomes if outcome.ok]
    failures = [outcome for outcome in outcomes if not outcome.ok]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0].error, ExcessCompensationError)

    with services_on(database_path) as final:
        product = final.products.get(product_id)
        assert product is not None
        assert product.loose_units == 1
        compensations = final.movements.list_by_product(
            product_id, MovementKind.COMPENSATION
        )
        assert len(compensations) == 1
        assert compensations[0].quantity == 4
        assert_inventory_invariants(final, product_id)


# --- (5) Compensaciones concurrentes de la misma salida suelta ---


def test_concurrent_compensations_of_the_same_loose_exit(
    database_path: Path,
) -> None:
    """Dos compensaciones de 3 sobre una salida suelta de 3: solo una pasa."""
    product_id, _ = seed_product(database_path, initial_loose_units=5)
    with services_on(database_path) as services:
        record = services.movement.register_loose_exit(
            product_id, quantity=3, reason="Venta"
        )
        exit_id = record.id

    barrier = threading.Barrier(2)

    def compensate_three(services: Services) -> Any:
        return services.movement.register_compensation(
            product_id,
            movement_id=exit_id,
            quantity=3,
            reason="Corrección",
        )

    outcomes = run_workers(
        run_with_services(database_path, barrier, compensate_three),
        run_with_services(database_path, barrier, compensate_three),
    )

    successes = [outcome for outcome in outcomes if outcome.ok]
    failures = [outcome for outcome in outcomes if not outcome.ok]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0].error, ExcessCompensationError)

    with services_on(database_path) as final:
        product = final.products.get(product_id)
        assert product is not None
        assert product.loose_units == 5
        compensations = final.movements.list_by_product(
            product_id, MovementKind.COMPENSATION
        )
        assert len(compensations) == 1
        assert compensations[0].quantity == 3
        assert_inventory_invariants(final, product_id)


# --- (6) Apertura y salida cerrada concurrentes con consumo FIFO ---


def test_concurrent_aperture_and_closed_exit_keep_fifo_consistent(
    database_path: Path,
) -> None:
    """Una apertura y una salida cerrada compiten por el mismo stock."""
    product_id, presentation_ids = seed_product(
        database_path,
        presentations=(closed_presentation(closed_stock=1),),
    )
    box_id = presentation_ids[1]
    with services_on(database_path) as services:
        services.movement.register_closed_entry(
            product_id,
            presentation_id=box_id,
            quantity=1,
            reason="Compra",
        )

    barrier = threading.Barrier(2)

    def open_one(services: Services) -> Any:
        return services.movement.register_aperture(
            product_id,
            presentation_id=box_id,
            quantity=1,
            reason="Apertura",
        )

    def exit_two(services: Services) -> Any:
        return services.movement.register_closed_exit(
            product_id,
            presentation_id=box_id,
            quantity=2,
            reason="Venta",
        )

    outcomes = run_workers(
        run_with_services(database_path, barrier, open_one),
        run_with_services(database_path, barrier, exit_two),
    )

    successes = [outcome for outcome in outcomes if outcome.ok]
    assert len(successes) == 1

    with services_on(database_path) as final:
        entries = final.movements.list_by_presentation(
            box_id, MovementKind.CLOSED_ENTRY
        )
        assert len(entries) == 2
        oldest, newest = entries
        assert oldest.consumed_quantity == 1
        assert oldest.consumed_quantity + oldest.compensated_quantity <= 1
        assert newest.consumed_quantity + newest.compensated_quantity <= 1
        # FIFO: la entrada más antigua se consume antes que la siguiente.
        assert not (
            newest.consumed_quantity > 0
            and oldest.consumed_quantity < oldest.quantity
        )
        box = final.presentations.get(box_id)
        assert box is not None
        assert box.closed_stock == (
            2 - oldest.consumed_quantity - newest.consumed_quantity
        )
        assert box.closed_stock >= 0
        product = final.products.get(product_id)
        assert product is not None
        # Si ganó la apertura, ingresaron 1 × 6 unidades sueltas; si ganó la
        # salida cerrada, las unidades sueltas no cambiaron.
        expected_loose = 6 if newest.consumed_quantity == 0 else 0
        assert product.loose_units == expected_loose
        assert_inventory_invariants(final, product_id)


# --- (7) Presentaciones base concurrentes ---


def test_concurrent_base_creation_keeps_a_single_base(
    database_path: Path,
) -> None:
    """
    Dos conexiones crean una presentación base a la vez: solo una lo consigue.

    Se parte de un producto sin presentación base y se insertan dos bases
    directamente por el repositorio, para ejercitar el índice único parcial del
    esquema (defensa en profundidad) si la validación del servicio no lo
    impidiera.
    """
    with services_on(database_path) as services:
        product = services.products.add(
            sku="SKU-1", name="Producto", base_unit="unidad"
        )
        product_id = product.id
        services.presentations.add(
            product_id=product_id,
            name="Caja x6",
            factor=6,
            sale_price=Decimal("600.00"),
        )

    barrier = threading.Barrier(2)

    def create_base(services: Services, name: str) -> Any:
        with transaction(services.connection):
            return services.presentations.add(
                product_id=product_id,
                name=name,
                factor=1,
                sale_price=Decimal("50.00"),
            )

    outcomes = run_workers(
        run_with_services(
            database_path,
            barrier,
            lambda services: create_base(services, "Unidad A"),
        ),
        run_with_services(
            database_path,
            barrier,
            lambda services: create_base(services, "Unidad B"),
        ),
    )

    successes = [outcome for outcome in outcomes if outcome.ok]
    failures = [outcome for outcome in outcomes if not outcome.ok]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0].error, PresentationError)

    with services_on(database_path) as final:
        factors = [
            presentation.factor
            for presentation in final.presentations.list_by_product(
                product_id
            )
        ]
        assert factors.count(1) == 1


def test_single_base_index_rejects_a_converted_extra_presentation(
    database_path: Path,
) -> None:
    """El índice parcial rechaza convertir otra presentación a factor 1."""
    product_id, _ = seed_product(database_path)
    with services_on(database_path) as services:
        box = services.presentations.add(
            product_id=product_id,
            name="Caja x6",
            factor=6,
            sale_price=Decimal("600.00"),
        )
        box_id = box.id

    with services_on(database_path) as other:
        with pytest.raises(PresentationError):
            with transaction(other.connection):
                other.presentations.set_factor(box_id, 1)

    with services_on(database_path) as final:
        box = final.presentations.get(box_id)
        assert box is not None
        assert box.factor == 6
        factors = [
            presentation.factor
            for presentation in final.presentations.list_by_product(
                product_id
            )
        ]
        assert factors.count(1) == 1


def test_concurrent_factor_changes_cannot_create_two_bases(
    database_path: Path,
) -> None:
    """
    Dos cambios de factor a 1 no dejan el producto con dos bases.

    El producto se prepara sin presentación base y con dos presentaciones de
    factor mayor que uno, y dos conexiones aplican los dos cambios de factor
    por el repositorio dentro de la transacción compartida. La prueba no se
    apoya en que el validador del dominio rechace el cambio sobre el conjunto
    proyectado —cada operación, por separado, dejaría el producto con una sola
    base—, sino en la defensa persistente: el índice único parcial
    `idx_presentations_single_base`, aplicado dentro de la transacción
    serializada (`BEGIN IMMEDIATE`). Si esa defensa desapareciera, los dos
    cambios se confirmarían y el producto quedaría con dos presentaciones base.
    El estado final se comprueba con una lectura nueva.
    """
    with services_on(database_path) as services:
        product = services.products.add(
            sku="SKU-1", name="Producto", base_unit="unidad"
        )
        product_id = product.id
        first = services.presentations.add(
            product_id=product_id,
            name="Caja x6",
            factor=6,
            sale_price=Decimal("600.00"),
        )
        second = services.presentations.add(
            product_id=product_id,
            name="Fardo x12",
            factor=12,
            sale_price=Decimal("1200.00"),
        )
        first_id = first.id
        second_id = second.id

    declared_factors = {first_id: 6, second_id: 12}
    barrier = threading.Barrier(2)

    def change_to_base(services: Services, presentation_id: int) -> Any:
        with transaction(services.connection):
            return services.presentations.set_factor(presentation_id, 1)

    outcomes = run_workers(
        run_with_services(
            database_path,
            barrier,
            lambda services: change_to_base(services, first_id),
        ),
        run_with_services(
            database_path,
            barrier,
            lambda services: change_to_base(services, second_id),
        ),
    )

    successes = [outcome for outcome in outcomes if outcome.ok]
    failures = [outcome for outcome in outcomes if not outcome.ok]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0].error, PresentationError)

    with services_on(database_path) as final:
        factors = {
            presentation.id: presentation.factor
            for presentation in final.presentations.list_by_product(
                product_id
            )
        }
        bases = [
            presentation_id
            for presentation_id, factor in factors.items()
            if factor == 1
        ]
        assert len(bases) == 1
        loser_id = second_id if bases[0] == first_id else first_id
        assert factors[loser_id] == declared_factors[loser_id]
        assert_inventory_invariants(final, product_id)


# --- (8) Actualización concurrente con desactivación del producto ---


def test_concurrent_update_and_deactivation_leave_no_partial_changes(
    database_path: Path,
) -> None:
    """Actualización y desactivación simultáneas sin estado mixto."""
    product_id, _ = seed_product(database_path, minimum_stock=3)
    barrier = threading.Barrier(2)

    def update(services: Services) -> Any:
        return services.product.update(
            product_id, name="Nombre nuevo", minimum_stock=15
        )

    def deactivate(services: Services) -> Any:
        return services.product.deactivate(product_id)

    outcomes = run_workers(
        run_with_services(database_path, barrier, update),
        run_with_services(database_path, barrier, deactivate),
    )

    assert outcomes[1].ok is True

    with services_on(database_path) as final:
        product = final.products.get(product_id)
        assert product is not None
        assert product.active is False
        # La modificación se aplicó completa o no se aplicó: nunca a medias.
        assert (product.name, product.minimum_stock) in {
            ("Producto", 3),
            ("Nombre nuevo", 15),
        }
        assert_inventory_invariants(final, product_id)

    if outcomes[0].ok:
        assert isinstance(outcomes[0].value, ProductRecord)
    else:
        assert isinstance(outcomes[0].error, InactiveProductError)


# --- (9) Registro simultáneo del mismo SKU ---


def test_simultaneous_registration_of_the_same_sku(
    database_path: Path,
) -> None:
    """Dos altas del mismo SKU: solo una se persiste."""
    barrier = threading.Barrier(2)

    def register(services: Services, name: str) -> Any:
        return services.product.register(
            ProductRegistration(
                sku="SKU-1",
                name=name,
                base_unit="unidad",
                base_sale_price=Decimal("100.00"),
            )
        )

    outcomes = run_workers(
        run_with_services(
            database_path,
            barrier,
            lambda services: register(services, "Uno"),
        ),
        run_with_services(
            database_path,
            barrier,
            lambda services: register(services, "Dos"),
        ),
    )

    successes = [outcome for outcome in outcomes if outcome.ok]
    failures = [outcome for outcome in outcomes if not outcome.ok]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0].error, DuplicateSkuError)

    with services_on(database_path) as final:
        products = final.products.list_all()
        assert len(products) == 1
        counts = final.connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM presentations) AS presentations
            """
        ).fetchone()
        assert counts["presentations"] == 1
        assert_inventory_invariants(final, products[0].id)


# --- (10) Fallo y rollback sin cambios parciales ---


def test_failed_operation_rolls_back_without_partial_changes(
    database_path: Path,
) -> None:
    """Un fallo a mitad de una transacción no deja cambios parciales."""
    barrier = threading.Barrier(2)

    def writer() -> None:
        connection = connect(database_path)
        try:
            products = SqliteProductRepository(connection)
            presentations = SqlitePresentationRepository(connection)
            with transaction(connection):
                product = products.add(
                    sku="SKU-1", name="Producto", base_unit="unidad"
                )
                presentations.add(
                    product_id=product.id,
                    name="unidad",
                    factor=1,
                    sale_price=Decimal("100.00"),
                )
                barrier.wait(timeout=BARRIER_TIMEOUT_SECONDS)
                raise RuntimeError("Fallo simulado a mitad de la transacción.")
        finally:
            connection.close()

    def reader() -> tuple[int, int]:
        with services_on(database_path) as services:
            barrier.wait(timeout=BARRIER_TIMEOUT_SECONDS)
            row = services.connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM products) AS products,
                    (SELECT COUNT(*) FROM presentations) AS presentations
                """
            ).fetchone()

            return (int(row["products"]), int(row["presentations"]))

    outcomes = run_workers(writer, reader)

    assert isinstance(outcomes[0].error, RuntimeError)
    assert outcomes[1].ok is True
    # El lector concurrente no observa escrituras sin confirmar.
    assert outcomes[1].value == (0, 0)

    with services_on(database_path) as final:
        assert final.products.list_all() == []
        counts = final.connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM presentations) AS presentations,
                (SELECT COUNT(*) FROM movements) AS movements
            """
        ).fetchone()
        assert counts["presentations"] == 0
        assert counts["movements"] == 0


# --- Timeout y SQLITE_BUSY ---


def test_reduced_timeout_surfaces_sqlite_busy_without_retries(
    database_path: Path,
) -> None:
    """
    Un bloqueo agotado con un timeout reducido falla con `SQLITE_BUSY`.

    Sin reintentos automáticos ni WAL: la conexión bloqueada propaga el error
    de SQLite al agotar su `timeout`, y una vez liberado el bloqueo la misma
    configuración vuelve a operar con normalidad.
    """
    holder = connect(database_path)
    holder.execute("BEGIN IMMEDIATE")
    try:
        blocked = connect(database_path, timeout=REDUCED_TIMEOUT_SECONDS)
        try:
            with pytest.raises(sqlite3.OperationalError) as error:
                with transaction(blocked):
                    pass
            message = str(error.value).lower()
            assert "locked" in message or "busy" in message
        finally:
            blocked.close()
    finally:
        holder.execute("ROLLBACK")
        holder.close()

    released = connect(database_path, timeout=REDUCED_TIMEOUT_SECONDS)
    try:
        with transaction(released):
            pass
    finally:
        released.close()
