# Revisión de herramientas frontend — 7 de septiembre de 2026

Las propuestas de Dependabot [#7](https://github.com/sjaramillov/aws-operational-approvals/pull/7), [#11](https://github.com/sjaramillov/aws-operational-approvals/pull/11) y [#12](https://github.com/sjaramillov/aws-operational-approvals/pull/12) se resuelven mediante una actualización conjunta, comprobada desde una instalación limpia del lockfile.

| Dependencia | Anterior | Revisada |
|---|---|---|
| Vite | 7.3.6 | 8.2.2 |
| `@vitejs/plugin-react` | 5.2.0 | 6.1.1 |
| Vitest | 3.2.7 | 5.0.0 |
| `@testing-library/user-event` | 14.6.6 | 14.6.7 |

El [plugin de React 6.1.1](https://registry.npmjs.org/@vitejs%2fplugin-react/6.1.1) exige Vite 8. [Vitest 5.0.0](https://registry.npmjs.org/vitest/5.0.0) admite Vite 6.4, 7 y 8; se incorpora en la misma revisión para comprobar el conjunto de herramientas. El rango de Node existente satisface los requisitos de las cuatro versiones.

Se revisaron las guías oficiales de [Vite 8](https://vite.dev/guide/migration), [Vitest 4](https://v4.vitest.dev/guide/migration) y [Vitest 5](https://vitest.dev/guide/migration/). El proyecto no utiliza configuración personalizada de Babel, esbuild, cobertura o pools. Conserva `build.target: es2022`, la selección de nueve archivos de pruebas, jsdom, los mocks y todas las aserciones existentes. La compilación con Rolldown/Oxc y el procesamiento CSS con Lightning CSS se comprueban mediante builds y recorridos locales de navegador.

Los tres PR originales fallaban antes de instalar dependencias: los hashes de destino del manifiesto no correspondían a los paquetes actualizados. Se corrigen solo esos dos hashes y su descripción; los 123 hashes de origen permanecen intactos. También se actualiza el inventario de licencias en [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md), incluido Lightning CSS bajo MPL-2.0.

Dependabot agrupa futuras propuestas de Vite, su plugin de React y Vitest. Se mantienen los checks de publicación, procedencia y secretos. El grupo no habilita fusión automática; los cambios futuros en archivos importados siguen requiriendo revisión del manifiesto.

## Verificación local

Entorno: macOS arm64, Node 26.0.0, npm 11.12.1, Python 3.13.12 y Terraform 1.15.5. Se reutilizó el virtualenv Python con los requisitos vigentes. Las dependencias npm se reinstalaron mediante `npm ci`; `npm ls --depth=0` no encontró incompatibilidades.

| Comprobación | Resultado |
|---|---|
| Contrato cruzado y control de contenido | PASS; ocho operaciones |
| Python: dominio, contratos e infraestructura | 153 tests y 27 subtests aprobados |
| TypeScript, runtime config y builds normal/demo | PASS |
| Vitest | 38 tests en nueve archivos aprobados |
| Configuración del runner remoto, sin ejecutarlo | 6 tests aprobados |
| Playwright: flujos, aislamiento, accesibilidad muestreada y 320 px | 8 tests aprobados, sin retries |
| HTTP loopback contra la API Python en memoria | 15 comprobaciones aprobadas |
| Paquetes fuente Lambda | Build y verificación reproducible aprobados; 12 entradas por ZIP, incluidos los tres avisos |
| Terraform | Formato recursivo y política estática aprobados |
| `npm audit` | 0 vulnerabilidades conocidas en la consulta |

Reproducción: `npm ci --prefix sales_demo/web`, `make check-local PYTHON=.venv/bin/python` y `npm audit --prefix sales_demo/web`. Los checks del PR registran la verificación adicional en Linux con Node 22. La consulta de vulnerabilidades es una observación puntual, no una garantía de ausencia de fallos.

Esta revisión cubre las superficies locales existentes. No aplica infraestructura, consulta una cuenta AWS ni ejecuta los E2E desplegados. La demo de navegador y la API Python se verifican por separado; estos resultados no acreditan su integración en producción.
