# Tareas: núcleo de inventario
## Resumen del alcance
Este documento descompone el plan técnico aprobado (`plan.md`) en tareas pequeñas, ordenadas por dependencias y verificables, para el MVP de inventario definido por `spec.md`.
- Los identificadores (`T001`–`T025`) son únicos y estables.
- Cada tarea pertenece a una fase, declara dependencias, archivos previstos, pruebas y criterios de aceptación verificables.
- Se priorizan las reglas deterministas del dominio y la persistencia antes de integrar el LLM.
- Las decisiones pendientes que bloquean una tarea se indican explícitamente y no se resuelven aquí.
- **Entorno local:** el usuario crea y activa `.venv`, crea el archivo local `.env` e instala las dependencias manualmente. El agente no debe crear ni modificar el entorno virtual, crear `.env` ni ejecutar instalaciones de paquetes. El archivo `.env.example` sí forma parte del repositorio y debe contener solo nombres de variables y valores de ejemplo no sensibles.

## Fase 0 — Esqueleto, configuración y scaffolding del proveedor
### T001 — Esqueleto del proyecto
- **Fase:** 0.
- **Objetivo:** preparar la base del proyecto sin sobrescribir trabajo previo.
- **Archivos previstos:** `requirements.txt`, `.env.example`, directorios `app/` y `tests/`; revisión de `.gitignore` existente.
- **Dependencias:** ninguna.
- **Pruebas:** verificación manual de archivos.
- **Criterios de aceptación:** `requirements.txt` y `.env.example` existen; `.env.example` no contiene credenciales; `.gitignore` conserva sus reglas actuales (solo se agrega `.streamlit/` si hace falta); el agente no crea `.venv` ni `.env` local y no instala dependencias; el usuario se encarga manualmente de esas acciones; no se modifican `AGENTS.md`, `MEMORY.md`, `LICENSE`, `spec.md` ni `plan.md`.

### T002 — Configuración e inicialización del LLM
- **Fase:** 0.
- **Objetivo:** centralizar configuración e inicialización del proveedor.
- **Archivos previstos:** `app/config.py`, `app/services/llm.py`.
- **Dependencias:** T001.
- **Pruebas:** `tests/unit/test_llm.py` (con `monkeypatch`, sin DeepSeek real, sin llamadas de red ni credenciales reales).
- **Criterios de aceptación:** `get_llm()` lanza error sin `DEEPSEEK_API_KEY`; con valores inyectados devuelve `ChatDeepSeek` con `temperature=0`; la prueba no realiza llamadas de red (el adaptador se verifica por construcción del objeto, no por invocación).
- **Pendiente:** el identificador del modelo DeepSeek queda documentado; no se fija aquí.

## Fase 1 — Dominio puro + persistencia
### T003 — Representación de cantidades y precio
- **Fase:** 1.
- **Objetivo:** definir la representación exacta de cantidades, stock y precio.
- **Archivos previstos:** conversores `to_cents`/`from_cents` y representación `Decimal` del precio.
- **Dependencias:** ninguna.
- **Pruebas:** `tests/unit/test_money_quantities.py`.
- **Criterios de aceptación:** `12.34` ↔ `1234` exacto e idempotente; cantidades con más de dos decimales se rechazan; precio se valida como `Decimal` mayor que cero sin imponer precisión nueva.

### T004 — Reglas de dominio de producto
- **Fase:** 1.
- **Objetivo:** implementar y validar las reglas de RF-01.
- **Archivos previstos:** `app/domain/products.py`.
- **Dependencias:** T003.
- **Pruebas:** `tests/unit/test_product_rules.py`.
- **Criterios de aceptación:** SKU único e inmutable, recortado; nombre obligatorio, recortado, no vacío; stock mínimo cero o positivo con hasta dos decimales; precio mayor que cero.

### T005 — Reglas de dominio de stock
- **Fase:** 1.
- **Objetivo:** implementar las reglas de control de existencias de RF-02.
- **Archivos previstos:** `app/domain/stock.py`.
- **Dependencias:** T003.
- **Pruebas:** `tests/unit/test_stock_rules.py`.
- **Criterios de aceptación:** una salida no deja stock negativo; stock inicial cero no genera movimiento; stock inicial positivo genera una entrada identificada como stock inicial.

