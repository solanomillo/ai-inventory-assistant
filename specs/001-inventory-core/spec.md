# Especificación funcional: núcleo de inventario

## Estado

Aprobada.

## Propósito

El sistema debe permitir gestionar el inventario básico de productos mediante presentaciones (unidades, cajas, fardos), distinguiendo stock cerrado y stock suelto, con registro del costo de ingreso y del precio de venta por presentación. Debe conservar trazabilidad de los cambios de existencias, prevenir stock negativo y proporcionar información fiable para reposición.

## Alcance

Esta especificación cubre productos, presentaciones, existencias (sueltas y cerradas), movimientos, aperturas, compensaciones, costo de ingreso, alertas de stock bajo, reposición y las consultas o solicitudes gestionadas por el asistente.
El MVP opera sobre un único inventario y una única ubicación. Las cantidades de inventario son unidades discretas enteras (botellas, paquetes, unidades); los productos fraccionables por peso o volumen quedan fuera del MVP.

## RF-01. Gestión de productos

### Objetivo

El sistema debe mantener la información mínima necesaria para identificar cada producto y su unidad base de inventario.

### Requisitos

- El sistema debe registrar productos con SKU, nombre y unidad base.
- La unidad base es el nombre de la unidad discreta en la que se mide el producto (por ejemplo, botella, paquete o unidad).
- Antes de validar y guardar, el sistema debe eliminar los espacios iniciales y finales de SKU, nombre y unidad base.
- El sistema debe conservar el texto original recortado de SKU, nombre y unidad base, sin normalizar mayúsculas ni minúsculas.
- El SKU, el nombre y la unidad base son obligatorios y, tras eliminar sus espacios, no pueden estar vacíos.
- Cada producto debe tener un SKU único y estable, sin imponer un formato específico; la unicidad del SKU se evalúa sin distinguir mayúsculas de minúsculas.
- El sistema no debe permitir modificar el SKU de un producto después de su registro.
- El sistema debe permitir consultar productos por SKU o por nombre; ambas consultas comparan sin distinguir mayúsculas de minúsculas y conservan el texto original almacenado.
- Cuando una consulta por nombre devuelva varias coincidencias, el sistema debe mostrarlas todas sin seleccionar automáticamente ninguna, conforme a la regla de mostrar coincidencias de RF-05.
- El sistema debe permitir modificar el nombre y el stock mínimo de un producto activo.
- El stock mínimo debe ser un entero mayor o igual que cero, expresado en unidades base.
- El sistema debe permitir desactivar un producto sin eliminarlo ni eliminar su historial.
- Un producto desactivado debe permanecer disponible para consultas e historial, y no debe admitir operaciones ordinarias nuevas, modificaciones de nombre o stock, creación o modificación de presentaciones ni aperturas; solo admite consultas, historial y compensaciones históricas válidas.

### Criterios de aceptación

- Dado un SKU no registrado, cuando se confirme el registro de un producto con SKU, nombre y unidad base válidos, el sistema debe crear el producto.
- Dado un SKU ya registrado, cuando se intente registrar otro producto con ese SKU, el sistema debe rechazar la operación e informar que el SKU ya existe.
- Dado un SKU ya registrado, cuando se intente registrar otro producto con un SKU que difiera solo en mayúsculas o minúsculas, el sistema debe rechazar la operación e informar que el SKU ya existe.
- Cuando el SKU, el nombre o la unidad base estén vacíos o contengan solo espacios, el sistema debe rechazar el registro.
- Cuando se registre un producto con SKU, nombre o unidad base que incluyan mayúsculas y minúsculas, el sistema debe almacenar y mostrar el texto original recortado sin normalizar el caso.
- Cuando se intente modificar el SKU de un producto, el sistema debe rechazar la operación.
- Cuando se intente asignar un stock mínimo negativo o no entero, el sistema debe rechazar la operación.
- Cuando se desactive un producto, el sistema debe conservar sus datos y movimientos históricos.
- Dado un producto desactivado, cuando se solicite una operación ordinaria nueva o una modificación, el sistema debe rechazar la operación.
- Dado un producto desactivado, cuando se consulte por SKU o nombre o se consulte su historial, el sistema debe devolver la información disponible.
- Cuando se consulte por SKU o por nombre con un texto que difiera solo en mayúsculas o minúsculas del valor almacenado, el sistema debe devolver el producto y mostrar su texto original almacenado.
- Cuando una consulta por nombre devuelva varias coincidencias, el sistema debe mostrarlas todas sin seleccionar automáticamente ninguna (regla de mostrar coincidencias de RF-05).

