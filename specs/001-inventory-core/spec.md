# Especificación funcional: núcleo de inventario

## Estado

Aprobada.

## Propósito

El sistema debe permitir gestionar el inventario básico de productos y consultar su estado mediante un asistente. Debe conservar trazabilidad de los cambios de existencias, prevenir stock negativo y proporcionar información fiable para reposición.

## Alcance

Esta especificación cubre productos, existencias, movimientos, alertas de stock bajo, reposición y las consultas o solicitudes gestionadas por el asistente.

El MVP opera sobre un único inventario y una única ubicación.

## RF-01. Gestión de productos

### Objetivo

El sistema debe mantener la información mínima necesaria para identificar cada producto, conocer su umbral de reposición y su precio de venta.

### Requisitos

-   El sistema debe registrar productos con SKU, nombre, stock mínimo, precio de venta y stock inicial.
-   Cada producto debe tener un SKU único y estable.
-   El sistema no debe permitir modificar el SKU de un producto después de su registro.
-   El sistema debe permitir consultar productos por SKU o por nombre.
-   El sistema debe permitir modificar el nombre, el precio de venta y el stock mínimo de un producto activo.
-   El stock mínimo debe ser cero o un valor positivo, con un máximo de dos decimales.
-   El precio de venta debe ser mayor que cero.
-   El sistema debe permitir desactivar un producto sin eliminarlo ni eliminar su historial.
-   Un producto desactivado debe permanecer disponible para consultas e historial.
-   Un producto desactivado no debe admitir operaciones ordinarias nuevas ni modificaciones.
-   Al registrar un producto con stock inicial mayor que cero, el sistema debe registrar una entrada identificada como stock inicial.
-   Al registrar un producto con stock inicial igual a cero, el sistema no debe generar un movimiento de cantidad cero.

### Criterios de aceptación

-   Dado un SKU no registrado, cuando se confirme el registro de un producto con todos los datos requeridos y stock inicial mayor que cero, el sistema debe crear el producto y registrar una entrada de stock inicial.
-   Dado un SKU no registrado, cuando se confirme el registro de un producto con stock inicial igual a cero, el sistema debe crear el producto con stock actual igual a cero y no debe registrar un movimiento.
-   Dado un SKU ya registrado, cuando se intente registrar otro producto con ese SKU, el sistema debe rechazar la operación e informar que el SKU ya existe.
-   Cuando se intente modificar el SKU de un producto, el sistema debe rechazar la operación e informar que el SKU no es modificable.
-   Cuando se consulte un SKU existente, el sistema debe mostrar el producto correspondiente.
-   Cuando se consulte un nombre que identifique inequívocamente un producto, el sistema debe mostrar ese producto.
-   Cuando se intente asignar un stock mínimo negativo o con más de dos decimales, el sistema debe rechazar la operación.
-   Cuando se asigne un stock mínimo de cero, el sistema debe aceptar la operación.
-   Cuando se intente asignar un precio de venta igual o menor que cero, el sistema debe rechazar la operación.
-   Cuando se desactive un producto, el sistema debe conservar sus datos y movimientos históricos.
-   Dado un producto desactivado, cuando se solicite una operación ordinaria nueva o una modificación, el sistema debe rechazar la operación.
-   Dado un producto desactivado, cuando se consulte por SKU o nombre o se consulte su historial, el sistema debe devolver la información disponible.

## RF-02. Control de existencias

### Objetivo

El sistema debe reflejar el stock actual a partir de los movimientos confirmados y prevenir existencias negativas.

### Requisitos

-   El sistema debe mantener el stock actual de cada producto de acuerdo con sus movimientos confirmados.
-   El stock actual debe admitir hasta dos decimales.
-   Cuando el stock inicial sea mayor que cero, su entrada identificada como stock inicial debe formar parte de los movimientos confirmados del producto.
-   Cuando el stock inicial sea cero, el stock actual inicial del producto debe ser cero sin registrar un movimiento.
-   Las cantidades de los movimientos deben ser números mayores que cero, enteros o decimales, con un máximo de dos decimales.
-   El sistema no debe aceptar una cantidad igual a cero, negativa o con más de dos decimales.
-   Cuando se solicite una salida, el sistema debe verificar que la cantidad no supere el stock disponible.
-   El sistema no debe confirmar ningún movimiento que resulte en stock negativo.
-   Estas restricciones también deben aplicarse a los movimientos compensatorios.

### Criterios de aceptación

-   Dado un producto registrado con stock inicial mayor que cero, el stock actual debe incluir la cantidad de su entrada inicial confirmada.
-   Dado un producto registrado con stock inicial igual a cero, el stock actual debe ser cero y no debe existir un movimiento de stock inicial con cantidad cero.
-   Dado un producto con stock disponible, cuando se confirme una entrada, el stock actual debe aumentar en la cantidad registrada.
-   Dado un producto con stock suficiente, cuando se confirme una salida, el stock actual debe disminuir en la cantidad registrada.
-   Cuando se solicite una cantidad igual a cero, negativa o con más de dos decimales, el sistema debe rechazar el movimiento.
-   Dado un producto con stock actual menor que la cantidad solicitada para una salida, el sistema debe rechazar la salida e informar el stock disponible.
-   Después de confirmar cualquier movimiento, el stock actual del producto no debe ser negativo.

