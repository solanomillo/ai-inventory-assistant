"""Herramientas LangChain de consulta del inventario (T019).

Expone las consultas de solo lectura que el Inventory Agent ofrece al modelo
(RF-01, RF-02, RF-03, RF-04 y RF-05): búsqueda de productos por nombre y por
SKU, existencias, presentaciones, stock bajo, reposición e historial de
movimientos.

Cada herramienta es un adaptador delgado: declara su entrada con un esquema
Pydantic (validación en el límite, D3), delega en el servicio de consultas
`app.services.inventory` y devuelve un contrato de salida Pydantic explícito
(D4). Las herramientas no acceden a la persistencia, no importan ni
instancian repositorios de infraestructura y no capturan los errores tipados
del servicio: un `RecordNotFoundError`, un `MovementError` o un
`InvalidPeriodError` se propagan tal como los define el dominio o los
contratos de los repositorios.

Las herramientas tampoco escriben: no invocan ninguna operación de alta,
actualización o baja y no abren transacciones. Están pensadas para
construirse con `create_inventory_tools(service)`, que inyecta el servicio de
consultas; se invocan sin LLM, sin API externa y sin grafo.

Limitaciones del MVP (D5): ninguna herramienta pagina sus resultados; las
consultas globales y el historial devuelven todas las coincidencias.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, ConfigDict, Field

from app.domain.movements import MovementKind
from app.domain.stock import ClosedStock
from app.services.inventory import InventoryService, ProductStock, StockAlert
from app.services.ports import (
    MovementRecord,
    PresentationRecord,
    ProductRecord,
)


class FindProductsByNameInput(BaseModel):
    """Entrada de la búsqueda de productos por nombre (RF-01)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(
        min_length=1,
        description=(
            "Nombre del producto a buscar. La coincidencia es exacta y no "
            "distingue mayúsculas de minúsculas."
        ),
    )


class FindProductBySkuInput(BaseModel):
    """Entrada de la búsqueda de un producto por SKU (RF-01)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sku: str = Field(
        min_length=1,
        description=(
            "SKU del producto a buscar. La coincidencia no distingue "
            "mayúsculas de minúsculas."
        ),
    )


class ProductIdInput(BaseModel):
    """
    Entrada de las consultas que identifican un producto por su id
    (RF-02, RF-07).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    product_id: int = Field(
        gt=0,
        description="Identificador interno del producto.",
    )


class MovementHistoryInput(BaseModel):
    """Entrada del historial de movimientos de un producto (RF-03)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    product_id: int = Field(
        gt=0,
        description="Identificador interno del producto.",
    )
    kind: MovementKind | None = Field(
        default=None,
        description=(
            "Tipo de movimiento que se filtra, o `null` para no filtrarlo."
        ),
    )
    period_start: datetime | None = Field(
        default=None,
        description=(
            "Inicio del período, inclusivo. Se interpreta en la hora local "
            "de Argentina cuando no incluye zona horaria."
        ),
    )
    period_end: datetime | None = Field(
        default=None,
        description=(
            "Final del período, exclusivo. Se interpreta en la hora local "
            "de Argentina cuando no incluye zona horaria."
        ),
    )


class NoInput(BaseModel):
    """
    Entrada vacía de las consultas globales que no reciben argumentos
    (RF-04).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


