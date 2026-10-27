# Plan técnico: núcleo de inventario

## Estado

Aprobado. Ampliado con RF-08 (actualización del precio de venta e historial).
RF-09 (interfaz de gestión del inventario) está **aprobada** y formalizada en este documento: no modifica RF-01 a RF-08. Sus decisiones técnicas aprobadas se registran en «Decisiones resueltas (RF-09)».

## Objetivo técnico

Implementar el MVP de inventario como una aplicación Python modular con Streamlit (interfaz), LangGraph (orquestación), LangChain (modelo y herramientas), DeepSeek (proveedor LLM), SQLite mediante `sqlite3` (persistencia duradera en archivo), Pydantic (validación y configuración) y pytest (pruebas).

Las reglas de negocio son deterministas y residen fuera del LLM. Los agentes y las herramientas no escriben directamente en la base de datos: delegan en servicios de aplicación que validan y ejecutan únicamente operaciones confirmadas.

El precio de venta de cada presentación se actualiza mediante propuestas confirmadas y conserva un **historial de cambios de precio** de solo anexado. Ese historial exige **persistencia duradera**: un archivo SQLite con ruta configurable, **independiente del directorio de trabajo** desde el que se inicia la aplicación.

## Stack acordado

| Componente        | Tecnología                | Responsabilidad                                               |
| ----------------- | ------------------------- | ------------------------------------------------------------- |
| Lenguaje          | Python                    | Lógica, interfaz, persistencia, agentes y pruebas.            |
| Interfaz          | Streamlit                 | Navegación lateral (sidebar) con secciones de consulta, formularios de propuestas, revisión/edición/descarte, conversación y confirmación explícita. |
| Orquestación      | LangGraph                 | Flujo Supervisor → Inventory Agent.                           |
| IA y herramientas | LangChain                 | Adaptador de modelo y definición de herramientas.             |
| LLM               | DeepSeek API              | Interpretación de lenguaje natural.                           |
| Persistencia      | SQLite mediante `sqlite3` | Datos del MVP y transacciones.                                |
| Validación        | Pydantic                  | Esquemas de entradas, propuestas y resultados en los límites. |
| Pruebas           | pytest                    | Unitarias, integración, concurrencia y de agentes.            |

## Alcance de agentes

- Supervisor: clasifica la solicitud y la dirige a Inventory Agent.
- Inventory Agent: resuelve consultas y genera propuestas; no confirma ni escribe en SQLite.

Se excluyen agentes de ventas, pronósticos y recomendaciones.

## Arquitectura y flujo

```
Streamlit (ui)
  → grafo LangGraph (graph)
      → Supervisor (agents/supervisor)
          → Inventory Agent (agents/inventory)
              → herramientas LangChain (tools)
                  → servicios de aplicación (services)
                      → repositorios (infrastructure/sqlite)
                          → SQLite
```

1. Streamlit recibe un mensaje.
2. El Supervisor lo deriva al Inventory Agent.
3. Inventory Agent usa herramientas de solo lectura para consultas.
4. Para escrituras, genera una propuesta estructurada (Pydantic) sin persistir movimientos.
5. Streamlit muestra la propuesta y pide confirmación explícita.
6. La confirmación (acción de UI, no del LLM) invoca el servicio de confirmación.
7. El servicio abre una transacción, revalida contra datos vigentes y aplica todo o revierte todo.
8. Streamlit muestra el resultado real o el motivo de rechazo.

**Composición dual de la interfaz (RF-09):** la aplicación compone, por sesión, la **lectura** —repositorios y servicios de consulta, **sin** `get_llm()`— y, por separado, el **asistente** (grafo Supervisor → Inventory Agent), que solo se compone cuando hay modelo configurado. Las secciones de consulta funcionan sin modelo; la sección del asistente informa su indisponibilidad y no bloquea al resto. La confirmación explícita (pasos 5 a 8) se concentra en un **único punto** de la interfaz y es la única ruta de escritura: los formularios crean propuestas y no escriben.

## Modelo de datos conceptual

### Producto

