# Procedencia y límites de reutilización

Este repositorio deriva selectivamente de un candidato comercial construido en el espacio de trabajo privado del autor. El directorio original se conservó intacto. Se mantuvo el dominio, API, interfaz, IaC, tests y documentación técnica necesaria, con cambios trazables.

El proyecto reúne código y documentación técnica propios, seleccionados por su relación con el producto. Las dependencias y contenidos de terceros conservan sus derechos; no se incorporaron materiales privados ajenos al alcance de la solución.

## Transformaciones

- Nombre visible independiente: Aprobaciones operativas; distintivo geométrico nuevo, sin copiar el logo fuente.
- Paleta de interfaz cambiada a verdes sobrios y neutros; se conserva la estructura y comportamiento, no una identidad de una empresa ajena.
- Identificadores técnicos de ejemplo y prefijos normalizados; siguen siendo placeholders, no recursos operativos.
- Dependencias JS declaradas con las versiones exactas del lockfile; Python de validación fijado con las versiones del entorno utilizado.
- Documentación base conservada en `docs/base/`, diferenciada de esta arquitectura y evidencia actual.
- El nombre interno `sales_demo` y los conceptos de flota/umbral permanecen para mantener trazabilidad y no presentar una generalización que aún no se implementó.

## Manifiesto

[source-manifest.json](source-manifest.json) registra origen relativo, destino, hash SHA-256 de ambos y categoría del cambio. No expone la ruta privada del propietario ni una URL operativa. Los hashes acreditan correspondencia de archivos, no autoría o licencia.

`python3 scripts/verify_source_manifest.py` comprueba los hashes destino. Con `--source-root <ruta-privada>` también comprueba el original sin imprimir sus contenidos ni guardar la ruta. Toda edición posterior de archivos importados debe actualizar el manifiesto y explicar el cambio.

## Dependencias

React, Vite, Python, AWS SDK y demás dependencias conservan sus licencias propias. Sus binarios y directorios `node_modules`, `.venv` y `.terraform` no forman parte de la distribución del código. La revisión de publicación debe incluir el manifiesto de dependencias y avisos requeridos; no se reclaman como creación propia.

## Exclusiones

Se excluyeron assets ajenos, documentación privada, infraestructura no relacionada, bootstrap operativo original, planes, state, varfiles, logs, capturas privadas, paquetes de entrega, caches, credenciales y otros artefactos sensibles. El detalle por grupo está en el manifiesto. No se trasladó evidencia runtime del otro workload al producto de aprobaciones.
