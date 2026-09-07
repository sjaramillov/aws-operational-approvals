# Piloto para un cliente de consultoría

## Problema verificable

El equipo recibe solicitudes que requieren reglas de aprobación y una decisión responsable. El piloto debe permitir reconstruir una decisión sin buscar conversaciones dispersas. El ejemplo de flotas sirve para discutir el flujo; no presupone que el cliente tiene ese mismo negocio.

## Descubrimiento y alcance

Se registra el proceso actual, solicitante, aprobador, regla de derivación, resultado y excepciones. Se elige una sola organización para la primera operación real y otra organización sintética para probar aislamiento. El cliente aporta criterios y ejemplos autorizados; durante esta fase se usan datos sintéticos.

La primera adaptación entrega formulario, reglas acordadas, bandeja de decisiones, historial y contrato de integración. No incluye pagos, firma legal o decisiones automatizadas reguladas por defecto. El umbral 50/51 y el campo `vehicleCount` son específicos de la demostración y deberán adaptarse junto con tests, API, UI y workflow.

## Aceptación

| Caso | Resultado esperado |
|---|---|
| Solicitud que satisface regla automática | Una activación y trazabilidad de la regla aplicada |
| Solicitud que requiere una persona | Permanece pendiente hasta decisión de un aprobador autorizado |
| Rechazo | Estado rechazado y razón conservada |
| Reintento de solicitud | Mismo resultado lógico, sin segunda solicitud |
| Persona de otra organización | Acceso denegado sin confirmar que el recurso exista |
| Decisión duplicada o contradictoria | Se conserva la decisión durable; el conflicto es observable |
| Ventana expirada | No se admiten nuevos efectos de negocio |

## Medición del piloto

Antes de operar, acordar definición y punto de captura de tiempo hasta decisión, solicitudes sin responsable, retrabajo por duplicación y decisiones con auditoría completa. Comparar un período base comparable con el piloto. Esta versión no presenta porcentajes de mejora ni beneficios económicos inventados.

## Hitos y decisión comercial

1. Demostración local y mapa del proceso aceptados.
2. Adaptación del dominio y pruebas de aceptación con ejemplos acordados.
3. Diseño de integración, identidad, retención y costo; propuesta concreta de despliegue.
4. Piloto AWS acotado solo tras completar el bootstrap y verificar el recorrido integrado en el entorno acordado.
5. Entrega de código, arquitectura, evidencia y operación; decisión del cliente sobre continuidad.

El cliente conserva capacidad de revisar el sistema y operar sus datos. El alcance, calendario y precio se estiman después de conocer la integración y la carga; no se promete una arquitectura productiva a partir de la demo local.
