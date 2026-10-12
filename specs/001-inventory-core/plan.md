# Plan técnico: núcleo de inventario

## Estado

Aprobado.

## Objetivo técnico

Implementar el MVP de inventario como una aplicación Python modular con Streamlit (interfaz), LangGraph (orquestación), LangChain (modelo y herramientas), DeepSeek (proveedor LLM), SQLite mediante `sqlite3` (persistencia), Pydantic (validación y configuración) y pytest (pruebas).

Las reglas de negocio son deterministas y residen fuera del LLM. Los agentes y las herramientas no escriben directamente en la base de datos: delegan en servicios de aplicación que validan y ejecutan únicamente operaciones confirmadas.

## Stack acordado

| Componente | Tecnología | Responsabilidad |
| --- | --- | --- |
| Lenguaje | Python | Lógica, interfaz, persistencia, agentes y pruebas. |
| Interfaz | Streamlit | Conversación, consultas, propuestas y confirmación explícita. |
| Orquestación | LangGraph | Flujo Supervisor → Inventory Agent. |
| IA y herramientas | LangChain | Adaptador de modelo y definición de herramientas. |
| LLM | DeepSeek API | Interpretación de lenguaje natural. |
| Persistencia | SQLite mediante `sqlite3` | Datos del MVP y transacciones. |
| Validación | Pydantic | Esquemas de entradas, propuestas y resultados en los límites. |
| Pruebas | pytest | Unitarias, integración, concurrencia y de agentes. |

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
- `sku` único e inmutable.
- `nombre` obligatorio, recortado, no vacío.
- `unidad_base` nombre de la unidad discreta (botella, paquete, unidad).
- `stock_minimo` entero mayor o igual que cero, en unidades base.
- `unidades_sueltas` entero mayor o igual que cero.
- `activo` booleano.

Sin `precio_venta`: el precio vive en las presentaciones.

### Presentación

- `id` interno.
- `producto_id`.
- `nombre` único dentro del producto.
- `factor` entero mayor o igual que 1 (equivalencia en unidades base).
- `precio_venta` Decimal mayor que cero.
- `stock_cerrado` entero mayor o igual que cero (solo relevante si `factor > 1`).

La unidad base es la presentación con `factor = 1`. Un producto vendido solo por unidad tiene una única presentación (`factor = 1`). El `factor` es inmutable cuando esa presentación tiene `stock_cerrado > 0` o movimientos históricos.

### Stock compuesto

- `unidades_sueltas` (producto) y `stock_cerrado` (por presentación) son los únicos saldos persistidos.
- `total_equivalente = unidades_sueltas + Σ(stock_cerrado_p × factor_p)`, derivado, nunca persistido.
- `stock_minimo`, `stock bajo` y `faltante` se evalúan sobre `total_equivalente`.

### Movimiento

- `id` interno.
- `producto_id`.
- `presentacion_id` (referencia a la presentación afectada; para sueltas, la unidad base de factor 1).
- `kind`: `entrada_cerrada`, `entrada_suelta`, `salida_cerrada`, `salida_suelta`, `apertura`, `compensacion`.
- `cantidad` entero mayor que cero.
- `motivo` obligatorio.
- `fecha_utc` instante automático en UTC.
- `costo` opcional (Decimal ≥ 0), solo entradas.
- `precio_aplicado` (Decimal > 0), solo salidas, snapshot al confirmar.
- `es_stock_inicial` booleano (entradas de stock inicial).
- `movimiento_original_id` opcional (solo compensaciones).

Para `entrada_cerrada`, además: `cantidad_consumida` y `cantidad_compensada` (enteros ≥ 0) para el consumo FIFO.

### Compensación

Es un movimiento de `kind = compensacion`, opuesto al original, vinculado a un original no compensatorio, con límite de cantidad compensable. Las aperturas no son compensables.

## Representación de cantidades, stock, precio y costo

- Cantidades de inventario, stock (suelto y cerrado) y factor de presentación: **enteros** (`int`). SQLite usa columnas `INTEGER`.
- Precio de venta y costo: **`Decimal` exacto** en Python, almacenado como `TEXT` canónico en SQLite. La especificación no define precisión decimal para precio ni costo.
- No se usan `float` para cálculos monetarios ni se introduce una representación en centésimas para cantidades.

## Configuración de DeepSeek

- `config.py`: `DEEPSEEK_API_KEY` y `DEEPSEEK_MODEL` desde entorno.
- `services/llm.py`: `get_llm()` → `ChatDeepSeek(model, api_key, temperature=0)`.
- `.env.example` sin credenciales.

Decisiones pendientes documentadas: identificador del modelo DeepSeek y zona horaria concreta de Argentina.

## Persistencia, transacciones y concurrencia

- Confirmación en una única transacción: `BEGIN IMMEDIATE` → leer propuesta/producto/disponibilidad vigente → revalidar reglas → aplicar cambios → `COMMIT`; ante fallo, `ROLLBACK` total.
- La **apertura** actualiza `stock_cerrado` y `unidades_sueltas` en la misma transacción y consume FIFO las `entrada_cerrada` correspondientes.
- Las `salida_cerrada` y las compensaciones consumen y actualizan los contadores FIFO dentro de la misma transacción.
- Las operaciones concurrentes no pueden confirmarse sobre disponibilidad obsoleta.
- Sin stock negativo ni cambios parciales.

## Esquema SQLite

- `productos`: `id`, `sku` (único), `nombre`, `unidad_base`, `stock_minimo` (INTEGER), `unidades_sueltas` (INTEGER), `activo`.
- `presentaciones`: `id`, `producto_id`, `nombre`, `factor` (INTEGER), `precio_venta` (TEXT Decimal), `stock_cerrado` (INTEGER).
- `movimientos`: `id`, `producto_id`, `presentacion_id`, `kind`, `cantidad` (INTEGER), `motivo`, `fecha_utc`, `costo` (TEXT, opcional), `precio_aplicado` (TEXT, opcional), `es_stock_inicial`, `movimiento_original_id`, `cantidad_consumida` (INTEGER), `cantidad_compensada` (INTEGER).
- `propuestas`: `id`, `tipo_accion`, `datos`, `estado`, `instante_creacion`.

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
- Persistencia SQLite: integración (unicidad, inmutabilidad, filtros por producto/kind/período).
- Herramientas: devuelven datos reales (stock suelto, cerrado y total) o crean propuestas (incluida apertura), sin escribir movimientos.
- Agentes y supervisor: modelo falso determinista, sin DeepSeek real.
- Confirmación y compensación: propuesta editable sin efectos; confirmación revalida; compensación que excede la cantidad compensable se rechaza; apertura no compensable.
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
- Zona horaria concreta de Argentina (antes de consultas por período).
- Ciclo de vida de propuestas y topología de despliegue (deferibles).
