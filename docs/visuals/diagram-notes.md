# Aprobaciones operativas: notas de los diagramas

Proyecto: **aws-operational-approvals**. Corte y consulta documental: **07-09-2026**.

Los dos SVG son vectores editables de 1800 × 1200, con texto Arial, título/descripción accesibles y sin imágenes, fuentes remotas ni logotipos oficiales. `solution-architecture.svg` distingue el estado actual del candidato AWS. `well-architected.svg` es una **matriz propia de revisión**, no evaluación oficial, certificación o puntuación de AWS.

## Cómo leer la arquitectura de solución

El carril superior contiene **dos recorridos independientes**. El navegador usa React/TypeScript, el adaptador demo y `localStorage`. El servidor HTTP Python en loopback usa sus servicios de dominio, callback simulado y repositorio en memoria. No hay una conexión UI→API Python. Las ocho pruebas E2E del navegador y quince comprobaciones HTTP acreditan superficies diferentes. Ver [la evidencia](../../evidence/status.md) y [la arquitectura](../architecture.md).

Las líneas continuas son relaciones locales. Las discontinuas corresponden a código/IaC candidato, no a recursos activos observados. El carril AWS contiene:

1. **CloudFront y S3 privado:** se agrupan para expresar la entrega de la PWA y la lectura autenticada del origen mediante OAC. La flecha hacia el navegador representa la entrega de contenido; no afirma que API Gateway esté detrás de CloudFront.
2. **Cognito y navegador:** intercambio de Authorization Code con PKCE. El cliente no tiene secret ni registro abierto. El navegador envía el JWT a HTTP API; no se dibuja un supuesto proxy Cognito→API en cada petición. La excepción anónima `GET /health` se omite por legibilidad; las rutas de negocio usan JWT y scope `openid`.
3. **Lambda API:** resuelve identidad/tenant, aplica cuotas e idempotencia y conserva datos en DynamoDB. Para registrar una solicitud inicia Step Functions. Para una decisión humana invoca el worker directamente; esa flecha separada evita confundir ingreso de solicitud con callback.
4. **Step Functions Standard y worker:** el workflow invoca al worker y espera el task token cuando el conteo supera 50. El worker devuelve el callback al servicio Step Functions; luego el workflow invoca la finalización. La respuesta HTTP `202` indica aceptación del comando; el resultado se consulta mediante GET. El timeout total del workflow candidato es 3.600 s, con espera humana de 3.540 s; el diagrama no promete espera indefinida.
5. **DynamoDB/KMS:** se representan lecturas/transacciones, auditoría y token cifrado con contexto. El token no se entrega al navegador. KMS también participa en el cifrado de tabla/workflow/logs según IaC; no se dibujan todas esas dependencias transversales. La tabla candidata es provisionada, 5 RCU/5 WCU, con PITR deshabilitado.
6. **EventBridge Scheduler:** invoca el worker para expiración redundante. Nace deshabilitado en el IaC; activación/readiness fija la ventana. No sustituye la comprobación de expiración en cada efecto de negocio. Su conexión exterior evita confundirlo con el camino de aprobación normal.
7. **Controles transversales:** cuatro roles IAM de ejecución, boundary, CMK, Budget y state externos; logs con retención de catorce días. El bootstrap independiente del cliente continúa pendiente. Estos archivos no constituyen un `apply` autónomo listo para producción.

El diagrama resume dependencias relevantes, sin incluir todas las respuestas HTTP, lecturas, permisos, métricas o rutas de error. La matriz de fuentes enlaza los archivos donde se puede revisar el detalle.

## Matriz AWS Well-Architected

