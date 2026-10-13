"""Reglas de dominio de los productos (RF-01)."""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from app.domain.quantities import validate_stock


class ProductError(ValueError):
    """Error relacionado con un producto inválido."""


class DuplicateSkuError(ProductError):
    """Error que indica que el SKU ya está registrado."""


class ImmutableSkuError(ProductError):
    """Error que indica un intento de modificar el SKU de un producto."""


def normalize_required_text(
    value: str,
    field_name: str,
    error_type: type[ValueError] = ProductError,
) -> str:
    """
    Recorta un campo de texto obligatorio y verifica que no quede vacío
    (RF-01).

    El módulo de presentaciones reutiliza esta función con su propio error.

    Args:
        value: Texto informado para el campo.
        field_name: Nombre del campo, usado en el mensaje de error.
        error_type: Excepción que se lanza ante un valor inválido.

    Returns:
        El texto sin espacios iniciales ni finales.

    Raises:
        ValueError:
            Si el valor no es texto o queda vacío después de recortarlo.
    """
    if not isinstance(value, str):
        raise error_type(f"El campo '{field_name}' debe ser texto.")

    normalized = value.strip()
    if not normalized:
        raise error_type(f"El campo '{field_name}' no puede estar vacío.")

    return normalized


def validate_sku(sku: str) -> str:
    """
    Recorta el SKU y verifica que no quede vacío.

    Args:
        sku: SKU informado para el producto.

    Returns:
        El SKU sin espacios iniciales ni finales.

    Raises:
        ProductError:
            Si el SKU no es texto o queda vacío después de recortarlo.
    """
    return normalize_required_text(sku, "sku")


def validate_product_name(name: str) -> str:
    """
    Recorta el nombre del producto y verifica que no quede vacío.

    Args:
        name: Nombre informado para el producto.

    Returns:
        El nombre sin espacios iniciales ni finales.

    Raises:
        ProductError:
            Si el nombre no es texto o queda vacío después de recortarlo.
    """
    return normalize_required_text(name, "nombre")


def validate_base_unit(base_unit: str) -> str:
    """
    Recorta la unidad base y verifica que no quede vacía.

    Args:
        base_unit: Unidad base informada para el producto.

    Returns:
        La unidad base sin espacios iniciales ni finales.

    Raises:
        ProductError:
            Si la unidad base no es texto o queda vacía después de recortarla.
    """
    return normalize_required_text(base_unit, "unidad base")


def validate_minimum_stock(minimum_stock: int) -> int:
    """
    Valida el stock mínimo de un producto.

    Args:
        minimum_stock: Stock mínimo expresado en unidades base.

    Returns:
        El stock mínimo validado.

    Raises:
        QuantityError:
            Si el stock mínimo no es un entero o es menor que cero.
    """
    return validate_stock(minimum_stock)


def validate_sku_unique(sku: str, existing_skus: Iterable[str]) -> str:
    """
    Verifica que el SKU no esté registrado en otro producto (RF-01).

    La comparación no distingue mayúsculas de minúsculas, de modo que un SKU
    que difiera solo en el caso también se considera duplicado. El texto
    original recortado se conserva como valor devuelto, sin normalizar el caso.

    Args:
        sku: SKU informado para el producto.
        existing_skus: SKU de los productos ya registrados.

    Returns:
        El SKU recortado y validado, con su texto original.

    Raises:
        ProductError:
            Si el SKU no es texto o queda vacío después de recortarlo.
        DuplicateSkuError:
            Si el SKU ya existe, aunque difiera solo en mayúsculas o
            minúsculas.
    """
    normalized = validate_sku(sku)
    existing_caseless = {existing.casefold() for existing in existing_skus}
    if normalized.casefold() in existing_caseless:
        raise DuplicateSkuError(f"El SKU '{normalized}' ya existe.")

    return normalized


def validate_sku_unchanged(current_sku: str, requested_sku: str) -> str:
    """
    Verifica que el SKU de un producto no se modifique (RF-01).

    Args:
        current_sku: SKU vigente del producto.
        requested_sku: SKU solicitado para el producto.

    Returns:
        El SKU vigente del producto.

    Raises:
        ProductError:
            Si el SKU solicitado no es texto o queda vacío después de
            recortarlo.
        ImmutableSkuError:
            Si el SKU solicitado difiere del vigente.
    """
    if validate_sku(requested_sku) != current_sku:
        raise ImmutableSkuError(
            f"El SKU '{current_sku}' no puede modificarse."
        )

    return current_sku


@dataclass(frozen=True)
class Product:
    """
    Producto del inventario con su información mínima (RF-01).

    Al construir el objeto, los campos de texto obligatorios se recortan y se
    validan. El SKU es inmutable: la instancia es inmutable y cualquier intento
    de modificarlo se rechaza con `validate_sku_unchanged`.
    """

    sku: str
    name: str
    base_unit: str
    minimum_stock: int = 0

    def __post_init__(self) -> None:
        """Recorta y valida los campos del producto."""
        object.__setattr__(self, "sku", validate_sku(self.sku))
        object.__setattr__(self, "name", validate_product_name(self.name))
        object.__setattr__(
            self,
            "base_unit",
            validate_base_unit(self.base_unit),
        )
        object.__setattr__(
            self,
            "minimum_stock",
            validate_minimum_stock(self.minimum_stock),
        )

    def with_name(self, name: str) -> "Product":
        """
        Devuelve el producto con el nombre actualizado (RF-01).

        Args:
            name: Nombre nuevo del producto.

        Returns:
            Una nueva instancia con el nombre actualizado y el mismo SKU.

        Raises:
            ProductError:
                Si el nombre no es texto o queda vacío después de recortarlo.
        """
        return replace(self, name=name)

    def with_minimum_stock(self, minimum_stock: int) -> "Product":
        """
        Devuelve el producto con el stock mínimo actualizado (RF-01).

        Args:
            minimum_stock: Stock mínimo nuevo, en unidades base.

        Returns:
            Una nueva instancia con el stock mínimo actualizado.

        Raises:
            QuantityError:
                Si el stock mínimo no es un entero o es menor que cero.
        """
        return replace(self, minimum_stock=minimum_stock)