- `id` interno.
- `sku` único e inmutable; la unicidad no distingue mayúsculas de minúsculas.
- `name` obligatorio, recortado, no vacío.
- `base_unit` nombre de la unidad discreta (botella, paquete, unidad).
- `minimum_stock` entero mayor o igual que cero, en unidades base.
- `loose_units` entero mayor o igual que cero.
- `active` booleano.
- Un producto con `active = false` solo admite consultas, historial y compensaciones históricas válidas; se rechazan el alta de movimientos ordinarios nuevos, las modificaciones, la creación o modificación de presentaciones y las aperturas.

Sin `sale_price`: el precio vive en las presentaciones.

### Presentación

- `id` interno.
- `product_id`.
- `name` único dentro del producto, sin distinguir mayúsculas de minúsculas.
- `factor` entero mayor o igual que 1 (equivalencia en unidades base).
- `sale_price` Decimal mayor que cero.
- `closed_stock` entero mayor o igual que cero (solo relevante si `factor > 1`).

La unidad base es la presentación con `factor = 1`. Un producto vendido solo por unidad tiene una única presentación (`factor = 1`). El `factor` es inmutable cuando esa presentación tiene `closed_stock > 0` o movimientos históricos.

### Stock compuesto

- `loose_units` (producto) y `closed_stock` (por presentación) son los únicos saldos persistidos.
- `total_equivalente = loose_units + Σ(closed_stock_p × factor_p)`, derivado, nunca persistido.
- `minimum_stock`, `stock bajo` y `faltante` se evalúan sobre `total_equivalente`.

### Movimiento

- `id` interno.
- `product_id`.
- `presentation_id` (referencia a la presentación afectada; para sueltas, la unidad base de factor 1).
- `kind`: `entrada_cerrada`, `entrada_suelta`, `salida_cerrada`, `salida_suelta`, `apertura`, `compensacion`.
- `quantity` entero mayor que cero.
- `reason` obligatorio.
- `occurred_at` instante automático en UTC.
- `cost` opcional (Decimal ≥ 0), solo entradas.
- `applied_price` (Decimal > 0), solo salidas, snapshot al confirmar.
- `is_initial_stock` booleano (entradas de stock inicial).
- `original_movement_id` opcional (solo compensaciones).

Para `entrada_cerrada`, además: `consumed_quantity` y `compensated_quantity` (enteros ≥ 0) para el consumo FIFO.

### Compensación

Es un movimiento de `kind = compensacion`, opuesto al original, vinculado a un original no compensatorio, con límite de cantidad compensable. Las aperturas no son compensables. En el MVP las salidas cerradas tampoco son compensables: compensarlas repondría `closed_stock` sin revertir los contadores FIFO (`consumed_quantity` y `compensated_quantity`) de las entradas cerradas afectadas.

### Cambio de precio

- `id` interno.
- `product_id` (referencia redundante al producto de la presentación, para consultar el historial por producto sin depender de la unión con `presentations`).
- `presentation_id` (presentación cuyo precio cambió).
- `previous_price` (TEXT `Decimal` > 0): precio vigente capturado al confirmar, no el mostrado al preparar la propuesta (RF-08).
- `new_price` (TEXT `Decimal` > 0).
- `occurred_at` instante automático en UTC.
- `event_type` identificador constante del tipo de cambio de precio (RF-08); el MVP no registra usuario porque no admite múltiples usuarios.
- Es un historial de **solo anexado**: no se edita ni se elimina, y **no** es un movimiento (no se incorpora ningún valor nuevo a `movements.kind`).
- **No** registra el precio de alta de la presentación: contiene únicamente los cambios posteriores confirmados.

## Representación de cantidades, stock, precio y costo

- Cantidades de inventario, stock (suelto y cerrado) y factor de presentación: **enteros** (`int`). SQLite usa columnas `INTEGER`.
- Precio de venta y costo: **`Decimal` exacto** en Python, almacenado como `TEXT` canónico en SQLite. La especificación no define precisión decimal para precio ni costo.
- Fechas y períodos: los instantes se persisten en **UTC**; las fechas de un período se interpretan en la zona horaria local de Argentina, cuyo identificador IANA es **`America/Argentina/Buenos_Aires`**, y se convierten a **UTC** antes de consultar los movimientos. La semántica del período es **`[inicio, fin)`** (inicio inclusivo, final exclusivo).
- No se usan `float` para cálculos monetarios ni se introduce una representación en centésimas para cantidades.

## Configuración de DeepSeek

