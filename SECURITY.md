# Seguridad

Para informar una vulnerabilidad, utiliza [Report a vulnerability](https://github.com/sjaramillov/aws-operational-approvals/security/advisories/new).
Incluye el archivo o commit, el impacto y una reproducción con datos ficticios.
No incluyas credenciales, datos personales ni detalles de clientes en issues públicos.

Esta demostración no es un entorno de producción. Las pruebas y límites están
descritos en el README y en la evidencia; los checks no certifican seguridad total.

Antes de enviar cambios, revisa `git diff --cached` y ejecuta
`gitleaks git . --redact`. El workflow de seguridad revisa la historia del checkout
con Gitleaks 8.30.1. Los ejemplos deben usar datos ficticios y la configuración
operativa debe permanecer fuera de Git. Las acciones de CI usan permisos de lectura
y no reciben credenciales de nube.
