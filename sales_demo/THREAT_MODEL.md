# Modelo de amenazas acotado — Aprobaciones operativas

| Activo / frontera | Amenaza | Control exigido | Evidencia local |
|---|---|---|---|
| Tenant | Customer enumera una solicitud ajena | `tenant_id` derivado de `sub`, clave compuesta y `404` para cruces | pruebas de repositorio y API |
| Aprobación | Customer llama endpoint de Manager | rol resuelto server-side y denegación antes de leer el recurso | pruebas de autorización |
| Callback | Task token llega al navegador o logs de aplicación | cifrado KMS con contexto, respuesta DTO sin token, `includeExecutionData=false` y logger estructurado allowlist | escaneo + tests de serialización; el historial nativo de Step Functions se declara por separado |
| Callback | Token se reutiliza o queda bloqueado tras una falla ambigua | claim condicional `READY → CLAIMED`, lease recuperable solo para la misma decisión y finalizador atómico que elimina ciphertext/claim | pruebas de conflicto, reclaim T+31 s y segunda decisión |
| Solicitud | Reintento crea duplicados | idempotency key por tenant y respuesta estable | prueba con bytes repetidos |
| Costo | Tráfico supera el objetivo de throttling | contador condicional 10/tenant/día, timeouts, capacidad provisionada | prueba del intento 11 |
| Expiración | Falla EventBridge y la app sigue escribiendo | control `now < expires_at` en cada handler y condición `pilot_status=ACTIVE` | pruebas con reloj inyectado |
| Datos | Se introducen PII o credenciales | fixtures sintéticos, validación de campos, secret scan y aviso persistente | gate de contenido |
| Frontend | XSS desde razón o nombre de empresa | React escaping, CSP de CloudFront y sin HTML arbitrario | tests y headers IaC |
| Frontend | Config runtime apunta a otro stack regional | el publicador compara API, pool, client, dominio y distribución live exactos; `deploymentBinding` liga SHA fuente y bundle | tests negativos cross-stack |
| Frontend | Refresh vencido o `401` genera loops/repite una escritura | renovación deduplicada, un solo retry HTTP, logout fail-closed; el polling nunca repite el `POST` de decisión | Vitest + E2E |
| Publicación | Falla entre el primer objeto y el manifiesto | manifiesto `PREPARING` escrito primero, allowlist de versiones y cleanup recuperable | tests de falla inyectada y retry |
| Readiness | T0 comienza con CloudFront vacío, auth rota o config ACTIVE no verificada | publicación PREPARED con `expiresAt=null`; sonda pública prueba shell, `no-store`, binding, health y denegación anónima; `/me` permite probar las cuatro sesiones sin habilitar negocio | tests de sonda, backend y runbook |
| OAuth | Interceptación de código o secret embebido | Authorization Code + PKCE, cliente público sin secret, redirect URIs exactas | assertions Terraform |
| Bucket | Lectura directa de S3 | bloqueo público, bucket owner enforced y OAC limitado por distribución | assertions Terraform |
| Activación | Infra incompleta inicia o acorta los ocho días | Git HEAD y artefactos ligados al plan; seed/PWA `PREPARED`, config/schedule verificados antes del commit, T0 exacto y `PREPARED → ACTIVE` condicional final | tests del activador y plan guard |
| Activación | Resultado ambiguo deja `ACTIVE` sin expiración redundante | rollback solo si DynamoDB confirma `PREPARED`; ante ambigüedad se conserva el schedule armado | tests de readback fallido |
| Operación | Demo local se presenta como servicio vivo | banner persistente, `T0` ausente hasta readiness desplegado y estado explícito en README; el E2E de negocio corre tras `ACTIVE` | tests de UI y documentación |

## Fuera de alcance

El piloto no procesa PII real, tarjetas, KYC, firma electrónica legal, aprovisionamiento físico ni integraciones externas. Esos dominios requieren threat models y controles regulatorios separados.
