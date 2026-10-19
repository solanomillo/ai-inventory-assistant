# Memoria del proyecto

## Estado actual
- Proyecto: AI Inventory Assistant.
- Rama de trabajo: main.
- Metodología: Spec-Driven Development (SDD).
- Documentos aprobados: `docs/constitution.md`, `specs/001-inventory-core/spec.md`
  (RF-01 a RF-07), `specs/001-inventory-core/plan.md` y `specs/001-inventory-core/tasks.md`
  (T001 a T026).

## Implementación
- T001 completada (commit `a20ca9c`): esqueleto del proyecto.
- T002 completada (commit `bf1e0a0`): configuración e inicialización del LLM.
- T003 completada (commit `663149e`): validadores numéricos (`app/domain/quantities.py`).
- T004 completada: reglas de dominio para productos y presentaciones.
  - Archivos: `app/domain/products.py`, `app/domain/presentations.py`,
    `tests/unit/test_product_rules.py`, `tests/unit/test_presentation_rules.py`.
  - Validación: el usuario confirmó que la suite completa pasó con 112 pruebas.
- T005 cerrada y aprobada (tras corrección y revisión independiente): reglas de dominio
  de stock compuesto y apertura.
  - Archivos: `app/domain/stock.py`, `tests/unit/test_stock_rules.py`.
  - Funcionalidad: validación de stock suelto/cerrado, cálculo del total equivalente
    y apertura de presentaciones con conservación del total.
  - Aperturas: rechaza la presentación base (factor 1) mediante
    `BasePresentationApertureError` y `validate_aperture_factor`.
  - Pruebas: `tests/unit/test_stock_rules.py`, incluida la prueba de rechazo de la
    apertura base y la prueba directa del invariante de `ApertureResult`.
  - Resultados reportados por implementer (no verificados independientemente por el
    coordinator ni el reviewer): 57 pruebas específicas y 169 en la suite completa.
  - La revisión independiente final fue APROBADO y los hallazgos de calidad de
    pruebas quedaron atendidos.
- T006 cerrada y aprobada (tras revisión independiente): reglas de dominio de movimientos
  y compensaciones con FIFO.
  - Archivos: `app/domain/movements.py`, `tests/unit/test_movement_rules.py`.
  - Modelo ratificado: `kind = compensación` (tipo de registro) con `effect_kind`
    (efecto opuesto sobre el stock).
  - Resultados reportados por implementer (no verificados por ejecución por el coordinator
    ni el reviewer): 87 pruebas específicas y 256 en la suite completa.
  - Revisión independiente final: APROBADO; la contradicción documental previa quedó resuelta.
- T007 cerrada y aprobada (tras revisión independiente): reglas de stock bajo y reposición.
  - Archivos: `app/domain/stock.py` (extensión), `tests/unit/test_stock_alerts.py`.
  - Criterios RF-04 cubiertos: stock bajo = total equivalente ≤ mínimo; consultas globales
    solo de activos; reposición solo con faltante > 0; faltante = max(0, mínimo − total);
    desactivados excluidos de las consultas globales.
  - Revisión independiente final: APROBADO, sin bloqueantes ni hallazgos importantes.
  - Resultados reportados por implementer (no verificados mediante ejecución independiente):
    38 pruebas específicas y 294 en la suite completa.
- T008 cerrada y aprobada (tras revisión independiente): esquema y conexión SQLite.
  - Archivos: `app/infrastructure/sqlite/schema.py`, `app/infrastructure/sqlite/connection.py`,
    `tests/integration/test_schema.py`.
  - Los nueve criterios de aceptación quedaron cubiertos según la revisión estática.
  - Revisión independiente final: APROBADO, sin bloqueantes.
  - Resultados reportados por implementer (el reviewer no ejecutó pytest; no verificados
    independientemente): 70 pruebas específicas y 364 en la suite completa.
- T009 cerrada y aprobada (tras revisión independiente): repositorios básicos y contrato.
  - Archivos: `app/services/ports.py`, `app/infrastructure/sqlite/repositories.py`,
    `tests/integration/test_repositories.py`.
  - Los seis criterios de aceptación están cubiertos según la revisión estática.
  - Revisión independiente final: APROBADO, sin bloqueantes.
  - Resultados reportados por implementer (el reviewer no ejecutó pytest; no verificados
    independientemente): 52 pruebas específicas y 416 en la suite completa.
- T010 cerrada y aprobada (tras revisión independiente): filtros temporales por período.
  - Archivos: `app/infrastructure/sqlite/instants.py`, `app/infrastructure/sqlite/repositories.py`,
    `tests/integration/test_period_filters.py` y `app/services/ports.py` (corrección I4).
  - Criterios de T010 cubiertos; hallazgo I4 corregido (contrato `ports.py` alineado con la
    implementación y `InvalidPeriodError` documentado).
  - Resultados reportados por implementer (no verificados independientemente por el coordinator
    ni el reviewer): 17 pruebas específicas y 433 en la suite completa.
- I3 resuelto (corrección previa a T011): representación canónica de `Decimal` en
  `app/infrastructure/sqlite/repositories.py` (serialización) y corrección de su docstring.
  - Serialización: texto decimal de punto fijo, sin notación científica ni ceros fraccionarios
    finales innecesarios; normaliza el cero negativo y preserva el valor numérico exacto.
  - Revisión independiente: APROBADO, sin bloqueantes ni hallazgos importantes.
  - El implementer reportó `pytest -q → 474 passed`; el coordinator y el reviewer no
    ejecutaron las pruebas independientemente.
- T011 cerrada y aprobada (tras revisión independiente): servicio de productos y presentaciones.
  - Archivos nuevos: `app/services/products.py`, `app/services/presentations.py`,
    `tests/integration/test_product_service.py`, `tests/integration/test_presentation_service.py`.
  - Archivo modificado: `app/infrastructure/sqlite/connection.py` (helper `transaction`:
    `BEGIN`/`COMMIT`/`ROLLBACK`, sin `BEGIN IMMEDIATE`).
  - Corrección I-1: validación de presentación base única en alta y cambio de factor, con
    dos pruebas de rechazo.
  - Los 11 criterios cubiertos según revisión estática.
  - Resultados reportados por implementer (no verificados mediante ejecución independiente por
    el coordinator ni el reviewer): 29 pruebas específicas y 531 en la suite completa.
- I2 resuelto (corrección previa a T012/T013/T014): contadores FIFO.
  - Contrato y persistencia: `MovementRepository.set_consumed_quantity(movement_id, consumed_quantity)`
    (`app/services/ports.py`) con implementación SQLite (`app/infrastructure/sqlite/repositories.py`):
    `UPDATE` condicional que solo cambia `consumed_quantity`, exige `entrada_cerrada` y respeta
    `consumed_quantity + compensated_quantity <= quantity`, preservando los campos históricos y
    `compensated_quantity`; se añadió `NotAClosedEntryError(ValueError)`.
  - T012 y T013 pueden reutilizar el mecanismo de consumo FIFO (junto con
    `consume_closed_entries_fifo` del dominio), sin duplicar la lógica.
  - T014 sigue necesitando una operación segura/condicional análoga para `compensated_quantity`
    (`set_compensated_quantity`, aún no implementada).
  - Pruebas reportadas por implementer (el reviewer no ejecutó pytest independientemente):
    21 específicas y 552 en la suite completa.
  - Revisión independiente: APROBADO; los cuatro hallazgos menores quedan como mejoras opcionales
    (sin abrir nueva corrección).
