"""Servicios de aplicación de presentaciones (T011 y T016).

Implementa el alta y la modificación de presentaciones conforme a RF-07,
reutilizando las reglas del dominio de `app/domain/presentations.py` y los
validadores numéricos de `app/domain/quantities.py`.

Las operaciones se rechazan cuando el producto está desactivado: un producto
desactivado no admite crear ni modificar presentaciones (RF-01, RF-07). Una
presentación nueva nace con stock cerrado cero y no altera los saldos ni las
equivalencias existentes, y el factor solo puede modificarse cuando la
presentación no tiene stock cerrado ni movimientos históricos.

El servicio conserva el invariante de RF-07 —exactamente una presentación base
con factor 1 por producto— reutilizando `validate_single_base_presentation` del
dominio sobre el conjunto resultante de cada operación, sin reimplementar la
regla. El alta y el cambio de factor se ejecutan dentro de la transacción
compartida (`BEGIN IMMEDIATE`, T016): la lectura, la validación y la escritura
quedan en el mismo bloque, de modo que dos operaciones simultáneas no pueden
crear dos presentaciones base. La base refuerza además la invariante con el
índice único parcial `idx_presentations_single_base` (defensa en profundidad).
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.presentations import (
    Presentation,
    validate_presentation_name_unique,
    validate_single_base_presentation,
)
from app.domain.quantities import validate_price
from app.services.ports import (
    MovementRepository,
    PresentationRecord,
    PresentationRepository,
    ProductRecord,
    ProductRepository,
    RecordNotFoundError,
)
from app.services.products import InactiveProductError, TransactionScope


class PresentationService:
    """Casos de uso de las presentaciones de un producto (RF-07)."""

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
            products: Repositorio de productos, para verificar que el producto
                exista y esté activo.
            presentations: Repositorio de presentaciones.
            movements: Repositorio de movimientos, para determinar si una
                presentación tiene historial y si su factor es inmutable.
            transaction: Fábrica de transacciones que envuelve el alta y el
                cambio de factor, para que la lectura, la validación y la
                escritura se apliquen dentro de la misma transacción.
        """
        self._products = products
        self._presentations = presentations
        self._movements = movements
        self._transaction = transaction

    def add(
        self,
        product_id: int,
        *,
        name: str,
        factor: int,
        sale_price: Decimal,
    ) -> PresentationRecord:
        """
        Agrega una presentación a un producto activo (RF-07).

        La presentación nueva nace con stock cerrado cero y no altera los
        saldos ni las equivalencias existentes. El nombre es único dentro del
        producto, sin distinguir mayúsculas de minúsculas. La operación solo se
        admite si el producto conserva exactamente una presentación base: con
        factor 1 se rechaza cuando el producto ya tiene una. La lectura, la
        validación y el alta se ejecutan dentro de la misma transacción, y el
        índice único parcial de la base también lo impide (T016).

        Args:
            product_id: Producto al que se agrega la presentación.
            name: Nombre de la presentación.
            factor: Equivalencia en unidades base, entero mayor o igual que 1.
            sale_price: Precio de venta, mayor que cero.

        Returns:
            La presentación persistida.

        Raises:
            RecordNotFoundError: Si el producto no existe.
            InactiveProductError: Si el producto está desactivado.
            PresentationError: Si el nombre es inválido, se repite dentro del
                producto o si el producto no quedaría con exactamente una
                presentación base.
            QuantityError: Si el factor o el precio son inválidos.
        """
        with self._transaction():
            self._require_active_product(product_id)

            existing = self._presentations.list_by_product(product_id)
            validated_name = validate_presentation_name_unique(
                name, [presentation.name for presentation in existing]
            )
            presentation = Presentation(
                name=validated_name,
                factor=factor,
                sale_price=sale_price,
                has_history=False,
            )
            projected = [
                self._domain_presentation(record) for record in existing
            ]
            projected.append(presentation)
            validate_single_base_presentation(projected)

            return self._presentations.add(
                product_id=product_id,
                name=presentation.name,
                factor=presentation.factor,
                sale_price=presentation.sale_price,
                closed_stock=0,
            )

    def change_sale_price(
        self, presentation_id: int, sale_price: Decimal
    ) -> PresentationRecord:
        """
        Modifica el precio de venta de una presentación (RF-07).

        Las salidas ya confirmadas conservan su precio aplicado; este cambio
        solo afecta a las operaciones futuras.

        Args:
            presentation_id: Presentación que se modifica.
            sale_price: Precio de venta nuevo, mayor que cero.

        Returns:
            La presentación persistida con el precio actualizado.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
            InactiveProductError: Si el producto está desactivado.
            QuantityError: Si el precio no es un Decimal finito mayor que cero.
        """
        record = self._required_presentation(presentation_id)
        self._require_active_product(record.product_id)

        return self._presentations.set_sale_price(
            presentation_id, validate_price(sale_price)
        )

    def change_factor(
        self, presentation_id: int, factor: int
    ) -> PresentationRecord:
        """
        Modifica el factor de una presentación (RF-07).

        El factor no puede modificarse cuando la presentación tiene stock
        cerrado o movimientos históricos, ni cuando es la presentación base
        (factor 1): la regla la valida el dominio y este servicio no la
        reimplementa. Tampoco se admite un factor nuevo que deje al producto
        con más de una presentación base: la operación se rechaza sobre el
        conjunto proyectado con el cambio aplicado. La lectura, la validación y
        la escritura se ejecutan dentro de la misma transacción, y el índice
        único parcial de la base también lo impide (T016).

        Args:
            presentation_id: Presentación que se modifica.
            factor: Factor nuevo, entero mayor o igual que 1.

        Returns:
            La presentación persistida con el factor actualizado.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
            InactiveProductError: Si el producto está desactivado.
            FactorNotMutableError: Si la presentación es base y el factor
                solicitado no es 1, o si tiene stock cerrado o historial.
            PresentationError: Si el factor nuevo produciría más de una
                presentación base en el producto.
            QuantityError: Si el factor no es un entero mayor o igual que uno.
        """
        with self._transaction():
            record = self._required_presentation(presentation_id)
            self._require_active_product(record.product_id)

            current = self._domain_presentation(record)
            updated = current.change_factor(factor)
            projected = [
                updated
                if presentation.id == presentation_id
                else self._domain_presentation(presentation)
                for presentation in self._presentations.list_by_product(
                    record.product_id
                )
            ]
            validate_single_base_presentation(projected)

            return self._presentations.set_factor(
                presentation_id, updated.factor
            )

    def _domain_presentation(
        self, record: PresentationRecord
    ) -> Presentation:
        """
        Reconstruye la presentación del dominio con su estado vigente (RF-07).

        El historial se deriva de los movimientos registrados de la
        presentación, de modo que el dominio pueda decidir si su factor es
        modificable.
        """
        has_history = bool(
            self._movements.list_by_presentation(record.id)
        )

        return Presentation(
            name=record.name,
            factor=record.factor,
            sale_price=record.sale_price,
            has_history=has_history,
            closed_stock=record.closed_stock,
        )

    def _required_presentation(
        self, presentation_id: int
    ) -> PresentationRecord:
        """
        Devuelve la presentación indicada y exige que exista.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
        """
        presentation = self._presentations.get(presentation_id)
        if presentation is None:
            raise RecordNotFoundError(
                f"No existe la presentación con id {presentation_id}."
            )

        return presentation

    def _require_active_product(self, product_id: int) -> ProductRecord:
        """
        Devuelve el producto indicado y exige que exista y esté activo
        (RF-01, RF-07).

        Raises:
            RecordNotFoundError: Si el producto no existe.
            InactiveProductError: Si el producto está desactivado.
        """
        product = self._products.get(product_id)
        if product is None:
            raise RecordNotFoundError(
                f"No existe el producto con id {product_id}."
            )

        if not product.active:
            raise InactiveProductError(
                f"El producto '{product.sku}' está desactivado y no admite "
                "operaciones sobre sus presentaciones."
            )

        return product