## RF-03. Movimientos de inventario

### Objetivo

El sistema debe conservar un historial verificable de entradas, salidas y correcciones para explicar cada cambio en las existencias.

### Requisitos

-   El sistema debe registrar entradas y salidas asociadas a un producto y una cantidad.
-   Cada movimiento debe incluir un motivo.
-   El sistema debe registrar automáticamente la fecha y hora de cada movimiento confirmado.
-   El sistema debe permitir consultar movimientos por producto, tipo y período.
-   El sistema no debe permitir editar ni eliminar movimientos confirmados.
-   Cuando sea necesario corregir un movimiento confirmado, el sistema debe registrar un movimiento compensatorio relacionado con el movimiento original.
-   Un movimiento compensatorio puede neutralizar total o parcialmente la cantidad del movimiento original.
-   La suma de las cantidades de todas las compensaciones confirmadas vinculadas a un movimiento original no debe superar la cantidad de ese movimiento original.
-   Cada nueva compensación no debe superar la cantidad pendiente de compensar del movimiento original.
-   El sistema debe conservar el vínculo entre cada movimiento compensatorio y su movimiento original.
-   La confirmación de una compensación no debe modificar ni eliminar el movimiento original.
-   Un producto desactivado puede admitir únicamente movimientos compensatorios necesarios para corregir su historial.
-   Los movimientos compensatorios de productos desactivados deben respetar las mismas reglas de cantidad y stock no negativo que cualquier otro movimiento.

### Criterios de aceptación

-   Cuando se confirme una entrada o salida válida con un motivo, el sistema debe registrar el tipo, producto, cantidad, motivo, fecha y hora.
-   Cuando se solicite un movimiento sin motivo, el sistema debe rechazar la operación.
-   Cuando se consulten movimientos de un producto, el sistema debe devolver únicamente los movimientos asociados a ese producto.
-   Cuando se consulte por tipo, el sistema debe devolver únicamente entradas o únicamente salidas, según lo solicitado.
-   Cuando se consulte por período, el sistema debe devolver únicamente los movimientos incluidos en el período solicitado.
-   Cuando se intente editar o eliminar un movimiento confirmado, el sistema debe rechazar la operación.
-   Cuando se confirme una compensación válida, el sistema debe crear un nuevo movimiento vinculado al original y conservar ambos en el historial.
-   Cuando una compensación total o parcial provocaría stock negativo, el sistema debe rechazarla.
-   Dado un movimiento original con compensaciones confirmadas parciales o totales, cuando se solicite una nueva compensación cuya cantidad supere la cantidad pendiente de compensar, el sistema debe rechazarla e informar el motivo.
-   Dado un producto desactivado, cuando se solicite una compensación válida de un movimiento de su historial, el sistema debe permitirla y conservar el vínculo con el movimiento original.
-   Dado un producto desactivado, cuando se solicite una entrada o salida ordinaria, el sistema debe rechazarla.

## RF-04. Stock bajo y reposición

### Objetivo

El sistema debe identificar de forma objetiva los productos que requieren atención para evitar faltantes.

### Requisitos

-   El sistema debe considerar que un producto tiene stock bajo cuando su stock actual sea menor o igual que su stock mínimo.
-   El sistema debe identificar los productos que requieren reposición.
-   Para cada producto, el faltante debe calcularse como el máximo entre cero y la diferencia entre el stock mínimo y el stock actual.

### Criterios de aceptación

-   Dado un producto cuyo stock actual sea igual a su stock mínimo, el sistema debe identificarlo como producto con stock bajo.
-   Dado un producto cuyo stock actual sea menor que su stock mínimo, el sistema debe identificarlo como producto con stock bajo.
-   Dado un producto cuyo stock actual sea mayor que su stock mínimo, el sistema no debe identificarlo como producto con stock bajo.
-   Dado un producto con stock mínimo de 10 y stock actual de 6, el sistema debe informar un faltante de 4.
-   Dado un producto con stock mínimo de 10 y stock actual de 12, el sistema debe informar un faltante de 0.

## RF-05. Asistente con IA

### Objetivo

El asistente debe permitir consultar el inventario y proponer operaciones mediante lenguaje natural, sin realizar cambios no confirmados ni presentar información inventada.

### Requisitos

-   El asistente debe responder consultas sobre stock actual, productos con stock bajo, necesidades de reposición e historial de movimientos.
-   Para responder consultas, el asistente debe consultar los datos reales del inventario.
-   El asistente debe interpretar solicitudes para registrar productos, entradas, salidas y modificaciones de productos.
-   Antes de cualquier modificación, el asistente debe mostrar la operación propuesta y solicitar confirmación.
-   El asistente debe ejecutar una modificación únicamente después de recibir confirmación.
-   Cuando no se reciba confirmación, el asistente no debe realizar cambios.
-   Cuando una solicitud no identifique inequívocamente un producto u operación, el asistente debe solicitar una aclaración.
-   Cuando existan coincidencias disponibles para una solicitud ambigua, el asistente debe mostrarlas.
-   Cuando una operación sea inválida o no pueda ejecutarse, el asistente debe informar claramente el error y no debe afirmar que se realizó.
-   El asistente no debe inventar productos, stock, movimientos, resultados ni operaciones realizadas.

