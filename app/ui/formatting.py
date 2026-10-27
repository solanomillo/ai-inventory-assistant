"""Formateo de valores y de fechas de la interfaz (T042, RF-09).

Centraliza cómo la interfaz muestra las cantidades enteras, los importes
`Decimal` y las fechas y horas, para que todas las secciones de RF-09
presenten los mismos valores de la misma manera y las fechas se vean en la
hora local argentina sobre un almacenamiento en UTC.

Contrato de formateo:

- Cantidades: el entero tal cual, sin separador de miles y sin decimales; un
  valor negativo conserva su signo.
- Importes: `Decimal` exacto con **dos** decimales fijos, redondeados con la
  política explícita `ROUND_HALF_UP` (`MONEY_ROUNDING`), sin depender del
  contexto global de `Decimal`, sin usar `float` y sin modificar el valor
  recibido.
- Costo unitario de referencia: derivado solo para mostrarlo, con `Decimal`,
  como el total del grupo dividido por sus unidades equivalentes; no se
  persiste y no modifica el total (T058).
- Fechas y horas: `YYYY-MM-DD HH:MM:SS ±HH:MM`, con un espacio antes del
  desplazamiento y el desplazamiento con dos puntos, convertidas a la zona
  IANA `America/Argentina/Buenos_Aires` con `local_timezone()`. El
  desplazamiento se construye desde el instante ya convertido, de modo que se
  respeta el que la zona tenía en esa fecha; **no** se usa un desplazamiento
  fijo.
- Valores ausentes: `None` se muestra como `no informado` (`ABSENT_MARKER`).
  El **cero real** se conserva como cero: `0` y `Decimal("0")` no son valores
  ausentes.

Alcance y límites:

- Es un módulo **puro** de presentación: sin efectos secundarios, sin estado,
  sin `datetime.now()`, sin `float`, sin el locale del sistema y sin acceso a
  la persistencia, los servicios, el dominio, el grafo ni las herramientas.
- Reutiliza el identificador de zona horaria del proyecto mediante
  `local_timezone()` para **no** duplicarlo, y **no** importa ni invoca la
  lógica de persistencia ni `resolve_period`.
- **No** altera la semántica `[inicio, fin)` de los períodos: solo formatea
  instantes ya resueltos.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from app.infrastructure.sqlite.instants import local_timezone

ABSENT_MARKER: str = "no informado"
# Texto de un valor ausente (`None`); nunca representa un cero real (C4).

MONEY_DECIMAL_PLACES: int = 2
# Decimales fijos de los importes; el cuantizador se deriva de este valor.

MONEY_ROUNDING: str = ROUND_HALF_UP
# Política de redondeo explícita de los importes: medio hacia arriba, sin
# depender del contexto global de `Decimal`.

INSTANT_FORMAT: str = "%Y-%m-%d %H:%M:%S"
# Fecha y hora locales sin desplazamiento; el desplazamiento se agrega aparte.

_MONEY_QUANTUM: Decimal = Decimal(1).scaleb(-MONEY_DECIMAL_PLACES)
# Cuantizador de dos decimales derivado de MONEY_DECIMAL_PLACES.


def format_quantity(value: int | None) -> str:
    """
    Formatea una cantidad entera de inventario.

    Args:
        value: Cantidad entera, o `None` si no se informó.

    Returns:
        El entero sin separador de miles y sin decimales, conservando el signo
        de un valor negativo; `ABSENT_MARKER` si el valor es `None`. El cero
        real se muestra como `0`, porque no es un valor ausente.
    """
    if value is None:
        return ABSENT_MARKER

    return str(value)


def format_money(value: Decimal | None) -> str:
    """
    Formatea un importe con dos decimales.

    No modifica el valor recibido: `quantize` devuelve un `Decimal` nuevo. La
    política de redondeo es la explícita `MONEY_ROUNDING` (`ROUND_HALF_UP`) y
    nunca el contexto global de `Decimal`.

    Args:
        value: Importe `Decimal` exacto, o `None` si no se informó.

    Returns:
        El importe con `MONEY_DECIMAL_PLACES` decimales y sin notación
        científica; `ABSENT_MARKER` si el valor es `None`.

    Raises:
        TypeError: Si el valor no es un `Decimal` ni `None`.
    """
    if value is None:
        return ABSENT_MARKER

    if not isinstance(value, Decimal):
        raise TypeError(
            "El importe debe ser un Decimal o None, no "
            f"{type(value).__name__}."
        )

    quantized = value.quantize(_MONEY_QUANTUM, rounding=MONEY_ROUNDING)

    return format(quantized, "f")


def unit_cost_reference(
    total: Decimal | None,
    quantity: int | None,
    *,
    factor: int | None = None,
) -> Decimal | None:
    """
    Calcula el costo unitario de referencia de un grupo de stock (T058).

    El costo informado es el importe total del grupo, no un costo unitario. La
    referencia se deriva solo para mostrarla: para unidades sueltas se divide el
    total por la cantidad y para presentaciones cerradas por la cantidad de
    paquetes multiplicada por su equivalencia. No se persiste ni modifica el
    total recibido.

    Args:
        total: Costo total informado, o `None` si no se informó.
        quantity: Cantidad inicial del grupo, o `None` si no se conoce.
        factor: Equivalencia de la presentación cerrada; `None` para unidades
            sueltas.

    Returns:
        El costo unitario exacto como `Decimal`, o `None` si falta el costo, la
        cantidad no es positiva o la equivalencia de una presentación cerrada
        no es positiva.
    """
    if total is None or quantity is None or quantity <= 0:
        return None

    if factor is not None and factor <= 0:
        return None

    units = quantity if factor is None else quantity * factor

    return total / units


def format_unit_cost_reference(
    total: Decimal | None,
    quantity: int | None,
    *,
    factor: int | None = None,
) -> str | None:
    """
    Muestra el costo unitario de referencia con el formato monetario (T058).

    Reutiliza `format_money`, de modo que la referencia se presenta con dos
    decimales y `ROUND_HALF_UP`. El redondeo es solo de presentación: no altera
    el costo total persistido ni los cálculos internos.

    Args:
        total: Costo total informado, o `None` si no se informó.
        quantity: Cantidad inicial del grupo, o `None` si no se conoce.
        factor: Equivalencia de la presentación cerrada; `None` para unidades
            sueltas.

    Returns:
        La referencia formateada, o `None` si no corresponde mostrarla.
    """
    reference = unit_cost_reference(total, quantity, factor=factor)
    if reference is None:
        return None

    return format_money(reference)


def format_instant(value: datetime | None) -> str:
    """
    Formatea un instante en la hora local argentina.

    El instante recibido se conserva tal cual: `astimezone` devuelve un
    `datetime` nuevo, de modo que el almacenamiento en UTC no se altera.

    Args:
        value: Instante con zona horaria, o `None` si no se informó.

    Returns:
        El instante convertido a la zona local con el formato
        `YYYY-MM-DD HH:MM:SS ±HH:MM`, o `ABSENT_MARKER` si el valor es `None`.

    Raises:
        ValueError: Si el instante no incluye zona horaria, porque no puede
            interpretarse de forma inequívoca.
    """
    if value is None:
        return ABSENT_MARKER

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(
            "El instante debe incluir zona horaria para mostrarse en la "
            "hora local."
        )

    local = value.astimezone(local_timezone())

    return f"{local.strftime(INSTANT_FORMAT)} {_offset_text(local)}"


def single_line(value: str) -> str:
    """
    Normaliza los saltos de línea de un valor solo para mostrarlo.

    El valor de negocio no se modifica: la unión se aplica a una copia usada
    únicamente para dibujar la vista, de modo que un texto con saltos no rompa
    la estructura de los campos etiquetados ni de las tablas.

    Args:
        value: Texto que se va a presentar.

    Returns:
        El texto con sus líneas unidas por un espacio simple.
    """
    return " ".join(value.splitlines())


def _offset_text(local: datetime) -> str:
    """
    Devuelve el desplazamiento de un instante local como `±HH:MM`.

    Se construye desde `utcoffset()` y no con `%z`, que omite los dos puntos,
    para respetar el desplazamiento real de la zona en esa fecha.

    Args:
        local: Instante ya convertido a la zona local del proyecto.

    Returns:
        El desplazamiento con signo y dos puntos, por ejemplo `-03:00`.
    """
    offset = local.utcoffset()
    total_seconds = 0 if offset is None else int(offset.total_seconds())
    sign = "-" if total_seconds < 0 else "+"
    hours, remainder = divmod(abs(total_seconds), 3600)
    minutes = remainder // 60

    return f"{sign}{hours:02d}:{minutes:02d}"
