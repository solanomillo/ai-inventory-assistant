"""Pruebas de las reglas de stock compuesto y aperturas (RF-02, RF-03)."""

from dataclasses import FrozenInstanceError

import pytest

from app.domain.quantities import QuantityError
from app.domain.stock import (
    ApertureResult,
    BasePresentationApertureError,
    ClosedStock,
    InsufficientClosedStockError,
    StockError,
    open_presentations,
    total_equivalent,
    validate_closed_stock,
    validate_loose_units,
)


# --- validate_loose_units ---


def test_validate_loose_units_accepts_zero() -> None:
    """Verifica que las unidades sueltas pueden ser cero."""
    assert validate_loose_units(0) == 0


def test_validate_loose_units_accepts_positive() -> None:
    """Verifica que una cantidad positiva de unidades sueltas se acepta."""
    assert validate_loose_units(7) == 7


def test_validate_loose_units_rejects_negative() -> None:
    """Verifica el rechazo de unidades sueltas negativas."""
    with pytest.raises(QuantityError):
        validate_loose_units(-1)


def test_validate_loose_units_rejects_fraction() -> None:
    """Verifica el rechazo de unidades sueltas fraccionarias."""
    with pytest.raises(QuantityError):
        validate_loose_units(1.5)


def test_validate_loose_units_rejects_bool() -> None:
    """Verifica el rechazo de un booleano como unidades sueltas."""
    with pytest.raises(QuantityError):
        validate_loose_units(True)


def test_validate_loose_units_rejects_non_numeric() -> None:
    """Verifica el rechazo de un valor que no es entero."""
    with pytest.raises(QuantityError):
        validate_loose_units("3")


# --- validate_closed_stock ---


def test_validate_closed_stock_accepts_zero() -> None:
    """Verifica que el stock cerrado puede ser cero."""
    assert validate_closed_stock(0) == 0


def test_validate_closed_stock_accepts_positive() -> None:
    """Verifica que un stock cerrado positivo se acepta."""
    assert validate_closed_stock(4) == 4


def test_validate_closed_stock_rejects_negative() -> None:
    """Verifica el rechazo de un stock cerrado negativo."""
    with pytest.raises(QuantityError):
        validate_closed_stock(-2)


def test_validate_closed_stock_rejects_fraction() -> None:
    """Verifica el rechazo de un stock cerrado fraccionario."""
    with pytest.raises(QuantityError):
        validate_closed_stock(2.0)


def test_validate_closed_stock_rejects_bool() -> None:
    """Verifica el rechazo de un booleano como stock cerrado."""
    with pytest.raises(QuantityError):
        validate_closed_stock(False)


def test_validate_closed_stock_rejects_non_numeric() -> None:
    """Verifica el rechazo de un valor que no es entero."""
    with pytest.raises(QuantityError):
        validate_closed_stock(None)


# --- ClosedStock ---


def test_closed_stock_defaults_to_zero_quantity() -> None:
    """Verifica que un saldo cerrado sin cantidad nace en cero."""
    closed_stock = ClosedStock(factor=6)

    assert closed_stock.quantity == 0
    assert closed_stock.equivalent_units == 0


def test_closed_stock_computes_equivalent_units() -> None:
    """Verifica la equivalencia en unidades base de un saldo cerrado."""
    closed_stock = ClosedStock(factor=6, quantity=3)

    assert closed_stock.equivalent_units == 18


def test_closed_stock_accepts_factor_one() -> None:
    """Verifica que el factor de la presentación base se acepta."""
    closed_stock = ClosedStock(factor=1, quantity=5)

    assert closed_stock.equivalent_units == 5


def test_closed_stock_rejects_factor_zero() -> None:
    """Verifica el rechazo del factor cero."""
    with pytest.raises(QuantityError):
        ClosedStock(factor=0, quantity=1)


def test_closed_stock_rejects_negative_factor() -> None:
    """Verifica el rechazo de un factor negativo."""
    with pytest.raises(QuantityError):
        ClosedStock(factor=-6, quantity=1)


def test_closed_stock_rejects_fractional_factor() -> None:
    """Verifica el rechazo de un factor fraccionario."""
    with pytest.raises(QuantityError):
        ClosedStock(factor=1.5, quantity=1)


