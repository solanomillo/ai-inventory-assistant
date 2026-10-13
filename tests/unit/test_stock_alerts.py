"""Pruebas de las reglas de stock bajo y reposición (RF-04)."""

from dataclasses import dataclass

import pytest

from app.domain.quantities import QuantityError
from app.domain.stock import (
    ClosedStock,
    calculate_shortage,
    is_low_stock,
    low_stock_items,
    replenishment_items,
)


@dataclass(frozen=True)
class _Item:
    """Ítem de prueba que cumple el contrato `StockAlertItem` (RF-04).

    Representa lo mínimo que un producto aporta a las consultas globales: su
    flag de actividad, sus saldos y su stock mínimo. No es una entidad del
    dominio; existe solo para las pruebas.
    """

    active: bool
    loose_units: int
    minimum_stock: int
    closed_stocks: tuple[ClosedStock, ...] = ()


# --- is_low_stock ---


def test_is_low_stock_when_total_equals_minimum() -> None:
    """Verifica que el total igual al stock mínimo es stock bajo (RF-04)."""
    assert is_low_stock(loose_units=10, minimum_stock=10) is True


def test_is_low_stock_when_total_is_below_minimum() -> None:
    """Verifica que un total menor que el stock mínimo es stock bajo."""
    assert is_low_stock(loose_units=6, minimum_stock=10) is True


def test_is_low_stock_is_false_when_total_is_above_minimum() -> None:
    """Verifica que un total mayor que el stock mínimo no es stock bajo."""
    assert is_low_stock(loose_units=12, minimum_stock=10) is False


def test_is_low_stock_with_zero_minimum_and_zero_stock() -> None:
    """Verifica el caso de stock mínimo y total equivalente iguales a cero."""
    assert is_low_stock(loose_units=0, minimum_stock=0) is True


def test_is_low_stock_uses_total_equivalent_of_closed_stocks() -> None:
    """Verifica que el total derivado de presentaciones cerradas se evalúa."""
    closed_stocks = [ClosedStock(factor=6, quantity=2)]

    assert is_low_stock(
        loose_units=0,
        closed_stocks=closed_stocks,
        minimum_stock=12,
    ) is True


def test_is_low_stock_is_false_when_closed_stock_exceeds_minimum() -> None:
    """Verifica que un total derivado mayor que el mínimo no es stock bajo."""
    closed_stocks = [ClosedStock(factor=6, quantity=2)]

    assert is_low_stock(
        loose_units=0,
        closed_stocks=closed_stocks,
        minimum_stock=11,
    ) is False


def test_is_low_stock_combines_loose_and_closed_stock() -> None:
    """Verifica la comparación sobre la suma de sueltas y cerradas."""
    closed_stocks = [ClosedStock(factor=6, quantity=2)]

    assert is_low_stock(
        loose_units=4,
        closed_stocks=closed_stocks,
        minimum_stock=16,
    ) is True


def test_is_low_stock_rejects_negative_loose_units() -> None:
    """Verifica el rechazo de unidades sueltas negativas."""
    with pytest.raises(QuantityError):
        is_low_stock(loose_units=-1, minimum_stock=0)


def test_is_low_stock_rejects_negative_minimum_stock() -> None:
    """Verifica el rechazo de un stock mínimo negativo."""
    with pytest.raises(QuantityError):
        is_low_stock(loose_units=0, minimum_stock=-1)


def test_is_low_stock_rejects_non_integer_minimum_stock() -> None:
    """Verifica el rechazo de un stock mínimo no entero."""
    with pytest.raises(QuantityError):
        is_low_stock(
            loose_units=0,
            minimum_stock=1.5,  # type: ignore[arg-type]
        )


def test_is_low_stock_rejects_bool_minimum_stock() -> None:
    """Verifica el rechazo de un booleano como stock mínimo."""
    with pytest.raises(QuantityError):
        is_low_stock(loose_units=0, minimum_stock=True)


def test_is_low_stock_is_deterministic() -> None:
    """Verifica que la evaluación es reproducible con los mismos datos."""
    assert is_low_stock(loose_units=6, minimum_stock=10) is is_low_stock(
        loose_units=6,
        minimum_stock=10,
    )


