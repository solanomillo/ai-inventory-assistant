# Tareas: núcleo de inventario

## Resumen del alcance

Este documento descompone el plan técnico aprobado (`plan.md`) en tareas pequeñas, ordenadas por dependencias y verificables, para el MVP de inventario definido por `spec.md`.

- Los identificadores (`T001`–`T026`) son únicos y estables.
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
- **Criterios de aceptación:** `get_llm()` lanza un error sin `DEEPSEEK_API_KEY`; con valores inyectados devuelve `ChatDeepSeek` con `temperature=0`; la prueba no realiza llamadas de red (el adaptador se verifica por construcción del objeto, no por invocación).
- **Pendiente:** el identificador del modelo DeepSeek queda documentado; no se fija aquí.

## Fase 1 — Dominio puro y persistencia

### T003 — Representación numérica (unidades enteras, factor, precio y costo)

- **Fase:** 1.
- **Objetivo:** definir la representación exacta de cantidades, factor, precio y costo.
- **Archivos previstos:** `app/domain/quantities.py` (validación de enteros, factor, precio y costo).
- **Dependencias:** ninguna.
- **Pruebas:** `tests/unit/test_quantities.py`.
- **Criterios de aceptación:**
  - Las cantidades de stock y el stock mínimo se representan mediante números enteros mayores o iguales a cero.
  - Las cantidades de movimientos de entrada, salida, apertura y compensación deben ser números enteros mayores que cero.
  - Los factores de conversión de las presentaciones son números enteros mayores o iguales a uno.
  - Los precios de venta se representan con `Decimal` y deben ser mayores que cero.
  - Los costos de entrada son opcionales; cuando se informan, se representan con `Decimal` y deben ser mayores o iguales a cero.
  - Los cálculos monetarios no utilizan `float`.
  - Las validaciones distinguen entre cantidades de stock y cantidades de movimientos.
- **RF:** RF-02 (cantidades), RF-07 (factor, precio, costo).

### T004 — Reglas de dominio de producto y presentaciones

- **Fase:** 1.
- **Objetivo:** implementar y validar las reglas de RF-01 y RF-07.
- **Archivos previstos:** `app/domain/products.py`, `app/domain/presentations.py`.
- **Dependencias:** T003.
- **Pruebas:** `tests/unit/test_product_rules.py`, `tests/unit/test_presentation_rules.py`.
- **Criterios de aceptación:**
  - El SKU es único sin distinguir mayúsculas de minúsculas, inmutable, obligatorio y se almacena sin espacios al principio o al final.
  - El nombre del producto y la unidad base son obligatorios, se recortan y no pueden quedar vacíos.
  - El stock mínimo es un entero mayor o igual que cero.
  - Cada producto tiene exactamente una presentación base con factor `1`.
  - El nombre de cada presentación es único dentro del producto, sin distinguir mayúsculas de minúsculas.
  - El factor es un entero mayor o igual que `1`.
  - El factor no puede modificarse cuando la presentación tiene stock cerrado o movimientos históricos.
  - El precio de venta de cada presentación es mayor que cero y se valida mediante `Decimal`.
- **RF:** RF-01, RF-07.

### T005 — Reglas de dominio de stock compuesto y apertura

- **Fase:** 1.
- **Estado:** **Cerrada y aprobada** (tras corrección y revisión independiente). Historial: T005 se había cerrado admitiendo la apertura de la presentación base (factor `1`); una revisión posterior detectó la divergencia con `spec.md` (RF-03), la corrección se aplicó en el dominio (`app/domain/stock.py`) con su prueba de rechazo y la revisión independiente final quedó aprobada. Esta aprobación previa se conserva como antecedente.
- **Objetivo:** implementar las reglas de RF-02 (stock suelto, cerrado, total equivalente y apertura).
- **Archivos previstos:** `app/domain/stock.py`.
- **Dependencias:** T003.
- **Pruebas:** `tests/unit/test_stock_rules.py`; se sustituye `test_open_presentations_accepts_base_presentation` por la prueba de rechazo `test_open_presentations_rejects_base_presentation`, que comprueba que la apertura con factor `1` se rechaza y que los saldos no cambian.
- **Criterios de aceptación:**
  - Las unidades sueltas y el stock cerrado no pueden ser negativos.
  - El total equivalente se deriva correctamente mediante `unidades_sueltas + Σ(stock_cerrado × factor)`.
  - El total equivalente no se persiste como un saldo independiente.
  - La apertura de `N` presentaciones resta `N` al stock cerrado y suma `N × factor` a las unidades sueltas.
  - Una apertura válida no modifica el total equivalente.
  - La apertura se rechaza si no existe stock cerrado suficiente.
  - La apertura se rechaza si la presentación tiene factor `1` (presentación base), sin alterar los saldos ni registrar el movimiento.
