# Convenciones visuales de la interfaz

Guía breve para las secciones Streamlit de RF-09. Complementa `AGENTS.md` y `docs/constitution.md`; no los sustituye.

## Tema y configuración

- El tema es nativo de Streamlit y vive en `.streamlit/config.toml` (base oscura, paleta azul profesional con acentos turquesa). No se usa CSS personalizado, `unsafe_allow_html` ni `st.html`.
- Paleta vigente: fondo `#061826`, paneles secundarios `#0D2B45`, texto `#E8F4FF`, primario `#0F766E`, enlaces `#5EEAD4` y bordes `#1E4E6B`. Código sobre `#08243A` con texto `#D8F3F0`.
- Sidebar con sección propia `[theme.sidebar]`: fondo `#04121F`, secundario `#0A2236`, texto `#E8F4FF`, bordes `#1E4E6B` y primario `#0F766E`.
- Tablas: borde `#1E4E6B`, cabecera `#0B3A52` con texto `#E8F4FF`, para conservar la legibilidad de `st.dataframe`.
- Radio medio (`baseRadius`/`buttonRadius`) y bordes visibles de widgets y sidebar (`showWidgetBorder`/`showSidebarBorder`).
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

## Nota de T064 (2026-10-24)

El tema nativo pasó de claro/neutro a oscuro azul (turquesa como primario, sidebar y tablas con paleta propia). El usuario confirmó que la interfaz se ve bien y que la validación visual manual no encontró problemas; no se registró una revisión individual por pantalla. Las pruebas AppTest verifican la declaración del tema y la ausencia de CSS. T064 está cerrada y aprobada; sin commit; cierre documental registrado.

## Verificación visual inicial de T057 (2026-10-20)

**Estado: parcial.** Esta ronda cubrió navegación y estados vacíos; no cubrió datos, confirmación ni contraste.

### Entorno y alcance

- **Fecha:** `2026-10-20`.
- **Commit de implementación:** `cb78d50` (según la evidencia aportada; no verificado mediante Git en esta sesión).
- **Aplicación:** `AI Inventory Assistant`, servida localmente en `http://127.0.0.1:8501/`.
- **Navegador:** Chromium `155.0.0.0`, controlado mediante Chrome DevTools MCP; el MCP está configurado para usar el ejecutable de Brave.
- **Sistema operativo:** Windows `NT 10.0` x64 (`Win32`), idioma `es-ES`.
- **Viewports:** `1440×900`, `1024×768` y `768×1024`.
- **Alcance:** etiquetas de navegación de las siete secciones y contenido de Productos, Operaciones, Propuestas pendientes, Movimientos, Historial de precios y Asistente. La sección Inicio no se revalidó en esta ronda.

### Resultados observados

- Las siete etiquetas de navegación permanecieron visibles en los tres viewports.
- No se observaron desbordamientos horizontales, solapamientos visibles ni truncamientos de texto en los estados inspeccionados.
- No se detectaron excepciones DOM, errores visibles ni errores o warnings de consola.
- Los estados vacíos se mostraron de forma explícita: catálogo sin productos, sin propuestas pendientes y sección Operaciones pendiente de implementación.
- Los datos ausentes no se presentaron como cero.
- A `768 px`, la barra lateral permaneció expandida y el título principal se distribuyó en dos líneas sin recorte.
- No se observaron controles inutilizables. En el Asistente, el botón de envío está deshabilitado mientras el campo está vacío.

| Sección | 1440×900 | 1024×768 | 768×1024 | Defectos observados |
| --- | --- | --- | --- | --- |
| Productos | Estado vacío explícito; controles visibles | Sin desbordamiento | Sin desbordamiento; título en dos líneas | Ninguno |
| Operaciones | Aviso de pendiente | Aviso de pendiente | Aviso de pendiente | Ninguno |
| Propuestas pendientes | Estado vacío explícito | Estado vacío explícito | Estado vacío explícito | Ninguno |
| Movimientos | Catálogo vacío; sin controles ni tabla | Igual | Igual | Ninguno |
| Historial de precios | Catálogo vacío; sin controles ni tabla | Igual | Igual | Ninguno |
| Asistente | Chat visible; sin mensajes | Igual | Igual | Ninguno |

### Comprobaciones no verificables

La base de datos estaba vacía. No pudieron verificarse:

- tablas con datos, anchos de columna, celdas vacías frente a cero y truncado en tablas;
- badges `Activo`/`Inactivo` y su representación en la cabecera del detalle;
- búsqueda, filtros y resultados con datos;
- propuestas reales, acciones y estados;
- movimientos e historial de precios con eventos;
- confirmación de propuestas y flujo conversacional del Asistente.

**Contraste:** no medido. No se declara aprobado.

### Evidencias

Capturas temporales, fuera del repositorio:

```text
C:\Users\julio\AppData\Local\Temp\opencode\
```

- `t057-1440x900.png`, `t057-1024x768.png`, `t057-768x1024.png`
- `t057-productos-{1440x900,1024x768,768x1024}.png`
- `t057-operaciones-{1440x900,1024x768,768x1024}.png`
- `t057-propuestas-{1440x900,1024x768,768x1024}.png`
- `t057-movimientos-{1440x900,1024x768,768x1024}.png`
- `t057-historial-precios-{1440x900,1024x768,768x1024}.png`
- `t057-asistente-{1440x900,1024x768,768x1024}.png`

### Conclusión

La validación visual inicial de T057 (2026-10-20) quedó **INCOMPLETA**: los estados disponibles no mostraban defectos, pero faltaban las comprobaciones con datos y la medición de contraste. Esta conclusión es histórica; la validación complementaria del 2026-10-23 fue aprobada por revisión independiente.

