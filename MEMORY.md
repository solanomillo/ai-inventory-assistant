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
- Próxima tarea técnica: T011, pendiente de autorización.

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
  (factor 1); el dominio la valida y T013 la reutiliza.

## Git
- Existen commits/push pendientes de confirmar (T004 a T010 y ajustes documentales).
  No asumir que se ejecutaron.

## Decisiones pendientes
- Antes de T012: normalizar el motivo (recorte de espacios/tabs/saltos) antes de persistir,
  porque el `CHECK` de SQLite solo elimina espacios (hallazgo I1 de T009).
- Antes de T013/T014: definir la actualización controlada de los contadores FIFO sin editar
  el movimiento confirmado (hallazgo I2 de T009).
- Antes de T012/T014: resolver la representación canónica de Decimal y la compatibilidad
  entre el dominio y los CHECK de SQLite (hallazgo importante de la revisión de T008).
- Identificador del modelo DeepSeek (antes de Fase 3).