### T006 — Reglas de movimientos y compensaciones
- **Fase:** 1.
- **Objetivo:** implementar las reglas de compensación de RF-03.
- **Archivos previstos:** `app/domain/movements.py`.
- **Dependencias:** T005.
- **Pruebas:** `tests/unit/test_movement_rules.py`.
- **Criterios de aceptación:** compensación de tipo opuesto; vínculo solo a original no compensatorio; cada nueva compensación debe ser menor o igual a la cantidad pendiente de compensar; la suma acumulada de los valores absolutos de las compensaciones no puede superar la cantidad absoluta del movimiento original; rechazo de compensación excedente, de tipo igual o encadenada.

### T007 — Reglas de stock bajo y reposición
- **Fase:** 1.
- **Objetivo:** implementar las reglas de RF-04.
- **Archivos previstos:** `app/domain/stock.py` (o módulo equivalente).
- **Dependencias:** T005.
- **Pruebas:** `tests/unit/test_stock_alerts.py`.
- **Criterios de aceptación:** stock actual ≤ mínimo es stock bajo; reposición solo para productos activos con faltante mayor que cero; `mínimo 10 / actual 6 → faltante 4`; `actual 12 → 0`; productos desactivados excluidos de consultas globales.

### T008 — Esquema y conexión SQLite
- **Fase:** 1.
- **Objetivo:** crear el esquema persistente y el manejo de conexión.
- **Archivos previstos:** `app/infrastructure/sqlite/schema.py`, `app/infrastructure/sqlite/connection.py`.
- **Dependencias:** T001.
- **Pruebas:** `tests/integration/test_schema.py` (SQLite en memoria).
- **Criterios de aceptación:** el esquema se crea desde una base vacía con tablas de productos, movimientos y propuestas; restricciones `CHECK` de positividad/≥0 según las reglas aprobadas; cada movimiento requiere un motivo no vacío, validado también por la persistencia.

### T009 — Repositorios básicos y contrato
- **Fase:** 1.
- **Objetivo:** implementar persistencia básica sin filtros temporales.
- **Archivos previstos:** `app/services/ports.py` (Protocol de repositorio), `app/infrastructure/sqlite/repositories.py`.
- **Dependencias:** T008.
- **Pruebas:** `tests/integration/test_repositories.py` (unicidad de SKU, rechazo de movimientos sin motivo o con motivo vacío, inmutabilidad de movimientos confirmados, filtros por producto y tipo).
- **Criterios de aceptación:** SKU duplicado rechazado; los movimientos sin motivo o con motivo vacío son rechazados; la persistencia no permite editar ni eliminar movimientos confirmados y rechaza cualquier intento de hacerlo; consultas por producto y por tipo devuelven únicamente los registros que cumplen el filtro.

### T010 — Filtros temporales por período
- **Fase:** 1.
- **Objetivo:** implementar consultas por período con conversión de zona horaria.
- **Archivos previstos:** `app/infrastructure/sqlite/repositories.py` (consulta por período), helpers de fechas.
- **Dependencias:** T009.
- **Pruebas:** `tests/integration/test_period_filters.py`.
- **Criterios de aceptación:** instantes almacenados en UTC; períodos interpretados en hora local de Argentina con inicio inclusivo y final exclusivo; filtros de producto, tipo y período combinados con AND.
- **Pendiente:** la zona horaria concreta de Argentina bloquea únicamente esta tarea y las consultas por período; no impide T001–T009 ni las tareas posteriores que no dependen de filtros temporales.

## Fase 2 — Servicios, confirmación, compensaciones y concurrencia
### T011 — Servicio de productos
- **Fase:** 2.
- **Objetivo:** implementar los casos de uso de alta, modificación y desactivación.
- **Archivos previstos:** `app/services/products.py`.
- **Dependencias:** T004, T009.
- **Pruebas:** `tests/integration/test_product_service.py`.
- **Criterios de aceptación:** alta válida; SKU duplicado e inmutabilidad rechazados; desactivación conserva datos e historial.

### T012 — Servicio de movimientos
- **Fase:** 2.
- **Objetivo:** registrar entradas y salidas con confirmación transaccional.
- **Archivos previstos:** `app/services/movements.py`.
- **Dependencias:** T005, T009.
- **Pruebas:** `tests/integration/test_movement_service.py` (incluye rechazo de movimientos sin motivo).
- **Criterios de aceptación:** movimiento válido persiste y actualiza stock; salida que supera disponibilidad se rechaza sin cambios parciales; no se permite stock negativo; un movimiento confirmado no puede editarse ni eliminarse mediante el servicio.

