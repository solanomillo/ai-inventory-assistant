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
- Próxima tarea técnica: T018 (Estado del grafo), pendiente de autorización.

## Especificación vigente (resumen)
- RF-01 a RF-07. Cantidades de inventario enteras (unidades discretas).
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
- Existen commits/push pendientes de confirmar (T004 a T017, corrección de T006, correcciones I3, I-1 e I2 y ajustes documentales).
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
