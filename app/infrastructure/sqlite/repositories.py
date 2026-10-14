"""Repositorios SQLite del inventario (T009 y T010).

Implementa los contratos de `app/services/ports.py` sobre las tablas creadas
por `app/infrastructure/sqlite/schema.py`. Los repositorios solo persisten y
consultan: no revalidan las reglas del dominio, que se aplican antes de
invocarlos.

Convenciones de la capa:

- Los identificadores, cantidades, factores, saldos y contadores son enteros.
- Los precios y costos viajan como `Decimal` exacto y se serializan como
  texto decimal de punto fijo, sin notación científica ni ceros fraccionarios
  finales innecesarios; el cero negativo se normaliza a `0` y el valor
  numérico exacto se preserva.
- Los instantes viajan como `datetime` con zona horaria y se almacenan en UTC
  con la representación uniforme de `app/infrastructure/sqlite/instants.py`,
  que permite compararlos por rango como texto.
- Los períodos de consulta se interpretan en la hora local de Argentina y se
  convierten a UTC con la semántica `[inicio, fin)`: el inicio es inclusivo y
  el final es exclusivo. Los filtros de producto, tipo y período se combinan
  mediante AND (RF-03).
- Las búsquedas por SKU y por nombre de producto comparan sin distinguir
  mayúsculas de minúsculas, con la misma colación (`NOCASE`) que las
  restricciones de unicidad del esquema, y devuelven el texto almacenado.
- Las conexiones se reciben ya abiertas y configuradas por
  `app/infrastructure/sqlite/connection.py`; abrir transacciones es
  responsabilidad de los servicios de confirmación.
- La actualización de los contadores FIFO de una entrada cerrada (consumo y
  compensación) se aplica solo sobre entradas cerradas, reemplaza únicamente el
  contador indicado y respeta el límite `consumida + compensada <= cantidad`
  que el esquema impone con `CHECK`.
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import NoReturn

from app.domain.movements import MovementKind
from app.domain.presentations import (
    DuplicatePresentationNameError,
    PresentationError,
)
from app.domain.products import DuplicateSkuError
from app.infrastructure.sqlite.instants import (
    from_utc_text,
    resolve_period,
    to_utc_text,
)
from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
    RecordNotFoundError,
)


class NotAClosedEntryError(ValueError):
    """
    Error que indica que el movimiento no es una entrada cerrada elegible.

    Solo las entradas cerradas tienen consumo FIFO: el contador de consumo de
    los demás tipos de movimiento no se actualiza.
    """


_PRODUCT_COLUMNS = (
    "id, sku, name, base_unit, minimum_stock, loose_units, active"
)

_PRESENTATION_COLUMNS = (
    "id, product_id, name, factor, sale_price, closed_stock"
)

_MOVEMENT_COLUMNS = (
    "id, product_id, presentation_id, kind, quantity, reason, occurred_at, "
    "cost, applied_price, is_initial_stock, original_movement_id, "
    "consumed_quantity, compensated_quantity"
)


def _decimal_to_text(value: Decimal) -> str:
    """
    Serializa un `Decimal` exacto con su representación textual canónica.

    La representación canónica es un decimal en notación fija, sin notación
    científica y sin ceros finales, que conserva el valor exacto del `Decimal`:
    nunca pasa por `float` ni se redondea con la precisión del contexto.

    - `Decimal("10.50")` se serializa como `"10.5"` y `Decimal("0.300")` como
      `"0.3"`.
    - `Decimal("1E+2")` se serializa como `"100"` y un valor entero como
      `Decimal("100")` no agrega el sufijo `.0` innecesario.
    - `Decimal("-0")` se serializa como `"0"`: el cero nunca lleva signo.
    - Un valor negativo conserva su signo, por ejemplo `Decimal("-5")` se
      serializa como `"-5"`. Que un precio o un costo admitan ese valor lo
      decide la validación del dominio, no esta función.

    El resultado es compatible con las restricciones `CHECK` del esquema, que
    exigen que el texto empiece con un dígito y que su valor sea mayor que cero
    (precios y precios aplicados) o mayor o igual que cero (costos).

    Args:
        value: Precio o costo ya validado por el dominio.

    Returns:
        El texto canónico del `Decimal`, que conserva su valor exacto.
    """
    if value == 0:
        return "0"

    text = format(value, "f")
    if "." not in text:
        return text

    return text.rstrip("0").rstrip(".")


def _text_to_decimal(value: str | None) -> Decimal | None:
    """
    Convierte un texto decimal almacenado en un `Decimal` exacto.

    Args:
        value: Texto de la columna, o `None` cuando la columna es opcional.

    Returns:
        El `Decimal` correspondiente, o `None` si no hay valor.
    """
    if value is None:
        return None

    return Decimal(value)


def _consumed_quantity_value(value: int) -> int:
    """
    Valida un contador FIFO de una entrada cerrada.

    Se usa para el contador de consumo y para el de compensaciones: ambos son
    cantidades acumuladas de la misma entrada cerrada.

    Args:
        value: Valor informado para el contador FIFO de la entrada cerrada.

    Returns:
        El valor validado.

    Raises:
        ValueError:
            Si el valor no es un entero mayor o igual que cero.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("La cantidad consumida debe ser un entero.")

    if value < 0:
        raise ValueError("La cantidad consumida no puede ser negativa.")

    return value


