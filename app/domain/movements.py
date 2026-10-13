"""Reglas de dominio de los movimientos y las compensaciones (RF-03).

Este módulo contiene reglas puras: no accede a SQLite, a la interfaz ni a
servicios externos. Define los tipos de movimiento, las reglas de las
compensaciones (tipo opuesto, no encadenadas y acotadas por la cantidad
compensable) y el consumo FIFO de las entradas cerradas que realizan las
aperturas y las salidas cerradas.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import Enum

from app.domain.quantities import (
    validate_movement_quantity,
    validate_stock,
)
from app.domain.stock import InsufficientClosedStockError


class MovementError(ValueError):
    """Error relacionado con un movimiento o una compensación inválida."""


class NotCompensableError(MovementError):
    """Error que indica que el movimiento original no puede compensarse."""


class ApertureNotCompensableError(NotCompensableError):
    """Error que indica que se intentó compensar una apertura (RF-03)."""


class ChainedCompensationError(NotCompensableError):
    """Error que indica que se intentó compensar una compensación (RF-03)."""


class ExcessCompensationError(MovementError):
    """Error que indica que una compensación supera la cantidad compensable."""


class EntryQuantityExceededError(MovementError):
    """Error que indica que el consumo de una entrada supera su cantidad."""


class InsufficientMovementStockError(MovementError):
    """Error que indica que el movimiento dejaría stock negativo (RF-03)."""


class MovementKind(str, Enum):
    """
    Tipo de movimiento del inventario (RF-03).

    Los valores coinciden con los identificadores del tipo de movimiento que
    se persisten. Las entradas incorporan existencias al inventario y las
    salidas las retiran; las aperturas y las compensaciones se rigen por sus
    propias reglas.
    """

    CLOSED_ENTRY = "entrada_cerrada"
    LOOSE_ENTRY = "entrada_suelta"
    CLOSED_EXIT = "salida_cerrada"
    LOOSE_EXIT = "salida_suelta"
    APERTURE = "apertura"
    COMPENSATION = "compensacion"

    @property
    def is_entry(self) -> bool:
        """Indica si el movimiento incorpora existencias al inventario."""
        return self in (MovementKind.CLOSED_ENTRY, MovementKind.LOOSE_ENTRY)


# Tipos de movimiento compensables y su tipo opuesto (RF-03). Las aperturas y
# las compensaciones no tienen tipo opuesto: no pueden compensarse ni ser
# destino de una compensación.
_OPPOSITE_KINDS: dict[MovementKind, MovementKind] = {
    MovementKind.CLOSED_ENTRY: MovementKind.CLOSED_EXIT,
    MovementKind.CLOSED_EXIT: MovementKind.CLOSED_ENTRY,
    MovementKind.LOOSE_ENTRY: MovementKind.LOOSE_EXIT,
    MovementKind.LOOSE_EXIT: MovementKind.LOOSE_ENTRY,
}


def _validate_identifier(value: int, field_name: str) -> int:
    """
    Valida un identificador interno de persistencia.

    Args:
        value: Identificador de una entrada cerrada o de una presentación.
        field_name: Nombre del campo, usado en el mensaje de error.

    Returns:
        El identificador validado.

    Raises:
        MovementError:
            Si el valor no es un entero mayor que cero.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise MovementError(f"El campo '{field_name}' debe ser un entero.")

    if value <= 0:
        raise MovementError(
            f"El campo '{field_name}' debe ser mayor que cero."
        )

    return value


def validate_movement_kind(kind: MovementKind | str) -> MovementKind:
    """
    Valida y normaliza un tipo de movimiento (RF-03).

    Args:
        kind: Tipo de movimiento informado, como miembro del enumerado o como
            su identificador persistido.

    Returns:
        El tipo de movimiento validado.

    Raises:
        MovementError:
            Si el valor no corresponde a un tipo de movimiento conocido.
    """
    if isinstance(kind, MovementKind):
        return kind

    try:
        return MovementKind(kind)
    except ValueError:
        raise MovementError(
            f"Tipo de movimiento desconocido: {kind!r}."
        ) from None


