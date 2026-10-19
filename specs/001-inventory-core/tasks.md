# Tareas: núcleo de inventario

## Resumen del alcance

Este documento descompone el plan técnico aprobado (`plan.md`) en tareas pequeñas, ordenadas por dependencias y verificables, para el MVP de inventario definido por `spec.md`.

- Los identificadores (`T001`–`T056`) son únicos y estables.
- Cada tarea pertenece a una fase, declara dependencias, archivos previstos, pruebas y criterios de aceptación verificables.
- Se priorizan las reglas deterministas del dominio y la persistencia antes de integrar el LLM.
- Las decisiones pendientes que bloquean una tarea se indican explícitamente y no se resuelven aquí.
- **Ampliación RF-08 (T027–T037):** la actualización del precio de venta y su historial, incorporada a `spec.md` como RF-08 (criterios C1–C13) y reflejada en `plan.md`, se descompone en la **Fase 6**. **T028–T037 están cerradas y aprobadas** (T037 con revisión independiente APROBADO; la verificación de `pytest` sigue siendo reportada, no independiente). RF-08 **no** se declara terminado sin demostrar **persistencia tras reiniciar** la aplicación y **atomicidad** entre la actualización del precio y el registro del historial.
- **Ampliación RF-09 (T038–T056):** la interfaz de gestión del inventario (navegación lateral, consultas **sin** modelo, formularios de propuestas, revisión/edición/descarte y estados de interfaz), incorporada a `spec.md` como **RF-09** (criterios C1–C22) y reflejada en `plan.md` (composición dual y Fase 7), se descompone en la **Fase 7**. La **formalización documental (T038)** está **cerrada y aprobada** (tras revisión independiente) y **no** implica implementación; **T039 está cerrada y aprobada** (tras **segunda revisión independiente**), **T040 está cerrada y aprobada** (tras revisión independiente), **T041 está cerrada y aprobada** (tras revisión independiente), **T042 está cerrada y aprobada** (tras revisión independiente), **T043 está cerrada y aprobada** (tras revisión independiente), **T044 está cerrada y aprobada** (tras revisión independiente), **T045 está cerrada y aprobada** (tras revisión independiente), **T046 está cerrada y aprobada** (tras revisión independiente) y **T047 está cerrada y aprobada** (tras revisión independiente): **T048–T056 permanecen pendientes** y la Fase 7 continúa en **T048**. RF-09 **no** modifica RF-01–RF-08; la desactivación de productos y el cambio de factor de presentación quedan **fuera**.
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
- **Estado:** **Cerrada y aprobada** (tras revalidación y revisión independiente). Historial: la aprobación anterior no cubría la exclusión de `salida_cerrada` como movimiento original compensable, decidida para el MVP, por lo que la tarea se reabrió; tras revalidarse que el dominio rechaza la compensación de una salida cerrada, la tarea se revalida y se cierra. Implementada en `app/domain/movements.py` y probada en `tests/unit/test_movement_rules.py`: `validate_compensable_kind()` rechaza `CLOSED_EXIT` como movimiento original compensable reutilizando `NotCompensableError` (mensaje `Las compensaciones de salidas cerradas quedan fuera del MVP.`); se eliminó la clave `_OPPOSITE_KINDS[MovementKind.CLOSED_EXIT]` y se conservan las compensaciones permitidas de `CLOSED_ENTRY`, `LOOSE_ENTRY` y `LOOSE_EXIT`, mientras que `APERTURE` y `COMPENSATION` siguen rechazándose.
- **Objetivo:** implementar las reglas de RF-03 (tipos, compensaciones y consumo FIFO).
- **Archivos previstos:** `app/domain/movements.py`.
- **Dependencias:** T005.
- **Pruebas:** `tests/unit/test_movement_rules.py`.
- **Pruebas reportadas (no verificadas por ejecución):** el implementer reportó 90 pruebas unitarias en `tests/unit/test_movement_rules.py` y 676 en la suite completa. Ni el coordinador ni el reviewer ejecutaron las pruebas de forma independiente; por lo tanto, estos resultados no están verificados. `git diff --check` sin errores; `ruff` no se pudo ejecutar por permisos del shell.
- **Revisión independiente: APROBADO.** Sin bloqueantes ni hallazgos importantes; los hallazgos menores quedan como mejoras opcionales sin abrir una nueva ronda de correcciones (ver «Decisiones pendientes»).
- **Criterios de aceptación:**
  - Una compensación es un movimiento de tipo compensación cuyo efecto sobre el stock es opuesto al del movimiento original, y no puede encadenarse.
  - Las aperturas y las salidas cerradas no son compensables.
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
- **Estado:** **Cerrada y aprobada** (tras revisión independiente). Implementada en `app/services/movements.py`: entradas y salidas sueltas y cerradas, con validación del motivo, del costo (solo en entradas) y del precio histórico (`applied_price`); transacciones atómicas; consumo FIFO mediante `consume_closed_entries_fifo` y `set_consumed_quantity`.
- **Objetivo:** registrar entradas y salidas cerradas y sueltas con confirmación transaccional.
- **Archivos previstos:** `app/services/movements.py`.
- **Dependencias:** T005, T009.
- **Nota (I1):** el motivo (`reason`) se normaliza con `normalize_required_text()`, que rechaza valores vacíos, solo espacios, tabs y saltos de línea antes de persistir (hallazgo I1 de T009 resuelto).
- **Nota (I2):** puede reutilizar el consumo FIFO del dominio (`consume_closed_entries_fifo`) y la actualización condicional `MovementRepository.set_consumed_quantity(...)` para descontar `consumed_quantity` de las entradas cerradas afectadas por salidas cerradas, sin duplicar la lógica.
- **Nota (decisión confirmada):** las operaciones cerradas (`entrada_cerrada` y `salida_cerrada`) se rechazan sobre presentaciones base de factor `1`; la presentación base solo admite operaciones sueltas. La regla es coherente con la prohibición de apertura de la presentación base (T005/T013).
- **Pruebas:** `tests/integration/test_movement_service.py` (incluye costo y snapshot de precio).
- **Pruebas reportadas (no verificadas por ejecución):** el implementer reportó 91 pruebas específicas y 643 en la suite completa. El reviewer no ejecutó `pytest` de forma independiente; por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** Sin bloqueantes ni hallazgos importantes; los hallazgos menores quedan como mejoras opcionales sin abrir una nueva ronda de correcciones (ver «Decisiones pendientes»).
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
- **Estado:** **Cerrada y aprobada** (tras revisión independiente). Implementada en `app/services/movements.py` mediante `MovementService.register_aperture()`, con pruebas en `tests/integration/test_aperture_service.py`. Reutiliza `open_presentations()` del dominio (valida cantidad, factor y stock; la presentación base, factor `1`, se rechaza con `BasePresentationApertureError` del dominio), `consume_closed_entries_fifo()` y `set_consumed_quantity()`, resolviendo la presentación por existencia y pertenencia, sin duplicar reglas. Atomicidad: el movimiento de apertura, `closed_stock`, `loose_units` y los contadores FIFO se actualizan en una única transacción. Histórico: en su cierre original esta tarea no usaba `BEGIN IMMEDIATE` (su ausencia correspondía a T016); T016 incorporó la transacción compartida con `BEGIN IMMEDIATE` (ver T016).
- **Objetivo:** registrar aperturas atómicas y consumir FIFO.
- **Archivos previstos:** `app/services/movements.py` (o módulo de apertura).
- **Dependencias:** T012.
- **Nota (I2):** reutiliza el consumo FIFO del dominio (`consume_closed_entries_fifo`) y la actualización condicional `MovementRepository.set_consumed_quantity(...)` para actualizar los contadores de consumo de las entradas cerradas afectadas, sin duplicar la lógica FIFO.
- **Nota (decisión confirmada):** el rechazo de las operaciones cerradas (`entrada_cerrada` y `salida_cerrada`) sobre la presentación base de factor `1` (ver T012) es coherente con el rechazo de la apertura de la presentación base que ya define esta tarea; ambas reglas provienen del dominio y no se reimplementan.
- **Pruebas:** `tests/integration/test_aperture_service.py`.
- **Pruebas reportadas (no verificadas por ejecución):** el implementador reportó 30 pruebas específicas, 91 de regresión de T012 y 673 en la suite completa. El código de salida de `pytest` no fue observable y el reviewer no ejecutó las pruebas de forma independiente; por lo tanto, estos resultados no están verificados. `git diff --check` sin errores.
- **Revisión independiente: APROBADO.** Sin bloqueantes ni hallazgos importantes; los hallazgos menores M1–M6 quedan como mejoras opcionales sin abrir una nueva ronda de correcciones (ver «Decisiones pendientes»).
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
- **Estado:** **Cerrada y aprobada** (tras revisión independiente, con un ciclo único de corrección de I1). Implementada en `app/services/movements.py` mediante `register_compensation()`, con la operación de persistencia `set_compensated_quantity(...)` en `app/services/ports.py` y `app/infrastructure/sqlite/repositories.py`. Reutiliza `validate_compensable_kind()` y `compensate()` del dominio (`app/domain/movements.py`) sin duplicar las reglas: la cantidad pendiente se calcula por tipo de movimiento (entrada cerrada con cantidad compensable FIFO + `closed_stock`; entradas y salidas sueltas con la pendiente vinculada por `original_movement_id`) y el contador `compensated_quantity` se actualiza mediante `set_compensated_quantity`. Archivos de la tarea: `app/services/movements.py` (`register_compensation`), `app/services/ports.py`, `app/infrastructure/sqlite/repositories.py` (`set_compensated_quantity`), `tests/integration/test_compensation_service.py` y `tests/integration/test_repositories.py`.
- **Objetivo:** registrar compensaciones parciales o totales con límite compensable.
- **Archivos previstos:** `app/services/movements.py` (o módulo de compensaciones).
- **Dependencias:** T006, T012.
- **Nota (I2, resuelta por T014):** `compensated_quantity` ya cuenta con una operación segura y condicional análoga a `set_consumed_quantity`: T014 incorporó `set_compensated_quantity(...)` en `app/services/ports.py`, con implementación SQLite en `app/infrastructure/sqlite/repositories.py`, y el servicio actualiza de forma consistente los contadores de compensación. Histórico de la corrección I2: antes de T014 la operación no existía y su ausencia se registró como dependencia de T014; T014 la resolvió (ver «Decisiones pendientes»), y el servicio rechaza además las compensaciones de `salida_cerrada`.
- **Nota (decisión confirmada, validada por T014):** las compensaciones de salidas cerradas quedan fuera del MVP. Compensar una `salida_cerrada` repondría `closed_stock` sin revertir los contadores FIFO (`consumed_quantity` y `compensated_quantity`) de las entradas cerradas consumidas, y esa reversión exacta no es posible porque no se persiste la relación salida→entrada. T014 rechaza estas compensaciones reutilizando `NotCompensableError` (la relación salida→entrada no se persiste), sin modificar movimientos, `closed_stock` ni los contadores FIFO.
- **Pruebas:** `tests/integration/test_compensation_service.py` (incluye el rechazo de una compensación de `salida_cerrada`, verificando que `closed_stock` y los contadores FIFO de las entradas cerradas no cambian).
- **Pruebas reportadas (no verificadas por ejecución):** el implementer reportó 31 pruebas específicas y 714 en la suite completa. El coordinador y el reviewer no ejecutaron `pytest` de forma independiente; además, el reviewer no pudo verificar el alcance mediante Git (no dispone de shell). Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** La primera revisión emitió `CAMBIOS NECESARIOS` con un único hallazgo importante (I1), resuelto en un ciclo único de corrección mediante la prueba discriminante `test_a_loose_entry_pending_quantity_only_counts_its_own_compensations` en `tests/integration/test_compensation_service.py`, que demuestra que la cantidad pendiente de un movimiento suelto se calcula solo con las compensaciones vinculadas por `original_movement_id` y falla si se elimina el filtro. La segunda revisión independiente aprobó sin bloqueantes ni hallazgos importantes; m1–m7 y O1–O3 quedan como mejoras opcionales o deuda técnica (ver «Decisiones pendientes»).
- **Criterios de aceptación:**
  - Una compensación válida crea un movimiento vinculado al movimiento original.
  - Se rechazan las compensaciones que exceden la cantidad pendiente, cuyo efecto no es opuesto al del movimiento original o intentan encadenarse.
  - Las aperturas no son compensables.
  - Se rechaza la compensación de una salida cerrada (`salida_cerrada`) sin modificar movimientos, `closed_stock` ni los contadores FIFO (`consumed_quantity`, `compensated_quantity`) de las entradas cerradas.
  - Las compensaciones de entradas cerradas respetan la cantidad compensable y el stock cerrado vigente.
  - Los contadores de cantidades compensadas se actualizan de manera consistente.
  - Los productos desactivados solo admiten compensaciones históricas válidas permitidas por la especificación.
  - Las entradas sueltas conservan los límites de compensación establecidos, sin atribuirles una trazabilidad por lote que el MVP no implementa.
  - La operación se ejecuta de forma atómica.
- **RF:** RF-03.

### T015 — Servicio de propuestas

- **Fase:** 2.
- **Estado:** **Cerrada y aprobada** (tras revisión independiente). Implementada mediante contratos Pydantic en `app/schemas/proposals.py` (`ProposalAction` con las nueve acciones, `ProposalStatus`, payloads discriminados por acción, `Proposal`, `ConfirmationResult`) y un servicio de propuestas en `app/services/proposals.py` **sin estado interno** (recibe y devuelve la propuesta por valor, sin singleton ni registro); la confirmación explícita despacha a los servicios destino y solo marca `CONFIRMED` tras el éxito. Incluye la corrección de atomicidad de `ProductService.update` (nombre y stock mínimo en una única transacción). Sin persistencia de propuestas: no se usa la tabla `proposals`. Archivos creados: `app/schemas/__init__.py` (vacío), `app/schemas/proposals.py`, `app/services/proposals.py`, `tests/integration/test_proposal_service.py`; modificados: `app/services/products.py`, `tests/integration/test_product_service.py`.
- **Objetivo:** crear, editar y confirmar propuestas con revalidación transaccional.
- **Archivos previstos:** `app/services/proposals.py`.
- **Dependencias:** T009, T011, T012, T013, T014.
- **Nota (decisión heredada de T014):** la confirmación revalida la cantidad compensable (ver criterios). Las compensaciones de salidas cerradas quedan fuera del MVP (ver nota en T014), por lo que la confirmación debe rechazarlas sin modificar `closed_stock` ni los contadores FIFO.
- **Pruebas:** `tests/integration/test_proposal_service.py`.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** se reportaron 42 pruebas en `tests/integration/test_proposal_service.py`, 29 en `tests/integration/test_product_service.py` y 757 en la suite completa; `git diff --check` sin errores de espacios (solo avisos LF→CRLF). Se ejecutaron en una sesión de verificación delegada: ni el coordinador ni el reviewer las ejecutaron, y el reviewer no pudo verificar el alcance mediante Git (no dispone de shell). Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** Sin bloqueantes ni hallazgos importantes; los hallazgos menores quedan como mejoras opcionales o deuda técnica sin abrir una nueva ronda de correcciones (ver «Decisiones pendientes»).
- **Limitación stateless:** el servicio de propuestas no mantiene estado interno ni registra o persiste las propuestas (no se usa la tabla `proposals`), por lo que reenviar una propuesta `PENDING` obsoleta no se detecta. La protección de concurrencia de las confirmaciones simultáneas ya está resuelta: **T016 está cerrada y aprobada** e implementó la protección mediante el helper transaccional con `BEGIN IMMEDIATE` (`app/infrastructure/sqlite/connection.py`) y el índice único parcial `idx_presentations_single_base` (`app/infrastructure/sqlite/schema.py`); esta limitación *stateless* de las propuestas es independiente de esa protección y **no queda resuelta** por ella (ver «Decisiones pendientes»).
- **Criterios de aceptación:**
  - Una propuesta pendiente no modifica el inventario ni crea movimientos confirmados.
  - Editar una propuesta no modifica los productos, las presentaciones ni los saldos existentes.
  - La confirmación revalida, dentro de la transacción, el stock vigente, las reglas del producto y la cantidad compensable.
  - Si una regla falla, se rechaza la confirmación sin cambios parciales.
  - La confirmación se ejecuta por un servicio de aplicación invocado explícitamente, sin delegar la decisión final al LLM.
- **RF:** RF-05.

### T016 — Concurrencia e integridad

- **Fase:** 2.
- **Estado:** **Cerrada y aprobada** (tras revisión independiente; la primera revisión emitió `CAMBIOS NECESARIOS` por I1 y hallazgos menores, resueltos en un ciclo único). Alcance implementado (D1–D5): (D1) presentación base única protegida por el servicio (`PresentationService.add`/`change_factor` dentro de la transacción) y por el índice único parcial `idx_presentations_single_base` (`app/infrastructure/sqlite/schema.py`), con traducción del conflicto a `PresentationError` (`repositories.py`); (D2) transacciones compartidas con `BEGIN IMMEDIATE` en el helper (`connection.py`), sin transacciones propias en `ProposalService` y sin anidamiento; (D3) `timeout` configurable (5 s por defecto) en `connect()`, sin reintentos ni WAL; (D4) prechequeo de SKU dentro de la transacción de `ProductService.register`, conservando el `UNIQUE`; (D5) `ProductService.update` atómico bajo la transacción compartida (lectura + validaciones + escrituras). Archivos de la tarea: `app/infrastructure/sqlite/{connection,schema,repositories}.py`, `app/services/{products,presentations}.py`, `tests/concurrency/__init__.py`, `tests/concurrency/test_concurrency.py`; ajustes de compatibilidad en `tests/integration/{test_presentation_service,test_proposal_service,test_movement_service,test_schema}.py`.
- **Objetivo:** proteger las confirmaciones frente a operaciones simultáneas.
- **Archivos previstos:** transacciones `BEGIN IMMEDIATE` en los servicios de confirmación.
- **Dependencias:** T012, T013, T014, T015.
- **Pruebas:** `tests/concurrency/test_concurrency.py` con conexiones SQLite independientes.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** se reportaron 13 pruebas en `tests/concurrency/test_concurrency.py`, 72 en `tests/integration/test_schema.py` y 772 en la suite completa; `git diff --check` sin errores de espacios (avisos LF→CRLF). Se ejecutaron en una sesión de verificación delegada: ni el coordinador ni el reviewer las ejecutaron, y no se verificaron de forma independiente mediante shell/Git. Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** La segunda revisión aprobó sin bloqueantes ni hallazgos importantes; la corrección M3 se verificó por razonamiento estático (no hubo mutación controlada). La deuda técnica/menores y las limitaciones M6/M7 quedan registradas en «Decisiones pendientes», sin abrir una nueva ronda de correcciones.
- **Limitaciones M6/M7:** `PresentationService.change_sale_price` y `ProductService.deactivate` conservan ventanas de carrera porque leen y escriben sin transacción propia (fuera del alcance aprobado de T016); el límite de compensación de **movimientos sueltos** depende solo de la serialización de escritores (sin contador por movimiento ni restricción específica en la base).
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
- **Estado:** **Cerrada y aprobada** (tras revisión independiente). Implementada en `app/prompts/prompts.py`: dos constantes centralizadas en español (`SUPERVISOR_PROMPT` e `INVENTORY_AGENT_PROMPT`) que describen las responsabilidades del **Supervisor** (clasifica y deriva) y del **Inventory Agent** (consulta y prepara propuestas), con uso exclusivo de **datos reales** (sin inventar), **prohibición de escribir directamente en SQLite** y **confirmación explícita del usuario** fuera de la decisión autónoma del LLM. `app/prompts/prompts.py` es un **módulo hoja** (sin imports del proyecto, sin configuración ni red; sin clases ni plantillas dinámicas) y no tiene conexión con agentes, grafo, herramientas ni LLM. Archivos creados: `app/prompts/__init__.py` (vacío), `app/prompts/prompts.py`, `tests/ai/__init__.py` (vacío), `tests/ai/test_prompts.py`.
- **Objetivo:** definir los prompts del Supervisor y del Inventory Agent.
- **Archivos previstos:** `app/prompts/prompts.py`.
- **Dependencias:** T001.
- **Pruebas:** `tests/ai/test_prompts.py`.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó 14 pruebas en `tests/ai/test_prompts.py` y 786 en la suite completa; `git diff --check` sin errores de espacios (avisos LF→CRLF). Ni el coordinador ni el reviewer las ejecutaron, y no se verificaron de forma independiente mediante shell/Git. Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** Sin bloqueantes ni hallazgos importantes; los hallazgos menores M1–M6 quedan como mejoras opcionales o deuda técnica menor sin abrir una nueva ronda de correcciones (ver «Decisiones pendientes»).
- **Criterios de aceptación:** existen constantes para los prompts del Supervisor y del Inventory Agent; cada prompt describe su responsabilidad, indica responder solo con datos reales y prohíbe explícitamente la escritura directa en la base de datos; las pruebas verifican que dichas prohibiciones y responsabilidades están presentes en el texto.

