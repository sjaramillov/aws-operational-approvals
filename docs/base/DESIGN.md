> Base de ingeniería extraída y sanitizada. Describe el candidato de origen; su estado actual y las pruebas de esta copia están en `../../evidence/status.md`. No es autorización de despliegue ni acreditación de derechos de terceros.

---
name: "Aprobaciones operativas"
description: "Herramienta operativa sobria para solicitar, aprobar y auditar la activación sintética de Servicio activo."
colors:
  primary-navy: "#0B2545"
  action-blue: "#2E74B5"
  action-blue-hover: "#225B8E"
  pale-blue: "#E8F1F7"
  success-green: "#3A7D44"
  danger-red: "#B42318"
  ink: "#1F2937"
  muted: "#64748B"
  border: "#D8E0E8"
  canvas: "#F7F8FA"
  surface: "#FFFFFF"
typography:
  headline:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "2rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 650
    lineHeight: 1.3
  body:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "1rem"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
    lineHeight: 1.35
rounded:
  sm: "6px"
  md: "10px"
  lg: "14px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
components:
  button-primary:
    backgroundColor: "{colors.action-blue}"
    textColor: "{colors.surface}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
  button-primary-hover:
    backgroundColor: "{colors.action-blue-hover}"
    textColor: "{colors.surface}"
    typography: "{typography.label}"
    rounded: "{rounded.sm}"
    padding: "10px 16px"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "10px 12px"
  card:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "24px"
---

# Design System: Aprobaciones operativas

## Overview

**Creative North Star: "La bitácora operativa"**

La interfaz se comporta como una bitácora confiable: cada acción deja un estado legible, una persona responsable y una marca temporal. La familiaridad sirve al producto; navegación, formularios, tablas y diálogos usan patrones estándar y desaparecen detrás de la tarea.

La superficie es clara, contenida y de densidad media. Rechaza el dashboard sintético lleno de métricas decorativas, el checkout que sugiere una compra real, la consola AWS genérica y cualquier estética “cyber” oscura con neón y gradientes. El movimiento comunica cambios de estado en 150–200 ms y se elimina con `prefers-reduced-motion`.

**Key Characteristics:**

- Jerarquía predecible y contenido en español.
- Una acción primaria por vista.
- Estados durables expresados con texto e icono, nunca solo color.
- Aviso persistente de datos sintéticos y expiración del piloto.
- Diseño responsive desde 320 px sin esconder controles críticos.

## Colors

El azul APPROVALS comunica operación y confianza; los colores semánticos aparecen solo cuando hay un estado que explicar.

### Primary

- **Azul de operación:** acción primaria, enlace y foco.
- **Azul noche APPROVALS:** encabezados, navegación y texto institucional de alto contraste.

### Secondary

- **Verde verificable:** confirmaciones y estados aprobados o activos.
- **Rojo de decisión:** rechazo, expiración y errores que requieren atención.

### Neutral

- **Tinta:** texto principal.
- **Pizarra:** texto secundario y metadatos.
- **Lienzo frío:** fondo general.
- **Superficie blanca:** formularios, paneles y tablas.
- **Borde tenue:** separación estructural sin ruido.

**The One Voice Rule.** El azul de acción ocupa menos del 10 % de cada pantalla; se reserva para foco, enlaces y la acción primaria.

**The Semantic Pair Rule.** Todo verde, rojo o azul informativo lleva texto o icono que explique el estado.

## Typography

**Display Font:** Inter con fallback al sistema
**Body Font:** Inter con fallback al sistema

**Character:** Una sola sans humanista mantiene el recorrido familiar, compacto y legible. Las cifras de vehículos usan números tabulares para que 50, 51 y 52 se comparen sin salto visual.

### Hierarchy

