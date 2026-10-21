---
description: Consulta el estado del flujo SDD sin modificar archivos
---

Consulta el estado actual del proyecto SDD. Si `$ARGUMENTS` contiene un ID de tarea, consulta esa tarea; si no, usa la tarea en curso y la siguiente.

Lee solo:
- `AGENTS.md`;
- el bloque `## Estado actual` de `MEMORY.md`;
- en `tasks.md`, los bloques de la tarea consultada y la siguiente;
- en `plan.md`, la fase y las decisiones citadas o necesarias para esas tareas;
- en `spec.md`, los RF o criterios citados o necesarios.

No cargues `MEMORY.md`, `spec.md`, `plan.md` ni `tasks.md` completos. Usa `grep` para localizar y leer el entorno mínimo.

Informa brevemente:

- **Fase actual:** estado del flujo SDD y tareas completadas.
- **Siguiente paso:** próxima tarea pendiente y sus dependencias.
- **Decisiones pendientes:** decisiones que los documentos indiquen que todavía requieren resolución.
- **Bloqueos:** impedimentos explícitos, diferenciados de las tareas futuras que simplemente están pendientes.

Usa los documentos como fuentes de verdad y señala las contradicciones documentales que encuentres. No tienes shell: no ejecutes Git ni comandos de terminal. No verifiques el estado de los commits; informa siempre el estado de Git como no verificado. No inventes estados.

No cambies de agente, no modifiques archivos, no ejecutes tareas de implementación, no prepares staging, no hagas commits ni push. No ejecutes comandos shell ocultos.
