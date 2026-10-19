"""Pruebas del formateo de valores y de fechas de la interfaz (T042, RF-09).

Verifican el contrato del módulo puro `app/ui/formatting.py` sin SQLite, sin
Streamlit y sin red: cantidades enteras, importes `Decimal` con dos decimales
y redondeo explícito, fechas y horas en la zona local argentina sobre
almacenamiento UTC, valores ausentes y el rechazo de instantes sin zona
horaria.

Cubren los criterios de aceptación de T042: la zona IANA
`America/Argentina/Buenos_Aires` mediante `local_timezone()` con el
almacenamiento en UTC intacto (`to_utc_text`), la preservación de la semántica
`[inicio, fin)` —el módulo no invoca `resolve_period`— y el valor ausente
informado como ausente y nunca como cero (C4).
"""

import ast
import re
from datetime import datetime, timedelta, timezone, tzinfo
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from app.infrastructure.sqlite.instants import (
    LOCAL_TIMEZONE_NAME,
    local_timezone,
    to_utc_text,
)
from app.ui.formatting import (
    ABSENT_MARKER,
    INSTANT_FORMAT,
    MONEY_DECIMAL_PLACES,
    MONEY_ROUNDING,
    format_instant,
    format_money,
    format_quantity,
)

FORMATTING_PATH: Path = (
    Path(__file__).resolve().parents[2] / "app" / "ui" / "formatting.py"
)
"""Ruta absoluta del módulo de formateo de la interfaz (T042)."""

INSTANT_PATTERN: re.Pattern[str] = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{2}:\d{2}$"
)
"""Formato exacto `YYYY-MM-DD HH:MM:SS ±HH:MM` del contrato (T042)."""

FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "app.services",
    "app.domain",
    "app.schemas",
    "app.graph",
    "app.tools",
    "app.ui.main",
    "app.infrastructure.sqlite.connection",
    "app.infrastructure.sqlite.repositories",
    "app.infrastructure.sqlite.schema",
    "sqlite3",
    "streamlit",
)
"""Dependencias que el módulo de formateo no debe importar (T042)."""

FORBIDDEN_CALLS: frozenset[str] = frozenset(
    {
        "resolve_period",
        "get_llm",
        "build_workflow",
        "build_session",
        "open_connection",
        "transaction",
        "float",
    }
)
"""Llamadas que romperían la pureza del módulo de formateo (T042)."""

FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset({"now"})
"""Atributos prohibidos, como `datetime.now()` (T042)."""

FORBIDDEN_IMPORTED_NAMES: frozenset[str] = frozenset(
    {"timedelta", "timezone", "ZoneInfo"}
)
"""Nombres que permitirían fijar un desplazamiento en lugar de usar la zona."""


class UnknownOffsetTimezone(tzinfo):
    """Zona horaria cuyo desplazamiento no está definido (T042)."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        """Devuelve `None` para forzar el caso defensivo del formateo."""
        return None


def formatting_source() -> str:
    """Devuelve el fuente del módulo de formateo de la interfaz (T042)."""
    return FORMATTING_PATH.read_text(encoding="utf-8")


def imported_modules(tree: ast.AST) -> set[str]:
    """Devuelve los módulos que importa un árbol sintáctico."""
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    return imported


def imported_names(tree: ast.AST) -> set[str]:
    """Devuelve los nombres importados por un árbol sintáctico."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.name for alias in node.names)

    return names


def called_names(tree: ast.AST) -> set[str]:
    """Devuelve los nombres invocados directamente por un árbol sintáctico."""
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def called_attributes(tree: ast.AST) -> set[str]:
    """Devuelve los atributos invocados por un árbol sintáctico."""
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def string_constants(tree: ast.AST) -> set[str]:
    """Devuelve los literales de texto de un árbol sintáctico."""
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


# --- Constantes del contrato (T042) ---


