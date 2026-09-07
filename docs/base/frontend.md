> Base de ingeniería extraída y sanitizada. Describe el candidato de origen; su estado actual y las pruebas de esta copia están en `../../evidence/status.md`. No es autorización de despliegue ni acreditación de derechos de terceros.

# PWA Solicitudes + Servicio activo

Interfaz React/Vite/TypeScript del candidato comercial condicionado. El modo demo es local, usa únicamente fixtures sintéticos y no llama AWS. El build normal prepara el cliente HTTP y Cognito Authorization Code + PKCE sin client secret.

## Modos explícitos

- `npm run dev`: servidor local en modo demo. La constante se fija en `vite.config.ts`; no depende de un `.env` ignorado.
- `npm run build:demo`: build estático con las cuatro personas sintéticas y el adaptador de `localStorage`.
- `npm run build`: build para API/Cognito. No requiere todavía URLs, IDs ni outputs de Terraform.

El modo demo ofrece `customer-a`, `manager-a`, `customer-b` y `manager-b` sin contraseñas ni correos. Cambiar de persona conserva las solicitudes del navegador para demostrar 50 automático, 51 aprobado, 52 rechazado y el `404` entre tenants. Restablecer el storage del origen reinicia la demostración. Este modo no solicita ni carga `/runtime-config.json`.

## Configuración pública post-apply

El build de producción carga `/runtime-config.json` al arrancar. Esto rompe el ciclo entre el hash del bundle y los nombres que AWS solo conoce después del primer `apply`. El archivo no contiene secretos y usa exactamente el output Terraform `frontend_runtime_config`.

```bash
npm run build
cd ../terraform
terraform output -json frontend_runtime_config \
  | node ../web/scripts/write-runtime-config.mjs /private/tmp/approvals-sales-runtime-config.json
python3 publish_frontend.py publish \
  --profile "$AWS_PROFILE" \
  --expected-account-id "$EXPECTED_ACCOUNT_ID" \
  --region "$AWS_REGION" \
  --bucket "$WEB_BUCKET" \
  --distribution-id "$DISTRIBUTION_ID" \
  --runtime-config /private/tmp/approvals-sales-runtime-config.json \
  --build-dir ../web/dist \
  --expected-release-sha256 "$FRONTEND_RELEASE_SHA256" \
  --source-revision "$SOURCE_REVISION" \
  --api-id "$API_ID" \
  --user-pool-id "$USER_POOL_ID" \
  --client-id "$COGNITO_CLIENT_ID" \
  --cognito-domain-prefix "$COGNITO_DOMAIN_PREFIX"
```

El JSON operacional se escribe con modo `0600` fuera de `dist/`; nunca se
sincroniza manualmente. `publish_frontend.py` recibe el bundle y el JSON por
separado, coteja recursos live y publica este último con `Cache-Control:
no-store`. El hash de release corresponde al bundle reproducible y **excluye**
`runtime-config.json`.

El bootstrap falla de forma cerrada y visible si falta el archivo, el fallback SPA devuelve HTML o el contrato diverge. En producción solo acepta HTTPS, región AWS válida, API Gateway y Cognito en esa región, user pool prefijado por región, callback/logout sobre el mismo origen CloudFront y un `deploymentBinding` exacto con SHA Git + SHA-256 del bundle. El publisher coteja ese binding contra outputs live y bytes reales; el cliente valida su forma sin presentarlo como prueba suficiente del stack. El único scope OIDC es `openid`, igual que en Cognito IaC. La configuración se publica mientras el backend reporta `PREPARED`; la UI no monta rutas ni ejecuta llamadas de negocio hasta observar `ACTIVE`. Así, la publicación y el readiness preceden al compromiso de T0.

## Contrato API consumido

El cliente de `src/api/http.ts` sigue `../openapi.yaml`:

- `GET /health`, `GET /me` (`PREPARED | ACTIVE | EXPIRED`; `expiresAt` es `null` mientras está `PREPARED`)
- `POST /applications` con `Idempotency-Key`
- `GET /applications`, `GET /applications/{applicationId}`
- `GET /approvals`, `POST /approvals/{applicationId}/decision`
- `GET /plan-plus`

`POST /approvals/{applicationId}/decision` responde `202` con `{ applicationId, accepted: true, status: "PENDING_MANAGER" }`; no inventa un resultado final. La interfaz bloquea permanentemente el comando aceptado y consulta `GET /applications/{applicationId}` con backoff acotado hasta `CONTRACT_ACTIVE` o `REJECTED`. Si vence el timeout solo ofrece volver a consultar: nunca reenvía el `POST`.

El frontend nunca envía `tenantId`. Lo obtiene de `/me`; la autorización real y el `404` indistinguible son responsabilidades obligatorias del backend. Access y refresh token Cognito viven únicamente en `sessionStorage`; no hay client secret, registro público ni task token en el bundle. Antes del `exp` del JWT —con 60 segundos de margen— se ejecuta un solo grant `refresh_token`; si Cognito rota el refresh token se reemplaza atómicamente. Un `401` permite exactamente una renovación y un reintento; otro `401` o un refresh fallido limpia ambos tokens y obliga a ingresar de nuevo, sin bucles ni logs de tokens. El callback valida state/verifier, elimina `code` y `state` de la URL antes del intercambio y limpia la transacción PKCE tanto en éxito como en fallo. El service worker no cachea queries, callback OAuth, API ni configuración runtime.

## Gates locales

```bash
npm install
npm run check
npm run test:e2e
```

`check` ejecuta TypeScript, Vitest, el contrato post-apply de `runtime-config` y el build de producción. Playwright ejecuta los recorridos 50/51/52, aislamiento de tenant, teclado, axe, política de Cache Storage y viewport de 320 px sin `skip`. El bundle generado es menor de 10 MiB y contiene manifest más service worker.

Que estos gates estén verdes no autoriza despliegue ni demuestra disponibilidad AWS. El banner persistente identifica el alcance: **“Demostración · datos sintéticos · sin PII real”**.

## E2E remoto del piloto desplegado

El carril remoto es independiente de `playwright.config.ts` y del demo local. No
levanta Vite, no selecciona fixtures ni escribe identidades en `localStorage`:
abre la distribución CloudFront, sigue Cognito Hosted UI y completa
Authorization Code + PKCE real. El runner autentica las cuatro identidades
sintéticas y crea exactamente tres solicitudes en `tenant-a`: 50, 51 y 52. La
comprobación cruzada reutiliza la solicitud 51 y, por tanto, no consume una
cuarta escritura.

El comando falla antes del primer navegador si falta cualquiera de estos datos:

- `APPROVALS_E2E_BASE_URL`: raíz HTTPS de la distribución CloudFront, sin ruta.
- `APPROVALS_E2E_CREDENTIALS_FILE`: ruta absoluta a un JSON privado `0600`, fuera del
  repositorio y no enlazado mediante symlink.
- `APPROVALS_E2E_EXPECTED_SOURCE_REVISION`: SHA Git completo de 40 caracteres.
- `APPROVALS_E2E_EXPECTED_RELEASE_SHA256`: hash SHA-256 del bundle aprobado.
- `APPROVALS_E2E_MODE`: `PREPARED_READINESS` antes de T0 o `ACTIVE_BUSINESS`
  inmediatamente después de T0.
- `APPROVALS_E2E_EXPECTED_EXPIRES_AT`: expiración RFC3339 exacta comprometida al
  activar T0; solo se define en `ACTIVE_BUSINESS`.
- `APPROVALS_E2E_CONFIRM_PREPARED_READS=YES`: autoriza exclusivamente los cuatro
  logins PKCE y `/me` mientras las operaciones de negocio siguen bloqueadas.
