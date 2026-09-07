# Componentes, fuentes y avisos de terceros

Revisión del árbol fuente: 7 de septiembre de 2026. El [alcance de Apache-2.0](docs/licensing.md) corresponde al material original del proyecto y no cambia las licencias de terceros. Los archivos de procedencia permiten comprobar correspondencia entre versiones; sus hashes no prueban titularidad ni conceden permisos.

## Qué se distribuye

El árbol Git contiene código, pruebas, configuración, documentación, diagramas y capturas del proyecto. No incorpora copias de `node_modules`, distribuciones Python, ejecutables Terraform, proveedores, SDK, fuentes tipográficas ni librerías vendorizadas. Los gestores descargan las dependencias descritas abajo por separado.

Los ZIP generados por `sales_demo/scripts/build_lambda_packages.py` contienen nueve archivos Python del proyecto y tres avisos en su raíz: `LICENSE`, `NOTICE` y este `THIRD_PARTY_NOTICES.md`. Este documento también describe dependencias del repositorio completo; esa descripción **no significa que estén incluidas en los ZIP**. El empaquetador no incluye `boto3`, `botocore`, el frontend ni el proveedor Terraform. La provisión y compatibilidad del SDK en el entorno de ejecución siguen siendo responsabilidad del despliegue.

Al distribuir un frontend compilado, contenedor, SDK o paquete que sí incorpore dependencias, se deben conservar los textos de licencia, copyright y avisos exigidos por los componentes efectivamente incluidos. Este inventario no sustituye esos textos ni constituye un inventario completo de un artefacto futuro.

## Frontend y herramientas npm

