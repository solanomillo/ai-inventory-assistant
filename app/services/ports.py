"""Contratos de persistencia de los servicios de aplicación (T009).

Define los `Protocol` de repositorio que consumen los servicios y las
estructuras de datos que estos devuelven. La infraestructura SQLite
(`app/infrastructure/sqlite/repositories.py`) implementa los contratos, de modo
que los servicios dependen de estas interfaces y no del motor de persistencia.

Los contratos ofrecen altas y consultas, además de la actualización de los
datos vigentes de productos y presentaciones. Ninguno permite editar ni
eliminar movimientos confirmados (RF-03 y RF-06): toda corrección de un
movimiento se registra como una compensación.

Los contratos no repiten las reglas del dominio: reciben valores ya validados y
solo fallan ante las restricciones de la persistencia. Las consultas del
historial de movimientos aceptan un período opcional (T010): sus límites se
interpretan en la hora local de Argentina (`America/Argentina/Buenos_Aires`) y
se convierten a UTC antes de consultar, con la semántica `[inicio, fin)`, es
decir, el inicio es inclusivo y el final es exclusivo. Los filtros de producto,
tipo y período se combinan mediante AND (RF-03).

Un período no válido se informa con `InvalidPeriodError`, que la persistencia
define como subclase de `ValueError`. El contrato nombra esa excepción para
documentarla sin importar `app/infrastructure`, de modo que los servicios
puedan tratarla como `ValueError` sin acoplarse al motor de persistencia
(constitución, §3).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from app.domain.movements import MovementKind


class RecordNotFoundError(LookupError):
    """Error que indica que el registro consultado no existe."""


@dataclass(frozen=True)
class ProductRecord:
    """
    Producto persistido con su identificador y sus saldos sueltos (RF-01).

    Conserva el texto original recortado de `sku`, `name` y `base_unit`;
    `active` indica si el producto admite operaciones ordinarias nuevas.
    """

    id: int
    sku: str
    name: str
    base_unit: str
    minimum_stock: int
    loose_units: int
    active: bool


@dataclass(frozen=True)
class PresentationRecord:
    """
    Presentación persistida de un producto (RF-07).

    `factor` es la equivalencia en unidades base, `sale_price` es el precio de
    venta vigente como `Decimal` exacto y `closed_stock` es el saldo de
    presentaciones cerradas.
    """

    id: int
    product_id: int
    name: str
    factor: int
    sale_price: Decimal
    closed_stock: int


@dataclass(frozen=True)
class MovementRecord:
    """
    Movimiento confirmado del inventario (RF-03).

    `occurred_at` es el instante del movimiento con zona horaria (UTC). `cost`
    y `applied_price` son opcionales y se representan con `Decimal` exacto.
    `consumed_quantity` y `compensated_quantity` son los contadores FIFO de las
    entradas cerradas.
    """

    id: int
    product_id: int
    presentation_id: int
    kind: MovementKind
    quantity: int
    reason: str
    occurred_at: datetime
    cost: Decimal | None = None
    applied_price: Decimal | None = None
    is_initial_stock: bool = False
    original_movement_id: int | None = None
    consumed_quantity: int = 0
    compensated_quantity: int = 0


class ProductRepository(Protocol):
    """Contrato de persistencia de los productos (RF-01)."""

    def add(
        self,
        *,
        sku: str,
        name: str,
        base_unit: str,
        minimum_stock: int = 0,
        loose_units: int = 0,
        active: bool = True,
    ) -> ProductRecord:
        """
        Registra un producto y devuelve su versión persistida.

        Raises:
            DuplicateSkuError: Si el SKU ya está registrado, sin distinguir
                mayúsculas de minúsculas.
            sqlite3.IntegrityError: Si el producto incumple las restricciones
                del esquema.
        """
        ...

    def get(self, product_id: int) -> ProductRecord | None:
        """Devuelve el producto indicado o `None` si no existe."""
        ...

    def find_by_sku(self, sku: str) -> ProductRecord | None:
        """
        Busca un producto por SKU, sin distinguir mayúsculas de minúsculas.

        El texto original almacenado se conserva sin normalizar.
        """
        ...

    def find_by_name(self, name: str) -> list[ProductRecord]:
        """
        Busca productos por nombre exacto, sin distinguir mayúsculas.

        Devuelve todas las coincidencias, sin seleccionar ninguna
        automáticamente, y una lista vacía si no hay ninguna.
        """
        ...

    def list_all(self) -> list[ProductRecord]:
        """Devuelve todos los productos registrados."""
        ...

    def set_name(self, product_id: int, name: str) -> ProductRecord:
        """
        Actualiza el nombre del producto y devuelve su versión persistida.

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        ...

    def set_minimum_stock(
        self, product_id: int, minimum_stock: int
    ) -> ProductRecord:
        """
        Actualiza el stock mínimo del producto, en unidades base.

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        ...

    def set_loose_units(
        self, product_id: int, loose_units: int
    ) -> ProductRecord:
        """
        Actualiza las unidades sueltas del producto.

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        ...

    def set_active(self, product_id: int, active: bool) -> ProductRecord:
        """
        Activa o desactiva el producto conservando su historial (RF-01).

        Raises:
            RecordNotFoundError: Si el producto no existe.
        """
        ...


