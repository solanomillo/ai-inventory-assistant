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
- Próxima tarea: T004, reglas de dominio de productos y presentaciones. Pendiente de autorización.

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

## Entorno local
- `.venv` y `.env` son responsabilidad del usuario; no inspeccionar secretos.
- El agente no crea `.venv` ni `.env` y no instala dependencias.

## Automatización SDD
- Auditoría y diseño completados.
- Comando local `sdd-status` creado (`.opencode/commands/sdd-status.md`) y probado.
- Próximo paso de automatización pendiente de decisión; aún no existen otros comandos, agentes ni skills.

## Decisiones pendientes
- Identificador del modelo DeepSeek (antes de Fase 3).
- Zona horaria concreta de Argentina (antes de consultas por período).
