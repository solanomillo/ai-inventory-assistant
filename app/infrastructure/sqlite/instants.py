"""Instantes y períodos temporales de la persistencia (T010).

Centraliza la representación de los instantes y la interpretación de los
períodos de consulta, de modo que la persistencia use una única convención:

- Los instantes se almacenan en UTC con una representación ISO 8601 uniforme
  (microsegundos y desplazamiento `+00:00`), para que su comparación textual en
  SQLite coincida exactamente con su orden cronológico.
- Los límites de un período se interpretan en la hora local de Argentina
  (`America/Argentina/Buenos_Aires`) y se convierten a UTC antes de consultar,
  con la semántica `[inicio, fin)`: el inicio es inclusivo y el final es
  exclusivo, conforme a RF-03 y a `plan.md`.

Contrato de los límites de un período:

- Un `datetime` con zona horaria se convierte a UTC tal como se recibe.
- Un `datetime` sin zona horaria se interpreta como hora local argentina. Ante
  una hora ambigua, repetida por un cambio de hora, se usa la primera
  ocurrencia (`fold = 0`), salvo que el llamador indique `fold = 1`.
- Una hora local inexistente, saltada por un cambio de hora, se rechaza con
  `InvalidPeriodError` porque no puede interpretarse de forma inequívoca.
- Un período cuyo final sea anterior a su inicio se rechaza con
  `InvalidPeriodError`; un período vacío (inicio igual a final) es válido y no
  coincide con ningún movimiento, porque `[inicio, inicio)` es vacío.
"""

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

LOCAL_TIMEZONE_NAME: str = "America/Argentina/Buenos_Aires"
"""Identificador IANA de la zona horaria local usada para los períodos."""

_UTC_TIMESPEC: str = "microseconds"


class InvalidPeriodError(ValueError):
    """Error que indica que el período solicitado no es válido."""


@lru_cache(maxsize=1)
def local_timezone() -> ZoneInfo:
    """
    Devuelve la zona horaria local del proyecto.

    Returns:
        La zona horaria `America/Argentina/Buenos_Aires`, reutilizada entre
        llamadas porque se consulta en cada consulta por período.
    """
    return ZoneInfo(LOCAL_TIMEZONE_NAME)


def to_utc_text(instant: datetime) -> str:
    """
    Serializa un instante en UTC con una representación ISO 8601 uniforme.

    Args:
        instant: Instante con zona horaria; se convierte a UTC.

    Returns:
        El instante en UTC con microsegundos y desplazamiento `+00:00`, de modo
        que dos instantes se ordenen igual como texto que como fecha.

    Raises:
        ValueError:
            Si el instante no incluye zona horaria, porque no puede
            interpretarse de forma inequívoca.
    """
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("El instante debe incluir zona horaria.")

    return instant.astimezone(timezone.utc).isoformat(
        timespec=_UTC_TIMESPEC
    )


def from_utc_text(text: str) -> datetime:
    """
    Convierte un instante almacenado en un `datetime` con zona horaria.

    Args:
        text: Texto ISO 8601 de la columna `occurred_at`.

    Returns:
        El instante correspondiente, con su zona horaria.
    """
    return datetime.fromisoformat(text)


def resolve_period(
    start: datetime | None,
    end: datetime | None,
) -> tuple[str | None, str | None]:
    """
    Convierte los límites de un período en textos UTC comparables.

    Los límites sin zona horaria se interpretan en la hora local argentina y,
    con zona horaria, se respetan tal como se reciben. El resultado se aplica
    sobre `movements.occurred_at` como `occurred_at >= inicio` y
    `occurred_at < fin`, de modo que el inicio es inclusivo y el final es
    exclusivo.

    Args:
        start: Inicio del período, o `None` para no acotarlo.
        end: Final del período, o `None` para no acotarlo.

    Returns:
        El par `(inicio, fin)` en UTC, con `None` en los límites no indicados.

    Raises:
        InvalidPeriodError:
            Si el final es anterior al inicio o si un límite sin zona horaria
            no existe en la hora local argentina.
        TypeError:
            Si un límite proporcionado no es un `datetime`.
    """
    start_utc = _bound_to_utc(start, "inicio")
    end_utc = _bound_to_utc(end, "fin")

    if start_utc is not None and end_utc is not None and end_utc < start_utc:
        raise InvalidPeriodError(
            "El final del período no puede ser anterior a su inicio."
        )

    return (
        None if start_utc is None else to_utc_text(start_utc),
        None if end_utc is None else to_utc_text(end_utc),
    )


def _bound_to_utc(bound: datetime | None, label: str) -> datetime | None:
    """
    Convierte un límite de período a UTC.

    Args:
        bound: Límite indicado, o `None` para no acotar el período.
        label: Nombre del límite (`inicio` o `fin`) para los mensajes.

    Returns:
        El límite en UTC, o `None` si no se indicó.

    Raises:
        InvalidPeriodError:
            Si el límite sin zona horaria no existe en la hora local.
        TypeError: Si el límite no es un `datetime`.
    """
    if bound is None:
        return None

    if not isinstance(bound, datetime):
        raise TypeError(f"El {label} del período debe ser un datetime.")

    if bound.tzinfo is None or bound.utcoffset() is None:
        return _local_bound_to_utc(bound, label)

    return bound.astimezone(timezone.utc)


def _local_bound_to_utc(bound: datetime, label: str) -> datetime:
    """
    Interpreta un límite sin zona horaria en la hora local argentina.

    Raises:
        InvalidPeriodError:
            Si la hora local no existe por un cambio de hora de la zona.
    """
    zone = local_timezone()
    if not _exists_on_local_clock(bound, zone):
        raise InvalidPeriodError(
            f"El {label} del período no existe en la zona horaria "
            f"{LOCAL_TIMEZONE_NAME}: {bound.isoformat()}."
        )

    return bound.replace(tzinfo=zone).astimezone(timezone.utc)


def _exists_on_local_clock(naive: datetime, zone: ZoneInfo) -> bool:
    """
    Indica si una hora local existe en la zona horaria indicada.

    Una hora ambigua, repetida por un cambio de hora, existe en sus dos
    ocurrencias; una hora inexistente, saltada por un cambio de hora, no
    vuelve a coincidir tras convertirla a UTC y de vuelta a la zona local.

    Args:
        naive: Instante sin zona horaria que se verifica.
        zone: Zona horaria local del proyecto.

    Returns:
        `True` si la hora local existe en alguna de sus ocurrencias.
    """
    for fold in (0, 1):
        candidate = naive.replace(tzinfo=zone, fold=fold)
        round_trip = candidate.astimezone(timezone.utc).astimezone(zone)
        if round_trip.replace(tzinfo=None) != naive:
            return False

    return True