def validate_compensable_kind(kind: MovementKind | str) -> MovementKind:
    """
    Valida que el movimiento original pueda compensarse (RF-03).

    Todo movimiento compensatorio se vincula a un movimiento original que no
    sea compensatorio; las aperturas no pueden ser compensadas ni ser destino
    de una compensación.

    Args:
        kind: Tipo del movimiento original que se compensa.

    Returns:
        El tipo validado.

    Raises:
        MovementError:
            Si el valor no corresponde a un tipo de movimiento conocido.
        ApertureNotCompensableError:
            Si el movimiento original es una apertura.
        ChainedCompensationError:
            Si el movimiento original es otra compensación.
    """
    validated_kind = validate_movement_kind(kind)

    if validated_kind is MovementKind.APERTURE:
        raise ApertureNotCompensableError(
            "Las aperturas no pueden compensarse ni ser destino de una "
            "compensación."
        )

    if validated_kind is MovementKind.COMPENSATION:
        raise ChainedCompensationError(
            "Una compensación no puede compensar a otra compensación."
        )

    return validated_kind


def opposite_kind(kind: MovementKind | str) -> MovementKind:
    """
    Devuelve el tipo opuesto al movimiento original (RF-03).

    Args:
        kind: Tipo del movimiento original.

    Returns:
        El tipo opuesto, que corresponde al efecto de la compensación.

    Raises:
        MovementError:
            Si el valor no corresponde a un tipo de movimiento conocido.
        ApertureNotCompensableError:
            Si el movimiento original es una apertura.
        ChainedCompensationError:
            Si el movimiento original es otra compensación.
    """
    validated_kind = validate_compensable_kind(kind)

    return _OPPOSITE_KINDS[validated_kind]


@dataclass(frozen=True)
class ClosedEntry:
    """
    Entrada cerrada con su trazabilidad FIFO (RF-03).

    Representa una entrada confirmada de presentaciones cerradas junto con la
    cantidad consumida por aperturas y salidas cerradas, y la cantidad
    compensada. La cantidad compensable se deriva como
    `cantidad − cantidad_consumida − cantidad_compensada` y nunca se persiste
    como saldo independiente. La cantidad consumida y la compensada no pueden
    superar, en conjunto, la cantidad original de la entrada.

    El orden FIFO de las entradas cerradas se determina por `entry_id`, que la
    persistencia asigna en el mismo orden en que se registran los movimientos.
    """

    entry_id: int
    presentation_id: int
    quantity: int
    consumed_quantity: int = 0
    compensated_quantity: int = 0

    def __post_init__(self) -> None:
        """Valida los identificadores, la cantidad y los contadores FIFO."""
        object.__setattr__(
            self,
            "entry_id",
            _validate_identifier(self.entry_id, "entry_id"),
        )
        object.__setattr__(
            self,
            "presentation_id",
            _validate_identifier(self.presentation_id, "presentation_id"),
        )
        object.__setattr__(
            self,
            "quantity",
            validate_movement_quantity(self.quantity),
        )
        object.__setattr__(
            self,
            "consumed_quantity",
            validate_stock(self.consumed_quantity),
        )
        object.__setattr__(
            self,
            "compensated_quantity",
            validate_stock(self.compensated_quantity),
        )

        if self.consumed_quantity + self.compensated_quantity > self.quantity:
            raise EntryQuantityExceededError(
                "La cantidad consumida y compensada de la entrada "
                f"{self.entry_id} no puede superar su cantidad original "
                f"({self.quantity})."
            )

    @property
    def compensable_quantity(self) -> int:
        """
        Cantidad compensable pendiente de la entrada cerrada (RF-03).

        Se calcula como `cantidad − cantidad_consumida − cantidad_compensada`.
        Es también la cantidad disponible de la entrada para el consumo FIFO
        que realizan las aperturas y las salidas cerradas.
        """
        return (
            self.quantity
            - self.consumed_quantity
            - self.compensated_quantity
        )


@dataclass(frozen=True)
class FifoConsumption:
    """Consumo FIFO aplicado a una entrada cerrada (RF-03)."""

    entry_id: int
    quantity: int

    def __post_init__(self) -> None:
        """Valida el identificador y la cantidad consumida."""
        object.__setattr__(
            self,
            "entry_id",
            _validate_identifier(self.entry_id, "entry_id"),
        )
        object.__setattr__(
            self,
            "quantity",
            validate_movement_quantity(self.quantity),
        )