def test_closed_stock_rejects_negative_quantity() -> None:
    """Verifica el rechazo de un saldo cerrado negativo."""
    with pytest.raises(QuantityError):
        ClosedStock(factor=6, quantity=-1)


def test_closed_stock_rejects_bool_quantity() -> None:
    """Verifica el rechazo de un booleano como saldo cerrado."""
    with pytest.raises(QuantityError):
        ClosedStock(factor=6, quantity=True)


# --- total_equivalent ---


def test_total_equivalent_without_stock_is_zero() -> None:
    """Verifica que un producto sin stock tiene total equivalente cero."""
    assert total_equivalent(0) == 0


def test_total_equivalent_accepts_empty_closed_stocks() -> None:
    """Verifica que una colección vacía de saldos cerrados no aporta."""
    assert total_equivalent(3, []) == 3


def test_total_equivalent_with_only_loose_units() -> None:
    """Verifica el total cuando solo hay unidades sueltas."""
    assert total_equivalent(7) == 7


def test_total_equivalent_with_only_one_closed_presentation() -> None:
    """Verifica el total cuando solo hay una presentación cerrada."""
    assert total_equivalent(0, [ClosedStock(factor=6, quantity=3)]) == 18


def test_total_equivalent_sums_closed_presentations() -> None:
    """Verifica que los saldos cerrados se suman entre presentaciones."""
    closed_stocks = [
        ClosedStock(factor=6, quantity=3),
        ClosedStock(factor=24, quantity=2),
    ]

    assert total_equivalent(0, closed_stocks) == 66


def test_total_equivalent_combines_loose_and_closed_stock() -> None:
    """Verifica el cálculo de `suelto + Σ(cerrado × factor)`."""
    closed_stocks = [
        ClosedStock(factor=6, quantity=2),
        ClosedStock(factor=1, quantity=4),
    ]

    assert total_equivalent(5, closed_stocks) == 21


def test_total_equivalent_sums_presentations_with_equal_factor() -> None:
    """Verifica que dos presentaciones con el mismo factor aportan ambas."""
    closed_stocks = [
        ClosedStock(factor=6, quantity=1),
        ClosedStock(factor=6, quantity=2),
    ]

    assert total_equivalent(0, closed_stocks) == 18


def test_total_equivalent_accepts_large_values() -> None:
    """Verifica el cálculo con cantidades grandes."""
    closed_stocks = [ClosedStock(factor=24, quantity=100_000)]

    assert total_equivalent(999, closed_stocks) == 2_400_999


def test_total_equivalent_rejects_negative_loose_units() -> None:
    """Verifica el rechazo de unidades sueltas negativas."""
    with pytest.raises(QuantityError):
        total_equivalent(-1, [ClosedStock(factor=6, quantity=1)])


def test_total_equivalent_rejects_non_integer_loose_units() -> None:
    """Verifica el rechazo de unidades sueltas no enteras."""
    with pytest.raises(QuantityError):
        total_equivalent(1.0)  # type: ignore[arg-type]


def test_total_equivalent_is_derived_from_components() -> None:
    """Verifica que el total se deriva de los componentes informados."""
    closed_stocks = [ClosedStock(factor=6, quantity=1)]

    assert total_equivalent(0, closed_stocks) == 6

    closed_stocks.append(ClosedStock(factor=24, quantity=1))

    assert total_equivalent(0, closed_stocks) == 30


def test_total_equivalent_is_deterministic() -> None:
    """Verifica que dos llamadas con los mismos datos dan el mismo total."""
    closed_stocks = [ClosedStock(factor=6, quantity=2)]

    assert total_equivalent(4, closed_stocks) == total_equivalent(
        4, closed_stocks
    )


# --- open_presentations ---


def test_open_presentations_updates_both_balances() -> None:
    """Verifica que la apertura resta al cerrado y suma al suelto."""
    result = open_presentations(
        loose_units=2,
        closed_stock=5,
        quantity=3,
        factor=6,
    )

    assert result.loose_units == 20
    assert result.closed_stock == 2
    assert result.quantity == 3
    assert result.factor == 6
    assert result.opened_units == 18


def test_open_presentations_accepts_single_presentation() -> None:
    """Verifica la apertura de una sola presentación."""
    result = open_presentations(
        loose_units=0,
        closed_stock=1,
        quantity=1,
        factor=24,
    )

    assert result.opened_units == 24
    assert result.loose_units == 24
    assert result.closed_stock == 0


