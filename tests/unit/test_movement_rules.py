"""Pruebas de las reglas de movimientos y compensaciones con FIFO (RF-03)."""

from dataclasses import FrozenInstanceError

import pytest

from app.domain.movements import (
    ApertureNotCompensableError,
    ChainedCompensationError,
    ClosedEntry,
    CompensationResult,
    EntryQuantityExceededError,
    ExcessCompensationError,
    FifoConsumption,
    FifoConsumptionResult,
    InsufficientMovementStockError,
    MovementError,
    MovementKind,
    NotCompensableError,
    compensate,
    consume_closed_entries_fifo,
    opposite_kind,
    validate_compensable_kind,
    validate_movement_kind,
)
from app.domain.quantities import QuantityError
from app.domain.stock import (
    InsufficientClosedStockError,
    open_presentations,
)


# --- MovementKind ---


def test_movement_kind_values_are_the_persisted_types() -> None:
    """Verifica que los tipos de RF-03 sean los identificadores persistidos."""
    assert {kind.value for kind in MovementKind} == {
        "entrada_cerrada",
        "entrada_suelta",
        "salida_cerrada",
        "salida_suelta",
        "apertura",
        "compensacion",
    }


def test_movement_kind_identifies_entries() -> None:
    """Verifica que solo las entradas incorporan existencias al inventario."""
    assert MovementKind.CLOSED_ENTRY.is_entry
    assert MovementKind.LOOSE_ENTRY.is_entry
    assert not MovementKind.CLOSED_EXIT.is_entry
    assert not MovementKind.LOOSE_EXIT.is_entry
    assert not MovementKind.APERTURE.is_entry
    assert not MovementKind.COMPENSATION.is_entry


# --- validate_movement_kind ---


def test_validate_movement_kind_accepts_enum_member() -> None:
    """Verifica que un miembro del enumerado se devuelve sin cambios."""
    assert (
        validate_movement_kind(MovementKind.CLOSED_ENTRY)
        is MovementKind.CLOSED_ENTRY
    )


def test_validate_movement_kind_accepts_persisted_value() -> None:
    """Verifica que un identificador persistido se normaliza al enumerado."""
    assert (
        validate_movement_kind("salida_suelta") is MovementKind.LOOSE_EXIT
    )


def test_validate_movement_kind_rejects_unknown_value() -> None:
    """Verifica el rechazo de un tipo de movimiento desconocido."""
    with pytest.raises(MovementError):
        validate_movement_kind("devolucion")


def test_validate_movement_kind_rejects_non_text_value() -> None:
    """Verifica el rechazo de un valor que no identifica un movimiento."""
    with pytest.raises(MovementError):
        validate_movement_kind(None)  # type: ignore[arg-type]


def test_validate_movement_kind_rejects_case_variant() -> None:
    """Verifica que los identificadores distinguen mayúsculas de minúsculas."""
    with pytest.raises(MovementError):
        validate_movement_kind("ENTRADA_CERRADA")


# --- validate_compensable_kind y opposite_kind ---


def test_validate_compensable_kind_accepts_entries_and_exits() -> None:
    """Verifica que las entradas y la salida suelta pueden compensarse."""
    compensable_kinds = (
        MovementKind.CLOSED_ENTRY,
        MovementKind.LOOSE_ENTRY,
        MovementKind.LOOSE_EXIT,
    )

    for kind in compensable_kinds:
        assert validate_compensable_kind(kind) is kind

    assert (
        validate_compensable_kind("entrada_suelta")
        is MovementKind.LOOSE_ENTRY
    )


def test_validate_compensable_kind_rejects_closed_exit() -> None:
    """Verifica que una salida cerrada no puede compensarse (RF-03, MVP)."""
    with pytest.raises(NotCompensableError) as error:
        validate_compensable_kind(MovementKind.CLOSED_EXIT)

    assert str(error.value) == (
        "Las compensaciones de salidas cerradas quedan fuera del MVP."
    )

    with pytest.raises(NotCompensableError):
        validate_compensable_kind("salida_cerrada")


def test_validate_compensable_kind_rejects_aperture() -> None:
    """Verifica que una apertura no puede compensarse (RF-03)."""
    with pytest.raises(ApertureNotCompensableError):
        validate_compensable_kind(MovementKind.APERTURE)

    with pytest.raises(ApertureNotCompensableError):
        validate_compensable_kind("apertura")


