# Estado de evidencia

Fecha de extracción: 7 de septiembre de 2026. Esta evidencia corresponde exclusivamente a la copia independiente del producto de aprobaciones.

La copia se comprobó localmente con resultados satisfactorios en los controles ejecutados. No se aplicó Terraform, no se accedió a una cuenta AWS y no se ejecutaron E2E remotos. El reporte legible por máquina está en [validation-local.json](validation-local.json) y queda ligado al hash del manifiesto de extracción.

| Comprobación reproducida | Resultado |
|---|---|
| Contrato cruzado y OpenAPI | PASS; ocho operaciones |
| Python: dominio/backend, contratos y herramientas de infraestructura | 146 tests aprobados |
| TypeScript y cliente de producción | Typecheck y build aprobados |
| Vitest | 38 tests aprobados |
| Configuración del runner remoto, sin acceder a un despliegue | 6 tests unitarios aprobados |
| Playwright sobre demo local | 8 tests aprobados |
| HTTP real en loopback contra la API Python en memoria | 15 comprobaciones aprobadas |
| Paquetes de fuente Lambda | Dos ZIP de 19.823 bytes, idénticos y reproducibles |
| Terraform | Formato, validate y un test mock aprobados |
| Plan guard estático y TFLint | Aprobados |
| Checkov | 171 aprobados, 0 fallidos, 40 exclusiones declaradas |
| Procedencia | Hash fuente y destino coinciden para 123 importaciones |
| QA visual | Login y solicitud pendiente revisados, con identidad neutral |

Los E2E incluyen aprobación automática, aprobación humana, rechazo, aislamiento, teclado, validación axe de pantallas muestreadas, ancho de 320 px y exclusión del código OAuth del cache. No equivalen a una auditoría WCAG completa.

Entorno: Python 3.14, pytest 9.1.1, Node 26.0.0, npm 11.12.1, Terraform 1.15.5 y provider AWS 6.61.0. Se reutilizaron dependencias disponibles; no hizo falta descargar paquetes. El provider usado fue local; esta ejecución no obtuvo una nueva verificación de firma desde el registro.

Los resultados históricos del proyecto de origen no se trasladan como éxito de este repositorio. La UI de navegador y la API Python son superficies locales separadas. Una prueba de una no acredita su integración con la otra.

El bootstrap externo de guardrails no está incluido como completo. Las exclusiones Checkov, el empaquetado del SDK usado por Lambda y la adaptación del contrato de cuenta requieren revisión antes del piloto AWS. No se acreditan tiempos de respuesta, ahorro, costos reales, disponibilidad o resultados en clientes.

Los primeros intentos de abrir puertos loopback fueron bloqueados por el sandbox y se repitieron con permiso de ejecución local. El smoke HTTP se corrigió para consultar el resultado asíncrono mediante GET acotados, sin repetir el POST de decisión. Las capturas son exclusivamente sintéticas: [ingreso](screenshots/demo-login.png), [solicitud pendiente](screenshots/demo-pending.png).
