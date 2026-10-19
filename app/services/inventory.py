"""Servicio de consultas de inventario de solo lectura (T019).

Expone las consultas que consumen las herramientas del asistente (RF-01,
RF-04 y RF-05): búsqueda de productos por nombre y por SKU, existencias
(unidades sueltas, presentaciones cerradas y total equivalente),
presentaciones, stock bajo, reposición e historial de movimientos. Además
expone el listado público de productos con su estado (RF-09), que consume la
interfaz de gestión sin usar como API pública la lectura interna del
repositorio.

El servicio es de solo lectura: no abre transacciones de escritura, no invoca
operaciones de alta, actualización ni baja y no modifica el inventario.
Recibe por inyección los contratos de `app/services/ports.py`, de modo que no
depende del motor de persistencia, y delega las reglas derivadas en
`app/domain/stock.py` —total equivalente, stock bajo, faltante y exclusión de
los productos inactivos—, sin reimplementarlas.

Contratos de las consultas:

- La búsqueda por nombre es una coincidencia exacta que no distingue
  mayúsculas de minúsculas y conserva el texto original almacenado; devuelve
  todas las coincidencias, sin seleccionar ninguna, y una lista vacía si no
  hay ninguna (RF-01).
- La búsqueda por SKU devuelve el producto o `None` si no existe (RF-01).
- El listado público de productos incluye los activos y los inactivos, sin
  filtros ni paginación, y hereda el orden de registro del repositorio; el
  filtro de estado corresponde a la interfaz (RF-09).
- Las consultas por producto verifican primero que el producto exista, porque
  la persistencia devuelve presentaciones e historial vacíos para un producto
  inexistente: un `product_id` inexistente se informa con
  `RecordNotFoundError`.
- El período del historial se delega a la persistencia, que interpreta sus
  límites en la hora local de Argentina con la semántica `[inicio, fin)` y
  combina los filtros de producto, tipo y período mediante AND (RF-03); un
  período inválido se informa con el `InvalidPeriodError` existente.
- Solo las consultas globales de stock bajo y de reposición excluyen los
  productos inactivos; el resto admite productos desactivados, que conservan
  consultas e historial (RF-01, RF-04).

Limitaciones del MVP (D5): las consultas no están paginadas. Las consultas
globales recorren todos los productos y leen las presentaciones de cada uno, y
el historial devuelve todos los movimientos que cumplen los filtros, por lo
que el costo de cada consulta crece con el tamaño del inventario.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.domain.movements import MovementKind, validate_movement_kind
from app.domain.products import validate_product_name, validate_sku
from app.domain.stock import (
    ClosedStock,
    calculate_shortage,
    low_stock_items,
    replenishment_items,
    total_equivalent,
)
from app.services.ports import (
    MovementRecord,
    MovementRepository,
    PresentationRecord,
    PresentationRepository,
    ProductRecord,
    ProductRepository,
    RecordNotFoundError,
)


@dataclass(frozen=True)
class ProductStock:
    """
    Existencias vigentes de un producto (RF-02).

    Agrupa el producto, sus presentaciones y el total equivalente derivado.
    Las unidades sueltas son las del producto y el stock cerrado el de cada
    presentación; el total equivalente se calcula con `total_equivalent` y
    `ClosedStock` del dominio y no se persiste.
    """

    product: ProductRecord
    presentations: tuple[PresentationRecord, ...]
    total_equivalent: int


@dataclass(frozen=True)
class StockAlert:
    """
    Producto evaluado para stock bajo o reposición (RF-04).

    `total_equivalent` es el total equivalente derivado del producto y
    `shortage` es su faltante, siempre mayor o igual que cero. La alerta no
    indica por sí misma si el producto tiene stock bajo o requiere reposición:
    esa condición la decide el dominio al filtrar la consulta global.
    """

    product: ProductRecord
    total_equivalent: int
    shortage: int


@dataclass(frozen=True)
class _AlertItem:
    """
    Ítem evaluado para stock bajo y reposición (RF-04).

    Cumple el contrato `StockAlertItem` del dominio: expone `active`,
    `loose_units`, `closed_stocks` y `minimum_stock`, y conserva el producto
    para informarlo en la alerta resultante. No es una entidad de dominio:
    solo reúne los datos persistidos que las reglas globales necesitan.
    """

    product: ProductRecord
    closed_stocks: tuple[ClosedStock, ...]

    @property
    def active(self) -> bool:
        """Indica si el producto admite operaciones ordinarias nuevas."""
        return self.product.active

    @property
    def loose_units(self) -> int:
        """Unidades sueltas del producto, en unidades base."""
        return self.product.loose_units

    @property
    def minimum_stock(self) -> int:
        """Stock mínimo del producto, en unidades base."""
        return self.product.minimum_stock

    def as_alert(self) -> StockAlert:
        """
        Construye la alerta del producto con sus valores derivados (RF-04).

        El total equivalente y el faltante se calculan con las reglas del
        dominio, de modo que la alerta no duplica esos cálculos.
        """
        return StockAlert(
            product=self.product,
            total_equivalent=total_equivalent(
                self.loose_units, self.closed_stocks
            ),
            shortage=calculate_shortage(
                loose_units=self.loose_units,
                closed_stocks=self.closed_stocks,
                minimum_stock=self.minimum_stock,
            ),
        )


class InventoryService:
    """Consultas de solo lectura del inventario (T019)."""

    def __init__(
        self,
        *,
        products: ProductRepository,
        presentations: PresentationRepository,
        movements: MovementRepository,
    ) -> None:
        """
        Args:
            products: Repositorio de productos, para resolver las búsquedas y
                las existencias.
            presentations: Repositorio de presentaciones, para conocer el
                factor y el stock cerrado de cada presentación.
            movements: Repositorio de movimientos, para consultar el historial.
        """
        self._products = products
        self._presentations = presentations
        self._movements = movements

    def find_products_by_name(self, name: str) -> list[ProductRecord]:
        """
        Devuelve los productos cuyo nombre coincide exactamente (RF-01).

        La comparación no distingue mayúsculas de minúsculas y conserva el
        texto original almacenado. Se devuelven todas las coincidencias, sin
        seleccionar ninguna, y una lista vacía si no hay ninguna.

        Args:
            name: Nombre buscado; se recorta antes de compararlo.

        Returns:
            Las coincidencias en orden de registro, o una lista vacía.

        Raises:
            ProductError: Si el nombre no es texto o queda vacío al recortarlo.
        """
        return list(self._products.find_by_name(validate_product_name(name)))

    def find_product_by_sku(self, sku: str) -> ProductRecord | None:
        """
        Devuelve el producto con el SKU indicado, o `None` si no existe
        (RF-01).

        La comparación no distingue mayúsculas de minúsculas y el texto
        original almacenado se conserva sin normalizar.

        Args:
            sku: SKU buscado; se recorta antes de compararlo.

        Returns:
            El producto encontrado, o `None` si el SKU no está registrado.

        Raises:
            ProductError: Si el SKU no es texto o queda vacío al recortarlo.
        """
        return self._products.find_by_sku(validate_sku(sku))

    def list_products(self) -> list[ProductRecord]:
        """
        Devuelve todos los productos registrados, con su estado (RF-09).

        Es una lectura pública del listado completo: incluye los productos
        activos y los inactivos, porque el filtro activo/inactivo corresponde
        a la interfaz, y no aplica ningún filtro ni paginación. Hereda el orden
        de registro del repositorio (por `id`) y no expone su lectura interna.

        Returns:
            Todos los productos en el orden que entrega el repositorio, o una
            lista vacía si el catálogo está vacío.
        """
        return list(self._products.list_all())

    def get_product_stock(self, product_id: int) -> ProductStock:
        """
        Devuelve las existencias vigentes del producto (RF-02).

        El total equivalente se deriva con `total_equivalent` y `ClosedStock`
        del dominio a partir de las unidades sueltas del producto y del stock
        cerrado de cada presentación; no se lee ningún saldo equivalente
        persistido.

        Args:
            product_id: Producto cuyas existencias se consultan.

        Returns:
            El producto, sus presentaciones y su total equivalente.

        Raises:
            RecordNotFoundError: Si el producto no existe.
            QuantityError: Si un saldo almacenado no es un entero mayor o
                igual que cero.
        """
        product = self._required_product(product_id)
        presentations = tuple(self._presentations.list_by_product(product.id))

        return ProductStock(
            product=product,
            presentations=presentations,
            total_equivalent=total_equivalent(
                product.loose_units,
                self._closed_stocks(presentations),
            ),
        )

    def get_product_presentations(
        self, product_id: int
    ) -> tuple[PresentationRecord, ...]:
        """
        Devuelve las presentaciones del producto (RF-07).

        Cada presentación informa su factor —la equivalencia en unidades
        base— y su stock cerrado, de modo que el asistente pueda distinguir
        las conversiones vigentes.

        Args:
            product_id: Producto cuyas presentaciones se consultan.

        Returns:
            Las presentaciones del producto, en orden de registro, o una tupla
            vacía si el producto no tiene ninguna.

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        product = self._required_product(product_id)

        return tuple(self._presentations.list_by_product(product.id))

    def get_low_stock_products(self) -> list[StockAlert]:
        """
        Devuelve los productos activos con stock bajo (RF-04).

        La condición `total_equivalente <= stock_minimo` y la exclusión de los
        productos inactivos las aplica `low_stock_items` del dominio; cada
        alerta informa además el total equivalente y el faltante derivados.

        Returns:
            Las alertas de los productos activos con stock bajo, en orden de
            registro, o una lista vacía si no hay ninguno.

        Raises:
            QuantityError: Si un saldo almacenado no es un entero mayor o
                igual que cero.
        """
        return [
            item.as_alert() for item in low_stock_items(self._alert_items())
        ]

    def get_replenishment_products(self) -> list[StockAlert]:
        """
        Devuelve los productos activos que requieren reposición (RF-04).

        Solo se incluyen los productos activos cuyo faltante es mayor que
        cero, condición que aplica `replenishment_items` del dominio; un
        producto con stock bajo y faltante cero no requiere reposición.

        Returns:
            Las alertas de los productos activos con faltante mayor que cero,
            en orden de registro, o una lista vacía si no hay ninguno.

        Raises:
            QuantityError: Si un saldo almacenado no es un entero mayor o
                igual que cero.
        """
        return [
            item.as_alert()
            for item in replenishment_items(self._alert_items())
        ]

    def get_movement_history(
        self,
        product_id: int,
        kind: MovementKind | str | None = None,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> list[MovementRecord]:
        """
        Devuelve el historial de movimientos del producto (RF-03).

        Los filtros de tipo y período se combinan mediante AND y el período
        aplica la semántica `[inicio, fin)`; sus límites se interpretan en la
        hora local de Argentina cuando no incluyen zona horaria. La
        persistencia resuelve ambos filtros. Un producto desactivado conserva
        su historial. La consulta no pagina y devuelve todos los movimientos
        que cumplen los filtros, en orden cronológico.

        Args:
            product_id: Producto cuyo historial se consulta.
            kind: Tipo de movimiento que se filtra, o `None` para no
                filtrarlo.
            period_start: Inicio del período, inclusivo, o `None` para no
                acotarlo.
            period_end: Final del período, exclusivo, o `None` para no
                acotarlo.

        Returns:
            Los movimientos que cumplen todos los filtros indicados.

        Raises:
            MovementError: Si el tipo de movimiento no es un tipo conocido.
            RecordNotFoundError: Si el producto no existe.
            InvalidPeriodError: Si el período no es válido.
        """
        validated_kind = None if kind is None else validate_movement_kind(kind)
        product = self._required_product(product_id)

        return self._movements.list_by_product(
            product.id,
            validated_kind,
            period_start=period_start,
            period_end=period_end,
        )

    def _alert_items(self) -> list[_AlertItem]:
        """
        Reúne los productos con sus saldos cerrados para evaluar alertas
        (RF-04).

        Cada ítem cumple el contrato `StockAlertItem` del dominio, de modo que
        las reglas de stock bajo y de faltante se delegan sin crear entidades
        nuevas.
        """
        return [
            _AlertItem(
                product=product,
                closed_stocks=self._closed_stocks(
                    self._presentations.list_by_product(product.id)
                ),
            )
            for product in self._products.list_all()
        ]

    @staticmethod
    def _closed_stocks(
        presentations: Iterable[PresentationRecord],
    ) -> tuple[ClosedStock, ...]:
        """
        Deriva el stock cerrado de cada presentación (RF-02).

        El saldo cerrado se expresa con `ClosedStock` del dominio, que valida
        el factor y la cantidad y aporta su equivalencia en unidades base.
        """
        return tuple(
            ClosedStock(
                factor=presentation.factor,
                quantity=presentation.closed_stock,
            )
            for presentation in presentations
        )

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