def _period_conditions(
    kind: MovementKind | None,
    period_start: datetime | None,
    period_end: datetime | None,
) -> tuple[str, list[str]]:
    """
    Construye las condiciones del historial y sus parámetros.

    Los filtros indicados se combinan mediante AND. El período se expresa como
    `occurred_at >= inicio` y `occurred_at < fin`, con el inicio inclusivo y el
    final exclusivo, sobre los instantes almacenados en UTC.

    Args:
        kind: Tipo de movimiento que se filtra, o `None` para no filtrarlo.
        period_start: Inicio del período, o `None` para no acotarlo.
        period_end: Final del período, o `None` para no acotarlo.

    Returns:
        El texto de las condiciones, que comienza con ` AND ` cuando hay al
        menos una, y los parámetros correspondientes en el mismo orden.

    Raises:
        InvalidPeriodError:
            Si el período no es válido, conforme al contrato de
            `app/infrastructure/sqlite/instants.py`.
    """
    conditions: list[str] = []
    parameters: list[str] = []

    if kind is not None:
        conditions.append("kind = ?")
        parameters.append(MovementKind(kind).value)

    start_text, end_text = resolve_period(period_start, period_end)
    if start_text is not None:
        conditions.append("occurred_at >= ?")
        parameters.append(start_text)
    if end_text is not None:
        conditions.append("occurred_at < ?")
        parameters.append(end_text)

    return "".join(f" AND {condition}" for condition in conditions), parameters


def _product_record(row: sqlite3.Row) -> ProductRecord:
    """Construye un producto a partir de una fila de `products`."""
    return ProductRecord(
        id=int(row["id"]),
        sku=str(row["sku"]),
        name=str(row["name"]),
        base_unit=str(row["base_unit"]),
        minimum_stock=int(row["minimum_stock"]),
        loose_units=int(row["loose_units"]),
        active=bool(row["active"]),
    )


def _presentation_record(row: sqlite3.Row) -> PresentationRecord:
    """Construye una presentación a partir de una fila de `presentations`."""
    return PresentationRecord(
        id=int(row["id"]),
        product_id=int(row["product_id"]),
        name=str(row["name"]),
        factor=int(row["factor"]),
        sale_price=Decimal(str(row["sale_price"])),
        closed_stock=int(row["closed_stock"]),
    )


def _is_duplicate_presentation_name(error: sqlite3.IntegrityError) -> bool:
    """
    Indica si el error corresponde al nombre de presentación duplicado.

    El esquema impone la unicidad del nombre dentro del producto con
    `UNIQUE (product_id, name)`; SQLite informa ese conflicto nombrando las dos
    columnas de la restricción.
    """
    return "presentations.name" in str(error)


def _is_single_base_conflict(error: sqlite3.IntegrityError) -> bool:
    """
    Indica si el error corresponde al índice único de presentación base.

    El índice parcial `idx_presentations_single_base` impide más de una
    presentación con `factor = 1` por producto (RF-07). SQLite informa ese
    conflicto nombrando solo `presentations.product_id`, a diferencia del
    nombre duplicado, que nombra también `presentations.name`.
    """
    message = str(error)
    return (
        "presentations.name" not in message
        and "presentations.product_id" in message
    )


