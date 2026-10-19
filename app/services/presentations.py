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

El cambio de precio de venta (RF-08, T031) es atómico: dentro de la misma
transacción se valida que la presentación exista, que su producto esté activo y
que el precio sea válido, y recién entonces se actualiza el precio y se anexa
el cambio al historial con el precio anterior leído antes del `UPDATE`. Si el
historial no se pudiera registrar, la transacción revierte la actualización
completa: no quedan precios cambiados sin su evento ni eventos sin su cambio.
El cambio de precio no altera los saldos, los movimientos ni el precio aplicado
de las salidas confirmadas.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
    PriceHistoryRepository,
    ProductRecord,
    ProductRepository,
    RecordNotFoundError,
)
from app.services.products import InactiveProductError, TransactionScope


class PresentationService:
    """Casos de uso de las presentaciones de un producto (RF-07 y RF-08)."""

    def __init__(
        self,
        *,
        products: ProductRepository,
        presentations: PresentationRepository,
        movements: MovementRepository,
        price_history: PriceHistoryRepository,
        transaction: TransactionScope,
    ) -> None:
        """
        Args:
            products: Repositorio de productos, para verificar que el producto
                exista y esté activo.
            presentations: Repositorio de presentaciones.
            movements: Repositorio de movimientos, para determinar si una
                presentación tiene historial y si su factor es inmutable.
            price_history: Historial de precios, donde se anexa cada cambio de
                precio dentro de la transacción del cambio.
            transaction: Fábrica de transacciones que envuelve el alta, el
                cambio de factor y el cambio de precio, para que la lectura, la
                validación y la escritura se apliquen dentro de la misma
                transacción.
        """
        self._products = products
        self._presentations = presentations
        self._movements = movements
        self._price_history = price_history
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
        Modifica el precio de venta de una presentación y registra el cambio
        (RF-07, RF-08).

        Las salidas ya confirmadas conservan su precio aplicado; este cambio
        solo afecta a las operaciones futuras. La operación se ejecuta dentro
        de una única transacción: primero se valida que la presentación
        exista, que su producto esté activo y que el precio sea válido, y
        recién entonces se actualiza el precio y se anexa el cambio al
        historial con el precio anterior leído antes del `UPDATE`. Si el
        historial no se pudiera registrar, la transacción revierte la
        actualización completa. Un precio igual al vigente también se
        registra: no es un no-op.

        Args:
            presentation_id: Presentación cuyo precio se modifica.
            sale_price: Precio de venta nuevo, mayor que cero.

        Returns:
            La presentación persistida con el precio actualizado.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
            InactiveProductError: Si el producto está desactivado.
            QuantityError: Si el precio no es un Decimal finito mayor que cero.
            sqlite3.IntegrityError: Si el historial incumple el esquema; la
                transacción se revierte por completo.
        """
        with self._transaction():
            record = self._required_presentation(presentation_id)
            self._require_active_product(record.product_id)
            validated_price = validate_price(sale_price)
            updated = self._presentations.set_sale_price(
                presentation_id, validated_price
            )
            self._price_history.add(
                product_id=record.product_id,
                presentation_id=presentation_id,
                previous_price=record.sale_price,
                new_price=validated_price,
                occurred_at=_utc_now(),
            )

            return updated

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

    def get_presentation(self, presentation_id: int) -> PresentationRecord:
        """
        Devuelve la presentación indicada (T034, RF-08).

        Es una consulta de solo lectura: no exige que el producto esté activo,
        no abre transacciones y no modifica precios, saldos ni historial, de
        modo que el historial de precios de un producto desactivado siga siendo
        consultable (RF-01, RF-08).

        Args:
            presentation_id: Presentación que se consulta.

        Returns:
            La presentación persistida.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
        """
        return self._required_presentation(presentation_id)

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


def _utc_now() -> datetime:
    """Devuelve el instante actual en UTC, con zona horaria."""
    return datetime.now(timezone.utc)
