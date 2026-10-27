# Memoria del proyecto

## Estado actual

- Proyecto: **AI Inventory Assistant**.
- Rama de trabajo: `main`.
- Metodología: **Spec-Driven Development (SDD)**.
- Documentos aprobados: `docs/constitution.md`, `spec.md` y `plan.md`; el historial de tareas y la trazabilidad viven en `tasks.md`.
- Tareas: `T041–T048` cerradas; `T049–T051` implementadas y revisadas; `T052–T064` cerradas.
- **T063** (Fase 11 — contexto conversacional): cerrada y aprobada.
- **T064** (Fase 12 — tema visual azul profesional): cerrada y aprobada; sustituye la convención visual de T057.
- La validación visual de T064 fue confirmada por el usuario; no se registró una revisión individual por pantalla.
- Última evidencia consolidada de pruebas: **1344 passed** reportada el **2026-10-26**.

## Decisiones permanentes

### Arquitectura y stack

- Python, Streamlit, LangGraph, LangChain, DeepSeek, SQLite mediante `sqlite3`, Pydantic y pytest.
- Flujo: Streamlit → grafo LangGraph → Supervisor → Inventory Agent → herramientas → servicios → dominio/repositorios → SQLite.
- El Supervisor es un router determinista; el Inventory Agent interpreta solicitudes y usa herramientas.
- Las reglas de negocio viven fuera del LLM, en el dominio y los servicios de aplicación.
- La interfaz usa los servicios; no accede directamente a repositorios, dominio ni SQLite.

### IA y proveedor

- El proveedor implementado es **DeepSeek** mediante `ChatDeepSeek` y `langchain-deepseek`.
- No hay integración con Google Gemini.
- La API key y el identificador del modelo se configuran por entorno (`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`).
- Sin configuración del modelo, el asistente no se habilita; las secciones de consulta siguen funcionando.

### Operaciones y confirmación

- Las lecturas son de solo lectura.
- Las operaciones se preparan como propuestas tipadas y **no escriben** hasta la confirmación.
- La confirmación es explícita del usuario, ocurre en la interfaz y queda fuera del LLM.
- El agente no confirma ni escribe directamente en SQLite.
- Las propuestas no se persisten; viven en la sesión de la interfaz.
- La protección contra doble envío es local a la sesión; no hay idempotencia durable.
- Editar o descartar una propuesta no cambia su acción ni escribe el inventario.

### Stock, movimientos y compensaciones

- Cantidades y factores son enteros; precios y costos son `Decimal` exactos.
- El stock se compone de unidades sueltas del producto y stock cerrado por presentación.
- El total equivalente es `loose_units + Σ(closed_stock × factor)` y es derivado, no persistido.
- Hay stock bajo cuando el total equivalente es menor o igual que el stock mínimo.
- Las consultas globales de stock bajo y reposición solo incluyen productos activos.
- El faltante de reposición es `max(0, stock mínimo − total equivalente)`; solo se considera reposición cuando el faltante es mayor que cero.
- El stock suelto y el cerrado nunca pueden ser negativos.
- Las salidas validan la disponibilidad vigente.
- Las aperturas son atómicas, inmutables y no compensables.
- Las salidas cerradas no son compensables en el MVP.
- Orígenes compensables: entrada cerrada, entrada suelta y salida suelta.
- Una compensación se vincula al movimiento original, no puede encadenarse y puede ser parcial o total.
- Para entradas cerradas, la cantidad compensable es `cantidad − cantidad_consumida − cantidad_compensada`.
- Las aperturas y salidas cerradas consumen entradas cerradas mediante FIFO.
- Los movimientos confirmados son inmutables.
- La presentación base tiene factor 1 y no admite aperturas ni operaciones cerradas.
- Un producto desactivado admite consultas, historial y compensaciones históricas válidas; rechaza operaciones ordinarias nuevas y modificaciones, incluidas las de nombre o stock, la creación o modificación de presentaciones y las aperturas.

### Precios y fechas