## RF-02. Control de existencias

### Objetivo

El sistema debe reflejar el stock a partir de los movimientos confirmados, distinguiendo unidades sueltas y presentaciones cerradas, y prevenir existencias negativas.

### Requisitos

- El sistema debe mantener, por producto, el stock de unidades sueltas y, por presentación, el stock de presentaciones cerradas.
- Las unidades sueltas se cuentan en unidades base enteras, siempre mayores o iguales que cero.
- El stock cerrado de cada presentación se cuenta en unidades enteras, siempre mayor o igual que cero.
- El total equivalente de un producto se calcula como `unidades_sueltas + Σ(stock_cerrado_p × factor_p)` y no se almacena como saldo independiente.
- Las cantidades de los movimientos deben ser enteros mayores que cero.
- El sistema no debe aceptar cantidades iguales a cero, negativas o no enteras.
- Cuando se solicite una salida, el sistema debe verificar que la cantidad no supere el stock disponible del tipo correspondiente (suelto o cerrado).
- El sistema no debe confirmar ningún movimiento que resulte en stock suelto o cerrado negativo.
- Al registrar un producto, el stock inicial puede indicarse como unidades sueltas y como presentaciones cerradas; cada parte mayor que cero genera una entrada identificada como stock inicial, y si el total inicial es cero no se genera ningún movimiento.
- Al confirmar una operación de stock, el sistema debe validar la disponibilidad vigente en ese momento y proteger las operaciones simultáneas para impedir confirmaciones basadas en una disponibilidad obsoleta.

### Criterios de aceptación

- Dado un producto registrado con stock inicial suelto o cerrado mayor que cero, el stock correspondiente debe reflejar esa entrada identificada como stock inicial.
- Dado un producto registrado con stock inicial total igual a cero, no debe existir ningún movimiento de stock inicial.
- Dado un producto con stock suelto suficiente, cuando se confirme una entrada suelta, las unidades sueltas deben aumentar en la cantidad registrada.
- Dado un producto con stock cerrado suficiente, cuando se confirme una entrada cerrada, el stock cerrado de esa presentación debe aumentar.
- Dado un producto con stock suficiente, cuando se confirme una salida suelta o cerrada, el stock correspondiente debe disminuir en la cantidad registrada.
- Dado un producto con stock suelto o cerrado menor que la cantidad solicitada para una salida, el sistema debe rechazar la salida e informar el stock disponible.
- Después de confirmar cualquier movimiento, ni las unidades sueltas ni el stock cerrado pueden ser negativos.

## RF-03. Movimientos de inventario

### Objetivo

El sistema debe conservar un historial verificable de entradas, salidas, aperturas y correcciones para explicar cada cambio en las existencias.

### Requisitos

- El sistema debe registrar movimientos de los tipos: entrada cerrada, entrada suelta, salida cerrada, salida suelta y apertura.
- Cada movimiento debe estar asociado a un producto y, cuando corresponda, a una presentación.
- Cada movimiento debe incluir un motivo.
- El sistema debe registrar automáticamente la fecha y hora de cada movimiento confirmado.
- El sistema debe permitir consultar movimientos por producto, tipo y período.
- Cuando se proporcionen fechas para un período, el sistema debe interpretarlas en la hora local de Argentina y almacenar los instantes de los movimientos en UTC.
- El período de consulta debe incluir su inicio y excluir su final.
- Cuando se combinen filtros de producto, tipo y período, el sistema debe devolver únicamente los movimientos que cumplan todos los filtros proporcionados.
- El sistema no debe permitir editar ni eliminar movimientos confirmados.