- `config.py`: `DEEPSEEK_API_KEY` y `DEEPSEEK_MODEL` desde entorno.
- `services/llm.py`: `get_llm()` → `ChatDeepSeek(model, api_key, temperature=0)`.
- `.env.example` sin credenciales.

Decisiones pendientes documentadas: identificador del modelo DeepSeek.

## Persistencia, transacciones y concurrencia

- Confirmación en una única transacción: `BEGIN IMMEDIATE` → leer propuesta/producto/disponibilidad vigente → revalidar reglas (incluido el estado activo del producto) → aplicar cambios → `COMMIT`; ante fallo, `ROLLBACK` total.
- La **apertura** actualiza `closed_stock` y `loose_units` en la misma transacción y consume FIFO las `entrada_cerrada` correspondientes.
- Las `salida_cerrada` consumen entradas cerradas y actualizan `consumed_quantity` dentro de la misma transacción; las compensaciones de entradas cerradas actualizan `compensated_quantity` del movimiento original. Ninguna operación del MVP revierte el consumo FIFO ya aplicado, por lo que las compensaciones de salidas cerradas quedan fuera del MVP.
- Las operaciones concurrentes no pueden confirmarse sobre disponibilidad obsoleta.
- Sin stock negativo ni cambios parciales.
- **Nota de límite:** la exclusión de las compensaciones de `salida_cerrada` se aplica en el **dominio y el servicio**; la relación salida→entrada consumida por FIFO **no se persiste** y el esquema SQLite no la restringe, por lo que no se introduce ninguna restricción nueva en la base de datos.
- **Persistencia duradera (decidida; implementación pendiente):** el funcionamiento normal usa una base SQLite en **archivo**, con **ruta configurable** y un **valor por defecto claro y determinista**, **independiente del directorio de trabajo** desde el que se inicia Streamlit (la ruta por defecto no debe resolverse de forma relativa al directorio actual). `:memory:` queda limitado a las pruebas. `connect()` debe abrir la ruta configurada en lugar de `:memory:`, conservando `check_same_thread=False` y una conexión por sesión (T023). Hoy `connect()` sigue usando `:memory:` (T028).
- **Actualización de precio atómica (decidida):** la confirmación de una actualización de precio de venta (RF-08) se ejecuta en una **única transacción**: `BEGIN IMMEDIATE` → revalidar producto activo y presentación vigente → capturar el precio anterior vigente al confirmar → `UPDATE presentations.sale_price` → `INSERT` en `price_changes` → `COMMIT`; ante cualquier fallo, `ROLLBACK` total, sin precio actualizado ni cambio registrado. Corrige la limitación **M6** de `PresentationService.change_sale_price`, que hoy lee y escribe sin transacción propia (`tasks.md:569`). La **presentación base (factor 1) queda incluida** igual que las demás: el servicio no filtra por factor.
- **Aislamiento de las propuestas:** la confirmación aplica el cambio y registra el historial; la propuesta en sí permanece **sin persistir** (el servicio de propuestas es *stateless* y no usa la tabla `proposals`), de modo que las propuestas pendientes quedan separadas de los cambios de precio confirmados (`tasks.md:570`).
- **Lectura del historial (decidida):** el acceso de lectura se resuelve en el **repositorio** (métodos por presentación y por producto), en orden cronológico ascendente por `occurred_at` con desempate estable por `id`, y se expone mediante un **servicio de consulta reutilizable** que consume la **interfaz**. **No** se añade una herramienta nueva al agente: la decisión aprobada no lo justifica en el MVP (T034, T035). Es solo lectura: no crea movimientos ni modifica saldos.
- **Resultado de la confirmación (decidida):** se conserva el contrato actual de `ProposalOutcome` (`app/schemas/proposals.py:313`), que ya devuelve la presentación actualizada (`PresentationRecord`); **no** se amplía sin una necesidad concreta.

## Esquema SQLite