### T018 — Estado del grafo

- **Fase:** 3.
- **Estado:** **Cerrada y aprobada** (tras revisión independiente). Alcance implementado: `GraphState` (`TypedDict`) con **exactamente** `user_message: str`, `pending_proposal: Proposal | None` y `execution_result: ConfirmationResult | None`, más `create_initial_state(user_message)`, que deja ambos campos opcionales en `None`. Reutiliza los contratos de `app.schemas.proposals` (sin tipos duplicados). El estado es **pasivo**: no valida reglas de negocio, no confirma, no escribe en SQLite, no invoca al LLM y no conecta agentes, herramientas ni grafo. Archivos creados: `app/graph/__init__.py` (vacío), `app/graph/state.py`, `tests/ai/test_state.py`.
- **Objetivo:** definir el estado compartido.
- **Archivos previstos:** `app/graph/state.py`.
- **Dependencias:** T001.
- **Pruebas:** `tests/ai/test_state.py`.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó 6 pruebas en `tests/ai/test_state.py` y 792 en la suite completa; `git diff --check` sin errores de espacios. Ni el coordinador ni el reviewer las ejecutaron, y no se verificaron de forma independiente mediante shell/Git. Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** Sin bloqueantes ni hallazgos importantes; los hallazgos menores M1–M3 y O1 quedan como deuda técnica menor/opcional sin abrir una nueva ronda de correcciones (ver «Decisiones pendientes»).
- **Nota (propagación pendiente):** el estado es pasivo y no conecta agentes, herramientas ni grafo; **la propagación real entre nodos queda pendiente hasta que exista el workflow (T022)**.
- **Criterios de aceptación:** el estado define y transporta el mensaje del usuario, la propuesta pendiente (si existe) y el resultado de la ejecución; las pruebas verifican la construcción del estado inicial y la propagación de cada campo entre nodos, sin que se introduzcan datos inventados.

### T019 — Herramientas de consulta

- **Fase:** 3.
- **Estado:** **Cerrada y aprobada (por el usuario; tras revisión independiente).** Implementada como servicio de consultas de **solo lectura** en `app/services/inventory.py` (dependencias inyectadas, contratos de `app/services/ports.py`, reglas derivadas delegadas en `app/domain/stock.py`) y 7 herramientas LangChain `@tool` en `app/tools/inventory_tools.py` (entradas Pydantic y salidas estructuradas explícitas), sin acceso directo a SQLite ni a repositorios de infraestructura. Archivos creados: `app/services/inventory.py`, `app/tools/__init__.py` (vacío), `app/tools/inventory_tools.py`, `tests/ai/test_inventory_tools.py`.
- **Objetivo:** exponer consultas de solo lectura.
- **Archivos previstos:** `app/services/inventory.py`, `app/tools/inventory_tools.py`.
- **Dependencias:** T009, T011, T012, T013.
- **Pruebas:** `tests/ai/test_inventory_tools.py`.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó 30 pruebas en `tests/ai/test_inventory_tools.py` y 822 en la suite completa; `git diff --check` sin errores de espacios. Ni el coordinador ni el reviewer las ejecutaron, y no se verificaron de forma independiente mediante shell/Git; el reviewer solo comprobó la consistencia interna del recuento (30 nodeids; 792+30=822). Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: CAMBIOS NECESARIOS**, limitado al hallazgo **I1 (documental)**, resuelto con esta actualización de `tasks.md`; **sin bloqueantes**. El **código y las pruebas quedaron aprobados por lectura** (revisión estática de solo lectura, sin ejecutar pytest ni git). Los hallazgos menores M1–M7 quedan como deuda técnica menor/opcional sin abrir una nueva ronda de correcciones (ver «Decisiones pendientes»).
- **Nota (historial):** la integración con agentes y grafo corresponde a T021/T022; la observación «pendiente de cierre por el usuario» quedó superada por la aprobación registrada en «Estado».
- **Criterios de aceptación:** cada herramienta devuelve datos reales de stock (suelto, cerrado y total equivalente), stock bajo, reposición e historial sin escribir en SQLite; las herramientas resuelven productos por SKU y por nombre sin distinguir mayúsculas de minúsculas, conservando el texto original almacenado y mostrando todas las coincidencias sin seleccionar ninguna.
- **RF:** RF-01, RF-04, RF-05.

### T020 — Herramientas de propuesta

- **Fase:** 3.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada en `app/tools/proposal_tools.py`: `create_proposal_tools(service) -> tuple[BaseTool, ...]` con **nueve** herramientas de creación (una por cada acción de `ProposalAction`, incluida `apertura`) y **una** de edición (`edit_proposal`), que validan su entrada con los payloads Pydantic existentes de `app/schemas/proposals.py` y delegan **únicamente** en `ProposalService` (crear/editar), **sin herramienta ni llamada de confirmación**. Salida reutilizada: el contrato `Proposal` existente (siempre `PENDING`). Archivos creados: `app/tools/proposal_tools.py`, `tests/ai/test_proposal_tools.py`.
- **Objetivo:** preparar y editar propuestas sin confirmar.
- **Archivos previstos:** `app/tools/proposal_tools.py`.
- **Dependencias:** T015.
- **Pruebas:** `tests/ai/test_proposal_tools.py`.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **39** pruebas en `tests/ai/test_proposal_tools.py` y **861** en la suite completa; `git diff --check` sin errores de espacios. **Ni el coordinador ni el reviewer las ejecutaron**; el reviewer verificó **por lectura** la consistencia de los recuentos (39 nodeids; 822+39=861) y los contratos. Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** Sin bloqueantes ni hallazgos importantes; los hallazgos menores **M1–M6** quedan como deuda técnica menor sin abrir una nueva ronda de correcciones (ver «Decisiones pendientes»).
- **Nota (alcance):** la confirmación queda **fuera del alcance de T020** (la realiza el usuario desde la UI, sin pasar por el LLM); `app/domain/proposals.py` figura en `plan.md:148` pero **no existe ni se necesita** para T020 (las reglas de negocio las validan los servicios de destino al confirmar).
- **Criterios de aceptación:** las herramientas crean o editan propuestas (incluida apertura) sin persistir movimientos confirmados ni modificar el inventario.
- **RF:** RF-05.

### T021 — Inventory Agent

- **Fase:** 3.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada en `app/agents/inventory.py`: la fábrica `create_inventory_agent(*, model: BaseChatModel, tools: Sequence[BaseTool]) -> CompiledStateGraph` construida con `langchain.agents.create_agent`, con modelo y herramientas **inyectados** por parámetro (sin `get_llm()` ni acceso a persistencia), reutilizando `INVENTORY_AGENT_PROMPT`. El agente recibe las **17** herramientas (7 de consulta de T019 + 10 de propuesta de T020, incluida `edit_proposal`), **sin** herramienta ni llamada de confirmación y **sin** acceso directo a infraestructura. Archivos creados: `app/agents/__init__.py` (vacío), `app/agents/inventory.py`, `tests/ai/test_inventory_agent.py`.
- **Objetivo:** interpretar intenciones y generar respuestas o propuestas.
- **Archivos previstos:** `app/agents/inventory.py`.
- **Dependencias:** T017, T018, T019, T020.
- **Pruebas:** `tests/ai/test_inventory_agent.py` (modelo falso).
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **11** pruebas en `tests/ai/test_inventory_agent.py` y **872** en la suite completa; `git diff --check` sin errores de espacios. **Ni el coordinador ni el reviewer las ejecutaron**; el reviewer verificó **por lectura** la consistencia de los recuentos (11 casos; 861+11=872) y la semántica de errores contra las versiones instaladas. Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** Sin bloqueantes; los hallazgos menores **m1–m5** quedan como deuda técnica menor sin abrir una nueva ronda de correcciones (ver «Decisiones pendientes»).
- **Nota (I1 — dependencia para T022/T025):** se registra como dependencia técnica pendiente la incompatibilidad entre `check_same_thread=True` en la conexión SQLite de producción (`app/infrastructure/sqlite/connection.py`) y la ejecución de herramientas en hilos del `ToolNode` de LangGraph: al conectar la conexión real al agente, la primera llamada a herramienta lanzaría `sqlite3.ProgrammingError`. **No** se modifica la infraestructura ni se resuelve dentro de T021; lo abordará el cableado real en T022/T025. Las pruebas de T021 usan una conexión de test con `check_same_thread=False` (documentada).
- **Nota (D4):** la modificación mínima de `INVENTORY_AGENT_PROMPT` queda **diferida**; `app/prompts/prompts.py` **no** se modificó y el prompt se reutilizó tal cual.
- **Criterios de aceptación:** el agente genera propuestas o respuestas con datos reales; no escribe directamente en la base de datos; las operaciones que modifican inventario requieren una propuesta y confirmación explícita.

### T022 — Supervisor y grafo

- **Fase:** 3.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementado el flujo `START → supervisor → inventory_agent → END` con un **Supervisor determinista** (`app/agents/supervisor.py`, `Command(goto=...)` sin llamada al LLM) y el grafo `app/graph/workflow.py` (`build_workflow(*, model, inventory_tools, proposal_tools)`), que **envuelve las 10 herramientas de propuesta** (conservando `name`/`description`/`args_schema`/`return_direct`/`response_format`, delegando una única ejecución en `tool.func`) para capturar el `Proposal` original, y hace el **puente de estado**: `response` ← último `AIMessage`, `pending_proposal` ← última propuesta capturada. `execution_result` queda `None` (la confirmación es de la UI, T024). Se **extiende `GraphState`** (D1) con `response: str | None` en `app/graph/state.py`. Archivos creados/modificados: `app/agents/supervisor.py`, `app/graph/workflow.py`, `app/graph/state.py` (solo `response`), `tests/ai/test_supervisor.py`, y la corrección de `tests/ai/test_state.py` (T018) para reflejar el nuevo campo.
- **Objetivo:** coordinar la derivación al Inventory Agent.
- **Archivos previstos:** `app/agents/supervisor.py`, `app/graph/workflow.py`.
- **Dependencias:** T021.
- **Pruebas:** `tests/ai/test_supervisor.py` (integración con modelo falso).
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **10** pruebas en `tests/ai/test_supervisor.py` y **882** en la suite completa (tras corregir `tests/ai/test_state.py`); `git diff --check` sin errores de espacios. **Ni el coordinador ni el reviewer las ejecutaron**; el reviewer verificó **por lectura** la coherencia de los recuentos y el contenido. Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** Tras resolver la **colisión de alcance** con `tests/ai/test_state.py` (T018) mediante la corrección mínima autorizada (añadir `response` al conjunto esperado), re-revisada **APROBADO**. Sin bloqueantes ni importantes restantes; quedan como deuda técnica menor las observaciones de la envoltura (`return_direct`/`response_format` en la prueba), `_last_response_text` (contenido no textual con `str(...)`) y el docstring de módulo de `tests/ai/test_state.py` sin `response` (ver «Decisiones pendientes»).
- **Nota (I1 — SQLite, pendiente para T025):** el riesgo de threading de la conexión SQLite de producción (`check_same_thread=True` frente al `ToolNode` en hilos) **permanece pendiente para T025**; `app/infrastructure/sqlite/connection.py` (`connect()`) **no** se modificó en T022. Las pruebas de T022 usan una conexión de test con `check_same_thread=False` (solo configuración de prueba).
- **Criterios de aceptación:** el Supervisor deriva la solicitud al Inventory Agent y devuelve un resultado válido; el flujo permite responder consultas o producir propuestas, sin confirmar operaciones por medio del LLM.

## Fase 4 — Interfaz Streamlit

### T023 — UI de conversación y consultas

- **Fase:** 4.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada la interfaz Streamlit `app/ui/main.py` como raíz de composición (`connect() → repositorios → servicios → herramientas → get_llm() → build_workflow`), con dependencias inyectables (`build_assistant(*, model=None, connection=None)`), sesión por `st.session_state` (sin `st.cache_resource` ni persistencia) y flujo `graph.invoke(create_initial_state(msg))` que muestra `response` y `pending_proposal`. **No** confirma propuestas, **no** invoca `ProposalService.confirm` ni asigna `execution_result` (T024). Se **modifica `connect()`** (D1) para permitir `check_same_thread=False`. Archivos creados/modificados: `app/infrastructure/sqlite/connection.py`, `app/ui/main.py`, `app/ui/__init__.py` (vacío), `tests/ui/__init__.py` (vacío), `tests/ui/test_main.py`, `tests/integration/test_ui.py`.
- **Objetivo:** mostrar consultas y respuestas.
- **Archivos previstos:** `app/ui/main.py`.
- **Dependencias:** T022.
- **Pruebas:** pruebas de flujo con proveedor falso.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **14** pruebas en `tests/ui/test_main.py`, **1** en `tests/integration/test_ui.py` y **897** en la suite completa; `git diff --check` sin errores de espacios. **Ni el coordinador ni el reviewer las ejecutaron**; el reviewer verificó **por lectura** la coherencia de los recuentos y el contenido. Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** Sin bloqueantes ni hallazgos importantes; quedan como deuda técnica menor cinco observaciones (ver «Decisiones pendientes»).
- **Nota (I1 resuelto en producción):** el riesgo de threading SQLite quedó resuelto en el camino de producción mediante el cambio autorizado en `app/infrastructure/sqlite/connection.py` (`check_same_thread=False`) y la prueba de integración que ejercita una herramienta real vía `ToolNode`. Se aclara que `check_same_thread=False` **no** garantiza por sí solo la seguridad concurrente: la serialización la aportan SQLite y el bloqueo de `BEGIN IMMEDIATE` (T016), con una conexión por sesión.
- **Criterios de aceptación:** las consultas muestran los datos reales del inventario, incluyendo stock suelto, cerrado y total equivalente, y presentan correctamente los resultados de stock bajo, reposición e historial.

### T024 — UI de propuestas y confirmación

- **Fase:** 4.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada en `app/ui/main.py` la **revisión y confirmación explícita** de propuestas (sin edición): `Assistant` expone `proposal_service` (D4); la confirmación se hace con un **botón dedicado de la UI**, fuera del chat y **sin pasar por el LLM**, que invoca `ProposalService.confirm`; **solo en éxito** se reemplaza `pending_proposal` por la copia `CONFIRMED` y se asigna el `ConfirmationResult` real a `execution_result`; se renderiza el `ProposalOutcome` real y los errores tipados se muestran sin cambios parciales. Archivos modificados: `app/ui/main.py`, `tests/ui/test_main.py`, `tests/integration/test_ui.py`.
- **Objetivo:** presentar propuestas y permitir su confirmación explícita.
- **Archivos previstos:** `app/ui/main.py`.
- **Dependencias:** T015, T022.
- **Pruebas:** pruebas de flujo con proveedor falso.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **21** pruebas en `tests/ui/test_main.py`, **2** en `tests/integration/test_ui.py` y **905** en la suite completa; `git diff --check` sin errores de espacios. **Ni el coordinador ni el reviewer las ejecutaron**; el reviewer verificó **por lectura** la coherencia de los recuentos y el contenido. Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** Sin bloqueantes ni hallazgos importantes; quedan como deuda técnica menor tres observaciones (ver «Decisiones pendientes»).
- **Nota (guard de reenvío):** la protección contra confirmaciones repetidas es la **transición de estado del turno** —sustituir `pending_proposal` por la copia `CONFIRMED` y retirar el control, con el botón deshabilitado durante el procesamiento—, válida para la sesión de la interfaz y **no** una idempotencia durable (el servicio de propuestas es stateless y no registra propuestas).
- **Criterios de aceptación:** las propuestas pueden revisarse antes de confirmar; la confirmación (incluida la apertura) se realiza mediante un control de UI, sin pasar por el LLM; se ejecutan únicamente operaciones válidas y los errores de validación se muestran sin aplicar cambios parciales.
- **RF:** RF-05.

## Fase 5 — Integración y validación

### T025 — Integración end-to-end

- **Fase:** 5.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada la prueba de integración end-to-end en `tests/integration/test_end_to_end.py` (archivo único; fixtures locales sin `conftest.py`, modelo falso, SQLite en memoria con `connect()` real, repositorios y servicios reales). Recorre el flujo completo (consulta → propuesta pendiente sin efectos → confirmación explícita → apertura → rechazo sin cambios parciales → consulta final coherente) con aserciones en SQL independiente.
- **Objetivo:** verificar el flujo completo.
- **Archivos previstos:** `tests/integration/test_end_to_end.py`.
- **Dependencias:** T024.
- **Pruebas:** flujo completo con proveedor falso.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **2** pruebas en `tests/integration/test_end_to_end.py` y **907** en la suite completa; `git diff --check` sin errores de espacios. **Ni el coordinador ni el reviewer las ejecutaron**; el reviewer verificó **por lectura** la coherencia de los recuentos y el contenido. Por lo tanto, estos resultados no están verificados.
- **Revisión independiente: APROBADO.** Sin bloqueantes ni hallazgos importantes; quedan como deuda técnica menor dos observaciones (ver «Decisiones pendientes»).
- **Nota (flujo y aserciones verificadas por lectura):** el flujo completo verifica stock suelto **4 → 7 → 13**, stock cerrado **2 → 1**, movimientos correctos (entrada suelta 3 y apertura 1; sin movimiento por el rechazo) y total equivalente **16 → 19 → 19**, con instantánea completa y `total_changes` idénticas en propuesta-pendiente y en rechazo.
- **Nota (dos herramientas vía ToolNode):** se incluye un caso con dos llamadas de herramienta en el mismo `AIMessage` ejecutadas por el `ToolNode` sobre la conexión SQLite real (`connect()` con `check_same_thread=False`), **con la salvedad de que no demuestra por sí solo seguridad concurrente completa** (la serialización la aportan SQLite y `BEGIN IMMEDIATE`).
- **Nota (trazabilidad):** T025 **no tiene un RF propio**; la matriz de trazabilidad de RF-01 a RF-07 corresponde a **T026**.
- **Criterios de aceptación:** el flujo de consulta, propuesta, confirmación, apertura y rechazo funciona sin llamadas reales a DeepSeek; las operaciones confirmadas respetan las reglas del dominio y persisten datos coherentes.

### T026 — Trazabilidad y validación RF-01 a RF-07

