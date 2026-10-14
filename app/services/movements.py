"""Servicios de aplicación de movimientos de inventario (T012, T013 y T014).

Implementa el registro de entradas y salidas cerradas y sueltas conforme a
RF-02, RF-03 y RF-07, reutilizando las reglas del dominio: las cantidades, el
costo y el precio se validan con `app/domain/quantities.py`; el motivo se
recorta y se exige con `normalize_required_text`; los saldos resultantes se
validan con `app/domain/stock.py`; y las salidas cerradas consumen las entradas
cerradas con `consume_closed_entries_fifo` de `app/domain/movements.py`. El
servicio no reimplementa ninguna de esas reglas.

Alcance de una operación ordinaria:

- Las operaciones cerradas afectan el stock cerrado de una presentación con
  factor mayor que uno. La presentación base (factor 1) no admite operaciones
  cerradas: sus movimientos se registran como operaciones sueltas.
- Las operaciones sueltas afectan las unidades sueltas del producto y se
  asocian a la presentación base, es decir, a la unidad base (factor 1).
- El costo es opcional y solo se admite en las entradas (RF-07). Las salidas
  guardan como `applied_price` una copia del precio de venta vigente de la
  presentación afectada, capturada al confirmar; los cambios posteriores de
  precio no alteran esa copia.
- Un producto desactivado no admite entradas ni salidas ordinarias nuevas
  (RF-01): se rechazan con `InactiveProductError`.
- Las salidas nunca dejan stock negativo: se rechazan contra la disponibilidad
  vigente del tipo afectado (RF-02).

La apertura de presentaciones cerradas (T013) reutiliza la misma validación del
dominio y el mismo consumo FIFO: `open_presentations` decide si el factor y la
disponibilidad permiten abrir, de modo que la presentación base (factor 1) se
rechaza con la regla del dominio y no se reimplementa. La apertura resta la
cantidad abierta al stock cerrado, suma `cantidad × factor` a las unidades
sueltas, no modifica el total equivalente y registra un movimiento de apertura
que no es compensable y que no lleva costo ni precio aplicado.

La compensación de un movimiento confirmado (T014) también reutiliza las reglas
del dominio: `validate_compensable_kind` rechaza las aperturas, las
compensaciones encadenadas y las salidas cerradas (fuera del MVP) antes de
cualquier escritura, y `compensate` acota la cantidad a la cantidad compensable
pendiente y al stock vigente del tipo afectado. El servicio solo reúne esas
reglas con la persistencia: registra el movimiento de tipo `COMPENSATION`
vinculado al original y actualiza el stock y el contador de compensaciones de
una entrada cerrada, o las unidades sueltas en las operaciones sueltas. El
movimiento original conserva todos sus datos históricos y un producto
desactivado admite sus compensaciones históricas válidas.

Cada confirmación se ejecuta dentro de una única transacción: la verificación
de la disponibilidad vigente, el alta del movimiento, la actualización del
saldo y, en las salidas cerradas, la actualización de los contadores FIFO de
las entradas consumidas se aplican juntos o se revierten juntos. La transacción
la abre este mismo servicio con el helper compartido `transaction` de
`app/infrastructure/sqlite/connection.py`, que comienza con `BEGIN IMMEDIATE`:
SQLite toma el bloqueo de escritura antes de leer, así que las lecturas y las
validaciones se hacen sobre el estado vigente y dos operaciones simultáneas no
pueden confirmar sobre un estado obsoleto. La protección de concurrencia forma
parte de esta implementación (T016).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal

from app.domain.movements import (
    ClosedEntry,
    CompensationResult,
    FifoConsumptionResult,
    MovementKind,
    compensate,
    consume_closed_entries_fifo,
    validate_compensable_kind,
)
from app.domain.products import normalize_required_text
from app.domain.quantities import (
    validate_cost,
    validate_movement_quantity,
    validate_price,
)
from app.domain.stock import (
    InsufficientClosedStockError,
    open_presentations,
    validate_closed_stock,
    validate_loose_units,
)
from app.services.ports import (
    MovementRecord,
    MovementRepository,
    PresentationRecord,
    PresentationRepository,
    ProductRecord,
    ProductRepository,
    RecordNotFoundError,
)
from app.services.products import InactiveProductError, TransactionScope


class MovementServiceError(ValueError):
    """Error de una entrada o una salida ordinaria de inventario (T012)."""


class InvalidMovementReasonError(MovementServiceError):
    """Error que indica que el motivo del movimiento no es válido (RF-03)."""


class BasePresentationOperationError(MovementServiceError):
    """Error que indica una operación cerrada sobre la presentación base."""


class InsufficientLooseUnitsError(MovementServiceError):
    """Error que indica que una salida supera las unidades sueltas (RF-02)."""


class MovementService:
    """Casos de uso de movimientos, aperturas y compensaciones."""

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
            products: Repositorio de productos, para verificar su existencia y
                su estado activo y para actualizar las unidades sueltas.
            presentations: Repositorio de presentaciones, para resolver la
                presentación afectada y actualizar su stock cerrado.
            movements: Repositorio de movimientos, para registrar el movimiento
                y actualizar los contadores FIFO de las entradas cerradas.
            transaction: Fábrica de transacciones que envuelve cada operación,
                para que el movimiento, el saldo y los contadores se apliquen
                de forma atómica.
        """
        self._products = products
        self._presentations = presentations
        self._movements = movements
        self._transaction = transaction

    def register_loose_entry(
        self,
        product_id: int,
        *,
        quantity: int,
        reason: str,
        cost: Decimal | None = None,
    ) -> MovementRecord:
        """
        Registra una entrada de unidades sueltas (RF-02, RF-03).

        La entrada suma la cantidad a las unidades sueltas del producto y se
        asocia a la presentación base, que es la unidad base (factor 1). El
        costo es opcional y, cuando se informa, no se distribuye entre las
        unidades ni implica una valoración del inventario (RF-07).

        Args:
            product_id: Producto que recibe la entrada.
            quantity: Cantidad de unidades sueltas, entero mayor que cero.
            reason: Motivo de la entrada; se recorta y no puede quedar vacío.
            cost: Costo de ingreso opcional, `Decimal` mayor o igual que cero.

        Returns:
            El movimiento persistido.

        Raises:
            RecordNotFoundError: Si el producto no existe o no tiene
                presentación base.
            InactiveProductError: Si el producto está desactivado.
            QuantityError: Si la cantidad o el costo son inválidos.
            InvalidMovementReasonError: Si el motivo queda vacío después de
                recortarlo.
            sqlite3.IntegrityError: Si una escritura incumple el esquema; la
                transacción se revierte por completo.
        """
        validated_quantity = validate_movement_quantity(quantity)
        validated_reason = _validate_movement_reason(reason)
        validated_cost = None if cost is None else validate_cost(cost)
        occurred_at = _utc_now()

        with self._transaction():
            product = self._require_active_product(product_id)
            base = self._base_presentation(product_id)
            movement = self._movements.add(
                product_id=product.id,
                presentation_id=base.id,
                kind=MovementKind.LOOSE_ENTRY,
                quantity=validated_quantity,
                reason=validated_reason,
                occurred_at=occurred_at,
                cost=validated_cost,
            )
            self._products.set_loose_units(
                product.id,
                validate_loose_units(
                    product.loose_units + validated_quantity
                ),
            )

        return movement

    def register_closed_entry(
        self,
        product_id: int,
        *,
        presentation_id: int,
        quantity: int,
        reason: str,
        cost: Decimal | None = None,
    ) -> MovementRecord:
        """
        Registra una entrada de presentaciones cerradas (RF-02, RF-03).

        La entrada suma la cantidad al stock cerrado de la presentación
        indicada y no altera las unidades sueltas del producto. La operación se
        rechaza sobre la presentación base, cuyas operaciones son sueltas.

        Args:
            product_id: Producto que recibe la entrada.
            presentation_id: Presentación cerrada del producto, con factor
                mayor que uno.
            quantity: Cantidad de presentaciones cerradas, entero mayor que
                cero.
            reason: Motivo de la entrada; se recorta y no puede quedar vacío.
            cost: Costo de ingreso opcional, `Decimal` mayor o igual que cero.

        Returns:
            El movimiento persistido.

        Raises:
            RecordNotFoundError: Si el producto no existe o si la presentación
                no existe o no pertenece al producto.
            InactiveProductError: Si el producto está desactivado.
            BasePresentationOperationError: Si la presentación es la base
                (factor 1).
            QuantityError: Si la cantidad o el costo son inválidos.
            InvalidMovementReasonError: Si el motivo queda vacío después de
                recortarlo.
            sqlite3.IntegrityError: Si una escritura incumple el esquema; la
                transacción se revierte por completo.
        """
        validated_quantity = validate_movement_quantity(quantity)
        validated_reason = _validate_movement_reason(reason)
        validated_cost = None if cost is None else validate_cost(cost)
        occurred_at = _utc_now()

        with self._transaction():
            product = self._require_active_product(product_id)
            presentation = self._closed_presentation(
                product_id, presentation_id
            )
            movement = self._movements.add(
                product_id=product.id,
                presentation_id=presentation.id,
                kind=MovementKind.CLOSED_ENTRY,
                quantity=validated_quantity,
                reason=validated_reason,
                occurred_at=occurred_at,
                cost=validated_cost,
            )
            self._presentations.set_closed_stock(
                presentation.id,
                validate_closed_stock(
                    presentation.closed_stock + validated_quantity
                ),
            )

        return movement

    def register_loose_exit(
        self,
        product_id: int,
        *,
        quantity: int,
        reason: str,
    ) -> MovementRecord:
        """
        Registra una salida de unidades sueltas (RF-02, RF-03).

        La salida resta la cantidad de las unidades sueltas del producto y se
        asocia a la presentación base, que es la unidad base (factor 1). El
        precio aplicado es una copia del precio de venta vigente de esa
        presentación, capturada al confirmar (RF-07).

        Args:
            product_id: Producto del que sale el stock.
            quantity: Cantidad de unidades sueltas, entero mayor que cero.
            reason: Motivo de la salida; se recorta y no puede quedar vacío.

        Returns:
            El movimiento persistido.

        Raises:
            RecordNotFoundError: Si el producto no existe o no tiene
                presentación base.
            InactiveProductError: Si el producto está desactivado.
            QuantityError: Si la cantidad es inválida.
            InvalidMovementReasonError: Si el motivo queda vacío después de
                recortarlo.
            InsufficientLooseUnitsError: Si la cantidad supera las unidades
                sueltas disponibles.
            sqlite3.IntegrityError: Si una escritura incumple el esquema; la
                transacción se revierte por completo.
        """
        validated_quantity = validate_movement_quantity(quantity)
        validated_reason = _validate_movement_reason(reason)
        occurred_at = _utc_now()

        with self._transaction():
            product = self._require_active_product(product_id)
            base = self._base_presentation(product_id)
            self._require_loose_units(product, validated_quantity)
            movement = self._movements.add(
                product_id=product.id,
                presentation_id=base.id,
                kind=MovementKind.LOOSE_EXIT,
                quantity=validated_quantity,
                reason=validated_reason,
                occurred_at=occurred_at,
                applied_price=validate_price(base.sale_price),
            )
            self._products.set_loose_units(
                product.id,
                validate_loose_units(
                    product.loose_units - validated_quantity
                ),
            )

        return movement

    def register_closed_exit(
        self,
        product_id: int,
        *,
        presentation_id: int,
        quantity: int,
        reason: str,
    ) -> MovementRecord:
        """
        Registra una salida de presentaciones cerradas (RF-02, RF-03).

        La salida resta la cantidad del stock cerrado de la presentación
        indicada, consume las entradas cerradas en orden FIFO y actualiza sus
        contadores de consumo dentro de la misma transacción. El consumo lo
        calcula el dominio (`consume_closed_entries_fifo`); este servicio solo
        lo persiste con la actualización condicional del repositorio. El precio
        aplicado es una copia del precio de venta vigente de la presentación,
        capturada al confirmar (RF-07).

        Args:
            product_id: Producto del que sale el stock.
            presentation_id: Presentación cerrada del producto, con factor
                mayor que uno.
            quantity: Cantidad de presentaciones cerradas, entero mayor que
                cero.
            reason: Motivo de la salida; se recorta y no puede quedar vacío.

        Returns:
            El movimiento persistido.

        Raises:
            RecordNotFoundError: Si el producto no existe o si la presentación
                no existe o no pertenece al producto.
            InactiveProductError: Si el producto está desactivado.
            BasePresentationOperationError: Si la presentación es la base
                (factor 1).
            QuantityError: Si la cantidad es inválida.
            InvalidMovementReasonError: Si el motivo queda vacío después de
                recortarlo.
            InsufficientClosedStockError: Si la cantidad supera el stock
                cerrado disponible o el consumo FIFO no puede distribuirla.
            sqlite3.IntegrityError: Si una escritura incumple el esquema; la
                transacción se revierte por completo.
        """
        validated_quantity = validate_movement_quantity(quantity)
        validated_reason = _validate_movement_reason(reason)
        occurred_at = _utc_now()

        with self._transaction():
            product = self._require_active_product(product_id)
            presentation = self._closed_presentation(
                product_id, presentation_id
            )
            self._require_closed_stock(presentation, validated_quantity)
            entries = self._closed_entries(presentation.id)
            consumption = consume_closed_entries_fifo(
                entries,
                presentation_id=presentation.id,
                quantity=validated_quantity,
            )
            movement = self._movements.add(
                product_id=product.id,
                presentation_id=presentation.id,
                kind=MovementKind.CLOSED_EXIT,
                quantity=validated_quantity,
                reason=validated_reason,
                occurred_at=occurred_at,
                applied_price=validate_price(presentation.sale_price),
            )
            self._presentations.set_closed_stock(
                presentation.id,
                validate_closed_stock(
                    presentation.closed_stock - validated_quantity
                ),
            )
            self._apply_fifo_consumption(entries, consumption)

        return movement

    def register_aperture(
        self,
        product_id: int,
        *,
        presentation_id: int,
        quantity: int,
        reason: str,
    ) -> MovementRecord:
        """
        Registra la apertura de presentaciones cerradas (RF-02, RF-03).

        La apertura resta la cantidad abierta al stock cerrado de la
        presentación y suma `cantidad × factor` a las unidades sueltas del
        producto en una única transacción, sin alterar el total equivalente.
        La operación consume las entradas cerradas de esa presentación en orden
        FIFO y actualiza sus contadores de consumo dentro de la misma
        transacción.

        La validez del factor y de la disponibilidad la decide el dominio:
        `open_presentations` rechaza la presentación base (factor 1) con
        `BasePresentationApertureError` y rechaza una cantidad que supera el
        stock cerrado con `InsufficientClosedStockError`; este servicio no
        reimplementa esas reglas. El movimiento de apertura no es compensable y
        no registra costo ni precio aplicado.

        Args:
            product_id: Producto cuya presentación cerrada se abre.
            presentation_id: Presentación cerrada del producto, con factor
                mayor que uno.
            quantity: Cantidad de presentaciones cerradas que se abre, entero
                mayor que cero.
            reason: Motivo de la apertura; se recorta y no puede quedar vacío.

        Returns:
            El movimiento de apertura persistido.

        Raises:
            RecordNotFoundError: Si el producto no existe o si la presentación
                no existe o no pertenece al producto.
            InactiveProductError: Si el producto está desactivado.
            QuantityError: Si la cantidad es inválida.
            BasePresentationApertureError: Si la presentación es la base
                (factor 1), regla validada por el dominio.
            InsufficientClosedStockError: Si no hay stock cerrado suficiente o
                si el consumo FIFO no puede distribuir la cantidad solicitada.
            InvalidMovementReasonError: Si el motivo queda vacío después de
                recortarlo.
            sqlite3.IntegrityError: Si una escritura incumple el esquema; la
                transacción se revierte por completo.
        """
        occurred_at = _utc_now()

        with self._transaction():
            product = self._require_active_product(product_id)
            presentation = self._required_presentation(
                product_id, presentation_id
            )
            aperture = open_presentations(
                loose_units=product.loose_units,
                closed_stock=presentation.closed_stock,
                quantity=quantity,
                factor=presentation.factor,
            )
            entries = self._closed_entries(presentation.id)
            consumption = consume_closed_entries_fifo(
                entries,
                presentation_id=presentation.id,
                quantity=aperture.quantity,
            )
            movement = self._movements.add(
                product_id=product.id,
                presentation_id=presentation.id,
                kind=MovementKind.APERTURE,
                quantity=aperture.quantity,
                reason=_validate_movement_reason(reason),
                occurred_at=occurred_at,
            )
            self._presentations.set_closed_stock(
                presentation.id,
                validate_closed_stock(aperture.closed_stock),
            )
            self._products.set_loose_units(
                product.id,
                validate_loose_units(aperture.loose_units),
            )
            self._apply_fifo_consumption(entries, consumption)

        return movement

    def register_compensation(
        self,
        product_id: int,
        *,
        movement_id: int,
        quantity: int,
        reason: str,
    ) -> MovementRecord:
        """
        Registra la compensación de un movimiento confirmado (RF-03).

        La compensación neutraliza total o parcialmente la cantidad del
        movimiento original y se registra como un movimiento de tipo
        `COMPENSATION` vinculado a él, cuyo efecto sobre el stock es el opuesto
        al del movimiento original. El movimiento original no se modifica ni se
        elimina: de una entrada cerrada solo cambia su contador de
        compensaciones, que es la traza de las compensaciones ya confirmadas.

        La validez la decide el dominio: `validate_compensable_kind` rechaza
        las aperturas, las compensaciones encadenadas y las salidas cerradas
        (fuera del MVP) antes de cualquier escritura, y `compensate` acota la
        cantidad a la cantidad compensable pendiente y, en las entradas, al
        stock vigente del tipo afectado. Un producto desactivado admite una
        compensación histórica válida (RF-03), por lo que esta operación no
        exige que el producto esté activo.

        Args:
            product_id: Producto al que pertenece el movimiento compensado.
            movement_id: Movimiento original que se compensa.
            quantity: Cantidad neutralizada, entero mayor que cero; puede ser
                parcial o total respecto de la cantidad compensable pendiente.
            reason: Motivo de la compensación; se recorta y no puede quedar
                vacío.

        Returns:
            El movimiento compensatorio persistido.

        Raises:
            RecordNotFoundError: Si el movimiento original no existe o no
                pertenece al producto indicado.
            ApertureNotCompensableError: Si el movimiento original es una
                apertura, regla validada por el dominio.
            ChainedCompensationError: Si el movimiento original es otra
                compensación, regla validada por el dominio.
            NotCompensableError: Si el movimiento original es una salida
                cerrada, cuya compensación queda fuera del MVP; regla validada
                por el dominio.
            QuantityError: Si la cantidad es inválida.
            InvalidMovementReasonError: Si el motivo queda vacío después de
                recortarlo.
            ExcessCompensationError: Si la cantidad supera la cantidad
                compensable pendiente del movimiento original.
            InsufficientMovementStockError: Si la compensación de una entrada
                dejaría el stock afectado en negativo.
            sqlite3.IntegrityError: Si una escritura incumple el esquema; la
                transacción se revierte por completo.
        """
        validated_quantity = validate_movement_quantity(quantity)
        validated_reason = _validate_movement_reason(reason)
        occurred_at = _utc_now()

        with self._transaction():
            original = self._compensated_movement(product_id, movement_id)
            kind = validate_compensable_kind(original.kind)
            compensable_quantity, available_stock = (
                self._compensation_availability(original, kind)
            )
            result = compensate(
                original_kind=kind,
                quantity=validated_quantity,
                compensable_quantity=compensable_quantity,
                available_stock=available_stock,
            )
            movement = self._movements.add(
                product_id=original.product_id,
                presentation_id=original.presentation_id,
                kind=MovementKind.COMPENSATION,
                quantity=result.quantity,
                reason=validated_reason,
                occurred_at=occurred_at,
                original_movement_id=original.id,
            )
            self._apply_compensation(original, kind, result)

        return movement

    def _apply_fifo_consumption(
        self,
        entries: Sequence[ClosedEntry],
        consumption: FifoConsumptionResult,
    ) -> None:
        """
        Persiste el consumo FIFO que calculó el dominio (RF-03).

        Solo se actualizan las entradas cuya cantidad consumida cambió: el
        repositorio reemplaza el contador con una actualización condicional y
        conserva intactos los datos históricos del movimiento confirmado.

        Args:
            entries: Entradas cerradas que se usaron para calcular el consumo,
                con su cantidad consumida previa.
            consumption: Resultado del consumo FIFO de las entradas cerradas.
        """
        previous_consumed = {
            entry.entry_id: entry.consumed_quantity for entry in entries
        }
        for updated in consumption.entries:
            if (
                updated.consumed_quantity
                != previous_consumed[updated.entry_id]
            ):
                self._movements.set_consumed_quantity(
                    updated.entry_id, updated.consumed_quantity
                )

    def _closed_entries(self, presentation_id: int) -> tuple[ClosedEntry, ...]:
        """
        Reconstruye las entradas cerradas vigentes de una presentación (RF-03).

        Las entradas se reconstruyen con su consumo y su compensación vigentes,
        de modo que el dominio pueda aplicar el orden FIFO. La consulta se
        limita a las entradas cerradas de la presentación indicada.

        Args:
            presentation_id: Presentación cuyas entradas cerradas se recuperan.

        Returns:
            Las entradas cerradas vigentes de la presentación, con su consumo
            y su compensación.
        """
        records = self._movements.list_by_presentation(
            presentation_id, MovementKind.CLOSED_ENTRY
        )

        return tuple(
            ClosedEntry(
                entry_id=record.id,
                presentation_id=record.presentation_id,
                quantity=record.quantity,
                consumed_quantity=record.consumed_quantity,
                compensated_quantity=record.compensated_quantity,
            )
            for record in records
        )

    def _compensated_movement(
        self, product_id: int, movement_id: int
    ) -> MovementRecord:
        """
        Devuelve el movimiento original que se compensa (RF-03).

        Raises:
            RecordNotFoundError: Si el movimiento no existe o no pertenece al
                producto indicado.
        """
        original = self._movements.get(movement_id)
        if original is None or original.product_id != product_id:
            raise RecordNotFoundError(
                f"No existe el movimiento con id {movement_id} en el "
                f"producto {product_id}."
            )

        return original

    def _compensation_availability(
        self, original: MovementRecord, kind: MovementKind
    ) -> tuple[int, int]:
        """
        Calcula la cantidad compensable pendiente y el stock afectado (RF-03).

        En una entrada cerrada, la cantidad compensable es la que el dominio
        deriva de su trazabilidad FIFO (`cantidad − consumida − compensada`) y
        el stock afectado es el stock cerrado vigente de su presentación. En
        una entrada o salida suelta, que el MVP no traza por lote, la cantidad
        pendiente es la cantidad original menos las compensaciones ya
        vinculadas al movimiento y el stock afectado son las unidades sueltas
        vigentes del producto.

        Args:
            original: Movimiento original que se compensa.
            kind: Tipo validado del movimiento original.

        Returns:
            La cantidad compensable pendiente y el stock vigente del tipo
            afectado por la compensación.
        """
        if kind is MovementKind.CLOSED_ENTRY:
            presentation = self._presentations.get(original.presentation_id)
            if presentation is None:
                raise RecordNotFoundError(
                    "No existe la presentación con id "
                    f"{original.presentation_id}."
                )

            entry = ClosedEntry(
                entry_id=original.id,
                presentation_id=original.presentation_id,
                quantity=original.quantity,
                consumed_quantity=original.consumed_quantity,
                compensated_quantity=original.compensated_quantity,
            )

            return entry.compensable_quantity, presentation.closed_stock

        product = self._products.get(original.product_id)
        if product is None:
            raise RecordNotFoundError(
                f"No existe el producto con id {original.product_id}."
            )

        compensated_quantity = sum(
            record.quantity
            for record in self._movements.list_by_product(
                original.product_id, MovementKind.COMPENSATION
            )
            if record.original_movement_id == original.id
        )

        return original.quantity - compensated_quantity, product.loose_units

    def _apply_compensation(
        self,
        original: MovementRecord,
        kind: MovementKind,
        result: CompensationResult,
    ) -> None:
        """
        Persiste el efecto de una compensación válida (RF-03).

        La compensación de una entrada cerrada resta stock cerrado a su
        presentación y actualiza el contador de compensaciones del movimiento
        original; la de una entrada o salida suelta actualiza las unidades
        sueltas del producto. Los demás datos del movimiento original quedan
        intactos.

        Args:
            original: Movimiento original que se compensa, con sus contadores
                vigentes.
            kind: Tipo validado del movimiento original.
            result: Resultado de la compensación calculado por el dominio.
        """
        if kind is MovementKind.CLOSED_ENTRY:
            self._presentations.set_closed_stock(
                original.presentation_id,
                validate_closed_stock(result.stock_after),
            )
            self._movements.set_compensated_quantity(
                original.id,
                original.compensated_quantity + result.quantity,
            )

            return

        self._products.set_loose_units(
            original.product_id,
            validate_loose_units(result.stock_after),
        )

    def _require_active_product(self, product_id: int) -> ProductRecord:
        """
        Devuelve el producto indicado y exige que exista y esté activo (RF-01).

        Raises:
            RecordNotFoundError: Si el producto no existe.
            InactiveProductError: Si el producto está desactivado, porque un
                producto desactivado no admite entradas ni salidas ordinarias
                nuevas.
        """
        product = self._products.get(product_id)
        if product is None:
            raise RecordNotFoundError(
                f"No existe el producto con id {product_id}."
            )

        if not product.active:
            raise InactiveProductError(
                f"El producto '{product.sku}' está desactivado y no admite "
                "entradas ni salidas ordinarias."
            )

        return product

    def _base_presentation(self, product_id: int) -> PresentationRecord:
        """
        Devuelve la presentación base del producto (RF-07).

        La presentación base tiene factor 1 y es la unidad base del producto:
        las operaciones sueltas se asocian a ella y usan su precio de venta.

        Raises:
            RecordNotFoundError: Si el producto no tiene presentación base.
        """
        for presentation in self._presentations.list_by_product(product_id):
            if presentation.factor == 1:
                return presentation

        raise RecordNotFoundError(
            f"El producto {product_id} no tiene una presentación base con "
            "factor 1."
        )

    def _closed_presentation(
        self, product_id: int, presentation_id: int
    ) -> PresentationRecord:
        """
        Devuelve la presentación de una operación cerrada (RF-02, RF-07).

        Una operación cerrada solo es válida sobre una presentación del
        producto cuyo factor sea mayor que uno; la presentación base (factor 1)
        no admite operaciones cerradas.

        Raises:
            RecordNotFoundError: Si la presentación no existe o no pertenece
                al producto.
            BasePresentationOperationError: Si la presentación es la base
                (factor 1).
        """
        presentation = self._presentations.get(presentation_id)
        if presentation is None or presentation.product_id != product_id:
            raise RecordNotFoundError(
                f"No existe la presentación con id {presentation_id} en el "
                f"producto {product_id}."
            )

        if presentation.factor == 1:
            raise BasePresentationOperationError(
                f"La presentación base '{presentation.name}' (factor 1) no "
                "admite operaciones cerradas: use las operaciones sueltas de "
                "la unidad base."
            )

        return presentation

    def _required_presentation(
        self, product_id: int, presentation_id: int
    ) -> PresentationRecord:
        """
        Devuelve la presentación del producto y exige que exista (RF-07).

        La apertura resuelve la presentación por su existencia y su pertenencia
        al producto, sin evaluar su factor: la validez del factor la decide el
        dominio al abrir (`open_presentations`), de modo que la presentación
        base (factor 1) se rechaza con la regla de la apertura
        (`BasePresentationApertureError`) y no con el error de las operaciones
        cerradas ordinarias.

        Raises:
            RecordNotFoundError: Si la presentación no existe o no pertenece
                al producto.
        """
        presentation = self._presentations.get(presentation_id)
        if presentation is None or presentation.product_id != product_id:
            raise RecordNotFoundError(
                f"No existe la presentación con id {presentation_id} en el "
                f"producto {product_id}."
            )

        return presentation

    @staticmethod
    def _require_loose_units(
        product: ProductRecord, quantity: int
    ) -> None:
        """
        Exige unidades sueltas suficientes para una salida (RF-02).

        Raises:
            InsufficientLooseUnitsError: Si la cantidad supera las unidades
                sueltas disponibles del producto.
        """
        if quantity > product.loose_units:
            raise InsufficientLooseUnitsError(
                "No hay unidades sueltas suficientes para la salida: se "
                f"solicitaron {quantity} y hay {product.loose_units} "
                "disponibles."
            )

    @staticmethod
    def _require_closed_stock(
        presentation: PresentationRecord, quantity: int
    ) -> None:
        """
        Exige stock cerrado suficiente para una salida (RF-02).

        Raises:
            InsufficientClosedStockError: Si la cantidad supera el stock
                cerrado disponible de la presentación.
        """
        if quantity > presentation.closed_stock:
            raise InsufficientClosedStockError(
                "No hay stock cerrado suficiente para la salida: se "
                f"solicitaron {quantity} presentaciones y hay "
                f"{presentation.closed_stock} disponibles."
            )


def _validate_movement_reason(reason: str) -> str:
    """
    Recorta el motivo del movimiento y exige que no quede vacío (RF-03).

    Reutiliza la normalización de los campos de texto obligatorios: rechaza un
    motivo vacío o compuesto solo por espacios, tabulaciones o saltos de línea,
    y conserva el texto original recortado.

    Args:
        reason: Motivo informado para el movimiento.

    Returns:
        El motivo recortado y validado.

    Raises:
        InvalidMovementReasonError: Si el motivo no es texto o queda vacío
            después de recortarlo.
    """
    return normalize_required_text(
        reason, "motivo", InvalidMovementReasonError
    )


def _utc_now() -> datetime:
    """Devuelve el instante actual en UTC, con zona horaria."""
    return datetime.now(timezone.utc)