### T013 — Servicio de compensaciones
- **Fase:** 2.
- **Objetivo:** registrar compensaciones parciales o totales.
- **Archivos previstos:** `app/services/movements.py` (o módulo de compensaciones).
- **Dependencias:** T006, T012.
- **Pruebas:** `tests/integration/test_compensation_service.py`.
- **Criterios de aceptación:** compensación válida crea movimiento vinculado; excedente, de tipo igual o encadenada se rechaza; un producto desactivado no admite operaciones ordinarias nuevas ni modificaciones y solo permite compensaciones históricas autorizadas vinculadas a su historial.

### T014 — Servicio de propuestas
- **Fase:** 2.
- **Objetivo:** crear, editar y confirmar propuestas con revalidación transaccional.
- **Archivos previstos:** `app/services/proposals.py`.
- **Dependencias:** T009, T011, T012, T013.
- **Pruebas:** `tests/integration/test_proposal_service.py`.
- **Criterios de aceptación:** una propuesta pendiente no modifica inventario; su edición no crea movimientos ni modifica datos; al confirmar se revalidan dentro de la transacción el stock vigente y el límite pendiente de compensación (parcial, acumulado y sin stock negativo); si una regla falla, se rechaza sin cambios parciales.

### T015 — Concurrencia e integridad
- **Fase:** 2.
- **Objetivo:** proteger las confirmaciones frente a operaciones simultáneas.
- **Archivos previstos:** transacciones `BEGIN IMMEDIATE` en los servicios de confirmación.
- **Dependencias:** T012, T013, T014.
- **Pruebas:** `tests/concurrency/test_concurrency.py` con conexiones SQLite independientes.
- **Criterios de aceptación:** dos salidas y dos compensaciones simultáneas sobre el mismo producto solo persisten si cada una cumple las reglas sobre el estado vigente; sin stock negativo, sin superar el límite pendiente de compensación y sin cambios parciales.

## Fase 3 — Herramientas, agentes y grafo
### T016 — Prompts centralizados
- **Fase:** 3.
- **Objetivo:** definir los prompts del Supervisor y del Inventory Agent.
- **Archivos previstos:** `app/prompts/prompts.py`.
- **Dependencias:** T001.
- **Pruebas:** `tests/ai/test_prompts.py`.
- **Criterios de aceptación:** existen constantes para los prompts del Supervisor y del Inventory Agent; cada prompt describe su responsabilidad, indica responder solo con datos reales y prohíbe explícitamente la escritura directa en la base de datos; las pruebas verifican que dichas prohibiciones y responsabilidades están presentes en el texto.

### T017 — Estado del grafo
- **Fase:** 3.
- **Objetivo:** definir el estado compartido.
- **Archivos previstos:** `app/graph/state.py`.
- **Dependencias:** T001.
- **Pruebas:** `tests/ai/test_state.py`.
- **Criterios de aceptación:** el estado define y transporta el mensaje del usuario, la propuesta pendiente (si existe) y el resultado de la ejecución; las pruebas verifican la construcción del estado inicial y la propagación de cada campo entre nodos, sin que se introduzcan datos inventados.

### T018 — Herramientas de consulta
- **Fase:** 3.
- **Objetivo:** exponer consultas de solo lectura.
- **Archivos previstos:** `app/tools/inventory_tools.py`.
- **Dependencias:** T009, T011, T012, T013.
- **Pruebas:** `tests/ai/test_inventory_tools.py`.
- **Criterios de aceptación:** cada herramienta devuelve datos reales de stock actual, stock bajo, reposición e historial sin escribir en SQLite.

### T019 — Herramientas de propuesta
- **Fase:** 3.
- **Objetivo:** preparar y editar propuestas sin confirmar.
- **Archivos previstos:** `app/tools/proposal_tools.py`.
- **Dependencias:** T014.
- **Pruebas:** `tests/ai/test_proposal_tools.py`.
- **Criterios de aceptación:** las herramientas crean o editan propuestas sin persistir movimientos ni modificar inventario.

### T020 — Inventory Agent
- **Fase:** 3.
- **Objetivo:** interpretar intenciones y generar respuestas o propuestas.
- **Archivos previstos:** `app/agents/inventory.py`.
- **Dependencias:** T016, T017, T018, T019.
- **Pruebas:** `tests/ai/test_inventory_agent.py` (modelo falso).
- **Criterios de aceptación:** el agente genera propuestas o respuestas con datos reales; no escribe directamente en la base de datos.