def test_validate_compensable_kind_rejects_compensation() -> None:
    """Verifica que una compensación no puede compensar a otra (RF-03)."""
    with pytest.raises(ChainedCompensationError):
        validate_compensable_kind(MovementKind.COMPENSATION)


def test_validate_compensable_kind_rejects_unknown_kind() -> None:
    """Verifica el rechazo de un tipo de movimiento desconocido."""
    with pytest.raises(MovementError):
        validate_compensable_kind("ajuste")


def test_opposite_kind_returns_the_opposite_movement_type() -> None:
    """Verifica que la compensación es del tipo opuesto al original."""
    opposite_kinds = {
        MovementKind.CLOSED_ENTRY: MovementKind.CLOSED_EXIT,
        MovementKind.LOOSE_ENTRY: MovementKind.LOOSE_EXIT,
        MovementKind.LOOSE_EXIT: MovementKind.LOOSE_ENTRY,
    }

    for kind, expected_kind in opposite_kinds.items():
        assert opposite_kind(kind) is expected_kind


def test_opposite_kind_rejects_closed_exit() -> None:
    """Verifica que una salida cerrada no tiene tipo opuesto (RF-03, MVP)."""
    with pytest.raises(NotCompensableError):
        opposite_kind(MovementKind.CLOSED_EXIT)

    with pytest.raises(NotCompensableError):
        opposite_kind("salida_cerrada")


def test_opposite_kind_accepts_persisted_value() -> None:
    """Verifica que el tipo opuesto se resuelve desde el valor persistido."""
    assert opposite_kind("entrada_cerrada") is MovementKind.CLOSED_EXIT


def test_opposite_kind_rejects_aperture() -> None:
    """Verifica que las aperturas no tienen tipo opuesto."""
    with pytest.raises(ApertureNotCompensableError):
        opposite_kind(MovementKind.APERTURE)


def test_opposite_kind_rejects_compensation() -> None:
    """Verifica que las compensaciones no pueden encadenarse."""
    with pytest.raises(ChainedCompensationError):
        opposite_kind(MovementKind.COMPENSATION)


def test_compensation_errors_hierarchy() -> None:
    """Verifica la jerarquía de los errores de compensación."""
    assert issubclass(NotCompensableError, MovementError)
    assert issubclass(ApertureNotCompensableError, NotCompensableError)
    assert issubclass(ChainedCompensationError, NotCompensableError)
    assert issubclass(ExcessCompensationError, MovementError)
    assert issubclass(InsufficientMovementStockError, MovementError)
    assert issubclass(EntryQuantityExceededError, MovementError)
    assert issubclass(MovementError, ValueError)


# --- ClosedEntry ---


def test_closed_entry_defaults_to_zero_counters() -> None:
    """Verifica que una entrada cerrada nueva no tiene contadores."""
    entry = ClosedEntry(entry_id=1, presentation_id=5, quantity=4)

    assert entry.consumed_quantity == 0
    assert entry.compensated_quantity == 0
    assert entry.compensable_quantity == 4


def test_closed_entry_compensable_quantity_subtracts_both_counters() -> None:
    """Verifica `cantidad − cantidad_consumida − cantidad_compensada`."""
    entry = ClosedEntry(
        entry_id=1,
        presentation_id=5,
        quantity=5,
        consumed_quantity=2,
        compensated_quantity=1,
    )

    assert entry.compensable_quantity == 2


def test_closed_entry_compensable_quantity_is_zero_when_fully_used() -> None:
    """Verifica que una entrada consumida y compensada no aporta saldo."""
    entry = ClosedEntry(
        entry_id=1,
        presentation_id=5,
        quantity=3,
        consumed_quantity=1,
        compensated_quantity=2,
    )

    assert entry.compensable_quantity == 0


def test_closed_entry_rejects_counters_above_original_quantity() -> None:
    """Verifica que el consumo y la compensación no superan la cantidad."""
    with pytest.raises(EntryQuantityExceededError):
        ClosedEntry(
            entry_id=1,
            presentation_id=5,
            quantity=3,
            consumed_quantity=2,
            compensated_quantity=2,
        )


def test_closed_entry_rejects_consumed_above_original_quantity() -> None:
    """Verifica que la cantidad consumida no supera la cantidad original."""
    with pytest.raises(EntryQuantityExceededError):
        ClosedEntry(
            entry_id=1,
            presentation_id=5,
            quantity=2,
            consumed_quantity=3,
        )