- T012 completada y aprobada (tras revisión independiente): entradas y salidas ordinarias.
  - Archivos: `app/services/movements.py`, `tests/integration/test_movement_service.py`.
  - Funcionalidad: entradas/salidas sueltas y cerradas; validación de motivo, costo (solo
    entradas) y precio histórico (`applied_price`); transacciones atómicas; consumo FIFO.
  - I1 resuelto: el `reason` se normaliza con `normalize_required_text()` (rechaza vacío,
    espacios, tabs y saltos).
  - Decisión confirmada: las operaciones cerradas (`entrada_cerrada`, `salida_cerrada`) se
    rechazan sobre presentaciones base (factor 1).
  - Pruebas reportadas por implementer (el reviewer no ejecutó pytest independientemente):
    91 específicas y 643 en la suite completa.
  - Revisión independiente: APROBADO, sin bloqueantes ni hallazgos importantes; los hallazgos
    menores quedan como mejoras opcionales (sin abrir nueva corrección).
- T013 completada y aprobada (tras revisión independiente): apertura de presentaciones cerradas.
  - Archivos: `app/services/movements.py` (`register_aperture()`), `tests/integration/test_aperture_service.py`.
  - Reutiliza `open_presentations()` (valida cantidad, factor y stock; la presentación base,
    factor 1, se rechaza con `BasePresentationApertureError` del dominio),
    `consume_closed_entries_fifo()` y `set_consumed_quantity()`.
  - Atomicidad: movimiento + `closed_stock` + `loose_units` + contadores FIFO en una única
    transacción; el `BEGIN IMMEDIATE` se incorporó después, en T016.
  - Pruebas reportadas por implementer: 30 específicas, 91 de regresión de T012 y 673 en la
    suite completa; el código de salida de pytest no fue observable y el reviewer no ejecutó
    las pruebas. `git diff --check` sin errores.
  - Revisión independiente: APROBADO, sin bloqueantes ni hallazgos importantes; los hallazgos
    M1–M6 quedan como mejoras opcionales (sin abrir nueva corrección).
- Decisión (MVP): las compensaciones de `salida_cerrada` quedan fuera del MVP; T014 debe
  rechazarlas sin modificar movimientos, `closed_stock` ni contadores FIFO. Se mantienen las
  compensaciones permitidas de `entrada_cerrada`, `entrada_suelta` y `salida_suelta`.
- T006 cerrada y aprobada (tras revalidación y revisión independiente): el dominio rechaza
  `CLOSED_EXIT` como movimiento original compensable con `NotCompensableError`; se conservan las
  compensaciones permitidas de `CLOSED_ENTRY`, `LOOSE_ENTRY` y `LOOSE_EXIT`.
  - Archivos: `app/domain/movements.py` (rechazo en `validate_compensable_kind`; eliminada la
    clave `_OPPOSITE_KINDS[CLOSED_EXIT]`) y `tests/unit/test_movement_rules.py`.
  - Pruebas reportadas por implementer (ni el coordinador ni el reviewer las ejecutaron de forma
    independiente): 90 unitarias y 676 en la suite completa.
  - Revisión independiente: APROBADO, sin bloqueantes ni hallazgos importantes; los hallazgos
    menores quedan como mejoras opcionales (sin abrir nueva corrección).
  - Persistencia: el esquema SQLite no impide vincular una compensación a una `salida_cerrada`;
    T014 lo valida desde el servicio (implementado).
- T014 cerrada y aprobada (tras revisión independiente y un ciclo único de corrección de I1).
  - Archivos: `app/services/movements.py` (`register_compensation`), `app/services/ports.py`,
    `app/infrastructure/sqlite/repositories.py` (`set_compensated_quantity`),
    `tests/integration/test_compensation_service.py` y `tests/integration/test_repositories.py`.
  - Compensaciones permitidas: `entrada_cerrada` (cantidad compensable FIFO + `closed_stock`),
    `entrada_suelta` y `salida_suelta` (pendiente = cantidad original − compensaciones vinculadas
    por `original_movement_id`); se rechazan aperturas, compensaciones encadenadas y `salida_cerrada`
    sin modificar movimientos, stock ni contadores.
  - I1 resuelto: prueba discriminante que demuestra que la pendiente de un movimiento suelto solo
    cuenta sus propias compensaciones.
  - Pruebas reportadas por implementer (no verificadas de forma independiente): 31 específicas y
    714 en la suite completa. El reviewer no pudo verificar el alcance mediante Git (sin shell).
  - Revisión independiente: APROBADO en la segunda revisión, sin bloqueantes ni hallazgos
    importantes; m1–m7 y O1–O3 quedan como mejoras opcionales o deuda técnica.
- T015 cerrada y aprobada (tras revisión independiente).
  - Archivos creados: `app/schemas/__init__.py` (vacío), `app/schemas/proposals.py`,
    `app/services/proposals.py`, `tests/integration/test_proposal_service.py`. Modificados:
    `app/services/products.py` (`update` atómico) y `tests/integration/test_product_service.py`.
  - Alcance: contratos Pydantic (`ProposalAction` con las nueve acciones, `ProposalStatus`,
    payloads discriminados por acción, `Proposal`, `ConfirmationResult`), servicio sin estado
    interno que recibe y devuelve la propuesta por valor, confirmación explícita con despacho a
    los servicios destino y `CONFIRMED` solo tras éxito; sin persistir propuestas (no usa la
    tabla `proposals`).
  - Pruebas reportadas (ejecutadas en una sesión de verificación delegada; ni el coordinador ni
    el reviewer las ejecutaron): 42 en `test_proposal_service.py`, 29 en `test_product_service.py`
    y 757 en la suite completa; `git diff --check` sin errores de espacios (avisos LF→CRLF). El
    reviewer no pudo verificar el alcance mediante Git (sin shell).
  - Revisión independiente: APROBADO, sin bloqueantes ni hallazgos importantes.
  - Limitación stateless: sin registro, reenviar una propuesta PENDING obsoleta no se detecta
    (solo se rechaza la copia CONFIRMED devuelta); T016 no la resuelve (limitación del MVP).
- T016 cerrada y aprobada (tras revisión independiente y un ciclo único de corrección).
  - Alcance (D1–D5): presentación base única protegida por el servicio **y** por un índice único
    parcial SQLite; transacciones compartidas con `BEGIN IMMEDIATE`; `timeout` configurable
    (5 s por defecto, sin reintentos); prechequeo de SKU dentro de la transacción (el `UNIQUE`
    sigue siendo la garantía); actualización de producto atómica bajo la transacción compartida.
  - Archivos: `app/infrastructure/sqlite/{connection,schema,repositories}.py`,
    `app/services/{products,presentations}.py`, `tests/concurrency/{__init__,test_concurrency}.py`
    y ajustes de compatibilidad en `tests/integration/{test_presentation_service,
    test_proposal_service,test_movement_service,test_schema}.py`.
  - Pruebas reportadas (ejecutadas en una sesión de verificación delegada; ni el coordinador ni el
    reviewer las ejecutaron): 13 en concurrencia, 72 del esquema y 772 en la suite completa;
    `git diff --check` sin errores de espacios (avisos LF→CRLF). No se verificaron de forma
    independiente mediante shell/Git.
  - Revisión independiente: APROBADO en la segunda revisión, sin bloqueantes ni hallazgos
    importantes; la corrección M3 se verificó por razonamiento estático (no hubo mutación).
  - Limitaciones (M6/M7): `PresentationService.change_sale_price` y `ProductService.deactivate`
    conservan ventanas de carrera porque leen y escriben sin transacción propia (fuera del alcance
    de T016); el límite de compensación de movimientos sueltos depende solo de la serialización de
    escritores (sin contador por movimiento ni restricción en la base).
- T017 cerrada y aprobada (tras revisión independiente).
  - Archivos creados: `app/prompts/__init__.py` (vacío), `app/prompts/prompts.py`,
    `tests/ai/__init__.py` (vacío), `tests/ai/test_prompts.py`.
  - Alcance: dos constantes centralizadas en español (`SUPERVISOR_PROMPT` e `INVENTORY_AGENT_PROMPT`)
    con las responsabilidades del Supervisor (clasifica y deriva) y del Inventory Agent (consulta y
    prepara propuestas), uso exclusivo de datos reales, prohibición de escribir directamente en
    SQLite y confirmación explícita del usuario fuera de la decisión autónoma del LLM.
    `app/prompts/prompts.py` es un módulo hoja (sin imports, sin configuración ni red).
  - Pruebas reportadas por la sesión de implementación (ni el coordinador ni el reviewer las
    ejecutaron): 14 específicas y 786 en la suite completa; `git diff --check` sin errores de
    espacios (avisos LF→CRLF). No se verificaron de forma independiente mediante shell/Git.
  - Revisión independiente: APROBADO, sin bloqueantes ni hallazgos importantes; M1–M6 quedan como
    deuda técnica menor (ver «Decisiones pendientes»).