- **RF:** RF-02, RF-03 (apertura).

### T006 — Reglas de movimientos y compensaciones con FIFO

- **Fase:** 1.
- **Estado:** **Cerrada y aprobada** tras revisión independiente.
- **Objetivo:** implementar las reglas de RF-03 (tipos, compensaciones y consumo FIFO).
- **Archivos previstos:** `app/domain/movements.py`.
- **Dependencias:** T005.
- **Pruebas:** `tests/unit/test_movement_rules.py`.
- **Criterios de aceptación:**
  - Una compensación es un movimiento de tipo compensación cuyo efecto sobre el stock es opuesto al del movimiento original, y no puede encadenarse.
  - Las aperturas no son compensables.
  - La cantidad compensable de una entrada cerrada se calcula como `cantidad - cantidad_consumida - cantidad_compensada`.
  - Las aperturas y las salidas cerradas consumen entradas cerradas mediante FIFO, por presentación.
  - Una compensación que excede la cantidad compensable se rechaza.
  - Las cantidades consumidas y compensadas no pueden superar la cantidad original.
  - Las operaciones de compensación no pueden producir stock negativo.
- **RF:** RF-03.

### T007 — Reglas de stock bajo y reposición

- **Fase:** 1.
- **Objetivo:** implementar las reglas de RF-04.
- **Archivos previstos:** `app/domain/stock.py` (o módulo equivalente).
- **Dependencias:** T005.
- **Pruebas:** `tests/unit/test_stock_alerts.py`.
- **Criterios de aceptación:**
  - Se considera que un producto tiene stock bajo cuando el total equivalente es menor o igual que el stock mínimo.
  - Las consultas globales de stock bajo y reposición incluyen únicamente productos activos.
  - La reposición se propone solo cuando el faltante es mayor que cero.
  - El faltante se calcula como `max(0, stock_minimo - total_equivalente)`.
  - Los productos desactivados se excluyen de las consultas globales de stock bajo y reposición.
- **RF:** RF-04.

### T008 — Esquema y conexión SQLite

- **Fase:** 1.
- **Objetivo:** crear el esquema persistente y el manejo de conexión.
- **Archivos previstos:** `app/infrastructure/sqlite/schema.py`, `app/infrastructure/sqlite/connection.py`.
- **Dependencias:** T001.
- **Pruebas:** `tests/integration/test_schema.py` (SQLite en memoria).
- **Criterios de aceptación:**
  - El esquema se crea desde una base vacía con tablas de productos, presentaciones, movimientos y propuestas.
  - Las cantidades, factores, saldos y contadores FIFO se almacenan en `INTEGER`.
  - Los precios y costos se almacenan como texto decimal exacto (`TEXT`).
  - Se definen restricciones de positividad o no negatividad según corresponda.
  - Los motivos de los movimientos no pueden quedar vacíos.
  - Las presentaciones respetan la unicidad del nombre dentro de cada producto.
  - La unicidad del SKU y del nombre de presentación se implementa sin distinguir mayúsculas de minúsculas (por ejemplo, `COLLATE NOCASE` o un índice sobre `lower(...)`).
  - El esquema incluye los campos necesarios para la trazabilidad FIFO de las entradas cerradas.
  - No se implementa migración de datos históricos: el esquema se crea desde una base nueva.
- **RF:** RF-02, RF-03, RF-07.

### T009 — Repositorios básicos y contrato

- **Fase:** 1.
- **Objetivo:** implementar persistencia básica sin filtros temporales.
- **Archivos previstos:** `app/services/ports.py` (Protocol de repositorio), `app/infrastructure/sqlite/repositories.py`.
- **Dependencias:** T008.
- **Pruebas:** `tests/integration/test_repositories.py` (unicidad de SKU, nombre de presentación único, rechazo de movimientos sin motivo, inmutabilidad, filtros por producto y tipo).
- **Criterios de aceptación:**
  - Los SKU duplicados y las presentaciones con nombres duplicados dentro del mismo producto se rechazan, incluido el caso en que difieran solo en mayúsculas o minúsculas (comparación sin distinguir mayúsculas).
  - Los movimientos sin motivo o con motivo vacío se rechazan.
  - La persistencia no permite editar ni eliminar movimientos confirmados.
  - Las consultas por producto y tipo devuelven únicamente los registros que cumplen los filtros.
  - Las búsquedas por SKU y por nombre no distinguen mayúsculas de minúsculas y conservan el texto original almacenado.
  - Los repositorios permiten recuperar los datos necesarios para reconstruir saldos y consultar el historial de movimientos.
