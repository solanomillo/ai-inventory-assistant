"""Sección Asistente: conversación, propuestas y confirmación (T048, RF-09).

Extrae de `app/ui/main.py` la **presentación interactiva** del asistente para
que la sección conserve la conversación y la confirmación vigentes (C20) sin
depender de la composición ni de los servicios de la aplicación. Este módulo
solo renderiza: recibe el historial, los turnos ya respondidos y un conjunto de
callbacks (`AssistantActions`) que resuelven la respuesta del asistente, la
confirmación de una propuesta y la descripción del contenido de un turno.

No importa `app/ui/main.py`, los servicios, el dominio, el grafo ni las
herramientas; no abre conexiones ni transacciones y no escribe. La confirmación
sigue siendo el **único** punto de `app/ui/main.py` (D-A), que se invoca a
través de `confirm_turn`: este módulo nunca ejecuta una operación por su
cuenta. La sección no presenta un dato ausente como un valor real (C4): sin
historial ni acciones muestra el motivo de la indisponibilidad del asistente y
no habilita el chat (C2).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import streamlit as st

CONFIRM_LABEL: str = "Confirmar"
# Etiqueta del control con el que el usuario confirma una propuesta (T024).

PROCESSING_KEY: str = "confirmation_in_progress"
# Bandera de la sesión que marca una confirmación en curso (T024).

CONFIRMATION_ERROR_PREFIX: str = "No se pudo confirmar la propuesta: "
# Prefijo del aviso que se muestra cuando la confirmación se rechaza (T024).

NO_RESPONSE_MESSAGE: str = (
    "El asistente no devolvió una respuesta; inténtalo de nuevo."
)
# Aviso que se muestra cuando el asistente no produjo ninguna respuesta.

NO_ASSISTANT_MESSAGE: str = (
    "El asistente no está disponible en esta sesión."
)
# Motivo de reserva cuando el asistente no se compuso y falta su causa (T041).

ASSISTANT_INPUT_PLACEHOLDER: str = "Escribe tu consulta de inventario"
# Texto de ayuda de la entrada de chat de la sección (T048).


class TurnLike(Protocol):
    """Contrato mínimo de un turno de la conversación (T048)."""

    user_message: str
    state: Mapping[str, Any]


@dataclass(frozen=True)
class TurnContent:
    """Contenido ya resuelto de un turno para mostrarlo (T048)."""

    response: str
    details: tuple[str, ...] = ()
    can_confirm: bool = False


@dataclass(frozen=True)
class AssistantActions:
    """Callbacks que resuelven las acciones del asistente (T048)."""

    answer: Callable[[str], TurnLike]
    confirm_turn: Callable[[TurnLike], str | None]
    describe_turn: Callable[[TurnLike], TurnContent]


def confirmation_key(index: int) -> str:
    """
    Devuelve la clave estable del control de confirmación de un turno (T024).

    La clave depende solo de la posición del turno en el historial, que no
    cambia porque los turnos solo se agregan.

    Args:
        index: Posición del turno en el historial de la sesión.

    Returns:
        La clave del botón de confirmación dentro de `st.session_state`.
    """
    return f"confirm_proposal_{index}"


def render_confirmation(
    turn: TurnLike,
    *,
    key: str,
    can_confirm: bool,
    actions: AssistantActions,
) -> None:
    """
    Muestra el control de confirmación de una propuesta pendiente (T024).

    El control es un botón propio de la interfaz, fuera del chat: confirmar es
    una acción explícita del usuario y no una decisión del LLM. Solo se ofrece
    cuando el contenido del turno lo habilita, se deshabilita mientras hay una
    confirmación en curso en la sesión y desaparece cuando la propuesta ya está
    confirmada. Si la confirmación se rechaza, se muestra el motivo y el
    control sigue disponible.

    Args:
        turn: Turno cuya propuesta se puede confirmar.
        key: Clave estable del botón dentro de la sesión.
        can_confirm: Indica si el turno tiene una propuesta pendiente.
        actions: Callbacks que ejecutan la confirmación en `main.py`.
    """
    if not can_confirm:
        return

    session = st.session_state
    processing = bool(session.get(PROCESSING_KEY, False))
    if not st.button(CONFIRM_LABEL, key=key, disabled=processing):
        return

    session[PROCESSING_KEY] = True
    try:
        error = actions.confirm_turn(turn)
    finally:
        session[PROCESSING_KEY] = False

    if error is None:
        st.rerun()
    else:
        st.error(f"{CONFIRMATION_ERROR_PREFIX}{error}")


def render_turn(
    turn: TurnLike,
    *,
    key: str,
    actions: AssistantActions,
) -> None:
    """
    Muestra un turno de la conversación con su propuesta y su resultado (T024).

    El contenido se resuelve con el callback `describe_turn`, que lee el estado
    real del turno en `main.py`: la respuesta, la propuesta —con su detalle de
    precio e historial cuando corresponde— y el resultado de una confirmación.
    El control de confirmación solo aparece cuando ese contenido lo habilita.

    Args:
        turn: Turno con el mensaje del usuario y el estado real.
        key: Clave estable del control de confirmación del turno.
        actions: Callbacks de la sección.
    """
    content = actions.describe_turn(turn)
    with st.chat_message("user"):
        st.markdown(turn.user_message)
    with st.chat_message("assistant"):
        st.markdown(content.response)
        for detail in content.details:
            st.markdown(detail)
        render_confirmation(
            turn,
            key=key,
            can_confirm=content.can_confirm,
            actions=actions,
        )


def render_history(
    history: list[TurnLike],
    *,
    actions: AssistantActions,
) -> None:
    """Muestra los turnos anteriores de la conversación (T024)."""
    for index, turn in enumerate(history):
        render_turn(turn, key=confirmation_key(index), actions=actions)


def handle_message(
    history: list[TurnLike],
    user_message: str,
    *,
    actions: AssistantActions,
) -> TurnLike:
    """
    Responde el mensaje del usuario, lo registra y lo muestra (T024).

    Args:
        history: Turnos de la conversación, a los que se agrega el nuevo.
        user_message: Mensaje escrito por el usuario.
        actions: Callbacks de la sección.

    Returns:
        El turno respondido, ya agregado al historial de la sesión.
    """
    turn = actions.answer(user_message)
    history.append(turn)
    render_turn(turn, key=confirmation_key(len(history) - 1), actions=actions)

    return turn


def assistant_section(
    history: list[TurnLike] | None,
    *,
    actions: AssistantActions | None = None,
    unavailable_reason: str | None = None,
) -> Callable[[], None]:
    """
    Construye el renderer de la sección del asistente (T041, T048, C20).

    Conserva el flujo conversacional vigente —el historial, la entrada de chat
    y el manejo del mensaje—, incluida la confirmación, que se delega en los
    callbacks de `main.py`. Cuando el asistente no está disponible porque el
    modelo no está configurado, la sección muestra el motivo en lugar de la
    conversación, sin impedir las demás secciones (C2).

    Args:
        history: Historial de la sesión, o `None` si el asistente no se
            compuso.
        actions: Callbacks de la sección, o `None` si no hay asistente.
        unavailable_reason: Motivo por el que el asistente no está disponible,
            o `None` si está disponible.

    Returns:
        El renderer sin argumentos de la sección del asistente.
    """

    def render() -> None:
        """Muestra la conversación del asistente o su indisponibilidad."""
        if history is None or actions is None:
            st.error(unavailable_reason or NO_ASSISTANT_MESSAGE)
            return

        render_history(history, actions=actions)
        user_message = st.chat_input(ASSISTANT_INPUT_PLACEHOLDER)
        if user_message:
            handle_message(history, user_message, actions=actions)

    return render
