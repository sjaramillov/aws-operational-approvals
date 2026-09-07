> Base de ingeniería extraída y sanitizada. Describe el candidato de origen; su estado actual y las pruebas de esta copia están en `../../evidence/status.md`. No es autorización de despliegue ni acreditación de derechos de terceros.

# Product

## Register

product

## Users

El piloto tiene dos usuarios operativos sintéticos dentro de empresas separadas:

- **Customer:** registra y consulta una solicitud de flota para activar Servicio activo.
- **Manager:** decide solicitudes de más de 50 vehículos dentro de su propio tenant.


## Product Purpose

Demostrar, con datos exclusivamente sintéticos, el tramo comercial mínimo que pide el caso de demostración: digitalizar una solicitud de venta y conservar la aprobación del Manager cuando la flota supera 50 vehículos. El éxito es un recorrido corto, verificable y accesible que termina en contrato y Servicio activo activo, o en rechazo con razón auditable. El piloto no procesa pagos, KYC, firma legal, PII real ni aprovisionamiento de vehículos.

## Brand Personality

**Serena, rigurosa y confiable.** La interfaz debe sentirse como una herramienta operativa madura: lenguaje directo, estados inequívocos y evidencia visible sin teatralidad. Debe inspirar la misma confianza que el resto del paquete CloudOps, cuya fortaleza es distinguir lo probado de lo propuesto.

## Anti-references

- Un dashboard sintético lleno de métricas decorativas que se lea como maqueta.
- Un checkout de comercio electrónico que sugiera pagos, firma legal o alta real.
- Una consola AWS genérica o una interfaz “cyber” oscura con neón y gradientes.
- Un producto que oculte que los datos son sintéticos o que presente disponibilidad futura como SLA.
- Patrones que dependan solo del color, animaciones ornamentales o controles no estándar.

## Design Principles

1. **La evidencia antes que el adorno.** Cada pantalla debe mostrar la decisión, el actor, la fecha y el siguiente estado útil.
2. **Un flujo, una intención.** Customer solicita; Manager decide; ambos ven solo lo que necesitan dentro de su tenant.
3. **Los límites son parte del producto.** “Datos sintéticos”, expiración y funciones excluidas se expresan en la propia interfaz.
4. **Estados durables y lenguaje preciso.** Pendiente, aprobado, rechazado, expirado y activo nunca se infieren por apariencia.
5. **La seguridad se puede demostrar.** Aislamiento de tenant, ausencia de registro público e idempotencia deben ser verificables, no solo descritos.

## Accessibility & Inclusion

Objetivo WCAG 2.2 AA para el recorrido demostrativo. Navegación completa por teclado, foco visible, landmarks y encabezados semánticos, mensajes de error asociados a campos, estados anunciados, contraste AA, objetivos táctiles suficientes y respeto por `prefers-reduced-motion`. Ninguna decisión o alerta dependerá únicamente del color.