@dataclass(frozen=True)
class FifoConsumptionResult:
    """
    Resultado del consumo FIFO de las entradas cerradas (RF-03).

    Describe la presentación consumida, la cantidad consumida, el detalle por
    entrada y las entradas de esa presentación actualizadas en orden FIFO. No
    modifica ni persiste saldos: aplicar las entradas actualizadas corresponde
    a la capa que ejecute la apertura o la salida cerrada.
    """

    presentation_id: int
    quantity: int
    consumptions: tuple[FifoConsumption, ...]
    entries: tuple[ClosedEntry, ...]

    def __post_init__(self) -> None:
        """Valida la presentación, la cantidad y el detalle del consumo."""
        object.__setattr__(
            self,
            "presentation_id",
            _validate_identifier(self.presentation_id, "presentation_id"),
        )
        object.__setattr__(
            self,
            "quantity",
            validate_movement_quantity(self.quantity),
        )
        object.__setattr__(self, "consumptions", tuple(self.consumptions))
        object.__setattr__(self, "entries", tuple(self.entries))

        consumed_quantity = sum(
            consumption.quantity for consumption in self.consumptions
        )
        if consumed_quantity != self.quantity:
            raise MovementError(
                "El consumo FIFO debe distribuir exactamente la cantidad "
                f"solicitada: se distribuyeron {consumed_quantity} de "
                f"{self.quantity}."
            )


def consume_closed_entries_fifo(
    entries: Iterable[ClosedEntry],
    *,
    presentation_id: int,
    quantity: int,
) -> FifoConsumptionResult:
    """
    Consume entradas cerradas mediante FIFO en una presentación (RF-03).

    Una apertura y una salida cerrada consumen, en orden FIFO, las
    presentaciones cerradas de la entrada cerrada más antigua con saldo
    disponible de esa presentación. El orden FIFO se determina por el
    identificador de la entrada, de la más antigua a la más reciente, y las
    entradas de otras presentaciones no se consumen. La operación es pura:
    devuelve las entradas actualizadas sin persistirlas.

    Args:
        entries: Entradas cerradas vigentes del producto.
        presentation_id: Presentación cuyas entradas cerradas se consumen.
        quantity: Cantidad de presentaciones cerradas que se consume.

    Returns:
        El resultado del consumo con las entradas de la presentación
        actualizadas y el detalle del consumo por entrada.

    Raises:
        MovementError:
            Si la presentación no tiene un identificador válido.
        QuantityError:
            Si la cantidad no es un entero mayor que cero.
        InsufficientClosedStockError:
            Si la cantidad compensable disponible de la presentación es menor
            que la cantidad solicitada.
    """
    validated_presentation_id = _validate_identifier(
        presentation_id, "presentation_id"
    )
    validated_quantity = validate_movement_quantity(quantity)

    presentation_entries = sorted(
        (
            entry
            for entry in entries
            if entry.presentation_id == validated_presentation_id
        ),
        key=lambda entry: entry.entry_id,
    )
    available_quantity = sum(
        entry.compensable_quantity for entry in presentation_entries
    )
    if validated_quantity > available_quantity:
        raise InsufficientClosedStockError(
            "No hay stock cerrado suficiente para consumir: se solicitaron "
            f"{validated_quantity} presentaciones y hay {available_quantity} "
            "disponibles."
        )

    remaining_quantity = validated_quantity
    consumptions: list[FifoConsumption] = []
    updated_entries: list[ClosedEntry] = []
    for entry in presentation_entries:
        consumed_quantity = min(entry.compensable_quantity, remaining_quantity)
        if consumed_quantity > 0:
            consumptions.append(
                FifoConsumption(
                    entry_id=entry.entry_id,
                    quantity=consumed_quantity,
                )
            )
            remaining_quantity -= consumed_quantity
        updated_entries.append(
            replace(
                entry,
                consumed_quantity=entry.consumed_quantity + consumed_quantity,
            )
        )

    return FifoConsumptionResult(
        presentation_id=validated_presentation_id,
        quantity=validated_quantity,
        consumptions=tuple(consumptions),
        entries=tuple(updated_entries),
    )


