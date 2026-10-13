"""Pruebas para las reglas de dominio de las presentaciones (RF-07)."""

from decimal import Decimal

import pytest

from app.domain.presentations import (
    DuplicatePresentationNameError,
    FactorNotMutableError,
    Presentation,
    PresentationError,
    validate_presentation_name,
    validate_presentation_name_unique,
    validate_single_base_presentation,
)
from app.domain.quantities import QuantityError


# --- validate_presentation_name ---


def test_validate_presentation_name_trims() -> None:
    """Verifica que el nombre de la presentación se recorta."""
    assert validate_presentation_name("  caja  ") == "caja"


def test_validate_presentation_name_rejects_empty() -> None:
    """Verifica el rechazo de un nombre vacío."""
    with pytest.raises(PresentationError):
        validate_presentation_name("")


def test_validate_presentation_name_rejects_blank() -> None:
    """Verifica el rechazo de un nombre con solo espacios."""
    with pytest.raises(PresentationError):
        validate_presentation_name("    ")


def test_validate_presentation_name_rejects_non_text() -> None:
    """Verifica el rechazo de un nombre que no es texto."""
    with pytest.raises(PresentationError):
        validate_presentation_name(None)  # type: ignore[arg-type]


# --- validate_presentation_name_unique ---


def test_validate_presentation_name_unique_accepts_new_name() -> None:
    """Verifica que un nombre no registrado se acepta."""
    assert validate_presentation_name_unique("caja", ["botella"]) == "caja"


def test_validate_presentation_name_unique_rejects_duplicate() -> None:
    """Verifica el rechazo de un nombre duplicado dentro del producto."""
    with pytest.raises(DuplicatePresentationNameError):
        validate_presentation_name_unique("caja", ["botella", "caja"])


def test_validate_presentation_name_unique_trims_before_comparing() -> None:
    """Verifica que la comparación de nombres usa el valor recortado."""
    with pytest.raises(DuplicatePresentationNameError):
        validate_presentation_name_unique("  caja  ", ["caja"])


def test_validate_presentation_name_unique_rejects_different_case() -> None:
    """Verifica que un nombre que difiere solo en el caso se rechaza."""
    with pytest.raises(DuplicatePresentationNameError):
        validate_presentation_name_unique("Caja", ["caja"])


def test_validate_presentation_name_unique_rejects_case_with_spaces() -> None:
    """Verifica que el recorte y la comparación sin caso se combinan."""
    with pytest.raises(DuplicatePresentationNameError):
        validate_presentation_name_unique("  CAJA  ", ["Caja"])


def test_validate_presentation_name_unique_keeps_original_case() -> None:
    """Verifica que se conserva el texto original recortado del nombre."""
    assert validate_presentation_name_unique("  CaJa  ", ["botella"]) == "CaJa"


def test_validate_presentation_name_unique_rejects_blank_name() -> None:
    """Verifica que un nombre vacío se rechaza antes de comparar."""
    with pytest.raises(PresentationError):
        validate_presentation_name_unique("   ", ["caja"])


def test_duplicate_presentation_name_error_is_presentation_error() -> None:
    """Verifica que un nombre duplicado produce un error de presentación."""
    assert issubclass(DuplicatePresentationNameError, PresentationError)


# --- Presentation: construcción ---


def test_presentation_normalizes_name() -> None:
    """Verifica que el nombre se recorta al construir la presentación."""
    presentation = Presentation(
        name="  caja  ",
        factor=6,
        sale_price=Decimal("120.50"),
        has_history=False,
    )

    assert presentation.name == "caja"


def test_presentation_accepts_factor_one() -> None:
    """Verifica que el factor 1 se acepta."""
    presentation = Presentation(
        name="botella",
        factor=1,
        sale_price=Decimal("10"),
        has_history=False,
    )

    assert presentation.factor == 1


def test_presentation_accepts_factor_above_one() -> None:
    """Verifica que un factor mayor que uno se acepta."""
    presentation = Presentation(
        name="fardo",
        factor=24,
        sale_price=Decimal("999.99"),
        has_history=False,
    )

    assert presentation.factor == 24


