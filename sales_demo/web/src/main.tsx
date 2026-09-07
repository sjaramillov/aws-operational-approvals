import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { IS_DEMO_MODE, loadRuntimeConfig, type PublicRuntimeConfig } from './runtimeConfig'
import './styles.css'

const root = createRoot(document.getElementById('root')!)

function renderApp(runtimeConfig: PublicRuntimeConfig | null): void {
  root.render(
    <StrictMode>
      <App runtimeConfig={runtimeConfig} />
    </StrictMode>,
  )
}

function renderConfigurationError(caught: unknown): void {
  const detail = caught instanceof Error ? caught.message : 'La configuración pública no pudo validarse.'
  root.render(
    <StrictMode>
      <main className="centered-shell">
        <section className="state-panel state-error" role="alert" aria-labelledby="runtime-config-error">
          <p className="eyebrow">Inicio bloqueado de forma segura</p>
          <h1 id="runtime-config-error">Configuración de despliegue no disponible</h1>
          <p>{detail}</p>
          <p>La aplicación no realizará autenticación ni llamadas de negocio hasta recibir una configuración válida.</p>
        </section>
      </main>
    </StrictMode>,
  )
}

async function bootstrap(): Promise<void> {
  if (IS_DEMO_MODE) {
    renderApp(null)
    return
  }
  try {
    renderApp(await loadRuntimeConfig())
  } catch (caught) {
    renderConfigurationError(caught)
  }
}

void bootstrap()

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // Offline support is progressive; the online workflow remains available.
    })
  })
}