def test_closed_entry_rejects_compensated_above_original_quantity() -> None:
    """Verifica que la cantidad compensada no supera la cantidad original."""
    with pytest.raises(EntryQuantityExceededError):
        ClosedEntry(
            entry_id=1,
            presentation_id=5,
            quantity=2,
            compensated_quantity=3,
        )


def test_closed_entry_accepts_counters_equal_to_original_quantity() -> None:
    """Verifica que la entrada puede quedar sin saldo disponible."""
    entry = ClosedEntry(
        entry_id=1,
        presentation_id=5,
        quantity=2,
        consumed_quantity=1,
        compensated_quantity=1,
    )

    assert entry.compensable_quantity == 0


def test_closed_entry_rejects_quantity_zero() -> None:
    """Verifica que una entrada cerrada sin cantidad es inválida."""
    with pytest.raises(QuantityError):
        ClosedEntry(entry_id=1, presentation_id=5, quantity=0)


def test_closed_entry_rejects_negative_quantity() -> None:
    """Verifica el rechazo de una cantidad de entrada negativa."""
    with pytest.raises(QuantityError):
        ClosedEntry(entry_id=1, presentation_id=5, quantity=-1)


def test_closed_entry_rejects_fractional_quantity() -> None:
    """Verifica el rechazo de una cantidad de entrada no entera."""
    with pytest.raises(QuantityError):
        ClosedEntry(entry_id=1, presentation_id=5, quantity=2.5)


def test_closed_entry_rejects_bool_quantity() -> None:
    """Verifica el rechazo de un booleano como cantidad de entrada."""
    with pytest.raises(QuantityError):
        ClosedEntry(entry_id=1, presentation_id=5, quantity=True)


def test_closed_entry_rejects_negative_consumed_quantity() -> None:
    """Verifica el rechazo de una cantidad consumida negativa."""
    with pytest.raises(QuantityError):
        ClosedEntry(
            entry_id=1,
            presentation_id=5,
            quantity=3,
            consumed_quantity=-1,
        )


def test_closed_entry_rejects_negative_compensated_quantity() -> None:
    """Verifica el rechazo de una cantidad compensada negativa."""
    with pytest.raises(QuantityError):
        ClosedEntry(
            entry_id=1,
            presentation_id=5,
            quantity=3,
            compensated_quantity=-1,
        )


def test_closed_entry_rejects_bool_consumed_quantity() -> None:
    """Verifica el rechazo de un booleano como cantidad consumida."""
    with pytest.raises(QuantityError):
        ClosedEntry(
            entry_id=1,
            presentation_id=5,
            quantity=3,
            consumed_quantity=True,
        )


def test_closed_entry_rejects_zero_entry_id() -> None:
    """Verifica el rechazo de un identificador de entrada no positivo."""
    with pytest.raises(MovementError):
        ClosedEntry(entry_id=0, presentation_id=5, quantity=1)


def test_closed_entry_rejects_negative_presentation_id() -> None:
    """Verifica el rechazo de un identificador de presentación inválido."""
    with pytest.raises(MovementError):
        ClosedEntry(entry_id=1, presentation_id=-5, quantity=1)


def test_closed_entry_rejects_non_integer_presentation_id() -> None:
    """Verifica el rechazo de un identificador de presentación no entero."""
    with pytest.raises(MovementError):
        ClosedEntry(
            entry_id=1,
            presentation_id="5",  # type: ignore[arg-type]
            quantity=1,
        )


def test_closed_entry_rejects_bool_entry_id() -> None:
    """Verifica el rechazo de un booleano como identificador de entrada."""
    with pytest.raises(MovementError):
        ClosedEntry(entry_id=True, presentation_id=5, quantity=1)


def test_closed_entry_is_immutable() -> None:
    """Verifica que una entrada cerrada no puede modificarse."""
    entry = ClosedEntry(entry_id=1, presentation_id=5, quantity=2)

    with pytest.raises(FrozenInstanceError):
        entry.consumed_quantity = 1  # type: ignore[misc]


# --- FifoConsumption ---


def test_fifo_consumption_rejects_zero_quantity() -> None:
    """Verifica que un consumo FIFO sin cantidad es inválido."""
    with pytest.raises(QuantityError):
        FifoConsumption(entry_id=1, quantity=0)


def test_fifo_consumption_rejects_negative_quantity() -> None:
    """Verifica el rechazo de un consumo FIFO negativo."""
    with pytest.raises(QuantityError):
        FifoConsumption(entry_id=1, quantity=-1)


def test_fifo_consumption_rejects_invalid_entry_id() -> None:
    """Verifica el rechazo de un identificador de entrada inválido."""
    with pytest.raises(MovementError):
        FifoConsumption(entry_id=0, quantity=1)


