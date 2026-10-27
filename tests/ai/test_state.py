"""Pruebas del estado compartido del grafo del asistente (T018, T063).

Verifican la construcción y la conservación del estado mínimo que circula
entre los nodos del grafo: el mensaje del usuario, la conversación reciente, la
propuesta pendiente y el resultado de la confirmación. El estado es un
contenedor pasivo, por lo que estas pruebas cubren únicamente la construcción y
la conservación de los objetos, no la propagación real entre nodos del grafo.

La conversación reciente se representa como una tupla de pares `(rol, texto)`
sin objetos de LangChain (T063): la traducción a mensajes del modelo corresponde
a `app/graph/workflow.py`.

Los objetos de `Proposal` y de `ConfirmationResult` que se usan aquí son
fixtures de prueba, construidos solo para verificar que el estado los conserva
tal cual; no representan resultados reales de la aplicación.
"""

import inspect

import pytest

from app.graph.state import GraphState, create_initial_state
from app.schemas.proposals import (
    ConfirmationResult,
    LooseEntryPayload,
    Proposal,
    ProposalAction,
    ProposalStatus,
)
from app.services.ports import ProductRecord


@pytest.fixture
def pending_proposal() -> Proposal:
    """Propuesta pendiente de prueba (fixture, no un resultado de la app)."""
    return Proposal(
        action=ProposalAction.LOOSE_ENTRY,
        payload=LooseEntryPayload(
            product_id=1,
            quantity=2,
            reason="Compra",
        ),
    )


@pytest.fixture
def confirmation_result() -> ConfirmationResult:
    """Resultado de confirmación de prueba (fixture, no un resultado real)."""
    proposal = Proposal(
        action=ProposalAction.LOOSE_ENTRY,
        payload=LooseEntryPayload(
            product_id=1,
            quantity=2,
            reason="Compra",
        ),
        status=ProposalStatus.CONFIRMED,
    )
    outcome = ProductRecord(
        id=1,
        sku="SKU-1",
        name="Producto de prueba",
        base_unit="botella",
        minimum_stock=0,
        loose_units=2,
        active=True,
    )
    return ConfirmationResult(
        action=ProposalAction.LOOSE_ENTRY,
        status=ProposalStatus.CONFIRMED,
        proposal=proposal,
        result=outcome,
    )


def test_initial_state_carries_the_message_and_none_for_optional_fields() -> None:
    """Verifica la construcción del estado inicial con sus campos."""
    state = create_initial_state("Consulta de stock")

    assert isinstance(state, dict)
    assert set(state.keys()) == {
        "user_message",
        "conversation",
        "pending_proposal",
        "execution_result",
        "response",
    }
    assert state["user_message"] == "Consulta de stock"
    assert state["conversation"] == ()
    assert state["pending_proposal"] is None
    assert state["execution_result"] is None
    assert state["response"] is None


def test_create_initial_state_stores_recent_conversation() -> None:
    """Verifica que el estado conserva la conversación reciente como tupla."""
    conversation = (
        ("human", "Busca Coca"),
        ("ai", "¿Te refieres a coca-cola-3L?"),
    )

    state = create_initial_state("sí, es ese", conversation=conversation)

    assert isinstance(state["conversation"], tuple)
    assert state["conversation"] == conversation
    assert state["conversation"][0] == ("human", "Busca Coca")
    assert state["conversation"][1] == ("ai", "¿Te refieres a coca-cola-3L?")
    assert all(
        isinstance(part, str)
        for message in state["conversation"]
        for part in message
    )
    assert state["user_message"] == "sí, es ese"
    assert state["pending_proposal"] is None
    assert state["response"] is None


def test_initial_state_does_not_fabricate_proposals_or_results() -> None:
    """Verifica que el estado inicial no fabrica propuestas ni resultados."""
    state = create_initial_state("Registrar un producto")

    assert state["pending_proposal"] is None
    assert state["execution_result"] is None


def test_state_preserves_a_real_pending_proposal(
    pending_proposal: Proposal,
) -> None:
    """Verifica que el estado conserva una instancia real de `Proposal`."""
    state = create_initial_state("Registrar una entrada suelta")

    state["pending_proposal"] = pending_proposal

    assert state["pending_proposal"] is pending_proposal
    assert state["pending_proposal"].status is ProposalStatus.PENDING
    assert isinstance(state["pending_proposal"].payload, LooseEntryPayload)
    assert state["execution_result"] is None


def test_state_preserves_a_real_confirmation_result(
    confirmation_result: ConfirmationResult,
) -> None:
    """Verifica que el estado conserva una instancia real de `ConfirmationResult`."""
    state = create_initial_state("Confirmar la entrada suelta")

    state["execution_result"] = confirmation_result

    assert state["execution_result"] is confirmation_result
    assert state["execution_result"].status is ProposalStatus.CONFIRMED
    assert isinstance(state["execution_result"].result, ProductRecord)
    assert state["pending_proposal"] is None


def test_fields_are_independent(
    pending_proposal: Proposal,
    confirmation_result: ConfirmationResult,
) -> None:
    """Verifica la independencia de los tres campos del estado."""
    state: GraphState = create_initial_state("Mensaje original")

    state["pending_proposal"] = pending_proposal
    assert state["user_message"] == "Mensaje original"
    assert state["execution_result"] is None

    state["execution_result"] = confirmation_result
    assert state["user_message"] == "Mensaje original"
    assert state["pending_proposal"] is pending_proposal

    state["user_message"] = "Mensaje actualizado"
    assert state["pending_proposal"] is pending_proposal
    assert state["execution_result"] is confirmation_result


def test_state_module_has_no_llm_agent_or_tool_imports() -> None:
    """Verifica que el módulo de estado no importa LLM, agentes ni herramientas."""
    import app.graph.state as state_module

    source = inspect.getsource(state_module)

    forbidden = (
        "langchain",
        "langgraph",
        "ChatDeepSeek",
        "app.services.llm",
        "app.agents",
        "app.tools",
    )
    for token in forbidden:
        assert token not in source, (
            f"El módulo de estado no debe importar `{token}`."
        )
