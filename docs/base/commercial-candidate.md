> Base de ingeniería extraída y sanitizada. Describe el candidato de origen; su estado actual y las pruebas de esta copia están en `../../evidence/status.md`. No es autorización de despliegue ni acreditación de derechos de terceros.

# Piloto local Solicitudes + Servicio activo


## Qué demuestra

- Customer crea una solicitud sintética para 50, 51 o 52 vehículos.
- 50 vehículos siguen la regla de aprobación automática.
- Más de 50 vehículos esperan una decisión humana durable.
- Manager puede aprobar 51 y rechazar 52 con un código de razón.
- Cada transición conserva actor sintético, instante UTC, decisión y correlación.
- La misma clave de idempotencia no crea una segunda solicitud.
- Un usuario de otro tenant obtiene `404`, sin confirmar la existencia del recurso.
- Una aprobación genera un contrato sintético y activa una vista mínima de Servicio activo.

No demuestra pagos, KYC, firma legal, aprovisionamiento, telemetría, disponibilidad productiva ni procesamiento de PII. La interfaz debe mostrar siempre: **“Demostración · datos sintéticos · sin PII real”**.

## Estructura

```text
sales_demo/
├── backend/       # Lambda API y worker/callback; dominio y adaptadores
├── terraform/     # state y perfil sales_demo independientes
├── web/           # PWA React/Vite/TypeScript
├── scripts/       # gates y paquetes Lambda reproducibles, sin acceso AWS
├── tests/         # contratos cruzados y pruebas por componente
├── openapi.yaml   # contrato HTTP público del piloto
└── workflow.asl.json
```

## Invariantes de negocio

1. `tenant_id` nunca se acepta desde body, query string ni cabeceras arbitrarias. Se resuelve en servidor a partir del `sub` autenticado.
2. Una solicitud pertenece a un solo tenant y todas las búsquedas aplican tenant + recurso; un cruce responde `404`.
3. `vehicle_count <= 50` no crea una tarea de Manager. `vehicle_count > 50` no puede activarse sin una decisión humana exitosa.
4. El task token nunca aparece en el navegador, las respuestas HTTP, los logs de aplicación ni la auditoría. Se cifra con contexto KMS; el worker usa un claim `READY/CLAIMED` recuperable y el finalizador elimina ciphertext y claim atómicamente. El historial nativo cifrado de Step Functions sí contiene el contexto interno necesario para el callback y no se presenta como evidencia pública.
5. Cada tenant puede crear como máximo diez solicitudes por día UTC. El contador DynamoDB, no el throttling de API Gateway, aplica el límite duro.
6. Antes de cualquier efecto lateral, API, worker y callback prueban `pilot_status=ACTIVE` y `now < expires_at`.
7. `/health` puede reportar `EXPIRED`; todos los demás endpoints responden `410` después de la expiración.
8. Los estados durables son `PENDING_MANAGER`, `APPROVED`, `REJECTED`, `CONTRACT_ACTIVE` y `EXPIRED`. Color y animación nunca sustituyen el texto.

## Arquitectura desplegable propuesta

La PWA usa un bucket S3 privado como origen regular de CloudFront. OAC firma siempre las solicitudes y la policy del bucket limita lectura a la distribución. Cognito se fija explícitamente en tier `LITE`, sin autorregistro, con Hosted UI clásico v1 materializado por Terraform; el cliente público usa solo Authorization Code + PKCE y no tiene secret. La rotación de refresh token excluye explícitamente `ALLOW_REFRESH_TOKEN_AUTH`. API Gateway HTTP API valida JWT y aplica throttling de stage/ruta como objetivo *best effort*.

La Lambda API aplica autorización, idempotencia y cuotas de negocio. Una Step Function **Standard** decide la ruta 50/51 y espera callback para aprobación humana. El worker cifra el task token con contexto KMS antes de persistirlo; al aceptar una decisión adquiere un lease condicional, descifra el token y responde `202`. El workflow finaliza luego la decisión, la auditoría, el estado y la limpieza del ciphertext en una sola transacción; el cliente consulta ese estado durable. DynamoDB usa una tabla single-table provisionada a 5 RCU/5 WCU. EventBridge marca expiración y deshabilita usuarios como redundancia; la comprobación en tiempo de petición es el control primario.

Referencias técnicas oficiales:

- [Cognito: Authorization Code + PKCE para clientes públicos](https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-client-apps.html)
- [HTTP API throttling es best effort](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-throttling.html)
- [Step Functions: callback con task token](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html)
- [CloudFront OAC para un origen S3 privado](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)

## Presupuesto y expiración

El diseño limita la exposición por construcción: Lambdas de 256 MB y 10 segundos, DynamoDB 5/5, frontend menor de 10 MiB, 5 RPS/burst 10 global, 1 RPS/burst 2 para escrituras, diez solicitudes por tenant/día y máximo cuatro usuarios sintéticos. El Budget USD 20 sigue siendo una alerta, no un límite técnico.

