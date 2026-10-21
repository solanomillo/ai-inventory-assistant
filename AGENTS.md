# Guía del repositorio

## Estado del proyecto

- Proyecto: AI Inventory Assistant.
- El repositorio contiene la licencia MIT, la constitución del proyecto y la especificación funcional del núcleo de inventario.
- La especificación funcional aprobada es `specs/001-inventory-core/spec.md`.
- El stack técnico y la arquitectura están definidos en `specs/001-inventory-core/plan.md`; respeta esas decisiones. Cualquier cambio requiere revisión y aprobación explícitas.
- Al introducir la primera implementación, documenta las instrucciones de configuración y verificación correspondientes a las herramientas elegidas.

## Memoria del proyecto

- Al comenzar una tarea, lee solo el bloque `## Estado actual` de `MEMORY.md` y el bloque de la tarea objetivo en `tasks.md`. Usa `grep` para localizar detalles históricos; no cargues esos documentos completos de forma rutinaria.
- Consulta `docs/constitution.md` y las secciones de `spec.md` o `plan.md` citadas o necesarias, localizadas por RF, criterio de aceptación, decisión o símbolo.
- Al cerrar una tarea aprobada, actualiza `MEMORY.md` una sola vez con el estado verificado y el commit; no registres supuestos ni historial duplicado.
- Mantén el bloque `## Estado actual` breve; el historial detallado corresponde a `tasks.md` y no debe cargarse por defecto.
- Si una decisión se convierte en una regla permanente, propón incorporarla a `AGENTS.md` o `docs/constitution.md`, según corresponda.
- Nunca guardes claves, tokens, credenciales ni otros secretos en `MEMORY.md`.

## Flujo de trabajo

- Sigue la metodología Spec-Driven Development (SDD).
- No avances a la siguiente fase sin la aprobación explícita del usuario.
- La especificación aprobada define el comportamiento funcional esperado.
- No implementes funcionalidades que no estén contempladas en la especificación aprobada.
- Antes de implementar, verifica que exista un plan técnico aprobado y tareas definidas.
- Trabaja únicamente en la rama `main`, salvo que el usuario autorice un cambio.
- El Coordinator solo edita `MEMORY.md`; los cambios pequeños de código, pruebas o documentación se delegan al rol correspondiente.
- Mantén los cambios simples y evita la sobreingeniería.
- Utiliza commits que sigan Conventional Commits, con mensajes en inglés.


## Protocolo SDD mínimo

- **Fuentes de verdad:** comportamiento → `spec.md` y `docs/constitution.md`; arquitectura → `plan.md`; estado de tareas → `tasks.md` y Git; `MEMORY.md` solo resume el estado actual.
- **Contexto mínimo:** al iniciar, lee `AGENTS.md`, el bloque `## Estado actual` de `MEMORY.md` y el bloque de la tarea objetivo en `tasks.md`. De `spec.md` y `plan.md`, lee las secciones citadas o necesarias, localizadas por RF, criterio de aceptación, decisión o símbolo. No cargues documentos completos de forma rutinaria.
- **Delegación:** cada encargo debe indicar tarea, objetivo, bloque documental, decisiones aprobadas, criterios de aceptación, archivos permitidos, restricciones de archivos y permisos, evidencia esperada y condición de parada. No repitas las reglas generales de este archivo.
- **Evidencia:** el Implementer entrega un bloque compacto con archivos modificados; pruebas ejecutadas y resultado; `git status --short --branch`; `git diff --check`; `git diff --stat`; `git diff --name-status`; riesgos y bloqueos.
- **Revisión:** el Reviewer es solo lectura. No ejecuta pruebas ni Git; verifica la evidencia y juzga requisitos, regresiones, arquitectura y cobertura. `APROBADO` no autoriza commit, cierre ni push.
- **Autorizaciones:** implementar, preparar staging, hacer commit, cerrar documentación y hacer push requieren autorización humana explícita e independiente. Ningún agente puede inferirla.
- **Cierre:** tras el commit verificado y la autorización de cierre, actualiza `tasks.md` y `MEMORY.md` una sola vez con el estado y el hash verificados. No cierres una tarea antes de esa autorización. El número y alcance de los commits de cierre se acuerdan con el usuario.

## Skills de desarrollo

- Para tareas relacionadas con Streamlit, consulta la skill local `developing-with-streamlit` cuando esté disponible y aplica únicamente las recomendaciones pertinentes al alcance autorizado.
- Verifica las APIs contra la versión de Streamlit instalada en el proyecto.
- No instales ni actualices dependencias por utilizar una skill sin autorización explícita.
- No copies al repositorio las referencias de la skill salvo que una tarea lo requiera expresamente.
- La skill es una guía de desarrollo, no una dependencia de ejecución de la aplicación.

## Calidad y seguridad

- Sigue los principios establecidos en `docs/constitution.md`.
- Respeta la separación de responsabilidades y las convenciones de código que se definan para las tecnologías elegidas.
- No inventes comandos de configuración, ejecución o pruebas.
- No expongas ni incluyas secretos en el código, los archivos versionados o los registros.
- Verifica los cambios antes de confirmar un commit.