def test_the_absent_marker_and_the_money_policy_are_explicit() -> None:
    """Fija el marcador de ausencia y la política de importes (C4, T042)."""
    assert ABSENT_MARKER == "no informado"
    assert MONEY_DECIMAL_PLACES == 2
    assert MONEY_ROUNDING == ROUND_HALF_UP
    assert INSTANT_FORMAT == "%Y-%m-%d %H:%M:%S"


# --- Cantidades (T042) ---


def test_a_real_zero_quantity_is_shown_as_zero() -> None:
    """Muestra el cero real como cero y no como ausente (C4, T042)."""
    assert format_quantity(0) == "0"
    assert format_quantity(0) != ABSENT_MARKER


def test_a_positive_quantity_keeps_its_integer_text() -> None:
    """Muestra un entero sin separador de miles ni decimales (T042)."""
    assert format_quantity(16) == "16"
    assert format_quantity(1234567) == "1234567"


def test_a_negative_quantity_keeps_its_sign() -> None:
    """Muestra un valor negativo defensivo con su signo (T042)."""
    assert format_quantity(-3) == "-3"


def test_an_absent_quantity_is_shown_as_absent() -> None:
    """Informa una cantidad ausente como ausente (C4, T042)."""
    assert format_quantity(None) == ABSENT_MARKER


# --- Importes (T042) ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("700"), "700.00"),
        (Decimal("600.00"), "600.00"),
        (Decimal("0"), "0.00"),
    ],
)
def test_an_amount_is_shown_with_exactly_two_decimals(
    value: Decimal,
    expected: str,
) -> None:
    """Muestra cualquier importe con dos decimales fijos (T042)."""
    assert format_money(value) == expected


def test_a_real_zero_amount_is_shown_as_zero() -> None:
    """Muestra el cero real de un importe como cero (C4, T042)."""
    assert format_money(Decimal("0")) == "0.00"
    assert format_money(Decimal("0")) != ABSENT_MARKER


def test_an_absent_amount_is_shown_as_absent() -> None:
    """Informa un importe ausente como ausente (C4, T042)."""
    assert format_money(None) == ABSENT_MARKER


def test_the_amount_rounding_policy_is_half_up() -> None:
    """Fija el redondeo medio hacia arriba y no el del contexto (T042)."""
    assert format_money(Decimal("1.005")) == "1.01"
    assert format_money(Decimal("0.125")) == "0.13"
    assert format_money(Decimal("1.004")) == "1.00"


def test_a_negative_amount_keeps_its_sign() -> None:
    """Muestra un importe negativo defensivo con su signo (T042)."""
    assert format_money(Decimal("-1.5")) == "-1.50"


def test_formatting_an_amount_does_not_change_the_received_value() -> None:
    """Conserva el `Decimal` recibido, sin redondearlo ni escalarlo (T042)."""
    value = Decimal("1.005")
    exponent_before = value.as_tuple().exponent

    assert format_money(value) == "1.01"
    assert value == Decimal("1.005")
    assert str(value) == "1.005"
    assert value.as_tuple().exponent == exponent_before


@pytest.mark.parametrize("value", [700, "700", 700.0, 700.5])
def test_a_non_decimal_amount_is_rejected(value: object) -> None:
    """Rechaza con `TypeError` un importe que no es `Decimal` (T042)."""
    with pytest.raises(TypeError, match="Decimal"):
        format_money(value)  # type: ignore[arg-type]


# --- Fechas y horas (T042) ---


def test_a_utc_instant_is_shown_in_the_local_argentine_time() -> None:
    """Muestra un instante UTC en la hora local argentina (T042)."""
    instant = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

    text = format_instant(instant)

    assert text == "2026-03-01 09:00:00 -03:00"
    assert INSTANT_PATTERN.match(text)


