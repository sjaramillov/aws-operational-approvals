# Operación local y preparación de un piloto

## Demostración visual

Desde la raíz, instalar con `npm ci --prefix sales_demo/web` y ejecutar `make demo`. Abrir el origen loopback indicado. El modo demo guarda datos sintéticos en el navegador. Cambiar de persona mantiene el estado; limpiar únicamente el almacenamiento de ese origen reinicia la demo.

Recorrido: Customer A registra 50 (automático), 51 (Manager A aprueba), 52 (Manager A rechaza). Customer B no puede abrir una solicitud de A. Revisar actor, razón y transición en la auditoría. No se envían correos ni se activa un servicio real.

## Backend local

`make demo-api` inicia la API Python en `127.0.0.1:8081`. La cabecera `x-demo-sub` acepta las cuatro identidades sintéticas documentadas. El repositorio en memoria desaparece al terminar el proceso. La UI demo no conecta con esta API.

`make smoke-api` levanta una instancia efímera en un puerto loopback libre, comprueba salud, autenticación sintética, aprobación, rechazo, idempotencia y acceso cruzado, y siempre cierra el servidor. No usa credenciales AWS.

## Comprobaciones

```bash
make python-check PYTHON=.venv/bin/python
make web-check
make e2e
make smoke-api PYTHON=.venv/bin/python
make packages PYTHON=.venv/bin/python
make terraform-static
python3 scripts/verify_source_manifest.py
```

`web-check` produce el build del cliente AWS; `e2e` produce y prueba el modo demo. Los ZIP reproducibles son artefactos locales ignorados. Los outputs de herramientas se conservan localmente en `evidence/local/`; la evidencia curada no incluye rutas privadas ni tokens.

Terraform validate y tests mock requieren que el provider fijado esté disponible. La inicialización `terraform -chdir=sales_demo/terraform init -backend=false` puede descargar providers; no utilizar credenciales operativas para esos checks. Después se pueden ejecutar `validate` y `test` con datos mock, nunca `plan` o `apply` como parte del check local.

## AWS antes de cualquier efecto externo

Completar el bootstrap independiente y sus permisos, revisar costos y duración, crear ejemplos sintéticos y declarar una región/cuenta del cliente. Adaptar los gates de la cuenta original, revisar un plan nuevo ligado al commit y probar el recorrido integrado. El estado inicial propuesto es `PREPARED`; `ACTIVE` solo después de readiness verificado. Presupuesto es alerta, no límite técnico de gasto.

Los scripts operativos heredados permanecen para revisión y futura adaptación. No existe despliegue automático en este repositorio. Los nombres neutralizados no apuntan a la cuenta original y no sustituyen la definición de guardrails del cliente.

## Cierre

Local: detener Vite/Python con Ctrl+C; el smoke cierra su proceso automáticamente. Futuro AWS: documentar expiración, retirar únicamente el workload autorizado y verificar inventario independiente. State, planes, outputs operativos y credenciales se conservan fuera de Git; no publicar resultados crudos del teardown.
