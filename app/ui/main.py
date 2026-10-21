"""Interfaz de gestión del inventario (T023, T024, T035, T041).

Aplicación Streamlit que permite consultar el inventario en lenguaje natural:
el mensaje del usuario se envía al grafo del asistente —Supervisor e Inventory
Agent (T022)—, que responde con datos reales obtenidos por sus herramientas y,
si corresponde, prepara una propuesta pendiente.

Alcance de T024: revisar y confirmar propuestas. La propuesta pendiente se
muestra en la conversación y el usuario la confirma con un botón propio de la
interfaz, fuera del chat: la confirmación no pasa por el LLM.

Alcance de T051 (RF-09): una propuesta pendiente se puede editar antes de
confirmarla, desde su propio formulario en «Propuestas pendientes». La edición
se aplica con `ProposalService.edit` —que exige el estado pendiente y conserva
la acción— y reemplaza solo la referencia de la propuesta en su origen de
sesión: la lista manual o el turno del asistente. Editar no escribe el
inventario; una propuesta confirmada no se edita.

Alcance de T035 (RF-08): el cambio de precio se muestra con la presentación
afectada, su precio vigente y el precio nuevo, junto con el historial de
precios de la presentación en orden cronológico. El precio vigente y el
historial se leen del inventario al mostrar el turno —antes y después de
confirmar— y nunca ejecutan la operación: el único punto de escritura sigue
siendo la confirmación explícita del usuario. Los precios se formatean con dos
decimales solo al mostrarlos; el dominio conserva `Decimal`.

Confirmación: al confirmar, la interfaz invoca `ProposalService.confirm`, que
revalida las reglas con el estado vigente y aplica la operación de forma
atómica; solo si la operación tiene éxito se guarda el `ConfirmationResult`
real en `execution_result` y la propuesta se reemplaza por su copia confirmada.
Un error del servicio se muestra como aviso y no marca nada como ejecutado ni
deja cambios parciales. El control desaparece cuando la propuesta ya está
confirmada y se deshabilita mientras hay una confirmación en curso; esa
protección pertenece a la sesión de la interfaz y **no** es idempotencia
durable, porque el servicio no registra las propuestas y no detecta un reenvío
desde otra sesión.

Composición dual (T039, RF-09): la interfaz compone **primero** la lectura de
la sesión —la conexión y un conjunto de servicios por sesión, en
`app/ui/session.py`, **sin** invocar `get_llm()`— y, **por separado**, el
asistente. La consulta funciona sin modelo configurado; cuando el modelo falta,
se muestra el motivo, la conversación no se habilita y las lecturas siguen
disponibles.

Navegación lateral (T041, T043, RF-09): la aplicación es de **una sola
página** —un único punto de entrada, sin el modo multipágina de Streamlit— y
ofrece las siete secciones de RF-09 en el sidebar, con «Asistente» por defecto.
Se renderiza **solo** la sección seleccionada: el asistente conserva la
conversación y la confirmación vigentes y su presentación interactiva vive en
`app/ui/sections/assistant.py` (T048); «Propuestas pendientes» se renderiza
desde `app/ui/sections/proposals.py`, una vista que no confirma y que presenta
la revisión legible de cada propuesta pendiente y su formulario de edición
(T050, T051); «Inicio» se
renderiza desde `app/ui/sections/home.py` y consulta sus datos de forma
perezosa, solo al mostrarse; «Productos» se renderiza desde
`app/ui/sections/products.py` y también lee de forma perezosa, con búsqueda
exacta y filtro de estado (T044); «Movimientos» se renderiza desde
`app/ui/sections/movements.py` y consulta de forma perezosa el historial del
producto elegido, con filtros de tipo y de período local (T046); «Historial de
precios» se renderiza desde `app/ui/sections/price_history.py` y consulta de
forma perezosa el historial del producto o de una presentación concreta
(T047); y «Operaciones» se renderiza desde `app/ui/sections/operations.py`, con
un formulario por cada acción admitida (T049). Ninguna sección queda pendiente:
todas presentan sus datos o un estado explícito y **no** presentan un dato
ausente como un valor real (C4).
Sin modelo, la aplicación **no** se abstiene: la sección del asistente informa
su indisponibilidad y el resto de las secciones sigue accesible.

Formularios de propuestas (T049, C10–C12): «Operaciones» ofrece un formulario
por cada acción admitida —incluido el alta de una presentación nueva con su
factor— y **ninguno** para desactivar productos ni para cambiar el factor de
una presentación existente. Enviar un formulario válido crea una propuesta
pendiente con `ProposalService.create`, que no escribe, y la guarda en
`st.session_state` bajo `MANUAL_PROPOSALS_KEY`, en el orden en que se creó. La
vista de `app/ui/sections/operations.py` no consulta servicios, no confirma, no
usa `st.session_state` y no escribe: las propuestas manuales se concatenan con
las del asistente en «Propuestas pendientes», sin fusionarlas ni
deduplicarlas.

Revisión legible de propuestas (T050, C13): «Propuestas pendientes» muestra
solo las propuestas pendientes —las del asistente y las manuales, concatenadas—
con la acción en lenguaje legible y sus campos. El cambio de precio incluye la
presentación afectada con su id y su factor, el precio vigente y el precio
nuevo; si la presentación ya no existe se informa el motivo y no se inventa un
precio vigente. Los precios se resuelven con el servicio de presentaciones de
la sesión, como máximo una consulta por presentación en cada render, de modo
que la sección funciona también con propuestas manuales y sin modelo. La
revisión solo presenta: la confirmación sigue en su único punto, en
`confirm_pending`.

Configuración y ejecución (desde la raíz del repositorio, con el entorno
virtual instalado y las variables `DEEPSEEK_API_KEY` y `DEEPSEEK_MODEL`
definidas en `.env` o en el entorno):

    streamlit run app/ui/main.py

Las dependencias se instalan con `pip install -r requirements.txt`. El
asistente usa **persistencia duradera**: abre una base SQLite en archivo cuya
ruta se configura con `INVENTORY_DB_PATH` y, sin configuración, es
`data/inventory.sqlite3` dentro del proyecto (T028), determinista e
independiente del directorio de trabajo. `:memory:` queda limitado a las
pruebas, que inyectan su propia conexión.

Sesión: la lectura de la sesión —la conexión y sus servicios— vive en
`st.session_state` bajo la clave `SESSION_KEY`, y el grafo y el historial bajo
`ASSISTANT_KEY`; el asistente referencia el contenedor de lectura, de modo que
ambos comparten la única conexión de la sesión (D-G). Las pruebas pueden dejar
en `ASSISTANT_KEY` un asistente ya construido —con un modelo falso y una
conexión en memoria— antes de la primera ejecución de la interfaz; en ese caso
la interfaz reutiliza su contenedor y no abre la base durable. No se usa
`st.cache_resource` porque el grafo compilado captura las propuestas en un
contenedor propio y no admite invocaciones concurrentes; tampoco se persisten
el historial, las propuestas ni los resultados de confirmación.

Lógica testeable: `build_session`, `build_assistant`, `answer`,
`describe_proposal`, `describe_price_update`, `describe_price_history`,
`describe_result`, `describe_turn`, `confirm_pending`, `pending_proposals`,
`manual_proposals`, `locate_proposal`, `edit_proposal`,
`home_section`,
`products_section`, `product_detail_section`, `movements_section`,
`price_history_section`, `operations_section` y `section_renderers` no dependen
del runtime de Streamlit, de modo que las pruebas puedan inyectar un modelo
falso y una conexión en memoria, sin red y sin DeepSeek.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal

import streamlit as st
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from app.graph.state import GraphState, create_initial_state
from app.graph.workflow import build_workflow
from app.schemas.proposals import (
    ConfirmationResult,
    PriceUpdatePayload,
    Proposal,
    ProposalAction,
    ProposalPayload,
    ProposalStatus,
)
from app.services.inventory import InventoryService
from app.services.llm import LLMConfigurationError, get_llm
from app.services.ports import (
    PriceChangeRecord,
    ProductRecord,
    RecordNotFoundError,
)
from app.services.presentations import PresentationService
from app.services.price_history import PriceHistoryService
from app.services.proposals import ProposalError, ProposalService
from app.tools.inventory_tools import create_inventory_tools
from app.tools.proposal_tools import create_proposal_tools
from app.ui.sections.assistant import (
    CONFIRM_LABEL,
    CONFIRMATION_ERROR_PREFIX,
    NO_RESPONSE_MESSAGE,
    PROCESSING_KEY,
    AssistantActions,
    TurnContent,
    assistant_section,
)
from app.ui.sections.home import render_home
from app.ui.sections.movements import (
    MovementQuery,
    render_empty_catalog as render_empty_movements_catalog,
    render_empty_filtered_movements,
    render_empty_movements,
    render_invalid_period,
    render_movement_controls,
    render_movements,
    render_movements_error,
    render_movements_header,
)
from app.ui.sections.operations import (
    render_action_selector,
    render_aperture_form,
    render_closed_entry_form,
    render_closed_exit_form,
    render_compensation_form,
    render_created_proposal,
    render_empty_catalog as render_empty_operations_catalog,
    render_empty_presentations as render_empty_operations_presentations,
    render_loose_entry_form,
    render_loose_exit_form,
    render_operations_header,
    render_presentation_registration_form,
    render_price_update_form,
    render_product_registration_form,
    render_product_selector as render_operations_product_selector,
    render_product_update_form,
)
from app.ui.sections.price_history import (
    render_empty_catalog as render_empty_price_history_catalog,
    render_empty_presentation_history,
    render_empty_product_history,
    render_price_history as render_price_changes,
    render_price_history_error,
    render_price_history_header,
    render_presentation_filter,
    render_product_filter as render_price_history_product_filter,
)
from app.ui.sections.product_detail import (
    render_product_detail,
    render_product_detail_error,
    render_product_selector,
)
from app.ui.sections.products import (
    ACTIVE_STATUS_FILTER,
    EMPTY_CATALOG_MESSAGE as EMPTY_PRODUCTS_CATALOG_MESSAGE,
    EMPTY_FILTER_MESSAGE as EMPTY_PRODUCTS_FILTER_MESSAGE,
    EMPTY_SEARCH_MESSAGE as EMPTY_PRODUCTS_SEARCH_MESSAGE,
    INACTIVE_STATUS_FILTER,
    SKU_FIELD,
    ProductQuery,
    ProductRow,
    render_product_controls,
    render_products,
)
from app.ui.sections.proposals import (
    PENDING_PROPOSAL_NOTE,
    PresentationPriceView,
    ProposalReview,
    describe_price_update_fields,
    render_pending_proposals,
)
from app.ui.session import SESSION_KEY, SessionResources, build_session

ASSISTANT_KEY: str = "inventory_assistant"
# Clave de `st.session_state` donde vive el asistente de la sesión (D7).

MANUAL_PROPOSALS_KEY: str = "manual_proposals"
# Clave de `st.session_state` de las propuestas preparadas con los formularios
# de «Operaciones», en el orden en que se crearon (T049). No se persisten.

# `PENDING_PROPOSAL_NOTE` se reexporta desde `app.ui.sections.proposals` (T050):
# la conversación y la sección de propuestas comparten un único aviso de que
# nada se ejecutó todavía (D5).

ALREADY_CONFIRMED_MESSAGE: str = (
    "La propuesta ya está confirmada: no se volvió a ejecutar ninguna "
    "operación."
)
# Motivo devuelto al intentar confirmar una propuesta ya confirmada (T024).

NO_PENDING_PROPOSAL_MESSAGE: str = (
    "El turno no tiene una propuesta pendiente que confirmar."
)
# Motivo devuelto cuando el turno no tiene propuesta pendiente (T024).

PROPOSAL_NOT_AVAILABLE_MESSAGE: str = (
    "La propuesta ya no está disponible en la sesión: no se editó nada."
)
# Motivo devuelto al editar una propuesta que ya no se puede localizar (T051).

SECTION_KEY: str = "inventory_section"
# Clave de `st.session_state` de la sección elegida en la navegación (T041).

NAVIGATION_LABEL: str = "Sección"
# Etiqueta del control de navegación lateral (T041).

DEFAULT_SECTION: str = "Asistente"
# Sección que se muestra al abrir la interfaz; conserva el comportamiento
# anterior a la navegación lateral (T041, C20).

SECTION_LABELS: tuple[str, ...] = (
    "Inicio",
    "Productos",
    "Operaciones",
    "Propuestas pendientes",
    "Movimientos",
    "Historial de precios",
    "Asistente",
)
# Secciones de RF-09, en el orden en que las ofrece la navegación lateral (C1).

SECTION_PENDING_MESSAGE: str = (
    "Sección pendiente de implementación: todavía no muestra datos."
)
# Aviso de las secciones que la Fase 7 todavía no implementó (T041, C4).


@dataclass(frozen=True)
class Turn:
    """Turno de la conversación: el mensaje del usuario y su estado real."""

    user_message: str
    state: GraphState


@dataclass(frozen=True)
class ProposalSlot:
    """
    Ubicación de una propuesta dentro de la sesión, por identidad (T051).

    Conserva la propuesta encontrada y la operación que reemplaza solo esa
    referencia: la entrada de la lista manual o el `pending_proposal` de un
    turno del asistente. No usa la igualdad de Pydantic ni un índice combinado,
    de modo que dos propuestas idénticas se distinguen por objeto.
    """

    proposal: Proposal
    """Propuesta encontrada en la sesión."""

    replace: Callable[[Proposal], None]
    """Reemplaza solo la referencia encontrada por la propuesta editada."""


@dataclass
class Assistant:
    """
    Recursos de una sesión del asistente (D7, T039).

    Agrupa el contenedor de lectura de la sesión (`SessionResources`, D-G), el
    grafo compilado y el historial de la conversación. El asistente no compone
    recursos propios: comparte la conexión y los servicios de la lectura de la
    sesión, de modo que la interfaz use una única conexión por sesión. Vive en
    `st.session_state`, no se serializa y no se persiste.
    """

    session: SessionResources
    graph: CompiledStateGraph
    history: list[Turn] = field(default_factory=list)

    @property
    def connection(self) -> sqlite3.Connection:
        """Conexión SQLite compartida de la sesión (D-G)."""
        return self.session.connection

    @property
    def proposal_service(self) -> ProposalService:
        """Servicio que ejecuta las confirmaciones del usuario (T024)."""
        return self.session.proposal_service

    @property
    def presentation_service(self) -> PresentationService:
        """Servicio de lectura del precio de una presentación (T035)."""
        return self.session.presentation_service

    @property
    def price_history_service(self) -> PriceHistoryService:
        """Servicio de lectura del historial de precios (T035)."""
        return self.session.price_history_service


def build_assistant(
    *,
    model: BaseChatModel | None = None,
    connection: sqlite3.Connection | None = None,
    session: SessionResources | None = None,
) -> Assistant:
    """
    Compone el asistente de una sesión sobre su lectura (T023, T039).

    Resuelve primero el modelo —se puede inyectar, por ejemplo un modelo falso
    en las pruebas— y recién después la lectura: reutiliza el contenedor de
    sesión recibido o lo compone con la conexión indicada. Sobre esa lectura
    arma las herramientas y el grafo del asistente, que comparten la conexión y
    los servicios de las secciones de consulta.

    Args:
        model: Modelo de chat ya construido, o `None` para obtener el modelo
            configurado del proveedor.
        connection: Conexión SQLite ya abierta, o `None` para abrir la conexión
            del MVP con su esquema. Se ignora si se recibe `session`.
        session: Contenedor de lectura ya compuesto, o `None` para componerlo
            con `connection`.

    Returns:
        El asistente con su contenedor de sesión, su grafo y su historial
        vacío.

    Raises:
        LLMConfigurationError: Si el modelo configurado no se puede obtener.
    """
    resolved_model = get_llm() if model is None else model
    resolved_session = session or build_session(connection=connection)
    graph = build_workflow(
        model=resolved_model,
        inventory_tools=create_inventory_tools(
            resolved_session.inventory_service
        ),
        proposal_tools=create_proposal_tools(
            resolved_session.proposal_service
        ),
    )

    return Assistant(session=resolved_session, graph=graph)


def answer(assistant: Assistant, user_message: str) -> Turn:
    """
    Responde una consulta del usuario invocando el grafo del asistente (RF-05).

    La respuesta no ejecuta la operación propuesta: el estado que devuelve el
    grafo conserva `execution_result` en `None` y cualquier propuesta queda
    pendiente (D5).

    Args:
        assistant: Recursos de la sesión.
        user_message: Mensaje del usuario, sin transformar.

    Returns:
        El turno con el mensaje del usuario y el estado real resultante.
    """
    state: GraphState = assistant.graph.invoke(
        create_initial_state(user_message)
    )

    return Turn(user_message=user_message, state=state)


def describe_proposal(proposal: Proposal) -> str:
    """
    Describe una propuesta pendiente para mostrarla en la conversación (RF-05).

    Args:
        proposal: Propuesta real devuelta por el grafo, todavía pendiente.

    Returns:
        El texto con la acción, su estado y los datos de la operación
        propuesta, seguido del aviso de que nada se ejecutó.
    """
    lines = [
        f"Propuesta pendiente: `{proposal.action.value}` "
        f"({proposal.status.value})"
    ]
    for name, value in proposal.payload.model_dump(
        exclude={"action"}
    ).items():
        lines.append(f"- {name}: {value}")
    lines.append(PENDING_PROPOSAL_NOTE)

    return "\n\n".join(lines)


def _format_price(value: Decimal) -> str:
    """Formatea un precio con dos decimales para mostrarlo (T035)."""
    return f"{value:.2f}"


def _presentation_price_view(
    service: PresentationService,
    presentation_id: int,
    cache: dict[int, PresentationPriceView | None],
) -> PresentationPriceView | None:
    """
    Resuelve el precio vigente de una presentación para revisarla (T050).

    Convierte el registro real en el valor de presentación de la vista y
    captura `RecordNotFoundError`: la ausencia se informa en la revisión como un
    motivo explícito y nunca como un precio inventado. El caché es local al
    render y evita consultar dos veces la misma presentación.

    Args:
        service: Servicio de lectura de presentaciones de la sesión.
        presentation_id: Presentación cuyo precio vigente se va a mostrar.
        cache: Resultados ya resueltos en este render, por identificador.

    Returns:
        La presentación con su precio vigente, o `None` si no existe.
    """
    if presentation_id not in cache:
        try:
            record = service.get_presentation(presentation_id)
        except RecordNotFoundError:
            cache[presentation_id] = None
        else:
            cache[presentation_id] = PresentationPriceView(
                id=record.id,
                name=record.name,
                factor=record.factor,
                sale_price=record.sale_price,
            )

    return cache[presentation_id]


def describe_price_update(assistant: Assistant, proposal: Proposal) -> str:
    """
    Describe el cambio de precio propuesto con sus precios reales (T035).

    Lee la presentación y su precio vigente del inventario en el momento de
    mostrar la propuesta —sin ejecutar nada— y agrega el precio nuevo tal como
    quedó declarado, formateado con dos decimales. Si la presentación ya no
    existe, se muestra un motivo explícito en lugar de un dato ausente; una
    propuesta de otra acción se describe con `describe_proposal`. El detalle de
    los campos se delega en `describe_price_update_fields` para que la
    conversación y la sección de propuestas (T050) muestren lo mismo.

    Args:
        assistant: Recursos de la sesión.
        proposal: Propuesta devuelta por el grafo, pendiente o ya confirmada.

    Returns:
        El texto con la presentación afectada, el precio vigente, el precio
        nuevo y, mientras la propuesta está pendiente, el aviso de que nada se
        ejecutó todavía.
    """
    payload = proposal.payload
    if not isinstance(payload, PriceUpdatePayload):
        return describe_proposal(proposal)

    lines = [
        f"Propuesta de cambio de precio: `{proposal.action.value}` "
        f"({proposal.status.value})"
    ]
    presentation = _presentation_price_view(
        assistant.presentation_service,
        payload.presentation_id,
        {},
    )
    lines.extend(
        f"- {line}"
        for line in describe_price_update_fields(proposal, presentation)
    )
    if proposal.status is ProposalStatus.PENDING:
        lines.append(PENDING_PROPOSAL_NOTE)

    return "\n\n".join(lines)


def describe_price_history(records: list[PriceChangeRecord]) -> str:
    """
    Describe el historial de precios de una presentación (T035, RF-08).

    Los registros llegan ya consultados, en el orden cronológico que aporta el
    repositorio del historial (T030): no se reordenan ni se recalculan precios,
    solo se formatean con dos decimales para mostrarlos.

    Args:
        records: Cambios de precio de una presentación, del más antiguo al más
            reciente.

    Returns:
        El texto con una línea por cambio —precio anterior, precio nuevo,
        instante y tipo de evento— o el aviso de que no hay cambios.
    """
    if not records:
        return "Historial de precios: sin cambios registrados."

    lines = [f"Historial de precios de la presentación ({len(records)}):"]
    lines.extend(
        f"- {_format_price(record.previous_price)} → "
        f"{_format_price(record.new_price)} "
        f"({record.occurred_at.isoformat()}, {record.event_type})"
        for record in records
    )

    return "\n".join(lines)


def _describe_history_of(assistant: Assistant, presentation_id: int) -> str:
    """
    Describe el historial de la presentación o el motivo por el que falta
    (T035).

    La consulta del historial exige que la presentación exista; cuando la
    propuesta se refiere a una presentación que ya no está, se muestra un
    motivo explícito en lugar de romper el turno: mostrar una propuesta nunca
    presenta un dato ausente ni lanza una excepción a la interfaz.
    """
    try:
        records = (
            assistant.price_history_service.get_price_history_by_presentation(
                presentation_id
            )
        )
    except RecordNotFoundError:
        return (
            "Historial de precios: no se pudo consultar porque la "
            "presentación no existe."
        )

    return describe_price_history(records)


def describe_result(result: ConfirmationResult) -> str:
    """
    Describe el resultado real de una confirmación (T024, RF-05).

    Args:
        result: Resultado real devuelto por el servicio al confirmar.

    Returns:
        El texto con la acción, su estado confirmado y los datos del registro
        que el servicio persistió.
    """
    lines = [
        f"Operación confirmada: `{result.action.value}` "
        f"({result.status.value})"
    ]
    for name, value in asdict(result.result).items():
        lines.append(f"- {name}: {value}")

    return "\n\n".join(lines)


def confirm_pending(assistant: Assistant, turn: Turn) -> str | None:
    """
    Confirma la propuesta pendiente del turno en el servicio de propuestas
    (T024, RF-05).

    La confirmación la decide el usuario en la interfaz y la ejecuta el
    servicio con el estado vigente del inventario: no interviene el LLM. Solo
    si la operación tiene éxito se reemplaza la propuesta pendiente por su
    copia confirmada y se guarda el resultado real en `execution_result`; ante
    un error, el turno no cambia y el servicio no deja cambios parciales.

    La protección contra una confirmación repetida es la transición de estado
    del turno —una propuesta confirmada ya no ofrece el control—, válida para
    esta sesión de la interfaz, pero **no** es idempotencia durable: el
    servicio de propuestas no registra las propuestas ni detecta un reenvío.

    Args:
        assistant: Recursos de la sesión.
        turn: Turno cuya propuesta pendiente se confirma.

    Returns:
        `None` si la operación se aplicó, o el motivo por el que no se aplicó:
        el turno no tiene propuesta pendiente, la propuesta ya estaba
        confirmada, o el servicio la rechazó con un error tipado de negocio o
        con un registro inexistente.
    """
    proposal = turn.state["pending_proposal"]
    if proposal is None:
        return NO_PENDING_PROPOSAL_MESSAGE

    if proposal.status is not ProposalStatus.PENDING:
        return ALREADY_CONFIRMED_MESSAGE

    try:
        result = assistant.proposal_service.confirm(proposal)
    except (LookupError, ValueError) as error:
        return str(error)

    turn.state["pending_proposal"] = result.proposal
    turn.state["execution_result"] = result

    return None


def describe_turn(assistant: Assistant, turn: Turn) -> TurnContent:
    """
    Resuelve el contenido que muestra un turno de la conversación (T024, T048).

    Reproduce el contenido vigente del turno: la respuesta del asistente —o su
    aviso de reserva—, la propuesta pendiente con su detalle y el resultado
    real de una confirmación. Un cambio de precio se muestra con la
    presentación afectada, su precio vigente y el historial de precios de la
    presentación (T035), tanto mientras la propuesta está pendiente como
    después de confirmarla: leer ese estado no ejecuta la operación ni confirma
    nada de nuevo. El control de confirmación se habilita solo mientras la
    propuesta está pendiente.

    Args:
        assistant: Recursos de la sesión.
        turn: Turno con el mensaje del usuario y el estado real.

    Returns:
        La respuesta, los detalles ya descritos y si corresponde ofrecer el
        control de confirmación.
    """
    details: list[str] = []
    proposal = turn.state["pending_proposal"]
    if proposal is not None and isinstance(
        proposal.payload, PriceUpdatePayload
    ):
        details.append(describe_price_update(assistant, proposal))
        details.append(
            _describe_history_of(
                assistant, proposal.payload.presentation_id
            )
        )
    elif proposal is not None and proposal.status is ProposalStatus.PENDING:
        details.append(describe_proposal(proposal))
    result = turn.state["execution_result"]
    if result is not None:
        details.append(describe_result(result))

    return TurnContent(
        response=turn.state["response"] or NO_RESPONSE_MESSAGE,
        details=tuple(details),
        can_confirm=(
            proposal is not None
            and proposal.status is ProposalStatus.PENDING
        ),
    )


def _assistant_actions(assistant: Assistant) -> AssistantActions:
    """
    Compone los callbacks de la sección del asistente (T048).

    Los callbacks delegan en la lógica de esta interfaz: `answer` responde con
    el grafo, `confirm_turn` usa el único punto de confirmación
    (`confirm_pending`) y `describe_turn` resuelve el contenido del turno. La
    vista de la sección solo presenta y no accede a los servicios.

    Args:
        assistant: Recursos de la sesión.

    Returns:
        Los callbacks que la sección usa para responder, confirmar y describir
        un turno.
    """
    return AssistantActions(
        answer=lambda user_message: answer(assistant, user_message),
        confirm_turn=lambda turn: confirm_pending(assistant, turn),
        describe_turn=lambda turn: describe_turn(assistant, turn),
    )


def pending_proposals(assistant: Assistant | None) -> list[Proposal]:
    """
    Reúne las propuestas pendientes de la conversación (T041, T050).

    Es una lectura pura del historial: no confirma, no edita, no descarta y no
    escribe. Cada turno conserva la propuesta que el asistente preparó en
    `pending_proposal`, pendiente o ya confirmada, con el estado real; solo se
    reúnen las que siguen pendientes y el historial no se modifica.

    Args:
        assistant: Recursos de la sesión, o `None` si el asistente no se
            compuso porque el modelo no está configurado.

    Returns:
        Las propuestas pendientes de los turnos, en el orden en que se
        prepararon, o una lista vacía cuando no hay asistente.
    """
    if assistant is None:
        return []

    pending: list[Proposal] = []
    for turn in assistant.history:
        proposal = turn.state["pending_proposal"]
        if proposal is not None and proposal.status is ProposalStatus.PENDING:
            pending.append(proposal)

    return pending


def manual_proposals() -> list[Proposal]:
    """
    Reúne las propuestas creadas con los formularios de «Operaciones» (T049).

    Es una colección ordenada propia de la sesión: conserva cada propuesta en
    el orden en que se creó, sin persistirla y sin deduplicarla por igualdad
    con otras propuestas. No confirma, no edita, no descarta y no escribe.

    Returns:
        La lista de las propuestas manuales de la sesión, que se crea vacía la
        primera vez y se conserva entre ejecuciones de la interfaz.
    """
    stored = st.session_state.get(MANUAL_PROPOSALS_KEY)
    if stored is None:
        stored = []
        st.session_state[MANUAL_PROPOSALS_KEY] = stored

    return stored


def locate_proposal(
    assistant: Assistant | None,
    key: int,
) -> ProposalSlot | None:
    """
    Localiza una propuesta de la sesión por identidad de objeto (T051).

    Busca primero en la colección manual —la que crean los formularios de
    «Operaciones»— y, si no está, en el historial del asistente, en el
    `pending_proposal` de cada turno. La comparación usa `id(proposal)`: no
    recurre a la igualdad de Pydantic ni al índice de una lista combinada, de
    modo que dos propuestas idénticas se distingan.

    Args:
        assistant: Recursos de la sesión, o `None` si el asistente no se
            compuso porque el modelo no está configurado.
        key: Identidad de la propuesta, tal como la asignó `proposal_section`.

    Returns:
        La propuesta encontrada junto con la operación que reemplaza solo su
        referencia, o `None` si ya no está en la sesión.
    """
    manual = manual_proposals()
    for index, proposal in enumerate(manual):
        if id(proposal) == key:
            return ProposalSlot(
                proposal=proposal,
                replace=_manual_proposal_replacer(manual, index),
            )

    if assistant is None:
        return None

    for turn in assistant.history:
        proposal = turn.state["pending_proposal"]
        if proposal is not None and id(proposal) == key:
            return ProposalSlot(
                proposal=proposal,
                replace=_turn_proposal_replacer(turn),
            )

    return None


def edit_proposal(
    assistant: Assistant | None,
    session: SessionResources,
    key: int,
    payload: ProposalPayload,
) -> str | None:
    """
    Edita una propuesta pendiente y reemplaza solo su referencia (T051, C14).

    La edición la valida y la construye `ProposalService.edit`: exige que la
    propuesta siga pendiente y rechaza un payload de otra acción. El servicio
    no escribe el inventario y devuelve una propuesta nueva; esta interfaz
    reemplaza únicamente la referencia localizada, sin tocar las demás
    propuestas de la sesión. Si la propuesta ya no está —por ejemplo, porque
    se confirmó o se retiró entre el render y el envío—, se devuelve un motivo
    explícito sin modificar nada.

    Args:
        assistant: Recursos de la sesión, o `None` si no hay asistente.
        session: Contenedor de lectura de la sesión, con el servicio de
            propuestas.
        key: Identidad de la propuesta que se edita.
        payload: Payload editado y validado, con la misma acción.

    Returns:
        `None` si la referencia se reemplazó, o el motivo por el que no se
        editó: la propuesta ya no está disponible, dejó de estar pendiente o el
        payload cambió su acción.
    """
    slot = locate_proposal(assistant, key)
    if slot is None:
        return PROPOSAL_NOT_AVAILABLE_MESSAGE

    try:
        edited = session.proposal_service.edit(slot.proposal, payload)
    except ProposalError as error:
        return str(error)

    slot.replace(edited)

    return None


def _manual_proposal_replacer(
    manual: list[Proposal],
    index: int,
) -> Callable[[Proposal], None]:
    """Devuelve la operación que reemplaza una propuesta manual (T051)."""

    def replace(edited: Proposal) -> None:
        """Reemplaza solo la entrada localizada de la lista manual."""
        manual[index] = edited

    return replace


def _turn_proposal_replacer(
    turn: Turn,
) -> Callable[[Proposal], None]:
    """Devuelve la operación que reemplaza la propuesta de un turno (T051)."""

    def replace(edited: Proposal) -> None:
        """Reemplaza solo la propuesta pendiente del turno localizado."""
        turn.state["pending_proposal"] = edited

    return replace


def pending_section(section: str) -> Callable[[], None]:
    """
    Construye el renderer de una sección todavía no implementada (T041, C4).

    El aviso es explícito y no presenta ningún dato: una sección que todavía no
    consulta sus servicios no muestra valores vacíos, ceros ni resultados
    inventados.

    Args:
        section: Etiqueta de la sección, tal como aparece en la navegación.

    Returns:
        El renderer sin argumentos de esa sección.
    """

    def render() -> None:
        """Muestra el aviso de que la sección está pendiente (T041)."""
        st.header(section)
        st.info(f"«{section}»: {SECTION_PENDING_MESSAGE}")

    return render


def home_section(session: SessionResources) -> Callable[[], None]:
    """
    Construye el renderer de la sección «Inicio» (T043, RF-09).

    Las lecturas se hacen de forma perezosa: al construir el renderer no se
    consulta nada, y al mostrar la sección se ejecuta **una** lectura pública
    del catálogo —de la que se derivan los conteos de activos e inactivos
    (D-4)— junto con las alertas de stock bajo y de reposición del servicio de
    inventario (C3). La vista de `app.ui.sections.home` solo recibe esos datos
    ya consultados y los presenta.

    Args:
        session: Contenedor de lectura de la sesión, con el servicio de
            inventario.

    Returns:
        El renderer sin argumentos de la sección de inicio.
    """

    def render() -> None:
        """Consulta y muestra las alertas y los conteos de inicio (T043)."""
        inventory = session.inventory_service
        render_home(
            products=inventory.list_products(),
            low_stock=inventory.get_low_stock_products(),
            replenishment=inventory.get_replenishment_products(),
        )

    return render


def _matches_status(product: ProductRecord, status: str) -> bool:
    """
    Indica si un producto pasa el filtro de estado elegido (T044, C1).

    «Todos» conserva activos e inactivos; «Activos» e «Inactivos» son filtros
    de presentación y no modifican las consultas globales de alertas.

    Args:
        product: Producto de la búsqueda o del catálogo.
        status: Filtro elegido en la vista.

    Returns:
        `True` si el producto debe mostrarse con ese filtro.
    """
    if status == ACTIVE_STATUS_FILTER:
        return product.active
    if status == INACTIVE_STATUS_FILTER:
        return not product.active

    return True


def _product_candidates(
    inventory: InventoryService,
    query: ProductQuery,
) -> list[ProductRecord]:
    """
    Obtiene los productos del catálogo o de la búsqueda elegida (T044, B1).

    Una entrada vacía o solo con espacios **no** invoca los métodos de
    búsqueda: se muestra el catálogo completo. Con texto, la búsqueda usa la
    lectura pública correspondiente —por nombre o por SKU—, sin normalizar el
    texto ni implementar coincidencias parciales.

    Args:
        inventory: Servicio de consultas de la sesión.
        query: Valores elegidos en los controles de la sección.

    Returns:
        Los productos en el orden que devuelve el servicio.
    """
    if not query.is_search:
        return inventory.list_products()

    if query.field == SKU_FIELD:
        product = inventory.find_product_by_sku(query.search_text)
        return [] if product is None else [product]

    return inventory.find_products_by_name(query.search_text)


def _product_row(
    inventory: InventoryService,
    product: ProductRecord,
) -> ProductRow:
    """
    Compone la fila de un producto con su stock cerrado sumado (T044, A1).

    Consulta las presentaciones del producto y suma sus `closed_stock`, sin
    ponderarlos por el factor: el campo «Stock cerrado» es la suma de
    presentaciones cerradas, no el total equivalente.

    Args:
        inventory: Servicio de consultas de la sesión.
        product: Producto ya filtrado que se va a mostrar.

    Returns:
        La fila con los siete campos de C5.
    """
    presentations = inventory.get_product_presentations(product.id)

    return ProductRow(
        sku=product.sku,
        name=product.name,
        base_unit=product.base_unit,
        minimum_stock=product.minimum_stock,
        loose_units=product.loose_units,
        closed_stock=sum(
            presentation.closed_stock for presentation in presentations
        ),
        active=product.active,
    )


def products_section(session: SessionResources) -> Callable[[], None]:
    """
    Construye el renderer de la sección «Productos» (T044, RF-09).

    Las lecturas son perezosas: al construir el renderer no se consulta nada.
    Al mostrar la sección se leen los controles, se obtienen los productos por
    la lectura pública del servicio —catálogo o búsqueda exacta—, se aplica el
    filtro de estado **después** de la búsqueda y **antes** de consultar las
    presentaciones (C1), y recién entonces se compone cada fila con su stock
    cerrado (A1). Al final, solo si la lista visible no está vacía, se
    ofrece el detalle del producto elegido (T045), que reutiliza esa misma
    lista visible; con la lista vacía prevalece el mensaje de T044 y no se
    dibuja el selector del detalle. La vista de `app.ui.sections.products`
    solo presenta los datos y no accede a los servicios.

    Args:
        session: Contenedor de lectura de la sesión, con el servicio de
            inventario.

    Returns:
        El renderer sin argumentos de la sección de productos.
    """

    def render() -> None:
        """Consulta y muestra el listado, la búsqueda o el filtro (T044)."""
        inventory = session.inventory_service
        query = render_product_controls()
        candidates = _product_candidates(inventory, query)
        selected = [
            product
            for product in candidates
            if _matches_status(product, query.status)
        ]
        rows = [_product_row(inventory, product) for product in selected]

        if rows:
            render_products(rows)
        elif query.is_search and not candidates:
            render_products(
                rows, empty_message=EMPTY_PRODUCTS_SEARCH_MESSAGE
            )
        elif candidates:
            render_products(
                rows, empty_message=EMPTY_PRODUCTS_FILTER_MESSAGE
            )
        else:
            render_products(
                rows, empty_message=EMPTY_PRODUCTS_CATALOG_MESSAGE
            )

        if selected:
            product_detail_section(session, selected)()

    return render


def product_detail_section(
    session: SessionResources,
    products: Sequence[ProductRecord],
) -> Callable[[], None]:
    """
    Construye el renderer del detalle de un producto (T045, C9).

    El detalle se integra al final de la sección Productos y reutiliza la
    lista ya visible —después de la búsqueda y del filtro de T044—. Sin un
    producto seleccionado no se consulta ningún servicio: el selector conserva
    su opción centinela y no se presentan datos ausentes como valores reales
    (C4). Con una selección, las tres lecturas son perezosas y usan los
    servicios de la sesión; un registro inexistente se muestra como error
    explícito y **no** como un historial vacío (C21).

    Args:
        session: Contenedor de lectura de la sesión, con el servicio de
            inventario y el del historial de precios.
        products: Productos visibles en la sección, en el orden del servicio.

    Returns:
        El renderer sin argumentos del detalle de producto.
    """

    def render() -> None:
        """Lee y muestra el detalle del producto elegido (T045)."""
        selection = render_product_selector(products)
        if selection is None:
            return

        product = products[selection]
        inventory = session.inventory_service
        try:
            stock = inventory.get_product_stock(product.id)
            movements = inventory.get_movement_history(product.id)
            price_changes = (
                session.price_history_service.get_price_history_by_product(
                    product.id
                )
            )
        except RecordNotFoundError as error:
            render_product_detail_error(str(error))
            return

        render_product_detail(
            stock=stock,
            movements=movements,
            price_changes=price_changes,
        )

    return render


def movements_section(session: SessionResources) -> Callable[[], None]:
    """
    Construye el renderer de la sección «Movimientos» (T046, RF-09).

    Las lecturas son perezosas: al construir el renderer no se consulta nada.
    Al mostrar la sección se lee el catálogo público y, si está vacío, solo se
    informa el vacío sin controles. Con catálogo se dibujan los filtros de
    producto, tipo y período; sin un producto elegido no se consulta ningún
    historial (D1). La validación de un período invertido se resuelve antes de
    llamar al servicio (D4). La conversión a `datetime` ocurre una sola vez y
    sin zona horaria: la persistencia interpreta esos límites en la hora local
    argentina y aplica `[inicio, fin)` (D2). Un error de lectura se muestra
    como error explícito y nunca como un historial vacío.

    Args:
        session: Contenedor de lectura de la sesión, con el servicio de
            inventario.

    Returns:
        El renderer sin argumentos de la sección de movimientos.
    """

    def render() -> None:
        """Consulta y muestra el historial del producto elegido (T046)."""
        inventory = session.inventory_service
        products = inventory.list_products()
        render_movements_header()
        if not products:
            render_empty_movements_catalog()
            return

        query: MovementQuery = render_movement_controls(products)
        if query.product_index is None:
            return

        if (
            query.start_date is not None
            and query.end_date is not None
            and query.start_date > query.end_date
        ):
            render_invalid_period()
            return

        product = products[query.product_index]
        period_start = (
            None
            if query.start_date is None
            else datetime.combine(query.start_date, time.min)
        )
        period_end = (
            None
            if query.end_date is None
            else datetime.combine(
                query.end_date + timedelta(days=1), time.min
            )
        )

        try:
            movements = inventory.get_movement_history(
                product.id,
                query.kind_value,
                period_start=period_start,
                period_end=period_end,
            )
            presentation_names = (
                _presentation_names(inventory, product.id)
                if movements
                else {}
            )
        except (LookupError, ValueError) as error:
            render_movements_error(str(error))
            return

        if movements:
            render_movements(movements, presentation_names)
        elif query.has_filters:
            render_empty_filtered_movements()
        else:
            render_empty_movements()

    return render


def price_history_section(session: SessionResources) -> Callable[[], None]:
    """
    Construye el renderer de la sección «Historial de precios» (T047, C19).

    Las lecturas son perezosas: al construir el renderer no se consulta nada.
    Al mostrar la sección se lee el catálogo público y, si está vacío, solo se
    informa el vacío sin controles. Con catálogo se dibuja el selector de
    producto; sin un producto elegido no se consulta ningún historial (D1). Con
    una selección se leen las presentaciones **una vez** y se ofrece el
    selector que, por defecto, consulta todo el producto y, con una
    presentación concreta, solo su historial (D2). Un registro inexistente se
    muestra como error explícito y **nunca** como un historial vacío (C21). La
    vista de `app.ui.sections.price_history` solo presenta los datos ya
    consultados.

    Args:
        session: Contenedor de lectura de la sesión, con el servicio de
            inventario y el del historial de precios.

    Returns:
        El renderer sin argumentos de la sección de historial de precios.
    """

    def render() -> None:
        """Consulta y muestra el historial del producto elegido (T047)."""
        inventory = session.inventory_service
        products = inventory.list_products()
        render_price_history_header()
        if not products:
            render_empty_price_history_catalog()
            return

        product_index = render_price_history_product_filter(products)
        if product_index is None:
            return

        product = products[product_index]
        try:
            presentations = inventory.get_product_presentations(product.id)
            presentation_index = render_presentation_filter(
                presentations,
                product_id=product.id,
            )
            history = session.price_history_service
            if presentation_index is None:
                price_changes = history.get_price_history_by_product(
                    product.id
                )
            else:
                price_changes = history.get_price_history_by_presentation(
                    presentations[presentation_index].id
                )
        except RecordNotFoundError as error:
            render_price_history_error(str(error))
            return

        if price_changes:
            presentation_names = {
                presentation.id: presentation.name
                for presentation in presentations
            }
            render_price_changes(price_changes, presentation_names)
        elif presentation_index is None:
            render_empty_product_history()
        else:
            render_empty_presentation_history()

    return render


def _presentation_names(
    inventory: InventoryService,
    product_id: int,
) -> dict[int, str]:
    """
    Indexa los nombres de las presentaciones del producto (T046).

    La lectura se ejecuta solo cuando hay movimientos que mostrar. El mapa
    permite resolver el nombre de la presentación de cada movimiento sin
    repetir la lectura.

    Args:
        inventory: Servicio de consultas de la sesión.
        product_id: Producto cuyos movimientos se están mostrando.

    Returns:
        Los nombres de las presentaciones indexados por identificador.
    """
    return {
        presentation.id: presentation.name
        for presentation in inventory.get_product_presentations(product_id)
    }


def operations_section(session: SessionResources) -> Callable[[], None]:
    """
    Construye el renderer de la sección «Operaciones» (T049, C10–C12).

    La sección ofrece un formulario por acción admitida. Las lecturas son
    perezosas: al construir el renderer no se consulta nada y, al mostrar el
    formulario, esta interfaz —y no la vista— lee los productos, las
    presentaciones o los movimientos que el formulario necesita. Solo cuando un
    formulario devuelve un payload validado se crea la propuesta pendiente con
    `ProposalService.create`, que no escribe, y se conserva en la colección
    manual de la sesión. La vista de `app.ui.sections.operations` no consulta
    servicios, no confirma y no escribe.

    Args:
        session: Contenedor de lectura de la sesión, con el servicio de
            inventario y el servicio de propuestas.

    Returns:
        El renderer sin argumentos de la sección de operaciones.
    """

    def render() -> None:
        """Muestra los formularios y crea la propuesta enviada (T049)."""
        render_operations_header()
        action = render_action_selector()
        payload = _operation_payload(session.inventory_service, action)
        if payload is None:
            return

        proposal = session.proposal_service.create(payload)
        manual_proposals().append(proposal)
        render_created_proposal(action)

    return render


def _operation_payload(
    inventory: InventoryService,
    action: ProposalAction,
) -> ProposalPayload | None:
    """
    Consulta los datos del formulario elegido y devuelve su payload (T049).

    El alta de producto no requiere catálogo. Las demás acciones exigen un
    producto elegido y, cuando el formulario lo necesita, esta interfaz lee sus
    presentaciones o su historial de movimientos. La lectura del historial de
    la compensación no evalúa la cantidad compensable ni la disponibilidad: esa
    revalidación queda para la confirmación.

    Args:
        inventory: Servicio de consultas de la sesión.
        action: Acción cuyo formulario se muestra.

    Returns:
        El payload validado del formulario enviado, o `None` mientras no se
        envíe ninguno válido.
    """
    if action is ProposalAction.PRODUCT_REGISTRATION:
        return render_product_registration_form()

    products = inventory.list_products()
    if not products:
        render_empty_operations_catalog(action)
        return None

    product = render_operations_product_selector(products)
    if product is None:
        return None

    if action is ProposalAction.PRODUCT_UPDATE:
        return render_product_update_form(product)
    if action is ProposalAction.PRESENTATION_REGISTRATION:
        return render_presentation_registration_form(product)
    if action is ProposalAction.LOOSE_ENTRY:
        return render_loose_entry_form(product)
    if action is ProposalAction.LOOSE_EXIT:
        return render_loose_exit_form(product)
    if action is ProposalAction.COMPENSATION:
        return render_compensation_form(
            product,
            inventory.get_movement_history(product.id),
        )

    presentations = inventory.get_product_presentations(product.id)
    if not presentations:
        render_empty_operations_presentations()
        return None

    if action is ProposalAction.CLOSED_ENTRY:
        return render_closed_entry_form(product, presentations)
    if action is ProposalAction.CLOSED_EXIT:
        return render_closed_exit_form(product, presentations)
    if action is ProposalAction.APERTURE:
        return render_aperture_form(product, presentations)
    if action is ProposalAction.PRICE_UPDATE:
        return render_price_update_form(product, presentations)

    return None


def proposal_section(
    assistant: Assistant | None,
    session: SessionResources,
) -> Callable[[], None]:
    """
    Construye el renderer de la sección «Propuestas pendientes» (T041, T050,
    T051).

    La vista vive en `app.ui.sections.proposals` y solo recibe las propuestas
    pendientes de la sesión: las manuales de «Operaciones» —concatenadas con
    las del asistente, sin fusionarlas ni deduplicarlas (T049)— filtradas por
    estado pendiente. Esta interfaz resuelve, con el servicio de presentaciones
    de la sesión, el precio vigente que necesita la revisión de un cambio de precio,
    de modo que la sección funciona también con propuestas manuales y sin
    asistente. El precio de cada presentación se consulta como máximo una vez
    por render. Cada propuesta se identifica por objeto (`key=id(proposal)`) y
    la edición se delega en `edit_proposal`, que reemplaza solo su referencia y
    conserva el único punto de confirmación en `confirm_pending` (D-A).

    Args:
        assistant: Recursos de la sesión, o `None` si el asistente no se
            compuso porque el modelo no está configurado.
        session: Contenedor de lectura de la sesión, con el servicio de
            presentaciones y el de propuestas.

    Returns:
        El renderer sin argumentos de la sección de propuestas pendientes.
    """

    def render() -> None:
        """Muestra las propuestas pendientes y permite editarlas (T051)."""
        proposals = [
            *pending_proposals(assistant),
            *(
                proposal
                for proposal in manual_proposals()
                if proposal.status is ProposalStatus.PENDING
            ),
        ]
        cache: dict[int, PresentationPriceView | None] = {}
        reviews = [
            ProposalReview(
                proposal=proposal,
                key=id(proposal),
                presentation=_review_presentation(
                    session.presentation_service,
                    proposal,
                    cache,
                ),
            )
            for proposal in proposals
        ]
        render_pending_proposals(
            reviews,
            on_edit=lambda key, payload: edit_proposal(
                assistant,
                session,
                key,
                payload,
            ),
        )

    return render


def _review_presentation(
    service: PresentationService,
    proposal: Proposal,
    cache: dict[int, PresentationPriceView | None],
) -> PresentationPriceView | None:
    """
    Resuelve la presentación que necesita la revisión de una propuesta (T050).

    Solo el cambio de precio requiere un precio vigente; las demás acciones se
    revisan con los campos de su payload y no consultan el inventario.

    Args:
        service: Servicio de lectura de presentaciones de la sesión.
        proposal: Propuesta pendiente que se va a revisar.
        cache: Resultados ya resueltos en este render, por identificador.

    Returns:
        La presentación afectada, o `None` si la propuesta no es un cambio de
        precio o si la presentación ya no existe.
    """
    payload = proposal.payload
    if not isinstance(payload, PriceUpdatePayload):
        return None

    return _presentation_price_view(service, payload.presentation_id, cache)


def section_renderers(
    *,
    assistant: Assistant | None,
    unavailable_reason: str | None,
    session: SessionResources,
) -> dict[str, Callable[[], None]]:
    """
    Registra el renderer de cada sección de la navegación lateral (T041, C1).

    El registro cubre exactamente `SECTION_LABELS` y solo se invoca el renderer
    de la sección seleccionada. «Inicio», «Productos», «Movimientos»,
    «Historial de precios» y «Operaciones» reciben la lectura de la sesión y
    consultan sus datos al renderizarse, no al construir el registro (T043,
    T044, T046, T047, T049). Una sección sin implementación mostraría su aviso
    de pendiente y **no** leería ningún servicio (C4); las siete secciones de
    RF-09 están implementadas.

    Args:
        assistant: Recursos de la sesión, o `None` si el asistente no se
            compuso.
        unavailable_reason: Motivo de la indisponibilidad del asistente, o
            `None` si está disponible.
        session: Contenedor de lectura de la sesión, compartido por las
            secciones.

    Returns:
        El registro que asocia cada etiqueta de sección con su renderer sin
        argumentos.
    """
    return {
        "Inicio": home_section(session),
        "Productos": products_section(session),
        "Operaciones": operations_section(session),
        "Propuestas pendientes": proposal_section(assistant, session),
        "Movimientos": movements_section(session),
        "Historial de precios": price_history_section(session),
        "Asistente": assistant_section(
            None if assistant is None else assistant.history,
            actions=(
                None if assistant is None else _assistant_actions(assistant)
            ),
            unavailable_reason=unavailable_reason,
        ),
    }


def render_navigation() -> str:
    """
    Muestra la navegación lateral y devuelve la sección elegida (T041, C1).

    La navegación es de una sola página: un único control de opciones en el
    sidebar, sin el modo multipágina de Streamlit y con `app/ui/main.py` como
    único punto de entrada.

    Bajo el control, el sidebar muestra una identidad discreta del proyecto
    (T057). La etiqueta y las opciones del control no cambian.

    Returns:
        La etiqueta de la sección seleccionada, conservada en la sesión bajo
        `SECTION_KEY`.
    """
    section = st.sidebar.radio(
        NAVIGATION_LABEL,
        SECTION_LABELS,
        index=SECTION_LABELS.index(DEFAULT_SECTION),
        key=SECTION_KEY,
    )
    st.sidebar.caption(":material/inventory_2: Inventario · datos locales")

    return section


def main() -> None:
    """
    Punto de entrada de la interfaz de gestión del inventario (T023, T039,
    T041, RF-05, RF-09).

    Compone primero la lectura de la sesión —una conexión y un conjunto de
    servicios por sesión (D-G)— y, por separado, el asistente. Después muestra
    la navegación lateral con las secciones de RF-09 y renderiza **solo** la
    sección seleccionada, que por defecto es el asistente. Si el modelo no está
    configurado, la sección del asistente muestra el motivo y la aplicación
    **no** se abstiene: la lectura de la sesión queda compuesta y las demás
    secciones siguen accesibles.
    """
    st.set_page_config(
        page_title="AI Inventory Assistant",
        page_icon=":material/inventory_2:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.title("AI Inventory Assistant")
    st.caption(
        "Consultas de inventario en lenguaje natural sobre datos reales."
    )
    assistant = st.session_state.get(ASSISTANT_KEY)
    session = (
        assistant.session
        if assistant is not None
        else st.session_state.get(SESSION_KEY)
    )
    if session is None:
        session = build_session()
    st.session_state[SESSION_KEY] = session

    unavailable_reason: str | None = None
    if assistant is None:
        try:
            assistant = build_assistant(session=session)
        except LLMConfigurationError as error:
            unavailable_reason = f"El asistente no está disponible: {error}"
        else:
            st.session_state[ASSISTANT_KEY] = assistant

    section = render_navigation()
    renderers = section_renderers(
        assistant=assistant,
        unavailable_reason=unavailable_reason,
        session=session,
    )
    renderers[section]()


if __name__ == "__main__":
    main()
