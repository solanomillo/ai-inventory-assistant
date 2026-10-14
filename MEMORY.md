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
    transacción; sin `BEGIN IMMEDIATE` (corresponde a T016).
  - Pruebas reportadas por implementer: 30 específicas, 91 de regresión de T012 y 673 en la
    suite completa; el código de salida de pytest no fue observable y el reviewer no ejecutó
    las pruebas. `git diff --check` sin errores.
  - Revisión independiente: APROBADO, sin bloqueantes ni hallazgos importantes; los hallazgos
    M1–M6 quedan como mejoras opcionales (sin abrir nueva corrección).
- T014 y T016 permanecen pendientes (no implementadas).
- Próxima tarea técnica: T014, pendiente de autorización.

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
- Existen commits/push pendientes de confirmar (T004 a T013, correcciones I3, I-1 e I2 y ajustes documentales).
  No asumir que se ejecutaron.

## Decisiones pendientes
- T014/T015 (riesgo registrado, sin diseñar): las compensaciones de salidas cerradas pueden
  afectar la relación entre `closed_stock` y las cantidades todavía compensables por FIFO.
- T014: falta una operación segura/condicional análoga a `set_consumed_quantity` para
  actualizar `compensated_quantity` (ver I2 resuelto).
- Antes de T016 (a evaluar): el invariante de presentación base única (factor 1) no está
  protegido frente a operaciones concurrentes (ni el esquema ni una transacción lo garantizan
  aún); hallazgo O-A de T011.
- Identificador del modelo DeepSeek (antes de Fase 3).
- Mejoras opcionales de T013 (M1–M6, sin abrir corrección): unificar
  `_required_presentation` con `_closed_presentation`; orden de validación de cantidad/motivo
  (hoist al estilo T012 o documentar y probar); acotar una aserción no discriminante del
  rollback; actualizar los docstrings de la clase/`__init__`; reducir duplicación de pruebas;
  cubrir `closed_stock = 0` sin entradas cerradas.