def test_presentation_starts_without_closed_stock_or_history() -> None:
    """Verifica que una presentación nueva nace sin stock ni historial."""
    presentation = Presentation(
        name="caja",
        factor=6,
        sale_price=Decimal("120"),
        has_history=False,
    )

    assert presentation.closed_stock == 0
    assert presentation.has_history is False


def test_presentation_requires_explicit_history() -> None:
    """Verifica que el historial debe informarse al construir la presentación."""
    with pytest.raises(TypeError):
        Presentation(  # type: ignore[call-arg]
            name="caja",
            factor=6,
            sale_price=Decimal("120"),
        )


def test_presentation_rejects_empty_name() -> None:
    """Verifica el rechazo de una presentación sin nombre."""
    with pytest.raises(PresentationError):
        Presentation(
            name="   ",
            factor=1,
            sale_price=Decimal("10"),
            has_history=False,
        )


def test_presentation_rejects_zero_factor() -> None:
    """Verifica el rechazo del factor cero."""
    with pytest.raises(QuantityError):
        Presentation(
            name="caja",
            factor=0,
            sale_price=Decimal("10"),
            has_history=False,
        )


def test_presentation_rejects_negative_factor() -> None:
    """Verifica el rechazo de un factor negativo."""
    with pytest.raises(QuantityError):
        Presentation(
            name="caja",
            factor=-1,
            sale_price=Decimal("10"),
            has_history=False,
        )


def test_presentation_rejects_fractional_factor() -> None:
    """Verifica el rechazo de un factor no entero."""
    with pytest.raises(QuantityError):
        Presentation(
            name="caja",
            factor=1.5,  # type: ignore[arg-type]
            sale_price=Decimal("10"),
            has_history=False,
        )


def test_presentation_rejects_bool_factor() -> None:
    """Verifica el rechazo de un booleano como factor."""
    with pytest.raises(QuantityError):
        Presentation(
            name="caja",
            factor=True,  # type: ignore[arg-type]
            sale_price=Decimal("10"),
            has_history=False,
        )


def test_presentation_rejects_zero_price() -> None:
    """Verifica el rechazo de un precio igual a cero."""
    with pytest.raises(QuantityError):
        Presentation(
            name="caja",
            factor=1,
            sale_price=Decimal("0"),
            has_history=False,
        )


def test_presentation_rejects_negative_price() -> None:
    """Verifica el rechazo de un precio negativo."""
    with pytest.raises(QuantityError):
        Presentation(
            name="caja",
            factor=1,
            sale_price=Decimal("-5"),
            has_history=False,
        )


def test_presentation_rejects_float_price() -> None:
    """Verifica el rechazo de un precio en punto flotante."""
    with pytest.raises(QuantityError):
        Presentation(
            name="caja",
            factor=1,
            sale_price=10.5,  # type: ignore[arg-type]
            has_history=False,
        )


def test_presentation_rejects_negative_closed_stock() -> None:
    """Verifica el rechazo de un stock cerrado negativo."""
    with pytest.raises(QuantityError):
        Presentation(
            name="caja",
            factor=6,
            sale_price=Decimal("10"),
            has_history=False,
            closed_stock=-1,
        )


# --- Presentation: precios independientes ---


def test_presentation_prices_are_independent() -> None:
    """Verifica que el precio de una presentación no deriva del de otra."""
    unit = Presentation(
        name="botella",
        factor=1,
        sale_price=Decimal("10"),
        has_history=False,
    )
    pack = Presentation(
        name="caja",
        factor=6,
        sale_price=Decimal("55"),
        has_history=False,
    )

    assert unit.sale_price == Decimal("10")
    assert pack.sale_price == Decimal("55")


# --- Presentation.create_base ---


def test_create_base_creates_factor_one_presentation() -> None:
    """Verifica que la presentación base se crea con factor 1."""
    base = Presentation.create_base("botella", Decimal("10"))

    assert base.factor == 1
    assert base.is_base is True
    assert base.name == "botella"
    assert base.closed_stock == 0
    assert base.has_history is False