- **Headline** (700, 2rem, 1.2): título único de la vista.
- **Title** (650, 1.25rem, 1.3): secciones y paneles.
- **Body** (400, 1rem, 1.5): instrucciones y contenido, con máximo de 72 caracteres por línea en prosa.
- **Label** (600, 0.875rem, 1.35): campos, botones, chips y encabezados de tabla.

**The Plain Language Rule.** Los estados dicen “Requiere aprobación del Manager”, no códigos internos ni jerga de Step Functions.

## Elevation

El sistema es plano por defecto y crea profundidad con capas tonales y bordes. Una sombra ambiental suave aparece únicamente en diálogos y en tarjetas que se levantan al pasar el puntero; el foco se expresa con un anillo, no con sombra decorativa.

### Shadow Vocabulary

- **Ambient low** (`0 8px 24px rgba(11, 37, 69, 0.10)`): diálogos y menú móvil.
- **Raised state** (`0 4px 12px rgba(11, 37, 69, 0.08)`): respuesta de hover en tarjetas accionables.

**The Flat-By-Default Rule.** Si todas las tarjetas parecen flotantes, la jerarquía falló: solo los elementos interactivos ganan elevación como respuesta de estado.

## Components

### Buttons

- **Shape:** bordes suavemente curvos (6px) y altura mínima de 44px.
- **Primary:** azul de operación, texto blanco, una por vista.
- **Hover / Focus:** azul profundo; anillo exterior de 3px con contraste AA y sin cambio de geometría.
- **Secondary / Ghost:** fondo blanco o transparente, borde tenue y texto azul noche.
- **Disabled / Loading:** conserva la etiqueta, expone `aria-disabled` y nunca elimina el foco sin trasladarlo.

### Chips

- **Style:** fondo tonal, icono opcional y texto explícito para `PENDIENTE`, `APROBADA`, `RECHAZADA`, `ACTIVA` o `EXPIRADA`.
- **State:** son indicadores, no botones disfrazados.

### Cards / Containers

- **Corner Style:** curva moderada (10px).
- **Background:** superficie blanca sobre lienzo frío.
- **Shadow Strategy:** plana en reposo; elevación solo cuando la tarjeta es accionable.
- **Border:** una línea tenue.
- **Internal Padding:** 24px en escritorio y 16px en móvil.

### Inputs / Fields

- **Style:** fondo blanco, borde tenue, radio de 6px y altura mínima de 44px.
- **Focus:** borde azul y anillo exterior visible.
- **Error / Disabled:** mensaje asociado mediante `aria-describedby`; el error no depende del rojo.

### Navigation

La barra superior identifica APPROVALS, el tenant activo, el rol y la expiración. En móvil colapsa sin ocultar “Solicitudes” ni “Aprobaciones”. La ruta activa combina peso, texto y subrayado.

### Audit Timeline

Cada hito muestra acción, actor sintético, fecha ISO legible, decisión y código de razón. El orden cronológico y los encabezados semánticos permiten comprenderla sin conectores decorativos.

## Do's and Don'ts

### Do:

- **Do** mostrar “Demostración · datos sintéticos · sin PII real” en todas las vistas autenticadas.
- **Do** mantener visibles tenant, rol y estado del piloto antes de cualquier efecto lateral.
- **Do** asociar errores de formulario al campo y llevar el foco al resumen al fallar el envío.
- **Do** probar teclado, lector de pantalla, contraste AA, 200 % de zoom y movimiento reducido.
- **Do** usar fechas con zona horaria explícita y números tabulares para el límite 50/51.

### Don't:

- **Don't** construir un dashboard sintético lleno de métricas decorativas que se lea como maqueta.
- **Don't** imitar un checkout de comercio electrónico que sugiera pagos, firma legal o alta real.
- **Don't** imitar una consola AWS genérica ni una interfaz “cyber” oscura con neón y gradientes.
- **Don't** ocultar que los datos son sintéticos o presentar disponibilidad observada como SLA.
- **Don't** depender solo del color, usar animaciones ornamentales o reinventar controles estándar.