def test_open_presentations_can_empty_the_closed_stock() -> None:
    """Verifica que se puede abrir todo el stock cerrado disponible."""
    result = open_presentations(
        loose_units=10,
        closed_stock=4,
        quantity=4,
        factor=6,
    )

    assert result.closed_stock == 0
    assert result.loose_units == 34


def test_open_presentations_rejects_base_presentation() -> None:
    """Verifica que la presentación base (factor 1) no puede abrirse.

    La verificación es significativa porque no se apoya en recalcular el mismo
    total ni en la inmutabilidad de variables locales ``int``, que por sí sola
    no demuestra que el estado no cambió. En su lugar, comprueba la excepción
    de dominio concreta y que el rechazo no deja estado observable alterado:
    una apertura válida posterior sobre los mismos saldos de entrada debe
    seguir produciendo el resultado esperado. Si el rechazo mutara estado
    compartido (por ejemplo, contadores internos del módulo), el resultado de
    esa llamada válida diferiría.
    """
    with pytest.raises(BasePresentationApertureError):
        open_presentations(
            loose_units=4,
            closed_stock=3,
            quantity=2,
            factor=1,
        )

    result = open_presentations(
        loose_units=4,
        closed_stock=3,
        quantity=2,
        factor=3,
    )

    assert result.loose_units == 10
    assert result.closed_stock == 1
    assert result.quantity == 2
    assert result.factor == 3
    assert result.opened_units == 6


def test_open_presentations_preserves_total_equivalent() -> None:
    """Verifica que una apertura válida no modifica el total equivalente."""
    other_presentation = ClosedStock(factor=24, quantity=1)
    closed_stock = ClosedStock(factor=6, quantity=5)
    before = total_equivalent(4, [closed_stock, other_presentation])

    result = open_presentations(
        loose_units=4,
        closed_stock=closed_stock.quantity,
        quantity=3,
        factor=closed_stock.factor,
    )

    opened_presentation = ClosedStock(
        factor=closed_stock.factor,
        quantity=result.closed_stock,
    )
    after = total_equivalent(
        result.loose_units,
        [opened_presentation, other_presentation],
    )

    assert after == before


def test_open_presentations_never_leaves_negative_balances() -> None:
    """Verifica que ningún saldo queda negativo tras una apertura válida."""
    for quantity in range(1, 6):
        result = open_presentations(
            loose_units=0,
            closed_stock=5,
            quantity=quantity,
            factor=6,
        )

        assert result.closed_stock >= 0
        assert result.loose_units >= 0
        assert result.closed_stock == 5 - quantity
        assert result.loose_units == quantity * 6


def test_open_presentations_result_is_immutable() -> None:
    """Verifica que el resultado de la apertura no se puede modificar."""
    result = open_presentations(
        loose_units=0,
        closed_stock=2,
        quantity=1,
        factor=6,
    )

    with pytest.raises(FrozenInstanceError):
        result.loose_units = 99  # type: ignore[misc]


def test_open_presentations_is_deterministic() -> None:
    """Verifica que la apertura es reproducible con los mismos datos."""
    first = open_presentations(
        loose_units=1,
        closed_stock=3,
        quantity=2,
        factor=12,
    )
    second = open_presentations(
        loose_units=1,
        closed_stock=3,
        quantity=2,
        factor=12,
    )

    assert first == second


def test_open_presentations_rejects_quantity_zero() -> None:
    """Verifica el rechazo de una apertura sin presentaciones."""
    with pytest.raises(QuantityError):
        open_presentations(
            loose_units=0,
            closed_stock=3,
            quantity=0,
            factor=6,
        )


def test_open_presentations_rejects_negative_quantity() -> None:
    """Verifica el rechazo de una cantidad de apertura negativa."""
    with pytest.raises(QuantityError):
        open_presentations(
            loose_units=0,
            closed_stock=3,
            quantity=-1,
            factor=6,
        )


def test_open_presentations_rejects_fractional_quantity() -> None:
    """Verifica el rechazo de una cantidad de apertura no entera."""
    with pytest.raises(QuantityError):
        open_presentations(
            loose_units=0,
            closed_stock=3,
            quantity=1.5,
            factor=6,
        )