Se utilizaron los seis nombres exactos de la [documentación oficial en español](https://docs.aws.amazon.com/es_es/wellarchitected/latest/framework/the-pillars-of-the-framework.html). La documentación presenta la revisión como una conversación sobre decisiones y mejoras, no como auditoría; nuestra matriz no se ejecutó en AWS Well-Architected Tool ni fue emitida por AWS. [Alcance del framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html).

| Pilar | Evidencia o decisión del producto | Pendiente y criterio para cerrarlo |
|---|---|---|
| Excelencia operativa | Runbook, pruebas por componente, paquetes de fuente reproducibles. El corte registra 146 tests Python y 8 E2E locales. | Acordar SLO, responsables y alertas; ejecutar y conservar un ensayo de operación/cierre del piloto del cliente. |
| Seguridad | Aislamiento por tenant y respuestas 404 probados; PKCE/JWT/OAC y cifrado KMS están implementados en el candidato. | Comprobar el flujo integrado real de identidad/autorización; revisar y aceptar o corregir cada una de las 40 exclusiones Checkov. Un resultado sin fallos no certifica seguridad productiva. |
| Fiabilidad | Idempotencia, claim recuperable y finalización condicional; aceptación `202` seguida de lectura durable. | Probar fallos y recuperación en AWS, acordar RTO/RPO e implementar recuperación acorde al cliente. PITR está deshabilitado para el caso sintético efímero, por lo que no se afirma recuperación de datos productivos. |
| Eficiencia del rendimiento | IaC fija Lambdas de 256 MB/10 s y tabla 5 RCU/5 WCU como configuración inicial. | Medir percentiles de latencia, throttling y desempeño bajo carga representativa; ajustar capacidad y memoria con resultados repetibles. Las cifras actuales son configuración, no resultados de rendimiento. |
| Optimización de costos | Cuota de diez solicitudes por tenant/día en el dominio; expiración de 192 horas en el diseño. | Registrar costo por decisión, almacenamiento, invocaciones y transferencia en una ventana real. No existe precio medido por solicitud ni ahorro acreditado; el Budget es alerta y API Gateway aplica throttling best effort. |
| Sostenibilidad | Se propone usar servicios administrados, duración limitada y retención de logs de catorce días. | Establecer consumo de recursos por solicitud/decisión, medir utilización y retención, y compararlos con una línea base adecuada. No hay medición ambiental ni estimación de carbono atribuible a este producto. |

Los pendientes son acciones propuestas a partir de código y evidencia; no son hallazgos emitidos por AWS. Elegir servicios administrados es compatible con [SUS05-BP03](https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_hardware_a4.html), pero no prueba automáticamente una reducción ambiental. La explicación de costos distingue cuota de negocio, capacidad provisionada, alertas y [throttling best effort de HTTP API](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html).

## Referencias oficiales verificadas

- **WA-OVERVIEW:** [AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html). Alcance del marco y revisión constructiva; no es un mecanismo de auditoría.
- **WA-PILLARS:** [Los pilares del marco](https://docs.aws.amazon.com/es_es/wellarchitected/latest/framework/the-pillars-of-the-framework.html). Nombres oficiales en español y conjunto exacto de seis pilares.
- **WA-OPS:** [Excelencia operativa](https://docs.aws.amazon.com/es_es/wellarchitected/latest/framework/operational-excellence.html). Marco para revisar operación y evolución del producto.
- **WA-SEC:** [Seguridad](https://docs.aws.amazon.com/es_es/wellarchitected/latest/framework/security.html). Marco de protección de datos, sistemas y activos.
- **WA-REL:** [Fiabilidad](https://docs.aws.amazon.com/es_es/wellarchitected/latest/framework/reliability.html). Marco de ejecución correcta y consistente durante el ciclo de vida.
- **WA-PERF:** [Eficiencia del rendimiento](https://docs.aws.amazon.com/es_es/wellarchitected/latest/framework/performance-efficiency.html). Uso de recursos acorde con la demanda y requisitos de desempeño.
- **WA-COST:** [Optimización de costos](https://docs.aws.amazon.com/es_es/wellarchitected/latest/framework/cost-optimization.html). Revisión del valor entregado respecto a los costos de operación.
- **WA-SUS:** [Sostenibilidad](https://docs.aws.amazon.com/es_es/wellarchitected/latest/framework/sustainability.html). Revisión del consumo de recursos y su impacto medioambiental.
- **SUS05-BP03:** [Use managed services](https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sus_sus_hardware_a4.html). Servicios administrados como decisión a evaluar, sin inferir beneficios ambientales medidos.
- **SF-CALLBACK:** [Discover service integration patterns in Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html). Standard admite waitForTaskToken; callback mediante API del servicio.
- **CF-OAC:** [Restrict access to an Amazon S3 origin](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html). Origen S3 regular privado con OAC y política acotada a la distribución.
- **COGNITO-PKCE:** [The redirect and authorization endpoint](https://docs.aws.amazon.com/cognito/latest/developerguide/authorization-endpoint.html). Intercambio de autorización del cliente de navegador y PKCE.
- **API-JWT:** [Control access to HTTP APIs with JWT authorizers](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html). Validación JWT y scopes de las rutas de negocio.
- **API-THROTTLE:** [Throttle requests to your HTTP APIs](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html). Throttling como objetivo best effort; no prueba un límite absoluto de costos.

## Trazabilidad local y límites

El catálogo completo de fuentes oficiales y archivos locales está en [sources.json](sources.json). La evidencia base es la registrada en `evidence/status.md` y `evidence/validation-local.json`; esta tarea gráfica no repitió despliegues, carga o mediciones. Se inspeccionó el código para relacionar los recursos y no trasladar cifras de otros workloads.

El tamaño de Lambda, capacidad DynamoDB y retenciones se verificaron en los archivos Terraform. Se comprobó la dirección de invocación API→worker, workflow→worker y worker→Step Functions mediante `aws_adapters.py`, `service.py` y `workflow.asl.json`. El caso de aprobación sigue siendo el ejemplo de flotas con umbral 50; no se afirma un motor universal de políticas.

## Validación de los SVG

XML parseable, `viewBox` exacto, IDs internos únicos y `title`/`desc` accesibles verificados. Se midieron 122 líneas con las métricas de Arial regular/negrita y se comprobaron 29 rectángulos: todo el texto y las formas permanecen dentro del lienzo y los anchos asignados. No hay elementos `image`, fuentes remotas ni assets externos. El texto conserva sus caracteres y los nodos son elementos SVG individuales editables.

La inspección raster final se realiza durante la integración; aquí no se generaron PNG. Los SVG muestran hechos locales y un objetivo candidato, sin puntuaciones ni sellos. No se modificaron el código, README, arquitectura principal, manifiestos de extracción o Git.