### T021 — Supervisor y grafo
- **Fase:** 3.
- **Objetivo:** coordinar la derivación al Inventory Agent.
- **Archivos previstos:** `app/agents/supervisor.py`, `app/graph/workflow.py`.
- **Dependencias:** T020.
- **Pruebas:** `tests/ai/test_supervisor.py` (integración con modelo falso).
- **Criterios de aceptación:** el Supervisor deriva la solicitud al Inventory Agent y devuelve un resultado válido.

## Fase 4 — Interfaz Streamlit
### T022 — UI de conversación y consultas
- **Fase:** 4.
- **Objetivo:** mostrar consultas y respuestas.
- **Archivos previstos:** `app/ui/main.py`.
- **Dependencias:** T021.
- **Pruebas:** pruebas de flujo con proveedor falso.
- **Criterios de aceptación:** las consultas muestran los datos reales del inventario.

### T023 — UI de propuestas y confirmación
- **Fase:** 4.
- **Objetivo:** presentar propuestas y confirmación explícita.
- **Archivos previstos:** `app/ui/main.py`.
- **Dependencias:** T014, T021.
- **Pruebas:** pruebas de flujo con proveedor falso.
- **Criterios de aceptación:** la confirmación se realiza mediante control de UI, sin pasar por el LLM, y ejecuta únicamente operaciones válidas.

## Fase 5 — Integración y validación
### T024 — Integración end-to-end
- **Fase:** 5.
- **Objetivo:** verificar el flujo completo.
- **Archivos previstos:** `tests/integration/test_end_to_end.py`.
- **Dependencias:** T023.
- **Pruebas:** flujo completo con proveedor falso.
- **Criterios de aceptación:** el flujo de consulta, propuesta, confirmación y rechazo funciona sin llamadas reales a DeepSeek.

### T025 — Trazabilidad y validación RF-01 a RF-06
- **Fase:** 5.
- **Objetivo:** verificar cobertura de requisitos y ausencia de funcionalidades excluidas.
- **Archivos previstos:** revisión de trazabilidad.
- **Dependencias:** T024.
- **Pruebas:** revisión de la matriz de trazabilidad.
- **Criterios de aceptación:** cada criterio de aceptación de RF-01 a RF-06 de `spec.md` está vinculado a al menos una prueba identificable (archivo y caso); la trazabilidad es verificable de forma explícita; el MVP no incluye múltiples usuarios, proveedores, compras ni ventas formales.

## Matriz de trazabilidad
| Requisito | Tareas | Criterios clave de aceptación |

| --- | --- | --- |
| RF-01 | T003, T004, T011, T019, T023 | SKU único e inmutable; nombre recortado no vacío; stock mínimo ≥0 con ≤2 decimales; precio >0; desactivación conserva historial. |
| RF-02 | T003, T005, T012, T015 | Stock según movimientos confirmados; no negativo; inicial cero sin movimiento y positivo como entrada inicial; disponibilidad validada al confirmar. |
| RF-03 | T006, T009, T012, T013, T014, T015 | Motivo obligatorio; movimientos confirmados inmutables; compensación de tipo opuesto, no encadenada, con límite acumulado; compensaciones en desactivados. |
| RF-04 | T007, T011, T018 | Stock bajo = actual ≤ mínimo; reposición solo activos con faltante >0; faltante = máx(0, mínimo − actual); desactivados excluidos. |
| RF-05 | T014, T016–T023 | Consultar datos reales; propuesta editable sin efectos; confirmación explícita; revalidación; no inventar resultados. |
| RF-06 | T009, T012, T013, T025 | Único inventario/ubicación; sin usuarios, proveedores, compras o ventas; sin edición directa de confirmados; corrección solo por compensación. |

## Decisiones pendientes
- **Identificador del modelo DeepSeek:** bloquea la ejecución real en Fase 3/5 (no las pruebas con modelo falso).
- **Zona horaria de Argentina:** bloquea T010 y las herramientas de consulta por período; las tareas no relacionadas pueden continuar.
- **Ciclo de vida de propuestas y topología de despliegue:** deferibles.

## Estrategia de ejecución
1. Ejecutar las tareas en orden, una por vez, dentro de su fase.
2. Al terminar una tarea, ejecutar sus pruebas y verificar sus criterios de aceptación.
3. Detenerse si una prueba falla o si una decisión pendiente bloquea la tarea; no avanzar sin resolverlo.
4. Revisar el resultado antes de continuar y esperar aprobación explícita al finalizar cada fase.