- `APPROVALS_E2E_CONFIRM_ACTIVE_WRITES=YES`: reconocimiento explícito de las tres
  escrituras sintéticas.

El archivo privado tiene este contrato. Los valores de contraseña se introducen
con un editor seguro; nunca se pasan como argumentos de shell, se versionan ni
se incluyen en una evidencia:

```json
{
  "schemaVersion": 1,
  "syntheticOnly": true,
  "users": {
    "customerA": { "username": "customer-a", "password": "<private>" },
    "managerA": { "username": "manager-a", "password": "<private>" },
    "customerB": { "username": "customer-b", "password": "<private>" },
    "managerB": { "username": "manager-b", "password": "<private>" }
  }
}
```

`terraform/provision_users.py apply --credentials-output <ruta-absoluta>` recibe
cada contraseña dos veces mediante `getpass` o genera cuatro valores únicos en
memoria con `--generate-private-credentials`; completa el postflight Cognito y
DynamoDB y solo entonces crea este archivo con modo `0600`. La ruta se valida
antes de cualquier mutación y de nuevo antes de escribir. Debe ser
nueva, pertenecer al operador, vivir fuera del repositorio y tener un directorio
padre sin acceso de grupo/otros. El provisionador no sobrescribe archivos ni
imprime la ruta o los valores. No se deben copiar contraseñas desde historial,
logs, outputs de Terraform ni variables de entorno. El archivo se conserva solo
hasta terminar el E2E y el teardown de las cuatro identidades.

El operador obtiene los identificadores públicos y hashes de sus preflights
privados ya aprobados; el runner no llama AWS CLI ni acepta access keys, perfil
AWS, client secret o account ID. Antes de autenticarse coteja
`runtime-config.json`, `deploymentBinding`, `expiresAt`, `syntheticDataOnly` y
`GET /health=ACTIVE`. Después ejecuta:

```bash
chmod 600 <absolute-private-credentials.json>
export APPROVALS_E2E_BASE_URL='https://<distribution>.cloudfront.net'
export APPROVALS_E2E_CREDENTIALS_FILE='<absolute-private-credentials.json>'
export APPROVALS_E2E_EXPECTED_SOURCE_REVISION='<full-git-sha>'
export APPROVALS_E2E_EXPECTED_RELEASE_SHA256='<bundle-sha256>'
export APPROVALS_E2E_MODE='ACTIVE_BUSINESS'
export APPROVALS_E2E_EXPECTED_EXPIRES_AT='<exact-rfc3339>'
export APPROVALS_E2E_CONFIRM_ACTIVE_WRITES='YES'
npm run test:e2e:deployed
```

Antes de activar T0 se ejecuta el mismo runner con
`APPROVALS_E2E_MODE=PREPARED_READINESS`, `APPROVALS_E2E_CONFIRM_PREPARED_READS=YES` y sin
las dos variables de expiración/escritura. Ese gate autentica los cuatro
usuarios por PKCE, valida `/me` y exige la pantalla PREPARED; no crea solicitudes.

La configuración remota fija un worker, cero retries, `maxFailures=1` y
deshabilita traces, screenshots, video, descargas y reportes HTML. No persiste
`storageState`; access/refresh tokens y contraseñas viven solo en los contextos
de navegador efímeros. El resultado verifica 50 automático con contrato y Plan
Plus, 51 aprobado por el Manager, 52 rechazado, aceptación `202` seguida de
polling GET sin repetir el POST, y el `404` indistinguible entre tenants.

Cada recorrido completo consume tres de las diez solicitudes diarias de
`tenant-a`. Debe ejecutarse una sola vez inmediatamente después de `ACTIVE`; no
se relanza automáticamente ante un fallo. Estructura y permisos se prueban sin
red ni secretos con `npm run test:e2e:deployed:config`. Un verde remoto prueba
el recorrido observado en esa ventana, no un SLA ni una disponibilidad futura.