class PresentationRepository(Protocol):
    """Contrato de persistencia de las presentaciones (RF-07)."""

    def add(
        self,
        *,
        product_id: int,
        name: str,
        factor: int,
        sale_price: Decimal,
        closed_stock: int = 0,
    ) -> PresentationRecord:
        """
        Registra una presentación y devuelve su versión persistida.

        Raises:
            DuplicatePresentationNameError: Si el nombre ya existe dentro del
                producto, sin distinguir mayúsculas de minúsculas.
            sqlite3.IntegrityError: Si la presentación incumple las
                restricciones del esquema.
        """
        ...

    def get(self, presentation_id: int) -> PresentationRecord | None:
        """Devuelve la presentación indicada o `None` si no existe."""
        ...

    def list_by_product(self, product_id: int) -> list[PresentationRecord]:
        """Devuelve las presentaciones del producto indicado."""
        ...

    def find_by_name(
        self, product_id: int, name: str
    ) -> PresentationRecord | None:
        """
        Busca una presentación por nombre dentro del producto.

        La comparación no distingue mayúsculas de minúsculas y el texto
        original almacenado se conserva sin normalizar.
        """
        ...

    def set_sale_price(
        self, presentation_id: int, sale_price: Decimal
    ) -> PresentationRecord:
        """
        Actualiza el precio de venta de la presentación.

        Las salidas ya confirmadas conservan su precio aplicado (RF-07).

        Raises:
            RecordNotFoundError: Si la presentación no existe.
        """
        ...

    def set_closed_stock(
        self, presentation_id: int, closed_stock: int
    ) -> PresentationRecord:
        """
        Actualiza el saldo de presentaciones cerradas.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
        """
        ...

    def set_factor(
        self, presentation_id: int, factor: int
    ) -> PresentationRecord:
        """
        Actualiza el factor de equivalencia de la presentación.

        La decisión de si el factor puede modificarse corresponde al dominio
        (RF-07); el repositorio solo persiste el valor indicado.

        Raises:
            RecordNotFoundError: Si la presentación no existe.
        """
        ...


class MovementRepository(Protocol):
    """
    Contrato de persistencia de los movimientos (RF-03).

    Solo permite registrar movimientos y consultarlos: no expone ninguna
    operación para editar ni eliminar movimientos confirmados.
    """

    def add(
        self,
        *,
        product_id: int,
        presentation_id: int,
        kind: MovementKind,
        quantity: int,
        reason: str,
        occurred_at: datetime,
        cost: Decimal | None = None,
        applied_price: Decimal | None = None,
        is_initial_stock: bool = False,
        original_movement_id: int | None = None,
    ) -> MovementRecord:
        """
        Registra un movimiento confirmado y devuelve su versión persistida.

        `occurred_at` debe ser un instante con zona horaria; se almacena en
        UTC.

        Raises:
            sqlite3.IntegrityError: Si el movimiento incumple las restricciones
                del esquema, por ejemplo un motivo vacío.
            ValueError: Si el instante no incluye zona horaria.
        """
        ...

    def get(self, movement_id: int) -> MovementRecord | None:
        """Devuelve el movimiento indicado o `None` si no existe."""
        ...

    def list_by_product(
        self,
        product_id: int,
        kind: MovementKind | None = None,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> list[MovementRecord]:
        """
        Devuelve el historial del producto, en orden cronológico.

        Cuando se indica `kind`, solo se devuelven los movimientos de ese tipo.
        Los filtros indicados se combinan mediante AND: el período acota
        `occurred_at` con el inicio inclusivo y el final exclusivo, y sus
        límites se interpretan en la hora local de Argentina y se convierten a
        UTC antes de consultar (T010). Sin período se devuelve el historial
        completo.

        Args:
            product_id: Producto cuyo historial se consulta.
            kind: Tipo de movimiento que se filtra, o `None` para no
                filtrarlo.
            period_start: Inicio del período, o `None` para no acotarlo.
            period_end: Final del período, o `None` para no acotarlo.

        Raises:
            InvalidPeriodError: Si el período no es válido, por ejemplo si el
                final es anterior al inicio o si un límite sin zona horaria no
                existe en la hora local. La persistencia la define como
                subclase de `ValueError`.
        """
        ...

    def list_by_presentation(
        self,
        presentation_id: int,
        kind: MovementKind | None = None,
        *,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> list[MovementRecord]:
        """
        Devuelve el historial de la presentación, en orden cronológico.

        Cuando se indica `kind`, solo se devuelven los movimientos de ese tipo.
        Los filtros indicados se combinan mediante AND: el período acota
        `occurred_at` con el inicio inclusivo y el final exclusivo, y sus
        límites se interpretan en la hora local de Argentina y se convierten a
        UTC antes de consultar (T010). Sin período se devuelve el historial
        completo.

        Args:
            presentation_id: Presentación cuyo historial se consulta.
            kind: Tipo de movimiento que se filtra, o `None` para no
                filtrarlo.
            period_start: Inicio del período, o `None` para no acotarlo.
            period_end: Final del período, o `None` para no acotarlo.

        Raises:
            InvalidPeriodError: Si el período no es válido, por ejemplo si el
                final es anterior al inicio o si un límite sin zona horaria no
                existe en la hora local. La persistencia la define como
                subclase de `ValueError`.
        """
        ...