def test_open_presentations_rejects_bool_quantity() -> None:
    """Verifica el rechazo de un booleano como cantidad de apertura."""
    with pytest.raises(QuantityError):
        open_presentations(
            loose_units=0,
            closed_stock=3,
            quantity=True,
            factor=6,
        )


def test_open_presentations_rejects_factor_zero() -> None:
    """Verifica el rechazo de un factor igual a cero."""
    with pytest.raises(QuantityError):
        open_presentations(
            loose_units=0,
            closed_stock=3,
            quantity=1,
            factor=0,
        )


def test_open_presentations_rejects_negative_factor() -> None:
    """Verifica el rechazo de un factor negativo."""
    with pytest.raises(QuantityError):
        open_presentations(
            loose_units=0,
            closed_stock=3,
            quantity=1,
            factor=-6,
        )


def test_open_presentations_rejects_fractional_factor() -> None:
    """Verifica el rechazo de un factor no entero."""
    with pytest.raises(QuantityError):
        open_presentations(
            loose_units=0,
            closed_stock=3,
            quantity=1,
            factor=1.5,
        )


def test_open_presentations_rejects_bool_factor() -> None:
    """Verifica el rechazo de un booleano como factor."""
    with pytest.raises(QuantityError):
        open_presentations(
            loose_units=0,
            closed_stock=3,
            quantity=1,
            factor=True,
        )


def test_open_presentations_rejects_negative_loose_units() -> None:
    """Verifica el rechazo de unidades sueltas negativas."""
    with pytest.raises(QuantityError):
        open_presentations(
            loose_units=-1,
            closed_stock=3,
            quantity=1,
            factor=6,
        )


def test_open_presentations_rejects_negative_closed_stock() -> None:
    """Verifica el rechazo de un stock cerrado negativo."""
    with pytest.raises(QuantityError):
        open_presentations(
            loose_units=0,
            closed_stock=-1,
            quantity=1,
            factor=6,
        )


def test_open_presentations_rejects_insufficient_closed_stock() -> None:
    """Verifica el rechazo de una apertura sin stock cerrado suficiente."""
    with pytest.raises(InsufficientClosedStockError):
        open_presentations(
            loose_units=0,
            closed_stock=2,
            quantity=3,
            factor=6,
        )


def test_open_presentations_rejects_empty_closed_stock() -> None:
    """Verifica el rechazo de una apertura sin stock cerrado."""
    with pytest.raises(InsufficientClosedStockError):
        open_presentations(
            loose_units=10,
            closed_stock=0,
            quantity=1,
            factor=6,
        )


def test_insufficient_closed_stock_message_reports_availability() -> None:
    """Verifica que el error informa la cantidad disponible."""
    with pytest.raises(InsufficientClosedStockError) as error:
        open_presentations(
            loose_units=0,
            closed_stock=2,
            quantity=5,
            factor=6,
        )

    message = str(error.value)
    assert "5" in message
    assert "2" in message


def test_insufficient_closed_stock_error_is_stock_error() -> None:
    """Verifica la jerarquía del error de stock cerrado insuficiente."""
    assert issubclass(InsufficientClosedStockError, StockError)
    assert issubclass(StockError, ValueError)


# --- ApertureResult ---


def test_aperture_result_rejects_negative_closed_stock() -> None:
    """Verifica que un resultado con saldo cerrado negativo es inválido."""
    with pytest.raises(QuantityError):
        ApertureResult(
            quantity=1,
            factor=6,
            loose_units=6,
            closed_stock=-1,
        )


def test_aperture_result_rejects_zero_quantity() -> None:
    """Verifica que un resultado sin presentaciones abiertas es inválido."""
    with pytest.raises(QuantityError):
        ApertureResult(
            quantity=0,
            factor=6,
            loose_units=0,
            closed_stock=0,
        )


def test_aperture_result_rejects_base_presentation_factor() -> None:
    """Verifica que un resultado con factor 1 (presentación base) es inválido.

    La construcción directa de `ApertureResult` reutiliza la misma regla de
    dominio que la apertura: abrir la presentación base (factor 1) se rechaza,
    independientemente de que el resultado provenga de `open_presentations`.
    """
    with pytest.raises(BasePresentationApertureError):
        ApertureResult(
            quantity=1,
            factor=1,
            loose_units=1,
            closed_stock=0,
        )