# --- consume_closed_entries_fifo ---


def test_aperture_consumes_closed_entries_in_fifo_order() -> None:
    """Verifica que una apertura consume las entradas cerradas más antiguas."""
    entries = [
        ClosedEntry(entry_id=1, presentation_id=5, quantity=2),
        ClosedEntry(entry_id=2, presentation_id=5, quantity=2),
    ]

    aperture = open_presentations(
        loose_units=0,
        closed_stock=4,
        quantity=3,
        factor=6,
    )
    result = consume_closed_entries_fifo(
        entries,
        presentation_id=5,
        quantity=aperture.quantity,
    )

    assert aperture.closed_stock == 1
    assert result.consumptions == (
        FifoConsumption(entry_id=1, quantity=2),
        FifoConsumption(entry_id=2, quantity=1),
    )
    assert result.entries == (
        ClosedEntry(
            entry_id=1,
            presentation_id=5,
            quantity=2,
            consumed_quantity=2,
        ),
        ClosedEntry(
            entry_id=2,
            presentation_id=5,
            quantity=2,
            consumed_quantity=1,
        ),
    )


def test_closed_exit_consumes_closed_entries_in_fifo_order() -> None:
    """Verifica que una salida cerrada consume en el mismo orden FIFO."""
    entries = [
        ClosedEntry(entry_id=1, presentation_id=5, quantity=3),
        ClosedEntry(entry_id=2, presentation_id=5, quantity=3),
    ]

    result = consume_closed_entries_fifo(
        entries,
        presentation_id=5,
        quantity=4,
    )

    assert result.consumptions == (
        FifoConsumption(entry_id=1, quantity=3),
        FifoConsumption(entry_id=2, quantity=1),
    )
    assert result.quantity == 4


def test_consume_closed_entries_fifo_affects_only_the_oldest_entry() -> None:
    """Verifica que un consumo parcial no alcanza a las entradas siguientes."""
    entries = [
        ClosedEntry(entry_id=1, presentation_id=5, quantity=4),
        ClosedEntry(entry_id=2, presentation_id=5, quantity=4),
    ]

    result = consume_closed_entries_fifo(
        entries,
        presentation_id=5,
        quantity=1,
    )

    assert result.consumptions == (FifoConsumption(entry_id=1, quantity=1),)
    assert result.entries == (
        ClosedEntry(
            entry_id=1,
            presentation_id=5,
            quantity=4,
            consumed_quantity=1,
        ),
        ClosedEntry(entry_id=2, presentation_id=5, quantity=4),
    )


def test_consume_closed_entries_fifo_skips_entries_without_balance() -> None:
    """Verifica que no se consumen entradas ya agotadas."""
    entries = [
        ClosedEntry(
            entry_id=1,
            presentation_id=5,
            quantity=2,
            consumed_quantity=2,
        ),
        ClosedEntry(entry_id=2, presentation_id=5, quantity=3),
    ]

    result = consume_closed_entries_fifo(
        entries,
        presentation_id=5,
        quantity=3,
    )

    assert result.consumptions == (FifoConsumption(entry_id=2, quantity=3),)


def test_consume_closed_entries_fifo_skips_compensated_balance() -> None:
    """Verifica que la cantidad compensada reduce el saldo consumible."""
    entries = [
        ClosedEntry(
            entry_id=1,
            presentation_id=5,
            quantity=3,
            compensated_quantity=2,
        ),
        ClosedEntry(entry_id=2, presentation_id=5, quantity=1),
    ]

    result = consume_closed_entries_fifo(
        entries,
        presentation_id=5,
        quantity=2,
    )

    assert result.consumptions == (
        FifoConsumption(entry_id=1, quantity=1),
        FifoConsumption(entry_id=2, quantity=1),
    )


def test_consume_fifo_does_not_consume_other_presentations() -> None:
    """Verifica que el consumo FIFO es por presentación."""
    entries = [
        ClosedEntry(entry_id=1, presentation_id=5, quantity=2),
        ClosedEntry(entry_id=2, presentation_id=9, quantity=5),
    ]

    result = consume_closed_entries_fifo(
        entries,
        presentation_id=9,
        quantity=1,
    )

    assert result.presentation_id == 9
    assert result.consumptions == (FifoConsumption(entry_id=2, quantity=1),)
    assert result.entries == (
        ClosedEntry(
            entry_id=2,
            presentation_id=9,
            quantity=5,
            consumed_quantity=1,
        ),
    )