### Apertura de presentaciones

- Una apertura convierte una o varias presentaciones cerradas en unidades sueltas: resta `N` al stock cerrado de una presentación y suma `N × factor` a las unidades sueltas del producto.
- Una apertura solo es válida sobre una presentación cuyo factor sea mayor que 1; la presentación base (factor 1) no puede abrirse.
- Una apertura es una operación atómica e inmutable: no cambia el total equivalente y se registra en el historial.
- Una apertura requiere stock cerrado suficiente de esa presentación.
- Una apertura no es compensable con las reglas de compensación actuales.
- Una apertura no debe permitirse sobre un producto desactivado.

### Costo de ingreso y precio de salida

- Las entradas (cerradas y sueltas) pueden registrar un costo opcional, un Decimal mayor o igual que cero, distinto del precio de venta.
- El costo no se distribuye automáticamente entre unidades ni introduce un método de valoración de inventario.
- Las salidas confirmadas guardan una copia del precio de venta aplicado en el momento de la confirmación; los cambios posteriores de precio no alteran esa copia.

### Compensaciones

- Cuando sea necesario corregir un movimiento confirmado, el sistema debe registrar un movimiento compensatorio vinculado al movimiento original.
- Un movimiento compensatorio puede neutralizar total o parcialmente la cantidad del movimiento original.
- Todo movimiento compensatorio debe tener el tipo opuesto al movimiento original y debe vincularse a un movimiento original que no sea compensatorio.
- Las aperturas no pueden ser compensadas ni ser destino de una compensación.
- La confirmación de una compensación no debe modificar ni eliminar el movimiento original.

### Consumo FIFO de presentaciones cerradas

- Para las entradas cerradas, el sistema mantiene, por entrada, la cantidad consumida y la cantidad compensada.
- Una apertura y una salida cerrada consumen, en orden FIFO, las presentaciones cerradas de la entrada cerrada más antigua con saldo disponible de esa presentación.
- La cantidad compensable de una entrada cerrada se calcula como `cantidad − cantidad_consumida − cantidad_compensada`.
- Una compensación de una entrada cerrada no puede superar su cantidad compensable ni el stock cerrado vigente de esa presentación.
- Las entradas sueltas no tienen trazabilidad individual por entrada: para ellas se conservan la validación de stock disponible y el límite de cantidad pendiente de compensar, sin afirmar que se impide compensar unidades ya transformadas cuando el modelo no puede demostrarlo.

### Criterios de aceptación

- Cuando se confirme una entrada o salida válida con un motivo, el sistema debe registrar el tipo, producto, presentación, cantidad, motivo, fecha y hora.
- Cuando se solicite un movimiento sin motivo, el sistema debe rechazar la operación.
- Cuando se consulte por producto, tipo o período, el sistema debe devolver únicamente los movimientos que cumplan el filtro; los filtros combinados se aplican de forma conjunta.
- Cuando se intente editar o eliminar un movimiento confirmado, el sistema debe rechazar la operación.
- Dada una apertura de `N` presentaciones con stock cerrado suficiente, el sistema debe restar `N` al stock cerrado y sumar `N × factor` a las unidades sueltas en una única transacción, sin alterar el total equivalente.
- Dada una apertura sin stock cerrado suficiente, el sistema debe rechazarla.
- Dada la presentación base (factor 1), cuando se solicite una apertura, el sistema debe rechazarla sin modificar el stock ni registrar el movimiento.
- Dado un producto desactivado, cuando se solicite una apertura, el sistema debe rechazarla.
- Cuando se solicite compensar una apertura, el sistema debe rechazarla.
- Dada una entrada cerrada A (+2), una apertura de 1, y una entrada cerrada B (+1) de la misma presentación, el sistema debe rechazar compensar A por 2 y debe permitir compensar A por 1.
- Dada una entrada cerrada con una compensación que exceda su cantidad compensable, el sistema debe rechazarla e informar el motivo.
- Cuando se confirme una compensación válida, el sistema debe crear un nuevo movimiento vinculado al original y conservar ambos en el historial.
- Dado un producto desactivado, cuando se solicite una compensación histórica válida de un movimiento de su historial, el sistema debe permitirla; una entrada o salida ordinaria debe rechazarse.