- T018 cerrada y aprobada (tras revisión independiente).
  - Archivos creados: `app/graph/__init__.py` (vacío), `app/graph/state.py`, `tests/ai/test_state.py`.
  - Alcance: `GraphState(TypedDict)` con exactamente `user_message: str`,
    `pending_proposal: Proposal | None` y `execution_result: ConfirmationResult | None`, más
    `create_initial_state(user_message)` (ambos opcionales en `None`). Reutiliza los contratos de
    `app.schemas.proposals`; el estado es pasivo: no confirma operaciones, no escribe en SQLite, no
    invoca al LLM y no conecta agentes, herramientas ni grafo.
  - Propagación real entre nodos: pendiente hasta que exista el workflow (T022).
  - Pruebas reportadas por la sesión de implementación (ni el coordinador ni el reviewer las
    ejecutaron): 6 específicas y 792 en la suite completa; `git diff --check` sin errores de
    espacios. No se verificaron de forma independiente mediante shell/Git.
  - Revisión independiente: APROBADO, sin bloqueantes ni hallazgos importantes; M1–M3 y O1 quedan
    como deuda técnica menor (ver «Decisiones pendientes»).
- T019 cerrada y aprobada por el usuario.
  - Archivos creados: `app/services/inventory.py`, `app/tools/__init__.py` (vacío),
    `app/tools/inventory_tools.py`, `tests/ai/test_inventory_tools.py`.
  - Alcance: servicio de consultas de solo lectura `InventoryService` (dependencias inyectadas con
    los contratos de `app/services/ports.py`; reglas derivadas delegadas en `app/domain/stock.py`)
    y 7 herramientas LangChain `@tool` (entradas Pydantic y salidas estructuradas explícitas) en
    `app/tools/inventory_tools.py`, sin acceso directo a SQLite ni a repositorios de
    infraestructura. Búsqueda por SKU inexistente → `None`; sin coincidencias por nombre → `[]`;
    `product_id` inexistente → `RecordNotFoundError`; período inválido → `InvalidPeriodError`; sin
    paginación.
  - Código y pruebas aprobados por lectura independiente, sin bloqueantes pendientes. I1
    (documental) resuelto actualizando el alcance de T019 en `specs/001-inventory-core/tasks.md`.
  - Pruebas reportadas por el implementer (no ejecutadas ni verificadas de forma independiente por
    el coordinador ni el reviewer): 30 específicas y 822 en la suite completa; `git diff --check`
    sin errores de espacios.
  - M1–M7 registrados como deuda técnica menor, sin ciclo adicional de correcciones (ver
    «Decisiones pendientes»).
- T020 cerrada y aprobada por el usuario (tras revisión independiente).
  - Archivos creados: `app/tools/proposal_tools.py`, `tests/ai/test_proposal_tools.py`.
  - Alcance: `create_proposal_tools(service) -> tuple[BaseTool, ...]` con **diez** herramientas —
    nueve de creación (una por acción de `ProposalAction`, incluida `apertura`) y `edit_proposal` —
    que validan su entrada con los payloads Pydantic existentes de `app/schemas/proposals.py` y
    delegan **exclusivamente** en `ProposalService` (crear/editar), **sin herramienta ni llamada de
    confirmación** (la confirmación es del usuario, en la UI, fuera del alcance). Salida reutilizada:
    el contrato `Proposal` existente (siempre `PENDING`).
  - Revisión independiente: APROBADO, cero bloqueantes y cero hallazgos importantes; M1–M6 quedan
    como deuda técnica menor, sin ciclo adicional de correcciones (ver «Decisiones pendientes»).
  - Pruebas reportadas por el implementer (pytest **no** ejecutado ni verificado de forma
    independiente por el coordinador ni el reviewer): 39 específicas y 861 en la suite completa;
    `git diff --check` sin errores de espacios.
- T021 cerrada y aprobada por el usuario (tras revisión independiente).
  - Archivos creados: `app/agents/__init__.py` (vacío), `app/agents/inventory.py`,
    `tests/ai/test_inventory_agent.py`.
  - Alcance: fábrica `create_inventory_agent(*, model, tools) -> CompiledStateGraph` con
    `langchain.agents.create_agent`, modelo y herramientas **inyectados**, reutilizando
    `INVENTORY_AGENT_PROMPT`. Recibe las **17** herramientas (7 de T019 + 10 de T020, incluida
    `edit_proposal`), **sin** confirmación ni acceso directo a infraestructura.
  - Revisión independiente: APROBADO, sin bloqueantes; m1–m5 como deuda técnica menor (ver
    «Decisiones pendientes»).
  - Pruebas reportadas por el implementer (pytest **no** ejecutado ni verificado de forma
    independiente por el coordinador ni el reviewer): 11 específicas y 872 en la suite completa.
  - I1: dependencia técnica pendiente para T022/T025 (`check_same_thread=True` frente a la
    ejecución en hilos del `ToolNode`); **no** resuelta en T021. D4 diferido:
    `app/prompts/prompts.py` sin modificar.
- T022 cerrada y aprobada por el usuario (tras revisión independiente).
  - Archivos creados/modificados: `app/agents/supervisor.py`, `app/graph/workflow.py`,
    `app/graph/state.py` (solo `response`), `tests/ai/test_supervisor.py`, y la corrección de
    `tests/ai/test_state.py` (T018) por la extensión D1 de `GraphState`.
  - Alcance: flujo `START → supervisor → inventory_agent → END` con Supervisor **determinista**
    (`Command(goto=...)`, sin LLM) y `build_workflow(*, model, inventory_tools, proposal_tools)`,
    que envuelve las 10 herramientas de propuesta (delegando en `tool.func`, una ejecución) para
    capturar el `Proposal` y hace el puente `response` ← último `AIMessage`, `pending_proposal` ←
    última propuesta. `execution_result` queda `None` (la confirmación es de la UI, T024).
  - Revisión independiente: APROBADO (tras resolver la colisión con `tests/ai/test_state.py`, cuya
    corrección se re-revisó APROBADO); deuda menor: envoltura sin aseverar
    `return_direct`/`response_format`, `_last_response_text` con `str(...)`, docstring de
    `test_state.py` sin `response` (ver «Decisiones pendientes»).
  - Pruebas reportadas por el implementer (pytest **no** ejecutado ni verificado de forma
    independiente por el coordinador ni el reviewer): 10 específicas y 882 en la suite completa.
  - I1 (SQLite threading): **pendiente para T025**; `connect()` **no** modificado en T022.
- T023 cerrada y aprobada por el usuario (tras revisión independiente).
  - Archivos creados/modificados: `app/infrastructure/sqlite/connection.py` (D1:
    `check_same_thread=False`), `app/ui/main.py`, `app/ui/__init__.py` (vacío),
    `tests/ui/__init__.py` (vacío), `tests/ui/test_main.py`, `tests/integration/test_ui.py`.
  - Alcance: interfaz Streamlit como raíz de composición (`connect() → repositorios → servicios →
    herramientas → get_llm() → build_workflow`), dependencias inyectables
    (`build_assistant(*, model=None, connection=None)`), sesión por `st.session_state` (sin
    `st.cache_resource` ni persistencia), flujo que muestra `response` y `pending_proposal`.
    **No** confirma propuestas ni asigna `execution_result` (T024).
  - Revisión independiente: APROBADO, sin bloqueantes ni importantes; 5 observaciones menores como
    deuda técnica (ver «Decisiones pendientes»).
  - Pruebas reportadas por el implementer (pytest **no** ejecutado ni verificado de forma
    independiente por el coordinador ni el reviewer): 14 (UI) + 1 (integración) y 897 en la suite.
  - I1 (SQLite threading): resuelto en producción por D1; `check_same_thread=False` **no** garantiza
    por sí solo la seguridad concurrente.