- `products`: `id`, `sku` (único), `name`, `base_unit`, `minimum_stock` (INTEGER), `loose_units` (INTEGER), `active`.
- `presentations`: `id`, `product_id`, `name`, `factor` (INTEGER), `sale_price` (TEXT Decimal), `closed_stock` (INTEGER).
- `movements`: `id`, `product_id`, `presentation_id`, `kind`, `quantity` (INTEGER), `reason`, `occurred_at`, `cost` (TEXT, opcional), `applied_price` (TEXT, opcional), `is_initial_stock`, `original_movement_id`, `consumed_quantity` (INTEGER), `compensated_quantity` (INTEGER).
- `proposals`: `id`, `action_type`, `data`, `status`, `created_at`.
- `price_changes`: `id`, `product_id`, `presentation_id`, `previous_price` (TEXT Decimal), `new_price` (TEXT Decimal), `occurred_at`, `event_type` (historial de RF-08; solo anexado).
- Índice `idx_price_changes_presentation_occurred_at` sobre `price_changes(presentation_id, occurred_at)` para la consulta cronológica del historial por presentación; la consulta por producto se resuelve por `product_id`.

Los nombres de tablas y columnas se expresan en inglés, conforme al código en inglés de la constitución (§4) y a los nombres del dominio (`Product`, `Presentation`, `ClosedEntry`). Los valores persistidos de `movements.kind` se mantienen en español (`entrada_cerrada`, `entrada_suelta`, `salida_cerrada`, `salida_suelta`, `apertura`, `compensacion`), coherentes con `MovementKind` del dominio.

Esquema para bases nuevas: `create_schema` sigue creando el esquema completo —ahora con la tabla del historial— cuando la base no existe; en ese caso no hay datos previos que migrar.

**Migración versionada (decidida; implementación pendiente).** El esquema se versiona con **`PRAGMA user_version`** y **versiones explícitas con migraciones controladas**: `create_schema` crea el esquema base de una base nueva y, sobre una base existente, aplica en orden las migraciones pendientes según la versión registrada, cada una dentro de una transacción. La tabla `price_changes` y su índice llegan como la migración `user_version = 1`.

Reglas de la migración:

- `create_schema` no debe fallar por tablas existentes: en una base ya creada se aplican únicamente las migraciones pendientes.
- La migración **no borra datos**, **no reinicializa el inventario** y **no** se considera resuelta por el hecho de crear una base vacía.
- La versión (`user_version`) se actualiza dentro de la misma transacción de cada migración, de modo que una interrupción no deje la versión adelantada respecto del esquema.
- Debe existir cobertura de la migración sobre una base creada con el esquema anterior **y con datos**, verificando que se conservan todas las filas.

Migrar es requisito para declarar RF-08 terminado (T028, T029).

## Estructura de directorios

```text
app/
  config.py
  services/  llm.py  ports.py  products.py  presentations.py  inventory.py  movements.py  proposals.py
  domain/    quantities.py  products.py  presentations.py  stock.py  movements.py  proposals.py
  infrastructure/sqlite/  connection.py  schema.py  repositories.py
  schemas/
  agents/  supervisor.py  inventory.py
  graph/  state.py  workflow.py
  prompts/  prompts.py
  tools/  inventory_tools.py  proposal_tools.py
  ui/  main.py  session.py  formatting.py  sections/
tests/  unit/  integration/  concurrency/  ai/  ui/
.env.example
.gitignore
requirements.txt
```

## Estrategia de pruebas

- Reglas de negocio: unitarias de dominio (producto, presentaciones, stock compuesto, apertura, total equivalente, compensaciones con FIFO, costo y precio).
- Persistencia SQLite: integración (unicidad, inmutabilidad, filtros por producto/kind/período; conversión de períodos locales de `America/Argentina/Buenos_Aires` a UTC con semántica `[inicio, fin)`).
- Herramientas: devuelven datos reales (stock suelto, cerrado y total) o crean propuestas (incluida apertura), sin escribir movimientos.
- Agentes y supervisor: modelo falso determinista, sin DeepSeek real.
- Confirmación y compensación: propuesta editable sin efectos; confirmación revalida; compensación que excede la cantidad compensable se rechaza; apertura no compensable; se rechaza la compensación de una salida cerrada sin modificar `closed_stock` ni los contadores FIFO.
- Productos desactivados: consultas e historial permitidos; se rechazan las operaciones ordinarias nuevas, las modificaciones de nombre o stock, la creación o modificación de presentaciones y las aperturas; se admiten las compensaciones históricas válidas.
- Concurrencia: aperturas, salidas y compensaciones simultáneas sobre la misma presentación sin desincronizar suelto/cerrado ni negativos.
- Errores de configuración/API: `get_llm()` sin key lanza error.
- Validación RF-01 a RF-07 con trazabilidad a pruebas.
- Actualización de precio y historial (RF-08): confirmación en una transacción con historial registrado; rechazo sin cambios parciales; rechazo sobre producto desactivado; precio inválido; el alta no registra historial; el historial no altera existencias ni `applied_price`; la presentación base incluida como cualquier otra; consulta cronológica por presentación y por producto a través del repositorio y del servicio de consulta (sin herramienta nueva del agente); persistencia verificada al cerrar y reabrir sobre la misma base en archivo con la ruta configurada; migración versionada (`PRAGMA user_version`) sobre una base existente con datos, sin pérdida de filas.
- Interfaz de gestión (RF-09): composición de lectura **sin** modelo frente a composición del asistente; un único punto de confirmación y ninguna escritura desde los formularios; secciones de consulta con datos reales y estados explícitos; revisión, edición y descarte **local** de propuestas; movimientos e historial de precios por servicio; presentación de fechas en hora local sobre almacenamiento UTC.