- El precio de venta solo cambia mediante una propuesta confirmada.
- El precio anterior se captura al confirmar, no al preparar la propuesta.
- El cambio de precio y su historial son atómicos.
- El historial de precios es de solo anexado y se consulta en orden cronológico.
- Los instantes se almacenan en UTC.
- Los períodos se interpretan en `America/Argentina/Buenos_Aires` con semántica `[inicio, fin)`.

### Persistencia

- SQLite en archivo, con ruta configurable por `INVENTORY_DB_PATH`.
- Ruta predeterminada: `data/inventory.sqlite3`, independiente del directorio de trabajo.
- El esquema se versiona con `PRAGMA user_version` y migraciones explícitas.
- Versión actual del esquema: `PRAGMA user_version = 1`.
- Las escrituras usan transacciones; `BEGIN IMMEDIATE` protege la validación y la escritura frente a concurrencia.
- `data/` y `.env` son locales y no deben publicarse.

### Interfaz

- Navegación lateral de una sola página con siete secciones.
- Las secciones de consulta funcionan sin modelo; el asistente es opcional.
- Tema nativo oscuro azul definido en `.streamlit/config.toml`.
- Sin CSS personalizado, `unsafe_allow_html` ni `st.html`.
- T064 sustituye la convención visual clara/neutra de T057.

## Reglas de trabajo para agentes

- Seguir `AGENTS.md`, `docs/constitution.md` y el flujo SDD.
- No avanzar de fase ni cerrar tareas sin autorización humana explícita.
- La especificación aprobada es la fuente de verdad del comportamiento.
- El Coordinator solo edita `MEMORY.md`; el resto se delega al rol correspondiente.
- Mantener los cambios pequeños, atómicos y limitados al alcance autorizado.
- Usar Conventional Commits en inglés.
- No inventar comandos, resultados de pruebas, decisiones ni evidencias.
- No exponer secretos, claves, tokens ni datos sensibles.
- El usuario administra `.venv` y `.env`; el agente no los crea ni instala dependencias.
- Consultar el estado Git con `git status` cuando haga falta; no fijar aquí un estado que caduque.

## Pendientes, limitaciones y deuda vigente

- La integración real con DeepSeek no está validada por la suite; las pruebas usan modelos falsos.
- Persisten referencias documentales desalineadas de RF-06 a RF-08; requieren una tarea documental específica.
- RF-01 tiene una brecha de trazabilidad: consulta por SKU/nombre de un producto desactivado sin caso identificable.
- Las propuestas no son persistentes y no existe idempotencia durable para confirmaciones.
- El contexto conversacional de T063 usa una ventana de 12 mensajes totales, no de 12 turnos.
- El MVP cubre un único inventario y una única ubicación; no hay multiusuario, autenticación, compras, ventas ni proveedores formales.
- No hay paginación; los listados muestran todos los registros y avisan cuando exceden el espacio.
- La interfaz no ofrece desactivación de productos ni cambio de factor de una presentación existente.
- Las tablas pueden requerir scroll horizontal en 768 px; el contraste del canvas de `st.dataframe` no se mide de forma fiable.
- `requirements.txt` no fija versiones; para reproducibilidad conviene congelar un entorno validado.
- Las deudas menores por tarea permanecen en `tasks.md`; no se duplican aquí.

## Índice de documentos fuente

- `AGENTS.md`: reglas de trabajo, delegación y protocolo SDD.
- `docs/constitution.md`: principios de simplicidad, separación, pruebas y seguridad.
- `specs/001-inventory-core/spec.md`: comportamiento funcional y criterios de aceptación.
- `specs/001-inventory-core/plan.md`: arquitectura, stack y decisiones técnicas.
- `specs/001-inventory-core/tasks.md`: historial, cierres, evidencia, deuda y trazabilidad.
- `docs/ui-guidelines.md`: convenciones visuales y validación de interfaz.
- `README.md`: presentación, instalación, ejecución y limitaciones para usuarios.