def test_consume_closed_entries_fifo_orders_by_entry_identifier() -> None:
    """Verifica que el orden FIFO no depende del orden de las entradas."""
    entries = [
        ClosedEntry(entry_id=7, presentation_id=5, quantity=1),
        ClosedEntry(entry_id=3, presentation_id=5, quantity=1),
    ]

    result = consume_closed_entries_fifo(
        entries,
        presentation_id=5,
        quantity=2,
    )

    assert result.consumptions == (
        FifoConsumption(entry_id=3, quantity=1),
        FifoConsumption(entry_id=7, quantity=1),
    )
    assert [entry.entry_id for entry in result.entries] == [3, 7]


def test_consume_closed_entries_fifo_can_exhaust_the_presentation() -> None:
    """Verifica que el consumo puede agotar todo el saldo disponible."""
    entries = [
        ClosedEntry(entry_id=1, presentation_id=5, quantity=2),
        ClosedEntry(entry_id=2, presentation_id=5, quantity=3),
    ]

    result = consume_closed_entries_fifo(
        entries,
        presentation_id=5,
        quantity=5,
    )

    assert all(entry.compensable_quantity == 0 for entry in result.entries)


def test_consume_fifo_distributes_exactly_the_quantity() -> None:
    """Verifica que el consumo distribución exactamente lo solicitado."""
    entries = [
        ClosedEntry(entry_id=1, presentation_id=5, quantity=2),
        ClosedEntry(entry_id=2, presentation_id=5, quantity=3),
    ]

    for quantity in range(1, 6):
        result = consume_closed_entries_fifo(
            entries,
            presentation_id=5,
            quantity=quantity,
        )

        assert sum(
            consumption.quantity for consumption in result.consumptions
        ) == quantity
        assert result.quantity == quantity
        for entry in result.entries:
            assert entry.compensable_quantity >= 0
            assert (
                entry.consumed_quantity + entry.compensated_quantity
                <= entry.quantity
            )


def test_consume_fifo_does_not_change_the_input_entries() -> None:
    """Verifica que la operación es pura y no altera las entradas recibidas."""
    entries = [ClosedEntry(entry_id=1, presentation_id=5, quantity=2)]

    consume_closed_entries_fifo(entries, presentation_id=5, quantity=2)

    assert entries[0].consumed_quantity == 0
    assert entries[0].compensable_quantity == 2
    assert len(entries) == 1


def test_consume_closed_entries_fifo_is_deterministic() -> None:
    """Verifica que el consumo es reproducible con los mismos datos."""
    entries = [
        ClosedEntry(entry_id=1, presentation_id=5, quantity=2),
        ClosedEntry(entry_id=2, presentation_id=5, quantity=2),
    ]

    first = consume_closed_entries_fifo(
        entries,
        presentation_id=5,
        quantity=3,
    )
    second = consume_closed_entries_fifo(
        entries,
        presentation_id=5,
        quantity=3,
    )

    assert first == second


def test_consume_closed_entries_fifo_rejects_insufficient_stock() -> None:
    """Verifica el rechazo del consumo sin saldo cerrado suficiente."""
    entries = [ClosedEntry(entry_id=1, presentation_id=5, quantity=2)]

    with pytest.raises(InsufficientClosedStockError):
        consume_closed_entries_fifo(
            entries,
            presentation_id=5,
            quantity=3,
        )


def test_consume_closed_entries_fifo_rejects_empty_entries() -> None:
    """Verifica el rechazo del consumo sin entradas cerradas."""
    with pytest.raises(InsufficientClosedStockError):
        consume_closed_entries_fifo([], presentation_id=5, quantity=1)


def test_consume_fifo_rejects_other_presentation_stock() -> None:
    """Verifica que el saldo de otra presentación no habilita el consumo."""
    entries = [ClosedEntry(entry_id=1, presentation_id=5, quantity=5)]

    with pytest.raises(InsufficientClosedStockError):
        consume_closed_entries_fifo(
            entries,
            presentation_id=9,
            quantity=1,
        )


def test_insufficient_closed_stock_message_reports_availability() -> None:
    """Verifica que el error de consumo informa la cantidad disponible."""
    entries = [ClosedEntry(entry_id=1, presentation_id=5, quantity=2)]

    with pytest.raises(InsufficientClosedStockError) as error:
        consume_closed_entries_fifo(
            entries,
            presentation_id=5,
            quantity=5,
        )

    message = str(error.value)
    assert "5" in message
    assert "2" in message