def _single_base_presentation_error() -> PresentationError:
    """Construye el error de la invariante de base única (RF-07)."""
    return PresentationError(
        "El producto debe tener exactamente una presentación base con "
        "factor 1."
    )


def _movement_record(row: sqlite3.Row) -> MovementRecord:
    """Construye un movimiento a partir de una fila de `movements`."""
    return MovementRecord(
        id=int(row["id"]),
        product_id=int(row["product_id"]),
        presentation_id=int(row["presentation_id"]),
        kind=MovementKind(str(row["kind"])),
        quantity=int(row["quantity"]),
        reason=str(row["reason"]),
        occurred_at=from_utc_text(str(row["occurred_at"])),
        cost=_text_to_decimal(row["cost"]),
        applied_price=_text_to_decimal(row["applied_price"]),
        is_initial_stock=bool(row["is_initial_stock"]),
        original_movement_id=(
            None
            if row["original_movement_id"] is None
            else int(row["original_movement_id"])
        ),
        consumed_quantity=int(row["consumed_quantity"]),
        compensated_quantity=int(row["compensated_quantity"]),
    )


class SqliteProductRepository:
    """Repositorio de productos sobre SQLite (RF-01)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """
        Args:
            connection: Conexión abierta con el esquema del inventario.
        """
        self._connection = connection

    def add(
        self,
        *,
        sku: str,
        name: str,
        base_unit: str,
        minimum_stock: int = 0,
        loose_units: int = 0,
        active: bool = True,
    ) -> ProductRecord:
        """
        Registra un producto y devuelve su versión persistida.

        La unicidad del SKU la impone el esquema sin distinguir mayúsculas de
        minúsculas; el texto se almacena tal como se recibe.

        Raises:
            DuplicateSkuError: Si el SKU ya está registrado.
            sqlite3.IntegrityError: Si el producto incumple las restricciones
                del esquema.
        """
        try:
            cursor = self._connection.execute(
                """
                INSERT INTO products (
                    sku, name, base_unit, minimum_stock, loose_units, active
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    sku,
                    name,
                    base_unit,
                    minimum_stock,
                    loose_units,
                    int(active),
                ),
            )
        except sqlite3.IntegrityError as error:
            if "products.sku" in str(error):
                raise DuplicateSkuError(
                    f"El SKU '{sku}' ya existe."
                ) from error
            raise

        return self._required(int(cursor.lastrowid))

    def get(self, product_id: int) -> ProductRecord | None:
        """Devuelve el producto indicado o `None` si no existe."""
        row = self._connection.execute(
            f"SELECT {_PRODUCT_COLUMNS} FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()

        return None if row is None else _product_record(row)

    def find_by_sku(self, sku: str) -> ProductRecord | None:
        """
        Busca un producto por SKU, sin distinguir mayúsculas de minúsculas.

        El texto original almacenado se conserva sin normalizar.
        """
        row = self._connection.execute(
            f"SELECT {_PRODUCT_COLUMNS} FROM products "
            "WHERE sku = ? COLLATE NOCASE",
            (sku,),
        ).fetchone()

        return None if row is None else _product_record(row)

    def find_by_name(self, name: str) -> list[ProductRecord]:
        """
        Busca productos por nombre exacto, sin distinguir mayúsculas.

        Devuelve todas las coincidencias, sin seleccionar ninguna
        automáticamente, y una lista vacía si no hay ninguna.
        """
        rows = self._connection.execute(
            f"SELECT {_PRODUCT_COLUMNS} FROM products "
            "WHERE name = ? COLLATE NOCASE ORDER BY id",
            (name,),
        ).fetchall()

        return [_product_record(row) for row in rows]

    def list_all(self) -> list[ProductRecord]:
        """Devuelve todos los productos registrados."""
        rows = self._connection.execute(
            f"SELECT {_PRODUCT_COLUMNS} FROM products ORDER BY id"
        ).fetchall()

        return [_product_record(row) for row in rows]

    def set_name(self, product_id: int, name: str) -> ProductRecord:
        """
        Actualiza el nombre del producto y devuelve su versión persistida.

        Raises:
            RecordNotFoundError: Si el producto no existe.
            sqlite3.IntegrityError: Si el nombre incumple las restricciones
                del esquema.
        """
        self._connection.execute(
            "UPDATE products SET name = ? WHERE id = ?",
            (name, product_id),
        )

        return self._required(product_id)

    def set_minimum_stock(
        self, product_id: int, minimum_stock: int
    ) -> ProductRecord:
        """
        Actualiza el stock mínimo del producto, en unidades base.

        Raises:
            RecordNotFoundError: Si el producto no existe.
            sqlite3.IntegrityError: Si el stock mínimo incumple las
                restricciones del esquema.
        """
        self._connection.execute(
            "UPDATE products SET minimum_stock = ? WHERE id = ?",
            (minimum_stock, product_id),
        )

        return self._required(product_id)

    def set_loose_units(
        self, product_id: int, loose_units: int
    ) -> ProductRecord:
        """
        Actualiza las unidades sueltas del producto.

        Raises:
            RecordNotFoundError: Si el producto no existe.
            sqlite3.IntegrityError: Si las unidades sueltas incumplen las
                restricciones del esquema.
        """
        self._connection.execute(
            "UPDATE products SET loose_units = ? WHERE id = ?",
            (loose_units, product_id),
        )

        return self._required(product_id)

    def set_active(self, product_id: int, active: bool) -> ProductRecord:
        """
        Activa o desactiva el producto conservando su historial (RF-01).

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        self._connection.execute(
            "UPDATE products SET active = ? WHERE id = ?",
            (int(active), product_id),
        )

        return self._required(product_id)

    def _required(self, product_id: int) -> ProductRecord:
        """
        Devuelve el producto indicado y exige que exista.

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        product = self.get(product_id)
        if product is None:
            raise RecordNotFoundError(
                f"No existe el producto con id {product_id}."
            )

        return product


class SqlitePresentationRepository:
    """Repositorio de presentaciones sobre SQLite (RF-07)."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """
        Args:
            connection: Conexión abierta con el esquema del inventario.
        """
        self._connection = connection

    def add(
        self,
        *,
        product_id: int,
        name: str,
        factor: int,
        sale_price: Decimal,
        closed_stock: int = 0,
    ) -> PresentationRecord:
        """
        Registra una presentación y devuelve su versión persistida.

        La unicidad del nombre dentro del producto la impone el esquema sin
        distinguir mayúsculas de minúsculas. El índice único parcial del
        esquema impide además más de una presentación base (factor 1) por
        producto; su conflicto se traduce al error de dominio de la invariante.

        Raises:
            DuplicatePresentationNameError: Si el nombre ya existe dentro del
                producto.
            PresentationError: Si el producto ya tiene una presentación base
                con factor 1.
            sqlite3.IntegrityError: Si la presentación incumple otras
                restricciones del esquema.
        """
        try:
            cursor = self._connection.execute(
                """
                INSERT INTO presentations (
                    product_id, name, factor, sale_price, closed_stock
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    name,
                    factor,
                    _decimal_to_text(sale_price),
                    closed_stock,
                ),
            )
        except sqlite3.IntegrityError as error:
            if _is_duplicate_presentation_name(error):
                raise DuplicatePresentationNameError(
                    f"La presentación '{name}' ya existe en el producto."
                ) from error
            if _is_single_base_conflict(error):
                raise _single_base_presentation_error() from error
            raise

        return self._required(int(cursor.lastrowid))

    def get(self, presentation_id: int) -> PresentationRecord | None:
        """Devuelve la presentación indicada o `None` si no existe."""
        row = self._connection.execute(
            f"SELECT {_PRESENTATION_COLUMNS} FROM presentations "
            "WHERE id = ?",
            (presentation_id,),
        ).fetchone()

        return None if row is None else _presentation_record(row)

    def list_by_product(self, product_id: int) -> list[PresentationRecord]:
        """Devuelve las presentaciones del producto indicado."""
        rows = self._connection.execute(
            f"SELECT {_PRESENTATION_COLUMNS} FROM presentations "
            "WHERE product_id = ? ORDER BY id",
            (product_id,),
        ).fetchall()

        return [_presentation_record(row) for row in rows]

    def find_by_name(
        self, product_id: int, name: str
    ) -> PresentationRecord | None:
        """
        Busca una presentación por nombre dentro del producto.

        La comparación no distingue mayúsculas de minúsculas y el texto
        original almacenado se conserva sin normalizar.
        """
        row = self._connection.execute(
            f"SELECT {_PRESENTATION_COLUMNS} FROM presentations "
            "WHERE product_id = ? AND name = ? COLLATE NOCASE",
            (product_id, name),
        ).fetchone()

        return None if row is None else _presentation_record(row)

    def set_sale_price(
        self, presentation_id: int, sale_price: Decimal
    ) -> PresentationRecord:
        """
        Actualiza el precio de venta de la presentación.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
            sqlite3.IntegrityError: Si el precio incumple las restricciones
                del esquema.
        """
        self._connection.execute(
            "UPDATE presentations SET sale_price = ? WHERE id = ?",
            (_decimal_to_text(sale_price), presentation_id),
        )

        return self._required(presentation_id)

    def set_closed_stock(
        self, presentation_id: int, closed_stock: int
    ) -> PresentationRecord:
        """
        Actualiza el saldo de presentaciones cerradas.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
            sqlite3.IntegrityError: Si el saldo incumple las restricciones
                del esquema.
        """
        self._connection.execute(
            "UPDATE presentations SET closed_stock = ? WHERE id = ?",
            (closed_stock, presentation_id),
        )

        return self._required(presentation_id)

    def set_factor(
        self, presentation_id: int, factor: int
    ) -> PresentationRecord:
        """
        Actualiza el factor de equivalencia de la presentación.

        Si el factor nuevo deja al producto con más de una presentación base
        (factor 1), el índice único parcial del esquema lo rechaza y su
        conflicto se traduce al error de dominio de la invariante (RF-07).

        Raises:
            RecordNotFoundError: Si la presentación no existe.
            PresentationError: Si el factor nuevo produciría más de una
                presentación base en el producto.
            sqlite3.IntegrityError: Si el factor incumple otras restricciones
                del esquema.
        """
        try:
            self._connection.execute(
                "UPDATE presentations SET factor = ? WHERE id = ?",
                (factor, presentation_id),
            )
        except sqlite3.IntegrityError as error:
            if _is_single_base_conflict(error):
                raise _single_base_presentation_error() from error
            raise

        return self._required(presentation_id)

    def _required(self, presentation_id: int) -> PresentationRecord:
        """
        Devuelve la presentación indicada y exige que exista.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
        """
        presentation = self.get(presentation_id)
        if presentation is None:
            raise RecordNotFoundError(
                f"No existe la presentación con id {presentation_id}."
            )

        return presentation


