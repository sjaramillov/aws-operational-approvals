# Revisión de dependencias — 7 de septiembre de 2026

Ocho actualizaciones propuestas por Dependabot se integran juntas para comprobar su compatibilidad y mantener la procedencia de los archivos importados.

| Dependencia | Versión anterior | Versión revisada | Propuesta |
|---|---|---|---|
| actions/checkout | 6.1.0 | 7.0.1 | [#1](https://github.com/sjaramillov/aws-operational-approvals/pull/1) |
| actions/setup-node | 6.5.0 | 7.0.0 | [#2](https://github.com/sjaramillov/aws-operational-approvals/pull/2) |
| actions/setup-python | 6.3.0 | 7.0.0 | [#3](https://github.com/sjaramillov/aws-operational-approvals/pull/3) |
| boto3 y botocore | 1.42.91 | 1.43.88 | [#4](https://github.com/sjaramillov/aws-operational-approvals/pull/4), [#8](https://github.com/sjaramillov/aws-operational-approvals/pull/8) |
| @testing-library/react | 16.3.2 | 16.3.3 | [#5](https://github.com/sjaramillov/aws-operational-approvals/pull/5) |
| jsdom | 26.1.0 | 30.0.1 | [#6](https://github.com/sjaramillov/aws-operational-approvals/pull/6) |
| Provider AWS de Terraform | 6.61.0 | 6.63.0 | [#9](https://github.com/sjaramillov/aws-operational-approvals/pull/9) |

Las Actions conservan pins por SHA completo. Se revisaron las notas oficiales de [checkout](https://github.com/actions/checkout/releases/tag/v7.0.1), [setup-node](https://github.com/actions/setup-node/releases/tag/v7.0.0) y [setup-python](https://github.com/actions/setup-python/releases/tag/v7.0.0), junto con los inputs utilizados por estos workflows.

boto3 1.43.88 requiere botocore >=1.43.88 y <1.44.0. Actualizar uno solo dejaba pins incompatibles. Ambos avanzan juntos y Dependabot los agrupa para futuras propuestas. Esto no habilita aprobación ni fusión automática.

[jsdom 30.0.1](https://registry.npmjs.org/jsdom/30.0.1) requiere Node `^22.22.2 || ^24.15.0 || >=26.0.0`; el paquete y el README declaran ese rango. La suite actual utiliza la configuración de jsdom verificada aquí. No se afirma compatibilidad con opciones de jsdom que el proyecto no utiliza.

## Actualización inicialmente pendiente

[@vitejs/plugin-react 6.1.1](https://registry.npmjs.org/@vitejs%2fplugin-react/6.1.1), propuesta en [#7](https://github.com/sjaramillov/aws-operational-approvals/pull/7), exige Vite 8. El proyecto conserva Vite 7.3.6 y Vitest 3.2.7. La migración requiere revisar conjuntamente el plugin, Vite, Vitest y sus cambios de transformación. No se incorpora esa actualización aislada.

La [revisión posterior del frontend](frontend-dependency-review-2026-09-07.md) documenta la migración conjunta y sus verificaciones. El párrafo anterior registra la decisión de esta primera revisión.

## Corrección del control de contenido

Un checksum oficial del provider 6.63.0 contiene doce dígitos consecutivos que el detector confundía con un identificador de cuenta AWS. Se conserva íntegro el lock del proveedor. Solo se excluyen del detector de cuentas las líneas completas de checksums SHA-256 válidos en `sales_demo/terraform/.terraform.lock.hcl`. Los comentarios, valores mal formados y otros archivos siguen sujetos al detector; los demás detectores inspeccionan siempre el texto completo. Cuatro tests nuevos cubren esos límites.

Se actualizan únicamente los hashes de destino de los archivos importados que cambiaron, con una descripción del cambio. Los hashes de origen se conservan.

## Verificación local

Entorno: Python 3.13.12, Node 26.0.0, npm 11.12.1, Terraform 1.15.5. El entorno Python se instaló en un virtualenv nuevo.

| Comprobación | Resultado |
|---|---|
| Contrato cruzado y control de contenido | PASS; ocho operaciones |
| Python: dominio, backend, contratos e infraestructura | 150 tests y 22 subtests aprobados |
| TypeScript, configuración de runtime y build Vite | PASS |
| Vitest y configuración del runner | 38 tests y 6 tests aprobados |
| Playwright sobre la demo local | 8 tests aprobados |
| HTTP en loopback contra API Python en memoria | 15 comprobaciones aprobadas |
| Paquetes fuente Lambda | Build y verificación reproducible aprobados; dos ZIP de 19.823 bytes |
| Terraform | Formato, validate y un test con provider mock aprobados |
| Plan guard estático | PASS |

Terraform instaló el provider 6.63.0 desde el registro con firma verificada de HashiCorp y lock en modo de solo lectura. Las pruebas usaron un provider mock; no se aplicó infraestructura ni se consultó una cuenta AWS. La demo de navegador y la API en memoria se probaron como superficies separadas. Los checks de GitHub del pull request registran la validación adicional en Linux y Node 22.

Esta revisión complementa la [evidencia inicial](status.md). No vuelve a certificar resultados históricos de TFLint o Checkov ni acredita un despliegue o comportamiento en producción.
