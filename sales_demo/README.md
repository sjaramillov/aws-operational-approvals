# Candidato técnico de Aprobaciones operativas

Código de dominio, API, interfaz, IaC y pruebas de una demostración con dos organizaciones sintéticas. El recorrido incluye solicitud automática, aprobación humana y rechazo auditable. El estado actualizado pertenece a [la evidencia de esta copia](../evidence/status.md).

## Arranque

Desde la raíz del repositorio: `make demo` para UI en navegador, `make demo-api` para API Python separada y `make check-local` para controles sin AWS. Usa `npm ci --prefix sales_demo/web` y las dependencias Python fijadas antes del primer arranque.

La UI demo usa almacenamiento local del navegador y fixtures. No llama al backend Python ni acredita autenticación real. El cliente HTTP/Cognito y el backend AWS están implementados como candidato, pero no se han desplegado desde esta copia.

## Contratos

- `backend/`: dominio, autorización, idempotencia, callback y almacenamiento.
- `openapi.yaml`: ocho operaciones; la decisión devuelve `202` y se consulta el resultado durable.
- `workflow.asl.json`: workflow Standard con espera de decisión humana.
- `web/`: frontend React y modos local/HTTP explícitos.
- `terraform/`: módulo comercial y validadores que requieren guardrails externos.

La regla concreta es hasta 50 vehículos automático, más de 50 con decisión del Manager. La entrada del tenant se resuelve por identidad; no se acepta libremente desde el body. Se mantiene el límite de diez solicitudes por organización/día y la expiración en cada efecto.

El diseño AWS sigue dependiendo de un bootstrap independiente que no se incluye como desplegado ni completado. Véase [arquitectura](../docs/architecture.md), [runbook](../docs/runbook.md) y [base comercial conservada](../docs/base/commercial-candidate.md).
