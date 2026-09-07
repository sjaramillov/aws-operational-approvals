# Colaborar

Describe primero el problema, el comportamiento esperado y cómo comprobarlo.
Trabaja en una rama y abre un pull request hacia `main`, incluyendo alcance y
verificación. Conserva los avisos de autoría y atribuye las fuentes utilizadas.

Antes de enviar un cambio, ejecuta los comandos de verificación del README,
`python3 scripts/check_repository.py` y un escaneo de secretos. El control de
repositorio usa los archivos añadidos a Git. Si editas un archivo importado,
actualiza su hash de destino y la descripción del cambio en el manifiesto.

Usa datos sintéticos. No añadas credenciales, exportaciones de organizaciones,
enunciados de evaluación ni artefactos de un cliente. Una propuesta de arquitectura
no debe presentarse como un servicio desplegado ni una métrica simulada como un
resultado obtenido en clientes. Acordar el alcance y condiciones precede a un piloto.

Las contribuciones originales enviadas para su inclusión se ofrecen bajo Apache
License 2.0, salvo acuerdo explícito diferente, conforme a su sección 5. Aporta
solo material sobre el que puedas conceder esos derechos y señala las licencias
de terceros. Verifica también la política de publicación con
`python3 -m unittest discover -s scripts -p 'test_publication_policy.py'`.