## Fases

- Fase 0: esqueleto, configuración y scaffolding del proveedor (sin credenciales).
- Fase 1: dominio puro + persistencia SQLite (sin LLM).
- Fase 2: servicios, confirmación, apertura, compensaciones y concurrencia (sin LLM).
- Fase 3: herramientas, agentes y grafo (requiere identificador de modelo y alcance de agentes).
- Fase 4: interfaz Streamlit con confirmación explícita.
- Fase 5: integración y validación RF-01 a RF-07.
- Fase 6: actualización del precio de venta e historial (RF-08): persistencia duradera en archivo, migración versionada con `PRAGMA user_version`, esquema, repositorio, servicio transaccional, propuesta y confirmación, consulta del historial por servicio e interfaz, y trazabilidad.
- Fase 7: interfaz de gestión del inventario (RF-09): navegación lateral, composición de lectura sin LLM, lectura pública de productos, secciones de consulta (inicio, productos, movimientos e historial de precios), formularios de propuestas, revisión/edición/descarte, confirmación centralizada y estados de interfaz.

## Decisiones pendientes

- Identificador del modelo DeepSeek (antes de Fase 3).
- Ciclo de vida de propuestas y topología de despliegue (deferibles).
- **RF-08 — contradicciones abiertas con el código y las pruebas actuales (a resolver en implementación):** `app/schemas/proposals.py:19-20` y `:50-51` excluyen explícitamente el cambio de precio; `tests/ai/test_proposal_tools.py:688-695` fija 10 herramientas y `:434` «nine actions»; `tests/integration/test_schema.py:194-205` fija 4 tablas. La especificación aprobada (`spec.md` RF-08) pasa a ser la fuente de verdad y esas pruebas deberán actualizarse al implementar.

## Decisiones resueltas (RF-08)

Aprobadas por el usuario. Se documentan aquí para conservar la trazabilidad. **Ninguna está implementada**: la implementación corresponde a la Fase 6 (T028–T037).

1. **Persistencia duradera:** base SQLite en **archivo** para el funcionamiento normal (no `:memory:`), con **ruta configurable** y **valor por defecto claro y determinista**, **independiente del directorio de trabajo** desde el que se inicia Streamlit. `:memory:` queda limitado a las pruebas.
2. **Migración:** versionado con **`PRAGMA user_version`**, con versiones explícitas y migraciones controladas.
3. **Presentación base:** incluida en RF-08 igual que las demás presentaciones (RF-08, `spec.md:261`); `PresentationService.change_sale_price` no filtra por factor.
4. **`ProposalOutcome`:** se conserva el contrato actual (`app/schemas/proposals.py:313`), que ya devuelve la presentación actualizada; **no** se amplía sin necesidad concreta.
5. **Lectura del historial:** consulta reutilizable vía **servicio/repositorio**, expuesta a la **UI**; **no** se añade herramienta nueva al agente, por falta de justificación en el MVP.
6. **Esquema y migración:** `create_schema` actualizado para bases nuevas (incluye la tabla del historial) **y** migración explícita para bases existentes; **sin** borrar datos, **sin** reinicializar el inventario y **sin** asumir que crear una base vacía resuelve la migración.