- **RF:** RF-01, RF-03, RF-07.

### T010 — Filtros temporales por período

- **Fase:** 1.
- **Objetivo:** implementar consultas por período con conversión de zona horaria.
- **Archivos previstos:** `app/infrastructure/sqlite/repositories.py` (consulta por período), helpers de fechas.
- **Dependencias:** T009.
- **Pruebas:** `tests/integration/test_period_filters.py`.
- **Criterios de aceptación:**
  - Los instantes se almacenan en UTC.
  - Los períodos se interpretan en la zona horaria local de Argentina (`America/Argentina/Buenos_Aires`) y se convierten a UTC para consultar los movimientos.
  - El inicio del período es inclusivo y el final es exclusivo.
  - Los filtros de producto, tipo y período se combinan mediante AND.
- **Decisión resuelta:** la zona horaria es `America/Argentina/Buenos_Aires`; los períodos ingresados en hora local de Argentina se convierten a UTC y la persistencia de instantes continúa en UTC, con semántica `[inicio, fin)`. La tarea mantiene su alcance: filtros temporales en las consultas de movimientos, conversión de zona horaria y combinación mediante AND con los filtros de producto y tipo.

## Fase 2 — Servicios, confirmación, apertura, compensaciones y concurrencia

### T011 — Servicio de productos y presentaciones

- **Fase:** 2.
- **Objetivo:** implementar los casos de uso de alta, modificación, desactivación y gestión de presentaciones.
- **Archivos previstos:** `app/services/products.py`, `app/services/presentations.py`.
- **Dependencias:** T004, T009.
- **Pruebas:** `tests/integration/test_product_service.py`, `tests/integration/test_presentation_service.py`.
- **Criterios de aceptación:**
  - Se permite dar de alta productos válidos y se rechazan los SKU duplicados.
  - El SKU no puede modificarse una vez creado.
  - El alta del producto permite registrar stock inicial suelto y/o cerrado.
  - Cada porción positiva de stock inicial genera un movimiento de entrada marcado con `is_initial_stock`.
  - Si el stock inicial es cero, no se crea ningún movimiento de stock inicial.
  - El stock inicial y sus movimientos se registran de forma consistente, sin duplicar saldos.
  - La desactivación conserva el historial.
  - Se rechaza cualquier modificación (nombre o stock mínimo) de un producto desactivado.
  - Se rechaza la creación o modificación de presentaciones de un producto desactivado.
  - Una presentación nueva comienza con stock cerrado cero y no modifica otros saldos.
  - El factor no puede modificarse cuando la presentación tiene stock o movimientos históricos.
- **RF:** RF-01, RF-02, RF-03, RF-07.

### T012 — Servicio de entradas y salidas (costo y snapshot de precio)

- **Fase:** 2.
- **Objetivo:** registrar entradas y salidas cerradas y sueltas con confirmación transaccional.
- **Archivos previstos:** `app/services/movements.py`.
- **Dependencias:** T005, T009.
- **Pruebas:** `tests/integration/test_movement_service.py` (incluye costo y snapshot de precio).
- **Criterios de aceptación:**
  - Una entrada válida persiste el movimiento y actualiza el stock correspondiente.
  - El costo de una entrada es opcional y, cuando se informa, debe ser mayor o igual que cero.
  - Una salida válida guarda `applied_price` con el precio de venta de la presentación vigente al confirmar.
  - Las salidas sueltas utilizan el precio de la presentación base.
  - Una salida que supera la disponibilidad se rechaza.
  - No se permite stock negativo.
  - Los movimientos confirmados son inmutables.
  - Las modificaciones posteriores de los precios de venta no alteran el precio aplicado registrado en movimientos anteriores.
  - Se rechaza toda entrada o salida ordinaria nueva sobre un producto desactivado.
- **RF:** RF-02, RF-03, RF-07.

### T013 — Servicio de apertura