- T024 cerrada y aprobada por el usuario (tras revisión independiente).
  - Archivos modificados: `app/ui/main.py`, `tests/ui/test_main.py`, `tests/integration/test_ui.py`.
  - Alcance: revisión y confirmación explícita de propuestas (sin edición). `Assistant` expone
    `proposal_service` (D4); la confirmación es un botón dedicado de la UI (fuera del chat, **sin**
    pasar por el LLM) que invoca `ProposalService.confirm`; **solo en éxito** se reemplaza
    `pending_proposal` por la copia `CONFIRMED` y se asigna `execution_result`; se renderiza el
    `ProposalOutcome` real; errores tipados sin cambios parciales.
  - Guard de reenvío: transición de estado del turno (propuesta → `CONFIRMED`, control retirado,
    botón deshabilitado en curso); válido por sesión, **no** idempotencia durable.
  - Revisión independiente: APROBADO, sin bloqueantes ni importantes; 3 observaciones menores como
    deuda técnica (ver «Decisiones pendientes»).
  - Pruebas reportadas por el implementer (pytest **no** ejecutado ni verificado de forma
    independiente por el coordinador ni el reviewer): 21 (UI) + 2 (integración) y 905 en la suite.
- T025 cerrada y aprobada por el usuario (tras revisión independiente).
  - Archivo creado: `tests/integration/test_end_to_end.py` (fixtures locales; modelo falso; SQLite
    `:memory:` con `connect()` real; repositorios/servicios reales).
  - Alcance: flujo completo end-to-end (consulta → propuesta PENDING sin efectos → confirmación
    explícita → apertura → rechazo sin cambios parciales → consulta final coherente), con aserciones
    en SQL independiente (stock suelto 4→7→13; cerrado 2→1; movimientos correctos; total 16→19→19) e
    instantánea + `total_changes` en propuesta/rechazo.
  - D2: caso con dos `tool_calls` en el mismo `AIMessage` vía `ToolNode` sobre `connect()` real;
    salvedad de que `check_same_thread=False` **no** demuestra por sí solo seguridad concurrente
    completa.
  - Revisión independiente: APROBADO, sin bloqueantes ni importantes; M1–M2 como deuda técnica (ver
    «Decisiones pendientes»).
  - Pruebas reportadas por el implementer (pytest **no** ejecutado ni verificado de forma
    independiente por el coordinador ni el reviewer): 2 específicas y 907 en la suite.
  - T025 no tiene RF propio; la trazabilidad RF-01 a RF-07 corresponde a T026.
- T026 cerrada y aprobada por el usuario (tras revisión independiente).
  - Matriz de trazabilidad RF-01 a RF-07 completada a nivel criterio → archivo:caso (en
    `specs/001-inventory-core/tasks.md`), con referencias T010/T025/T016/T023 incorporadas y
    etiquetas de estado explícitas (reportado vs. revisado por lectura; estructural; brecha).
  - Revisión independiente: APROBADO; H1/H2/H3 resueltos en el ciclo único de corrección y
    re-revisados APROBADO. Observación terminológica «modelo falso»/«proveedor falso» opcional.
  - Brecha conservada (no resuelta): RF-01 `spec.md:49` (consulta por SKU/nombre de un producto
    desactivado) sin caso de prueba identificable.
  - Limitaciones conocidas (no resueltas): RF-06 como evidencia estructural sin evidencia
    conductual; integración real con DeepSeek **no validada** (modelo falso; identificador
    pendiente).
- RF-08 (actualización del precio de venta e historial): especificación ampliada y aprobada. T027
  (documental) y **T028 (Persistencia duradera y migración segura del esquema)** cerradas y
  aprobadas. T028 implementó: base SQLite durable en archivo configurable (`INVENTORY_DB_PATH`,
  default `data/inventory.sqlite3` independiente del CWD), `PRAGMA user_version` con `create_schema`
  idempotente/transaccional y rechazo seguro de bases incompatibles; `data/` ignorado en
  `.gitignore`. **I1 resuelto** (fallback ante `INVENTORY_DB_PATH` ausente o vacío). Observaciones
  menores pendientes (sin reabrir): comentario opcional en `.env.example` (no aplicado por permisos,
  funcionalmente irrelevante) y robustez de `resolve_database_path()` ante asignación posterior de
  vacío. **T029 (Esquema: tabla del historial de precios e índice)** cerrada y aprobada: tabla
  `price_changes` + índice `(presentation_id, occurred_at)` como `MIGRATIONS[1]` (`SCHEMA_VERSION`
  0→1), sin triggers/índice por producto. Observaciones menores O1–O3 (sin reabrir).
  **T030 (Repositorio y puerto del historial de precios)** cerrada y aprobada: `PriceChangeRecord`
  (frozen) + protocolo `PriceHistoryRepository` (`add`/`list_by_presentation`/`list_by_product`) en
  `ports.py`, y `SqlitePriceHistoryRepository` en `repositories.py` (sin edición/borrado ni
  transacciones propias; coherencia producto↔presentación para T031). Observaciones menores O1–O3
  (sin reabrir). **T031 (Servicio de actualización de precio atómico)** cerrada y aprobada:
  `PresentationService.change_sale_price` en una única transacción `BEGIN IMMEDIATE` (valida
  existencia→activo→precio, `set_sale_price` + `price_history.add` con `previous_price` previo al
  UPDATE y `occurred_at=_utc_now()`); `price_history` inyectado en los 9 sitios de construcción.
  Observaciones menores O1–O3 (sin reabrir). **Pendiente documental (D9):** las referencias
  obsoletas a M6 en `tasks.md:545`/`plan.md:145` siguen sin actualizar (autorización aparte).
  **T032 (Esquema Pydantic y acción de propuesta `cambio_precio`)** cerrada y aprobada:
  `ProposalAction.PRICE_UPDATE="cambio_precio"` + `PriceUpdatePayload` (sin `product_id`,
  `MonetaryValue`/`gt=0`) en la unión `ProposalPayload`; exclusiones reescritas; `_dispatch`/
  `ProposalOutcome` intactos (confirmar una propuesta de precio es fail-closed hasta T033).
  Observaciones documentales (sin reabrir): referencias «nueve acciones» y docstring de
  `proposal_tools.py` (corregidos en T033). **T033 (Herramienta del asistente y despacho de la
  nueva acción)** cerrada y aprobada: `propose_price_update` + rama `_dispatch`
  `PriceUpdatePayload → change_sale_price`; superficies 10→11 (herramientas) y 17→18 (agentes);
  correspondencia 1:1 restaurada. Deuda documental pendiente (fuera de alcance): en
  `tests/integration/test_proposal_service.py` persiste «nueve acciones» y un docstring sobre la
  confirmación de precio (llegó en T033). **T034 (Consulta del historial de precios: servicio y
  repositorio, sin herramienta nueva)** cerrada y aprobada: `PriceHistoryService`
  (`app/services/price_history.py`) consulta el historial por presentación y producto (solo lectura;
  pre-chequea existencia → `RecordNotFoundError`; orden delegado en T030) y
  `PresentationService.get_presentation(presentation_id)` resuelve la lectura pública (sin exigir
  producto activo). Deuda documental/observaciones pendientes (sin reabrir): I1 (`plan.md:179` sin
  `price_history.py`) y O1–O5. **T035 (Interfaz: precio vigente, precio nuevo y confirmación)**
  cerrada y aprobada: `app/ui/main.py` expone `presentation_service`/`price_history_service`,
  `describe_price_update`/`describe_price_history` (con `RecordNotFoundError` capturado y sin `None`),
  precios con 2 decimales en la UI, bloque de precio pendiente y confirmado; un único punto de
  escritura. O2–O5 como deuda (sin reabrir) y O1 corregida (referencias de la matriz de `test_main.py`).
  **T036 (Pruebas de integración de la actualización y el historial)** cerrada y aprobada: 11 casos
  de integración en `test_presentation_service.py` (precio anterior real), `test_repositories.py`
  (`event_type` literal + refuerzo estructural append-only) y `test_proposal_service.py` (C2/C1/C5/C4/
  C6/C7/C11/C12; docstring corregido); sin idempotencia durable. O1–O6 como deuda (sin reabrir).
  **T037 (End-to-end, persistencia tras reinicio y trazabilidad de RF-08)** cerrada y aprobada:
  casos E2E en `tests/integration/test_end_to_end.py` (flujo con base en archivo y ruta configurada;
  cierre/reapertura → persistencia + `schema_version`; atomicidad con base utilizable; presentación
  base; `snapshot` con `price_changes`) y **matriz de trazabilidad de RF-08** (13 filas C1–C13, C13
  estructural). **Fase 6 (T027–T037) completa.** Deuda documental aceptada (no bloqueante): I-A
  (`tasks.md:461`), I-B (`tasks.md:823`), D9/M6, I1 (`plan.md:179`); y la nota de «reinicio» =
  cierre/reapertura (no reinicio real de proceso). **No hay próxima tarea de implementación
  autorizada** (Fase 6 cerrada).
