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
- Próxima tarea técnica: T005, todavía sin autorización para comenzar.

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

## Git
- Existen commits/push pendientes de confirmar (T004 y ajustes documentales).
  No asumir que se ejecutaron.

## Decisiones pendientes
- Identificador del modelo DeepSeek (antes de Fase 3).
- Zona horaria concreta de Argentina (antes de consultas por período).