- **Fase:** 2.
- **Objetivo:** registrar aperturas atómicas y consumir FIFO.
- **Archivos previstos:** `app/services/movements.py` (o módulo de apertura).
- **Dependencias:** T012.
- **Pruebas:** `tests/integration/test_aperture_service.py`.
- **Criterios de aceptación:**
  - La apertura de `N ≥ 1` presentaciones resta `N` al stock cerrado y suma `N × factor` a las unidades sueltas en una única transacción.
  - El movimiento de apertura conserva la presentación, cantidad y motivo.
  - La apertura consume FIFO las entradas cerradas correspondientes a esa presentación.
  - Se actualizan los contadores de consumo de las entradas cerradas afectadas.
  - La operación se rechaza si no existe stock cerrado suficiente.
  - Se rechaza la apertura de la presentación base (factor `1`) reutilizando la validación del dominio de T005 (`app/domain/stock.py`); el rechazo proviene del dominio, sin reimplementar la regla ni duplicar la lógica de negocio, y no crea movimientos ni altera saldos.
  - Se rechaza la apertura sobre un producto desactivado.
  - La apertura es inmutable y no compensable.
  - Ante un error, se revierten todos los cambios de la operación.
- **RF:** RF-02, RF-03.

### T014 — Servicio de compensaciones

- **Fase:** 2.
- **Objetivo:** registrar compensaciones parciales o totales con límite compensable.
- **Archivos previstos:** `app/services/movements.py` (o módulo de compensaciones).
- **Dependencias:** T006, T012.
- **Pruebas:** `tests/integration/test_compensation_service.py`.
- **Criterios de aceptación:**
  - Una compensación válida crea un movimiento vinculado al movimiento original.
  - Se rechazan las compensaciones que exceden la cantidad pendiente, cuyo efecto no es opuesto al del movimiento original o intentan encadenarse.
  - Las aperturas no son compensables.
  - Las compensaciones de entradas cerradas respetan la cantidad compensable y el stock cerrado vigente.
  - Los contadores de cantidades compensadas se actualizan de manera consistente.
  - Los productos desactivados solo admiten compensaciones históricas válidas permitidas por la especificación.
  - Las entradas sueltas conservan los límites de compensación establecidos, sin atribuirles una trazabilidad por lote que el MVP no implementa.
  - La operación se ejecuta de forma atómica.
- **RF:** RF-03.

### T015 — Servicio de propuestas

- **Fase:** 2.
- **Objetivo:** crear, editar y confirmar propuestas con revalidación transaccional.
- **Archivos previstos:** `app/services/proposals.py`.
- **Dependencias:** T009, T011, T012, T013, T014.
- **Pruebas:** `tests/integration/test_proposal_service.py`.
- **Criterios de aceptación:**
  - Una propuesta pendiente no modifica el inventario ni crea movimientos confirmados.
  - Editar una propuesta no modifica los productos, las presentaciones ni los saldos existentes.
  - La confirmación revalida, dentro de la transacción, el stock vigente, las reglas del producto y la cantidad compensable.
  - Si una regla falla, se rechaza la confirmación sin cambios parciales.
  - La confirmación se ejecuta por un servicio de aplicación invocado explícitamente, sin delegar la decisión final al LLM.
- **RF:** RF-05.

### T016 — Concurrencia e integridad

- **Fase:** 2.
- **Objetivo:** proteger las confirmaciones frente a operaciones simultáneas.
- **Archivos previstos:** transacciones `BEGIN IMMEDIATE` en los servicios de confirmación.
- **Dependencias:** T012, T013, T014, T015.
- **Pruebas:** `tests/concurrency/test_concurrency.py` con conexiones SQLite independientes.
- **Criterios de aceptación:**
  - Las aperturas, salidas y compensaciones simultáneas sobre la misma presentación solo persisten si cada una cumple las reglas sobre el estado vigente.
  - No se produce stock negativo ni se supera la cantidad compensable.
  - Los saldos suelto y cerrado se mantienen coherentes.
  - Los contadores FIFO se actualizan de manera consistente.
  - Una operación fallida no deja cambios parciales.
  - Las validaciones de disponibilidad y compensación se realizan dentro de la transacción.
- **RF:** RF-02, RF-03.

## Fase 3 — Herramientas, agentes y grafo

### T017 — Prompts centralizados

- **Fase:** 3.
- **Objetivo:** definir los prompts del Supervisor y del Inventory Agent.
- **Archivos previstos:** `app/prompts/prompts.py`.
- **Dependencias:** T001.
- **Pruebas:** `tests/ai/test_prompts.py`.
- **Criterios de aceptación:** existen constantes para los prompts del Supervisor y del Inventory Agent; cada prompt describe su responsabilidad, indica responder solo con datos reales y prohíbe explícitamente la escritura directa en la base de datos; las pruebas verifican que dichas prohibiciones y responsabilidades están presentes en el texto.

