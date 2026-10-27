"""Pruebas de integración del servicio de productos (T011).

Cubren los criterios de aceptación de T011 sobre RF-01 y RF-02: el alta válida
y el rechazo de SKU duplicado, la inmutabilidad del SKU, el stock inicial
suelto y cerrado, el movimiento por cada porción inicial positiva marcado como
stock inicial, la ausencia de movimientos cuando el stock inicial es cero, la
atomicidad del alta y la ausencia de saldos duplicados, la desactivación con
historial conservado y el rechazo de modificaciones de un producto desactivado.

Las pruebas usan la base SQLite en memoria con el esquema real y los
repositorios de T009, de modo que verifican el comportamiento de extremo a
extremo del servicio.
"""

import sqlite3
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from datetime import timedelta, timezone
from decimal import Decimal
from functools import partial
from typing import Any

import pytest

from app.domain.movements import MovementKind
from app.domain.presentations import (
    DuplicatePresentationNameError,
    PresentationError,
)
from app.domain.products import (
    DuplicateSkuError,
    ImmutableSkuError,
)
from app.domain.quantities import QuantityError
from app.domain.stock import ClosedStock, total_equivalent
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
from app.services.ports import (
    MovementRecord,
    ProductRecord,
    RecordNotFoundError,
)
from app.services.products import (
    INITIAL_STOCK_REASON,
    InactiveProductError,
    InitialClosedStock,
    ProductRegistration,
    ProductService,
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
    cost: Decimal | None = None,
) -> InitialClosedStock:
    """Declara una presentación adicional con su stock inicial cerrado."""
    return InitialClosedStock(
        name=name,
        factor=factor,
        sale_price=Decimal(sale_price),
        closed_stock=closed_stock,
        cost=cost,
    )


class _FailingMovementRepository:
    """
    Repositorio de movimientos que delega y falla al registrar.

    Permite provocar un error a mitad del alta, después de que el producto y
    sus presentaciones ya se escribieron, para verificar la reversión completa.
    """

    def __init__(self, delegate: SqliteMovementRepository) -> None:
        self._delegate = delegate

    def add(self, **values: Any) -> MovementRecord:
        """Registra el movimiento y luego falla, dentro de la transacción."""
        self._delegate.add(**values)
        raise RuntimeError("Fallo simulado al registrar el movimiento.")

    def get(self, movement_id: int) -> MovementRecord | None:
        """Reenvía la consulta al repositorio real."""
        return self._delegate.get(movement_id)

    def list_by_product(
        self,
        product_id: int,
        kind: MovementKind | None = None,
        **period: Any,
    ) -> list[MovementRecord]:
        """Reenvía la consulta al repositorio real."""
        return self._delegate.list_by_product(product_id, kind, **period)

    def list_by_presentation(
        self,
        presentation_id: int,
        kind: MovementKind | None = None,
        **period: Any,
    ) -> list[MovementRecord]:
        """Reenvía la consulta al repositorio real."""
        return self._delegate.list_by_presentation(
            presentation_id, kind, **period
        )


class _FailingProductRepository:
    """
    Repositorio de productos que delega y falla al ejecutar una escritura.

    Permite provocar un error después de la primera escritura de una
    modificación, de modo que el error ocurra una vez aplicada la escritura
    indicada y se verifique la reversión de todo el cambio.
    """

    def __init__(
        self, delegate: SqliteProductRepository, failing_method: str
    ) -> None:
        self._delegate = delegate
        self._failing_method = failing_method

    def __getattr__(self, name: str) -> Any:
        """Delega el atributo y hace fallar la operación indicada."""
        attribute = getattr(self._delegate, name)
        if name != self._failing_method:
            return attribute

        def failing_operation(*args: Any, **kwargs: Any) -> Any:
            attribute(*args, **kwargs)
            raise RuntimeError(f"Fallo simulado en '{name}'.")

        return failing_operation


# --- Alta válida y SKU duplicado (criterio 1) ---


