"""Pruebas de los prompts centralizados del Supervisor y del Inventory Agent.

Verifican las responsabilidades y prohibiciones exigidas por RF-05 sin invocar
ningún modelo, sin red y sin configuración del LLM.
"""

import importlib
import re

import pytest

from app.prompts import prompts
from app.prompts.prompts import INVENTORY_AGENT_PROMPT, SUPERVISOR_PROMPT

_PROMPTS = (
    pytest.param("SUPERVISOR_PROMPT", SUPERVISOR_PROMPT, id="supervisor"),
    pytest.param(
        "INVENTORY_AGENT_PROMPT",
        INVENTORY_AGENT_PROMPT,
        id="inventory-agent",
    ),
)


def test_prompts_are_non_empty_strings() -> None:
    """Verifica que ambos prompts existen, son `str` y no están vacíos."""
    for name, prompt in (
        ("SUPERVISOR_PROMPT", SUPERVISOR_PROMPT),
        ("INVENTORY_AGENT_PROMPT", INVENTORY_AGENT_PROMPT),
    ):
        assert isinstance(prompt, str), f"{name} debe ser una cadena."
        assert prompt.strip(), f"{name} no debe estar vacío."


def test_prompts_are_different() -> None:
    """Verifica que los dos prompts son textos distintos."""
    assert SUPERVISOR_PROMPT != INVENTORY_AGENT_PROMPT


def test_prompts_identify_their_role() -> None:
    """Verifica que cada prompt declara el rol que le corresponde."""
    assert "supervisor" in SUPERVISOR_PROMPT.lower()
    assert "inventory agent" in INVENTORY_AGENT_PROMPT.lower()


def test_supervisor_prompt_classifies_and_delegates() -> None:
    """Verifica que el Supervisor clasifica y deriva al Inventory Agent."""
    text = SUPERVISOR_PROMPT.lower()

    assert re.search(r"clasific\w*", text), (
        "El prompt del Supervisor debe indicar que clasifica la solicitud."
    )
    assert re.search(r"deriv\w*", text), (
        "El prompt del Supervisor debe indicar que deriva la solicitud."
    )
    assert "inventory agent" in text, (
        "El prompt del Supervisor debe nombrar al Inventory Agent como destino."
    )


def test_inventory_agent_prompt_consults_and_prepares_proposals() -> None:
    """Verifica que el Inventory Agent consulta y prepara propuestas."""
    text = INVENTORY_AGENT_PROMPT.lower()

    assert re.search(r"consulta\w*", text), (
        "El prompt del Inventory Agent debe indicar que consulta información."
    )
    assert re.search(r"propuestas?\b", text), (
        "El prompt del Inventory Agent debe indicar que prepara propuestas."
    )
    assert re.search(r"prepar\w*", text), (
        "El prompt del Inventory Agent debe indicar que prepara, no ejecuta."
    )


@pytest.mark.parametrize(("name", "prompt"), _PROMPTS)
def test_prompts_require_real_data_and_forbid_inventing(
    name: str,
    prompt: str,
) -> None:
    """Verifica que ambos prompts exigen datos reales y no inventar nada."""
    text = prompt.lower()

    assert "datos reales" in text, (
        f"{name} debe exigir el uso de datos reales."
    )
    assert re.search(r"no\s+invent\w*", text), (
        f"{name} debe prohibir inventar información."
    )


@pytest.mark.parametrize(("name", "prompt"), _PROMPTS)
def test_prompts_forbid_direct_database_writes(name: str, prompt: str) -> None:
    """Verifica que ambos prompts prohíben escribir directamente en la base."""
    pattern = re.compile(
        r"(no|nunca)\s+\w*\s*escrib\w*[^.\n]{0,60}base de datos",
        re.IGNORECASE,
    )

    assert pattern.search(prompt), (
        f"{name} debe prohibir explícitamente escribir directamente en la "
        "base de datos."
    )


@pytest.mark.parametrize(("name", "prompt"), _PROMPTS)
def test_prompts_require_explicit_user_confirmation(
    name: str,
    prompt: str,
) -> None:
    """Verifica que la confirmación es explícita y ajena al LLM."""
    text = prompt.lower()

    assert re.search(r"confirmaci[oó]n[^.\n]{0,40}expl[ií]cita", text), (
        f"{name} debe exigir una confirmación explícita del usuario."
    )
    assert re.search(r"no\s+confirm\w*", text), (
        f"{name} debe indicar que el agente no confirma operaciones."
    )
    assert "fuera de tu decisión autónoma" in text, (
        f"{name} debe indicar que la confirmación no depende del LLM."
    )


def test_prompts_avoid_detailed_business_rules() -> None:
    """Verifica que los prompts no duplican reglas de negocio del dominio."""
    for name, prompt in (
        ("SUPERVISOR_PROMPT", SUPERVISOR_PROMPT),
        ("INVENTORY_AGENT_PROMPT", INVENTORY_AGENT_PROMPT),
    ):
        text = prompt.lower()

        assert "fifo" not in text, f"{name} no debe incluir reglas FIFO."
        assert "decimal" not in text, (
            f"{name} no debe incluir detalles de representación decimal."
        )


def test_prompts_module_exposes_only_the_prompt_constants() -> None:
    """Verifica que el módulo es hoja: solo expone las dos constantes."""
    public_names = {
        name for name in vars(prompts) if not name.startswith("__")
    }

    assert public_names == {"SUPERVISOR_PROMPT", "INVENTORY_AGENT_PROMPT"}


def test_prompts_import_without_deepseek_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que el módulo se importa sin `DEEPSEEK_API_KEY` ni red."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    reloaded = importlib.reload(prompts)

    assert isinstance(reloaded.SUPERVISOR_PROMPT, str)
    assert isinstance(reloaded.INVENTORY_AGENT_PROMPT, str)
    assert reloaded.SUPERVISOR_PROMPT.strip()
    assert reloaded.INVENTORY_AGENT_PROMPT.strip()