@dataclass(frozen=True)
class CompensationResult:
    """
    Resultado de una compensación válida (RF-03).

    El movimiento compensatorio es siempre de tipo `COMPENSATION` y su efecto
    es del tipo opuesto al movimiento original. La compensación de una entrada
    reduce existencias y la de una salida las repone; en ambos casos el saldo
    resultante del tipo afectado nunca es negativo.
    """

    original_kind: MovementKind
    quantity: int
    stock_before: int
    stock_after: int

    def __post_init__(self) -> None:
        """Valida el tipo original, la cantidad y el saldo resultante."""
        validated_kind = validate_compensable_kind(self.original_kind)
        object.__setattr__(self, "original_kind", validated_kind)
        object.__setattr__(
            self,
            "quantity",
            validate_movement_quantity(self.quantity),
        )
        object.__setattr__(
            self,
            "stock_before",
            validate_stock(self.stock_before),
        )
        object.__setattr__(
            self,
            "stock_after",
            validate_stock(self.stock_after),
        )

        expected_stock_after = (
            self.stock_before - self.quantity
            if validated_kind.is_entry
            else self.stock_before + self.quantity
        )
        if self.stock_after != expected_stock_after:
            raise MovementError(
                "El saldo resultante de la compensación no es coherente con "
                f"la cantidad compensada ({self.quantity})."
            )

    @property
    def kind(self) -> MovementKind:
        """Tipo del movimiento compensatorio creado."""
        return MovementKind.COMPENSATION

    @property
    def effect_kind(self) -> MovementKind:
        """Tipo de efecto de la compensación, opuesto al original."""
        return opposite_kind(self.original_kind)


def compensate(
    *,
    original_kind: MovementKind | str,
    quantity: int,
    compensable_quantity: int,
    available_stock: int,
) -> CompensationResult:
    """
    Valida una compensación de un movimiento original (RF-03).

    La compensación es de tipo opuesto al movimiento original y no puede
    encadenarse: el movimiento original no puede ser una apertura ni otra
    compensación. La cantidad compensada puede ser parcial o total, no puede
    superar la cantidad compensable pendiente del movimiento original y, en el
    caso de una entrada, tampoco puede dejar el stock afectado en negativo. La
    operación es pura: devuelve el movimiento compensatorio sin persistirlo ni
    modificar el movimiento original.

    Args:
        original_kind: Tipo del movimiento original que se compensa.
        quantity: Cantidad que se neutraliza del movimiento original.
        compensable_quantity: Cantidad compensable pendiente del movimiento
            original; para una entrada cerrada es su cantidad compensable FIFO
            y para una entrada suelta, la cantidad pendiente de compensar.
        available_stock: Stock vigente del tipo afectado, es decir, el stock
            cerrado de la presentación o las unidades sueltas del producto.
            Solo limita la compensación de una entrada, porque compensarla
            reduce existencias.

    Returns:
        El resultado de la compensación, con el saldo resultante.

    Raises:
        MovementError:
            Si el valor no corresponde a un tipo de movimiento conocido.
        ApertureNotCompensableError:
            Si el movimiento original es una apertura.
        ChainedCompensationError:
            Si el movimiento original es otra compensación.
        QuantityError:
            Si la cantidad no es un entero mayor que cero o si la cantidad
            compensable o el stock no son enteros mayores o iguales que cero.
        ExcessCompensationError:
            Si la cantidad supera la cantidad compensable pendiente.
        InsufficientMovementStockError:
            Si la compensación de una entrada dejaría stock negativo.
    """
    validated_kind = validate_compensable_kind(original_kind)
    validated_quantity = validate_movement_quantity(quantity)
    validated_compensable = validate_stock(compensable_quantity)
    validated_stock = validate_stock(available_stock)

    if validated_quantity > validated_compensable:
        raise ExcessCompensationError(
            f"La compensación de {validated_quantity} supera la cantidad "
            f"compensable pendiente ({validated_compensable})."
        )

    if validated_kind.is_entry and validated_quantity > validated_stock:
        raise InsufficientMovementStockError(
            "La compensación dejaría stock negativo: se intentaron compensar "
            f"{validated_quantity} con {validated_stock} disponibles."
        )

    stock_after = (
        validated_stock - validated_quantity
        if validated_kind.is_entry
        else validated_stock + validated_quantity
    )

    return CompensationResult(
        original_kind=validated_kind,
        quantity=validated_quantity,
        stock_before=validated_stock,
        stock_after=stock_after,
    )