- **RF-09 (interfaz de gestión del inventario):** RF-09 formalizada y aprobada (T038, documental).
  **T039 (Composición de lectura sin LLM y contenedor de sesión)** cerrada y aprobada (tras **segunda
  revisión independiente**): `app/ui/session.py` (nuevo; `SESSION_KEY`, `SessionResources`,
  `open_connection()`, `build_session(*, connection=None)` —compone repositorios y servicios de
  **lectura** sin `get_llm()` ni grafo—) y `app/ui/main.py` (`build_assistant(*, model=None,
  connection=None, session=None)` resuelve el modelo **primero** y reutiliza la sesión; `Assistant`
  referencia el contenedor con propiedades de compatibilidad `connection`/`proposal_service`/
  `presentation_service`/`price_history_service` y conserva `history`, `answer`, `confirm_pending` y
  el **único** punto `.confirm(`; `main()` compone la **lectura primero** y el asistente por separado
  en `try/except LLMConfigurationError`, sin bloquear las consultas sin modelo).
  - Pruebas nuevas/adaptadas en `tests/ui/test_main.py` (lectura sin modelo, sesión única compartida,
    ausencia de escritura, asistente inyectado y reutilización de `session=`). Pruebas **reportadas**
    por el implementer (pytest **ejecutado** en esa sesión; el reviewer no las ejecutó): 34 en
    `tests/ui/test_main.py` y 1025 en la suite completa.
  - Primer ciclo de revisión: **CAMBIOS NECESARIOS** por el hallazgo importante **I1** (la rama
    `build_assistant(session=…)` no tenía prueba); resuelto en un **ciclo único** que añadió
    `test_build_assistant_reuses_the_received_session`. **Segunda revisión independiente: APROBADO**,
    sin bloqueantes ni hallazgos importantes. Observaciones opcionales O1–O2 y deudas previas quedan
    diferidas (sin reabrir).
  - A continuación se abordó **T040** (ver la viñeta siguiente).
- **T040 (Lectura pública de productos en el servicio de inventario)** cerrada y aprobada (tras revisión
  independiente): método público y aditivo `InventoryService.list_products() -> list[ProductRecord]`
  (`app/services/inventory.py:213`) que delega en `self._products.list_all()` —incluye productos activos e
  inactivos, sin filtros ni paginación, y hereda el orden de registro del repositorio— sin exponer la lectura
  interna; docstring del módulo ampliado. **No** se modificaron `app/services/ports.py`, las firmas
  existentes, el dominio ni las herramientas (siguen **7**). El requisito **C5** (stock cerrado) se difiere a
  **T044**. Archivos: `app/services/inventory.py` (modificado),
  `tests/integration/test_inventory_service.py` (nuevo).
  - Pruebas **reportadas** por el implementer (pytest **ejecutado** en esa sesión; el reviewer revisó en solo
    lectura y **no** ejecutó pytest ni Git): 38 en `test_inventory_service.py` + `test_inventory_tools.py` y
    1033 en la suite completa. **Revisión independiente: APROBADO**, sin bloqueantes ni hallazgos
    importantes; observaciones opcionales O1–O3 y O5 quedan como mejoras no bloqueantes (sin abrir ciclo).
  - A continuación se abordó **T041** (ver la viñeta siguiente).
- **T041 (Navegación lateral y enrutado de secciones)** cerrada y aprobada (tras revisión independiente):
  navegación lateral de **una sola página** con las siete secciones de RF-09 y «Asistente» por defecto,
  renderizando **solo** la sección elegida; `SECTION_KEY`, `SECTION_LABELS` y el registro de enrutado en
  `app/ui/main.py` (`:146-164`, `:721-775`). Nuevos `app/ui/sections/__init__.py` (vacío) y
  `app/ui/sections/proposals.py` (vista de «Propuestas pendientes», de **solo lectura**: encabezado, estado
  vacío explícito y acción/estado por propuesta; **no** confirma, edita ni descarta). Las cinco secciones no
  implementadas (inicio, productos, operaciones, movimientos, historial de precios) muestran avisos explícitos
  que **no** leen servicios. Se eliminó el `return` del `except LLMConfigurationError` de `main()`: sin modelo
  la aplicación **no** se abstiene (las secciones de consulta siguen accesibles y el asistente informa su
  motivo). Guard estructural de `.confirm(` ampliado a todo `app/ui/**` (**1** aparición, `app/ui/main.py:480`).
  Se conservan el único punto de confirmación, el guard `PROCESSING_KEY`, la sesión compartida (D-G) y el
  estado de las propuestas. Archivos: `app/ui/main.py`, `app/ui/sections/__init__.py` (nuevo),
  `app/ui/sections/proposals.py` (nuevo), `tests/ui/test_main.py`.
  - Pruebas **reportadas** por el implementer (pytest **ejecutado** en esa sesión; el reviewer revisó en solo
    lectura y **no** ejecutó pytest ni Git): 47 en `tests/ui/test_main.py`, 610 en `tests/ui` +
    `tests/integration` y 1046 en la suite completa. **Revisión independiente: APROBADO** (7/7 criterios, sin
    bloqueantes ni hallazgos importantes; observaciones m1–m3 y mejoras opcionales O1–O8 no bloqueantes, sin
    ciclo de corrección).
  - A continuación se abordó **T042** (ver la viñeta siguiente).
- **T042 (Formateo de valores y de fechas en la interfaz)** cerrada y aprobada (tras revisión independiente):
  nuevo módulo **puro de presentación** `app/ui/formatting.py` con `format_quantity(int | None)`,
  `format_money(Decimal | None)` y `format_instant(datetime | None)`: cantidades enteras sin separador ni
  decimales (negativos con signo); importes con **dos decimales fijos** y redondeo explícito
  `ROUND_HALF_UP` —sin mutar el `Decimal` ni usar `float`—; fechas en `YYYY-MM-DD HH:MM:SS ±HH:MM`
  convertidas con `local_timezone()` a `America/Argentina/Buenos_Aires` (sin desplazamiento fijo: el
  histórico se deriva del instante); `no informado` para `None` con el cero real conservado; `ValueError`
  para instantes sin zona horaria. Reutiliza `app.infrastructure.sqlite.instants` para no duplicar el
  identificador IANA. **No** se modificó `main.py` ni ningún archivo existente: la integración del formato
  en las secciones (y la unificación con `_format_price`/`isoformat()` de `main.py`) queda para T050/T055.
  Archivos: `app/ui/formatting.py` (nuevo), `tests/ui/test_formatting.py` (nuevo).
  - Pruebas **reportadas** por el implementer (pytest **ejecutado** en esa sesión; el reviewer revisó en solo
    lectura y **no** ejecutó pytest ni Git): 26 en `tests/ui/test_formatting.py`, 73 en `tests/ui` y 1072 en
    la suite completa. **Revisión independiente: APROBADO** (sin bloqueantes ni importantes; observaciones
    m1–m5 y mejoras opcionales O1–O4 no bloqueantes, sin ciclo de corrección).
  - **Deuda pendiente registrada (no implementada):** `Decimal("-0")` → `"-0.00"`; robustez ante valores
    `Decimal` extremos en `quantize`; validación de tipo en `format_quantity`; dependencia de `tzdata` (no
    declarado en `requirements.txt`); huecos de los guards estructurales (imports relativos/alias,
    `.today()`/`time.time()`, literales de offset); acoplamiento de `formatting.py` con
    `app.infrastructure.sqlite.instants`; y diferencias transitorias de formato en `main.py`
    (`_format_price` con redondeo del contexto y fechas en `isoformat()` UTC).
  - A continuación se abordó **T043** (ver la viñeta siguiente).
