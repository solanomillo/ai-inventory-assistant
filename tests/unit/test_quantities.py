"""Pruebas para las validaciones numéricas de cantidades, factor, precio y costo."""

from decimal import Decimal

import pytest

from app.domain.quantities import (
    QuantityError,
    validate_cost,
    validate_factor,
    validate_movement_quantity,
    validate_price,
    validate_stock,
)


# --- validate_stock ---


def test_validate_stock_accepts_zero() -> None:
    """Verifica que el stock mínimo/actual puede ser cero."""
    assert validate_stock(0) == 0


def test_validate_stock_accepts_positive() -> None:
    """Verifica que una cantidad de stock positiva se acepta."""
    assert validate_stock(5) == 5


def test_validate_stock_rejects_negative() -> None:
    """Verifica el rechazo de un stock negativo."""
    with pytest.raises(QuantityError):
        validate_stock(-1)


def test_validate_stock_rejects_float() -> None:
    """Verifica el rechazo de una cantidad fraccionaria."""
    with pytest.raises(QuantityError):
        validate_stock(2.5)


def test_validate_stock_rejects_bool() -> None:
    """Verifica el rechazo de un booleano cuando se espera un entero."""
    with pytest.raises(QuantityError):
        validate_stock(True)


def test_validate_stock_rejects_non_numeric() -> None:
    """Verifica el rechazo de un valor que no es entero."""
    with pytest.raises(QuantityError):
        validate_stock("5")


# --- validate_movement_quantity ---


def test_validate_movement_quantity_accepts_positive() -> None:
    """Verifica que una cantidad de movimiento positiva se acepta."""
    assert validate_movement_quantity(1) == 1
    assert validate_movement_quantity(10) == 10


def test_validate_movement_quantity_rejects_zero() -> None:
    """Verifica el rechazo de una cantidad igual a cero."""
    with pytest.raises(QuantityError):
        validate_movement_quantity(0)


def test_validate_movement_quantity_rejects_negative() -> None:
    """Verifica el rechazo de una cantidad negativa."""
    with pytest.raises(QuantityError):
        validate_movement_quantity(-3)


def test_validate_movement_quantity_rejects_fraction() -> None:
    """Verifica el rechazo de una cantidad fraccionaria."""
    with pytest.raises(QuantityError):
        validate_movement_quantity(1.5)


def test_validate_movement_quantity_rejects_bool() -> None:
    """Verifica el rechazo de un booleano."""
    with pytest.raises(QuantityError):
        validate_movement_quantity(False)


# --- validate_factor ---


def test_validate_factor_accepts_one_and_above() -> None:
    """Verifica que el factor 1 y valores mayores se aceptan."""
    assert validate_factor(1) == 1
    assert validate_factor(6) == 6
    assert validate_factor(24) == 24


def test_validate_factor_rejects_zero() -> None:
    """Verifica el rechazo del factor cero."""
    with pytest.raises(QuantityError):
        validate_factor(0)


def test_validate_factor_rejects_negative() -> None:
    """Verifica el rechazo de un factor negativo."""
    with pytest.raises(QuantityError):
        validate_factor(-1)


def test_validate_factor_rejects_fraction() -> None:
    """Verifica el rechazo de un factor fraccionario."""
    with pytest.raises(QuantityError):
        validate_factor(1.5)


def test_validate_factor_rejects_bool() -> None:
    """Verifica el rechazo de un booleano."""
    with pytest.raises(QuantityError):
        validate_factor(True)


# --- validate_price ---


def test_validate_price_accepts_positive() -> None:
    """Verifica que un precio positivo se acepta."""
    assert validate_price(Decimal("10.5")) == Decimal("10.5")


def test_validate_price_accepts_many_decimals() -> None:
    """Verifica que no se impone una precisión máxima al precio."""
    assert validate_price(Decimal("10.123456")) == Decimal("10.123456")


def test_validate_price_rejects_zero() -> None:
    """Verifica el rechazo de un precio igual a cero."""
    with pytest.raises(QuantityError):
        validate_price(Decimal("0"))


def test_validate_price_rejects_negative() -> None:
    """Verifica el rechazo de un precio negativo."""
    with pytest.raises(QuantityError):
        validate_price(Decimal("-1"))


def test_validate_price_rejects_float() -> None:
    """Verifica el rechazo de un precio en punto flotante."""
    with pytest.raises(QuantityError):
        validate_price(10.5)


def test_validate_price_rejects_non_finite() -> None:
    """Verifica el rechazo de un precio no finito."""
    with pytest.raises(QuantityError):
        validate_price(Decimal("Infinity"))


# --- validate_cost ---


def test_validate_cost_accepts_zero() -> None:
    """Verifica que un costo cero se acepta."""
    assert validate_cost(Decimal("0")) == Decimal("0")


def test_validate_cost_accepts_positive() -> None:
    """Verifica que un costo positivo se acepta."""
    assert validate_cost(Decimal("10.5")) == Decimal("10.5")


def test_validate_cost_rejects_negative() -> None:
    """Verifica el rechazo de un costo negativo."""
    with pytest.raises(QuantityError):
        validate_cost(Decimal("-1"))


def test_validate_cost_rejects_float() -> None:
    """Verifica el rechazo de un costo en punto flotante."""
    with pytest.raises(QuantityError):
        validate_cost(10.5)


def test_validate_cost_rejects_non_finite() -> None:
    """Verifica el rechazo de un costo no finito."""
    with pytest.raises(QuantityError):
        validate_cost(Decimal("NaN"))
