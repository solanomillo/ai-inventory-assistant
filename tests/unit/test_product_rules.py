"""Pruebas para las reglas de dominio de los productos (RF-01)."""

from dataclasses import FrozenInstanceError

import pytest

from app.domain.products import (
    DuplicateSkuError,
    ImmutableSkuError,
    Product,
    ProductError,
    normalize_required_text,
    validate_base_unit,
    validate_minimum_stock,
    validate_product_name,
    validate_sku,
    validate_sku_unique,
    validate_sku_unchanged,
)
from app.domain.quantities import QuantityError


# --- normalize_required_text y campos de texto ---


def test_normalize_required_text_trims_surrounding_spaces() -> None:
    """Verifica que los espacios iniciales y finales se eliminan."""
    assert normalize_required_text("  arroz  ", "nombre") == "arroz"


def test_normalize_required_text_keeps_inner_spaces() -> None:
    """Verifica que los espacios internos se conservan."""
    assert normalize_required_text(" arroz largo ", "nombre") == "arroz largo"


def test_normalize_required_text_rejects_empty() -> None:
    """Verifica el rechazo de un texto vacío."""
    with pytest.raises(ProductError):
        normalize_required_text("", "nombre")


def test_normalize_required_text_rejects_blank() -> None:
    """Verifica el rechazo de un texto con solo espacios."""
    with pytest.raises(ProductError):
        normalize_required_text("   ", "nombre")


def test_normalize_required_text_rejects_non_text() -> None:
    """Verifica el rechazo de un valor que no es texto."""
    with pytest.raises(ProductError):
        normalize_required_text(123, "nombre")  # type: ignore[arg-type]


def test_validate_sku_trims() -> None:
    """Verifica que el SKU se recorta."""
    assert validate_sku("  SKU-001  ") == "SKU-001"


def test_validate_product_name_accepts_valid_name() -> None:
    """Verifica que un nombre válido se acepta y se recorta."""
    assert validate_product_name("  Yerba  ") == "Yerba"


def test_validate_base_unit_accepts_valid_unit() -> None:
    """Verifica que una unidad base válida se acepta y se recorta."""
    assert validate_base_unit("  botella  ") == "botella"


# --- validate_minimum_stock ---


def test_validate_minimum_stock_accepts_zero() -> None:
    """Verifica que el stock mínimo puede ser cero."""
    assert validate_minimum_stock(0) == 0


def test_validate_minimum_stock_accepts_positive() -> None:
    """Verifica que un stock mínimo positivo se acepta."""
    assert validate_minimum_stock(10) == 10


def test_validate_minimum_stock_rejects_negative() -> None:
    """Verifica el rechazo de un stock mínimo negativo."""
    with pytest.raises(QuantityError):
        validate_minimum_stock(-1)


def test_validate_minimum_stock_rejects_fraction() -> None:
    """Verifica el rechazo de un stock mínimo no entero."""
    with pytest.raises(QuantityError):
        validate_minimum_stock(2.5)  # type: ignore[arg-type]


def test_validate_minimum_stock_rejects_bool() -> None:
    """Verifica el rechazo de un booleano como stock mínimo."""
    with pytest.raises(QuantityError):
        validate_minimum_stock(True)  # type: ignore[arg-type]


# --- validate_sku_unique ---


def test_validate_sku_unique_accepts_new_sku() -> None:
    """Verifica que un SKU no registrado se acepta."""
    assert validate_sku_unique("SKU-002", ["SKU-001"]) == "SKU-002"


def test_validate_sku_unique_accepts_when_there_are_no_products() -> None:
    """Verifica que un SKU se acepta si no hay productos registrados."""
    assert validate_sku_unique("SKU-001", []) == "SKU-001"


def test_validate_sku_unique_rejects_duplicate() -> None:
    """Verifica el rechazo de un SKU ya registrado."""
    with pytest.raises(DuplicateSkuError):
        validate_sku_unique("SKU-001", ["SKU-001", "SKU-002"])


def test_validate_sku_unique_trims_before_comparing() -> None:
    """Verifica que la comparación de SKU usa el valor recortado."""
    with pytest.raises(DuplicateSkuError):
        validate_sku_unique("  SKU-001  ", ["SKU-001"])


def test_validate_sku_unique_rejects_duplicate_with_different_case() -> None:
    """Verifica el rechazo de un SKU que difiere solo en mayúsculas."""
    with pytest.raises(DuplicateSkuError):
        validate_sku_unique("sku-001", ["SKU-001"])


def test_validate_sku_unique_rejects_uppercase_against_lowercase() -> None:
    """Verifica el rechazo cuando el SKU registrado está en minúsculas."""
    with pytest.raises(DuplicateSkuError):
        validate_sku_unique("SKU-001", ["sku-001"])


def test_validate_sku_unique_rejects_duplicate_with_case_and_spaces() -> None:
    """Verifica que el recorte y la comparación sin caso se combinan."""
    with pytest.raises(DuplicateSkuError):
        validate_sku_unique("  Sku-001  ", ["sKU-001"])


def test_validate_sku_unique_keeps_original_case() -> None:
    """Verifica que se conserva el texto original recortado del SKU."""
    assert validate_sku_unique("  Sku-002  ", ["SKU-001"]) == "Sku-002"


def test_duplicate_sku_error_is_product_error() -> None:
    """Verifica que el error de SKU duplicado es un error de producto."""
    assert issubclass(DuplicateSkuError, ProductError)


