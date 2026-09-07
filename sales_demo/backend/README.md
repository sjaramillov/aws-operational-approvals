# Backend local Aprobaciones operativas

El backend mantiene dominio y contratos independientes de AWS. Los imports de
`boto3` son tardíos, por lo que `pytest` ejecuta los flujos 50/51/52, expiración,
cuota, aislamiento e idempotencia sin credenciales.

## Entrypoints

- API HTTP v2: `sales_demo.backend.lambda_api.lambda_handler`
- Worker/callback: `sales_demo.backend.lambda_worker.lambda_handler`
- Demo local: `python -m sales_demo.backend.local_server --port 8081`

El servidor local reconoce únicamente los cuatro `sub` sintéticos mediante
`x-demo-sub`: `customer-a`, `manager-a`, `customer-b` y `manager-b`. Esa cabecera
no existe en el despliegue: API Gateway entrega el `sub` validado por su JWT
authorizer.

## Variables de entorno

| Lambda | Variable | Propósito |
|---|---|---|
| ambas | `SALES_PILOT_ID` | identifica el control de estado/expiración |
| ambas | `SALES_TABLE_NAME` | tabla DynamoDB single-table |
| API | `SALES_STATE_MACHINE_ARN` | workflow Standard iniciado asíncronamente |
| API | `SALES_WORKER_FUNCTION_NAME` | invocación interna de la decisión |
| API | `SALES_DAILY_LIMIT` | límite duro, `10` por defecto |
| worker | `SALES_KMS_KEY_ID` | CMK para el task token |
| worker | `SALES_USER_POOL_ID` | pool cuyos usuarios sintéticos se deshabilitan al expirar |
| worker | `SALES_SYNTHETIC_USERNAMES` | allowlist exacta `customer-a,manager-a,customer-b,manager-b` |

## Claves persistentes

| PK | SK | Contenido |
|---|---|---|
| `PILOT#<pilot_id>` | `META` | `status`, `expires_at_epoch` |
| `IDENTITY#<sha256(sub)>` | `PROFILE` | tenant, rol, nombre sintético, enabled |
| `TENANT#<tenant>` | `APP#<application_id>` | solicitud y auditoría durable |
| `TENANT#<tenant>` | `IDEMPOTENCY#<sha256(key)>` | correlación y fingerprint del body |
| `TENANT#<tenant>` | `QUOTA#<YYYY-MM-DD>` | contador condicional diario |
| `TENANT#<tenant>` | `PLAN#PLUS` | contrato sintético activo |

El `sub` nunca se persiste en claro. El task token se cifra con contexto KMS
`pilot + tenant + application` y no llega al navegador ni a los logs de las
Lambdas de la aplicación. El worker adquiere un claim `READY → CLAIMED` con
lease antes de enviarlo a Step Functions; no ejecuta ninguna escritura después
de `SendTaskSuccess`. El finalizador posterior conserva decisión y dos hitos de
auditoría, cambia el estado y elimina ciphertext/claim en una sola transacción.

La respuesta de decisión es `202 Accepted`: confirma el callback, no el estado
final. El cliente observa `CONTRACT_ACTIVE` o `REJECTED` mediante `GET`.

El token sí forma parte del contexto interno del callback administrado por Step
Functions. El despliegue debe cifrar su execution history con la CMK, restringir
su lectura por IAM y configurar CloudWatch con `includeExecutionData=false`.
Esto acota la afirmación: **no aparece en browser ni logs de aplicación**, no
que sea inexistente dentro del servicio administrado.

La idempotencia de arranque necesita `states:DescribeExecution`; un cierre
`FAILED`, `TIMED_OUT` o `ABORTED` solo usa `states:RedriveExecution` cuando AWS
devuelve explícitamente `redriveStatus=REDRIVABLE`. Una ejecución cerrada no
redrivable produce un fallo de dependencia, nunca un falso éxito.

## Ejecución local

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q sales_demo/tests/backend
PYTHONDONTWRITEBYTECODE=1 python3 -m sales_demo.backend.local_server --port 8081
```

Ejemplo de lectura local:

```bash
curl -H 'x-demo-sub: customer-a' http://127.0.0.1:8081/me
```

Los adaptadores `memory.py` son exclusivamente una simulación verificable; el
despliegue usa condiciones DynamoDB y cifrado KMS de `aws_adapters.py`.
