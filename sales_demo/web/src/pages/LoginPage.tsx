import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { roleLabel } from '../format'
import { LoadingSkeleton } from '../components/AsyncStates'

export function LoginPage() {
  const { identity, loading, error, isDemo, demoIdentities, loginDemo, loginCognito } = useAuth()
  const [subject, setSubject] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)

  if (loading) return <main className="centered-shell"><LoadingSkeleton label="Preparando ingreso" /></main>
  if (identity) return <Navigate to="/" replace />

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setLocalError(null)
    if (isDemo) {
      if (!subject) {
        setLocalError('Selecciona una persona sintética para continuar.')
        return
      }
      loginDemo(subject)
      return
    }
    setSubmitting(true)
    try {
      await loginCognito()
    } catch (caught) {
      setLocalError(caught instanceof Error ? caught.message : 'No se pudo iniciar Cognito.')
      setSubmitting(false)
    }
  }

  return (
    <main className="login-layout">
      <section className="login-intro" aria-labelledby="login-title">
        <div className="brand-mark large" aria-hidden="true">AP</div>
        <p className="eyebrow">Piloto comercial acotado</p>
        <h1 id="login-title">Aprobaciones operativas</h1>
        <p>
          Recorrido verificable para solicitar una flota, conservar la decisión del Manager y activar un contrato sintético.
        </p>
        <div className="scope-notice">
          <strong>Demostración · datos sintéticos · sin PII real</strong>
          <p>Sin pagos, KYC, firma legal, telemetría ni aprovisionamiento.</p>
        </div>
      </section>
      <section className="login-panel" aria-labelledby="access-title">
        <p className="eyebrow">{isDemo ? 'Modo demo local' : 'Acceso administrado'}</p>
        <h2 id="access-title">{isDemo ? 'Elige una persona sintética' : 'Ingresa con Cognito'}</h2>
        <p>
          {isDemo
            ? 'Las cuatro identidades son fixtures locales, no representan personas reales.'
            : 'No existe registro público. Cognito usa Authorization Code con PKCE y un cliente sin secret.'}
        </p>
        {(localError || error) ? <div className="form-error-summary" role="alert">{localError ?? error}</div> : null}
        <form onSubmit={submit} noValidate>
          {isDemo ? (
            <fieldset className="identity-options">
              <legend>Persona para esta sesión</legend>
              {demoIdentities.map((candidate) => (
                <label key={candidate.subject}>
                  <input
                    type="radio"
                    name="demo-identity"
                    value={candidate.subject}
                    checked={subject === candidate.subject}
                    onChange={() => setSubject(candidate.subject)}
                  />
                  <span>
                    <strong>{candidate.displayName}</strong>
                    <small>{candidate.tenantName} · {roleLabel(candidate.role)}</small>
                  </span>
                </label>
              ))}
            </fieldset>
          ) : null}
          <button className="button primary full" type="submit" disabled={submitting} aria-disabled={submitting}>
            {submitting ? 'Redirigiendo a Cognito' : isDemo ? 'Entrar a la demostración' : 'Continuar con Cognito'}
          </button>
        </form>
        <p className="privacy-note">No solicites ni ingreses nombres, correos, placas u otra PII real.</p>
      </section>
    </main>
  )
}
