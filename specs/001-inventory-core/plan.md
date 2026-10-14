# Plan técnico: núcleo de inventario

## Estado

Aprobado.

## Objetivo técnico

Implementar el MVP de inventario como una aplicación Python modular con Streamlit (interfaz), LangGraph (orquestación), LangChain (modelo y herramientas), DeepSeek (proveedor LLM), SQLite mediante `sqlite3` (persistencia), Pydantic (validación y configuración) y pytest (pruebas).

Las reglas de negocio son deterministas y residen fuera del LLM. Los agentes y las herramientas no escriben directamente en la base de datos: delegan en servicios de aplicación que validan y ejecutan únicamente operaciones confirmadas.

## Stack acordado

| Componente        | Tecnología                | Responsabilidad                                               |
| ----------------- | ------------------------- | ------------------------------------------------------------- |
| Lenguaje          | Python                    | Lógica, interfaz, persistencia, agentes y pruebas.            |
| Interfaz          | Streamlit                 | Conversación, consultas, propuestas y confirmación explícita. |
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

## Esquema SQLite

- `products`: `id`, `sku` (único), `name`, `base_unit`, `minimum_stock` (INTEGER), `loose_units` (INTEGER), `active`.
- `presentations`: `id`, `product_id`, `name`, `factor` (INTEGER), `sale_price` (TEXT Decimal), `closed_stock` (INTEGER).
- `movements`: `id`, `product_id`, `presentation_id`, `kind`, `quantity` (INTEGER), `reason`, `occurred_at`, `cost` (TEXT, opcional), `applied_price` (TEXT, opcional), `is_initial_stock`, `original_movement_id`, `consumed_quantity` (INTEGER), `compensated_quantity` (INTEGER).
- `proposals`: `id`, `action_type`, `data`, `status`, `created_at`.

Los nombres de tablas y columnas se expresan en inglés, conforme al código en inglés de la constitución (§4) y a los nombres del dominio (`Product`, `Presentation`, `ClosedEntry`). Los valores persistidos de `movements.kind` se mantienen en español (`entrada_cerrada`, `entrada_suelta`, `salida_cerrada`, `salida_suelta`, `apertura`, `compensacion`), coherentes con `MovementKind` del dominio.

Se crea como esquema nuevo; no hay datos previos que migrar.

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
  ui/  main.py
tests/  unit/  integration/  concurrency/  ai/
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

## Fases

- Fase 0: esqueleto, configuración y scaffolding del proveedor (sin credenciales).
- Fase 1: dominio puro + persistencia SQLite (sin LLM).
- Fase 2: servicios, confirmación, apertura, compensaciones y concurrencia (sin LLM).
- Fase 3: herramientas, agentes y grafo (requiere identificador de modelo y alcance de agentes).
- Fase 4: interfaz Streamlit con confirmación explícita.
- Fase 5: integración y validación RF-01 a RF-07.

## Decisiones pendientes

- Identificador del modelo DeepSeek (antes de Fase 3).
- Ciclo de vida de propuestas y topología de despliegue (deferibles).