- **Fase:** 5.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). La matriz de trazabilidad de RF-01 a RF-07 queda a nivel **criterio → archivo:caso**, con las referencias T010/T025/T016/T023 incorporadas y etiquetas de estado explícitas (reportado vs. revisado por lectura; estructural; brecha).
- **Objetivo:** verificar la cobertura de requisitos y la ausencia de funcionalidades excluidas.
- **Archivos previstos:** revisión de trazabilidad.
- **Dependencias:** T025.
- **Pruebas:** revisión de la matriz de trazabilidad.
- **Revisión independiente: APROBADO.** Los hallazgos de la revisión —H1 (clasificación contradictoria de la fila de inmutabilidad de RF-03), H2 (referencias de línea en la nota de DeepSeek) y H3 (duplicación requisito/criterio)— quedaron **resueltos en el ciclo único de corrección** y **re-revisados APROBADO**.
- **Nota (matriz y estados conservados):** se conserva la matriz criterio → archivo:caso y sus estados de evidencia, incluyendo la distinción entre **pruebas reportadas por el implementador** y **ejecución independiente** (no verificada).
- **Nota (brecha conservada):** se mantiene la brecha de **RF-01 `spec.md:49`** (consulta por SKU/nombre de un producto desactivado, sin caso identificable), **sin resolverla**.
- **Nota (RF-06 estructural):** RF-06 (única ubicación; sin usuarios, proveedores, compras ni ventas) se conserva como **evidencia estructural, sin evidencia conductual**.
- **Nota (DeepSeek):** se conserva la **limitación de integración real con DeepSeek** (modelo falso; no validada).
- **Nota (observación opcional):** la diferencia terminológica «modelo falso»/«proveedor falso» queda registrada como **opcional**, sin abrir otra corrección.
- **Criterios de aceptación:** cada criterio de aceptación de RF-01 a RF-07 de `spec.md` está vinculado a al menos una prueba identificable (archivo y caso); la matriz mapea los criterios de consultas por SKU/nombre sin distinguir mayúsculas a T009 y T019 y los criterios de productos desactivados a T011 y T013; la trazabilidad es verificable de forma explícita; el MVP no incluye múltiples usuarios, proveedores, compras ni ventas formales.

## Fase 6 — Actualización del precio de venta e historial (RF-08)

**Estado de la fase: completa.** **T027 (alineación documental) está Cerrada y aprobada** por el usuario; **T028–T037 están cerradas y aprobadas** (T037 con revisión independiente APROBADO; verificación de `pytest` reportada, no independiente). Las decisiones técnicas de RF-08 están aprobadas (ver «Decisiones pendientes» y `plan.md` «Decisiones resueltas (RF-08)»). Ninguna de estas tareas debe reabrir T001–T026.

### T027 — Alineación documental de `spec.md` y `plan.md` con RF-08

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; documental; sin implementación). Cierra la alineación documental de `spec.md` (RF-08, requisitos y criterios C1–C13), `plan.md` (esquema, persistencia duradera, migración con `PRAGMA user_version`, operación transaccional y lectura del historial) y `tasks.md` (T027–T037 y matriz de trazabilidad) con las decisiones de RF-08 aprobadas. **No** implicó ninguna implementación; **T028–T037 están cerradas y aprobadas**.
- **Objetivo:** dejar especificación, plan y tareas coherentes con la ampliación del precio de venta e historial antes de escribir código.
- **Archivos previstos:** `specs/001-inventory-core/spec.md`, `specs/001-inventory-core/plan.md`, `specs/001-inventory-core/tasks.md`.
- **Dependencias:** T026.
- **Pruebas:** revisión de coherencia documental (sin ejecutar `pytest`).
- **Criterios de aceptación:**
  - `spec.md` contiene RF-08 con requisitos y criterios C1–C13, sin contradicción con RF-05 ni RF-07.
  - `plan.md` declara la tabla e índice del historial, la persistencia duradera, la estrategia de migración, la operación transaccional y la lectura del historial.
  - `tasks.md` contiene T027–T037 con dependencias y criterios verificables, y la matriz de trazabilidad incorpora los criterios de RF-08 marcados como `pendiente`.
- **RF:** RF-08.

### T028 — Persistencia duradera y migración segura del esquema

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada la persistencia SQLite durable en archivo configurable (`INVENTORY_DB_PATH`, default `data/inventory.sqlite3` independiente del CWD), la migración versionada con `PRAGMA user_version` (esquema base idempotente `IF NOT EXISTS`, adopción de bases legadas validadas y rechazo seguro de estructuras desconocidas, todo dentro de `BEGIN IMMEDIATE`), `create_schema()` idempotente, y `resolve_database_path()` que crea el directorio padre. Archivos: `app/config.py`, `app/infrastructure/sqlite/schema.py`, `app/ui/main.py`, `tests/integration/test_schema.py`, `.gitignore` (`data/`), `.env.example` (`INVENTORY_DB_PATH`). **I1 resuelto** (fallback ante `INVENTORY_DB_PATH` ausente o vacío).
- **Objetivo:** habilitar la base SQLite en archivo con ruta configurable y una migración versionada que no pierda datos.
- **Archivos previstos:** `app/config.py`, `app/infrastructure/sqlite/connection.py`, `app/infrastructure/sqlite/schema.py`, `tests/integration/test_schema.py`.
- **Dependencias:** T027.
- **Pruebas:** pruebas de integración de conexión, esquema y migración.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **84** en `tests/integration/test_schema.py` y **919** en la suite completa; `git diff --check` sin errores de espacios; `git status` sin `data/` (la base local no se versiona). Ni el coordinador ni el reviewer las ejecutaron.
- **Revisión independiente: APROBADO** (tras resolver I1 en el ciclo único de corrección, re-revisado APROBADO).
- **Criterios de aceptación:**
  - El funcionamiento normal abre una base SQLite en **archivo** (no `:memory:`); `:memory:` queda limitado a las pruebas.
  - La ruta de la base es **configurable**, con un valor por defecto **claro y determinista**, **independiente del directorio de trabajo** desde el que se inicia Streamlit.
  - `connect()` abre la ruta configurada y conserva `check_same_thread=False` y una conexión por sesión.
  - La migración usa **`PRAGMA user_version`** con versiones explícitas: `create_schema` crea el esquema completo en una base nueva y, en una base existente, aplica en orden las migraciones pendientes según la versión registrada.
  - La migración **no** borra datos, **no** reinicializa el inventario y **no** se resuelve creando una base vacía; sobre una base existente con datos conserva todas las filas.
  - `create_schema` deja de fallar por tablas existentes.
  - Existe un caso que demuestra persistencia al **cerrar y reabrir** la conexión sobre la misma base en archivo, y otro que migra una base creada con el esquema anterior y con datos.
- **RF:** RF-08.
- **Decisión aprobada (ver `plan.md` «Decisiones resueltas (RF-08)»):** persistencia en archivo configurable e independiente del directorio de trabajo; migración con `PRAGMA user_version`.

### T029 — Esquema: tabla del historial de precios e índice

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada la tabla `price_changes` (id, product_id FK, presentation_id FK, previous_price/new_price TEXT Decimal estricto > 0, occurred_at, event_type) y el índice `idx_price_changes_presentation_occurred_at (presentation_id, occurred_at)`, declarados **solo en `MIGRATIONS[1]`** con `SCHEMA_VERSION` 0→1; sin triggers, sin índice por producto, sin CHECK `previous <> new`. La coherencia `product_id`↔`presentation_id` se valida transaccionalmente en el servicio (T031). Archivos: `app/infrastructure/sqlite/schema.py`, `tests/integration/test_schema.py`.
- **Objetivo:** persistir el historial de cambios de precio conforme a RF-08.
- **Archivos previstos:** `app/infrastructure/sqlite/schema.py`, `tests/integration/test_schema.py`.
- **Dependencias:** T028.
- **Pruebas:** `tests/integration/test_schema.py`.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **112** en `tests/integration/test_schema.py` (84 + 28) y **947** en la suite (919 + 28); `git diff --check` sin errores. Ni el coordinador ni el reviewer las ejecutaron.
- **Revisión independiente: APROBADO** (sin bloqueantes ni importantes).
- **Criterios de aceptación:**
  - La tabla del historial contiene `product_id`, `presentation_id`, `previous_price` (TEXT Decimal), `new_price` (TEXT Decimal), `occurred_at` y `event_type`.
  - Los precios se restringen a texto decimal mayor que cero (coherente con `presentations.sale_price`).
  - Existe el índice sobre `(presentation_id, occurred_at)` para la consulta cronológica.
  - La tabla y el índice se crean en el esquema de una **base nueva** **y** como **migración `user_version = 1`** para bases existentes, sin recrear tablas ni perder filas.
  - **No** se incorpora ningún valor nuevo a `movements.kind` y **no** se expone ninguna operación de edición ni borrado del historial.
- **RF:** RF-08.

### T030 — Repositorio y puerto del historial de precios

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementados `PRICE_CHANGE_EVENT_TYPE`, `PriceChangeRecord` (frozen) y el protocolo `PriceHistoryRepository` (`add`/`list_by_presentation`/`list_by_product`) en `app/services/ports.py`, y `SqlitePriceHistoryRepository` en `app/infrastructure/sqlite/repositories.py` (INSERT con `_decimal_to_text`/`to_utc_text`/constante de evento, lecturas `ORDER BY occurred_at, id`, sin transacciones propias ni edición/borrado). Coherencia `product_id`↔`presentation_id` documentada para T031. Archivos: `app/services/ports.py`, `app/infrastructure/sqlite/repositories.py`, `tests/integration/test_repositories.py`.
- **Objetivo:** persistir y leer los cambios de precio desde el repositorio SQLite.
- **Archivos previstos:** `app/services/ports.py`, `app/infrastructure/sqlite/repositories.py`, `tests/integration/test_repositories.py`.
- **Dependencias:** T029.
- **Pruebas:** pruebas de integración del repositorio.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **74** en `tests/integration/test_repositories.py` (59 + 15) y **962** en la suite (947 + 15); `git diff --check` sin errores. Ni el coordinador ni el reviewer las ejecutaron.
- **Revisión independiente: APROBADO** (sin bloqueantes ni importantes).
- **Criterios de aceptación:**
  - Existe una operación de alta del cambio de precio que inserta y devuelve el registro persistido.
  - Existen operaciones de lectura por presentación y por producto, en orden cronológico ascendente por `occurred_at` con desempate estable por `id`.
  - El contrato del repositorio **no** expone edición ni borrado del historial.
  - El `Decimal` se conserva como texto canónico, sin `float`.
  - El repositorio y su puerto son la superficie de lectura **reutilizable** que consumen el servicio de consulta y la interfaz; **no** se añade una herramienta nueva al agente.
- **RF:** RF-08.

### T031 — Servicio de actualización de precio atómico

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementado `PresentationService.change_sale_price` en una única transacción `BEGIN IMMEDIATE` (existencia → activo → precio válido → `set_sale_price` → `price_history.add` con `previous_price` capturado antes del UPDATE y `occurred_at=_utc_now()`), con `price_history` inyectado en el constructor y actualizado en los 9 sitios de construcción (`app/ui/main.py` + 8 archivos de pruebas). Cubre presentación base y precio igual; no altera saldos/movimientos/`applied_price`; reutiliza `ProposalOutcome` sin ampliarlo. Archivos: `app/services/presentations.py`, `tests/integration/test_presentation_service.py`, `app/ui/main.py`, y los 7 archivos de pruebas con fixtures + `tests/concurrency/test_concurrency.py`.
- **Objetivo:** actualizar el precio de venta y registrar el historial en una única transacción, corrigiendo **M6**.
- **Archivos previstos:** `app/services/presentations.py`, `tests/integration/test_presentation_service.py`.
- **Dependencias:** T030.
- **Pruebas:** pruebas de integración del servicio de presentaciones.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **39** en `tests/integration/test_presentation_service.py`, **14** en `tests/concurrency/test_concurrency.py` y **973** en la suite; `git diff --check` sin errores. Ni el coordinador ni el reviewer las ejecutaron.
- **Revisión independiente: APROBADO** (sin bloqueantes ni importantes).
- **Criterios de aceptación:**
  - La actualización de precio se ejecuta en una única transacción (`BEGIN IMMEDIATE`), revalidando el estado activo del producto y la presentación vigente.
  - El precio anterior se captura con el valor **vigente al confirmar**, no con el mostrado al preparar la propuesta.
  - Si falla cualquier parte, no queda el precio actualizado ni el cambio registrado (rollback total).
  - La operación no altera `loose_units`, `closed_stock`, los movimientos ni `applied_price` de las salidas confirmadas.
  - El alta de un producto o de una presentación **no** registra un cambio de precio.
  - La **presentación base** (factor 1) queda incluida igual que las demás presentaciones: el servicio no filtra por factor.
  - El resultado de la confirmación reutiliza `ProposalOutcome` **sin ampliarlo** (`app/schemas/proposals.py:313`), que ya devuelve la presentación actualizada.
  - Queda eliminada la ventana de carrera de **M6** (`tasks.md:569`).
- **RF:** RF-08.

### T032 — Esquema Pydantic y acción de propuesta `cambio_precio`

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementados `ProposalAction.PRICE_UPDATE = "cambio_precio"` y `PriceUpdatePayload` (`presentation_id: int Field(gt=0)`, `new_price: MonetaryValue Field(gt=0)`, sin `product_id`) incorporado a la unión `ProposalPayload`; exclusiones reescritas (solo se quita «cambios de precio», se conserva desactivación y factor). `_dispatch`/`ProposalOutcome` intactos: crear/editar no aplican el cambio ni registran historial (fail-closed al confirmar hasta T033). Archivos: `app/schemas/proposals.py`, `tests/ai/test_proposal_tools.py`, `tests/integration/test_proposal_service.py`.
- **Objetivo:** representar el cambio de precio como propuesta estructurada susceptible de confirmación.
- **Archivos previstos:** `app/schemas/proposals.py`, `tests/ai/test_proposal_tools.py`.
- **Dependencias:** T031.
- **Pruebas:** pruebas de esquemas y de herramientas de propuesta.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **47** en `tests/ai/test_proposal_tools.py` (39 + 8), **42** en `tests/integration/test_proposal_service.py` y **981** en la suite; `git diff --check` sin errores. Ni el coordinador ni el reviewer las ejecutaron.
- **Revisión independiente: APROBADO** (sin bloqueantes ni importantes).
- **Criterios de aceptación:**
  - `ProposalAction` incorpora la acción de cambio de precio y se elimina la exclusión explícita que hoy la deja fuera de alcance (`app/schemas/proposals.py:19-20`, `:50-51`).
  - El payload exige la presentación afectada y el precio nuevo como `Decimal` mayor que cero, rechazando `float`.
  - Crear y editar la propuesta **no** aplican el cambio ni registran historial.
  - La propuesta sigue sin persistirse (servicio *stateless*, sin uso de la tabla `proposals`).
- **RF:** RF-08, RF-05.

### T033 — Herramienta del asistente y despacho de la nueva acción

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada la herramienta `propose_price_update` (`args_schema=PriceUpdatePayload`, delega en `service.create`, sin escrituras) y la rama de `_dispatch` `PriceUpdatePayload → PresentationService.change_sale_price` (antes del `raise` final, sin capturar errores, `ProposalOutcome` sin ampliar). Superficie de herramientas 10→11 y de agentes 17→18; correspondencia 1:1 restaurada (10 acciones/10 herramientas de creación). Archivos: `app/tools/proposal_tools.py`, `app/services/proposals.py`, `tests/ai/test_proposal_tools.py`, `tests/ai/test_supervisor.py`, `tests/ai/test_inventory_agent.py`.
- **Objetivo:** permitir que el asistente proponga la actualización de precio y que la confirmación la ejecute.
- **Archivos previstos:** `app/tools/proposal_tools.py`, `app/services/proposals.py`, `tests/ai/test_proposal_tools.py`.
- **Dependencias:** T032.
- **Pruebas:** pruebas de herramientas de propuesta y de confirmación.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **54** en `tests/ai/test_proposal_tools.py`, **21** en `tests/ai/test_supervisor.py` + `tests/ai/test_inventory_agent.py` y **988** en la suite; `git diff --check` sin errores. Ni el coordinador ni el reviewer las ejecutaron.
- **Revisión independiente: APROBADO** (tras el ciclo único de corrección de docstrings, re-revisado APROBADO).
- **Criterios de aceptación:**
  - Existe una herramienta de creación que devuelve una propuesta pendiente **sin** escribir en la base de datos.
  - El despacho de la confirmación enruta la nueva acción al servicio transaccional de T031.
  - Sin confirmación explícita no se modifica el precio ni se registra historial.
  - Se actualizan las pruebas que hoy fijan la superficie vigente (10 herramientas y «nine actions», `tests/ai/test_proposal_tools.py:688-695`, `:434`).
- **RF:** RF-08, RF-05.

### T034 — Consulta del historial de precios: servicio y repositorio (sin herramienta nueva)

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementados `PriceHistoryService` (`app/services/price_history.py`) — consulta el historial por presentación y por producto (pre-chequea existencia → `RecordNotFoundError`; `[]` si no hay cambios; orden `occurred_at, id` delegado en T030; sin escrituras ni transacciones) — y `PresentationService.get_presentation(presentation_id)` como lectura pública (delega en `_required_presentation`, sin exigir producto activo). Sin herramienta nueva. Archivos: `app/services/price_history.py`, `app/services/presentations.py`, `tests/integration/test_price_history_service.py`.
- **Objetivo:** exponer el historial de cambios de precio como consulta reutilizable para la interfaz.
- **Archivos previstos:** `app/services/price_history.py` (servicio de consulta sobre el repositorio de T030), `app/services/presentations.py` (método de lectura `get_presentation`, D4), `tests/integration/test_price_history_service.py`.
- **Dependencias:** T030.
- **Pruebas:** pruebas de integración del servicio de consulta (sin ejecución real en esta fase documental).
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **11** en `tests/integration/test_price_history_service.py`, **39** en `tests/integration/test_presentation_service.py` y **999** en la suite; `git diff --check` sin errores. Ni el coordinador ni el reviewer las ejecutaron.
- **Revisión independiente: APROBADO** (sin bloqueantes).
- **Criterios de aceptación:**
  - El historial se devuelve por presentación y por producto, en orden cronológico, sin crear movimientos ni modificar saldos.
  - La consulta es **reutilizable** desde el servicio/repositorio y la consume la **interfaz** (T035).
  - **No** se añade una herramienta nueva al agente: la decisión aprobada no lo justifica en el MVP.
  - Una presentación inexistente se informa como error.
  - Un producto desactivado **sí** admite la consulta de su historial (coherente con `spec.md:49`).
  - La presentación afectada se resuelve mediante `PresentationService.get_presentation(presentation_id)`. Si no existe, se lanza `RecordNotFoundError`. La consulta no modifica el precio ni registra ningún cambio.
- **RF:** RF-08, RF-05.
- **Decisión aprobada:** lectura por servicio/repositorio expuesta a la UI, sin herramienta al agente (ver `plan.md` «Decisiones resueltas (RF-08)»).

