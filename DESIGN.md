# Identidad de Aprobaciones operativas

La persona revisa solicitudes y razones de aprobación en un escritorio durante el día. Se usa tema claro y una paleta contenida de verdes/neutros, con amarillo para pendiente y rojo para rechazo. El cambio de identidad conserva los componentes, espacios, responsive y semántica del candidato original.

La fuente usa el sistema local con fallback Inter, sin descargar fuentes. Las acciones y focos conservan contraste comprobado mediante axe en los recorridos probados. El acento de acción es `oklch(0.48 0.12 165)` y el encabezado `oklch(0.29 0.085 165)`; las variables exactas están en `sales_demo/web/src/styles.css`.

El distintivo AP y el SVG de aprobación son nuevos para la extracción. No se reutiliza el logo del origen. Estados con texto, objetivos táctiles de al menos 44 px, foco visible y respeto por movimiento reducido permanecen en el flujo.

La base visual histórica se conserva sanitizada en `docs/base/DESIGN.md`; sus colores históricos no describen esta versión. No se afirma auditoría WCAG completa a partir de checks automáticos parciales.
