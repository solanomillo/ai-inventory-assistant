"""Reglas de dominio del stock compuesto y las aperturas (RF-02, RF-03).

Este módulo contiene reglas puras: no accede a SQLite, a la interfaz ni a
servicios externos. Los saldos persistidos son las unidades sueltas del
producto y el stock cerrado de cada presentación; el total equivalente se
deriva y nunca se persiste como saldo independiente.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.quantities import (
    validate_factor,
    validate_movement_quantity,
    validate_stock,
)


class StockError(ValueError):
    """Error relacionado con un saldo de stock inválido."""


class InsufficientClosedStockError(StockError):
    """Error que indica que no hay stock cerrado suficiente para abrir."""


class BasePresentationApertureError(StockError):
    """Error que indica que se intentó abrir la presentación base (factor 1)."""


def validate_loose_units(units: int) -> int:
    """
    Valida las unidades sueltas de un producto (RF-02).

    Args:
        units: Unidades sueltas expresadas en unidades base enteras.

    Returns:
        El valor validado.

    Raises:
        QuantityError:
            Si el valor no es un entero o es menor que cero.
    """
    return validate_stock(units)


def validate_closed_stock(quantity: int) -> int:
    """
    Valida el stock cerrado de una presentación (RF-02).

    Args:
        quantity: Presentaciones cerradas expresadas en unidades enteras.

    Returns:
        El valor validado.

    Raises:
        QuantityError:
            Si el valor no es un entero o es menor que cero.
    """
    return validate_stock(quantity)


def validate_aperture_factor(factor: int) -> int:
    """
    Valida el factor de la presentación que se abre (RF-03).

    Una apertura solo es válida sobre una presentación cuyo factor sea mayor
    que uno; la presentación base (factor 1) no puede abrirse. Esta validación
    es la regla de dominio reutilizable por las capas que ejecutan aperturas.

    Args:
        factor: Factor de equivalencia de la presentación, en unidades base.

    Returns:
        El factor validado, siempre mayor que uno.

    Raises:
        QuantityError:
            Si el factor no es un entero mayor o igual que uno.
        BasePresentationApertureError:
            Si el factor es 1 (presentación base).
    """
    validated_factor = validate_factor(factor)

    if validated_factor == 1:
        raise BasePresentationApertureError(
            "La presentación base (factor 1) no puede abrirse."
        )

    return validated_factor


@dataclass(frozen=True)
class ClosedStock:
    """
    Saldo de stock cerrado de una presentación (RF-02).

    Representa la cantidad de presentaciones cerradas junto con el factor de
    equivalencia de su presentación, de modo que pueda derivarse su aporte al
    total equivalente. No es un saldo persistido independiente: es el dato de
    entrada para calcular el total equivalente.

    `factor` es un entero mayor o igual que uno y `quantity` es un entero
    mayor o igual que cero; ambos se validan al construir el objeto.
    """

    factor: int
    quantity: int = 0

    def __post_init__(self) -> None:
        """Valida el factor y la cantidad de presentaciones cerradas."""
        object.__setattr__(self, "factor", validate_factor(self.factor))
        object.__setattr__(
            self,
            "quantity",
            validate_closed_stock(self.quantity),
        )

    @property
    def equivalent_units(self) -> int:
        """Aporte del saldo cerrado al total equivalente, en unidades base."""
        return self.quantity * self.factor


def total_equivalent(
    loose_units: int,
    closed_stocks: Iterable[ClosedStock] = (),
) -> int:
    """
    Calcula el total equivalente de un producto (RF-02).

    El total equivalente se deriva como
    `unidades_sueltas + Σ(stock_cerrado × factor)` y no se persiste como saldo
    independiente; cada llamada lo recalcula a partir de sus componentes.

    Args:
        loose_units: Unidades sueltas del producto, en unidades base.
        closed_stocks: Saldos cerrados por presentación que aportan al total.

    Returns:
        El total equivalente del producto, en unidades base.

    Raises:
        QuantityError:
            Si las unidades sueltas no son un entero mayor o igual que cero.
            Los saldos cerrados se validan al construir cada `ClosedStock`.
    """
    validated_loose_units = validate_loose_units(loose_units)

    return validated_loose_units + sum(
        closed_stock.equivalent_units for closed_stock in closed_stocks
    )


@dataclass(frozen=True)
class ApertureResult:
    """
    Resultado de una apertura de presentaciones cerradas (RF-02, RF-03).

    Describe la operación completa: cuántas presentaciones se abrieron, con qué
    factor, y los saldos resultantes del producto. No modifica ni persiste
    saldos: la aplicación de los nuevos saldos corresponde a la capa que
    ejecute la operación.
    """

    quantity: int
    factor: int
    loose_units: int
    closed_stock: int

    def __post_init__(self) -> None:
        """Valida las cantidades y los saldos resultantes."""
        object.__setattr__(
            self,
            "quantity",
            validate_movement_quantity(self.quantity),
        )
        object.__setattr__(
            self,
            "factor",
            validate_aperture_factor(self.factor),
        )
        object.__setattr__(
            self,
            "loose_units",
            validate_loose_units(self.loose_units),
        )
        object.__setattr__(
            self,
            "closed_stock",
            validate_closed_stock(self.closed_stock),
        )

    @property
    def opened_units(self) -> int:
        """Unidades base que la apertura incorpora al stock suelto."""
        return self.quantity * self.factor


def open_presentations(
    *,
    loose_units: int,
    closed_stock: int,
    quantity: int,
    factor: int,
) -> ApertureResult:
    """
    Abre presentaciones cerradas y las convierte en unidades sueltas (RF-02,
    RF-03).

    La apertura resta la cantidad abierta al stock cerrado de la presentación y
    suma `quantity × factor` a las unidades sueltas del producto, de modo que
    el total equivalente no cambia. La operación es pura: devuelve los saldos
    resultantes sin persistirlos ni alterar los valores de entrada.

    La apertura de la presentación base (factor 1) se rechaza: solo son válidas
    las presentaciones cuyo factor sea mayor que uno (RF-03).

    Args:
        loose_units: Unidades sueltas vigentes del producto.
        closed_stock: Stock cerrado vigente de la presentación que se abre.
        quantity: Cantidad de presentaciones cerradas que se abre (`N`).
        factor: Factor de equivalencia de la presentación, en unidades base.

    Returns:
        El resultado de la apertura con los saldos actualizados.

    Raises:
        QuantityError:
            Si `quantity` no es un entero mayor que cero, si `factor` no es un
            entero mayor o igual que uno, o si `loose_units` o `closed_stock`
            no son enteros mayores o iguales que cero.
        BasePresentationApertureError:
            Si `factor` es 1 (presentación base), que no puede abrirse.
        InsufficientClosedStockError:
            Si la cantidad solicitada supera el stock cerrado vigente.
    """
    validated_quantity = validate_movement_quantity(quantity)
    validated_factor = validate_aperture_factor(factor)
    validated_loose_units = validate_loose_units(loose_units)
    validated_closed_stock = validate_closed_stock(closed_stock)

    if validated_quantity > validated_closed_stock:
        raise InsufficientClosedStockError(
            "No hay stock cerrado suficiente para la apertura: se solicitaron "
            f"{validated_quantity} presentaciones y hay "
            f"{validated_closed_stock} disponibles."
        )

    return ApertureResult(
        quantity=validated_quantity,
        factor=validated_factor,
        loose_units=validated_loose_units
        + validated_quantity * validated_factor,
        closed_stock=validated_closed_stock - validated_quantity,
    )
