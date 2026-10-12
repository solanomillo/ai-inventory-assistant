"""Validaciones numéricas de cantidades, factor, precio y costo."""

from decimal import Decimal


class QuantityError(ValueError):
    """Error relacionado con un valor numérico inválido."""


def validate_stock(value: int) -> int:
    """
    Valida una cantidad de stock (sueltas, cerradas o stock mínimo).

    Args:
        value: Cantidad de stock expresada en unidades enteras.

    Returns:
        El valor validado.

    Raises:
        QuantityError:
            Si el valor no es un entero o es menor que cero.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise QuantityError("La cantidad de stock debe ser un entero.")

    if value < 0:
        raise QuantityError("La cantidad de stock no puede ser negativa.")

    return value


def validate_movement_quantity(value: int) -> int:
    """
    Valida una cantidad de movimiento.

    Args:
        value: Cantidad de movimiento expresada en unidades enteras.

    Returns:
        El valor validado.

    Raises:
        QuantityError:
            Si el valor no es un entero o no es mayor que cero.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise QuantityError("La cantidad de movimiento debe ser un entero.")

    if value <= 0:
        raise QuantityError(
            "La cantidad de movimiento debe ser mayor que cero."
        )

    return value


def validate_factor(value: int) -> int:
    """
    Valida el factor de equivalencia de una presentación.

    Args:
        value: Factor de conversión a unidades base.

    Returns:
        El valor validado.

    Raises:
        QuantityError:
            Si el valor no es un entero o es menor que uno.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise QuantityError("El factor debe ser un entero.")

    if value < 1:
        raise QuantityError("El factor debe ser mayor o igual que uno.")

    return value


def validate_price(value: Decimal) -> Decimal:
    """
    Valida un precio de venta.

    No impone una precisión decimal máxima.

    Args:
        value: Precio de venta como Decimal.

    Returns:
        El valor validado.

    Raises:
        QuantityError:
            Si el valor no es un Decimal finito o no es mayor que cero.
    """
    if not isinstance(value, Decimal):
        raise QuantityError("El precio debe ser un valor Decimal.")

    if not value.is_finite():
        raise QuantityError("El precio debe ser un valor finito.")

    if value <= 0:
        raise QuantityError("El precio debe ser mayor que cero.")

    return value


def validate_cost(value: Decimal) -> Decimal:
    """
    Valida un costo de ingreso.

    El costo es opcional; cuando se informa debe ser un Decimal finito
    mayor o igual que cero.

    Args:
        value: Costo de ingreso como Decimal.

    Returns:
        El valor validado.

    Raises:
        QuantityError:
            Si el valor no es un Decimal finito o es menor que cero.
    """
    if not isinstance(value, Decimal):
        raise QuantityError("El costo debe ser un valor Decimal.")

    if not value.is_finite():
        raise QuantityError("El costo debe ser un valor finito.")

    if value < 0:
        raise QuantityError("El costo no puede ser negativo.")

    return value
