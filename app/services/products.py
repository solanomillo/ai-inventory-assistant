"""Servicios de aplicación de productos (T011 y T016).

Implementa los casos de uso de alta, modificación y desactivación de productos,
incluido el registro del stock inicial suelto y cerrado (RF-01, RF-02 y RF-07).

El servicio depende de los contratos de `app/services/ports.py` y reutiliza las
reglas del dominio (`app/domain/products.py`, `app/domain/presentations.py` y
`app/domain/stock.py`): no reimplementa validaciones ni reglas de negocio. El
alta se ejecuta dentro de una única transacción, de modo que el producto, sus
presentaciones y los movimientos de stock inicial se persisten todos o ninguno;
ante un fallo, la transacción se revierte por completo.

La restricción de los productos desactivados se aplica aquí: un producto
desactivado conserva su historial y solo admite consultas, historial y
compensaciones históricas válidas (RF-01). Cada caso de uso de escritura se
ejecuta dentro de la transacción compartida (`BEGIN IMMEDIATE`, T016), de modo
que la lectura, la validación y la escritura no se separan frente a
operaciones simultáneas.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from app.domain.movements import MovementKind
from app.domain.presentations import (
    Presentation,
    validate_presentation_name_unique,
    validate_single_base_presentation,
)
from app.domain.products import (
    Product,
    validate_sku_unique,
    validate_sku_unchanged,
)
from app.domain.quantities import QuantityError, validate_cost
from app.domain.stock import validate_closed_stock, validate_loose_units
from app.services.ports import (
    MovementRepository,
    PresentationRepository,
    ProductRecord,
    ProductRepository,
    RecordNotFoundError,
)


class InactiveProductError(ValueError):
    """Error que indica que el producto está desactivado (RF-01)."""


TransactionScope = Callable[[], AbstractContextManager[None]]
"""Fábrica de transacciones que envuelve la escritura de un caso de uso."""


INITIAL_STOCK_REASON: str = "Stock inicial"
"""Motivo con el que se registran los movimientos de stock inicial (RF-02)."""

INITIAL_LOOSE_GROUP_LABEL: str = "las unidades sueltas iniciales"
"""Nombre del grupo de stock inicial suelto en los mensajes de validación."""

ZERO_QUANTITY_COST_TEMPLATE: str = (
    "No se puede informar un costo para {group}: el grupo no tiene cantidad "
    "inicial. El costo es el importe total del grupo y requiere una cantidad "
    "mayor que cero."
)
"""Motivo del rechazo de un costo informado sin cantidad (T058)."""


@dataclass(frozen=True)
class InitialClosedStock:
    """
    Presentación declarada en el alta de un producto, con su stock inicial.

    `closed_stock` es la cantidad inicial de presentaciones cerradas de esa
    presentación; cuando es cero no se registra ningún movimiento de stock
    inicial (RF-02). `cost` es el costo total opcional de ese grupo inicial; se
    conserva en el movimiento inicial existente y no se distribuye entre
    unidades (T058).
    """

    name: str
    factor: int
    sale_price: Decimal
    closed_stock: int = 0
    cost: Decimal | None = None


@dataclass(frozen=True)
class ProductRegistration:
    """
    Datos del alta de un producto (RF-01, RF-02 y RF-07).

    `base_sale_price` es el precio de venta de la presentación base, que tiene
    factor 1. `base_presentation_name` es opcional: cuando no se indica, la
    presentación base toma el nombre de la unidad base. `presentations` declara
    las presentaciones adicionales del producto, cada una con su stock inicial
    cerrado. `initial_loose_cost` es el costo total opcional de las unidades
    sueltas iniciales, independiente del costo de cada presentación (T058).
    """

    sku: str
    name: str
    base_unit: str
    base_sale_price: Decimal
    minimum_stock: int = 0
    initial_loose_units: int = 0
    initial_loose_cost: Decimal | None = None
    presentations: tuple[InitialClosedStock, ...] = ()
    base_presentation_name: str | None = None


class ProductService:
    """Casos de uso de productos y de su alta con stock inicial (T011)."""

    def __init__(
        self,
        *,
        products: ProductRepository,
        presentations: PresentationRepository,
        movements: MovementRepository,
        transaction: TransactionScope,
    ) -> None:
        """
        Args:
            products: Repositorio de productos.
            presentations: Repositorio de presentaciones.
            movements: Repositorio de movimientos.
            transaction: Fábrica de transacciones que envuelve el alta, para
                que el producto, sus presentaciones y sus movimientos de stock
                inicial se persistan de forma atómica.
        """
        self._products = products
        self._presentations = presentations
        self._movements = movements
        self._transaction = transaction

    def register(self, registration: ProductRegistration) -> ProductRecord:
        """
        Da de alta un producto con su presentación base y su stock inicial
        (RF-01, RF-02).

        El producto se crea activo, con la presentación base de factor 1 y, si
        se declararon, con las presentaciones adicionales. Cada porción
        positiva de stock inicial —las unidades sueltas y el stock cerrado de
        cada presentación declarada— genera un movimiento de entrada marcado
        como stock inicial; una porción igual a cero no genera movimiento. Los
        saldos se escriben una sola vez, junto con sus movimientos, dentro de
        una única transacción.

        El costo de cada grupo inicial es opcional e independiente: las
        unidades sueltas tienen su propio costo total y cada presentación
        cerrada el suyo. Un costo informado para un grupo sin cantidad se
        rechaza antes de cualquier escritura, porque el costo es el importe
        total del grupo; el costo omitido queda ausente y el costo cero es
        válido cuando la cantidad es positiva (T058).

        El prechequeo de unicidad del SKU se ejecuta dentro de la transacción,
        de modo que la comprobación y el alta no se separen; la garantía final
        sigue siendo la restricción `UNIQUE` del esquema, que también cubre dos
        altas simultáneas con el mismo SKU (T016).

        Args:
            registration: Datos del alta del producto.

        Returns:
            El producto persistido.

        Raises:
            QuantityError: Si el stock mínimo, las unidades sueltas, el stock
                cerrado inicial, un factor, un precio o un costo son inválidos,
                o si se informó un costo para un grupo sin cantidad.
            ProductError: Si el SKU, el nombre o la unidad base son inválidos.
            DuplicateSkuError: Si el SKU ya está registrado, sin distinguir
                mayúsculas de minúsculas.
            PresentationError: Si el nombre de una presentación se repite
                dentro del producto o si no queda exactamente una presentación
                base.
            sqlite3.IntegrityError: Si una escritura incumple el esquema; la
                transacción se revierte por completo.
        """
        product = Product(
            sku=registration.sku,
            name=registration.name,
            base_unit=registration.base_unit,
            minimum_stock=registration.minimum_stock,
        )
        base, extra_presentations = self._prepare_presentations(
            registration, product.base_unit
        )
        loose_units = validate_loose_units(registration.initial_loose_units)
        loose_cost = _validated_initial_cost(
            registration.initial_loose_cost,
            quantity=loose_units,
            group=INITIAL_LOOSE_GROUP_LABEL,
        )
        occurred_at = _utc_now()

        with self._transaction():
            sku = validate_sku_unique(
                product.sku,
                [record.sku for record in self._products.list_all()],
            )
            stored = self._products.add(
                sku=sku,
                name=product.name,
                base_unit=product.base_unit,
                minimum_stock=product.minimum_stock,
                loose_units=loose_units,
            )
            base_record = self._presentations.add(
                product_id=stored.id,
                name=base.name,
                factor=base.factor,
                sale_price=base.sale_price,
            )
            self._record_initial_stock(
                product_id=stored.id,
                presentation_id=base_record.id,
                kind=MovementKind.LOOSE_ENTRY,
                quantity=loose_units,
                cost=loose_cost,
                occurred_at=occurred_at,
            )
            for presentation, closed_stock, cost in extra_presentations:
                record = self._presentations.add(
                    product_id=stored.id,
                    name=presentation.name,
                    factor=presentation.factor,
                    sale_price=presentation.sale_price,
                    closed_stock=closed_stock,
                )
                self._record_initial_stock(
                    product_id=stored.id,
                    presentation_id=record.id,
                    kind=MovementKind.CLOSED_ENTRY,
                    quantity=closed_stock,
                    cost=cost,
                    occurred_at=occurred_at,
                )

        return stored

    def update(
        self,
        product_id: int,
        *,
        sku: str | None = None,
        name: str | None = None,
        minimum_stock: int | None = None,
    ) -> ProductRecord:
        """
        Modifica el nombre o el stock mínimo de un producto activo (RF-01).

        El SKU es inmutable: si se informa uno distinto del vigente, la
        operación se rechaza. Un producto desactivado no admite modificaciones.
        La lectura del producto, la validación del SKU, la del estado activo,
        la de los cambios y las escrituras del nombre y del stock mínimo se
        ejecutan dentro de una única transacción: si una falla, no queda
        ninguna escritura aplicada (T016).

        Args:
            product_id: Producto que se modifica.
            sku: SKU solicitado, o `None` para no verificarlo. Solo se admite
                el SKU vigente.
            name: Nombre nuevo, o `None` para no modificarlo.
            minimum_stock: Stock mínimo nuevo, o `None` para no modificarlo.

        Returns:
            El producto persistido con los cambios aplicados.

        Raises:
            RecordNotFoundError: Si el producto no existe.
            ImmutableSkuError: Si el SKU solicitado difiere del vigente.
            InactiveProductError: Si el producto está desactivado.
            ProductError: Si el nombre es inválido.
            QuantityError: Si el stock mínimo es inválido.
        """
        with self._transaction():
            record = self._required_product(product_id)

            if sku is not None:
                validate_sku_unchanged(record.sku, sku)

            self._require_active(record)

            current = Product(
                sku=record.sku,
                name=record.name,
                base_unit=record.base_unit,
                minimum_stock=record.minimum_stock,
            )
            updated = current
            if name is not None:
                updated = updated.with_name(name)
            if minimum_stock is not None:
                updated = updated.with_minimum_stock(minimum_stock)

            if name is None and minimum_stock is None:
                return record

            if name is not None:
                self._products.set_name(product_id, updated.name)
            if minimum_stock is not None:
                self._products.set_minimum_stock(
                    product_id, updated.minimum_stock
                )

            return self._required_product(product_id)

    def deactivate(self, product_id: int) -> ProductRecord:
        """
        Desactiva un producto conservando sus datos y su historial (RF-01).

        Args:
            product_id: Producto que se desactiva.

        Returns:
            El producto persistido, desactivado.

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        self._required_product(product_id)

        return self._products.set_active(product_id, False)

    def _prepare_presentations(
        self,
        registration: ProductRegistration,
        base_unit: str,
    ) -> tuple[
        Presentation,
        tuple[tuple[Presentation, int, Decimal | None], ...],
    ]:
        """
        Valida la presentación base y las presentaciones declaradas (RF-07).

        Args:
            registration: Datos del alta del producto.
            base_unit: Unidad base ya recortada y validada del producto.

        Returns:
            La presentación base y las presentaciones adicionales junto con su
            stock cerrado inicial y su costo total opcional.

        Raises:
            ProductError: Si el nombre de la presentación base es inválido.
            PresentationError: Si un nombre de presentación se repite dentro
                del producto o si no queda exactamente una presentación base.
            QuantityError: Si un factor, un precio, un stock cerrado inicial o
                un costo son inválidos, o si se informó un costo para una
                presentación sin stock inicial.
        """
        base_name = (
            base_unit
            if registration.base_presentation_name is None
            else registration.base_presentation_name
        )
        base = Presentation.create_base(
            name=base_name,
            sale_price=registration.base_sale_price,
        )

        declared_names = [base.name]
        planned: list[tuple[Presentation, int, Decimal | None]] = []
        for declared in registration.presentations:
            name = validate_presentation_name_unique(
                declared.name, declared_names
            )
            declared_names.append(name)
            closed_stock = validate_closed_stock(declared.closed_stock)
            cost = _validated_initial_cost(
                declared.cost,
                quantity=closed_stock,
                group=f"la presentación adicional '{name}'",
            )
            planned.append(
                (
                    Presentation(
                        name=name,
                        factor=declared.factor,
                        sale_price=declared.sale_price,
                        has_history=False,
                    ),
                    closed_stock,
                    cost,
                )
            )

        validate_single_base_presentation(
            [base, *(presentation for presentation, _, _ in planned)]
        )

        return base, tuple(planned)

    def _record_initial_stock(
        self,
        *,
        product_id: int,
        presentation_id: int,
        kind: MovementKind,
        quantity: int,
        occurred_at: datetime,
        cost: Decimal | None = None,
    ) -> None:
        """
        Registra el movimiento de una porción positiva de stock inicial
        (RF-02).

        Una porción igual a cero no genera ningún movimiento. El costo es el
        importe total opcional del grupo: se conserva en el mismo movimiento
        inicial, sin crear movimientos adicionales ni distribuirlo entre las
        unidades (T058).

        Raises:
            QuantityError: Si se informó un costo para un grupo sin cantidad o
                si el costo no es un `Decimal` finito y no negativo.
        """
        if quantity == 0:
            if cost is not None:
                raise QuantityError(
                    "No se puede informar un costo para un grupo de stock "
                    "inicial sin cantidad."
                )
            return

        if cost is not None:
            validate_cost(cost)

        self._movements.add(
            product_id=product_id,
            presentation_id=presentation_id,
            kind=kind,
            quantity=quantity,
            reason=INITIAL_STOCK_REASON,
            occurred_at=occurred_at,
            cost=cost,
            is_initial_stock=True,
        )

    def _required_product(self, product_id: int) -> ProductRecord:
        """
        Devuelve el producto indicado y exige que exista.

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        product = self._products.get(product_id)
        if product is None:
            raise RecordNotFoundError(
                f"No existe el producto con id {product_id}."
            )

        return product

    @staticmethod
    def _require_active(product: ProductRecord) -> None:
        """
        Exige que el producto admita operaciones ordinarias nuevas (RF-01).

        Raises:
            InactiveProductError: Si el producto está desactivado.
        """
        if not product.active:
            raise InactiveProductError(
                f"El producto '{product.sku}' está desactivado y no admite "
                "modificaciones."
            )


def _validated_initial_cost(
    cost: Decimal | None,
    *,
    quantity: int,
    group: str,
) -> Decimal | None:
    """
    Valida el costo total opcional de un grupo de stock inicial (T058).

    Un costo omitido queda ausente. Un costo informado exige una cantidad
    inicial positiva, porque es el importe total del grupo y no un costo
    unitario; además debe ser un `Decimal` finito y no negativo.

    Args:
        cost: Costo total informado, o `None` si se omitió.
        quantity: Cantidad inicial del grupo.
        group: Nombre del grupo para el mensaje de rechazo.

    Returns:
        El costo validado, o `None` si se omitió.

    Raises:
        QuantityError: Si el grupo no tiene cantidad y se informó un costo, o
            si el costo no es un `Decimal` finito y no negativo.
    """
    if cost is None:
        return None

    if quantity == 0:
        raise QuantityError(ZERO_QUANTITY_COST_TEMPLATE.format(group=group))

    return validate_cost(cost)


def _utc_now() -> datetime:
    """Devuelve el instante actual en UTC, con zona horaria."""
    return datetime.now(timezone.utc)