def test_an_instant_already_in_the_local_offset_keeps_the_same_text() -> None:
    """Muestra el mismo texto para un instante ya local y su UTC (T042)."""
    utc_instant = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    local_instant = datetime(
        2026,
        3,
        1,
        9,
        0,
        tzinfo=timezone(timedelta(hours=-3)),
    )

    assert format_instant(local_instant) == format_instant(utc_instant)
    assert format_instant(local_instant) == "2026-03-01 09:00:00 -03:00"


def test_a_historical_instant_uses_the_iana_offset_not_a_fixed_one() -> None:
    """
    Respeta el desplazamiento histórico de la zona IANA (T042).

    En enero de 2008 la zona `America/Argentina/Buenos_Aires` usaba `-02:00`:
    el texto debe reflejarlo, lo que demuestra que no se aplica un
    desplazamiento fijo.
    """
    instant = datetime(2008, 1, 15, 12, 0, tzinfo=timezone.utc)

    text = format_instant(instant)

    assert text == "2008-01-15 10:00:00 -02:00"
    assert text.endswith("-02:00")
    assert not text.endswith("-03:00")
    assert text.startswith(
        instant.astimezone(local_timezone()).strftime(INSTANT_FORMAT)
    )


def test_an_absent_instant_is_shown_as_absent() -> None:
    """Informa una fecha ausente como ausente (C4, T042)."""
    assert format_instant(None) == ABSENT_MARKER


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 3, 1, 12, 0),
        datetime(2026, 3, 1, 12, 0, tzinfo=UnknownOffsetTimezone()),
    ],
)
def test_a_naive_instant_is_rejected(value: datetime) -> None:
    """Rechaza con `ValueError` un instante sin zona horaria (T042)."""
    with pytest.raises(ValueError, match="zona horaria"):
        format_instant(value)


def test_formatting_an_instant_is_repeatable_and_does_not_change_it() -> None:
    """Repite el mismo texto sin mutar el instante recibido (T042)."""
    instant = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    local = datetime(2026, 3, 1, 9, 0, tzinfo=timezone(timedelta(hours=-3)))

    assert format_instant(instant) == format_instant(instant)
    assert format_instant(local) == format_instant(local)
    assert instant == datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    assert instant.tzinfo is timezone.utc
    assert instant.utcoffset() == timedelta(0)
    assert local.utcoffset() == timedelta(hours=-3)
    assert local.tzinfo is not None


def test_formatting_an_instant_preserves_the_stored_utc_text() -> None:
    """Conserva el texto UTC almacenado tras formatear el instante (T042)."""
    instant = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
    before = to_utc_text(instant)

    format_instant(instant)

    assert to_utc_text(instant) == before
    assert before == "2026-03-01T12:00:00.000000+00:00"


# --- Guarda estructural (T042) ---


def test_the_formatting_module_is_a_pure_presentation_module() -> None:
    """
    Verifica el aislamiento del formateo por lectura de su fuente (T042).

    El módulo no depende de la persistencia, los servicios, el dominio, el
    grafo, las herramientas ni la interfaz principal; no invoca
    `resolve_period` ni `datetime.now()`, no usa `float`, no confirma
    propuestas y no fija un desplazamiento horario. Reutiliza la zona horaria
    del proyecto sin duplicar su identificador IANA.
    """
    source = formatting_source()
    tree = ast.parse(source)

    assert not called_names(tree) & FORBIDDEN_CALLS
    assert not called_attributes(tree) & FORBIDDEN_ATTRIBUTES
    assert "float(" not in source
    assert ".confirm(" not in source
    assert "timedelta" not in source
    assert "LOCAL_TIMEZONE_NAME" not in source
    assert LOCAL_TIMEZONE_NAME not in string_constants(tree)

    modules = imported_modules(tree)
    for module in modules:
        for prefix in FORBIDDEN_IMPORTS:
            assert not (
                module == prefix or module.startswith(f"{prefix}.")
            ), module

    assert "app.infrastructure.sqlite.instants" in modules
    assert "local_timezone" in imported_names(tree)
    assert not imported_names(tree) & FORBIDDEN_IMPORTED_NAMES