# --- calculate_shortage ---


def test_calculate_shortage_reports_missing_units() -> None:
    """Verifica el faltante cuando el total es menor que el mínimo."""
    assert calculate_shortage(loose_units=6, minimum_stock=10) == 4


def test_calculate_shortage_is_zero_above_minimum() -> None:
    """Verifica que un total mayor que el mínimo no genera faltante."""
    assert calculate_shortage(loose_units=12, minimum_stock=10) == 0


def test_calculate_shortage_is_zero_when_equal_to_minimum() -> None:
    """Verifica que un total igual al mínimo no genera faltante."""
    assert calculate_shortage(loose_units=10, minimum_stock=10) == 0


def test_calculate_shortage_is_zero_without_minimum() -> None:
    """Verifica que un producto sin stock mínimo no tiene faltante."""
    assert calculate_shortage(loose_units=0, minimum_stock=0) == 0


def test_calculate_shortage_considers_closed_stock() -> None:
    """Verifica el faltante sobre el total derivado de cerradas."""
    closed_stocks = [ClosedStock(factor=6, quantity=3)]

    assert calculate_shortage(
        loose_units=0,
        closed_stocks=closed_stocks,
        minimum_stock=24,
    ) == 6


def test_calculate_shortage_never_returns_negative_values() -> None:
    """Verifica que el faltante nunca es negativo."""
    for loose_units, minimum_stock in ((0, 0), (5, 5), (7, 3), (100, 1)):
        assert (
            calculate_shortage(
                loose_units=loose_units,
                minimum_stock=minimum_stock,
            )
            >= 0
        )


def test_calculate_shortage_rejects_negative_loose_units() -> None:
    """Verifica el rechazo de unidades sueltas negativas."""
    with pytest.raises(QuantityError):
        calculate_shortage(loose_units=-1, minimum_stock=10)


def test_calculate_shortage_rejects_negative_minimum_stock() -> None:
    """Verifica el rechazo de un stock mínimo negativo."""
    with pytest.raises(QuantityError):
        calculate_shortage(loose_units=0, minimum_stock=-1)


def test_calculate_shortage_rejects_non_integer_minimum_stock() -> None:
    """Verifica el rechazo de un valor no entero como stock mínimo."""
    with pytest.raises(QuantityError):
        calculate_shortage(
            loose_units=0,
            minimum_stock="10",  # type: ignore[arg-type]
        )


# --- low_stock_items ---


def test_low_stock_items_includes_only_active_items_with_low_stock() -> None:
    """Verifica que la consulta global solo devuelve activos con stock bajo."""
    low_and_active = _Item(active=True, loose_units=6, minimum_stock=10)
    equal_and_active = _Item(active=True, loose_units=10, minimum_stock=10)
    enough = _Item(active=True, loose_units=12, minimum_stock=10)
    inactive = _Item(active=False, loose_units=1, minimum_stock=10)
    items = [low_and_active, equal_and_active, enough, inactive]

    result = low_stock_items(items)

    assert result == [low_and_active, equal_and_active]


def test_low_stock_items_includes_zero_minimum_and_zero_stock() -> None:
    """Verifica que un activo con mínimo y total cero figura con stock bajo."""
    item = _Item(active=True, loose_units=0, minimum_stock=0)

    assert low_stock_items([item]) == [item]


def test_low_stock_items_excludes_inactive_items() -> None:
    """Verifica que los productos desactivados se excluyen (RF-04)."""
    inactive = _Item(active=False, loose_units=0, minimum_stock=50)

    assert low_stock_items([inactive]) == []


def test_low_stock_items_excludes_active_items_with_sufficient_stock() -> None:
    """Verifica que un activo con stock suficiente no figura con stock bajo."""
    item = _Item(active=True, loose_units=11, minimum_stock=10)

    assert low_stock_items([item]) == []


def test_low_stock_items_uses_closed_stock_to_decide() -> None:
    """Verifica que el stock cerrado cuenta para la condición de stock bajo."""
    with_enough_closed_stock = _Item(
        active=True,
        loose_units=5,
        minimum_stock=16,
        closed_stocks=(ClosedStock(factor=6, quantity=2),),
    )
    without_enough = _Item(active=True, loose_units=5, minimum_stock=16)

    assert low_stock_items([with_enough_closed_stock, without_enough]) == [
        without_enough
    ]


