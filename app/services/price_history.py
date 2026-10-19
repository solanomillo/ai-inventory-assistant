"""Servicio de consultas del historial de precios (T034, RF-08).

Expone el historial de cambios de precio de una presentación y de un producto
como una consulta reutilizable por la interfaz. El servicio es de solo lectura:
no crea movimientos, no modifica precios ni saldos y no abre transacciones; el
orden cronológico —`occurred_at` con desempate por `id`— lo aporta el
repositorio del historial (T030), de modo que el servicio no reordena ni filtra
los registros que recibe.

La consulta por presentación exige que la presentación exista y la consulta por
producto exige que el producto exista; un identificador inexistente se informa
con `RecordNotFoundError`. Una entidad existente sin cambios registrados
devuelve una lista vacía, y un producto desactivado admite la consulta de su
historial, porque la desactivación no elimina los datos históricos (RF-01,
RF-08).
"""

from __future__ import annotations

from app.services.ports import (
    PriceChangeRecord,
    PriceHistoryRepository,
    PresentationRecord,
    PresentationRepository,
    ProductRecord,
    ProductRepository,
    RecordNotFoundError,
)


class PriceHistoryService:
    """Consultas de solo lectura del historial de precios (T034)."""

    def __init__(
        self,
        *,
        products: ProductRepository,
        presentations: PresentationRepository,
        price_history: PriceHistoryRepository,
    ) -> None:
        """
        Args:
            products: Repositorio de productos, para verificar que el producto
                exista.
            presentations: Repositorio de presentaciones, para verificar que la
                presentación exista.
            price_history: Repositorio del historial de precios, que resuelve
                las consultas y su orden cronológico.
        """
        self._products = products
        self._presentations = presentations
        self._price_history = price_history

    def get_price_history_by_presentation(
        self, presentation_id: int
    ) -> list[PriceChangeRecord]:
        """
        Devuelve el historial de la presentación, en orden cronológico (RF-08).

        Args:
            presentation_id: Presentación cuyo historial se consulta.

        Returns:
            Los cambios de precio de la presentación, del más antiguo al más
            reciente, o una lista vacía si no tiene ninguno.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
        """
        self._required_presentation(presentation_id)

        return self._price_history.list_by_presentation(presentation_id)

    def get_price_history_by_product(
        self, product_id: int
    ) -> list[PriceChangeRecord]:
        """
        Devuelve el historial de las presentaciones del producto (RF-08).

        Args:
            product_id: Producto cuyo historial se consulta.

        Returns:
            Los cambios de precio de todas las presentaciones del producto, del
            más antiguo al más reciente, o una lista vacía si no hay ninguno.

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        self._required_product(product_id)

        return self._price_history.list_by_product(product_id)

    def _required_presentation(
        self, presentation_id: int
    ) -> PresentationRecord:
        """
        Devuelve la presentación indicada y exige que exista.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
        """
        presentation = self._presentations.get(presentation_id)
        if presentation is None:
            raise RecordNotFoundError(
                f"No existe la presentación con id {presentation_id}."
            )

        return presentation

    def _required_product(self, product_id: int) -> ProductRecord:
        """
        Devuelve el producto indicado y exige que exista.

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        product = self._products.get(product_id)
        if product is None:
            raise RecordNotFoundError(
                f"No existe el producto con id {product_id}."
            )

        return product
