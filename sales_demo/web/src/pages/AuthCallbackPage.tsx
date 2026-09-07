import { useEffect, useRef, useState } from 'react'
import { Link, Navigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import { ErrorState, LoadingSkeleton } from '../components/AsyncStates'

export function AuthCallbackPage() {
  const { identity, completeCallback } = useAuth()
  const [error, setError] = useState<string | null>(null)
  const started = useRef(false)

  useEffect(() => {
    if (started.current) return
    started.current = true
    completeCallback(window.location.search).catch((caught) => {
      setError(caught instanceof Error ? caught.message : 'No se pudo completar la autenticación.')
    })
  }, [completeCallback])

  if (identity) return <Navigate to="/" replace />
  return (
    <main className="centered-shell">
      {error ? (
        <div>
          <ErrorState message={error} />
          <Link className="button secondary" to="/ingreso">Volver a ingresar</Link>
        </div>
      ) : <LoadingSkeleton label="Validando respuesta de Cognito" />}
    </main>
  )
}