## RF-04. Stock bajo y reposición

### Objetivo

El sistema debe identificar de forma objetiva los productos que requieren atención para evitar faltantes.

### Requisitos

- El sistema debe considerar que un producto tiene stock bajo cuando su total equivalente sea menor o igual que su stock mínimo.
- Las consultas globales de stock bajo y de reposición deben incluir únicamente productos activos.
- El sistema debe identificar como productos que requieren reposición únicamente los productos activos cuyo faltante sea mayor que cero.
- Para cada producto, el faltante debe calcularse como el máximo entre cero y la diferencia entre el stock mínimo y el total equivalente.

### Criterios de aceptación

- Dado un producto cuyo total equivalente sea igual a su stock mínimo, el sistema debe identificarlo como producto con stock bajo.
- Dado un producto cuyo total equivalente sea menor que su stock mínimo, el sistema debe identificarlo como producto con stock bajo.
- Dado un producto cuyo total equivalente sea mayor que su stock mínimo, el sistema no debe identificarlo como producto con stock bajo.
- Dado un producto con stock mínimo de 10 y total equivalente de 6, el sistema debe informar un faltante de 4.
- Dado un producto con stock mínimo de 10 y total equivalente de 12, el sistema debe informar un faltante de 0.
- Dado un producto activo con stock mínimo de 0 y total equivalente de 0, el sistema debe identificarlo con stock bajo, pero no debe incluirlo entre los productos que requieren reposición.
- Dado un producto desactivado, el sistema no debe incluirlo en las consultas globales de stock bajo ni de reposición.

## RF-05. Asistente con IA

### Objetivo

El asistente debe permitir consultar el inventario y proponer operaciones mediante lenguaje natural, sin realizar cambios no confirmados ni presentar información inventada.

### Requisitos

- El asistente debe responder consultas sobre stock actual (suelto, cerrado y total equivalente), productos con stock bajo, necesidades de reposición e historial de movimientos.
- Para responder consultas, el asistente debe consultar los datos reales del inventario.
- El asistente debe interpretar solicitudes para registrar productos, presentaciones, entradas, salidas, aperturas y modificaciones de productos.
- Antes de cualquier modificación, el asistente debe mostrar la operación propuesta y solicitar confirmación.
- Antes de confirmarse, una propuesta debe poder editarse sin crear movimientos ni modificar el inventario.
- El asistente debe ejecutar una modificación únicamente después de recibir confirmación.
- Al recibir una confirmación, el asistente debe volver a validar todas las reglas de negocio aplicables con el estado actual del inventario.
- Si una regla aplicable falla al confirmar, el asistente debe rechazar la operación, explicar el motivo y no guardar cambios parciales.
- Cuando una solicitud no identifique inequívocamente un producto u operación, el asistente debe solicitar una aclaración y, si hay coincidencias, mostrarlas; para las coincidencias múltiples por nombre aplica la regla general de RF-01.
- El asistente no debe inventar productos, presentaciones, stock, movimientos, resultados ni operaciones realizadas.

### Criterios de aceptación

