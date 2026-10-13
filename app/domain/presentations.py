"""Reglas de dominio de las presentaciones de un producto (RF-07)."""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal

from app.domain.products import normalize_required_text
from app.domain.quantities import (
    validate_factor,
    validate_price,
    validate_stock,
)


class PresentationError(ValueError):
    """Error relacionado con una presentación inválida."""


class DuplicatePresentationNameError(PresentationError):
    """Error que indica que el nombre ya existe dentro del producto."""


class FactorNotMutableError(PresentationError):
    """Error que indica que el factor no puede modificarse."""


def validate_presentation_name(name: str) -> str:
    """
    Recorta el nombre de la presentación y verifica que no quede vacío (RF-07).

    Args:
        name: Nombre informado para la presentación.

    Returns:
        El nombre sin espacios iniciales ni finales.

    Raises:
        PresentationError:
            Si el nombre no es texto o queda vacío después de recortarlo.
    """
    return normalize_required_text(name, "nombre", PresentationError)


def validate_presentation_name_unique(
    name: str,
    existing_names: Iterable[str],
) -> str:
    """
    Verifica que el nombre no se repita dentro del producto (RF-07).

    La comparación no distingue mayúsculas de minúsculas, de modo que un
    nombre que difiera solo en el caso también se considera duplicado. El
    texto original recortado se conserva como valor devuelto, sin normalizar
    el caso.

    Args:
        name: Nombre informado para la presentación.
        existing_names: Nombres de las presentaciones ya registradas.

    Returns:
        El nombre recortado y validado, con su texto original.

    Raises:
        PresentationError:
            Si el nombre no es texto o queda vacío después de recortarlo.
        DuplicatePresentationNameError:
            Si el nombre ya existe dentro del producto, aunque difiera solo en
            mayúsculas o minúsculas.
    """
    normalized = validate_presentation_name(name)
    existing_caseless = {existing.casefold() for existing in existing_names}
    if normalized.casefold() in existing_caseless:
        raise DuplicatePresentationNameError(
            f"La presentación '{normalized}' ya existe en el producto."
        )

    return normalized


def validate_single_base_presentation(
    presentations: Iterable["Presentation"],
) -> "Presentation":
    """
    Verifica que el producto tenga exactamente una presentación base (RF-07).

    La presentación base es la que equivale a la unidad base, es decir, la que
    tiene factor 1.

    Args:
        presentations: Presentaciones del producto.

    Returns:
        La presentación base del producto.

    Raises:
        PresentationError:
            Si no existe ninguna presentación base o existe más de una.
    """
    base_presentations = [
        presentation for presentation in presentations if presentation.is_base
    ]
    if len(base_presentations) != 1:
        raise PresentationError(
            "El producto debe tener exactamente una presentación base "
            "con factor 1."
        )

    return base_presentations[0]


@dataclass(frozen=True)
class Presentation:
    """
    Presentación de un producto (RF-07).

    El nombre se recorta y se valida al construir el objeto. `has_history` es
    obligatorio y debe informarse explícitamente: no existe un valor por
    defecto que habilite el cambio de factor por omisión. `closed_stock` y
    `has_history` describen el estado vigente de la presentación y determinan
    si su factor puede modificarse.
    """

    name: str
    factor: int
    sale_price: Decimal
    has_history: bool
    closed_stock: int = 0

    def __post_init__(self) -> None:
        """Recorta y valida los campos de la presentación."""
        object.__setattr__(
            self,
            "name",
            validate_presentation_name(self.name),
        )
        object.__setattr__(self, "factor", validate_factor(self.factor))
        object.__setattr__(self, "sale_price", validate_price(self.sale_price))
        object.__setattr__(
            self,
            "closed_stock",
            validate_stock(self.closed_stock),
        )

    @property
    def is_base(self) -> bool:
        """Indica si la presentación equivale a la unidad base (factor 1)."""
        return self.factor == 1

    @classmethod
    def create_base(cls, name: str, sale_price: Decimal) -> "Presentation":
        """
        Crea la presentación base del producto, con factor 1 (RF-07).

        La presentación base nace sin stock cerrado y sin movimientos
        históricos; el historial se informa de forma explícita como `False`.

        Args:
            name: Nombre de la presentación base.
            sale_price: Precio de venta de la presentación base.

        Returns:
            La presentación base, sin stock cerrado ni historial.

        Raises:
            PresentationError:
                Si el nombre no es texto o queda vacío después de recortarlo.
            QuantityError:
                Si el precio no es un Decimal finito mayor que cero.
        """
        return cls(
            name=name,
            factor=1,
            sale_price=sale_price,
            has_history=False,
        )

    def change_factor(self, factor: int) -> "Presentation":
        """
        Devuelve la presentación con el factor actualizado (RF-07).

        La presentación base no puede dejar de ser base: si esta presentación
        tiene factor 1, el único factor admisible es 1. La decisión de
        mutabilidad es explícita y conservadora: `has_history` debe informarse
        al construir la presentación, así que la omisión no habilita el cambio
        de factor ante movimientos históricos.

        Args:
            factor: Factor nuevo de equivalencia en unidades base.

        Returns:
            Una nueva instancia con el factor actualizado.

        Raises:
            FactorNotMutableError:
                Si la presentación es base y el factor solicitado no es 1, o
                si tiene stock cerrado o movimientos históricos.
            QuantityError:
                Si el factor no es un entero o es menor que uno.
        """
        validated_factor = validate_factor(factor)

        if self.is_base and validated_factor != 1:
            raise FactorNotMutableError(
                f"La presentación base '{self.name}' debe conservar el "
                "factor 1."
            )

        if self.closed_stock > 0 or self.has_history:
            raise FactorNotMutableError(
                f"El factor de la presentación '{self.name}' no puede "
                "modificarse porque tiene stock cerrado o movimientos "
                "históricos."
            )

        return replace(self, factor=validated_factor)