## Validación complementaria de T057 (2026-10-23)

**Estado: validación combinada completa con limitaciones aceptadas; revisión independiente APROBADO.**

- **Instancia:** `http://localhost:8502/`.
- **Base:** `C:\Users\julio\AppData\Local\Temp\opencode\t057-vigente\inventory.sqlite3`.
- **Viewports:** `1440×900`, `1024×768`, `768×1024`.
- **Evidencias:** capturas en `C:\Users\julio\AppData\Local\Temp\opencode\t057-vigente-*.png`.

**Alcance de la validación combinada:** el estado final de T057 resulta de **combinar** la ronda inicial del `2026-10-20` y la ronda complementaria del `2026-10-23`. No es una matriz exhaustiva de todos los estados en todos los viewports: cada sección se cubrió en los viewports y estados que se detallan a continuación, y las celdas sin cobertura se declaran como limitación residual en lugar de darse por verificadas. La evidencia del `2026-10-20` se conserva sin cambios.

### Cobertura combinada por sección y viewport

| Sección | 1440×900 | 1024×768 | 768×1024 | Limitación residual |
| --- | --- | --- | --- | --- |
| Inicio | Observado y capturado | No capturado | No capturado | La validación de Inicio se limita a 1440 px; no se capturaron 1024 ni 768 |
| Productos | Observado y capturado | Observado y capturado | Observado y capturado | Sin limitación residual de viewport |
| Detalle | Observado y capturado | No capturado | Observado y capturado | No se capturó 1024 px |
| Operaciones | Observado y capturado | Observado y capturado | Observado y capturado | Sin limitación residual de viewport |
| Propuestas | Observado y capturado | Observado y capturado | Observado y capturado | Sin limitación residual de viewport |
| Movimientos | Evidencia de detalle capturada a 1440 | No capturado de forma independiente | Observado y capturado | La cobertura a 1440 proviene de la evidencia de detalle; no hay captura independiente de 1024 |
| Historial de precios | V13 observado por el usuario | No capturado | No capturado | Sin captura nueva del `2026-10-23`; la verificación es por observación directa, sin consultas SQL |
| Asistente | Observado y capturado | No capturado | Observado y capturado | No se capturó 1024 px |

### Resultados

- **V10:** se creó y confirmó una propuesta de entrada suelta de 1 unidad para `coca-500`, motivo `Prueba V10 - entrada de stock`. Productos mostró stock suelto 2; Movimientos mostró una entrada de cantidad 1, además del movimiento inicial de cantidad 2. Evidencia por observación directa del usuario; no se ejecutaron consultas SQL.
- **V13:** el historial de `botella` mostró precio anterior `1500.00`, precio nuevo `1600.00`, fecha `2026-10-23 17:05:02 -03:00` y evento `cambio_precio`.
- **V16:** se midieron muestras de texto con contraste mínimo `6.68:1`. El texto dibujado dentro del canvas de las tablas no se midió de forma fiable; no se declara contraste total.
- **V17:** no hubo overflow de página a 1024 ni 768. Las tablas usan scroll horizontal interno contenido y accesible; se acepta como limitación documentada, sin pérdida de contenido.

### Limitaciones aceptadas

- El canvas de las tablas no permite medir contraste de forma fiable con la herramienta disponible.
- El scroll horizontal interno de tablas a 768 px es nativo y está contenido; no desborda la página ni elimina contenido.
- La revisión independiente final emitió APROBADO el `2026-10-23`; el cierre documental se registra sin commit adicional.

### Evidencias nuevas

- `t057-vigente-productos-1440x900.png`
- `t057-vigente-productos-1024x768.png`
- `t057-vigente-productos-768x1024.png`
- `t057-vigente-detalle-1440x900.png`
- `t057-vigente-detalle-bottom-1440x900.png`
- `t057-vigente-detalle-768x1024.png`
- `t057-vigente-busqueda-1440x900.png`
- `t057-vigente-operaciones-1440x900.png`
- `t057-vigente-operaciones-1024x768.png`
- `t057-vigente-operaciones-768x1024.png`
- `t057-vigente-entrada-cerrada-1440x900.png`
- `t057-vigente-entrada-cerrada-vacia-1440x900.png`
- `t057-vigente-propuestas-1440x900.png`
- `t057-vigente-propuestas-1024x768.png`
- `t057-vigente-propuestas-768x1024.png`
- `t057-vigente-edicion-1440x900.png`
- `t057-vigente-rechazo-1440x900.png`
- `t057-vigente-movimientos-768x1024.png`
- `t057-vigente-inicio-1440x900.png`
- `t057-vigente-asistente-1440x900.png`
- `t057-vigente-asistente-768x1024.png`
- `t057-vigente-productos-1440.png` (captura omitida en el listado anterior)
- `t057-vigente-productos-bottom-1034.png` (captura omitida en el listado anterior; nombre de archivo tal como se registró)

### Conclusión de la validación combinada

La validación visual de T057 es **combinada** entre la ronda inicial (`2026-10-20`) y la ronda complementaria (`2026-10-23`): cubre navegación, estados vacíos, datos reales, flujo de propuestas, tablas y responsive en los viewports registrados en la matriz anterior, sin desbordes de página. Las limitaciones residuales aceptadas son el contraste del texto dibujado dentro del canvas de las tablas (no medido de forma fiable; las muestras de texto alcanzan 6.68:1) y el scroll horizontal interno de tablas a 768 px.

La revisión independiente final emitió APROBADO el 2026-10-23, con limitaciones aceptadas sobre contraste del canvas y scroll interno de tablas.