`T0` solo se define después de que el E2E local completo y el readiness público
desplegado en estado `PREPARED` estén verdes. Ese readiness no ejecuta ventas:
prueba el PWA servido, el binding de release, `/health=PREPARED`, la denegación
anónima y las cuatro identidades/mappings. `expires_at = T0 + 192 h`; el destroy
debe ocurrir dentro de la hora siguiente. Como este candidato es local, todavía
no existe `T0` ni una afirmación de disponibilidad.

## Gate de despliegue

Un despliegue solo sería admisible en una ventana AWS nueva, fechada y aprobada, y únicamente si todos estos gates locales pasan:

- Terraform fmt, validate, test, TFLint y Checkov.
- Policies IAM por debajo de 6.144 caracteres y `plan_guard` fail-closed.
- Tests de backend, contrato OpenAPI, TypeScript, Vitest y Playwright sin `skip`.
- E2E 50 automático, 51 aprobado, 52 rechazado y denegación entre tenants.
- WCAG 2.2 AA, teclado, zoom 200 %, movimiento reducido y QA responsive.
- Escaneo de secretos e identificadores; cero account IDs, tokens, estados o planes versionados.

Si falta una función o un gate, este directorio permanece local y no se fusiona, etiqueta ni despliega.

El gate integral se ejecuta desde este directorio con `make check-local`. Los
ZIP Lambda se construyen de forma determinista en `build/` —ignorado por Git— y
`make packages` exige que una segunda construcción produzca los mismos hashes.

## Ciclo operativo futuro (no ejecutado)

Este candidato **no se activa durante su construcción local**. En una ventana
AWS nueva, fechada y aprobada, el operador seguirá este orden fail-closed:

1. Partir de un commit limpio y ejecutar `make check-local`. Crear los ZIP
   reproducibles y el bundle web antes de producir el plan.
2. Aplicar guardrails con `sales_demo_runtime_enabled=true` y
   `sales_demo_deployment_enabled=true`. El primer flag conserva boundary y
   contexto KMS mientras viva el workload; el segundo adjunta temporalmente las
   dos policies de control plane. Este apply también materializa el bucket y la
   tabla de lock persistentes del backend comercial; sus nombres salen de los
   outputs de guardrails y nunca contienen el ID de cuenta en claro.
3. Con dos perfiles explícitos —el source profile que STS resuelve al usuario
   exacto `approvals-local-login` y el profile asumido que resuelve a
   `APPROVALS-TerraformDeploymentRole`— ejecutar `terraform/preflight.py`. La sonda
   falla ante cuenta distinta de `FREE/ACTIVE`, créditos API no positivos o con
   vigencia menor a 193 h, Budget distinto de USD 20, cuota Lambda distinta de
   10, access keys permanentes, falta de MFA, Organizations, acuerdos activos de
   Marketplace, entitlement de Support Business/Enterprise, Kinesis/Firehose o
   telemetría, drift de las siete policies esperadas o inventario comercial no
   cero. `SubscriptionRequiredException` solo excluye el entitlement cubierto
   por la API de Support; no se presenta como prueba general de facturación. La
   salida sanitizada `0600` expira en 15 minutos y no contiene cuenta, ARN ni
   correo en claro.
4. Inicializar el state remoto aislado con `backend.s3.hcl` privado construido
   únicamente a partir de los cinco outputs `sales_demo_backend_*` de
   guardrails. Crear un
   `tfplan` y su JSON con modo `0600`, ejecutar `terraform/plan_guard.py` con
   `--frontend-build-dir` y `--preflight-json`; el guard liga Git HEAD limpio,
   el preflight todavía vigente, los dos ZIP Lambda y `web/dist` a los hashes
   del plan. Aplicar exactamente el plan revisado.
   Terraform deja el piloto en `PREPARED`, el schedule deshabilitado y
   `expires_at_epoch=0`.
5. Ejecutar `terraform/provision_users.py apply` con `--profile`,
   `--credentials-output` hacia un archivo nuevo `0600` fuera del repositorio y
   la sesión asumida de `APPROVALS-TerraformDeploymentRole`. El postflight exige exactamente
   cuatro usuarios habilitados, estado Cognito utilizable, grupos exactos y los
   cuatro mappings hash de `sub`. Las claves se leen con `getpass` o se generan
   dentro del proceso mediante `--generate-private-credentials`; nunca aparecen
   en argumentos o logs, y el archivo privado solo se materializa después de
   ese postflight.
6. Publicar el mismo bundle aprobado con `terraform/publish_frontend.py publish`
   y el output `frontend_runtime_config` de Terraform, cuyo `expiresAt` es
   `null`. El comando standalone rechaza cualquier configuración ACTIVE.
7. Ejecutar `terraform/readiness_probe.py`: debe observar el PWA por CloudFront,
   `runtime-config.json` con `no-store` y binding exacto, `/health=PREPARED` y
   `/me=401` sin token. Completar además el postflight de las cuatro identidades
   Cognito y sus cuatro mappings DynamoDB exactos; autenticar cada usuario por
   PKCE y comprobar su propio `/me`, la pantalla PREPARED y navegación de
   negocio ausente. `GET /me` es la única lectura autenticada admitida antes de
   T0. No se ejecutan ventas ni aprobaciones; `business_effects=0` y T0 sigue
   sin existir.