- **T043 (Sección Inicio: alertas y conteos)** cerrada y aprobada (tras revisión independiente):
  nueva vista `app/ui/sections/home.py`, de **solo presentación**: `main.py` lee los servicios y le pasa los
  datos ya consultados (`home_section(session)`, lecturas perezosas solo al renderizar Inicio); la vista no
  importa servicios, dominio, infraestructura ni `app.ui.main`, no escribe y no usa `st.session_state`.
  **D-1/D-7:** dos listas independientes —stock bajo y reposición— con nombre, SKU, total equivalente y
  faltante. **D-2:** cada lista vacía muestra su propio mensaje, también en el caso mixto. **D-3:** se
  preserva el orden del servicio, sin reordenar ni deduplicar. **D-4:** una única llamada a
  `InventoryService.list_products()` por render; de ella derivan los conteos de activos e inactivos.
  **D-5:** catálogo vacío con mensaje explícito y sin conteos. **D-6:** no se usó `app/ui/formatting.py`.
  Inactivos excluidos de las alertas por el dominio pero contabilizados; activo con mínimo 0 y total 0 →
  stock bajo con faltante `0` y sin reposición. Intactos `SECTION_LABELS`, «Asistente» como sección
  predeterminada y el único punto de confirmación. Archivos: `app/ui/sections/home.py` (nuevo),
  `app/ui/main.py`, `tests/ui/test_main.py`.
  - Pruebas **reportadas** por el implementer (pytest **ejecutado** en esa sesión; el reviewer revisó en solo
    lectura y **no** ejecutó pytest ni Git): 11 específicas de Inicio, 57 en `tests/ui/test_main.py`, 83 en
    `tests/ui` y 1082 en la suite completa. **Revisión independiente: APROBADO** (sin bloqueantes ni
    importantes; observaciones M1–M8 no bloqueantes, sin ciclo de corrección).
  - **Deuda pendiente registrada (no implementada):** M1 la prueba de orden no discrimina por lista; M2 la
    prueba de conteos no distingue su origen de las longitudes de alertas; M3 la aserción `"0/0"` es
    tautológica; M4 guards sin cubrir imports relativos, alias ni llamadas por atributo; M5 redundancia UX
    en catálogo vacío; M6 lecturas sin instantánea única; M7 aserciones de orden antes de comprobar la
    excepción; M8 estado de carga no cubierto (corresponde a tareas posteriores). Persiste además la
    discrepancia de anclas históricas del criterio de T043 (`:169`, `:349`), no corregida en este cierre.
  - A continuación se abordó **T044** (ver la viñeta siguiente).