### T018 — Estado del grafo

- **Fase:** 3.
- **Objetivo:** definir el estado compartido.
- **Archivos previstos:** `app/graph/state.py`.
- **Dependencias:** T001.
- **Pruebas:** `tests/ai/test_state.py`.
- **Criterios de aceptación:** el estado define y transporta el mensaje del usuario, la propuesta pendiente (si existe) y el resultado de la ejecución; las pruebas verifican la construcción del estado inicial y la propagación de cada campo entre nodos, sin que se introduzcan datos inventados.

### T019 — Herramientas de consulta

- **Fase:** 3.
- **Objetivo:** exponer consultas de solo lectura.
- **Archivos previstos:** `app/tools/inventory_tools.py`.
- **Dependencias:** T009, T011, T012, T013.
- **Pruebas:** `tests/ai/test_inventory_tools.py`.
- **Criterios de aceptación:** cada herramienta devuelve datos reales de stock (suelto, cerrado y total equivalente), stock bajo, reposición e historial sin escribir en SQLite; las herramientas resuelven productos por SKU y por nombre sin distinguir mayúsculas de minúsculas, conservando el texto original almacenado y mostrando todas las coincidencias sin seleccionar ninguna.
- **RF:** RF-01, RF-04, RF-05.

### T020 — Herramientas de propuesta

- **Fase:** 3.
- **Objetivo:** preparar y editar propuestas sin confirmar.
- **Archivos previstos:** `app/tools/proposal_tools.py`.
- **Dependencias:** T015.
- **Pruebas:** `tests/ai/test_proposal_tools.py`.
- **Criterios de aceptación:** las herramientas crean o editan propuestas (incluida apertura) sin persistir movimientos confirmados ni modificar el inventario.
- **RF:** RF-05.

### T021 — Inventory Agent

- **Fase:** 3.
- **Objetivo:** interpretar intenciones y generar respuestas o propuestas.
- **Archivos previstos:** `app/agents/inventory.py`.
- **Dependencias:** T017, T018, T019, T020.
- **Pruebas:** `tests/ai/test_inventory_agent.py` (modelo falso).
- **Criterios de aceptación:** el agente genera propuestas o respuestas con datos reales; no escribe directamente en la base de datos; las operaciones que modifican inventario requieren una propuesta y confirmación explícita.

### T022 — Supervisor y grafo

- **Fase:** 3.
- **Objetivo:** coordinar la derivación al Inventory Agent.
- **Archivos previstos:** `app/agents/supervisor.py`, `app/graph/workflow.py`.
- **Dependencias:** T021.
- **Pruebas:** `tests/ai/test_supervisor.py` (integración con modelo falso).
- **Criterios de aceptación:** el Supervisor deriva la solicitud al Inventory Agent y devuelve un resultado válido; el flujo permite responder consultas o producir propuestas, sin confirmar operaciones por medio del LLM.

## Fase 4 — Interfaz Streamlit

### T023 — UI de conversación y consultas

- **Fase:** 4.
- **Objetivo:** mostrar consultas y respuestas.
- **Archivos previstos:** `app/ui/main.py`.
- **Dependencias:** T022.
- **Pruebas:** pruebas de flujo con proveedor falso.
- **Criterios de aceptación:** las consultas muestran los datos reales del inventario, incluyendo stock suelto, cerrado y total equivalente, y presentan correctamente los resultados de stock bajo, reposición e historial.

### T024 — UI de propuestas y confirmación

- **Fase:** 4.
- **Objetivo:** presentar propuestas y permitir su confirmación explícita.
- **Archivos previstos:** `app/ui/main.py`.
- **Dependencias:** T015, T022.
- **Pruebas:** pruebas de flujo con proveedor falso.
- **Criterios de aceptación:** las propuestas pueden revisarse antes de confirmar; la confirmación (incluida la apertura) se realiza mediante un control de UI, sin pasar por el LLM; se ejecutan únicamente operaciones válidas y los errores de validación se muestran sin aplicar cambios parciales.
- **RF:** RF-05.

## Fase 5 — Integración y validación

### T025 — Integración end-to-end