# --- validate_sku_unchanged ---


def test_validate_sku_unchanged_accepts_same_sku() -> None:
    """Verifica que mantener el SKU vigente se acepta."""
    assert validate_sku_unchanged("SKU-001", "SKU-001") == "SKU-001"


def test_validate_sku_unchanged_accepts_same_sku_with_spaces() -> None:
    """Verifica que el mismo SKU con espacios alrededor se acepta."""
    assert validate_sku_unchanged("SKU-001", " SKU-001 ") == "SKU-001"


def test_validate_sku_unchanged_rejects_different_sku() -> None:
    """Verifica el rechazo de un cambio de SKU."""
    with pytest.raises(ImmutableSkuError):
        validate_sku_unchanged("SKU-001", "SKU-999")


def test_validate_sku_unchanged_rejects_blank_sku() -> None:
    """Verifica el rechazo de un SKU solicitado vacío."""
    with pytest.raises(ProductError):
        validate_sku_unchanged("SKU-001", "   ")


def test_immutable_sku_error_is_product_error() -> None:
    """Verifica que el error de SKU inmutable es un error de producto."""
    assert issubclass(ImmutableSkuError, ProductError)


# --- Product ---


def test_product_normalizes_text_fields() -> None:
    """Verifica que el producto recorta SKU, nombre y unidad base."""
    product = Product(
        sku="  SKU-001  ",
        name="  Yerba mate  ",
        base_unit="  paquete  ",
    )

    assert product.sku == "SKU-001"
    assert product.name == "Yerba mate"
    assert product.base_unit == "paquete"


def test_product_defaults_minimum_stock_to_zero() -> None:
    """Verifica que el stock mínimo por defecto es cero."""
    product = Product(sku="SKU-001", name="Yerba", base_unit="paquete")

    assert product.minimum_stock == 0


def test_product_accepts_positive_minimum_stock() -> None:
    """Verifica que un stock mínimo positivo se conserva."""
    product = Product(
        sku="SKU-001",
        name="Yerba",
        base_unit="paquete",
        minimum_stock=12,
    )

    assert product.minimum_stock == 12


def test_product_rejects_empty_sku() -> None:
    """Verifica el rechazo de un producto sin SKU."""
    with pytest.raises(ProductError):
        Product(sku="   ", name="Yerba", base_unit="paquete")


def test_product_rejects_empty_name() -> None:
    """Verifica el rechazo de un producto sin nombre."""
    with pytest.raises(ProductError):
        Product(sku="SKU-001", name="", base_unit="paquete")


def test_product_rejects_empty_base_unit() -> None:
    """Verifica el rechazo de un producto sin unidad base."""
    with pytest.raises(ProductError):
        Product(sku="SKU-001", name="Yerba", base_unit="  ")


def test_product_rejects_negative_minimum_stock() -> None:
    """Verifica el rechazo de un stock mínimo negativo."""
    with pytest.raises(QuantityError):
        Product(
            sku="SKU-001",
            name="Yerba",
            base_unit="paquete",
            minimum_stock=-1,
        )


def test_product_rejects_non_integer_minimum_stock() -> None:
    """Verifica el rechazo de un stock mínimo no entero."""
    with pytest.raises(QuantityError):
        Product(
            sku="SKU-001",
            name="Yerba",
            base_unit="paquete",
            minimum_stock=1.5,  # type: ignore[arg-type]
        )


def test_product_sku_cannot_be_reassigned() -> None:
    """Verifica que el SKU de un producto no puede reasignarse."""
    product = Product(sku="SKU-001", name="Yerba", base_unit="paquete")

    with pytest.raises(FrozenInstanceError):
        product.sku = "SKU-999"  # type: ignore[misc]


def test_product_name_cannot_be_reassigned_directly() -> None:
    """Verifica que el nombre no puede cambiarse por asignación directa."""
    product = Product(sku="SKU-001", name="Yerba", base_unit="paquete")

    with pytest.raises(FrozenInstanceError):
        product.name = "Otro"  # type: ignore[misc]


def test_product_with_name_returns_updated_copy() -> None:
    """Verifica que el nombre se actualiza sin cambiar el SKU."""
    product = Product(sku="SKU-001", name="Yerba", base_unit="paquete")

    updated = product.with_name("  Yerba nueva  ")

    assert updated.name == "Yerba nueva"
    assert updated.sku == "SKU-001"
    assert product.name == "Yerba"


def test_product_with_name_rejects_blank_name() -> None:
    """Verifica que el cambio de nombre rechaza un nombre vacío."""
    product = Product(sku="SKU-001", name="Yerba", base_unit="paquete")

    with pytest.raises(ProductError):
        product.with_name("   ")


def test_product_with_minimum_stock_returns_updated_copy() -> None:
    """Verifica que el stock mínimo se actualiza y el original no cambia."""
    product = Product(sku="SKU-001", name="Yerba", base_unit="paquete")

    updated = product.with_minimum_stock(8)

    assert updated.minimum_stock == 8
    assert product.minimum_stock == 0


def test_product_with_minimum_stock_rejects_invalid_value() -> None:
    """Verifica que el cambio de stock mínimo rechaza valores inválidos."""
    product = Product(sku="SKU-001", name="Yerba", base_unit="paquete")

    with pytest.raises(QuantityError):
        product.with_minimum_stock(-2)