- Cuando se consulte el stock de un producto identificado inequívocamente, el asistente debe responder con los datos reales de stock suelto, cerrado y total equivalente.
- Cuando se soliciten productos con stock bajo o reposición, el asistente debe responder con los productos que cumplan la condición y su faltante calculado.
- Cuando se solicite el historial de movimientos con filtros válidos, el asistente debe mostrar los movimientos que correspondan a esos filtros.
- Antes de registrar un producto, presentación, movimiento, apertura, modificación o compensación, el asistente debe mostrar los datos propuestos y pedir confirmación.
- Dada una propuesta no confirmada, el sistema no debe crear ni modificar datos.
- Dada una propuesta confirmada y válida, el sistema debe ejecutar la operación e informar el resultado.
- Dada una propuesta que deje de cumplir una regla antes de confirmarse, el asistente debe rechazarla al confirmar y no guardar cambios parciales.
- Cuando una operación infrinja una regla de negocio, el asistente debe informar el motivo y no debe confirmar un resultado inexistente.

## RF-06. Límites del MVP

### Objetivo

El MVP debe mantener un alcance reducido y centrado en el control esencial de un único inventario.

### Requisitos

- El sistema debe gestionar un único inventario y una única ubicación.
- El MVP no debe incluir múltiples usuarios, proveedores, compras ni ventas formales.
- El MVP no debe permitir edición ni eliminación directa de movimientos confirmados.
- El MVP no debe incluir funcionalidades fuera del alcance definido en esta especificación.

### Criterios de aceptación

- El sistema debe operar sobre una única ubicación de inventario.
- El sistema no debe exponer operaciones para múltiples usuarios, proveedores, compras o ventas formales.
- El sistema debe rechazar la edición o eliminación directa de un movimiento confirmado.
- Cualquier corrección de un movimiento confirmado debe realizarse mediante un movimiento compensatorio relacionado, salvo las aperturas, que no son compensables.

## RF-07. Presentaciones y costo de ingreso

### Objetivo

El sistema debe permitir administrar un producto por distintas presentaciones con equivalencias y precios propios, y registrar el costo de ingreso diferenciado del precio de venta.

### Requisitos

- Cada producto debe tener exactamente una presentación base, correspondiente a la unidad base y con factor igual a 1. Puede tener además otras presentaciones.
- Cada presentación debe tener un nombre único dentro del producto, comparado sin distinguir mayúsculas de minúsculas, un factor entero mayor o igual que 1 que expresa su equivalencia en unidades base, y un precio de venta mayor que cero.
- Los precios de las presentaciones son independientes: el precio de una presentación no se calcula automáticamente a partir del precio de otra.
- El factor de una presentación no debe modificarse cuando esa presentación tiene stock cerrado o movimientos históricos; para representar un cambio debe crearse otra presentación.
- El sistema debe permitir agregar presentaciones aunque el producto tenga stock; la nueva presentación nace con stock cerrado igual a cero y no altera el stock ni las equivalencias existentes.
- En el MVP no se desactivan presentaciones individualmente; la desactivación opera a nivel de producto.
- El costo de ingreso es opcional, se registra en las entradas y no equivale al precio de venta.
- El sistema no debe permitir crear ni modificar presentaciones de un producto desactivado.

### Criterios de aceptación

- Dado un producto recién registrado, el sistema debe crear su presentación de unidad base con factor 1.
- Dada una presentación con nombre duplicado dentro del producto, el sistema debe rechazarla; se considera duplicado el nombre que difiera solo en mayúsculas o minúsculas.
- Dado un factor menor que 1, no entero, o un precio menor o igual que cero, el sistema debe rechazar la presentación.
- Dada una presentación con stock cerrado o historial, cuando se intente modificar su factor, el sistema debe rechazar la operación.
- Dado un producto con stock, cuando se agregue una presentación nueva, el sistema debe crearla con stock cerrado cero sin modificar el stock existente.
- Dada una entrada con costo, el sistema debe almacenarlo tal como fue informado, sin distribuirlo ni derivar costo unitario.
- Dado un producto desactivado, cuando se intente crear o modificar una presentación, el sistema debe rechazar la operación.

## Reglas de negocio transversales