## Decisiones resueltas (RF-09)

Aprobadas por el usuario para la formalización de RF-09. Se identifican como **D-A** a **D-G** y la implementación corresponde a la **Fase 7** (T038–T057), **en curso**: T038–T047 están cerradas y aprobadas; T048 está cerrada y aprobada (por el usuario; revisión independiente APROBADO), commit `8562280`; T049 está implementada y revisada (revisión independiente APROBADO; pruebas reportadas por el implementer —168 en tests/ui/test_main.py y 194 en tests/ui—, no reejecutadas por el reviewer; commit `c7c03f0`; cierre documental registrado); T050 está implementada y revisada (revisión independiente APROBADO; pruebas reportadas por el implementer —187 en tests/ui/test_main.py, 213 en tests/ui, 6 en integración de UI/end-to-end y 1212 en la suite—, no reejecutadas por el reviewer; commit `c7c03f0`; cierre documental registrado); el alcance se acreditó con las salidas Git reportadas; T051 está implementada y revisada (revisión independiente APROBADO, sin hallazgos bloqueantes; pruebas reportadas por el implementer —199 en tests/ui/test_main.py, 225 en tests/ui y 1224 en la suite—, no reejecutadas por el reviewer; commit `c7c03f0`; cierre documental registrado); T054 está implementada y aprobada (revisión independiente APROBADO; pruebas reportadas por el implementer —219 en tests/ui/test_main.py, 245 en tests/ui y 1244 en la suite—, no reejecutadas por el reviewer; sin commit; cierre documental registrado); T055 está implementada y aprobada (revisión independiente APROBADO; corrección B1 aprobada; pruebas reportadas por el implementer —4 focalizadas, 226 en tests/ui/test_main.py y 252 en tests/ui—, no reejecutadas por el reviewer; sin commit; cierre documental registrado); T056 está cerrada y aprobada (revisión independiente APROBADO) con la matriz RF-09 C1–C22 registrada; T057 está cerrada y aprobada (revisión independiente APROBADO), commit de implementación `cb78d50`, con validación visual combinada completa y limitaciones aceptadas; sin commit adicional. Las observaciones opcionales de T049 quedan para T054 o mejoras futuras, sin abrir una nueva ronda de correcciones. Las observaciones de T050 para T053 (confirmación de propuestas manuales y texto informativo de la sección) quedan registradas, sin abrir una nueva ronda de correcciones. No queda pendiente la implementación de T048–T055 ni T057.

- **D-A — Ubicación de la confirmación:** el único punto de confirmación permanece en `app/ui/main.py`; las secciones no confirman por su cuenta y la comprobación estructural sobre el texto fuente (una sola aparición de `.confirm(`) se conserva.
- **D-B — Contenedor de sesión:** la composición de **lectura** sin LLM y los servicios compartidos se concentran en `app/ui/session.py`; **no** se amplía `Assistant` con responsabilidades no conversacionales.
- **D-C — Revisión legible:** la propuesta se presenta por acción y de forma legible; **no** se expone al usuario el volcado genérico del payload.
- **D-D — Compensabilidad:** la interfaz **no** recalcula la cantidad compensable pendiente; cuando la muestre, la obtendrá de una lectura pública que reutilice las reglas de compensación vigentes.
- **D-E — Estados:** los estados de la interfaz (carga, vacío, validación, rechazo y confirmación) son **transversales** a las secciones, no una sección aparte con lógica propia.
- **D-F — Ubicación documental:** RF-09 se documenta **después** de RF-08 en `spec.md` y la Fase 7 usa identificadores desde **T038**; no se reabren T001–T037.
- **D-G — Conexión y servicios:** una conexión y un conjunto de servicios **por sesión**, compartidos por las secciones; la interfaz no abre conexiones ni transacciones propias.

## Cierre T049–T051
T049, T050 y T051 están implementadas, revisadas y cerradas documentalmente; commit de código `c7c03f0`. Esta actualización prevalece sobre referencias anteriores de este documento que indiquen que T049–T051 seguían pendientes. T056 está cerrada y aprobada (revisión independiente APROBADO) con la matriz RF-09 C1–C22 registrada.

