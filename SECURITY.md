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

## Prevención de publicaciones accidentales

`scripts/check_repository.py` rechaza rutas de credenciales, wallets, perfiles de
nube, estados y planes Terraform (incluidas copias de respaldo), bases locales y
archivos comprimidos sin revisión. Revisa los archivos versionados, por lo que
`git add -f` no evita este control de CI. `.gitignore` es una ayuda local y no
protege archivos ya versionados. Los ejemplos permitidos siguen sujetos al
escaneo de contenido y a revisión humana.

Antes de publicar, comprueba el índice y ejecuta:

```bash
git diff --cached --stat
python3 scripts/check_repository.py
python3 scripts/check_publication_history.py
python3 -m unittest discover -s scripts -p 'test_publication_policy.py'
gitleaks git . --redact
```

El control de rutas no detecta cualquier dato personal o secreto dentro de un
archivo con nombre permitido. Los escaneos automáticos y la revisión humana se
complementan; ningún resultado garantiza ausencia de toda filtración.

Si se publica una credencial, revócala o rótala primero, investiga su uso y reporta
el incidente mediante el canal privado indicado arriba. Borrar la última versión
del archivo no elimina la exposición del historial, forks, caches o copias.
Coordina la limpieza del historial cuando corresponda, sin publicar el secreto
en un issue, PR o registro de pruebas. Una licencia de código abierto no sustituye
estos controles ni un acuerdo de confidencialidad.


Para ejecutar también el control antes de enviar commits al remoto, instala
Gitleaks y activa el hook local de este repositorio:

```bash
git config --local core.hooksPath .githooks
```

El hook `pre-push` comprueba las rutas versionadas, las rutas de todos los árboles
del historial local y el contenido histórico con Gitleaks antes de la transferencia.
Retirar un archivo del índice o del commit actual no oculta su ruta histórica.
Busca el ejecutable en `PATH` o en
`~/.local/bin/gitleaks` y bloquea el envío si falta el escáner. La configuración
del hook es local: cada clon debe activarla. Los controles de GitHub siguen
siendo necesarios, porque un hook local puede omitirse.