def test_consume_closed_entries_fifo_rejects_quantity_zero() -> None:
    """Verifica el rechazo de un consumo sin cantidad."""
    with pytest.raises(QuantityError):
        consume_closed_entries_fifo([], presentation_id=5, quantity=0)


def test_consume_closed_entries_fifo_rejects_negative_quantity() -> None:
    """Verifica el rechazo de un consumo con cantidad negativa."""
    with pytest.raises(QuantityError):
        consume_closed_entries_fifo([], presentation_id=5, quantity=-1)


def test_consume_closed_entries_fifo_rejects_fractional_quantity() -> None:
    """Verifica el rechazo de un consumo con cantidad no entera."""
    with pytest.raises(QuantityError):
        consume_closed_entries_fifo([], presentation_id=5, quantity=1.5)


def test_consume_closed_entries_fifo_rejects_bool_quantity() -> None:
    """Verifica el rechazo de un booleano como cantidad de consumo."""
    with pytest.raises(QuantityError):
        consume_closed_entries_fifo([], presentation_id=5, quantity=True)


def test_consume_closed_entries_fifo_rejects_invalid_presentation_id() -> None:
    """Verifica el rechazo de una presentación sin identificador válido."""
    with pytest.raises(MovementError):
        consume_closed_entries_fifo([], presentation_id=0, quantity=1)


# --- FifoConsumptionResult ---


def test_fifo_consumption_result_rejects_unbalanced_consumptions() -> None:
    """Verifica que el detalle debe distribuir toda la cantidad consumida."""
    with pytest.raises(MovementError):
        FifoConsumptionResult(
            presentation_id=5,
            quantity=3,
            consumptions=(FifoConsumption(entry_id=1, quantity=2),),
            entries=(),
        )


def test_fifo_consumption_result_rejects_zero_quantity() -> None:
    """Verifica que un resultado de consumo sin cantidad es inválido."""
    with pytest.raises(QuantityError):
        FifoConsumptionResult(
            presentation_id=5,
            quantity=0,
            consumptions=(),
            entries=(),
        )


def test_fifo_consumption_result_rejects_invalid_presentation_id() -> None:
    """Verifica el rechazo de un resultado sin presentación válida."""
    with pytest.raises(MovementError):
        FifoConsumptionResult(
            presentation_id=-1,
            quantity=1,
            consumptions=(FifoConsumption(entry_id=1, quantity=1),),
            entries=(),
        )


def test_fifo_consumption_result_normalizes_collections() -> None:
    """Verifica que el resultado es inmutable y guarda tuplas."""
    result = FifoConsumptionResult(
        presentation_id=5,
        quantity=1,
        consumptions=[  # type: ignore[arg-type]
            FifoConsumption(entry_id=1, quantity=1)
        ],
        entries=[  # type: ignore[arg-type]
            ClosedEntry(entry_id=1, presentation_id=5, quantity=2)
        ],
    )

    assert isinstance(result.consumptions, tuple)
    assert isinstance(result.entries, tuple)
    with pytest.raises(FrozenInstanceError):
        result.quantity = 2  # type: ignore[misc]


# --- compensate ---


def test_compensate_of_closed_entry_returns_opposite_effect() -> None:
    """Verifica que compensar una entrada cerrada resta su stock."""
    result = compensate(
        original_kind=MovementKind.CLOSED_ENTRY,
        quantity=2,
        compensable_quantity=2,
        available_stock=5,
    )

    assert result.kind is MovementKind.COMPENSATION
    assert result.effect_kind is MovementKind.CLOSED_EXIT
    assert result.quantity == 2
    assert result.stock_before == 5
    assert result.stock_after == 3


def test_compensate_of_loose_entry_returns_opposite_effect() -> None:
    """Verifica que compensar una entrada suelta resta unidades sueltas."""
    result = compensate(
        original_kind=MovementKind.LOOSE_ENTRY,
        quantity=1,
        compensable_quantity=1,
        available_stock=4,
    )

    assert result.effect_kind is MovementKind.LOOSE_EXIT
    assert result.stock_after == 3


def test_compensate_rejects_closed_exit() -> None:
    """Verifica que compensar una salida cerrada queda fuera del MVP."""
    with pytest.raises(NotCompensableError) as error:
        compensate(
            original_kind=MovementKind.CLOSED_EXIT,
            quantity=3,
            compensable_quantity=3,
            available_stock=0,
        )

    assert str(error.value) == (
        "Las compensaciones de salidas cerradas quedan fuera del MVP."
    )

    with pytest.raises(NotCompensableError):
        compensate(
            original_kind="salida_cerrada",
            quantity=3,
            compensable_quantity=3,
            available_stock=0,
        )


