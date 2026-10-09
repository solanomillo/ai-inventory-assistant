# Memoria del proyecto

## Estado actual
- Proyecto: AI Inventory Assistant.
- Rama de trabajo: main.
- Metodología: Spec-Driven Development (SDD).
- La guía AGENTS.md y la constitución del proyecto están preparadas.
- La especificación funcional aprobada del núcleo de inventario está en
  `specs/001-inventory-core/spec.md` y es la referencia oficial del MVP.
- No hay implementación funcional todavía.

## Decisiones importantes
- Mantener el MVP simple y evitar sobreingeniería.
- La especificación aprobada es la referencia del comportamiento funcional.
- No implementar requisitos que no estén aprobados.
- Trabajar únicamente en main.
- Usar Conventional Commits con mensajes en inglés.
- Aprobar cada fase antes de avanzar a la siguiente.
- Finalizó la revisión de los nueve hallazgos de clarificación.
- El usuario aprobó explícitamente la especificación funcional actualizada.
- Las compensaciones son opuestas al movimiento original no compensatorio,
  pueden ser parciales hasta su cantidad total y no generan stock negativo.
- Los períodos usan hora local de Argentina, inicio inclusivo, final exclusivo
  y filtros combinados mediante AND; los instantes se almacenan en UTC.
- Las consultas globales de stock bajo y reposición incluyen solo productos
  activos; la reposición requiere faltante mayor que cero.
- SKU y nombre se recortan, son obligatorios y no pueden quedar vacíos.
- Las propuestas no confirmadas son editables; al confirmar se revalidan las
  reglas, se evita persistencia parcial y se protege el stock ante concurrencia.

## Próximo paso
- Iniciar la planificación técnica de la especificación aprobada.