def test_low_stock_items_returns_empty_list_without_items() -> None:
    """Verifica que una consulta sin ítems devuelve una lista vacía."""
    assert low_stock_items([]) == []


def test_low_stock_items_preserves_input_order_and_items() -> None:
    """Verifica que la consulta conserva el orden y la identidad."""
    first = _Item(active=True, loose_units=0, minimum_stock=1)
    second = _Item(active=True, loose_units=0, minimum_stock=2)

    result = low_stock_items([first, second])

    assert result[0] is first
    assert result[1] is second


def test_low_stock_items_does_not_modify_the_input() -> None:
    """Verifica que la consulta no altera la colección de entrada."""
    low = _Item(active=True, loose_units=1, minimum_stock=5)
    enough = _Item(active=True, loose_units=9, minimum_stock=5)
    items = [low, enough]

    result = low_stock_items(items)

    assert items == [low, enough]
    assert result is not items


def test_low_stock_items_rejects_invalid_stock_data() -> None:
    """Verifica el rechazo de ítems con datos de stock inválidos."""
    invalid = _Item(active=True, loose_units=0, minimum_stock=-1)

    with pytest.raises(QuantityError):
        low_stock_items([invalid])


# --- replenishment_items ---


def test_replenishment_items_includes_active_items_with_shortage() -> None:
    """Verifica que un activo con faltante mayor que cero se repone."""
    item = _Item(active=True, loose_units=6, minimum_stock=10)

    assert replenishment_items([item]) == [item]
    assert calculate_shortage(
        loose_units=item.loose_units,
        closed_stocks=item.closed_stocks,
        minimum_stock=item.minimum_stock,
    ) == 4


def test_replenishment_items_excludes_sufficient_stock() -> None:
    """Verifica que un activo con stock suficiente no requiere reposición."""
    item = _Item(active=True, loose_units=12, minimum_stock=10)

    assert replenishment_items([item]) == []


def test_replenishment_items_excludes_items_equal_to_minimum() -> None:
    """Verifica que un faltante igual a cero no propone reposición."""
    item = _Item(active=True, loose_units=10, minimum_stock=10)

    assert replenishment_items([item]) == []


def test_replenishment_items_excludes_low_stock_without_shortage() -> None:
    """Verifica que un activo con mínimo y total cero no se repone."""
    item = _Item(active=True, loose_units=0, minimum_stock=0)

    assert is_low_stock(
        loose_units=item.loose_units,
        minimum_stock=item.minimum_stock,
    ) is True
    assert replenishment_items([item]) == []


def test_replenishment_items_excludes_inactive_items() -> None:
    """Verifica que los productos desactivados se excluyen (RF-04)."""
    inactive = _Item(active=False, loose_units=0, minimum_stock=50)

    assert replenishment_items([inactive]) == []


def test_replenishment_items_returns_empty_list_without_items() -> None:
    """Verifica que una consulta sin ítems devuelve una lista vacía."""
    assert replenishment_items([]) == []


def test_replenishment_items_preserves_input_order() -> None:
    """Verifica que la consulta conserva el orden de entrada."""
    first = _Item(active=True, loose_units=1, minimum_stock=3)
    second = _Item(active=True, loose_units=4, minimum_stock=9)

    result = replenishment_items([first, second])

    assert result[0] is first
    assert result[1] is second


def test_replenishment_items_is_subset_of_low_stock_items() -> None:
    """Verifica que reponer implica stock bajo y excluye a los desactivados."""
    active_low = _Item(active=True, loose_units=6, minimum_stock=10)
    active_equal = _Item(active=True, loose_units=10, minimum_stock=10)
    active_enough = _Item(active=True, loose_units=12, minimum_stock=10)
    inactive_low = _Item(active=False, loose_units=0, minimum_stock=10)
    items = [active_low, active_equal, active_enough, inactive_low]

    low_items = low_stock_items(items)
    replenishment = replenishment_items(items)

    assert low_items == [active_low, active_equal]
    assert replenishment == [active_low]
    assert all(item in low_items for item in replenishment)
