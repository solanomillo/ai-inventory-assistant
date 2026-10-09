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
- `stock_minimo` cero o positivo, ≤2 decimales.
- `precio_venta` mayor que cero.
- `stock_actual` saldo materializado (detalle interno, no funcional).
- `activo` booleano.

### Movimiento

- `id` interno.
- `producto_id`.
- `tipo` entrada o salida.
- `cantidad` mayor que cero, ≤2 decimales.
- `motivo` obligatorio.
- `fecha_utc` instante automático en UTC.
- `movimiento_original_id` opcional (compensación).
- `es_stock_inicial` booleano (entrada inicial).

Invariantes: `es_stock_inicial` implica `tipo=entrada` sin original; un movimiento con original es compensatorio.

### Compensación

Es un movimiento compensatorio: tipo opuesto al original, vinculado a un original no compensatorio, con suma de valores absolutos que no supera la cantidad pendiente, sin producir stock negativo.

### Propuesta pendiente

- `id`.
- `tipo_accion` (alta, modificación, desactivación, entrada, salida, compensación).
- `datos` (payload validado).
- `estado` pendiente, confirmada o rechazada.

Una propuesta pendiente no crea movimientos ni modifica stock.

## Representación de cantidades, stock y precio

- Cantidades de movimiento, `stock_actual` y `stock_minimo`: enteros en centésimas (`int`) en dominio, servicios, repositorios y esquemas. SQLite usa columnas `INTEGER`. Pydantic valida ≤2 decimales y los servicios convierten con `to_cents()`/`from_cents()`.
- Precio de venta: `Decimal` exacto en Python y `TEXT` canónico en SQLite; la especificación no define precisión decimal para el precio.

## Configuración de DeepSeek

- `config.py`: `DEEPSEEK_API_KEY` y `DEEPSEEK_MODEL` desde entorno.
- `services/llm.py`: `get_llm()` → `ChatDeepSeek(model, api_key, temperature=0)`.
- `.env.example` sin credenciales.

Decisiones pendientes documentadas: identificador del modelo DeepSeek y zona horaria concreta de Argentina.

## Persistencia, transacciones y concurrencia

- Confirmación en una única transacción: `BEGIN IMMEDIATE` → leer propuesta/producto/disponibilidad vigente → revalidar reglas → insertar movimiento, actualizar saldo, marcar propuesta → `COMMIT`; ante fallo, `ROLLBACK` total.
- Las operaciones concurrentes no pueden confirmarse sobre disponibilidad obsoleta.
- Sin stock negativo ni cambios parciales.

## Estructura de directorios

```text
app/
  config.py
  services/  llm.py  ports.py  products.py  inventory.py  movements.py  proposals.py
  domain/    products.py  stock.py  movements.py  proposals.py
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

- Reglas de negocio: unitarias de dominio.
- Persistencia SQLite: integración (unicidad, inmutabilidad, filtros, períodos).
- Herramientas: devuelven datos reales o crean propuestas, sin escribir movimientos.
- Agentes y supervisor: modelo falso determinista, sin DeepSeek real.
- Confirmación y compensación: propuesta editable sin efectos; confirmación revalida; compensación excedente rechazada.
- Concurrencia: salidas/compensaciones simultáneas sin stock negativo ni cambios parciales.
- Errores de configuración/API: `get_llm()` sin key lanza error.
- Validación RF-01 a RF-06 con trazabilidad a pruebas.

## Fases

- Fase 0: esqueleto, configuración y scaffolding del proveedor (sin credenciales).
- Fase 1: dominio puro + persistencia SQLite (sin LLM).
- Fase 2: servicios, confirmación, compensaciones y concurrencia (sin LLM).
- Fase 3: herramientas, agentes y grafo (requiere identificador de modelo y alcance de agentes).
- Fase 4: interfaz Streamlit con confirmación explícita.
- Fase 5: integración y validación RF-01 a RF-06.

## Decisiones pendientes

- Identificador del modelo DeepSeek (antes de Fase 3).
- Zona horaria concreta de Argentina (antes de consultas por período).
- Ciclo de vida de propuestas y topología de despliegue (deferibles).