8. Ejecutar `terraform/activate_pilot.py apply`. El activador liga bundle y
   configuración a los recursos live exactos, publica primero un manifiesto
   recuperable `PREPARING`, verifica `runtime-config.json` con `no-store`, arma
   la expiración y espera un T0 acotado. `ACTIVE` es la última escritura
   condicional; si se pierde el segundo exacto de T0, no acorta silenciosamente
   la ventana y conserva `PREPARED`.
9. Ejecutar los E2E desplegados 50/51/52 y aislamiento inmediatamente después
   de `ACTIVE`. Solo un resultado verde establece el inicio de disponibilidad
   observada; no crea un SLA. Si falla, cerrar y destruir, sin afirmar ocho días.
   Después de un resultado verde, reaplicar guardrails con
   `sales_demo_deployment_enabled=false`, manteniendo
   `sales_demo_runtime_enabled=true` durante las 192 horas.

No se guardan comandos con valores reales. Cuenta, ARNs completos, nombre del
bucket, planes, outputs y backend config viven únicamente en preflights
privados. La URL de API, pool/client/dominio Cognito y origen CloudFront son
identificadores públicos no secretos que aparecen solo en `runtime-config.json`
servido con `no-store`; el repositorio conserva únicamente placeholders.

### Cierre obligatorio

Las requests iniciadas en o después de `expires_at` fallan cerradas aunque el
evento redundante se retrase. Un efecto admitido justo antes del corte queda
acotado por el timeout Lambda de diez segundos; las transiciones posteriores
revalidan el estado durable. EventBridge intenta marcar `EXPIRED` y deshabilitar
las cuatro identidades. Dentro de la hora siguiente:

1. Rehabilitar temporalmente `sales_demo_deployment_enabled=true`.
2. Ejecutar `publish_frontend.py cleanup`; solo acepta el manifiesto confiable,
   elimina versiones exactas y aborta multipart uploads allowlisted.
3. Destruir el plan del root `sales_demo/terraform`. PITR queda deshabilitado
   para no crear un backup `SYSTEM` de 35 días al borrar la tabla sintética.
4. Probar inventario comercial con la sonda paginada y fail-closed:

   ```bash
   python3 terraform/inventory_zero.py \
     --profile <assumed-deployment-role-profile> \
     --expected-account-id <account_id> \
     --region us-east-1 \
     --phase workload \
     --guardrail-prefix <guardrail-prefix> \
     --output <private-0600-workload-inventory.json>
   ```

   Solo `status=WORKLOAD_ZERO`, con las tres policies comerciales persistentes
   allowlisted y presentes, permite continuar. `WORKLOAD_NON_ZERO` exige
   limpieza; `INCONCLUSIVE` bloquea la afirmación aunque no haya hallazgos.
5. Aplicar guardrails con ambos flags en `false`, retirando policies, boundary y
   contextos KMS comerciales.
6. Repetir la sonda con `--phase guardrails`, el mismo
   `--guardrail-prefix` y un segundo output privado. Esta fase usa las lecturas
   IAM base del rol persistente y solo `status=GUARDRAILS_ZERO` prueba que las
   tres policies comerciales y sus attachments fueron retirados.

Si una publicación se interrumpe, el manifiesto previo `PREPARING` permite
reintentar o limpiar. Si la escritura `ACTIVE` queda ambigua, el activador nunca
deshabilita el schedule de expiración hasta clasificar el estado durable. En ese
caso se ejecuta `activate_pilot.py reconcile` con el mismo perfil, SHA, release,
tabla, bucket y schedule privados. El comando no muta AWS y devuelve únicamente:

- `ACTIVE`: contrato DynamoDB/runtime/schedule exacto; continuar E2E;
- `PREPARED`: obedecer `operator_action`; si el runtime aún anuncia expiry,
  republicar primero el runtime `PREPARED` con `expiresAt=null`, luego desarmar
  el schedule y recién entonces reintentar;
- `UNEXPECTED`: no reintentar ni desarmar el schedule; cerrar con diagnóstico y
  teardown controlado.

## Límites honestos de exposición

El contador DynamoDB sí fija un máximo duro de diez solicitudes por tenant y
día: dos tenants durante ocho días implican como máximo 160 workflows de venta.
Los objetivos de API Gateway (5 RPS/burst 10 y 1 RPS/burst 2 en escrituras) son
*best effort*; el Budget USD 20 es una alerta y CloudFront no tiene un cap duro
de transferencia. Por eso 20.000 llamadas y 1 GiB son envolventes operativas,
no garantías técnicas de gasto máximo.

La HTTP API usa CORS `*` sin credenciales para romper el ciclo de dependencias
CloudFront↔API; CORS no se presenta como autorización. La protección efectiva es
JWT con scope `openid`, mapping `sub`→tenant en DynamoDB, CSP con los hosts API y
Cognito exactos y binding live del `runtime-config` en el publicador.
