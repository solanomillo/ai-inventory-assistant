"""Vista de las propuestas pendientes de la interfaz (T041, RF-09).

Renderiza la sección «Propuestas pendientes» de la navegación lateral: un
encabezado, un estado vacío explícito y, por cada propuesta, su acción y su
estado. Es una vista de **solo lectura**: no confirma, no edita, no descarta y
no escribe nada.

La confirmación explícita del usuario permanece en el **único** punto de
`app/ui/main.py` (D-A): este módulo no invoca el servicio de propuestas, no
escribe en el estado de la sesión y no abre conexiones ni transacciones. La
edición de una propuesta (T051), su descarte (T052) y el formateo de valores y
fechas (T042) quedan fuera de este alcance.

La vista no accede a la persistencia ni al dominio: recibe las propuestas ya
preparadas por la sesión —esquemas Pydantic de `app/schemas/proposals.py`— y
solo las presenta por su acción y su estado, sin volcar el payload genérico
(D-C). Un dato ausente se informa como ausente y nunca como un valor real
(C4).
"""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from app.schemas.proposals import Proposal

PENDING_PROPOSALS_HEADER: str = "Propuestas pendientes"
# Encabezado de la sección de propuestas pendientes (T041).

EMPTY_PROPOSALS_MESSAGE: str = (
    "No hay propuestas pendientes: todavía no se preparó ninguna operación."
)
# Estado vacío explícito de la sección, sin inventar valores (T041, C4).


def render_pending_proposals(proposals: Sequence[Proposal]) -> None:
    """
    Muestra las propuestas de la sesión con su acción y su estado (T041, C13).

    Args:
        proposals: Propuestas ya preparadas por la sesión, en el orden en que
            se crearon. La vista no las transforma, no las confirma y no
            escribe en el inventario.
    """
    st.header(PENDING_PROPOSALS_HEADER)
    if not proposals:
        st.info(EMPTY_PROPOSALS_MESSAGE)
        return

    for index, proposal in enumerate(proposals, start=1):
        st.markdown(
            f"{index}. acción `{proposal.action.value}`, estado "
            f"`{proposal.status.value}`"
        )