- **T044 (Sección Productos: listado, búsqueda y filtro)** cerrada y aprobada (tras revisión independiente):
  nueva vista `app/ui/sections/products.py`, de **solo presentación**: `main.py` lee los servicios y le pasa los
  datos, y la vista devuelve los valores de los controles. Muestra los **siete campos** de C5 —SKU, nombre,
  unidad base, stock mínimo, unidades sueltas, stock cerrado y estado— en el orden del servicio y sin truncar.
  **A1:** «Stock cerrado» es la **suma de `closed_stock` de las presentaciones**, potencialmente con factores
  distintos; no es una cantidad homogénea en unidades base. **B1:** un campo de texto y un selector
  «Nombre / SKU» (Nombre por defecto); búsqueda exacta y sin distinguir mayúsculas según el servicio, con
  advertencia; entrada vacía o solo con espacios no busca. **C1:** filtro «Todos / Activos / Inactivos»
  (Todos por defecto), aplicado tras la búsqueda y antes de leer presentaciones, sin alterar las alertas
  globales (C7). **D1:** aviso con el número real y sin paginación (C8). **E1/G1:** claves estables y valores
  de retorno, sin `st.session_state` en la vista. **F1:** texto preformateado que no interpreta Markdown.
  Mensajes diferenciados para catálogo vacío, búsqueda sin resultados y filtro sin resultados. Intactos
  `SECTION_LABELS`, «Asistente» como sección predeterminada, la navegación, Inicio y el único punto de
  confirmación. Archivos: `app/ui/sections/products.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
  - Pruebas **reportadas** por el implementer (pytest **ejecutado** en esa sesión; el reviewer revisó en solo
    lectura y **no** ejecutó pytest ni Git): **16 pruebas nuevas** de Productos; 72 en `tests/ui/test_main.py`,
    98 en `tests/ui` y 1097 en la suite completa. **Revisión independiente: APROBADO** (sin bloqueantes ni
    importantes; M1–M7 y O1–O6 no bloqueantes, sin ciclo de corrección).
  - **Deuda pendiente registrada (no implementada):** M1 el título aparece después de los controles; M2 con
    catálogo vacío y búsqueda aplicada se muestra el mensaje de búsqueda; M3 `_matches_status` cae a «Todos»
    ante un estado desconocido; M4 `render_products` no muestra mensaje si recibe lista vacía y
    `empty_message=None`; M5 el docstring de `PENDING_SECTIONS` aún cita T044; M6 `ACTIVE_STATUS_FILTER`
    importado sin uso (sin prueba del filtro «Activos»); M7 el reporte inicial decía 17 pruebas y son 16.
    **O1** recorte duplicado del texto; **O2** aserción laxa `>= 1`; **O3** guards sin cubrir imports
    relativos/atributos; **O4** faltan pruebas de SKU inexistente, catálogo vacío positivo, filtro «Activos»
    e interacción filtro→alertas; **O5** desglose de stock cerrado para T045; **O6** `COLLATE NOCASE` solo
    cubre ASCII. Además, el N+1 de lecturas de presentaciones (~2N por render) y el alcance no certificado
    por Git.
  - A continuación se abordó **T045** (ver la viñeta siguiente).
- **T045 (Detalle de producto)** cerrada y aprobada (tras **segunda revisión independiente**):
  nueva vista `app/ui/sections/product_detail.py`, de **solo presentación**, integrada al final de Productos y
  con la lista visible de T044. `main.py` lee `get_product_stock`, `get_movement_history` y
  `get_price_history_by_product`; la vista muestra SKU, nombre, unidad base, stock mínimo, estado, unidades
  sueltas y total equivalente, las presentaciones con nombre, factor, stock cerrado y marca de base, los
  movimientos (tipo traducido, cantidad, motivo, fecha local, presentación, costo/precio solo si existen) y el
  historial de precios (anterior/nuevo, fecha, evento) en orden cronológico. Usa `app/ui/formatting.py`,
  conserva el cero real, omite ausentes, distingue vacío de `RecordNotFoundError` y permite consultar
  inactivos. Sin escrituras, sin `st.session_state` y sin `.confirm(`. **Corrección I1/I2:** con lista visible
  vacía (catálogo/búsqueda/filtro) no se renderiza selector ni detalle y prevalece el mensaje de T044; se
  añadieron cinco pruebas de integración (vacíos, exclusión del seleccionado y transición entre listas).
  Archivos: `app/ui/sections/product_detail.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
  - Pruebas **reportadas** por el implementer (pytest **ejecutado** en esa sesión; el reviewer revisó en solo
    lectura y **no** ejecutó pytest ni Git): 5 nuevas de corrección; 97 en `tests/ui/test_main.py`, 123 en
    `tests/ui` y 1122 en la suite completa. **Segunda revisión independiente: APROBADO** (sin bloqueantes ni
    importantes; sin otro ciclo de corrección).
  - **Deuda pendiente registrada (no implementada):** M1 gramática de avisos con `count == 1`; M2
    `MovementView.kind: str` frente a `MovementKind`; M3 acoplamiento vista→vista con `products.py`; M4
    selector resuelto por etiqueta (`options.index`); M5 solo se captura `RecordNotFoundError`; M6 aserciones
    redundantes (`.exists()`, `value in options`); M7 índices de widgets frágiles en pruebas; M8 Streamlit sin
    versión fijada; M9 estado de carga pendiente (T055). El alcance no está certificado por Git porque
    `app/ui/` y `tests/ui/` siguen sin rastrear; sin commits, push ni cambio de rama.
  - A continuación se abordó **T046** (ver la viñeta siguiente).
- **T046 (Sección Movimientos: filtros y período local)** cerrada y aprobada (tras revisión independiente):
  nueva vista `app/ui/sections/movements.py`, de **solo presentación**, compuesta desde `main.py` con lecturas
  perezosas. **D1:** producto obligatorio (centinela, sin «Todos» ni lectura global). **D3:** tipo «Todos los
  tipos» y sin período inicial; no se consulta hasta elegir producto. **D2:** «Desde» y «Hasta (incluido)»; el
  fin técnico es la medianoche local siguiente, exclusiva; los límites se envían como `datetime` naive locales
  y la persistencia los interpreta en `America/Argentina/Buenos_Aires` (`[inicio, fin)`), sin doble conversión.
  **D4:** período invertido validado sin llamada y errores de lectura explícitos, nunca vacío. **D5:** reutiliza
  etiquetas, descripción y avisos de T045. **D6:** orden del servicio, estados diferenciados (catálogo vacío,
  sin selección, sin movimientos, filtros sin resultados, período inválido, error) y aviso C8 con N real sin
  truncar. **D7:** inactivos consultables y marcados. **D8:** sin escrituras ni creación de compensaciones; las
  históricas se consultan. Sin `st.session_state`, sin `.confirm(`, navegación y confirmación intactas.
  Archivos: `app/ui/sections/movements.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
  - Pruebas **reportadas** por el implementer (pytest **ejecutado** en esa sesión; ni el reviewer ni el
    coordinador ejecutaron pytest ni Git): **25 casos netos**; 122 en `tests/ui/test_main.py`, 148 en
    `tests/ui` y 1147 en la suite completa. **Revisión independiente: APROBADO** (sin bloqueantes ni
    importantes; observaciones menores no bloqueantes, sin ciclo de corrección).
  - **Deuda pendiente registrada (no implementada):** M1 captura amplia `(LookupError, ValueError)` en
    `main.py` (puede ocultar `ValueError` de programación; deja fuera `TypeError`/`sqlite3.Error`); M2
    selección de producto por etiqueta; M3 `st.date_input` sin límites explícitos (±10 años); M4 concordancia
    del aviso C8 con `count == 1` («los 1 movimientos»); M5 docstrings de pruebas desactualizados; M6
    acoplamiento vista→vista y dependencia transitiva con `instants`; M7 cobertura faltante (fallo de lectura
    de presentaciones, C8 tras filtro, deduplicación, `sqlite3.Error`); M8 `access violation` intermitente al
    importar protobuf de Streamlit, no reproducido. El alcance no está certificado por Git porque `app/ui/` y
    `tests/ui/` siguen sin rastrear.
  - **Siguiente paso pendiente: T047** (Historial de precios en la interfaz), aún **sin** autorización de
    implementación.

## Especificación vigente (resumen)
- RF-01 a RF-08. Cantidades de inventario enteras (unidades discretas).
- Presentaciones con factor de conversión y precio de venta independiente.
- Stock descompuesto en unidades sueltas y presentaciones cerradas; total equivalente derivado.
- Costo de ingreso opcional (Decimal), distinto del precio.
- Apertura atómica e inmutable; trazabilidad FIFO de entradas cerradas (compensaciones).

## Arquitectura aprobada (según plan.md)
- Python; LangGraph (orquestación); LangChain (modelo y herramientas); DeepSeek (LLM);
  Streamlit (interfaz); SQLite mediante `sqlite3` (persistencia); Pydantic (validación); pytest (pruebas).

## Reglas permanentes
- Trabajar únicamente en `main`.
- Conventional Commits con mensajes en inglés.
- Ejecutar pruebas antes de dar por cerrada una tarea.
- Respetar `docs/constitution.md`; no avanzar de fase sin aprobación explícita.
- SKU y nombres de presentación: únicos sin distinguir mayúsculas de minúsculas,
  conservando el texto original recortado.
- Zona horaria del proyecto: `America/Argentina/Buenos_Aires`; los períodos se interpretan
  en esa zona y se convierten a UTC; los instantes se almacenan en UTC.

## Entorno local
- `.venv` y `.env` son responsabilidad del usuario; no inspeccionar secretos.
- El agente no crea `.venv` ni `.env` y no instala dependencias.

## Automatización SDD
- Agentes locales disponibles: `coordinator`, `planner`, `implementer` y `reviewer`
  (en `.opencode/agents/`).
- Comando local `sdd-status` (`.opencode/commands/sdd-status.md`).
- No ampliar esta automatización por ahora.

## Documentación pendiente
- Cambios pendientes de revisión/cierre en `specs/001-inventory-core/spec.md`,
  `plan.md` y `tasks.md`.
- No reabrir revisiones cosméticas; corregir solo defectos funcionales importantes.
- Documentado en spec/tasks: prohibición de aperturas de la presentación base
  (factor 1); la valida el dominio y T013 la reutiliza ya implementada.

## Git
- Existen cambios sin commitear (T018 a T026, la ampliación documental de RF-08 en `spec.md`,
  `plan.md`, `tasks.md` y `MEMORY.md`, y la implementación de T028–T037 en `app/config.py`,
  `app/infrastructure/sqlite/{schema,repositories}.py`,
  `app/services/{ports,presentations,proposals,price_history}.py`, `app/schemas/proposals.py`,
  `app/tools/proposal_tools.py`, `app/ui/main.py`,
  `tests/integration/{test_schema,test_repositories,test_presentation_service,test_movement_service,
  test_proposal_service,test_price_history_service}.py`, `tests/concurrency/test_concurrency.py`,
  `tests/ui/test_main.py`, `tests/ai/{test_supervisor,test_proposal_tools,test_inventory_agent}.py`,
  `.gitignore` y `.env.example`); no se afirma que estén commiteados y no se realizó push.
  No asumir que se ejecutaron.

## Decisiones pendientes
- Compensaciones de salidas cerradas (T014/T015): decisión de MVP — quedan excluidas. El rechazo
  reutiliza `NotCompensableError` (existente) y el esquema SQLite no la restringe (no se introduce
  restricción nueva). La relación salida→entrada consumida por FIFO no se persiste y no puede
  revertirse exactamente; reconsiderar si se diseña esa trazabilidad exacta.
- T014 resuelto: `set_compensated_quantity` ya existe en el protocolo y en el repositorio SQLite
  (actualización condicional solo para entradas cerradas) y el servicio rechaza las compensaciones
  de `salida_cerrada`; las notas previas que decían lo contrario quedaron corregidas.
- Mejoras opcionales/deuda técnica de T014 (m1–m7, O1–O3; sin abrir corrección): mensajes del
  validador compartido de contadores; docstrings «(T012)» del servicio; cobertura del tipo
  `COMPENSATION` en el rechazo del repositorio; límite por stock cerrado vigente en una prueba;
  docstring «T009» de `test_repositories.py`; helper con SQL directo en `test_fifo_counters.py`;
  redundancia y aserción débil en la prueba de I1.
- Mejoras opcionales/deuda técnica de T015 (sin abrir corrección): líneas de 80-82 columnas en
  los tres archivos nuevos; dependencia `app/schemas/proposals.py` → `app.services.ports` para el
  tipo de resultado; restricciones numéricas duplicadas entre esquema y dominio (defensivas); y
  tres casos de cobertura pendientes (`float` en `sale_price` de una presentación inicial,
  rechazo por diccionario con `action` conocida distinta y compensación histórica sobre producto
  desactivado a través de `ProposalService`).
- Deuda técnica/menores de T016 (sin abrir corrección): encabezado de `app/services/movements.py`
  sin T016; docstrings de `app/services/proposals.py` parcialmente repetidos; y
  `tests/integration/test_schema.py` compara el SQL del índice sin normalizar espacios.
  Limitaciones M6/M7: ventanas de carrera en `change_sale_price`/`deactivate` y dependencia de la
  serialización para el límite de compensación de movimientos sueltos (ver bloque de T016).
- Deuda técnica de T019 (M1–M7, sin abrir corrección): (M1) etiquetas `D2`–`D5` citadas en
  docstrings sin definir y en colisión con las `D1–D5` de T016; (M2) línea de 80 columnas en
  `tests/ai/test_inventory_tools.py:10`; (M3) `NoInput` (`extra="forbid"`) no valida en runtime
  porque LangChain omite la validación de esquemas sin campos (inocuo); (M4) doble cómputo en
  `_AlertItem.as_alert()` sin duplicar reglas; (M5) `ProductSummary` omite
  `minimum_stock`/`loose_units` (no exigido); (M6) huecos de cobertura opcionales (producto sin
  presentaciones, argumentos inesperados, inactivo con datos reales); (M7) guarda de imports
  textual (no detectaría una dependencia indirecta de infraestructura).
- Deuda técnica de T020 (M1–M6, sin abrir corrección): (M1) el parámetro `action` no se usa en el
  cuerpo de las herramientas de creación (correcto y documentado; un linter marcaría `ARG001`);
  (M2) el caso `propose_closed_entry` omite `reason` en los campos comparados de una prueba;
  (M3) la prueba de «otra acción» discrimina el esquema `Literal`, no el cuerpo de la herramienta;
  (M4) guardas textuales de imports (`inspect.getsource`), que no detectarían una dependencia
  indirecta; (M5) las pruebas dependen de internals de terceros sin versiones fijadas en
  `requirements.txt`; (M6) registrar la superficie de T020 y la divergencia de `plan.md:148`
  (`app/domain/proposals.py` inexistente).
- Hallazgos menores m1–m5 de T021 (deuda técnica menor / opcional, sin abrir corrección): (m1) la
  prueba del enlazado de herramientas accede a internals de LangChain/LangGraph
  (`agent.nodes["tools"].bound.tools_by_name`), frágil ante cambios de versión; (m2) la aserción de
  «sin confirmación» se hace sobre el conjunto inyectado, no sobre el nodo de herramientas; (m3)
  guarda de imports textual (`inspect.getsource`), que no detectaría dependencias indirectas; (m4)
  aserciones por subcadena sobre el `ToolMessage` serializado; (m5) `recursion_limit: 10` defensivo
  innecesario en las pruebas de aborto.
- Hallazgos menores de T022 (deuda técnica menor / opcional, sin abrir corrección): (1) la prueba
  de la envoltura no asevera `return_direct` ni `response_format` (el código sí los conserva);
  (2) `_last_response_text` usa `str(...)` para contenido no textual (aceptable en MVP); (3) el
  docstring de módulo de `tests/ai/test_state.py` sigue sin mencionar `response` (anterior y fuera
  de alcance).
- Hallazgos menores de T023 (deuda técnica menor / opcional, sin abrir corrección): (1) la prueba
  de integración emite una única llamada de herramienta (endurecer con ≥2 en paralelo); (2) el
  mensaje de error inyectado es idéntico al real de `get_llm()` sin API key; (3) la guarda de
  no-confirmación es textual; (4) `describe_proposal` imprime campos `None`; (5)
  `sqlite3.threadsafety == 3` se verifica en pruebas pero no se usa como guarda en `connect()`.
- Hallazgos menores de T024 (deuda técnica menor / opcional, sin abrir corrección): (1) no se
  ejercita `NotCompensableError` desde la UI (hueco de cobertura; la implementación sí lo cubre vía
  `(LookupError, ValueError)`); (2) `describe_proposal` imprime campos `None` (deuda D5, no
  corregida); (3) guarda textual frágil heredada de T023.
- Hallazgos menores de T025 (deuda técnica menor / opcional, sin abrir corrección): (1) aserciones
  por subcadena sobre la salida serializada del modelo (frágil ante cambios de formato; patrón ya
  anotado en T021/T023); (2) `total_changes()` depende del orden actual «validación antes de
  escritura» (correcto hoy; frágil ante un reordenamiento futuro).
- Brecha de trazabilidad de T026 (pendiente, no resuelta): RF-01 `spec.md:49` (consulta por
  SKU/nombre de un producto desactivado) no tiene un caso de prueba identificable en la matriz; no
  se resuelve sin autorización. Limitaciones conocidas (no resueltas): RF-06 como evidencia
  estructural sin evidencia conductual; integración real con DeepSeek no validada.
- Dependencia técnica de T021 (I1) — **RESUELTA en T023**: `connect()` ahora acepta
  `check_same_thread=False` (D1), lo que habilita a las herramientas del `ToolNode` a usar la
  conexión abierta por la interfaz; la prueba de integración lo ejercita. Se aclara que
  `check_same_thread=False` **no** garantiza por sí solo la seguridad concurrente (la serialización
  la aportan SQLite y `BEGIN IMMEDIATE`, con una conexión por sesión).
- Deuda técnica de T018 (M1–M3 y O1, sin abrir corrección): (M1) líneas de más de 79 columnas en
  `tests/ai/test_state.py`; (M2) prueba redundante que repite aserciones ya cubiertas; (M3) la
  guarda de imports no incluye `sqlite` ni `app.infrastructure`, por lo que no detectaría una
  dependencia hipotética de infraestructura; (O1) capacidad discriminante limitada de las pruebas
  de identidad, porque `TypedDict` es un `dict` en tiempo de ejecución.
- Deuda técnica de T017 (M1–M6, sin abrir corrección): (M1) línea de más de 79 columnas en
  `tests/ai/test_prompts.py`; (M2) la prueba de ausencia de API key/red no discrimina del todo la
  dependencia de configuración; (M3) fragilidad ante cambios en frases exactas de los prompts;
  (M4) guarda anti-reglas-de-negocio incompleta; (M5) la redacción sobre consultas SQL podría
  interpretarse como prohibición de consultas de solo lectura mediante herramientas autorizadas
  (revisar al conectar las herramientas); (M6) doble import en el test para permitir
  `importlib.reload`.
- O-A (T011) resuelto por T016: el invariante de presentación base única (factor 1) quedó
  protegido por el servicio (`PresentationService.add`/`change_factor` dentro de la transacción)
  y por el índice único parcial `idx_presentations_single_base`.
- Identificador del modelo DeepSeek (antes de Fase 3).
- Mejoras opcionales de T013 (M1–M6, sin abrir corrección): unificar
  `_required_presentation` con `_closed_presentation`; orden de validación de cantidad/motivo
  (hoist al estilo T012 o documentar y probar); acotar una aserción no discriminante del
  rollback; actualizar los docstrings de la clase/`__init__`; reducir duplicación de pruebas;
  cubrir `closed_stock = 0` sin entradas cerradas.