def test_compensate_of_loose_exit_restores_loose_units() -> None:
    """Verifica que compensar una salida suelta repone unidades sueltas."""
    result = compensate(
        original_kind="salida_suelta",
        quantity=2,
        compensable_quantity=2,
        available_stock=1,
    )

    assert result.effect_kind is MovementKind.LOOSE_ENTRY
    assert result.stock_after == 3


def test_compensate_accepts_total_compensation() -> None:
    """Verifica que la compensación puede neutralizar todo el movimiento."""
    result = compensate(
        original_kind=MovementKind.CLOSED_ENTRY,
        quantity=4,
        compensable_quantity=4,
        available_stock=4,
    )

    assert result.stock_after == 0


def test_compensate_rejects_aperture() -> None:
    """Verifica que una apertura no puede compensarse (RF-03)."""
    with pytest.raises(ApertureNotCompensableError):
        compensate(
            original_kind=MovementKind.APERTURE,
            quantity=1,
            compensable_quantity=1,
            available_stock=1,
        )


def test_compensate_rejects_chained_compensation() -> None:
    """Verifica que una compensación no puede compensar a otra (RF-03)."""
    with pytest.raises(ChainedCompensationError):
        compensate(
            original_kind=MovementKind.COMPENSATION,
            quantity=1,
            compensable_quantity=1,
            available_stock=1,
        )


def test_compensate_rejects_excess_over_compensable_quantity() -> None:
    """Verifica el rechazo de una compensación que excede lo compensable."""
    with pytest.raises(ExcessCompensationError) as error:
        compensate(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=3,
            compensable_quantity=1,
            available_stock=10,
        )

    message = str(error.value)
    assert "3" in message
    assert "1" in message


def test_compensate_rejects_negative_stock() -> None:
    """Verifica que la compensación no puede producir stock negativo."""
    with pytest.raises(InsufficientMovementStockError) as error:
        compensate(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=2,
            compensable_quantity=2,
            available_stock=1,
        )

    message = str(error.value)
    assert "2" in message
    assert "1" in message


def test_compensate_rejects_negative_loose_stock() -> None:
    """Verifica que el suelto no queda negativo al compensar entradas."""
    with pytest.raises(InsufficientMovementStockError):
        compensate(
            original_kind=MovementKind.LOOSE_ENTRY,
            quantity=1,
            compensable_quantity=1,
            available_stock=0,
        )


def test_compensate_respects_fifo_consumption_example_of_rf_03() -> None:
    """Verifica el escenario de RF-03 para A (+2), una apertura y B (+1).

    Con la entrada cerrada A de cantidad 2 consumida en 1 por una apertura y la
    entrada cerrada B de cantidad 1, compensar A por 2 debe rechazarse y
    compensar A por 1 debe permitirse.
    """
    entries = [
        ClosedEntry(entry_id=1, presentation_id=5, quantity=2),
        ClosedEntry(entry_id=2, presentation_id=5, quantity=1),
    ]

    consumption = consume_closed_entries_fifo(
        entries,
        presentation_id=5,
        quantity=1,
    )
    entry_a = consumption.entries[0]
    closed_stock = sum(
        entry.compensable_quantity for entry in consumption.entries
    )

    assert entry_a.compensable_quantity == 1
    assert closed_stock == 2

    with pytest.raises(ExcessCompensationError):
        compensate(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=2,
            compensable_quantity=entry_a.compensable_quantity,
            available_stock=closed_stock,
        )

    result = compensate(
        original_kind=MovementKind.CLOSED_ENTRY,
        quantity=1,
        compensable_quantity=entry_a.compensable_quantity,
        available_stock=closed_stock,
    )

    assert result.quantity == 1
    assert result.stock_after == closed_stock - 1


def test_compensate_rejects_quantity_zero() -> None:
    """Verifica el rechazo de una compensación sin cantidad."""
    with pytest.raises(QuantityError):
        compensate(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=0,
            compensable_quantity=1,
            available_stock=1,
        )


def test_compensate_rejects_negative_quantity() -> None:
    """Verifica el rechazo de una compensación con cantidad negativa."""
    with pytest.raises(QuantityError):
        compensate(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=-1,
            compensable_quantity=1,
            available_stock=1,
        )