## Cierre T052
T052 está implementada y aprobada (revisión independiente APROBADO); sin commit asignado. El descarte es local a `st.session_state`; una propuesta descartada no se ofrece ni puede confirmarse desde «Asistente». No se modificaron dominio, `ProposalStatus`, grafo ni persistencia. Pruebas reportadas: 206 en `tests/ui/test_main.py` y 232 en `tests/ui`.

## Cierre T053
T053 está implementada y aprobada (revisión independiente APROBADO); sin commit asignado. La confirmación se centraliza en `_apply_confirmation`, con una única llamada a `proposal_service.confirm`; `confirm_pending` y `confirm_proposal` delegan en ese punto. La protección contra reenvío es local a la sesión de la interfaz; no existe idempotencia durable. Pruebas reportadas: 212 en `tests/ui/test_main.py` y 238 en `tests/ui`.

## Cierre T054
T054 está implementada y aprobada (revisión independiente APROBADO); sin commit asignado. `MovementService.get_compensation_availability` es una lectura pública de solo lectura que reutiliza `_compensation_availability`; no duplica la regla ni usa `register_compensation`. La UI no recalcula la cantidad compensable: `main.py` coordina y `operations.py` solo presenta el valor; si la lectura no está disponible, no se afirma compensabilidad. La consulta no escribe ni altera el inventario. Pruebas reportadas: 219 en `tests/ui/test_main.py`, 245 en `tests/ui` y 1244 en la suite completa. Limitación registrada: T054 menciona una salida cerrada, pero el MVP excluye compensar salidas cerradas; se usaron entradas cerradas con y sin cantidad pendiente, sin modificar las reglas del dominio.

## Cierre T055
T055 está implementada y aprobada (revisión independiente APROBADO); sin commit asignado. Los estados de carga, vacío, validación, rechazo y confirmación son explícitos y transversales: `loading_state`/`st.spinner`, `st.info`, `render_validation`/`st.warning`, `st.error` con el motivo real y `st.success`. Las fechas se presentan en hora local con `format_instant`; la corrección B1 formatea los `datetime` de `describe_result` en el turno del asistente y en el aviso de «Propuestas pendientes», conservando el almacenamiento UTC y sin cambios de negocio. Pruebas reportadas: 4 focalizadas de B1 (222 deselected), 226 en `tests/ui/test_main.py` y 252 en `tests/ui`. T057 quedó cerrada y aprobada con validación visual combinada y limitaciones aceptadas (contraste del canvas y scroll interno de tablas).

## T058 — Costos iniciales (implementada)

T058 está implementada y aprobada (revisión independiente APROBADO); sin commit. El alcance incluye app/tools/proposal_tools.py para conservar la integración de la herramienta de alta con LangChain. La implementación de T058 extendió el alta de producto para aceptar costos totales opcionales por grupo inicial y asociarlos a los movimientos iniciales existentes. No requiere cambios de esquema: `movements.cost` ya admite entradas y el costo se serializa como Decimal. El costo unitario de referencia es un valor derivado de presentación, no persistido. La referencia se muestra en la vista previa de la propuesta, no en una vista nueva. Se calcula con `Decimal` y se presenta con `ROUND_HALF_UP` a dos decimales; el redondeo es solo de presentación y no altera el costo total persistido ni los cálculos internos. La apertura existente no transferirá costos ni introducirá valoración de inventario; el costo permanece en el movimiento inicial original. Los precios de venta no se modifican.

## T063 — Contexto conversacional (implementada)

**Estado: cerrada y aprobada** (revisión independiente APROBADO); sin commit; cierre documental registrado. La implementación conserva el contexto reciente en la sesión, limita el envío a 12 mensajes totales, no duplica el mensaje actual y no persiste el historial.

- **Estado del grafo:** `GraphState` incorpora `conversation` reciente como tupla de mensajes simples (rol y texto), ordenada de más antiguo a más reciente, sin importar LangChain en `app/graph/state.py`; `create_initial_state` la recibe de forma opcional y la conserva tal cual.
- **Ventana:** `answer()` reconstruye una ventana limitada (12 mensajes / 6 turnos, constante documentada) desde `Assistant.history`, que sigue viviendo en `st.session_state` sin serializarse ni persistirse.
- **Envío al modelo:** el nodo del agente traduce esa tupla a `HumanMessage`/`AIMessage` y envía la conversación reciente más el mensaje actual una sola vez, sin duplicar el mensaje actual.
- **Sin extensiones:** no se implementan resúmenes automáticos, RAG, memoria persistente ni nuevas abstracciones; tampoco cambian el esquema, la búsqueda parcial (T062) ni la confirmación de propuestas (T053, T054).
- **Evidencia:** pruebas focalizadas de T063 reejecutadas el 2026-10-26: **5 passed**; suite completa reportada en el cierre: **1343 passed**. Limitación vigente: falta la validación manual con DeepSeek real; no bloquea el cierre definido de T063.