### Criterios de aceptación

-   Cuando se consulte el stock de un producto identificado inequívocamente, el asistente debe responder con el stock actual registrado.
-   Cuando se soliciten productos con stock bajo, el asistente debe responder con los productos que cumplan esa condición.
-   Cuando se solicite reposición, el asistente debe responder con los productos identificados y su faltante calculado.
-   Cuando se solicite el historial de movimientos con filtros válidos, el asistente debe mostrar los movimientos que correspondan a esos filtros.
-   Antes de registrar un producto, movimiento, modificación o compensación, el asistente debe mostrar los datos propuestos y pedir confirmación.
-   Dada una propuesta no confirmada, el sistema no debe crear ni modificar datos.
-   Dada una propuesta confirmada y válida, el sistema debe ejecutar la operación e informar el resultado.
-   Cuando una referencia de producto sea ambigua, el asistente debe solicitar aclaración y mostrar las coincidencias disponibles.
-   Cuando una referencia de producto no exista, el asistente debe informarlo claramente.
-   Cuando una operación infrinja una regla de negocio, el asistente debe informar el motivo y no debe confirmar un resultado inexistente.

## RF-06. Límites del MVP

### Objetivo

El MVP debe mantener un alcance reducido y centrado en el control esencial de un único inventario.

### Requisitos

-   El sistema debe gestionar un único inventario y una única ubicación.
-   El MVP no debe incluir múltiples usuarios.
-   El MVP no debe incluir proveedores.
-   El MVP no debe incluir compras ni ventas formales.
-   El MVP no debe permitir edición ni eliminación directa de movimientos confirmados.
-   El MVP no debe incluir funcionalidades fuera del alcance definido en esta especificación.

### Criterios de aceptación

-   El sistema debe operar sobre una única ubicación de inventario.
-   El sistema no debe exponer operaciones para múltiples usuarios, proveedores, compras o ventas formales.
-   El sistema debe rechazar la edición o eliminación directa de un movimiento confirmado.
-   Cualquier corrección de un movimiento confirmado debe realizarse mediante un movimiento compensatorio relacionado.

## Reglas de negocio transversales

-   El SKU identifica de forma única a un producto y no puede modificarse.
-   El stock actual y el stock mínimo admiten hasta dos decimales.
-   El stock mínimo puede ser cero o un valor positivo, pero no puede ser negativo.
-   El precio de venta debe ser mayor que cero.
-   El stock inicial puede ser cero o un valor positivo, con un máximo de dos decimales.
-   Un stock inicial igual a cero no debe generar un movimiento.
-   Un stock inicial mayor que cero debe generar una entrada identificada como stock inicial.
-   Toda cantidad de movimiento debe ser mayor que cero y tener como máximo dos decimales.
-   El stock resultante de un movimiento confirmado nunca puede ser negativo.
-   El stock actual debe reflejar los movimientos confirmados, junto con el valor inicial cero cuando un producto se registre sin movimiento de stock inicial.
-   Todo movimiento requiere un motivo y fecha y hora registradas automáticamente.
-   Los movimientos confirmados son inmutables.
-   Las correcciones deben conservar la trazabilidad mediante movimientos compensatorios vinculados al movimiento original.
-   Las compensaciones pueden ser totales o parciales y deben respetar todas las reglas de integridad del stock.
-   La suma de las cantidades de todas las compensaciones confirmadas vinculadas a un movimiento original no puede superar la cantidad de ese movimiento original.
-   Cada nueva compensación no puede superar la cantidad pendiente de compensar del movimiento original.
-   Los productos desactivados solo admiten consultas e historial y no permiten operaciones ordinarias nuevas ni modificaciones. Como excepción, pueden recibir movimientos compensatorios vinculados a su historial, siempre que cumplan las reglas de integridad y stock.
-   Toda modificación requiere una propuesta visible y confirmación previa.

## Casos de error relevantes

-   SKU duplicado al registrar un producto.
-   Intento de modificar un SKU.
-   Stock mínimo negativo o con más de dos decimales.
-   Precio de venta igual o menor que cero.
-   Stock inicial negativo o con más de dos decimales.
-   Intento de registrar un movimiento de stock inicial con cantidad cero.
-   Cantidad de movimiento igual a cero, negativa o con más de dos decimales.
-   Salida o compensación que produciría stock negativo.
-   Compensación cuya cantidad supera la cantidad pendiente de compensar del movimiento original.
-   Movimiento sin motivo.
-   Producto inexistente.
-   Producto desactivado en una solicitud de operación ordinaria o modificación.
-   Consulta o solicitud ambigua que no identifica un producto u operación.
-   Intento de editar o eliminar un movimiento confirmado.
-   Solicitud de modificación sin confirmación.