class ProductSummary(BaseModel):
    """
    Producto identificado, sin sus saldos ni sus presentaciones (RF-01).

    Es el contrato con el que las consultas por nombre y por SKU informan las
    coincidencias encontradas: identifica el producto y conserva su texto
    original almacenado. El stock se consulta con la herramienta de
    existencias.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    sku: str
    name: str
    base_unit: str
    active: bool

    @classmethod
    def from_record(cls, record: ProductRecord) -> "ProductSummary":
        """Adapta el producto persistido al contrato de salida."""
        return cls(
            id=record.id,
            sku=record.sku,
            name=record.name,
            base_unit=record.base_unit,
            active=record.active,
        )


class ClosedStockSummary(BaseModel):
    """
    Stock cerrado de una presentación con su equivalencia (RF-02).

    `equivalent_units` es el aporte de esa presentación al total equivalente.
    """

    model_config = ConfigDict(frozen=True)

    presentation_id: int
    name: str
    factor: int
    closed_stock: int
    equivalent_units: int

    @classmethod
    def from_record(
        cls, presentation: PresentationRecord
    ) -> "ClosedStockSummary":
        """
        Adapta el stock cerrado de una presentación al contrato de salida.

        La equivalencia se delega en `ClosedStock` del dominio, de modo que no
        se recalcula aquí.
        """
        closed_stock = ClosedStock(
            factor=presentation.factor,
            quantity=presentation.closed_stock,
        )

        return cls(
            presentation_id=presentation.id,
            name=presentation.name,
            factor=closed_stock.factor,
            closed_stock=closed_stock.quantity,
            equivalent_units=closed_stock.equivalent_units,
        )


class ProductStockSummary(BaseModel):
    """
    Existencias de un producto (RF-02).

    Informa las unidades sueltas, el stock cerrado de cada presentación con su
    factor y el total equivalente derivado.
    """

    model_config = ConfigDict(frozen=True)

    product: ProductSummary
    loose_units: int
    closed_stocks: list[ClosedStockSummary]
    total_equivalent: int

    @classmethod
    def from_stock(cls, stock: ProductStock) -> "ProductStockSummary":
        """Adapta las existencias del servicio al contrato de salida."""
        return cls(
            product=ProductSummary.from_record(stock.product),
            loose_units=stock.product.loose_units,
            closed_stocks=[
                ClosedStockSummary.from_record(presentation)
                for presentation in stock.presentations
            ],
            total_equivalent=stock.total_equivalent,
        )


class PresentationSummary(BaseModel):
    """
    Presentación de un producto (RF-07).

    `factor` expresa la equivalencia en unidades base y `closed_stock` su
    stock cerrado vigente.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    product_id: int
    name: str
    factor: int
    sale_price: Decimal
    closed_stock: int

    @classmethod
    def from_record(
        cls, record: PresentationRecord
    ) -> "PresentationSummary":
        """Adapta la presentación persistida al contrato de salida."""
        return cls(
            id=record.id,
            product_id=record.product_id,
            name=record.name,
            factor=record.factor,
            sale_price=record.sale_price,
            closed_stock=record.closed_stock,
        )


class StockAlertSummary(BaseModel):
    """
    Producto informado en una consulta global de alertas (RF-04).

    Acompaña el producto con el total equivalente derivado, su stock mínimo y
    su faltante; el faltante es cero cuando las existencias alcanzan el
    mínimo.
    """

    model_config = ConfigDict(frozen=True)

    product: ProductSummary
    total_equivalent: int
    minimum_stock: int
    shortage: int

    @classmethod
    def from_alert(cls, alert: StockAlert) -> "StockAlertSummary":
        """Adapta la alerta del servicio al contrato de salida."""
        return cls(
            product=ProductSummary.from_record(alert.product),
            total_equivalent=alert.total_equivalent,
            minimum_stock=alert.product.minimum_stock,
            shortage=alert.shortage,
        )


class MovementSummary(BaseModel):
    """
    Movimiento confirmado del historial de un producto (RF-03).

    Conserva los datos históricos del movimiento, incluidos los contadores
    FIFO de las entradas cerradas y el vínculo con el movimiento original de
    una compensación.
    """

    model_config = ConfigDict(frozen=True)

    id: int
    product_id: int
    presentation_id: int
    kind: MovementKind
    quantity: int
    reason: str
    occurred_at: datetime
    cost: Decimal | None
    applied_price: Decimal | None
    is_initial_stock: bool
    original_movement_id: int | None
    consumed_quantity: int
    compensated_quantity: int

    @classmethod
    def from_record(cls, record: MovementRecord) -> "MovementSummary":
        """Adapta el movimiento persistido al contrato de salida."""
        return cls(
            id=record.id,
            product_id=record.product_id,
            presentation_id=record.presentation_id,
            kind=record.kind,
            quantity=record.quantity,
            reason=record.reason,
            occurred_at=record.occurred_at,
            cost=record.cost,
            applied_price=record.applied_price,
            is_initial_stock=record.is_initial_stock,
            original_movement_id=record.original_movement_id,
            consumed_quantity=record.consumed_quantity,
            compensated_quantity=record.compensated_quantity,
        )