## T064 — Tema visual azul profesional (cerrada)

**Estado: cerrada y aprobada** (validación visual manual confirmada por el usuario; suite completa reportada 1344 passed el 2026-10-26); sin commit; cierre documental registrado. Sustituye únicamente la convención visual clara/neutra de T057 por un tema oscuro azul. No reabre T057, no altera `spec.md`, no cambia comportamiento, textos, navegación, layouts ni `app/**` y no modifica los criterios funcionales C1–C22 de RF-09.

- **Versión y compatibilidad verificadas (2026-10-24):** Streamlit **1.65.0**, según los metadatos de la distribución instalada (`.venv/Lib/site-packages/streamlit-1.65.0.dist-info/METADATA`). Se comprobaron en `streamlit/config.py` las opciones `base`, `primaryColor`, `backgroundColor`, `secondaryBackgroundColor`, `textColor`, `linkColor`, `borderColor`, `codeBackgroundColor`, `codeTextColor`, `font`, `baseRadius`, `buttonRadius`, `dataframeBorderColor`, `dataframeHeaderBackgroundColor`, `dataframeHeaderTextColor`, `showWidgetBorder`, `showSidebarBorder` y la sección anidada `[theme.sidebar]`. Todas las opciones de la paleta son compatibles; no se actualizan dependencias.
- **Único cambio de producto:** `.streamlit/config.toml`. El tema se define solo con opciones nativas: sin CSS personalizado, sin `unsafe_allow_html` y sin `st.html`.
- **Paleta:** `base = "dark"`; `primaryColor #0F766E`; `backgroundColor #061826`; `secondaryBackgroundColor #0D2B45`; `textColor #E8F4FF`; `linkColor #5EEAD4`; `borderColor #1E4E6B`; `codeBackgroundColor #08243A`; `codeTextColor #D8F3F0`; `dataframeBorderColor #1E4E6B`; `dataframeHeaderBackgroundColor #0B3A52`; `dataframeHeaderTextColor #E8F4FF`; `baseRadius = "medium"`; `buttonRadius = "medium"`; `showWidgetBorder = true`; `showSidebarBorder = true`.
- **Sección `[theme.sidebar]`:** `backgroundColor #04121F`, `secondaryBackgroundColor #0A2236`, `textColor #E8F4FF`, `borderColor #1E4E6B` y `primaryColor #0F766E`, para separar el panel lateral del contenido sin cambiar la navegación.
- **Sin cambios funcionales:** no se tocan `app/**` (incluida la lógica de secciones), los textos y etiquetas, la navegación, los layouts, el esquema, la persistencia ni las dependencias. No se añaden herramientas ni puntos de confirmación.
- **Pruebas:** actualizar `test_theme_config_uses_the_approved_palette`; añadir `test_theme_config_defines_the_sidebar_and_dataframe_palette`; conservar `test_the_ui_does_not_use_custom_css` en `tests/ui/test_main.py`. Las pruebas están actualizadas y cubiertas por la suite; la validación manual fue confirmada por el usuario.
- **Validación visual manual completada:** el usuario confirmó que la interfaz se ve bien y que la validación visual no encontró problemas, sin registrar una revisión individual por pantalla. `docs/ui-guidelines.md` refleja la convención vigente.
- **Trazabilidad:** T064 se documenta como Fase 12 en `tasks.md`; no añade criterios a la matriz RF-09 y no reabre tareas cerradas.
- **Evidencia de validación:** el usuario confirmó que la interfaz se ve bien y que la validación visual manual no encontró problemas, sin registrar una revisión individual por pantalla; suite completa reportada el 2026-10-26: **1344 passed**. T064 queda cerrada y aprobada; sin commit; cierre documental registrado.