Versiones y expresiones de licencia tomadas de `sales_demo/web/package.json` y `sales_demo/web/package-lock.json`, sin cambiar resoluciones ni integridades. Cada paquete conserva los términos y avisos de sus autores; las fichas del [registro npm](https://registry.npmjs.org/) y los archivos de licencia incluidos en cada distribución son las fuentes de referencia.

| Dependencia directa | Versión | Licencia declarada | Uso |
| --- | --- | --- | --- |
| React | 19.2.8 | MIT | Interfaz |
| React DOM | 19.2.8 | MIT | Interfaz |
| React Router DOM | 7.18.2 | MIT | Navegación |
| `@axe-core/playwright` | 4.13.0 | MPL-2.0 | Pruebas de accesibilidad |
| `@playwright/test` | 1.62.1 | Apache-2.0 | Pruebas de navegador |
| `@testing-library/jest-dom` | 6.9.1 | MIT | Pruebas |
| `@testing-library/react` | 16.3.3 | MIT | Pruebas |
| `@testing-library/user-event` | 14.6.6 | MIT | Pruebas |
| `@types/react` | 19.2.18 | MIT | Tipos |
| `@types/react-dom` | 19.2.5 | MIT | Tipos |
| `@vitejs/plugin-react` | 5.2.0 | MIT | Compilación |
| jsdom | 30.0.1 | MIT | Pruebas |
| TypeScript | 5.8.3 | Apache-2.0 | Compilación |
| Vite | 7.3.6 | MIT | Desarrollo y compilación |
| Vitest | 3.2.7 | MIT | Pruebas |

Las 223 entradas de dependencias del lockfile, incluyendo transitivas y opcionales, declaran: MIT (195), Apache-2.0 (8), ISC (7), BlueOak-1.0.0 (3), MPL-2.0 (2), MIT-0 (2), BSD-2-Clause (2), BSD-3-Clause (2), CC-BY-4.0 (1) y CC0-1.0 (1). Son metadatos de los paquetes, no una atribución de Apache-2.0 a todo el árbol de dependencias.

Dos grupos requieren distinguirse expresamente:

- **MPL-2.0:** `@axe-core/playwright` y `axe-core`, ambos 4.13.0, herramientas de Deque Systems para pruebas. No se redistribuye su código en este repositorio ni en los ZIP Lambda. Si se distribuye material cubierto por MPL, deben conservarse sus términos, avisos y obligaciones correspondientes al código cubierto. Fuentes: [paquete publicado](https://registry.npmjs.org/@axe-core%2fplaywright/4.13.0), [licencia upstream](https://github.com/dequelabs/axe-core/blob/develop/LICENSE).
- **CC-BY-4.0:** `caniuse-lite` 1.0.30001810, datos de compatibilidad de Can I Use utilizados por las herramientas de desarrollo. Sus datos mantienen la atribución y licencia de sus autores; no se relicencian como Apache-2.0. Fuentes: [metadatos de la versión](https://registry.npmjs.org/caniuse-lite/1.0.30001810), [licencia upstream](https://github.com/browserslist/caniuse-lite/blob/main/LICENSE), [Can I Use](https://caniuse.com/).

## Python y SDK de AWS

`requirements-dev.txt` fija estas dependencias directas, descargadas desde PyPI:

| Paquete | Versión | Licencia declarada | Fuente |
| --- | --- | --- | --- |
| pytest | 9.1.1 | MIT | [Metadatos PyPI](https://pypi.org/pypi/pytest/9.1.1/json) |
| boto3 | 1.43.88 | Apache-2.0 | [Metadatos PyPI](https://pypi.org/pypi/boto3/1.43.88/json) |
| botocore | 1.43.88 | Apache-2.0 | [Metadatos PyPI](https://pypi.org/pypi/botocore/1.43.88/json) |
| PyYAML | 6.0.3 | MIT | [Metadatos PyPI](https://pypi.org/pypi/PyYAML/6.0.3/json) |

Boto3 y botocore son componentes del SDK de AWS y conservan los avisos de sus respectivos proyectos. El adaptador los utiliza sin incorporar su código fuente en el árbol publicado ni en los ZIP. Dependencias transitivas como `jmespath`, `s3transfer`, `python-dateutil`, `urllib3`, `pluggy`, `packaging`, `iniconfig` y `pygments` se resuelven por los requisitos de esos paquetes: no existe aquí un lock completo de todas sus versiones. Se debe revisar la distribución concreta antes de redistribuir un entorno Python con terceros incluidos.

## Terraform y proveedor AWS

La configuración HCL del proyecto y el lockfile no incluyen los ejecutables ni el código fuente de las herramientas. Terraform CLI y el proveedor se instalan separadamente y conservan sus licencias propias:

- **Terraform CLI 1.15.5**, versión usada en la validación local: Business Source License 1.1, con sus parámetros y condiciones específicos. Apache-2.0 del repositorio no se aplica a Terraform CLI. Fuente: [LICENSE de Terraform v1.15.5](https://github.com/hashicorp/terraform/blob/v1.15.5/LICENSE).
- **`hashicorp/aws` 6.63.0**, fijado en `.terraform.lock.hcl`: Mozilla Public License 2.0; el aviso upstream identifica a IBM Corp. Fuente: [LICENSE del proveedor v6.63.0](https://github.com/hashicorp/terraform-provider-aws/blob/v6.63.0/LICENSE).

## Diagramas, imágenes y marcas

Los diagramas adaptan conceptos del [AWS Well-Architected Framework](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html) a la solución descrita. Son documentación del proyecto, no una evaluación oficial, certificación o respaldo de AWS. AWS y las marcas de sus servicios pertenecen a Amazon.com, Inc. o sus afiliadas; su mención describe interoperabilidad y no concede derechos sobre ellas. Véanse las [directrices de marcas de AWS](https://aws.amazon.com/trademark-guidelines/).

Los SVG publicados utilizan texto y formas editables sin logos oficiales ni assets externos. La mención de Arial en el estilo no incluye ni redistribuye una fuente tipográfica. La portada se creó con asistencia de IA y las capturas documentan una demostración local con datos sintéticos; su procedencia se registra en `docs/visuals/assets.json`. El alcance de la licencia sobre las imágenes se limita a los derechos que posea el titular, sin prometer exclusividad ni titularidad sobre derechos ajenos. Estas observaciones sobre el árbol revisado no acreditan por sí solas una cadena jurídica de titularidad.