def create_inventory_tools(service: InventoryService) -> tuple[BaseTool, ...]:
    """
    Construye las herramientas LangChain de consulta del inventario (T019).

    Cada herramienta valida su entrada con su esquema Pydantic, delega en el
    servicio de consultas y devuelve un contrato de salida Pydantic. Los
    errores tipados del servicio se propagan sin transformarse en respuestas
    vacías.

    Args:
        service: Servicio de consultas del inventario. Las herramientas solo
            leen a través de él y no acceden a la persistencia.

    Returns:
        Las herramientas de consulta, listas para enlazarse a un modelo.
    """

    @tool(args_schema=FindProductsByNameInput)
    def find_products_by_name(name: str) -> list[ProductSummary]:
        """Devuelve los productos cuyo nombre coincide exactamente.

        La comparación no distingue mayúsculas de minúsculas y conserva el
        texto original almacenado. Devuelve todas las coincidencias, sin
        seleccionar ninguna, y una lista vacía si no hay ninguna (RF-01).
        """
        return [
            ProductSummary.from_record(record)
            for record in service.find_products_by_name(name)
        ]

    @tool(args_schema=FindProductBySkuInput)
    def find_product_by_sku(sku: str) -> ProductSummary | None:
        """Devuelve el producto con el SKU indicado, o `null` si no existe.

        La comparación no distingue mayúsculas de minúsculas y conserva el
        texto original almacenado (RF-01).
        """
        record = service.find_product_by_sku(sku)
        if record is None:
            return None

        return ProductSummary.from_record(record)

    @tool(args_schema=ProductIdInput)
    def get_product_stock(product_id: int) -> ProductStockSummary:
        """Devuelve las existencias reales del producto.

        Informa las unidades sueltas, el stock cerrado de cada presentación
        con su factor y el total equivalente derivado
        `sueltas + Σ(cerradas × factor)` (RF-02).
        """
        return ProductStockSummary.from_stock(
            service.get_product_stock(product_id)
        )

    @tool(args_schema=ProductIdInput)
    def get_product_presentations(
        product_id: int,
    ) -> list[PresentationSummary]:
        """Devuelve las presentaciones del producto.

        Cada presentación informa su factor —la equivalencia en unidades
        base—, su precio de venta y su stock cerrado vigente, de modo que
        puedan distinguirse las conversiones (RF-07).
        """
        return [
            PresentationSummary.from_record(presentation)
            for presentation in service.get_product_presentations(product_id)
        ]

    @tool(args_schema=NoInput)
    def get_low_stock_products() -> list[StockAlertSummary]:
        """Devuelve los productos activos con stock bajo.

        Un producto tiene stock bajo cuando su total equivalente es menor o
        igual que su stock mínimo. Los productos desactivados se excluyen
        (RF-04).
        """
        return [
            StockAlertSummary.from_alert(alert)
            for alert in service.get_low_stock_products()
        ]

    @tool(args_schema=NoInput)
    def get_replenishment_products() -> list[StockAlertSummary]:
        """Devuelve los productos activos que requieren reposición.

        Se informan los productos activos cuyo faltante es mayor que cero,
        con su stock mínimo, su total equivalente y el faltante
        `max(0, mínimo − total)`. El faltante cero no requiere reposición
        (RF-04).
        """
        return [
            StockAlertSummary.from_alert(alert)
            for alert in service.get_replenishment_products()
        ]

    @tool(args_schema=MovementHistoryInput)
    def get_movement_history(
        product_id: int,
        kind: MovementKind | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> list[MovementSummary]:
        """Devuelve el historial de movimientos del producto.

        Los filtros de tipo y período se combinan: solo se devuelven los
        movimientos que cumplen todos los filtros indicados. El período
        incluye su inicio y excluye su final, y sus límites se interpretan en
        la hora local de Argentina cuando no incluyen zona horaria. Sin
        filtros se devuelve el historial completo (RF-03).
        """
        return [
            MovementSummary.from_record(record)
            for record in service.get_movement_history(
                product_id,
                kind,
                period_start=period_start,
                period_end=period_end,
            )
        ]

    return (
        find_products_by_name,
        find_product_by_sku,
        get_product_stock,
        get_product_presentations,
        get_low_stock_products,
        get_replenishment_products,
        get_movement_history,
    )