def test_register_creates_an_active_product_with_its_base_presentation(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica el alta válida y la presentación base con factor 1."""
    stored = product_service.register(
        make_registration(
            sku="  SKU-1  ",
            name="  Producto  ",
            base_unit=" botella ",
        )
    )

    assert stored.sku == "SKU-1"
    assert stored.name == "Producto"
    assert stored.base_unit == "botella"
    assert stored.minimum_stock == 0
    assert stored.loose_units == 0
    assert stored.active is True

    registered = presentations.list_by_product(stored.id)
    assert len(registered) == 1
    assert registered[0].name == "botella"
    assert registered[0].factor == 1
    assert registered[0].sale_price == Decimal("100.00")
    assert registered[0].closed_stock == 0


def test_register_uses_the_base_presentation_name_when_provided(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
) -> None:
    """Verifica que el nombre de la presentación base puede indicarse."""
    stored = product_service.register(
        make_registration(base_unit="botella", base_presentation_name="Unidad")
    )

    assert [
        presentation.name
        for presentation in presentations.list_by_product(stored.id)
    ] == ["Unidad"]


@pytest.mark.parametrize("duplicated_sku", ["sku-1", "SKU-1", "Sku-1"])
def test_register_rejects_a_duplicate_sku(
    product_service: ProductService,
    products: SqliteProductRepository,
    duplicated_sku: str,
) -> None:
    """Verifica el rechazo del SKU duplicado sin distinguir mayúsculas."""
    product_service.register(make_registration(sku="sKu-1"))

    with pytest.raises(DuplicateSkuError):
        product_service.register(
            make_registration(sku=duplicated_sku, name="Otro producto")
        )

    assert len(products.list_all()) == 1


def test_rejected_duplicate_sku_is_not_persisted(
    product_service: ProductService,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    connection: sqlite3.Connection,
) -> None:
    """Verifica que el alta rechazada no deja producto ni presentación."""
    product_service.register(make_registration(sku="SKU-1"))

    with pytest.raises(DuplicateSkuError):
        product_service.register(make_registration(sku="SKU-1"))

    assert len(products.list_all()) == 1
    total = connection.execute(
        "SELECT COUNT(*) AS total FROM presentations"
    ).fetchone()["total"]
    assert total == 1


# --- SKU inmutable (criterio 2) ---


def test_update_rejects_a_sku_change(
    product_service: ProductService,
    products: SqliteProductRepository,
) -> None:
    """Verifica que el SKU no puede modificarse una vez creado."""
    stored = product_service.register(make_registration(sku="SKU-1"))

    with pytest.raises(ImmutableSkuError):
        product_service.update(stored.id, sku="SKU-2")

    reloaded = products.get(stored.id)
    assert reloaded is not None
    assert reloaded.sku == "SKU-1"


def test_update_accepts_the_current_sku(
    product_service: ProductService,
) -> None:
    """Verifica que informar el SKU vigente no se considera un cambio."""
    stored = product_service.register(make_registration(sku="SKU-1"))

    updated = product_service.update(
        stored.id, sku="SKU-1", name="Nombre nuevo"
    )

    assert updated.sku == "SKU-1"
    assert updated.name == "Nombre nuevo"


def test_update_modifies_the_name_and_the_minimum_stock(
    product_service: ProductService,
    products: SqliteProductRepository,
) -> None:
    """Verifica la modificación del nombre y del stock mínimo."""
    stored = product_service.register(make_registration(minimum_stock=2))

    updated = product_service.update(
        stored.id, name="Nombre nuevo", minimum_stock=15
    )

    assert updated.name == "Nombre nuevo"
    assert updated.minimum_stock == 15
    assert products.get(stored.id) == updated


# --- Atomicidad de la modificación conjunta (T015) ---


def test_update_rolls_back_both_writes_when_the_second_one_fails(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que un fallo tras la primera escritura no deja cambios."""
    product_service = ProductService(
        products=products,
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )
    stored = product_service.register(
        make_registration(name="Producto", minimum_stock=2)
    )
    failing_service = ProductService(
        products=_FailingProductRepository(products, "set_minimum_stock"),
        presentations=presentations,
        movements=movements,
        transaction=partial(transaction, connection),
    )

    with pytest.raises(RuntimeError):
        failing_service.update(
            stored.id, name="Nombre nuevo", minimum_stock=15
        )

    reloaded = products.get(stored.id)
    assert reloaded is not None
    assert reloaded.name == "Producto"
    assert reloaded.minimum_stock == 2


# --- Stock inicial suelto y/o cerrado (criterios 3, 4, 5 y 6) ---


def test_register_records_the_loose_initial_stock_as_an_initial_entry(
    product_service: ProductService,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica el stock inicial suelto y su movimiento de entrada."""
    stored = product_service.register(
        make_registration(initial_loose_units=5)
    )

    reloaded = products.get(stored.id)
    assert reloaded is not None
    assert reloaded.loose_units == 5

    base = presentations.list_by_product(stored.id)[0]
    history = movements.list_by_product(stored.id)
    assert len(history) == 1
    assert history[0].kind is MovementKind.LOOSE_ENTRY
    assert history[0].presentation_id == base.id
    assert history[0].quantity == 5
    assert history[0].reason == INITIAL_STOCK_REASON
    assert history[0].is_initial_stock is True


def test_register_records_the_closed_initial_stock_per_presentation(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica el stock inicial cerrado y su movimiento de entrada."""
    stored = product_service.register(
        make_registration(
            presentations=(make_closed_presentation(closed_stock=2),)
        )
    )

    registered = presentations.list_by_product(stored.id)
    assert [presentation.name for presentation in registered] == [
        "unidad",
        "Caja x6",
    ]
    box = registered[1]
    assert box.factor == 6
    assert box.sale_price == Decimal("600.00")
    assert box.closed_stock == 2

    history = movements.list_by_product(stored.id)
    assert len(history) == 1
    assert history[0].kind is MovementKind.CLOSED_ENTRY
    assert history[0].presentation_id == box.id
    assert history[0].quantity == 2
    assert history[0].is_initial_stock is True


def test_register_records_the_mixed_initial_stock(
    product_service: ProductService,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica el stock inicial suelto y cerrado en un mismo alta."""
    stored = product_service.register(
        make_registration(
            initial_loose_units=3,
            presentations=(make_closed_presentation(closed_stock=2),),
        )
    )

    history = movements.list_by_product(stored.id)

    assert [movement.kind for movement in history] == [
        MovementKind.LOOSE_ENTRY,
        MovementKind.CLOSED_ENTRY,
    ]
    assert [movement.quantity for movement in history] == [3, 2]
    assert all(movement.is_initial_stock for movement in history)


def test_register_does_not_duplicate_the_initial_balances(
    product_service: ProductService,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que los saldos iniciales se registran una sola vez."""
    stored = product_service.register(
        make_registration(
            initial_loose_units=3,
            presentations=(make_closed_presentation(closed_stock=2),),
        )
    )

    reloaded = products.get(stored.id)
    assert reloaded is not None
    assert reloaded.loose_units == 3

    registered = presentations.list_by_product(stored.id)
    closed_stocks = [
        ClosedStock(
            factor=presentation.factor,
            quantity=presentation.closed_stock,
        )
        for presentation in registered
    ]
    assert total_equivalent(reloaded.loose_units, closed_stocks) == 3 + 2 * 6
    assert len(movements.list_by_product(stored.id)) == 2


def test_register_without_initial_stock_creates_no_movement(
    product_service: ProductService,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que un stock inicial total de cero no crea movimientos."""
    stored = product_service.register(make_registration())

    assert movements.list_by_product(stored.id) == []


def test_register_with_zero_closed_stock_creates_no_movement(
    product_service: ProductService,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que una presentación sin stock inicial no crea movimiento."""
    stored = product_service.register(
        make_registration(presentations=(make_closed_presentation(),))
    )

    registered = presentations.list_by_product(stored.id)
    assert [presentation.closed_stock for presentation in registered] == [0, 0]
    assert movements.list_by_product(stored.id) == []


# --- Costos iniciales opcionales por grupo (T058) ---


def test_register_records_independent_costs_for_the_initial_groups(
    product_service: ProductService,
    movements: SqliteMovementRepository,
) -> None:
    """
    Verifica los costos independientes de las unidades y de la caja (T058).

    El costo suelto se conserva en el movimiento de entrada suelta y el de la
    presentación en el movimiento de entrada cerrada; no se derivan del
    precio de venta ni se crea un movimiento adicional por grupo.
    """
    stored = product_service.register(
        make_registration(
            initial_loose_units=3,
            initial_loose_cost=Decimal("1000"),
            presentations=(
                make_closed_presentation(
                    closed_stock=1,
                    cost=Decimal("7500"),
                ),
            ),
        )
    )

    history = movements.list_by_product(stored.id)

    assert len(history) == 2
    assert [movement.kind for movement in history] == [
        MovementKind.LOOSE_ENTRY,
        MovementKind.CLOSED_ENTRY,
    ]
    assert [movement.quantity for movement in history] == [3, 1]
    assert [movement.cost for movement in history] == [
        Decimal("1000"),
        Decimal("7500"),
    ]
    assert all(movement.is_initial_stock for movement in history)


def test_register_leaves_the_initial_costs_absent_when_they_are_omitted(
    product_service: ProductService,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que un costo omitido se persiste como ausente (T058)."""
    stored = product_service.register(
        make_registration(
            initial_loose_units=2,
            presentations=(make_closed_presentation(closed_stock=1),),
        )
    )

    history = movements.list_by_product(stored.id)

    assert [movement.cost for movement in history] == [None, None]


def test_register_accepts_a_zero_cost_with_a_positive_quantity(
    product_service: ProductService,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el costo cero es válido con cantidad positiva (T058)."""
    stored = product_service.register(
        make_registration(
            initial_loose_units=2,
            initial_loose_cost=Decimal("0"),
            presentations=(
                make_closed_presentation(
                    closed_stock=1,
                    cost=Decimal("0"),
                ),
            ),
        )
    )

    history = movements.list_by_product(stored.id)

    assert [movement.cost for movement in history] == [
        Decimal("0"),
        Decimal("0"),
    ]


@pytest.mark.parametrize(
    ("registration", "group"),
    [
        (
            make_registration(initial_loose_cost=Decimal("10")),
            "unidades sueltas iniciales",
        ),
        (
            make_registration(
                presentations=(
                    make_closed_presentation(
                        name="Caja x6",
                        cost=Decimal("10"),
                    ),
                )
            ),
            "Caja x6",
        ),
    ],
)
def test_register_rejects_a_cost_for_a_group_without_quantity(
    product_service: ProductService,
    connection: sqlite3.Connection,
    registration: ProductRegistration,
    group: str,
) -> None:
    """
    Verifica el rechazo de un costo para un grupo sin cantidad (T058, D1).

    El mensaje identifica el grupo y no queda ningún producto, presentación ni
    movimiento escrito: la validación ocurre antes de escribir.
    """
    with pytest.raises(QuantityError, match=group):
        product_service.register(registration)

    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM products) AS products,
            (SELECT COUNT(*) FROM presentations) AS presentations,
            (SELECT COUNT(*) FROM movements) AS movements
        """
    ).fetchone()

    assert counts["products"] == 0
    assert counts["presentations"] == 0
    assert counts["movements"] == 0


@pytest.mark.parametrize(
    "registration",
    [
        make_registration(
            initial_loose_units=1,
            initial_loose_cost=Decimal("-1"),
        ),
        make_registration(
            initial_loose_units=1,
            initial_loose_cost=Decimal("NaN"),
        ),
        make_registration(
            initial_loose_units=1,
            initial_loose_cost=10.5,  # type: ignore[arg-type]
        ),
        make_registration(
            presentations=(
                make_closed_presentation(
                    closed_stock=1,
                    cost=Decimal("-0.01"),
                ),
            )
        ),
    ],
)
def test_register_rejects_an_invalid_initial_cost_without_writing(
    product_service: ProductService,
    connection: sqlite3.Connection,
    registration: ProductRegistration,
) -> None:
    """
    Verifica el rechazo de costos negativos, no finitos o no exactos (T058).

    El costo debe ser un `Decimal` finito y no negativo; el rechazo no deja
    escrituras.
    """
    with pytest.raises(QuantityError):
        product_service.register(registration)

    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM products) AS products,
            (SELECT COUNT(*) FROM movements) AS movements
        """
    ).fetchone()

    assert counts["products"] == 0
    assert counts["movements"] == 0


# --- Atomicidad y validaciones previas (criterio 6) ---


def test_register_rolls_back_every_write_when_a_movement_fails(
    connection: sqlite3.Connection,
    products: SqliteProductRepository,
    presentations: SqlitePresentationRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que un fallo a mitad del alta no deja escrituras parciales."""
    failing_service = ProductService(
        products=products,
        presentations=presentations,
        movements=_FailingMovementRepository(movements),
        transaction=partial(transaction, connection),
    )

    with pytest.raises(RuntimeError):
        failing_service.register(
            make_registration(
                initial_loose_units=5,
                presentations=(make_closed_presentation(closed_stock=2),),
            )
        )

    assert products.list_all() == []
    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM presentations) AS presentations,
            (SELECT COUNT(*) FROM movements) AS movements
        """
    ).fetchone()
    assert counts["presentations"] == 0
    assert counts["movements"] == 0


@pytest.mark.parametrize(
    "declared_presentations",
    [
        (make_closed_presentation(name="UNIDAD"),),
        (
            make_closed_presentation(name="Caja"),
            make_closed_presentation(name="caja"),
        ),
    ],
)
def test_register_rejects_a_repeated_presentation_name_without_writing(
    product_service: ProductService,
    connection: sqlite3.Connection,
    declared_presentations: tuple[InitialClosedStock, ...],
) -> None:
    """Verifica que un nombre de presentación repetido no deja escrituras."""
    with pytest.raises(DuplicatePresentationNameError):
        product_service.register(
            make_registration(presentations=declared_presentations)
        )

    total = connection.execute(
        "SELECT COUNT(*) AS total FROM products"
    ).fetchone()["total"]
    assert total == 0


def test_register_rejects_an_extra_base_presentation_without_writing(
    product_service: ProductService,
    connection: sqlite3.Connection,
) -> None:
    """Verifica que solo puede existir una presentación base (factor 1)."""
    with pytest.raises(PresentationError):
        product_service.register(
            make_registration(
                presentations=(make_closed_presentation(factor=1),)
            )
        )

    total = connection.execute(
        "SELECT COUNT(*) AS total FROM products"
    ).fetchone()["total"]
    assert total == 0


# --- Desactivación con historial conservado (criterio 7) ---


def test_deactivate_keeps_the_product_and_its_history(
    product_service: ProductService,
    products: SqliteProductRepository,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que la desactivación conserva los datos y el historial."""
    stored = product_service.register(
        make_registration(initial_loose_units=4)
    )
    history_before = movements.list_by_product(stored.id)
    assert history_before != []

    deactivated = product_service.deactivate(stored.id)

    assert deactivated.active is False
    assert deactivated.sku == stored.sku
    assert products.get(stored.id) == deactivated
    assert movements.list_by_product(stored.id) == history_before


# --- Rechazo de modificaciones de un producto desactivado (criterio 8) ---


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", "Otro nombre"), ("minimum_stock", 10)],
)
def test_update_rejects_a_deactivated_product(
    product_service: ProductService,
    products: SqliteProductRepository,
    field: str,
    value: Any,
) -> None:
    """Verifica que un producto desactivado no admite modificaciones."""
    stored = product_service.register(
        make_registration(name="Producto", minimum_stock=3)
    )
    product_service.deactivate(stored.id)

    with pytest.raises(InactiveProductError):
        product_service.update(stored.id, **{field: value})

    reloaded = products.get(stored.id)
    assert reloaded is not None
    assert reloaded.name == "Producto"
    assert reloaded.minimum_stock == 3


def test_update_requires_an_existing_product(
    product_service: ProductService,
) -> None:
    """Verifica que modificar un producto inexistente se rechaza."""
    with pytest.raises(RecordNotFoundError):
        product_service.update(999, name="Nombre nuevo")


# --- Regresión de los contratos existentes ---


def test_register_serializes_the_price_and_the_instant_canonically(
    product_service: ProductService,
    connection: sqlite3.Connection,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica la serialización canónica del precio y del instante."""
    stored = product_service.register(
        make_registration(
            base_sale_price=Decimal("1200.00"),
            initial_loose_units=1,
        )
    )

    price_text = connection.execute(
        "SELECT sale_price FROM presentations WHERE product_id = ?",
        (stored.id,),
    ).fetchone()["sale_price"]
    assert price_text == "1200"

    movement = movements.list_by_product(stored.id)[0]
    assert movement.occurred_at.utcoffset() == timedelta(0)
    assert movement.occurred_at.tzinfo is not None

    instant_text = connection.execute(
        "SELECT occurred_at FROM movements WHERE product_id = ?",
        (stored.id,),
    ).fetchone()["occurred_at"]
    assert instant_text.endswith("+00:00")


def test_initial_movements_keep_the_immutable_record_contract(
    product_service: ProductService,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que los movimientos iniciales siguen siendo inmutables."""
    stored = product_service.register(
        make_registration(initial_loose_units=2)
    )

    movement = movements.list_by_product(stored.id)[0]

    assert movement == movements.get(movement.id)
    with pytest.raises(FrozenInstanceError):
        movement.quantity = 99  # type: ignore[misc]


def test_register_returns_a_frozen_product_record(
    product_service: ProductService,
) -> None:
    """Verifica que el producto devuelto conserva el contrato inmutable."""
    stored: ProductRecord = product_service.register(make_registration())

    with pytest.raises(FrozenInstanceError):
        stored.name = "Otro"  # type: ignore[misc]


def test_register_rejects_an_invalid_minimum_stock(
    product_service: ProductService,
    connection: sqlite3.Connection,
) -> None:
    """Verifica que un stock mínimo inválido no deja escrituras."""
    with pytest.raises(QuantityError):
        product_service.register(make_registration(minimum_stock=-1))

    total = connection.execute(
        "SELECT COUNT(*) AS total FROM products"
    ).fetchone()["total"]
    assert total == 0


def test_register_initial_movements_share_the_registration_instant(
    product_service: ProductService,
    movements: SqliteMovementRepository,
) -> None:
    """Verifica que el alta usa un único instante para sus movimientos."""
    stored = product_service.register(
        make_registration(
            initial_loose_units=2,
            presentations=(make_closed_presentation(closed_stock=1),),
        )
    )

    history = movements.list_by_product(stored.id)

    assert len(history) == 2
    assert history[0].occurred_at == history[1].occurred_at
    assert history[0].occurred_at.tzinfo == timezone.utc