def test_create_base_rejects_invalid_price() -> None:
    """Verifica que la presentación base valida su precio."""
    with pytest.raises(QuantityError):
        Presentation.create_base("botella", Decimal("0"))


def test_is_base_is_false_for_factor_above_one() -> None:
    """Verifica que una presentación con factor mayor que uno no es base."""
    presentation = Presentation(
        name="caja",
        factor=6,
        sale_price=Decimal("60"),
        has_history=False,
    )

    assert presentation.is_base is False


# --- Presentation.change_factor ---


def test_change_factor_returns_updated_copy() -> None:
    """Verifica que el factor se actualiza sin modificar el original."""
    presentation = Presentation(
        name="caja",
        factor=6,
        sale_price=Decimal("60"),
        has_history=False,
    )

    updated = presentation.change_factor(12)

    assert updated.factor == 12
    assert presentation.factor == 6


def test_change_factor_rejects_invalid_factor() -> None:
    """Verifica que el factor nuevo también se valida."""
    presentation = Presentation(
        name="caja",
        factor=6,
        sale_price=Decimal("60"),
        has_history=False,
    )

    with pytest.raises(QuantityError):
        presentation.change_factor(0)


def test_change_factor_rejects_when_closed_stock_exists() -> None:
    """Verifica el rechazo del cambio de factor con stock cerrado."""
    presentation = Presentation(
        name="caja",
        factor=6,
        sale_price=Decimal("60"),
        has_history=False,
        closed_stock=3,
    )

    with pytest.raises(FactorNotMutableError):
        presentation.change_factor(12)


def test_change_factor_rejects_when_there_is_history() -> None:
    """Verifica el rechazo del cambio de factor con movimientos históricos."""
    presentation = Presentation(
        name="caja",
        factor=6,
        sale_price=Decimal("60"),
        has_history=True,
    )

    with pytest.raises(FactorNotMutableError):
        presentation.change_factor(12)


def test_change_factor_rejects_non_base_factor_for_base_presentation() -> None:
    """Verifica que la presentación base no puede dejar de ser base."""
    base = Presentation.create_base("botella", Decimal("10"))

    with pytest.raises(FactorNotMutableError):
        base.change_factor(6)


def test_change_factor_accepts_factor_one_for_base_presentation() -> None:
    """Verifica que el factor 1 sobre la presentación base es un no-op."""
    base = Presentation.create_base("botella", Decimal("10"))

    updated = base.change_factor(1)

    assert updated.factor == 1
    assert updated.is_base is True
    assert updated.name == "botella"
    assert base.factor == 1


def test_factor_not_mutable_error_is_presentation_error() -> None:
    """Verifica que el factor inmutable produce un error de presentación."""
    assert issubclass(FactorNotMutableError, PresentationError)


# --- validate_single_base_presentation ---


def test_validate_single_base_presentation_returns_base() -> None:
    """Verifica que se devuelve la única presentación base."""
    base = Presentation.create_base("botella", Decimal("10"))
    pack = Presentation(
        name="caja",
        factor=6,
        sale_price=Decimal("60"),
        has_history=False,
    )

    assert validate_single_base_presentation([base, pack]) is base


def test_validate_single_base_presentation_rejects_missing_base() -> None:
    """Verifica el rechazo cuando no existe presentación base."""
    pack = Presentation(
        name="caja",
        factor=6,
        sale_price=Decimal("60"),
        has_history=False,
    )

    with pytest.raises(PresentationError):
        validate_single_base_presentation([pack])


def test_validate_single_base_presentation_rejects_no_presentations() -> None:
    """Verifica el rechazo cuando el producto no tiene presentaciones."""
    with pytest.raises(PresentationError):
        validate_single_base_presentation([])


def test_validate_single_base_presentation_rejects_two_bases() -> None:
    """Verifica el rechazo cuando existe más de una presentación base."""
    first = Presentation.create_base("botella", Decimal("10"))
    second = Presentation.create_base("unidad", Decimal("12"))

    with pytest.raises(PresentationError):
        validate_single_base_presentation([first, second])
