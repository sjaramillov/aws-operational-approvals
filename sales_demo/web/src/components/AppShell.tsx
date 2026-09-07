import { useEffect, useState } from 'react'
import { NavLink, Navigate, Outlet } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import type { Health } from '../domain'
import { errorMessage, formatDateTime, roleLabel } from '../format'
import { LoadingSkeleton } from './AsyncStates'
import { StatusChip } from './StatusChip'

export function AppShell() {
  const { identity, api, loading, logout, isDemo } = useAuth()
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [healthCheck, setHealthCheck] = useState(0)

  useEffect(() => {
    if (!api) return
    const controller = new AbortController()
    api
      .getHealth(controller.signal)
      .then(setHealth)
      .catch((caught) => {
        const message = errorMessage(caught)
        if (message) setHealthError(message)
      })
    return () => controller.abort()
  }, [api, healthCheck])

  const retryHealth = () => {
    setHealth(null)
    setHealthError(null)
    setHealthCheck((value) => value + 1)
  }

  if (loading) {
    return <main className="centered-shell"><LoadingSkeleton label="Restaurando sesión" /></main>
  }
  if (!identity || !api) return <Navigate to="/ingreso" replace />

  const customer = identity.role === 'CUSTOMER'
  const active = health?.status === 'ACTIVE'
  return (
    <div className="app-shell">
      <a className="skip-link" href="#contenido-principal">Saltar al contenido principal</a>
      <aside className="demo-banner" aria-label="Alcance de la demostración">
        <strong>Demostración · datos sintéticos · sin PII real</strong>
        <span>{isDemo ? 'Modo demo local, sin conexión con AWS' : 'Sesión Cognito con PKCE'}</span>
      </aside>
      <header className="topbar">
        <div className="brand-block">
          <span className="brand-mark" aria-hidden="true">AP</span>
          <span><strong>Aprobaciones operativas</strong><small>Piloto comercial acotado</small></span>
        </div>
        <dl className="session-facts" aria-label="Contexto de sesión">
          <div><dt>Tenant</dt><dd>{identity.tenantName ?? identity.tenantId}</dd></div>
          <div><dt>Rol</dt><dd>{roleLabel(identity.role)}</dd></div>
          <div>
            <dt>Expiración</dt>
            <dd>
              {health?.expiresAt
                ? <time dateTime={health.expiresAt}>{formatDateTime(health.expiresAt)}</time>
                : health?.status === 'PREPARED'
                  ? 'Se fija al activar T0'
                  : healthError ?? 'Consultando'}
            </dd>
          </div>
        </dl>
        <div className="session-actions">
          {health ? <StatusChip status={health.status} /> : null}
          <button className="button ghost compact" type="button" onClick={logout}>
            {isDemo ? 'Cambiar persona' : 'Cerrar sesión'}
          </button>
        </div>
      </header>
      {active ? (
        <nav className="primary-nav" aria-label="Navegación principal">
          {customer ? (
            <>
              <NavLink to="/solicitudes" end>Solicitudes</NavLink>
              <NavLink to="/solicitudes/nueva">Nueva solicitud</NavLink>
              <NavLink to="/plan-plus">Servicio</NavLink>
            </>
          ) : (
            <NavLink to="/aprobaciones">Aprobaciones</NavLink>
          )}
        </nav>
      ) : null}
      <main id="contenido-principal" className="main-content" tabIndex={-1}>
        {healthError ? (
          <section className="state-panel state-error" role="alert" aria-labelledby="health-unavailable-title">
            <p className="eyebrow">Acciones bloqueadas</p>
            <h1 id="health-unavailable-title">No se pudo verificar el estado del piloto</h1>
            <p>{healthError}</p>
            <p>Ninguna operación de negocio está habilitada hasta recuperar un estado ACTIVE verificable.</p>
            <button className="button secondary" type="button" onClick={retryHealth}>Volver a verificar estado</button>
          </section>
        ) : !health ? (
          <section aria-labelledby="pilot-health-loading-title">
            <h1 id="pilot-health-loading-title" className="sr-only">Verificando estado del piloto</h1>
            <LoadingSkeleton label="Verificando estado del piloto" />
          </section>
        ) : health.status === 'PREPARED' ? (
          <section className="state-panel" role="status" aria-labelledby="pilot-prepared-title">
            <p className="eyebrow">PREPARED · inicio controlado</p>
            <h1 id="pilot-prepared-title">El piloto aún no está habilitado</h1>
            <p>La infraestructura está preparada, pero T0 no ha comenzado. Las acciones de negocio permanecen bloqueadas hasta confirmar readiness y estado ACTIVE.</p>
            <button className="button secondary" type="button" onClick={retryHealth}>Verificar activación</button>
          </section>
        ) : health.status === 'EXPIRED' ? (
          <section className="state-panel state-error" role="status" aria-labelledby="pilot-expired-title">
            <p className="eyebrow">Ventana cerrada</p>
            <h1 id="pilot-expired-title">El piloto expiró</h1>
            <p>La interfaz conserva únicamente este aviso; no se permiten lecturas ni escrituras de negocio.</p>
          </section>
        ) : (
          <Outlet context={{ identity, api, health }} />
        )}
      </main>
      <footer className="site-footer">
        <p>No procesa pagos, KYC, firma legal ni aprovisionamiento real.</p>
      </footer>
    </div>
  )
}
