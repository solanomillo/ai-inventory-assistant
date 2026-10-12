# Guía del repositorio

## Estado del proyecto

- Proyecto: AI Inventory Assistant.
- El repositorio contiene la licencia MIT, la constitución del proyecto y la especificación funcional del núcleo de inventario.
- La especificación funcional aprobada es `specs/001-inventory-core/spec.md`.
- El stack técnico y la arquitectura están definidos en `specs/001-inventory-core/plan.md`; respeta esas decisiones. Cualquier cambio requiere revisión y aprobación explícitas.
- Al introducir la primera implementación, documenta las instrucciones de configuración y verificación correspondientes a las herramientas elegidas.

## Memoria del proyecto

- Al comenzar una tarea, lee `MEMORY.md` para conocer el estado del proyecto y las decisiones tomadas.
- Consulta `docs/constitution.md` y la especificación funcional correspondiente cuando sean relevantes para la tarea.
- Al terminar una tarea o fase aprobada, actualiza `MEMORY.md` con el estado actual, las decisiones importantes y los próximos pasos.
- Mantén `MEMORY.md` breve, con un objetivo aproximado de 50 líneas.
- Si una decisión se convierte en una regla permanente, propón incorporarla a `AGENTS.md` o `docs/constitution.md`, según corresponda.
- Nunca guardes claves, tokens, credenciales ni otros secretos en `MEMORY.md`.

## Flujo de trabajo

- Sigue la metodología Spec-Driven Development (SDD).
- No avances a la siguiente fase sin la aprobación explícita del usuario.
- La especificación aprobada define el comportamiento funcional esperado.
- No implementes funcionalidades que no estén contempladas en la especificación aprobada.
- Antes de implementar, verifica que exista un plan técnico aprobado y tareas definidas.
- Trabaja únicamente en la rama `main`, salvo que el usuario autorice un cambio.
- Resuelve manualmente los cambios pequeños y evidentes cuando sea más eficiente que delegarlos a un agente.
- Mantén los cambios simples y evita la sobreingeniería.
- Utiliza commits que sigan Conventional Commits, con mensajes en inglés.

## Calidad y seguridad

- Sigue los principios establecidos en `docs/constitution.md`.
- Respeta la separación de responsabilidades y las convenciones de código que se definan para las tecnologías elegidas.
- No inventes comandos de configuración, ejecución o pruebas.
- No expongas ni incluyas secretos en el código, los archivos versionados o los registros.
- Verifica los cambios antes de confirmar un commit.
