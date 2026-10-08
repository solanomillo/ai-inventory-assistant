# Constitución del Proyecto

## Propósito

Este proyecto desarrolla un asistente de inventario con IA. Su evolución debe priorizar soluciones comprensibles, verificables y alineadas con la especificación vigente.

## 1. Simplicidad

- Se debe elegir la solución más simple que satisfaga la necesidad actual.
- No se deben introducir abstracciones, capas, patrones, dependencias ni configuraciones sin una necesidad comprobable.
- La optimización prematura y la funcionalidad no solicitada están prohibidas.
- Las decisiones que aumenten de forma relevante la complejidad deben quedar justificadas.

## 2. La especificación es la fuente de verdad

- Toda funcionalidad debe responder a una especificación aprobada.
- Si el código, la documentación o una solicitud informal contradicen la especificación, se debe actualizar o aclarar la especificación antes de implementar.
- Los criterios de aceptación deben ser verificables mediante pruebas, revisión o validación manual definida.

## 3. Separación de responsabilidades

- Cada módulo, función y componente debe tener una responsabilidad clara y acotada.
- La lógica de negocio debe mantenerse separada de las interfaces de entrada y salida, la persistencia y las integraciones externas.
- Las dependencias entre componentes deben ser explícitas y minimizar el acoplamiento.
- No se debe mezclar lógica de dominio con detalles de infraestructura cuando puedan mantenerse separados sin complejidad innecesaria.

## 4. Calidad del código

- El código Python debe seguir PEP 8 y priorizar legibilidad, nombres claros y funciones pequeñas.
- El código debe estar escrito en inglés, incluidos nombres de variables, funciones, clases, módulos y comentarios técnicos.
- Se debe eliminar código muerto, duplicación evitable y complejidad innecesaria.
- Los errores deben manejarse de forma explícita; no se deben ocultar fallos ni capturar excepciones de forma indiscriminada.

## 5. Pruebas como puerta de calidad

- Toda funcionalidad nueva o cambio de comportamiento debe incluir pruebas proporcionales a su riesgo.
- Los defectos corregidos deben incluir una prueba que evite su reaparición cuando sea viable.
- No se debe integrar código si las pruebas relevantes fallan.
- Las pruebas deben ser deterministas, independientes y no depender de servicios externos salvo que su propósito sea validar una integración.

## 6. Seguridad y secretos

- Los secretos, credenciales, claves de API, tokens y datos sensibles no deben incluirse en el repositorio, el código fuente, las pruebas ni los registros.
- La configuración sensible debe obtenerse desde mecanismos externos apropiados para el entorno.
- Se debe aplicar el principio de mínimo privilegio a accesos, credenciales e integraciones.
- Las entradas externas deben validarse antes de procesarse o utilizarse.

## 7. Git y Conventional Commits

- Cada cambio debe ser atómico, revisable y limitado a un propósito claro.
- Los mensajes de commit deben seguir Conventional Commits.
- No se deben incluir cambios no relacionados en un mismo commit.
- No se deben sobrescribir ni revertir cambios ajenos sin una instrucción explícita.

## 8. Documentación

- La documentación debe reflejar el comportamiento real y mantenerse junto con los cambios que afecten su contenido.
- Las decisiones relevantes, supuestos, configuraciones necesarias y limitaciones deben documentarse cuando no sean evidentes en el código.
- La documentación dirigida a personas debe escribirse en español, salvo que una convención futura del proyecto establezca otra cosa.

## 9. Cumplimiento y cambios

- Esta constitución establece las reglas generales de desarrollo del proyecto.
- La especificación vigente define el comportamiento funcional del sistema.
- Las excepciones deben ser explícitas, justificadas y limitadas en alcance.
- Los cambios a esta constitución deben revisarse con el mismo rigor que los cambios funcionales.