### T035 — Interfaz: precio vigente, precio nuevo y confirmación

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada en `app/ui/main.py`: `Assistant` expone `presentation_service` y `price_history_service`; `describe_price_update` (presentación/precio vigente/nuevo; captura `RecordNotFoundError`; sin `None`) y `describe_price_history` (cronológico); precios con 2 decimales en la UI (dominio sigue `Decimal`); el bloque de precio se muestra pendiente **y** confirmado sin reejecutar; un único punto de escritura (`confirm_pending` → `proposal_service.confirm`); `describe_proposal`/`except (LookupError, ValueError)` intactos. Archivos: `app/ui/main.py`, `tests/ui/test_main.py`.
- **Objetivo:** mostrar la propuesta de cambio de precio y confirmarla explícitamente desde la interfaz.
- **Archivos previstos:** `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T033, T034.
- **Pruebas:** pruebas de flujo de la interfaz con proveedor falso.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **28** en `tests/ui/test_main.py` (21 + 7) y **1006** en la suite; `git diff --check` sin errores. Ni el coordinador ni el reviewer las ejecutaron.
- **Revisión independiente: APROBADO** (sin bloqueantes ni importantes).
- **Criterios de aceptación:**
  - La propuesta muestra la presentación afectada, el precio vigente y el precio nuevo.
  - La confirmación se realiza con un control de la interfaz **fuera del chat**, sin pasar por el LLM (mismo criterio que T024).
  - El historial consultado se renderiza con datos reales.
  - La descripción de la propuesta no imprime `None` para los campos del cambio de precio.
- **RF:** RF-08, RF-05.

### T036 — Pruebas de integración de la actualización y el historial

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Añadidos 11 casos de integración en `tests/integration/test_presentation_service.py` (precio anterior real con mutación intermedia; presentación inexistente sin historial), `tests/integration/test_repositories.py` (`event_type` literal persistido `"cambio_precio"`; refuerzo estructural del contrato append-only y ausencia de `UPDATE`/`DELETE`/`DROP`, sin triggers) y `tests/integration/test_proposal_service.py` (C2 crear no escribe; C1/C5 confirmar aplica y registra; C4 estricto precio anterior al confirmar; rechazos sin escrituras parciales; C7 rollback con `_FailingPriceHistoryRepository`); docstring obsoleto corregido. Sin idempotencia durable. Archivos: `tests/integration/test_presentation_service.py`, `tests/integration/test_repositories.py`, `tests/integration/test_proposal_service.py`.
- **Objetivo:** cubrir a nivel integración los criterios de RF-08.
- **Archivos previstos:** `tests/integration/test_presentation_service.py`, `tests/integration/test_repositories.py`, `tests/integration/test_proposal_service.py`.
- **Dependencias:** T031, T035.
- **Pruebas:** pruebas de integración.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **166** en los tres archivos de integración (41 + 76 + 49) y **1017** en la suite; `git diff --check` sin errores. Ni el coordinador ni el reviewer las ejecutaron.
- **Revisión independiente: APROBADO** (sin bloqueantes ni importantes).
- **Criterios de aceptación:**
  - Cada caso cubre de forma explícita un criterio de RF-08 (`C1`, `C2`, `C4`, `C5`, `C6`, `C7`, `C8`, `C9`, `C11`, `C12`, `C13`): confirmación aplica y registra el cambio (`C1`); propuesta no confirmada no cambia ni registra nada (`C2`); precio anterior capturado al confirmar (`C4`); el cambio registra producto, presentación, precios, instante UTC y evento, y el alta no registra (`C5`); rechazo sin cambios parciales (`C6`); fallo del registro del historial no deja el precio actualizado (`C7`); existencias y stock cerrado intactos (`C8`); `applied_price` de las salidas confirmadas conservado (`C9`); producto desactivado rechazado (`C11`); precio inválido rechazado (`C12`); editar o eliminar el historial rechazado (`C13`). **Quedan fuera de T036** `C3` (mostrar vigente/nuevo, UI/T035) y `C10` (historial cronológico, T034/T037).
  - Las pruebas reportadas se distinguen de la ejecución independiente.
- **RF:** RF-08.

### T037 — End-to-end, persistencia tras reinicio y trazabilidad de RF-08

- **Fase:** 6.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementados los casos `test_the_price_change_and_its_history_survive_a_restart` y `test_a_failing_history_rolls_back_and_the_database_stays_usable` en `tests/integration/test_end_to_end.py` (flujo E2E con base en archivo y ruta configurada; consulta → propuesta sin efectos → confirmación → precio/`previous_price`/`event_type` → sin cambios de stock/movimientos → presentación base → cierre y reapertura → persistencia + `schema_version`; atomicidad con `foreign_key_check` y base utilizable; `snapshot` con `price_changes`), y la matriz de trazabilidad de RF-08 (13 filas C1–C13, con C13 estructural). Revisión independiente APROBADO. Archivos: `tests/integration/test_end_to_end.py`, `specs/001-inventory-core/tasks.md`.
- **Objetivo:** demostrar el flujo completo, la persistencia y la atomicidad, y cerrar la trazabilidad de RF-08.
- **Archivos previstos:** `tests/integration/test_end_to_end.py`, `specs/001-inventory-core/tasks.md`.
- **Dependencias:** T036.
- **Pruebas:** prueba de integración end-to-end y actualización de la matriz de trazabilidad.
- **Pruebas reportadas (no verificadas por el coordinador ni por el reviewer):** la sesión de implementación reportó **4** en `tests/integration/test_end_to_end.py` (2 de T025 + 2 de T037) y **1019** en la suite; `git diff --check` sin errores. Ni el usuario ni el reviewer ejecutaron `pytest` de forma independiente.
- **Criterios de aceptación:**
  - El flujo completo (consulta → propuesta → confirmación → consulta del historial) funciona sin llamadas reales a DeepSeek.
  - Se demuestra **persistencia tras reiniciar** el proceso de la aplicación sobre la misma base en archivo y con la **ruta configurada** (independiente del directorio de trabajo): el historial sobrevive al cierre y a la reapertura.
  - Se demuestra **atomicidad**: un fallo forzado durante la confirmación no deja el precio actualizado sin su registro de historial ni un registro sin su precio.
  - Se verifica la actualización de la **presentación base** (factor 1) igual que las demás, y que la confirmación reutiliza `ProposalOutcome` **sin ampliarlo**.
  - Las 13 filas de RF-08 en la matriz pasan de `pendiente` a evidencia con `archivo:caso`.
  - RF-08 **no** se declara terminado sin las dos demostraciones anteriores.
- **RF:** RF-08.

## Fase 7 — Interfaz de gestión del inventario (RF-09)

**Estado de la fase: en curso.** Contiene las tareas **T038** (**cerrada y aprobada** tras revisión independiente), **T039** (**cerrada y aprobada** tras **segunda revisión independiente**), **T040** (**cerrada y aprobada** tras revisión independiente), **T041** (**cerrada y aprobada** tras revisión independiente), **T042** (**cerrada y aprobada** tras revisión independiente), **T043** (**cerrada y aprobada** tras revisión independiente), **T044** (**cerrada y aprobada** tras revisión independiente), **T045** (**cerrada y aprobada** tras revisión independiente), **T046** (**cerrada y aprobada** tras revisión independiente) y **T047** (**cerrada y aprobada** tras revisión independiente); las tareas **T048–T056** permanecen **Pendientes** y la Fase 7 continúa en **T048**. Las decisiones técnicas de RF-09 están aprobadas (ver `plan.md` «Decisiones resueltas (RF-09)»). La Fase 7 **no** modifica RF-01–RF-08 y **no** reabre T001–T037. La verificación es **reportada** mientras no exista ejecución independiente de `pytest` (ninguna sesión la ha realizado en este proyecto).

### T038 — Formalización documental de RF-09

- **Fase:** 7.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Alcance: **formalización documental de RF-09** en `spec.md` (RF-09, requisitos y criterios C1–C22), `plan.md` (navegación lateral, composición dual, Fase 7 y «Decisiones resueltas (RF-09)» **D-A**…**D-G**) y `tasks.md` (Fase 7, T038–T056). **Segunda revisión independiente: APROBADO** (sin bloqueantes ni hallazgos importantes; **I1 resuelto** en el ciclo único de corrección). **No** implica ninguna implementación: **T048–T056 permanecen pendientes** y la Fase 7 continúa en **T048**.
- **Objetivo:** dejar especificación, plan y tareas coherentes con la interfaz de gestión (RF-09) antes de escribir código.
- **Archivos previstos:** `specs/001-inventory-core/spec.md`, `specs/001-inventory-core/plan.md`, `specs/001-inventory-core/tasks.md`.
- **Dependencias:** Ninguna (continúa la numeración de T037; no la reabre).
- **Pruebas:** revisión de coherencia documental (sin ejecutar `pytest`).
- **Criterios de aceptación:**
  - `spec.md` contiene RF-09 con objetivo, requisitos y criterios C1–C22, **después** de RF-08 y **antes** de las reglas de negocio transversales, sin alterar RF-01–RF-08 ni las reglas transversales.
  - RF-09 declara explícitamente que **no** modifica RF-01–RF-08 y que la desactivación de productos y el cambio de factor de presentación quedan **fuera**.
  - `plan.md` declara la navegación lateral, la composición dual (lectura sin LLM y asistente por separado) y la estructura `ui/ main.py session.py formatting.py sections/`.
  - `tasks.md` contiene T038–T056 con fase, estado, objetivo, archivos previstos, dependencias, pruebas y criterios verificables, todos con `RF: RF-09`.
  - **Ninguna** tarea de T001–T037 se modifica ni se renumera.
- **RF:** RF-09.

### T039 — Composición de lectura sin LLM y contenedor de sesión

- **Fase:** 7.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras la **segunda revisión independiente**). Implementada la composición dual: nuevo `app/ui/session.py` (`SESSION_KEY`, `SessionResources`, `open_connection()`, `build_session(*, connection=None)`, que compone repositorios y servicios de **lectura** sin invocar `get_llm()`, sin grafo ni herramientas); `app/ui/main.py` pasa a `build_assistant(*, model=None, connection=None, session=None)` (resuelve el modelo **primero** y reutiliza el `SessionResources` recibido; `Assistant` referencia el contenedor con propiedades de compatibilidad —`connection`, `proposal_service`, `presentation_service`, `price_history_service`— y conserva `history`, `answer`, `confirm_pending` y el **único** punto `.confirm(`); `main()` compone la **lectura primero** y el asistente por separado en `try/except LLMConfigurationError`, de modo que las secciones de consulta siguen disponibles sin modelo. Archivos: `app/ui/session.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Objetivo:** componer por sesión la **lectura** (repositorios y servicios) **sin** `get_llm()`, y el asistente por separado, de modo que las secciones de consulta funcionen sin modelo configurado.
- **Archivos previstos:** `app/ui/session.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T038.
- **Pruebas:** pruebas de la interfaz con `AppTest` que verifiquen el arranque sin `DEEPSEEK_API_KEY`.
- **Pruebas reportadas (ejecutadas por el implementer; no verificadas de forma independiente por el reviewer, que revisó en modo de solo lectura):** 34 en `tests/ui/test_main.py` y 1025 en la suite completa.
- **Revisión independiente:** la **primera** revisión emitió **CAMBIOS NECESARIOS** por el hallazgo importante **I1** (la rama `build_assistant(session=…)` no tenía ninguna prueba y una regresión habría pasado inadvertida); se autorizó un **ciclo único de corrección** limitado a `tests/ui/test_main.py`, que añadió `test_build_assistant_reuses_the_received_session` (verifica `assistant.session is session`, `assistant.connection is session.connection` y que una consulta real devuelve el catálogo preparado; además, el parche de `open_connection` detectaría una apertura adicional). **Segunda revisión independiente: APROBADO**, sin bloqueantes ni hallazgos importantes. Las observaciones opcionales O1–O2 y las deudas diferidas permanecen sin cambios (no abordadas).
- **Criterios de aceptación:**
  - Con `DEEPSEEK_API_KEY` ausente, la aplicación arranca y las secciones de consulta obtienen datos reales (**C2**).
  - La ruta de lectura **no** invoca `get_llm()` (`app/ui/main.py:186`); la composición del asistente solo se ejecuta cuando hay modelo.
  - La abstención actual del arranque sin modelo (`app/ui/main.py:598-605`) y el caso que la fija (`tests/ui/test_main.py:618-634`) quedan resueltos de forma **explícita**, sin declarar probado lo no ejecutado.
  - La adaptación de esas pruebas debe **conservar** la cobertura del **asistente y de sus errores**: la comprobación de que `get_llm()` sin clave falla se **traslada** del arranque a la sección del asistente y **no** se elimina (**C2**).
  - Una **única** conexión y un conjunto de servicios **por sesión**, compartidos por las secciones (**D-G** en `plan.md` «Decisiones resueltas (RF-09)»); la interfaz no abre conexiones propias (`app/ui/main.py:613-625`).
- **RF:** RF-09.

### T040 — Lectura pública de productos en el servicio de inventario

- **Fase:** 7.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementado el método público y aditivo `InventoryService.list_products() -> list[ProductRecord]` en `app/services/inventory.py:213`, que delega en `self._products.list_all()` —incluye productos activos e inactivos, sin filtros ni paginación, y hereda el orden de registro del repositorio—; se amplió el docstring del módulo para enumerar el listado. Archivos: `app/services/inventory.py` (modificado) y `tests/integration/test_inventory_service.py` (nuevo); **no** se modificó `app/services/ports.py` ni las firmas existentes. El requisito **C5** (stock cerrado) se difiere a **T044**.
- **Objetivo:** **añadir** al servicio de inventario una **lectura pública** del listado de productos (con estado) consumible por la interfaz —de forma aditiva, sin alterar las lecturas existentes—, sin usar como API pública la lectura interna del repositorio.
- **Archivos previstos:** `app/services/inventory.py`, `app/services/ports.py` (solo si el contrato lo exige), `tests/integration/test_inventory_service.py`.
- **Dependencias:** T039.
- **Pruebas:** pruebas de integración del servicio de inventario.
- **Pruebas reportadas (ejecutadas por el implementer; no verificadas de forma independiente por el reviewer, que revisó en solo lectura):** 38 en `tests/integration/test_inventory_service.py` + `tests/ai/test_inventory_tools.py` y 1033 en la suite completa.
- **Revisión independiente: APROBADO**, sin bloqueantes ni hallazgos importantes; las observaciones opcionales O1–O3 y O5 quedan como mejoras no bloqueantes (sin abrir ciclo de corrección).
- **Criterios de aceptación:**
  - Se **añade** a `InventoryService` (clase en `app/services/inventory.py:153`) un método público **nuevo y aditivo** —implementado como `list_products()` en `app/services/inventory.py:213`— que devuelve los productos con su estado, reutilizando `ProductRepository.list_all` (`app/services/ports.py:158`; lectura interna en `app/services/inventory.py:386`).
  - El método incluye los productos **inactivos**, porque el filtro activo/inactivo es de la interfaz (**C7**); las lecturas de alertas (`app/services/inventory.py:370`, `_alert_items`) siguen considerando **solo** productos activos.
  - **No** se modifica el contrato de las lecturas existentes ni se añaden herramientas al agente.
- **RF:** RF-09.

### T041 — Navegación lateral y enrutado de secciones

- **Fase:** 7.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada la navegación lateral de **una sola página** con las siete secciones de RF-09, «Asistente» por defecto y solo la sección elegida renderizada: `SECTION_KEY`, `SECTION_LABELS` y el registro de enrutado en `app/ui/main.py` (`:146-164`, `:721-775`); se eliminó el `return` del `except LLMConfigurationError` de `main()` para que la falta de modelo no aborte la aplicación. Nuevos `app/ui/sections/__init__.py` (vacío) y `app/ui/sections/proposals.py` (vista de «Propuestas pendientes», de **solo lectura**: encabezado, estado vacío explícito y acción/estado por propuesta; **sin** confirmar, editar ni descartar). Las cinco secciones no implementadas (inicio, productos, operaciones, movimientos e historial de precios) muestran avisos explícitos que no leen servicios. Guard estructural de `.confirm(` ampliado a todo `app/ui/**` (Q5=A). Archivos: `app/ui/main.py`, `app/ui/sections/__init__.py` (nuevo), `app/ui/sections/proposals.py` (nuevo), `tests/ui/test_main.py`.
- **Objetivo:** introducir la navegación lateral con las secciones de RF-09 conservando el único punto de entrada.
- **Archivos previstos:** `app/ui/main.py`, `app/ui/sections/__init__.py` (nuevo), `app/ui/sections/proposals.py` (nuevo; sección «Propuestas pendientes»), `tests/ui/test_main.py`.
- **Dependencias:** T039.
- **Pruebas:** pruebas de la interfaz que verifiquen la presencia de las secciones y la ausencia de multipágina.
- **Pruebas reportadas (ejecutadas por el implementer; no verificadas de forma independiente por el reviewer, que revisó en solo lectura):** 47 en `tests/ui/test_main.py`, 610 en `tests/ui` + `tests/integration` y 1046 en la suite completa.
- **Revisión independiente: APROBADO** (7/7 criterios cumplidos; sin bloqueantes ni hallazgos importantes; observaciones m1–m3 y mejoras opcionales O1–O8 no bloqueantes, sin abrir ciclo de corrección).
- **Criterios de aceptación:**
  - La aplicación ofrece navegación lateral con las secciones **inicio, productos, operaciones, propuestas pendientes, movimientos, historial de precios y asistente** (**C1**).
  - La sección **«Propuestas pendientes»** se renderiza desde `app/ui/sections/proposals.py`, con responsabilidad única de **vista**; ese módulo **no** confirma: la confirmación permanece en el único punto de `main.py` (**D-A**), sin duplicar responsabilidades.
  - El punto de entrada sigue siendo `app/ui/main.py` (`main()` en `:778-823`; `tests/ui/test_main.py:97-99` y `:1584-1604`; `tests/integration/test_ui.py:43`; `tests/integration/test_end_to_end.py:85-88`), sin adoptar multipágina de Streamlit.
  - La comprobación estructural de un único punto de confirmación se conserva y se amplía a **todo `app/ui/**`**: la suma de `.confirm(` es **1** (`tests/ui/test_main.py:969-990`, con `ui_sources()` en `:320-322`).
  - Las secciones no importan repositorios de persistencia ni el dominio directamente (`tests/ui/test_main.py:1607-1628`).
- **RF:** RF-09.

### T042 — Formateo de valores y de fechas en la interfaz

- **Fase:** 7.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementado `app/ui/formatting.py` como módulo **puro de presentación** (sin efectos secundarios, sin `datetime.now()`, sin `float`, sin locale del sistema y sin desplazamiento fijo), con `format_quantity(int | None)`, `format_money(Decimal | None)` y `format_instant(datetime | None)` documentados en español: cantidades enteras sin separador ni decimales; importes con **dos decimales fijos** y redondeo explícito `ROUND_HALF_UP` sin mutar el `Decimal`; fechas en `YYYY-MM-DD HH:MM:SS ±HH:MM` convertidas con `local_timezone()` a la zona IANA argentina (el desplazamiento se deriva del instante, no es fijo); `no informado` para `None` con el cero real conservado; y `ValueError` para instantes sin zona horaria. **No** se modificó `app/ui/main.py` ni ningún archivo existente: la integración del formato en las secciones (y la unificación con `_format_price`/`isoformat()` de `main.py`) corresponde a **T050/T055**. Archivos: `app/ui/formatting.py` (nuevo), `tests/ui/test_formatting.py` (nuevo).
- **Objetivo:** centralizar el formateo de cantidades, precios y fechas/horas, presentando las fechas en la zona local sobre almacenamiento UTC.
- **Archivos previstos:** `app/ui/formatting.py` (nuevo), `tests/ui/test_formatting.py` (previsto).
- **Dependencias:** T039.
- **Pruebas:** pruebas unitarias del formateo (previstas).
- **Pruebas reportadas (ejecutadas por el implementer; no verificadas de forma independiente por el reviewer, que revisó en solo lectura):** 26 en `tests/ui/test_formatting.py`, 73 en `tests/ui` y 1072 en la suite completa.
- **Revisión independiente: APROBADO** (criterios cumplidos; sin bloqueantes ni hallazgos importantes; observaciones m1–m5 y mejoras opcionales O1–O4 no bloqueantes, sin abrir ciclo de corrección; la deuda queda pendiente y **no** se resuelve en T042).
- **Criterios de aceptación:**
  - Las fechas y horas se presentan en `America/Argentina/Buenos_Aires` (`LOCAL_TIMEZONE_NAME`, `app/infrastructure/sqlite/instants.py:31`; `local_timezone()`, `:42`) y el **almacenamiento permanece en UTC** (requisito de RF-09, `spec.md:320`; `plan.md:128`).
  - El formateo **no** altera la semántica `[inicio, fin)` de los períodos (`resolve_period`, `app/infrastructure/sqlite/instants.py:90`).
  - Un valor ausente se representa como ausente y **no** como cero (**C4**).
- **Evidencia de implementación (revisión independiente):** `app/ui/formatting.py` — `format_quantity` `:62-77`, `format_money` `:80-109`, `format_instant` `:112-141`, `_offset_text` `:144-163`; único import de `app`: `local_timezone` `:43`. Pruebas en `tests/ui/test_formatting.py`: cantidades `:162-181`, importes `:187-241`, fechas `:247-290`, ausencia y naive `:293-308`, pureza `:311-333`, guard estructural `:339-369`.
- **RF:** RF-09.

### T043 — Sección Inicio: alertas y conteos

- **Fase:** 7.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada `app/ui/sections/home.py` como **vista de solo presentación**: recibe de `app/ui/main.py` los datos ya consultados y no lee servicios, no escribe y no usa `st.session_state`. Muestra **dos listas independientes** —stock bajo y reposición— con nombre, SKU, total equivalente y faltante (D-1, D-7), en el orden del servicio y sin deduplicar (D-3); realiza **una única** llamada a `InventoryService.list_products()` por render, de la que derivan los conteos de activos e inactivos (D-4); cada lista vacía informa su propio mensaje (D-2) y el catálogo vacío se informa sin conteos (D-5). Las lecturas son **perezosas**: `main.py` las ejecuta solo al renderizar Inicio mediante `home_section(session)`, y conserva `SECTION_LABELS`, la sección predeterminada «Asistente» y el único punto de confirmación. **No** se usó `app/ui/formatting.py` (D-6) ni se modificó ningún otro archivo de la aplicación. Archivos: `app/ui/sections/home.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Objetivo:** mostrar en la sección de inicio las alertas de stock bajo y de reposición y los conteos de productos.
- **Archivos previstos:** `app/ui/sections/home.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T040, T041, T042.
- **Pruebas:** pruebas de la interfaz con datos reales en base temporal.
- **Pruebas reportadas (ejecutadas por el implementer; no verificadas de forma independiente por el reviewer, que revisó en solo lectura):** 11 específicas de Inicio, 57 en `tests/ui/test_main.py`, 83 en `tests/ui` y 1082 en la suite completa.
- **Revisión independiente: APROBADO** (los 11 criterios de revisión se cumplen; sin bloqueantes ni hallazgos importantes; observaciones M1–M8 y mejoras opcionales no bloqueantes, sin ciclo de corrección; la deuda queda pendiente y **no** se resuelve en T043).
- **Criterios de aceptación:**
  - Las alertas de stock bajo y de reposición muestran producto, total equivalente y faltante, obtenidos del servicio (`app/services/inventory.py:169` y siguientes; `_alert_items`, `:349`) (**C3**).
  - El conteo de activos e inactivos proviene de la **lectura pública** de T040 (**C3**).
  - Sin datos, la sección informa el vacío explícitamente (**C4**).
- **Evidencia de implementación (revisión independiente):** `app/ui/sections/home.py` — `render_home` `:121-161`, `describe_stock_alert` `:73-92`, `_render_alert_list` `:95-118`; integración en `app/ui/main.py` — `home_section` `:700-728`, registro `:783`, paso de la sesión `:852-856`; datos de `get_low_stock_products` (`app/services/inventory.py:284`), `get_replenishment_products` (`:304`) y `list_products` (`:213`). Las anclas `:169`/`:349` del criterio anterior son históricas y no se corrigen en este cierre. Pruebas en `tests/ui/test_main.py` `:1783-2142` (11 casos) y espía de lectura única `:334-356`.
- **RF:** RF-09.

### T044 — Sección Productos: listado, búsqueda y filtro

- **Fase:** 7.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada `app/ui/sections/products.py` como **vista de solo presentación**: `app/ui/main.py` ejecuta las lecturas públicas (`list_products()`, `find_products_by_name()` o `find_product_by_sku()` según el campo, y `get_product_presentations()` por fila visible) y la vista solo presenta los datos y devuelve los valores de los controles. Muestra los **siete campos** de C5 —SKU, nombre, unidad base, stock mínimo, unidades sueltas, stock cerrado y estado— en el orden del servicio y sin truncar; **«Stock cerrado» es la suma de `closed_stock` de las presentaciones (A1), potencialmente con factores distintos, por lo que no es una cantidad homogénea en unidades base**. **B1:** un campo de texto y un selector «Nombre / SKU» (Nombre por defecto); la búsqueda es exacta y sin distinguir mayúsculas según el servicio (C6), se advierte en la interfaz y una entrada vacía o solo con espacios no busca. **C1:** filtro «Todos / Activos / Inactivos» (Todos por defecto), aplicado después de la búsqueda y antes de leer presentaciones, sin alterar las alertas globales (C7). **D1:** aviso con el número real de productos y sin paginación (C8). **E1/G1:** claves estables y valores de retorno, sin `st.session_state` en la vista. **F1:** texto preformateado que no interpreta Markdown. Mensajes diferenciados para catálogo vacío, búsqueda sin resultados y filtro sin resultados. Intactos `SECTION_LABELS`, «Asistente» como sección predeterminada, la navegación, Inicio y el único punto de confirmación. Archivos: `app/ui/sections/products.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Objetivo:** listar, buscar y filtrar productos desde la interfaz por servicios de lectura.
- **Archivos previstos:** `app/ui/sections/products.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T040, T041, T042.
- **Pruebas:** pruebas de la interfaz (listado, búsqueda exacta, filtro de estado, listado extenso).
- **Pruebas reportadas (ejecutadas por el implementer; no verificadas de forma independiente por el reviewer, que revisó en solo lectura):** **16 pruebas nuevas** de Productos; 72 en `tests/ui/test_main.py`, 98 en `tests/ui` y 1097 en la suite completa.
- **Revisión independiente: APROBADO** (criterios C5–C8 cumplidos; sin bloqueantes ni hallazgos importantes; observaciones M1–M7 y mejoras opcionales O1–O6 no bloqueantes, sin ciclo de corrección; la deuda queda pendiente y **no** se resuelve en T044).
- **Criterios de aceptación:**
  - El listado muestra SKU, nombre, unidad base, stock mínimo, unidades sueltas, stock cerrado y estado, desde la lectura pública (**C5**).
  - La búsqueda reutiliza las lecturas existentes —coincidencia **exacta** y **sin** distinguir mayúsculas (`app/infrastructure/sqlite/repositories.py:368`, `:383`)— y la interfaz lo advierte (**C6**).
  - El filtro activo/inactivo es de presentación y **no** altera las alertas globales (**C7**).
  - Si el listado excede el espacio visible, la interfaz informa que **no** se pagina, sin límites arbitrarios (**C8**).
- **Evidencia de implementación (revisión independiente):** `app/ui/sections/products.py` — `render_product_controls` `:197-223`, `describe_product_row` `:237-258`, `render_products` `:261-289`; composición en `app/ui/main.py` — `_matches_status` `:750-769`, `_product_candidates` `:772-798`, `_product_row` `:801-831` (suma de `closed_stock` `:827-829`), `products_section` `:834-879`, registro `:935`; lecturas en `app/services/inventory.py` — `find_products_by_name` `:175`, `find_product_by_sku` `:194`, `list_products` `:213`, `get_product_presentations` `:260`. Pruebas en `tests/ui/test_main.py` `:2226-2756` (16 casos), espía `:365-412` y guard estructural `:2728-2756`.
- **RF:** RF-09.

### T045 — Detalle de producto

- **Fase:** 7.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras **segunda revisión independiente**). Implementado `app/ui/sections/product_detail.py` como **vista de solo presentación**: `app/ui/main.py` lee `get_product_stock()`, `get_movement_history()` y `PriceHistoryService.get_price_history_by_product()`, y la vista recibe los datos ya consultados. El detalle se integra **al final de la sección Productos** y **reutiliza la lista visible de T044**; sin productos visibles (catálogo vacío, búsqueda o filtro sin resultados) no se renderiza el selector ni el detalle y prevalece el mensaje de T044 (**I1**). Muestra SKU, nombre, unidad base, stock mínimo, estado, unidades sueltas y total equivalente; por presentación, nombre, factor, stock cerrado e indicación de presentación base; movimientos (tipo traducido, cantidad, motivo, fecha local, presentación y costo/precio aplicado solo si existen) e historial de precios (presentación, precios anterior/nuevo, fecha y evento) en orden cronológico. Usa `app/ui/formatting.py` (`format_quantity`, `format_money`, `format_instant`), conserva el cero real, omite campos ausentes y distingue vacío de error de lectura (`RecordNotFoundError`). Los productos inactivos siguen siendo consultables. Avisos C8 con N real y sin truncar. Sin escrituras, sin `st.session_state` y sin `.confirm(`. **I2:** cinco pruebas de integración cubren los estados vacíos, la exclusión del producto seleccionado y la transición entre listas. Archivos: `app/ui/sections/product_detail.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Objetivo:** mostrar el detalle de un producto con sus presentaciones, stock, movimientos e historial de precios por lectura.
- **Archivos previstos:** `app/ui/sections/product_detail.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T040, T041, T042.
- **Pruebas:** pruebas de la interfaz sobre un producto con presentaciones, movimientos y cambios de precio.
- **Pruebas reportadas (ejecutadas por el implementer; no verificadas de forma independiente por el reviewer, que revisó en solo lectura):** 5 pruebas nuevas de corrección; 97 en `tests/ui/test_main.py`, 123 en `tests/ui` y 1122 en la suite completa.
- **Revisión independiente: APROBADO** en la segunda revisión (sin bloqueantes ni hallazgos importantes, sin otro ciclo de corrección; la primera revisión había requerido corregir I1/I2). Hallazgos menores y mejoras opcionales no bloqueantes, conservados como deuda.
- **Criterios de aceptación:**
  - Se muestran las presentaciones con factor y stock cerrado, las unidades sueltas y el total equivalente (**C9**).
  - Se muestran los movimientos del producto y su historial de precios, por servicio de lectura (**C9**).
  - Un producto **desactivado** admite consulta de su detalle y de su historial, coherente con RF-08.
- **Evidencia de implementación (segunda revisión independiente):** `app/ui/sections/product_detail.py` — selector `:254-283`, `describe_product` `:366-391`, `describe_presentation` `:394-416`, `describe_movement` `:419-454`, `describe_price_change` `:457-480`, render de listas `:483-546`; composición en `app/ui/main.py` — `products_section` `:839-890` (detalle solo con lista visible `:889-890`), `product_detail_section` `:895-943`; lecturas `app/services/inventory.py:228-258`, `:325-368` y `app/services/price_history.py:74-92`. Pruebas en `tests/ui/test_main.py:2964-3579`; corrección I1/I2 en `:3354-3579`; guard estructural `:3486-3518`.
- **RF:** RF-09.

### T046 — Sección Movimientos: filtros y período local

- **Fase:** 7.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada `app/ui/sections/movements.py` como **vista de solo presentación**, integrada en `app/ui/main.py` con lecturas perezosas. **D1:** el producto es obligatorio (selector con centinela, sin «Todos» ni lectura global); **D7:** incluye activos e inactivos marcados. **D3:** tipo «Todos los tipos» y sin período inicial; no se consulta hasta elegir producto. **D2:** «Desde» y «Hasta (incluido)»; el fin técnico es la medianoche local del día siguiente, exclusiva, y los límites se envían como `datetime` naive locales para que la persistencia los interprete en `America/Argentina/Buenos_Aires` sin doble conversión (`[inicio, fin)`). **D4:** período invertido validado sin llamada y errores de lectura mostrados como error, nunca como vacío. **D5:** se reutilizan etiquetas, descripción y avisos de T045. **D6:** orden del servicio, estados diferenciados (catálogo vacío, sin selección, sin movimientos, filtros sin resultados, período inválido y error) y aviso C8 con N real sin truncar. **D8:** sin acciones de escritura ni creación de compensaciones; las compensaciones históricas se consultan. Sin `st.session_state`, sin `.confirm(` y sin cambios en navegación ni confirmación. Archivos: `app/ui/sections/movements.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Objetivo:** consultar movimientos con filtros por producto, tipo y período, con el período interpretado en hora local.
- **Archivos previstos:** `app/ui/sections/movements.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T039, T041, T042.
- **Pruebas:** pruebas de la interfaz con movimientos en fechas límite del período.
- **Pruebas reportadas (ejecutadas por el implementer; no verificadas de forma independiente por el reviewer ni por el coordinador):** 25 casos netos añadidos; 122 en `tests/ui/test_main.py`, 148 en `tests/ui` y 1147 en la suite completa.
- **Revisión independiente: APROBADO** (sin bloqueantes ni hallazgos importantes; observaciones menores no bloqueantes, sin ciclo de corrección).
- **Criterios de aceptación:**
  - Los filtros disponibles son producto, tipo y período, y el período se interpreta `[inicio, fin)` en la zona local (**C17**; `app/services/movements.py` y `app/infrastructure/sqlite/instants.py:90`).
  - La interfaz **no** ofrece compensaciones que las reglas vigentes rechacen (**C17**).
  - Los tipos mostrados corresponden a los `kind` vigentes (`app/domain/movements.py:49-64`), sin incorporar valores nuevos.
- **Evidencia de implementación (revisión independiente):** `app/ui/sections/movements.py` — controles `:170-229`, render `:247-281`, mensajes `:85-114`; composición en `app/ui/main.py` — `movements_section` `:964-1044`, prevalidación `:999-1005`, conversión `:1008-1019`, presentaciones `:1028-1032` y `:1047-1068`, registro `:1128`; lectura `app/services/inventory.py:325-368` e interpretación local `app/infrastructure/sqlite/instants.py:90-128`. Pruebas en `tests/ui/test_main.py:4079-5048` (25 casos netos) y guard estructural `:5016-5048`.
- **RF:** RF-09.

### T047 — Historial de precios en la interfaz

- **Fase:** 7.
- **Estado:** **Cerrada y aprobada** (por el usuario; tras revisión independiente). Implementada `app/ui/sections/price_history.py` como **vista de solo presentación**, integrada en `app/ui/main.py` con lecturas perezosas. **D1:** el producto es obligatorio (selector con centinela); sin selección no se consulta nada. **D2:** selector de presentación con «Todas las presentaciones» por defecto; una presentación concreta consulta su propio historial. **D3:** incluye productos activos e inactivos, marcados. **D4:** `event_type` se muestra tal como está almacenado. **D5:** reutiliza `describe_price_change` de T045. **D6:** aviso C8 con el número real de cambios, sin truncar. **D7:** no se modificaron `describe_price_history` ni `_format_price` del asistente. **D8:** bloques `st.text`. Usa `format_money` y `format_instant`; conserva el orden cronológico del servicio (C19), funciona sin modelo (C20) y diferencia catálogo vacío, sin selección, producto sin cambios, presentación sin cambios y error de lectura (C4/C21). Sin escrituras, sin `st.session_state` y sin `.confirm(`. Archivos: `app/ui/sections/price_history.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Objetivo:** consultar el historial de cambios de precio por presentación y por producto, con independencia del asistente.
- **Archivos previstos:** `app/ui/sections/price_history.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T039, T041, T042.
- **Pruebas:** pruebas de la interfaz sobre un producto con cambios de precio confirmados.
- **Pruebas reportadas (ejecutadas por el implementer; no verificadas de forma independiente por el reviewer ni por el coordinador):** **14 pruebas nuevas**; 135 en `tests/ui/test_main.py`, 161 en `tests/ui` y 1160 en la suite completa.
- **Revisión independiente: APROBADO** (sin bloqueantes ni hallazgos importantes; observaciones menores no bloqueantes, sin ciclo de corrección; la deuda queda pendiente y **no** se resuelve en T047).
- **Criterios de aceptación:**
  - El historial se consulta por presentación y por producto, en orden cronológico (**C19**), reutilizando la consulta de RF-08 (T034), **sin** herramienta nueva del agente.
  - La sección funciona sin modelo configurado (**C20**).
  - Un producto sin cambios informa el vacío explícitamente (**C4**).
- **Evidencia de implementación (revisión independiente):** `app/ui/sections/price_history.py` — `render_product_filter` `:119-149`, `render_presentation_filter` `:152-187`, `render_price_history` `:200-230`, error `:233-241`; composición en `app/ui/main.py` — `price_history_section` `:1059-1126`, registro `:1211`; lecturas `app/services/price_history.py:54-92`. Pruebas en `tests/ui/test_main.py:5197-5701` (14 casos) y guards `:5657-5701`.
- **RF:** RF-09.

### T048 — Sección Asistente IA

- **Fase:** 7.
- **Estado:** Pendiente.
- **Objetivo:** conservar la conversación y la confirmación vigentes en su propia sección, sin bloquear las secciones de consulta.
- **Archivos previstos:** `app/ui/main.py`, `app/ui/sections/assistant.py` (nuevo), `tests/ui/test_main.py`.
- **Dependencias:** T039, T041.
- **Pruebas:** pruebas de la interfaz con proveedor falso y sin modelo.
- **Criterios de aceptación:**
  - La conversación y la confirmación desde la interfaz conservan el comportamiento vigente (`app/ui/main.py:244`, `:422-463`, `:482-516`; `tests/ui/test_main.py`).
  - Sin modelo, la sección informa su indisponibilidad y **no** impide las demás secciones (**C2**, **C20**).
  - El agente mantiene su superficie: **10** acciones de propuesta (`app/schemas/proposals.py:57-66`) y **18** herramientas sin confirmación (`tests/ai/test_proposal_tools.py:467`, `tests/ai/test_supervisor.py:499`, `tests/ai/test_inventory_agent.py:400-401`), sin ampliarla.
- **RF:** RF-09.

### T049 — Formularios de propuestas sin escritura

- **Fase:** 7.
- **Estado:** Pendiente.
- **Objetivo:** ofrecer formularios para preparar las operaciones admitidas, creando propuestas pendientes y sin escribir.
- **Archivos previstos:** `app/ui/sections/operations.py` (nuevo), `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T039, T041.
- **Pruebas:** pruebas de la interfaz que verifiquen que enviar un formulario no altera la base.
- **Criterios de aceptación:**
  - Existen formularios para las acciones admitidas por las propuestas y **ninguno** para desactivar productos ni cambiar el factor de una presentación (**C10**; alcance fuera de RF-09).
  - Enviar un formulario válido crea una **propuesta pendiente** (`app/services/proposals.py:102`, `:117`) y el inventario y los movimientos se conservan sin cambios (**C11**).
  - Ningún formulario invoca un servicio que escriba (**C12**).
- **RF:** RF-09.

### T050 — Revisión legible de la propuesta

- **Fase:** 7.
- **Estado:** Pendiente.
- **Objetivo:** presentar la propuesta pendiente de forma legible por acción, sin exponer el volcado genérico del payload.
- **Archivos previstos:** `app/ui/sections/proposals.py`, `app/ui/formatting.py`, `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T042, T049.
- **Pruebas:** pruebas de la interfaz que comparen la revisión con los campos de la propuesta.
- **Criterios de aceptación:**
  - La revisión se renderiza desde `app/ui/sections/proposals.py` (vista de «Propuestas pendientes», creada en T041), sin duplicar la responsabilidad de confirmar de `main.py` (**D-A**).
  - La revisión muestra la acción y sus campos en lenguaje legible y, para el cambio de precio, presentación, precio vigente y precio nuevo (**C13**), reutilizando `describe_price_update` (`app/ui/main.py:295-343`).
  - La revisión general **no** depende del volcado genérico de `describe_proposal` (`app/ui/main.py:266-287`); ajustarlo mantiene las pruebas existentes (`tests/ui/test_main.py:486`, `:543`) o las actualiza de forma explícita.
  - La revisión **no** imprime valores ausentes como `None` ni como cero.
- **RF:** RF-09.

### T051 — Edición de una propuesta pendiente

- **Fase:** 7.
- **Estado:** Pendiente.
- **Objetivo:** permitir editar una propuesta pendiente antes de confirmarla, sin escribir y sin cambiar su acción.
- **Archivos previstos:** `app/ui/sections/proposals.py`, `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T050.
- **Pruebas:** pruebas de la interfaz que editen una propuesta pendiente.
- **Criterios de aceptación:**
  - La edición no escribe y no cambia la acción de la propuesta (**C14**; `app/services/proposals.py:117`, `_require_pending`, `:288-306`).
  - Una propuesta **confirmada** no puede editarse (**C14**).
  - La edición conserva el contrato vigente (editable sin efectos).
- **RF:** RF-09.

### T052 — Descarte local de una propuesta pendiente

- **Fase:** 7.
- **Estado:** Pendiente.
- **Objetivo:** permitir descartar una propuesta pendiente de forma **local a la interfaz**, sin persistencia y sin nuevos estados.
- **Archivos previstos:** `app/ui/sections/proposals.py`, `app/ui/main.py`, `tests/ui/test_main.py`.
- **Dependencias:** T050.
- **Pruebas:** pruebas de la interfaz que descarten una propuesta y verifiquen que la base no cambia.
- **Criterios de aceptación:**
  - El descarte es **local a la interfaz** y se resuelve en el **estado de sesión de Streamlit** (`st.session_state`): la interfaz deja de ofrecer la propuesta descartada. **No** se añade estado de cancelación al dominio ni al grafo (`app/graph/state.py` permanece **sin cambios**), ni a la persistencia (**C15**).
  - `ProposalStatus` conserva solo pendiente y confirmada (`app/schemas/proposals.py:79-80`) y los contratos de propuestas no cambian: el descarte **no** invoca `create`, `edit` ni `confirm` (`app/services/proposals.py:102`, `:117`, `:149`) ni introduce persistencia (**C15**).
  - Descartar no modifica la propuesta, no escribe y no deja traza (**C15**).
- **RF:** RF-09.

### T053 — Confirmación centralizada y prevención de doble ejecución

- **Fase:** 7.
- **Estado:** Pendiente.
- **Objetivo:** concentrar la confirmación en un único punto y evitar la doble ejecución desde la interfaz.
- **Archivos previstos:** `app/ui/main.py`, `app/ui/sections/proposals.py` (vista; sin confirmar), `tests/ui/test_main.py`.
- **Dependencias:** T049, T050.
- **Pruebas:** pruebas de la interfaz que confirmen una propuesta y que intenten confirmarla dos veces.
- **Criterios de aceptación:**
  - La confirmación se ejecuta por `proposal_service.confirm` (`app/services/proposals.py:149`) desde un **único** punto (`app/ui/main.py:446-488`; **D-A**), y se conserva la comprobación estructural de un único `.confirm(` en todo `app/ui/**` (**1**; `tests/ui/test_main.py:969-990`) (**C12**).
  - Una propuesta confirmada no se vuelve a ofrecer ni se ejecuta dos veces, y el reenvío se impide mientras una confirmación está en curso (**C16**).
  - Se documenta que **no** existe idempotencia durable (**C16**), sin afirmar una garantía que el sistema no ofrece.
- **RF:** RF-09.

### T054 — Compensación condicional por lectura pública

- **Fase:** 7.
- **Estado:** Pendiente.
- **Objetivo:** mostrar la cantidad compensable solo cuando una lectura pública la exponga, sin recalcularla en la interfaz.
- **Archivos previstos:** `app/services/movements.py`, `app/ui/main.py` o `app/ui/sections/operations.py`, `tests/ui/test_main.py`.
- **Dependencias:** T049.
- **Pruebas:** pruebas de la interfaz sobre una salida cerrada con y sin compensación pendiente.
- **Criterios de aceptación:**
  - La interfaz **no** recalcula la cantidad compensable pendiente; cuando la muestra, la obtiene de una lectura pública que reutiliza `_compensation_availability` (`app/services/movements.py:663-701`) o el dominio (`ClosedEntry.compensable_quantity`, `app/domain/movements.py:265`) (**C18**).
  - **No** se duplica la lógica de compensación en la interfaz, ni se reutiliza `register_compensation` (`app/services/movements.py:499-583`) para calcular (**C18**).
  - Si la lectura no está disponible, la interfaz **no** afirma compensabilidad alguna (**D-D** en `plan.md` «Decisiones resueltas (RF-09)»).
- **RF:** RF-09.

### T055 — Estados de interfaz y zona horaria

- **Fase:** 7.
- **Estado:** Pendiente.
- **Objetivo:** hacer explícitos y transversales los estados de la interfaz y homogénea la presentación de fechas.
- **Archivos previstos:** `app/ui/formatting.py`, `app/ui/main.py`, `app/ui/sections/`, `tests/ui/test_main.py`.
- **Dependencias:** T041, T042, T043, T044, T045, T046, T047.
- **Pruebas:** pruebas de la interfaz que provoquen carga, vacío, validación, rechazo y confirmación.
- **Criterios de aceptación:**
  - Carga, vacío, validación, rechazo y confirmación se distinguen y son transversales a las secciones, sin sección aparte con lógica propia (**C21**; **D-E** en `plan.md` «Decisiones resueltas (RF-09)»).
  - Un rechazo muestra el motivo devuelto por el servicio y **no** presenta la operación como ejecutada (**C21**).
  - Las fechas se muestran en hora local sobre almacenamiento UTC en todas las secciones (**C4**, **C17**, **C19**).
- **RF:** RF-09.

### T056 — Trazabilidad de RF-09 y actualización de `MEMORY.md`

- **Fase:** 7.
- **Estado:** Pendiente.
- **Objetivo:** vincular cada criterio de RF-09 con evidencia identificable y registrar el estado de la fase en `MEMORY.md`.
- **Archivos previstos:** `specs/001-inventory-core/tasks.md` (matriz de trazabilidad), `MEMORY.md`.
- **Dependencias:** todas las anteriores (T039–T055).
- **Pruebas:** ejecución de la suite (`pytest`) y actualización de la matriz de trazabilidad, con separación entre lo **reportado** y lo verificado de forma independiente.
- **Criterios de aceptación:**
  - La matriz incorpora los criterios de RF-09 (C1–C22) con evidencia `archivo:caso` o, cuando corresponda, `sin evidencia conductual (ausencia estructural)` (**C22**).
  - Cada sección declara la fuente de sus datos (**C22**).
  - `MEMORY.md` se actualiza al cierre de la fase con el estado, las decisiones de la Fase 7 y los próximos pasos, **sin** secretos ni credenciales y manteniendo su extensión objetivo.
  - La verificación por ejecución se distingue de la reportada; **no** se declara probado lo no ejecutado.
- **RF:** RF-09.

## Matriz de trazabilidad

La matriz se resuelve a nivel **criterio → evidencia**, conforme a **T026** (`tasks.md:437-444`):
cada criterio de aceptación de RF-01 a RF-07 se vincula a la prueba identificable
(`archivo:línea` + caso). Se abandona el mapeo anterior RF→tareas. Columnas: **tipo**
(`conductual` = caso que ejerce el comportamiento observable; `estructural` = ausencia de
operación o restricción de esquema) y **estado**.

Las filas de **RF-08** (`spec.md:274-288`) **no** forman parte del cierre de T026 ni de sus criterios
históricos, que se conservan limitados a «RF-01 a RF-07» (`tasks.md:451-452`): RF-08 se **incorpora y
valida por separado** en la **Fase 6** (T027–T037), y su trazabilidad corresponde a **T037**. Las trece filas ya tienen evidencia con `archivo:caso`: la implementación de T028–T036 está cerrada y T037 aporta el flujo extremo a extremo con persistencia en archivo.

**Etiquetas de estado:**

- `cubierto (reportado; ejecución no verificada independientemente)`: existe un caso que ejerce
  el criterio y la sesión de implementación **reportó** su éxito; ninguna sesión ejecutó `pytest`
  de forma independiente (ver `tasks.md:401`, `:414`, `:425`). Estos casos fueron además
  **revisados por lectura** en la revisión independiente de su tarea.
- `sin evidencia conductual (ausencia estructural)`: el criterio se sostiene por la ausencia de
  la operación o por una restricción de esquema, sin caso que lo ejercite.
- `brecha`: no se identificó un caso concreto que demuestre el criterio.
- `pendiente (especificado; sin implementación ni evidencia)`: el criterio está especificado, pero la
  funcionalidad no está implementada y no existe ningún caso que lo ejercite (RF-08).

Referencias incorporadas respecto del mapeo anterior: **T010→RF-03** (período y zona horaria),
**T025→RF-05** (flujo end-to-end), **T016→RF-07** (índice único de presentación base y
concurrencia) y **T023→RF-04** (solo los criterios que su prueba parametrizada cubre).

| RF | Criterio (`spec.md`) | Evidencia (`archivo:línea`, caso) | Tipo | Estado |
| --- | --- | --- | --- | --- |
| RF-01 | Registro válido crea el producto (`spec.md:40`) | `tests/integration/test_product_service.py:209` `test_register_creates_an_active_product_with_its_base_presentation` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-01 | SKU ya registrado se rechaza (`spec.md:41`) | `tests/integration/test_product_service.py:253` `test_register_rejects_a_duplicate_sku` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-01 | SKU que difiere solo en mayúsculas se rechaza (`spec.md:42`) | `tests/unit/test_product_rules.py:125` `test_validate_sku_unique_rejects_duplicate_with_different_case`; `tests/integration/test_repositories.py:185` `test_add_product_rejects_a_sku_that_differs_only_in_case` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-01 | SKU/nombre/unidad base vacíos se rechazan (`spec.md:43`) | `tests/unit/test_product_rules.py:36` `test_normalize_required_text_rejects_empty`; `tests/unit/test_product_rules.py:218` `test_product_rejects_empty_sku` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-01 | Texto original recortado sin normalizar caso (`spec.md:44`) | `tests/unit/test_product_rules.py:143` `test_validate_sku_unique_keeps_original_case`; `tests/integration/test_repositories.py:207` `test_add_product_keeps_the_original_sku_text` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-01 | Modificar el SKU se rechaza (`spec.md:45`) | `tests/unit/test_product_rules.py:166` `test_validate_sku_unchanged_rejects_different_sku`; `tests/integration/test_product_service.py:291` `test_update_rejects_a_sku_change` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-01 | Stock mínimo negativo o no entero se rechaza (`spec.md:46`) | `tests/unit/test_product_rules.py:82` `test_validate_minimum_stock_rejects_negative`; `tests/integration/test_product_service.py:708` `test_register_rejects_an_invalid_minimum_stock` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-01 | Desactivar conserva datos e historial (`spec.md:47`) | `tests/integration/test_product_service.py:593` `test_deactivate_keeps_the_product_and_its_history` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-01 | Producto desactivado rechaza operación/modificación (`spec.md:48`) | `tests/integration/test_product_service.py:620` `test_update_rejects_a_deactivated_product`; `tests/integration/test_movement_service.py:715` `test_an_ordinary_operation_on_a_deactivated_product_is_rejected` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-01 | Producto desactivado admite consulta por SKU/nombre e historial (`spec.md:49`) | — | — | brecha (no se identificó un caso que consulte por SKU/nombre un producto desactivado) |
| RF-01 | Consulta por SKU/nombre sin distinguir caso conserva texto (`spec.md:50`) | `tests/integration/test_repositories.py:609` `test_find_by_sku_ignores_case_and_returns_the_stored_text`; `tests/integration/test_repositories.py:634` `test_find_by_name_ignores_case_and_returns_the_stored_text`; `tests/ai/test_inventory_tools.py:376` `test_find_product_by_sku_matches_without_case` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-01 | Varias coincidencias por nombre se muestran todas (`spec.md:51`) | `tests/integration/test_repositories.py:648` `test_find_by_name_returns_every_match`; `tests/ai/test_inventory_tools.py:334` `test_find_products_by_name_returns_every_match` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-02 | Stock inicial > 0 refleja entrada de stock inicial (`spec.md:74`) | `tests/integration/test_product_service.py:376` `test_register_records_the_loose_initial_stock_as_an_initial_entry`; `tests/integration/test_product_service.py:401` `test_register_records_the_closed_initial_stock_per_presentation` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-02 | Stock inicial total cero no genera movimiento (`spec.md:75`) | `tests/integration/test_product_service.py:483` `test_register_without_initial_stock_creates_no_movement` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-02 | Entrada suelta aumenta las unidades sueltas (`spec.md:76`) | `tests/integration/test_movement_service.py:377` `test_a_loose_entry_updates_the_loose_units_and_records_the_movement` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-02 | Entrada cerrada aumenta el stock cerrado (`spec.md:77`) | `tests/integration/test_movement_service.py:418` `test_a_closed_entry_updates_the_closed_stock_and_records_the_movement` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-02 | Salida suelta/cerrada disminuye el stock correspondiente (`spec.md:78`) | `tests/integration/test_movement_service.py:473` `test_a_loose_exit_updates_the_loose_units_and_captures_the_base_price`; `tests/integration/test_movement_service.py:497` `test_a_closed_exit_updates_the_closed_stock_and_captures_the_price` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-02 | Salida que supera el stock se rechaza (`spec.md:79`) | `tests/integration/test_movement_service.py:956` `test_a_loose_exit_that_exceeds_the_loose_units_is_rejected`; `tests/integration/test_movement_service.py:978` `test_a_closed_exit_that_exceeds_the_closed_stock_is_rejected` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-02 | Stock nunca negativo tras confirmar (`spec.md:80`) | `tests/integration/test_movement_service.py:549` `test_exits_can_leave_a_balance_at_zero_but_never_negative`; `tests/integration/test_schema.py:340` `test_loose_units_cannot_be_negative`; `tests/integration/test_schema.py:353` `test_closed_stock_cannot_be_negative` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-02 | Disponibilidad vigente validada y operaciones simultáneas protegidas (`spec.md:70`) | `tests/concurrency/test_concurrency.py:299` `test_simultaneous_loose_exits_with_insufficient_stock`; `tests/concurrency/test_concurrency.py:337` `test_simultaneous_closed_exits_on_the_same_presentation` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Período en hora local Argentina convertido a UTC; instantes en UTC (`spec.md:96`, `spec.md:97`) | `tests/integration/test_period_filters.py:147` `test_period_bounds_use_local_argentina_time_converted_to_utc`; `tests/integration/test_period_filters.py:174` `test_a_movement_exactly_at_the_period_start_is_included`; `tests/integration/test_period_filters.py:204` `test_a_movement_exactly_at_the_period_end_is_excluded` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Movimiento válido registra tipo, producto, presentación, cantidad, motivo, fecha y hora (`spec.md:135`) | `tests/integration/test_movement_service.py:377` `test_a_loose_entry_updates_the_loose_units_and_records_the_movement`; `tests/integration/test_repositories.py:321` `test_add_movement_keeps_the_reason` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Movimiento sin motivo se rechaza (`spec.md:136`) | `tests/integration/test_repositories.py:265` `test_add_movement_rejects_a_blank_reason`; `tests/integration/test_movement_service.py:627` `test_an_invalid_reason_is_rejected` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Filtros producto/tipo/período combinados con AND (`spec.md:137`) | `tests/integration/test_repositories.py:495` `test_combined_product_and_kind_filters_cannot_return_other_records`; `tests/integration/test_period_filters.py:335` `test_product_kind_and_period_filters_are_combined_with_and` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Criterio: editar o eliminar un movimiento confirmado se rechaza (`spec.md:138`) | `tests/integration/test_repositories.py:346` `test_movement_repository_exposes_no_edit_or_delete_operations` (el contrato del repositorio no expone operaciones de edición ni borrado) | estructural | sin evidencia conductual (ausencia estructural) |
| RF-03 | Apertura resta `N` cerrado, suma `N × factor` sin alterar el total (`spec.md:139`) | `tests/integration/test_aperture_service.py:342` `test_an_aperture_updates_the_balances_and_keeps_the_total_equivalent`; `tests/unit/test_stock_rules.py:322` `test_open_presentations_preserves_total_equivalent` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Apertura sin stock cerrado suficiente se rechaza (`spec.md:140`) | `tests/integration/test_aperture_service.py:449` `test_an_aperture_without_enough_closed_stock_is_rejected` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Apertura de la presentación base (factor 1) se rechaza (`spec.md:141`) | `tests/integration/test_aperture_service.py:420` `test_an_aperture_on_the_base_presentation_is_rejected`; `tests/unit/test_stock_rules.py:288` `test_open_presentations_rejects_base_presentation` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Entrada/salida cerrada sobre la base (factor 1) se rechaza (`spec.md:142`) | `tests/integration/test_movement_service.py:844` `test_a_closed_entry_on_the_base_presentation_is_rejected`; `tests/integration/test_movement_service.py:864` `test_a_closed_exit_on_the_base_presentation_is_rejected` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Apertura sobre producto desactivado se rechaza (`spec.md:143`) | `tests/integration/test_aperture_service.py:482` `test_an_aperture_on_a_deactivated_product_is_rejected` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Compensar una apertura se rechaza (`spec.md:144`) | `tests/integration/test_compensation_service.py:716` `test_an_aperture_compensation_is_rejected_without_changes`; `tests/unit/test_movement_rules.py:859` `test_compensate_rejects_aperture` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Compensar una salida cerrada se rechaza sin modificar stock ni contadores FIFO (`spec.md:145`) | `tests/integration/test_compensation_service.py:674` `test_a_closed_exit_compensation_is_rejected_without_changes`; `tests/unit/test_movement_rules.py:811` `test_compensate_rejects_closed_exit` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Ejemplo FIFO: A(+2), apertura 1, B(+1) → rechaza A por 2 y permite A por 1 (`spec.md:146`) | `tests/unit/test_movement_rules.py:922` `test_compensate_respects_fifo_consumption_example_of_rf_03`; `tests/integration/test_compensation_service.py:456` `test_a_closed_entry_is_limited_by_its_pending_fifo_quantity` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Compensación que excede la cantidad compensable se rechaza (`spec.md:147`) | `tests/integration/test_compensation_service.py:413` `test_successive_compensations_reach_the_total_and_the_excess_is_rejected`; `tests/unit/test_movement_rules.py:881` `test_compensate_rejects_excess_over_compensable_quantity` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Compensación válida crea un movimiento vinculado y conserva ambos (`spec.md:148`) | `tests/integration/test_compensation_service.py:347` `test_a_partial_compensation_reduces_the_closed_stock`; `tests/integration/test_repositories.py:805` `test_compensation_records_are_linked_to_the_original_movement` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Producto desactivado admite compensación histórica válida (`spec.md:149`) | `tests/integration/test_compensation_service.py:788` `test_a_deactivated_product_accepts_a_valid_historical_compensation` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-03 | Requisito general: movimientos confirmados inmutables (`spec.md:99`) | `tests/integration/test_movement_service.py:1216` `test_a_confirmed_movement_is_immutable` (la reasignación de un campo del movimiento confirmado lanza `FrozenInstanceError` y el movimiento persistido no cambia) | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-04 | Total equivalente igual al mínimo → stock bajo (`spec.md:166`) | `tests/unit/test_stock_alerts.py:35` `test_is_low_stock_when_total_equals_minimum` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-04 | Total equivalente menor que el mínimo → stock bajo (`spec.md:167`) | `tests/unit/test_stock_alerts.py:40` `test_is_low_stock_when_total_is_below_minimum` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-04 | Total equivalente mayor que el mínimo → no stock bajo (`spec.md:168`) | `tests/unit/test_stock_alerts.py:45` `test_is_low_stock_is_false_when_total_is_above_minimum` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-04 | Mínimo 10, total 6 → faltante 4 (`spec.md:169`) | `tests/unit/test_stock_alerts.py:126` `test_calculate_shortage_reports_missing_units` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-04 | Mínimo 10, total 12 → faltante 0 (`spec.md:170`) | `tests/unit/test_stock_alerts.py:131` `test_calculate_shortage_is_zero_above_minimum` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-04 | Activo con mínimo 0 y total 0 → stock bajo pero no reposición (`spec.md:171`) | `tests/unit/test_stock_alerts.py:50` `test_is_low_stock_with_zero_minimum_and_zero_stock`; `tests/unit/test_stock_alerts.py:206` `test_low_stock_items_includes_zero_minimum_and_zero_stock` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-04 | Producto desactivado excluido de stock bajo y reposición (`spec.md:172`) | `tests/unit/test_stock_alerts.py:213` `test_low_stock_items_excludes_inactive_items`; `tests/ai/test_inventory_tools.py:486` `test_get_low_stock_products_excludes_inactive_products` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-04 | Consulta de stock bajo/reposición mostrada en la interfaz con datos reales (T023) (`spec.md:171`, `spec.md:172`) | `tests/ui/test_main.py:519` `test_the_app_shows_the_real_data_of_each_query` (parametrizaciones `low_stock` y `replenishment`) | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-05 | Consulta de stock responde con datos reales suelto/cerrado/total (`spec.md:195`) | `tests/ai/test_inventory_tools.py:400` `test_get_product_stock_derives_the_total_equivalent`; `tests/ui/test_main.py:519` `test_the_app_shows_the_real_data_of_each_query` (criterio `stock`) | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-05 | Stock bajo/reposición responde con condición y faltante (`spec.md:196`) | `tests/ai/test_inventory_tools.py:508` `test_get_replenishment_products_requires_a_positive_shortage`; `tests/ai/test_inventory_tools.py:486` `test_get_low_stock_products_excludes_inactive_products` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-05 | Historial con filtros válidos muestra los movimientos correspondientes (`spec.md:197`) | `tests/ai/test_inventory_tools.py:539` `test_get_movement_history_returns_the_full_history_in_order`; `tests/ai/test_inventory_tools.py:614` `test_get_movement_history_combines_the_filters_with_and` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-05 | Antes de registrar se muestra la propuesta y se pide confirmación (`spec.md:198`) | `tests/ai/test_proposal_tools.py:446` `test_each_creation_tool_builds_a_pending_proposal`; `tests/ui/test_main.py:543` `test_the_app_shows_a_pending_proposal_without_confirming` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-05 | Propuesta no confirmada no crea ni modifica datos (`spec.md:199`) | `tests/ui/test_main.py:543` `test_the_app_shows_a_pending_proposal_without_confirming`; `tests/integration/test_end_to_end.py:498` (nota de paso de `test_the_full_flow_keeps_the_inventory_coherent`) | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-05 | Propuesta confirmada y válida ejecuta e informa el resultado (`spec.md:200`) | `tests/ui/test_main.py:651` `test_confirming_a_pending_proposal_executes_it_once`; `tests/integration/test_end_to_end.py:433` `test_the_full_flow_keeps_the_inventory_coherent` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-05 | Propuesta que deja de cumplir una regla se rechaza sin cambios parciales (`spec.md:201`) | `tests/integration/test_proposal_service.py:867` `test_confirming_revalidates_the_stock_that_changed_after_the_proposal`; `tests/ui/test_main.py:731` `test_a_rejected_confirmation_shows_the_error_without_changes` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-05 | Operación que infringe una regla informa el motivo sin confirmar resultado inexistente (`spec.md:202`) | `tests/integration/test_proposal_service.py:843` `test_a_failed_confirmation_leaves_the_proposal_pending`; `tests/ui/test_main.py:731` `test_a_rejected_confirmation_shows_the_error_without_changes` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-05 | Flujo end-to-end integrado de RF-05 (T025) (`spec.md:195`–`spec.md:202`) | `tests/integration/test_end_to_end.py:433` `test_the_full_flow_keeps_the_inventory_coherent`; `tests/integration/test_end_to_end.py:593` `test_two_tools_in_one_message_run_on_the_real_connection` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-06 | Opera sobre una única ubicación de inventario (`spec.md:219`) | — | estructural | sin evidencia conductual (ausencia estructural) |
| RF-06 | No expone usuarios, proveedores, compras ni ventas formales (`spec.md:220`) | — | estructural | sin evidencia conductual (ausencia estructural) |
| RF-06 | Edición o eliminación directa de un confirmado se rechaza (`spec.md:221`) | `tests/integration/test_repositories.py:346` `test_movement_repository_exposes_no_edit_or_delete_operations` | estructural | sin evidencia conductual (ausencia estructural) |
| RF-06 | Corrección de un confirmado mediante compensación, salvo aperturas y salidas cerradas (`spec.md:222`) | `tests/integration/test_compensation_service.py:674` `test_a_closed_exit_compensation_is_rejected_without_changes`; `tests/integration/test_compensation_service.py:716` `test_an_aperture_compensation_is_rejected_without_changes`; `tests/unit/test_movement_rules.py:859` `test_compensate_rejects_aperture` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-07 | Producto nuevo crea su presentación base con factor 1 (`spec.md:244`) | `tests/unit/test_presentation_rules.py:280` `test_create_base_creates_factor_one_presentation`; `tests/integration/test_product_service.py:209` `test_register_creates_an_active_product_with_its_base_presentation` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-07 | Nombre de presentación duplicado se rechaza sin distinguir caso (`spec.md:245`) | `tests/unit/test_presentation_rules.py:53` `test_validate_presentation_name_unique_rejects_duplicate`; `tests/integration/test_presentation_service.py:226` `test_add_rejects_a_duplicate_name` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-07 | Factor menor que 1/no entero o precio ≤ 0 se rechaza (`spec.md:246`) | `tests/unit/test_presentation_rules.py:166` `test_presentation_rejects_zero_factor`; `tests/unit/test_presentation_rules.py:210` `test_presentation_rejects_zero_price`; `tests/integration/test_presentation_service.py:247` `test_add_rejects_an_invalid_factor` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-07 | Factor no modificable con stock cerrado o historial (`spec.md:247`) | `tests/unit/test_presentation_rules.py:340` `test_change_factor_rejects_when_closed_stock_exists`; `tests/integration/test_presentation_service.py:416` `test_change_factor_rejects_a_presentation_with_closed_stock` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-07 | Agregar presentación con stock existente nace con cerrado cero sin alterar saldos (`spec.md:248`) | `tests/integration/test_presentation_service.py:170` `test_add_creates_a_presentation_with_zero_closed_stock`; `tests/integration/test_presentation_service.py:193` `test_add_does_not_change_other_balances` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-07 | Entrada con costo lo almacena sin distribuirlo (`spec.md:249`) | `tests/integration/test_movement_service.py:404` `test_a_loose_entry_without_cost_records_no_cost`; `tests/integration/test_repositories.py:769` `test_movement_keeps_the_optional_cost_and_applied_price` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-07 | Producto desactivado rechaza crear o modificar presentaciones (`spec.md:250`) | `tests/integration/test_presentation_service.py:282` `test_add_rejects_a_deactivated_product`; `tests/integration/test_presentation_service.py:374` `test_change_sale_price_rejects_a_deactivated_product` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-07 | Presentación base única protegida por índice (T016) (`spec.md:232`, `spec.md:244`) | `tests/integration/test_schema.py:819` `test_single_base_index_is_unique_and_partial`; `tests/concurrency/test_concurrency.py:622` `test_concurrent_base_creation_keeps_a_single_base` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | Precio confirmado actualiza el precio vigente (`spec.md:276`) | `tests/integration/test_proposal_service.py:1369` `test_confirming_a_price_update_applies_it_and_records_the_history`; `tests/integration/test_presentation_service.py:376` `test_change_sale_price_updates_the_price_and_records_the_change`; `tests/integration/test_end_to_end.py:643` `test_the_price_change_and_its_history_survive_a_restart`; `tests/ui/test_main.py:954` `test_confirming_a_price_update_applies_it_once` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | Propuesta no confirmada no modifica el precio ni registra historial (`spec.md:277`) | `tests/integration/test_proposal_service.py:1340` `test_creating_a_price_update_does_not_touch_the_price_or_the_history`; `tests/integration/test_end_to_end.py:643` `test_the_price_change_and_its_history_survive_a_restart` (paso de propuesta: `snapshot` y `total_changes` sin cambios, `:708-726`); `tests/ui/test_main.py:927` `test_the_app_shows_the_price_proposal_without_writing` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | La propuesta muestra precio vigente y precio nuevo (`spec.md:278`) | `tests/ui/test_main.py:898` `test_the_app_shows_the_price_update_with_its_prices`; `tests/integration/test_end_to_end.py:643` (render del precio vigente y del nuevo, `:722-723`) | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | El precio anterior se captura al confirmar (`spec.md:279`) | `tests/integration/test_proposal_service.py:1417` `test_confirming_a_price_update_uses_the_price_of_the_confirmation`; `tests/integration/test_presentation_service.py:621` `test_change_sale_price_records_the_immediately_previous_price` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | El cambio confirmado registra producto, presentación, precios, fecha y evento; el alta no registra (`spec.md:280`) | `tests/integration/test_proposal_service.py:1369` (fila cruda persistida con el `event_type` literal, `:1410-1414`); `tests/integration/test_repositories.py:1300` `test_add_price_change_persists_the_readable_event_type`; `tests/integration/test_presentation_service.py:484` `test_registration_and_presentation_changes_record_no_price_change`; `tests/integration/test_end_to_end.py:643` (`:743-745`) | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | El rechazo no deja cambios parciales (`spec.md:281`) | `tests/integration/test_proposal_service.py:1457` `test_confirming_a_price_update_of_a_missing_presentation_is_rejected`, `:1474` `test_confirming_a_price_update_of_an_inactive_product_is_rejected`, `:1504` `test_confirming_a_price_update_with_an_invalid_price_is_rejected`; `tests/integration/test_presentation_service.py:550` `test_change_sale_price_rejects_an_invalid_price`, `:596` `test_change_sale_price_rejects_a_deactivated_product`, `:649` `test_change_sale_price_of_a_missing_presentation_records_nothing` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | El fallo del registro del historial no deja el precio actualizado (`spec.md:282`) | `tests/integration/test_end_to_end.py:837` `test_a_failing_history_rolls_back_and_the_database_stays_usable` (precio intacto, historial vacío y base utilizable); `tests/integration/test_proposal_service.py:1539` `test_a_price_update_rolls_back_when_the_history_fails`; `tests/integration/test_presentation_service.py:567` `test_change_sale_price_rolls_back_when_the_history_fails` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | Existencias y movimientos conservados sin cambios (`spec.md:283`) | `tests/integration/test_presentation_service.py:506` `test_change_sale_price_does_not_touch_balances_or_history`; `tests/integration/test_end_to_end.py:643` (saldos y movimientos intactos antes, después y tras la reapertura, `:746-752`, `:807-815`) | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | `applied_price` de las salidas confirmadas conservado sin cambios (`spec.md:284`) | `tests/integration/test_presentation_service.py:506` (el precio aplicado de la salida confirmada sigue en `900.00`, `:544`) | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | Historial consultado en orden cronológico (`spec.md:285`) | `tests/integration/test_price_history_service.py:224` `test_get_price_history_by_presentation_returns_it_in_order`; `tests/integration/test_repositories.py:1368` `test_list_by_presentation_returns_its_history_in_order`; `tests/integration/test_proposal_service.py:1417` (orden tras dos cambios confirmados, `:1444-1451`) | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | Producto desactivado rechaza la actualización de precio (`spec.md:286`) | `tests/integration/test_proposal_service.py:1474` `test_confirming_a_price_update_of_an_inactive_product_is_rejected`; `tests/integration/test_presentation_service.py:596` `test_change_sale_price_rejects_a_deactivated_product`; `tests/ui/test_main.py:1082` `test_a_deactivated_product_rejects_the_price_confirmation` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | Precio ≤ 0, no finito o no `Decimal` se rechaza (`spec.md:287`) | `tests/integration/test_presentation_service.py:550` `test_change_sale_price_rejects_an_invalid_price` (parametrizado, `:547-548`); `tests/integration/test_proposal_service.py:1504` `test_confirming_a_price_update_with_an_invalid_price_is_rejected`; `tests/integration/test_repositories.py:1346` `test_add_price_change_rejects_an_invalid_price` | conductual | cubierto (reportado; ejecución no verificada independientemente) |
| RF-08 | Editar o eliminar un cambio de precio se rechaza (`spec.md:288`) | `tests/integration/test_repositories.py:1159` `test_price_history_repository_exposes_only_the_append_only_contract`, `:1175` `test_the_repositories_module_never_mutates_a_price_change`, `:1476` `test_price_change_records_are_immutable`. La garantía es la **ausencia** de operaciones de edición y borrado en el contrato y en el módulo, **no** un rechazo en tiempo de ejecución ante SQL directo: el esquema no define disparadores (`tests/integration/test_schema.py:676` `test_the_schema_defines_no_triggers`) | estructural | sin evidencia conductual (ausencia estructural) |

**Estado de RF-08:** los 13 criterios tienen evidencia con `archivo:caso` (T028–T036 implementadas; T037 aporta el extremo a extremo y la persistencia en archivo). La **persistencia tras reiniciar** se demuestra cerrando y reabriendo la base en archivo con la ruta configurada (`tests/integration/test_end_to_end.py:643`) y la **atomicidad** entre el precio y su registro con `tests/integration/test_end_to_end.py:837` (además de `tests/integration/test_proposal_service.py:1539`).
Alcance de lo demostrado: cierre y reapertura de la conexión, **no** un reinicio real del proceso, y **no** idempotencia durable de propuestas (el servicio de propuestas no las persiste). Ninguna sesión ejecutó `pytest` de forma independiente: las filas conductuales llevan `cubierto (reportado; ejecución no verificada independientemente)` y `C13` es **estructural**.
RF-08 **no** se declara terminado sin esas dos demostraciones (persistencia y atomicidad), que ahora existen por evidencia **reportada**.

**Limitación conocida de RF-05 (integración real):** las pruebas de RF-05 usan un modelo falso
(`tasks.md:429`) y por diseño **no realizan llamadas reales a DeepSeek** (`tasks.md:435`). La matriz
**no** acredita la integración real con el proveedor DeepSeek: el identificador del modelo sigue
pendiente (`tasks.md:556`) y esa integración **no está validada**.

## Correcciones previas resueltas

- **I1 — normalización del motivo de los movimientos: RESUELTO** (corrección previa a T012).
  - **Normalización:** el `reason` se normaliza con `normalize_required_text()` antes de persistir; rechaza valores vacíos, cadenas con solo espacios, tabs y saltos de línea. Cierra el hallazgo I1 de T009 (el `CHECK` de SQLite solo eliminaba espacios).
  - **Aplicación:** T012 aplica esta normalización en el servicio de entradas y salidas (`app/services/movements.py`), de modo que los motivos inválidos se rechazan antes de crear el movimiento.
- **I2 — actualización de contadores FIFO sin editar movimientos confirmados: RESUELTO** (corrección previa a T012/T013/T014).
  - **Contrato y persistencia:** existe `MovementRepository.set_consumed_quantity(movement_id, consumed_quantity)` en `app/services/ports.py`, con implementación SQLite en `app/infrastructure/sqlite/repositories.py`. Es un `UPDATE` condicional que solo cambia `consumed_quantity`, exige que el movimiento sea una entrada cerrada y respeta `consumed_quantity + compensated_quantity <= quantity`, preservando los campos históricos y `compensated_quantity`. Se añadió `NotAClosedEntryError(ValueError)`.
  - **Reutilización:** T012 y T013 pueden reutilizar este mecanismo junto con `consume_closed_entries_fifo` del dominio (`app/domain/movements.py`) para consumir entradas cerradas, sin duplicar la lógica FIFO (ver notas en T012 y T013).
  - **Resuelto por T014:** la actualización de `compensated_quantity` requería una operación segura y condicional análoga a `set_consumed_quantity`; T014 la incorporó como `set_compensated_quantity(...)` y actualiza de forma consistente los contadores de compensación (ver nota en T014 y «Decisiones pendientes»). Histórico de la corrección I2: la operación no existía antes de T014 y su ausencia se registró como dependencia de esa tarea.
  - **Pruebas reportadas (no verificadas por ejecución):** el implementer reportó 21 pruebas específicas y 552 en la suite completa. El reviewer no ejecutó `pytest` de forma independiente; por lo tanto, estos resultados no están verificados.
  - **Revisión independiente: APROBADO.** Quedan cuatro hallazgos menores/opcionales sin abrir corrección (ver «Decisiones pendientes»).

## Decisiones pendientes

- **Identificador del modelo DeepSeek:** bloquea la ejecución real en Fase 3/5, pero no las pruebas con modelo falso.
- **Ciclo de vida de propuestas y topología de despliegue:** deferibles; no deben ampliar el alcance sin una decisión posterior.
- **Actualización segura de `compensated_quantity` (T014): RESUELTA por T014.** `set_compensated_quantity` existe en el protocolo (`app/services/ports.py`) y en el repositorio SQLite (`app/infrastructure/sqlite/repositories.py`) como operación condicional análoga a `set_consumed_quantity`, y el servicio actualiza de forma consistente los contadores de compensación. Histórico de la corrección I2: antes de T014 la operación no existía, su ausencia se registró como dependencia y quedó resuelta con el cierre de T014, que también rechaza las compensaciones de `salida_cerrada` (ver la viñeta «Decisión resuelta (MVP)…»).
- **Hallazgos menores m1–m7 y O1–O3 de T014 (opcionales / deuda técnica):** se registran como mejoras opcionales o deuda técnica tras la revisión independiente de T014, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (m1) mensajes del validador compartido de contadores; (m2) docstrings «(T012)» en `app/services/movements.py`; (m3) falta `COMPENSATION` en la parametrización del rechazo del repositorio; (m4) falta una prueba del límite por stock cerrado vigente; (m5) docstring «T009» de `tests/integration/test_repositories.py`; (m6) notas desactualizadas de `tasks.md`; (m7) helper con SQL directo y docstring obsoleto en `tests/integration/test_fifo_counters.py`; (O1) instantáneas redundantes en la prueba de I1; (O2) aserción débil `second.quantity == 3`; (O3) comprobación indirecta de la pendiente. No se diseña aquí ninguna solución.
- **Hallazgos menores de T015 (opcionales / deuda técnica):** se registran tras la revisión independiente de T015, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (1) líneas de 80–82 columnas en los tres archivos nuevos; (2) dependencia `app/schemas/proposals.py` → `app.services.ports` para el tipo de resultado; (3) restricciones numéricas duplicadas entre esquema y dominio (defensivas); (4) tres casos de cobertura pendientes (`float` en `sale_price` de una presentación inicial; rechazo por diccionario con `action` conocida distinta; compensación histórica sobre producto desactivado a través de `ProposalService`). No se diseña aquí ninguna solución.
- **Deuda técnica / hallazgos menores de T016 (opcionales):** se registran tras la revisión independiente de T016, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (1) encabezado de `app/services/movements.py` sin mención de T016; (2) docstrings de `app/services/proposals.py` parcialmente repetidos; (3) `tests/integration/test_schema.py` compara el SQL del índice sin normalizar espacios. No se diseña aquí ninguna solución.
- **Limitaciones M6/M7 de T016 (fuera del alcance aprobado):** `PresentationService.change_sale_price` y `ProductService.deactivate` conservan ventanas de carrera porque leen y escriben sin transacción propia, y su corrección queda fuera del alcance aprobado de T016 (M6); el límite de compensación de movimientos sueltos depende solo de la serialización de escritores, sin contador por movimiento ni restricción específica en la base (M7). Se registran como limitaciones conocidas sin diseñar aquí ninguna solución y sin bloquear el cierre.
- **Limitación stateless de las propuestas (T015):** el servicio de propuestas no mantiene estado interno ni registra o persiste las propuestas (no se usa la tabla `proposals`), de modo que reenviar una propuesta `PENDING` obsoleta no se detecta. **Estado de la concurrencia:** T016, cerrada y aprobada, ya implementó y revisó la protección de concurrencia mediante el helper transaccional con `BEGIN IMMEDIATE` (`app/infrastructure/sqlite/connection.py`) y el índice único parcial `idx_presentations_single_base` (`app/infrastructure/sqlite/schema.py`); esa protección es ajena a esta limitación *stateless*, que permanece. **Limitaciones residuales:** M6 — ventanas de carrera en `PresentationService.change_sale_price` y `ProductService.deactivate`, que leen y escriben sin transacción propia; M7 — el límite de compensación de movimientos sueltos depende de la serialización de escritores, sin contador por movimiento ni restricción específica de base de datos. Ver la viñeta «Decisión resuelta (MVP) — compensaciones de salidas cerradas (T014/T015)».
- **Decisión resuelta (MVP) — compensaciones de salidas cerradas (T014/T015):** las compensaciones de `salida_cerrada` quedan fuera del MVP. Compensarlas repondría `closed_stock` sin revertir los contadores FIFO (`consumed_quantity` y `compensated_quantity`) de las entradas cerradas consumidas, y esa reversión exacta no es posible porque no se persiste la relación salida→entrada. El rechazo reutiliza `NotCompensableError` (`app/domain/movements.py`) y no modifica movimientos, `closed_stock` ni los contadores FIFO; el esquema SQLite no restringe esta relación y no se introduce ninguna restricción nueva. Se reconsiderará cuando se diseñe la trazabilidad exacta de los consumos FIFO. La concurrencia ya está cubierta por T016, cerrada y aprobada: el helper transaccional con `BEGIN IMMEDIATE` y el índice único parcial `idx_presentations_single_base` protegen las confirmaciones simultáneas, de modo que la garantía de rechazo «sin cambios» se mantiene sin carreras concurrentes entre operaciones simultáneas; ver la viñeta «Limitación stateless de las propuestas (T015)».
- **Hallazgos menores de I2 (opcionales):** se registran como mejoras opcionales, sin abrir corrección y sin bloquear T012/T013/T014: (1) reutilizar `validate_stock` en lugar de duplicar validaciones; (2) matizar el docstring del módulo; (3) revisar el rango entero extremo; (4) ajustar la precisión de una aserción.
- **Hallazgos menores M1–M6 de T013 (opcionales):** se registran como mejoras opcionales tras la revisión independiente de T013, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (M1) unificar `_required_presentation` con `_closed_presentation`; (M2) revisar el orden de validación de cantidad/motivo (hoist al estilo T012, o documentar y probar); (M3) acotar una aserción no discriminante del rollback; (M4) actualizar los docstrings de la clase/`__init__`; (M5) reducir la duplicación de pruebas; (M6) cubrir el caso `closed_stock = 0` sin entradas cerradas. No se diseña aquí ninguna solución.
- **Hallazgos menores de T006 (opcionales):** se registran como mejoras opcionales tras la revisión independiente de T006, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (1) aserciones de excepción poco discriminantes en las ramas del valor persistido `"salida_cerrada"` (`tests/unit/test_movement_rules.py:123-124, 165-166, 825-831, 1113-1119`), porque `ApertureNotCompensableError` y `ChainedCompensationError` heredan de `NotCompensableError`; (2) los docstrings del módulo y de `MovementKind` no mencionan la exclusión; (3) nomenclatura del test `..._accepts_entries_and_exits`; (4) `Args` de `compensate` sin mencionar la salida suelta. No se diseña aquí ninguna solución. **Limitación de persistencia:** el esquema SQLite no impide vincular una compensación a una `salida_cerrada` (`app/infrastructure/sqlite/repositories.py`, `app/infrastructure/sqlite/schema.py`); la decisión ya documentada en la viñeta «Decisión resuelta (MVP) — compensaciones de salidas cerradas» y en `plan.md:129` se mantiene, por lo que **T014 valida desde el servicio** que el movimiento original no sea `salida_cerrada`.
- **Hallazgos menores M1–M6 de T017 (deuda técnica menor / opcional):** se registran tras la revisión independiente de T017, sin diseñar aquí ninguna solución y sin bloquear el cierre: (M1) línea de más de 79 columnas en `tests/ai/test_prompts.py`; (M2) la prueba de ausencia de API key/red no discrimina completamente la dependencia de configuración; (M3) fragilidad de las pruebas ante cambios en frases exactas de los prompts; (M4) guarda anti-reglas-de-negocio incompleta; (M5) la redacción sobre consultas SQL podría interpretarse como prohibición de consultas de solo lectura mediante herramientas autorizadas (revisar al conectar las herramientas); (M6) doble import en el test para permitir `importlib.reload`. No se diseña aquí ninguna solución.
- **Hallazgos menores M1–M3 y O1 de T018 (deuda técnica menor / opcional):** se registran tras la revisión independiente de T018, sin diseñar aquí ninguna solución y sin bloquear el cierre: (M1) líneas de más de 79 columnas en `tests/ai/test_state.py`; (M2) prueba redundante que repite aserciones ya cubiertas; (M3) la guarda de imports no incluye `sqlite` ni `app.infrastructure`, por lo que no detectaría una dependencia hipotética de infraestructura; (O1) capacidad discriminante limitada de las pruebas de identidad, porque `TypedDict` es un `dict` en tiempo de ejecución. No se diseña aquí ninguna solución.
- **Hallazgos menores M1–M7 de T019 (deuda técnica menor / opcional):** se registran tras la revisión independiente de T019, sin diseñar aquí ninguna solución y sin bloquear el cierre: (M1) las etiquetas `D2`–`D5` citadas en los docstrings de `app/services/inventory.py` y `app/tools/inventory_tools.py` no están definidas en ningún documento y colisionan con las `D1–D5` de T016 (`tasks.md:292`); (M2) línea de 80 columnas en `tests/ai/test_inventory_tools.py:10`; (M3) `NoInput` (`extra="forbid"`) no valida en runtime porque LangChain omite la validación de esquemas sin campos (inocuo: las consultas globales no reciben argumentos); (M4) doble cómputo en `_AlertItem.as_alert()` (recalcula lo ya calculado por `low_stock_items`/`replenishment_items`), sin duplicar reglas; (M5) `ProductSummary` omite `minimum_stock` y `loose_units` (no exigido por T019); (M6) huecos de cobertura opcionales (producto sin presentaciones; rechazo de argumentos inesperados en `ProductIdInput`/`NoInput`/`MovementHistoryInput`; producto inactivo con datos reales); (M7) guarda de imports textual, que no detectaría una dependencia indirecta de infraestructura (misma deuda que T017/M4 y T018/M3). No se diseña aquí ninguna solución.
- **Hallazgos menores M1–M6 de T020 (deuda técnica menor / opcional):** se registran tras la revisión independiente de T020, sin diseñar aquí ninguna solución y sin abrir una nueva ronda de correcciones: (M1) el parámetro `action` no se usa en el cuerpo de las herramientas de creación (correcto y documentado; un linter marcaría `ARG001`); (M2) el caso `propose_closed_entry` omite `reason` en los campos comparados de una prueba; (M3) la prueba de «otra acción» discrimina el esquema `Literal`, no el cuerpo de la herramienta; (M4) guardas textuales de imports (`inspect.getsource`), que no detectarían una dependencia indirecta; (M5) las pruebas dependen de internals de terceros sin versiones fijadas en `requirements.txt` (verificadas hoy con las versiones instaladas de `langchain_core`/`pydantic`); (M6) al cerrar, registrar la superficie de T020 y la divergencia de `plan.md:148` (`app/domain/proposals.py` inexistente). No se diseña aquí ninguna solución.
- **Hallazgos menores m1–m5 de T021 (deuda técnica menor / opcional):** se registran tras la revisión independiente de T021, sin diseñar aquí ninguna solución y sin abrir una nueva ronda de correcciones: (m1) la prueba del enlazado de herramientas accede a internals de LangChain/LangGraph (`agent.nodes["tools"].bound.tools_by_name`), frágil ante cambios de versión; (m2) la aserción de «sin confirmación» se hace sobre el conjunto inyectado, no sobre el nodo de herramientas; (m3) guarda de imports textual (`inspect.getsource`), que no detectaría dependencias indirectas; (m4) aserciones por subcadena sobre el `ToolMessage` serializado; (m5) `recursion_limit: 10` defensivo innecesario en las pruebas de aborto. No se diseña aquí ninguna solución.
- **Hallazgos menores de T022 (deuda técnica menor / opcional):** se registran tras la revisión independiente de T022, sin diseñar aquí ninguna solución y sin abrir una nueva ronda de correcciones: (1) la prueba de la envoltura no asevera `return_direct` ni `response_format` (el código sí los conserva); (2) `_last_response_text` usa `str(...)` para contenido no textual (aceptable en MVP); (3) el docstring de módulo de `tests/ai/test_state.py` sigue sin mencionar `response` (anterior y fuera de alcance). No se diseña aquí ninguna solución.
- **Hallazgos menores de T023 (deuda técnica menor / opcional):** se registran tras la revisión independiente de T023, sin diseñar aquí ninguna solución y sin abrir una nueva ronda de correcciones: (1) la prueba de integración emite una única llamada de herramienta, por lo que podría no cruzar hilos si el `ToolNode` ejecuta llamadas únicas en el hilo invocador (endurecer con ≥2 llamadas en paralelo); (2) el mensaje de error inyectado en una prueba es idéntico al real de `get_llm()` sin API key (no discrimina si el monkeypatch se aplicó); (3) la guarda de no-confirmación es textual y no detectaría una confirmación indirecta; (4) `describe_proposal` imprime campos `None` (p. ej. `cost: None`); (5) `sqlite3.threadsafety == 3` se verifica en pruebas pero no se usa como guarda en `connect()`. No se diseña aquí ninguna solución.
- **Hallazgos menores de T024 (deuda técnica menor / opcional):** se registran tras la revisión independiente de T024, sin diseñar aquí ninguna solución y sin bloquear el cierre: (1) no se ejercita `NotCompensableError` desde la UI (hueco de cobertura; la implementación sí lo cubre vía `(LookupError, ValueError)`); (2) `describe_proposal` imprime campos `None` (deuda D5, no corregida); (3) guarda textual frágil heredada de T023. No se diseña aquí ninguna solución.
- **Hallazgos menores de T025 (deuda técnica menor / opcional):** se registran tras la revisión independiente de T025, sin diseñar aquí ninguna solución y sin bloquear el cierre: (1) aserciones por subcadena sobre la salida serializada del modelo (frágil ante cambios de formato; patrón ya anotado en T021/T023); (2) `total_changes()` depende del orden actual «validación antes de escritura» (correcto hoy; frágil ante un reordenamiento futuro). No se diseña aquí ninguna solución.
- **Dependencia técnica de T021 (I1) — RESUELTA en T023:** el riesgo de threading de SQLite (conexión de producción `check_same_thread=True` frente a las herramientas del `ToolNode` en hilos) quedó resuelto en el camino de producción: `connect()` ahora acepta `check_same_thread=False` (D1 de T023) y la prueba de integración ejercita una herramienta real vía `ToolNode`. Se mantiene la salvedad de que `check_same_thread=False` **no** garantiza por sí solo la seguridad concurrente (la serialización la aportan SQLite y `BEGIN IMMEDIATE`, con una conexión por sesión).
- **RF-08 (T027–T037) — decisiones técnicas: RESUELTAS** (aprobadas por el usuario; ver `plan.md` «Decisiones resueltas (RF-08)»): (1) **persistencia** en archivo SQLite, con ruta configurable y valor por defecto determinista, independiente del directorio de trabajo (no `:memory:` para el funcionamiento normal); (2) **migración** con `PRAGMA user_version` y migraciones explícitas y controladas; (3) **presentación base** incluida en RF-08 igual que las demás; (4) **`ProposalOutcome`** conservado sin ampliar (ya devuelve la presentación actualizada); (5) **lectura del historial** vía servicio/repositorio y expuesta a la UI, **sin** añadir herramienta al agente; (6) **esquema y migración**: `create_schema` para bases nuevas **y** migración para bases existentes, sin borrar datos y sin reinicializar el inventario. **Implementadas** por T028–T037 (T037 cierra la trazabilidad de la matriz); la verificación es **reportada**, sin ejecución independiente de `pytest`.
- **Observaciones menores de T028 (sin reabrir):** se registran tras la revisión independiente de T028, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (a) el comentario opcional en `.env.example` no se aplicó por permisos (funcionalmente irrelevante: `app/config.py` contempla la variable vacía); (b) observación de robustez de `resolve_database_path()` ante una eventual asignación posterior de `INVENTORY_DB_PATH=""` tras el import. No se diseña aquí ninguna solución.
- **Observaciones menores de T029 (sin reabrir):** se registran tras la revisión independiente de T029, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (O1) la cabecera/docstring de `tests/integration/test_schema.py` no menciona T029/RF-08; (O2) `presentations.sale_price` conserva un CHECK más permisivo preexistente (protegido por las reglas del dominio); (O3) diferencia de ubicación documental en `plan.md:161` y `:163`, reconciliada mediante D1. No se diseña aquí ninguna solución.
- **Observaciones menores de T030 (sin reabrir):** se registran tras la revisión independiente de T030, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (O1) la prueba de `event_type` compara contra la constante, sin discriminar el literal esperado; (O2) la prueba de desempate cronológico no demuestra estrictamente que el orden por `id` sea el responsable del resultado; (O3) el docstring del módulo `repositories.py` no menciona T030 (cosmético). No se diseña aquí ninguna solución.
- **Observaciones menores de T031 (sin reabrir):** se registran tras la revisión independiente de T031, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (O1) duplicación de `_utc_now()` en tres helpers idénticos (`products.py`, `movements.py`, `presentations.py`); (O2) el docstring `Raises` de `change_sale_price` no aclara que cualquier excepción dentro del bloque revierte la transacción; (O3) el desglose exacto «10+1» no es verificable sin Git (cosmético). No se diseña aquí ninguna solución.
- **Observaciones documentales de T032 (sin reabrir):** se registran tras la revisión independiente de T032, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (a) referencias a «nueve acciones» desactualizadas en `test_proposal_service.py` y `test_proposal_tools.py`; (b) el docstring de `app/tools/proposal_tools.py` sigue pendiente y queda fuera del alcance de T032 (corregir en T033); (c) la ruta fail-closed para confirmar una propuesta de precio aún no está cubierta por una prueba específica (abordarla en T033). No se diseña aquí ninguna solución.
- **Deuda documental de T033 (fuera de alcance):** en `tests/integration/test_proposal_service.py` persiste la referencia a «nueve acciones» y el docstring que indica que la confirmación del cambio de precio llega en T033 o se rechaza con `ProposalError`; no se corrige en T033 (archivo no autorizado).
- **Deuda documental y observaciones de T034 (sin reabrir):** se registran tras la revisión independiente de T034, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (I1) `plan.md:179` no incluye `app/services/price_history.py` en la lista de `services/`; (O1) el docstring de `app/services/presentations.py` no menciona `get_presentation`; (O2) aserción redundante sobre `product_id`; (O3) la prueba de no-escritura no cubre `get_presentation`; (O4) duplicación del patrón `_required_*`; (O5) el valor devuelto por los helpers se descarta (solo valida existencia). No se diseña aquí ninguna solución.
- **Observaciones de T035 (sin reabrir):** se registran tras la revisión independiente de T035, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (O2) `describe_result` no formatea el precio con dos decimales; (O3) falta una prueba directa de `describe_price_history`; (O4) en la rama sin cambio de precio, un turno confirmado no imprime `describe_proposal`; (O5) `_format_price` redondea valores con más de dos decimales. No se diseña aquí ninguna solución.
- **Deuda documental de T036 (sin corregir):** se registra sin abrir una nueva ronda de correcciones y **sin** editarse aquí: (a) la referencia desfasada de la matriz `tasks.md:738` cita `tests/integration/test_repositories.py:346` para `test_movement_repository_exposes_no_edit_or_delete_operations`, y el caso está en `:405`; (b) la nota obsoleta `tasks.md:762` («los 13 criterios figuran como pendientes») tras T031–T035. No se diseña aquí ninguna solución.
- **Observaciones de T036 (sin reabrir):** se registran tras la revisión independiente de T036, sin abrir una nueva ronda de correcciones y sin bloquear el cierre: (O1) duplicación parcial de la prueba de presentación inexistente; (O2) C5/C8/C9 cubiertos por evidencia preexistente, sin casos nuevos; (O3) etiquetas de criterio incompletas en algunos rechazos; (O4) `_FailingPriceHistoryRepository` duplicado en dos archivos (sin `conftest`); (O5) referencias obsoletas a «nueve acciones»; (O6) C13 comprobado de forma estructural (AST/texto), sin triggers. No se diseña aquí ninguna solución.
- **Deuda documental de la matriz (T037; registrada sin corregir):** las filas de RF-07 (`tasks.md:742-747`) arrastran referencias desfasadas por las inserciones de T031/T036: `tasks.md:742` cita `tests/integration/test_presentation_service.py:226` (el caso está en `:261`); `tasks.md:743` cita `:247` (está en `:282`); `tasks.md:744` cita `:416` (está en `:679`); `tasks.md:745` cita `:170` y `:193` (están en `:205` y `:228`); `tasks.md:746` cita `tests/integration/test_repositories.py:769` (está en `:843`); `tasks.md:747` cita `:282` y `:374` (están en `:317` y `:596`). La viñeta **Deuda documental de T036** cita `tests/integration/test_repositories.py:346` y afirma que `test_movement_repository_exposes_no_edit_or_delete_operations` está en `:405`, cuando está en `:420`, y su referencia a `tasks.md:762` apunta a la nota de estado de RF-08, que ahora está en `tasks.md:763`. Fuera del alcance de T037: no se corrigen aquí. No se diseña aquí ninguna solución.
- **Pendiente documental (D9):** las referencias obsoletas a M6 en `tasks.md:545` y `plan.md:145` siguen pendientes de actualización (autorización documental aparte); **no** se editan aquí.
- **RF-08 — condición de terminado:** RF-08 exige demostrar **persistencia tras reiniciar** sobre la misma base en archivo (T028, T037) y **atomicidad** entre la actualización del precio y el registro del historial (T031, T036, T037). Ambas demostraciones existen por evidencia **reportada** (sin ejecución independiente de `pytest`): `tests/integration/test_end_to_end.py:643` (cierre y reapertura con la ruta configurada, sin reinicio real del proceso) y `tests/integration/test_end_to_end.py:837` (rollback con la base utilizable después del fallo). Las condiciones antiguas —`connect()` en `:memory:` y migración de bases existentes— quedaron resueltas por T028/T029 (`app/ui/main.py:613-625`; `app/infrastructure/sqlite/schema.py:341-385`).
- **RF-08 — contradicciones abiertas con el código actual (a resolver en implementación):** `app/schemas/proposals.py:19-20` y `:50-51` excluyen explícitamente el cambio de precio; `tests/ai/test_proposal_tools.py:688-695` fija 10 herramientas y `:434` «nine actions»; `tests/integration/test_schema.py:194-205` fija 4 tablas. La especificación aprobada (`spec.md` RF-08) pasa a ser la fuente de verdad y esas pruebas deberán actualizarse al implementar.
- **Deuda documental de T037 (aceptada; no bloqueante):** (I-A) `tasks.md:461` — el bloque histórico de T027 conserva una afirmación desactualizada sobre el estado de T028–T037 (T028–T036 se cerraron después); (I-B) `tasks.md:823` — la viñeta «RF-08 — contradicciones abiertas…» conserva contradicciones ya resueltas por T032/T033 (`app/schemas/proposals.py:19-22` ya incluye `cambio_precio`; las superficies fijadas en pruebas quedaron desactualizadas: `tests/ai/test_proposal_tools.py:467`, `tests/ai/test_supervisor.py:499`, `tests/ai/test_inventory_agent.py:400-401`). No se corrigen; no son bloqueantes funcionales.
- **Alcance de «reinicio» (T037):** el criterio `tasks.md:647` («reiniciar el proceso») se demuestra como **cierre y reapertura de la conexión y reconstrucción de la composición** sobre la misma base en archivo, **no** como un reinicio real de proceso, como ya aclara `tasks.md:765`.

## Estrategia de ejecución

1. Ejecutar las tareas en orden, una por vez, dentro de su fase.
2. Al terminar una tarea, ejecutar sus pruebas y verificar sus criterios de aceptación.
3. Detenerse si una prueba falla o si una decisión pendiente bloquea la tarea; no avanzar sin resolverlo.
4. Revisar el resultado antes de continuar y esperar aprobación explícita al finalizar cada fase.
5. No modificar `.venv`, `.env` ni instalar dependencias desde el agente.
6. No realizar cambios en tareas posteriores como parte de la implementación de una tarea individual, salvo autorización explícita.