class SqliteMovementRepository:
    """
    Repositorio de movimientos sobre SQLite (RF-03).

    Registra y consulta movimientos, y actualiza el contador de consumo FIFO de
    las entradas cerradas. No expone ninguna operación para editar ni eliminar
    movimientos confirmados: los datos históricos no cambian y la corrección de
    un movimiento se registra como una compensación.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """
        Args:
            connection: Conexión abierta con el esquema del inventario.
        """
        self._connection = connection

    def add(
        self,
        *,
        product_id: int,
        presentation_id: int,
        kind: MovementKind,
        quantity: int,
        reason: str,
        occurred_at: datetime,
        cost: Decimal | None = None,
        applied_price: Decimal | None = None,
        is_initial_stock: bool = False,
        original_movement_id: int | None = None,
    ) -> MovementRecord:
        """
        Registra un movimiento confirmado y devuelve su versión persistida.

        Los contadores FIFO se inicializan en cero, según el esquema. La
        actualización del consumo de una entrada cerrada corresponde a
        `set_consumed_quantity`.

        Raises:
            sqlite3.IntegrityError: Si el movimiento incumple las restricciones
                del esquema, por ejemplo un motivo vacío.
            ValueError: Si el instante no incluye zona horaria.
        """
        cursor = self._connection.execute(
            """
            INSERT INTO movements (
                product_id, presentation_id, kind, quantity, reason,
                occurred_at, cost, applied_price, is_initial_stock,
                original_movement_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                presentation_id,
                MovementKind(kind).value,
                quantity,
                reason,
                to_utc_text(occurred_at),
                None if cost is None else _decimal_to_text(cost),
                (
                    None
                    if applied_price is None
                    else _decimal_to_text(applied_price)
                ),
                int(is_initial_stock),
                original_movement_id,
            ),
        )

        return self._required(int(cursor.lastrowid))

    def get(self, movement_id: int) -> MovementRecord | None:
        """Devuelve el movimiento indicado o `None` si no existe."""
        row = self._connection.execute(
            f"SELECT {_MOVEMENT_COLUMNS} FROM movements WHERE id = ?",
            (movement_id,),
        ).fetchone()

        return None if row is None else _movement_record(row)

    def list_by_product(
        self,
        product_id: int,
        kind: MovementKind | None = None,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> list[MovementRecord]:
        """
        Devuelve el historial del producto, en orden cronológico.

        Los filtros indicados se combinan mediante AND: `kind` restringe el
        tipo de movimiento y el período restringe el instante, con el inicio
        inclusivo y el final exclusivo. Los límites del período se interpretan
        en la hora local de Argentina y se convierten a UTC antes de consultar.

        Raises:
            InvalidPeriodError: Si el período indicado no es válido.
        """
        conditions, parameters = _period_conditions(
            kind, period_start, period_end
        )

        rows = self._connection.execute(
            f"SELECT {_MOVEMENT_COLUMNS} FROM movements WHERE product_id = ?"
            f"{conditions} ORDER BY occurred_at, id",
            [product_id, *parameters],
        ).fetchall()

        return [_movement_record(row) for row in rows]

    def list_by_presentation(
        self,
        presentation_id: int,
        kind: MovementKind | None = None,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> list[MovementRecord]:
        """
        Devuelve el historial de la presentación, en orden cronológico.

        Los filtros indicados se combinan mediante AND: `kind` restringe el
        tipo de movimiento y el período restringe el instante, con el inicio
        inclusivo y el final exclusivo. Los límites del período se interpretan
        en la hora local de Argentina y se convierten a UTC antes de consultar.

        Raises:
            InvalidPeriodError: Si el período indicado no es válido.
        """
        conditions, parameters = _period_conditions(
            kind, period_start, period_end
        )

        rows = self._connection.execute(
            f"SELECT {_MOVEMENT_COLUMNS} FROM movements "
            "WHERE presentation_id = ?"
            f"{conditions} ORDER BY occurred_at, id",
            [presentation_id, *parameters],
        ).fetchall()

        return [_movement_record(row) for row in rows]

    def set_consumed_quantity(
        self, movement_id: int, consumed_quantity: int
    ) -> MovementRecord:
        """
        Actualiza la cantidad consumida por FIFO de una entrada cerrada.

        Solo se reemplaza `consumed_quantity`: los datos históricos del
        movimiento y su contador de compensaciones se conservan intactos. La
        actualización se ejecuta con una única sentencia condicionada que exige
        que el registro exista, sea una entrada cerrada y respete el límite
        `consumida + compensada <= cantidad` que el esquema impone con `CHECK`,
        de modo que el rechazo se informe como `ValueError` y no como
        `sqlite3.IntegrityError`.

        No abre transacciones: se ejecuta dentro de la transacción del servicio
        que la invoca.

        Raises:
            RecordNotFoundError: Si el movimiento no existe.
            NotAClosedEntryError: Si el movimiento no es una entrada cerrada,
                porque solo las entradas cerradas tienen consumo FIFO.
            ValueError: Si la cantidad consumida no es un entero mayor o igual
                que cero o si viola el límite de la entrada.
        """
        validated_quantity = _consumed_quantity_value(consumed_quantity)

        cursor = self._connection.execute(
            """
            UPDATE movements
            SET consumed_quantity = ?
            WHERE id = ?
                AND kind = ?
                AND compensated_quantity + ? <= quantity
            """,
            (
                validated_quantity,
                movement_id,
                MovementKind.CLOSED_ENTRY.value,
                validated_quantity,
            ),
        )
        if cursor.rowcount == 0:
            self._reject_consumed_quantity_update(
                movement_id, validated_quantity
            )

        return self._required(movement_id)

    def set_compensated_quantity(
        self, movement_id: int, compensated_quantity: int
    ) -> MovementRecord:
        """
        Actualiza la cantidad compensada de una entrada cerrada (RF-03).

        Solo se reemplaza `compensated_quantity`: los datos históricos del
        movimiento y su contador de consumo se conservan intactos. La
        actualización se ejecuta con una única sentencia condicionada que exige
        que el registro exista, sea una entrada cerrada y respete el límite
        `consumida + compensada <= cantidad` que el esquema impone con `CHECK`,
        de modo que el rechazo se informe como `ValueError` y no como
        `sqlite3.IntegrityError`.

        No abre transacciones: se ejecuta dentro de la transacción del servicio
        que la invoca.

        Raises:
            RecordNotFoundError: Si el movimiento no existe.
            NotAClosedEntryError: Si el movimiento no es una entrada cerrada,
                porque solo las entradas cerradas tienen cantidad compensable.
            ValueError: Si la cantidad compensada no es un entero mayor o igual
                que cero o si viola el límite de la entrada.
        """
        validated_quantity = _consumed_quantity_value(compensated_quantity)

        cursor = self._connection.execute(
            """
            UPDATE movements
            SET compensated_quantity = ?
            WHERE id = ?
                AND kind = ?
                AND consumed_quantity + ? <= quantity
            """,
            (
                validated_quantity,
                movement_id,
                MovementKind.CLOSED_ENTRY.value,
                validated_quantity,
            ),
        )
        if cursor.rowcount == 0:
            self._reject_compensated_quantity_update(
                movement_id, validated_quantity
            )

        return self._required(movement_id)

    def _reject_consumed_quantity_update(
        self, movement_id: int, consumed_quantity: int
    ) -> NoReturn:
        """
        Informa el rechazo de una actualización del contador de consumo.

        Se invoca cuando la actualización condicionada no modificó ninguna
        fila, para distinguir la causa del rechazo con el estado vigente del
        movimiento.

        Raises:
            RecordNotFoundError: Si el movimiento no existe.
            NotAClosedEntryError: Si el movimiento no es una entrada cerrada.
            ValueError: Si la cantidad consumida viola el límite de la
                entrada.
        """
        movement = self.get(movement_id)
        if movement is None:
            raise RecordNotFoundError(
                f"No existe el movimiento con id {movement_id}."
            )

        if movement.kind is not MovementKind.CLOSED_ENTRY:
            raise NotAClosedEntryError(
                f"El movimiento {movement_id} no es una entrada cerrada "
                f"({movement.kind.value}): solo las entradas cerradas tienen "
                "consumo FIFO."
            )

        raise ValueError(
            "La cantidad consumida y compensada de la entrada "
            f"{movement_id} no puede superar su cantidad original "
            f"({movement.quantity}): se intentaron consumir "
            f"{consumed_quantity} con {movement.compensated_quantity} ya "
            "compensadas."
        )

    def _reject_compensated_quantity_update(
        self, movement_id: int, compensated_quantity: int
    ) -> NoReturn:
        """
        Informa el rechazo de una actualización del contador de compensaciones.

        Se invoca cuando la actualización condicionada no modificó ninguna
        fila, para distinguir la causa del rechazo con el estado vigente del
        movimiento.

        Raises:
            RecordNotFoundError: Si el movimiento no existe.
            NotAClosedEntryError: Si el movimiento no es una entrada cerrada.
            ValueError: Si la cantidad compensada viola el límite de la
                entrada.
        """
        movement = self.get(movement_id)
        if movement is None:
            raise RecordNotFoundError(
                f"No existe el movimiento con id {movement_id}."
            )

        if movement.kind is not MovementKind.CLOSED_ENTRY:
            raise NotAClosedEntryError(
                f"El movimiento {movement_id} no es una entrada cerrada "
                f"({movement.kind.value}): solo las entradas cerradas tienen "
                "cantidad compensable."
            )

        raise ValueError(
            "La cantidad consumida y compensada de la entrada "
            f"{movement_id} no puede superar su cantidad original "
            f"({movement.quantity}): se intentaron compensar "
            f"{compensated_quantity} con {movement.consumed_quantity} ya "
            "consumidas."
        )

    def _required(self, movement_id: int) -> MovementRecord:
        """
        Devuelve el movimiento indicado y exige que exista.

        Raises:
            RecordNotFoundError: Si el movimiento no existe.
        """
        movement = self.get(movement_id)
        if movement is None:
            raise RecordNotFoundError(
                f"No existe el movimiento con id {movement_id}."
            )

        return movement