def test_compensate_rejects_fractional_quantity() -> None:
    """Verifica el rechazo de una compensación con cantidad no entera."""
    with pytest.raises(QuantityError):
        compensate(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=1.5,
            compensable_quantity=2,
            available_stock=2,
        )


def test_compensate_rejects_bool_quantity() -> None:
    """Verifica el rechazo de un booleano como cantidad de compensación."""
    with pytest.raises(QuantityError):
        compensate(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=True,
            compensable_quantity=1,
            available_stock=1,
        )


def test_compensate_rejects_negative_compensable_quantity() -> None:
    """Verifica el rechazo de una cantidad compensable negativa."""
    with pytest.raises(QuantityError):
        compensate(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=1,
            compensable_quantity=-1,
            available_stock=1,
        )


def test_compensate_rejects_negative_stock_input() -> None:
    """Verifica el rechazo de un stock vigente negativo."""
    with pytest.raises(QuantityError):
        compensate(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=1,
            compensable_quantity=1,
            available_stock=-1,
        )


def test_compensate_rejects_unknown_original_kind() -> None:
    """Verifica el rechazo de un movimiento original desconocido."""
    with pytest.raises(MovementError):
        compensate(
            original_kind="ajuste",
            quantity=1,
            compensable_quantity=1,
            available_stock=1,
        )


def test_compensate_is_deterministic() -> None:
    """Verifica que la compensación es reproducible con los mismos datos."""
    first = compensate(
        original_kind=MovementKind.CLOSED_ENTRY,
        quantity=1,
        compensable_quantity=2,
        available_stock=3,
    )
    second = compensate(
        original_kind=MovementKind.CLOSED_ENTRY,
        quantity=1,
        compensable_quantity=2,
        available_stock=3,
    )

    assert first == second


# --- CompensationResult ---


def test_compensation_result_kind_is_compensation() -> None:
    """Verifica que el movimiento compensatorio es de tipo compensación."""
    result = CompensationResult(
        original_kind=MovementKind.CLOSED_ENTRY,
        quantity=1,
        stock_before=3,
        stock_after=2,
    )

    assert result.kind is MovementKind.COMPENSATION
    assert result.effect_kind is MovementKind.CLOSED_EXIT


def test_compensation_result_rejects_aperture_original() -> None:
    """Verifica que un resultado no puede compensar una apertura."""
    with pytest.raises(ApertureNotCompensableError):
        CompensationResult(
            original_kind=MovementKind.APERTURE,
            quantity=1,
            stock_before=3,
            stock_after=4,
        )


def test_compensation_result_rejects_compensation_original() -> None:
    """Verifica que un resultado no puede encadenar compensaciones."""
    with pytest.raises(ChainedCompensationError):
        CompensationResult(
            original_kind=MovementKind.COMPENSATION,
            quantity=1,
            stock_before=3,
            stock_after=2,
        )


def test_compensation_result_rejects_closed_exit_original() -> None:
    """Verifica que un resultado no puede compensar una salida cerrada."""
    with pytest.raises(NotCompensableError) as error:
        CompensationResult(
            original_kind=MovementKind.CLOSED_EXIT,
            quantity=1,
            stock_before=0,
            stock_after=1,
        )

    assert str(error.value) == (
        "Las compensaciones de salidas cerradas quedan fuera del MVP."
    )

    with pytest.raises(NotCompensableError):
        CompensationResult(
            original_kind="salida_cerrada",
            quantity=1,
            stock_before=0,
            stock_after=1,
        )


def test_compensation_result_rejects_negative_stock() -> None:
    """Verifica que un resultado con stock negativo es inválido."""
    with pytest.raises(QuantityError):
        CompensationResult(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=5,
            stock_before=2,
            stock_after=-3,
        )


def test_compensation_result_rejects_incoherent_stock() -> None:
    """Verifica que el saldo resultante debe ser coherente con la cantidad."""
    with pytest.raises(MovementError):
        CompensationResult(
            original_kind=MovementKind.CLOSED_ENTRY,
            quantity=1,
            stock_before=5,
            stock_after=5,
        )


def test_compensation_result_is_immutable() -> None:
    """Verifica que el resultado de la compensación no puede modificarse."""
    result = CompensationResult(
        original_kind=MovementKind.CLOSED_ENTRY,
        quantity=1,
        stock_before=5,
        stock_after=4,
    )

    with pytest.raises(FrozenInstanceError):
        result.stock_after = 9  # type: ignore[misc]