- **Fase:** 5.
- **Objetivo:** verificar el flujo completo.
- **Archivos previstos:** `tests/integration/test_end_to_end.py`.
- **Dependencias:** T024.
- **Pruebas:** flujo completo con proveedor falso.
- **Criterios de aceptación:** el flujo de consulta, propuesta, confirmación, apertura y rechazo funciona sin llamadas reales a DeepSeek; las operaciones confirmadas respetan las reglas del dominio y persisten datos coherentes.

### T026 — Trazabilidad y validación RF-01 a RF-07

- **Fase:** 5.
- **Objetivo:** verificar la cobertura de requisitos y la ausencia de funcionalidades excluidas.
- **Archivos previstos:** revisión de trazabilidad.
- **Dependencias:** T025.
- **Pruebas:** revisión de la matriz de trazabilidad.
- **Criterios de aceptación:** cada criterio de aceptación de RF-01 a RF-07 de `spec.md` está vinculado a al menos una prueba identificable (archivo y caso); la matriz mapea los criterios de consultas por SKU/nombre sin distinguir mayúsculas a T009 y T019 y los criterios de productos desactivados a T011 y T013; la trazabilidad es verificable de forma explícita; el MVP no incluye múltiples usuarios, proveedores, compras ni ventas formales.

## Matriz de trazabilidad

| Requisito | Tareas                                         | Criterios clave de aceptación                                                                                                                                                                                                                                                                                                                                                                             |
| --------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RF-01     | T004, T009, T011, T019, T020, T024             | SKU único e inmutable (sin distinguir mayúsculas); nombre y unidad base obligatorios; consultas por SKU y por nombre sin distinguir mayúsculas conservando el texto original; múltiples coincidencias mostradas sin seleccionar automáticamente; stock mínimo entero ≥ 0; desactivación conserva historial; el producto desactivado solo admite consultas, historial y compensaciones históricas válidas. |
| RF-02     | T003, T005, T008, T011, T012, T013, T016, T019 | Stock suelto/cerrado no negativo; total equivalente derivado; cantidades enteras; stock inicial registrado mediante entradas; apertura atómica y rechazada sobre un producto desactivado; disponibilidad validada al confirmar; consultas de stock por SKU/nombre sin distinguir mayúsculas.                                                                                                              |
| RF-03     | T005, T006, T008, T009, T012, T013, T014, T016 | Motivo obligatorio; movimientos inmutables; compensación opuesta y no encadenada; consumo FIFO y cantidad compensable; apertura no compensable, rechazada sobre un producto desactivado y prohibida sobre la presentación base (factor `1`), regla validada en el dominio (T005) y reutilizada sin duplicación por el servicio de apertura (T013); compensaciones históricas válidas admitidas en productos desactivados.                                                                                                                                          |
| RF-04     | T007, T019                                     | Stock bajo = total equivalente ≤ mínimo; reposición solo para activos con faltante > 0; desactivados excluidos.                                                                                                                                                                                                                                                                                           |
| RF-05     | T015, T017–T024                                | Consultar datos reales; propuesta editable sin efectos; confirmación explícita; revalidación; no inventar resultados.                                                                                                                                                                                                                                                                                     |
| RF-06     | T009, T012, T014, T026                         | Único inventario/ubicación; sin usuarios, proveedores, compras o ventas; sin edición directa de confirmados; corrección por compensación, salvo aperturas.                                                                                                                                                                                                                                                |
| RF-07     | T003, T004, T008, T009, T011, T012             | Presentación base con factor 1; factor y precio válidos; factor inmutable con stock/historial; precio independiente; costo opcional sin distribución; se rechaza crear o modificar presentaciones de un producto desactivado.                                                                                                                                                                             |

## Decisiones pendientes

- **Identificador del modelo DeepSeek:** bloquea la ejecución real en Fase 3/5, pero no las pruebas con modelo falso.
- **Ciclo de vida de propuestas y topología de despliegue:** deferibles; no deben ampliar el alcance sin una decisión posterior.

## Estrategia de ejecución

1. Ejecutar las tareas en orden, una por vez, dentro de su fase.
2. Al terminar una tarea, ejecutar sus pruebas y verificar sus criterios de aceptación.
3. Detenerse si una prueba falla o si una decisión pendiente bloquea la tarea; no avanzar sin resolverlo.
4. Revisar el resultado antes de continuar y esperar aprobación explícita al finalizar cada fase.
5. No modificar `.venv`, `.env` ni instalar dependencias desde el agente.
6. No realizar cambios en tareas posteriores como parte de la implementación de una tarea individual, salvo autorización explícita.
