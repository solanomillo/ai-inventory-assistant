# Convenciones visuales de la interfaz

Guía breve para las secciones Streamlit de RF-09. Complementa `AGENTS.md` y `docs/constitution.md`; no los sustituye.

## Tema y configuración

- El tema es nativo de Streamlit y vive en `.streamlit/config.toml` (base clara, paleta azul/neutra). No se usa CSS personalizado, `unsafe_allow_html` ni `st.html`.
- `st.set_page_config()` se ejecuta al inicio de `main()`: título, icono Material, `layout="wide"` y sidebar expandido.

## Navegación

- Las siete etiquetas de `SECTION_LABELS` son fijas: no cambiar textos, orden ni la sección predeterminada («Asistente»).
- El sidebar contiene navegación, identidad de la app y, como máximo, filtros globales. El contenido principal va en el área principal.

## Jerarquía y composición

- `st.header` para la sección; `st.subheader` para bloques; `st.caption` para contexto auxiliar.
- Agrupar con `st.container(border=True)`. Evitar divisores; usar `st.space` solo si es imprescindible.
- Máximo cuatro columnas. Para grupos de controles, `st.container(horizontal=True)`.

## Datos y formato

- KPIs con `st.metric` y datos reales; nunca valores inventados.
- Tablas con `st.dataframe(..., hide_index=True, width="stretch")` a partir de listas de diccionarios. Mantener el orden del servicio, no truncar y conservar el aviso C8. La reordenación por clic de `st.dataframe` es **local**: no persiste, no altera el orden inicial del servicio ni el aviso C8.
- Importes: `format_money`. Fechas: `format_instant`. Cantidades: `format_quantity`. No introducir formatos alternativos.
- Estados: `st.info` para vacío, `st.error` para error y `st.warning` para validación. Un dato ausente no se presenta como cero.
- Estados de producto: en las celdas de `st.dataframe` **no** se interpreta Markdown, así que el estado se muestra como texto plano (`Activo`/`Inactivo`). Los badges Markdown (`:green-badge[Activo]`, `:gray-badge[Inactivo]`) se reservan para contextos que sí los renderizan, como la cabecera del detalle. Sin emojis ni CSS.

## Accesibilidad y responsive

- Etiquetas visibles y claras; sentence casing. Iconos Material (`:material/nombre:`) con moderación.

## Pruebas

- `AppTest` para comportamiento (`app.metric`, `app.dataframe`, etc.). Colores y responsive se validan en el checklist manual.
- Conservar `.confirm(` en 1, sin escrituras y sin imports de servicios/dominio/infraestructura en `app/ui/sections/**`.
- Las vistas de solo lectura **no** usan `st.session_state`.
- Cuando una tarea lo requiera (T052, descarte local), el uso se limitará al estado de sesión de Streamlit, sin persistencia ni cambios de dominio, y se documentará en su tarea.

## Verificación visual de T057

- **Fecha:** `2026-10-20`.
- **No se abrió la aplicación en un navegador.** No hay capturas de pantalla y no se midieron píxeles ni contraste. La verificación visual real queda **pendiente**.

| Comprobación | Estado | Evidencia |
| --- | --- | --- |
| Tema nativo claro/neutro definido en `.streamlit/config.toml` | Comprobado por inspección estática | `.streamlit/config.toml` |
| Sin CSS personalizado, `unsafe_allow_html` ni `st.html` | Comprobado por inspección estática | Revisión del código de la interfaz |
| `st.set_page_config()` como primera llamada `st.*` | Comprobado por inspección estática | `app/ui/main.py` |
| Siete etiquetas de navegación y «Asistente» por defecto | Comprobado por inspección estática y pruebas | `app/ui/main.py`; pruebas de interfaz |
| Métricas con datos reales y catálogo vacío sin métricas | Comprobado por pruebas | Pruebas de interfaz |
| Seis tablas con `hide_index=True` y `width="stretch"` | Comprobado por inspección estática | `app/ui/sections/products.py`, `product_detail.py`, `movements.py` y `price_history.py` |
| Contenedores y `caption` del sidebar | Comprobado por inspección estática | `app/ui/main.py` |
| Suite automatizada en verde | **Reportado por el implementer**, no verificado por el reviewer ni el coordinador | 137 en `tests/ui/test_main.py`; 163 en `tests/ui`; 1162 en la suite completa |
| Render real a 1440, 1024 y 768 px | Pendiente de navegador | Sin capturas ni medición |
| Desbordes horizontales en las tres anchuras | Pendiente de navegador | Sin capturas ni medición |
| Contraste de texto, badges y estados | Pendiente de navegador | Sin medición de contraste |
| Badges renderizados como badges en la cabecera del detalle | Pendiente de navegador | Sin capturas |
| Tablas: anchos, truncado visual y celdas vacías frente a cero | Pendiente de navegador | Sin capturas |
| Sidebar e icono | Pendiente de navegador | Sin capturas |
| Estados vacío, error y advertencia | Pendiente de navegador | Sin capturas |
| Contenedores vacíos | Pendiente de navegador | Sin capturas |

### Checklist manual pendiente

1. Abrir la aplicación a **1440 px** y revisar navegación, jerarquía, tablas y sidebar.
2. Repetir a **1024 px** con las mismas comprobaciones.
3. Repetir a **768 px** y confirmar que no hay desbordes horizontales.
4. Verificar el contraste de texto, badges y mensajes de estado.
5. Confirmar que los badges se renderizan como badges en la cabecera del detalle y que las celdas de `st.dataframe` muestran el estado como texto plano.
6. Revisar las tablas: anchos, truncado visual y distinción entre celda vacía y cero.
7. Revisar el sidebar y el icono de la aplicación.
8. Revisar los estados vacío, error y advertencia, y los contenedores vacíos.

La verificación visual **no** está completa hasta ejecutar este checklist en un navegador y registrar sus resultados.