- El SKU identifica de forma única a un producto y no puede modificarse; la unicidad del SKU y del nombre de presentación se evalúa sin distinguir mayúsculas de minúsculas.
- El SKU, el nombre y la unidad base son obligatorios y, tras eliminar sus espacios iniciales y finales, no pueden quedar vacíos.
- El SKU, el nombre del producto, el nombre de la presentación y la unidad base se conservan con su texto original recortado, sin normalizar el caso.
- Las consultas por SKU y por nombre de producto comparan sin distinguir mayúsculas de minúsculas y conservan el texto original almacenado; cuando una consulta por nombre devuelva varias coincidencias, se muestran todas sin seleccionar automáticamente ninguna (RF-01).
- Las cantidades de inventario y los factores de presentación son enteros; los precios y costos son Decimales exactos.
- El stock mínimo es un entero mayor o igual que cero.
- El precio de venta de una presentación debe ser mayor que cero.
- El costo de ingreso, cuando se informa, debe ser mayor o igual que cero.
- Toda cantidad de movimiento debe ser un entero mayor que cero.
- El stock suelto y el stock cerrado nunca pueden ser negativos.
- El total equivalente se deriva de las unidades sueltas y del stock cerrado por presentación; no se persiste como saldo independiente.
- Todo movimiento requiere un motivo y fecha y hora registradas automáticamente.
- Los instantes se almacenan en UTC; los períodos se interpretan en hora local de Argentina con inicio inclusivo y final exclusivo; los filtros combinados se aplican de forma conjunta.
- Los movimientos confirmados son inmutables.
- Las aperturas son atómicas, inmutables y no compensables; solo aplican a presentaciones con factor mayor que 1, por lo que la presentación base (factor 1) no puede abrirse, y no cambian el total equivalente.
- Las correcciones se realizan mediante movimientos compensatorios vinculados al original.
- Las compensaciones son opuestas al original, no encadenadas y pueden ser parciales o totales.
- Para las entradas cerradas, el consumo FIFO de aperturas y salidas cerradas reduce la cantidad compensable; la cantidad compensable es `cantidad − cantidad_consumida − cantidad_compensada`.
- Las entradas sueltas no tienen trazabilidad individual por entrada; se conservan la validación de stock disponible y el límite de cantidad pendiente de compensar.
- Los productos desactivados solo admiten consultas, historial y compensaciones históricas válidas; no admiten operaciones ordinarias nuevas ni modificaciones, incluidas las modificaciones de nombre o stock, la creación o modificación de presentaciones y las aperturas.
- Las propuestas no confirmadas pueden editarse sin efectos; toda modificación requiere confirmación previa y se revalida al confirmar.
- Las operaciones de stock se validan contra la disponibilidad vigente y mantienen la integridad frente a ejecuciones simultáneas.

## Casos de error relevantes

- SKU duplicado al registrar un producto, incluido el caso en que difiera solo en mayúsculas o minúsculas.
- Nombre de presentación duplicado dentro del producto, incluido el caso en que difiera solo en mayúsculas o minúsculas.
- SKU, nombre o unidad base vacíos tras eliminar los espacios iniciales y finales.
- Intento de modificar un SKU.
- Stock mínimo negativo o no entero.
- Factor de presentación menor que 1 o no entero.
- Precio de venta menor o igual que cero.
- Costo de ingreso negativo.
- Modificación del factor de una presentación con stock cerrado o historial.
- Cantidad de movimiento igual a cero, negativa o no entera.
- Salida que supera el stock suelto o cerrado disponible.
- Apertura sin stock cerrado suficiente.
- Apertura de la presentación base (factor 1).
- Compensación de una apertura.
- Compensación de una entrada cerrada que supera su cantidad compensable (stock ya transformado o consumido).
- Compensación con el mismo tipo que el movimiento original o encadenada.
- Movimiento sin motivo.
- Producto inexistente.
- Producto desactivado en una solicitud de operación ordinaria, modificación, creación o modificación de presentaciones o apertura.
- Consulta o solicitud ambigua que no identifica un producto u operación.
- Intento de editar o eliminar un movimiento confirmado.
- Solicitud de modificación sin confirmación.
- Regla de negocio que deja de cumplirse entre la propuesta y su confirmación.
