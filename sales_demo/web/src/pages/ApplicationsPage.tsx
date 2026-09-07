import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { EmptyState, ErrorState, LoadingSkeleton } from '../components/AsyncStates'
import { PageHeader } from '../components/PageHeader'
import { StatusChip } from '../components/StatusChip'
import type { SalesApplication } from '../domain'
import { errorMessage, formatDateTime } from '../format'
import { useRuntime } from '../runtime'

export function ApplicationsPage() {
  const { api } = useRuntime()
  const [items, setItems] = useState<SalesApplication[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setError(null)
    api
      .listApplications(controller.signal)
      .then(setItems)
      .catch((caught) => {
        const message = errorMessage(caught)
        if (message) setError(message)
      })
    return () => controller.abort()
  }, [api, reload])

  return (
    <>
      <PageHeader
        eyebrow="Flujo Customer"
        title="Solicitudes de flota"
        description="Cada solicitud conserva su estado durable y el rastro de quién actuó y cuándo."
        action={<Link className="button primary" to="/solicitudes/nueva">Nueva solicitud</Link>}
      />
      {error ? <ErrorState message={error} onRetry={() => setReload((value) => value + 1)} /> : null}
      {!error && items === null ? <LoadingSkeleton label="Cargando solicitudes" /> : null}
      {!error && items?.length === 0 ? (
        <EmptyState
          title="Aún no hay solicitudes"
          detail="Registra una flota sintética. Con 50 vehículos se aprueba automáticamente; con 51 o más interviene el Manager."
          action={<Link className="button primary" to="/solicitudes/nueva">Crear la primera solicitud</Link>}
        />
      ) : null}
      {items && items.length > 0 ? (
        <ol className="record-list" aria-label="Solicitudes del tenant">
          {items.map((application) => (
            <li key={application.applicationId}>
              <Link className="record-link" to={`/solicitudes/${application.applicationId}`}>
                <span className="record-main">
                  <strong>{application.applicationId}</strong>
                  <span><b className="tabular">{application.vehicleCount}</b> vehículos</span>
                </span>
                <span className="record-meta">
                  <StatusChip status={application.status} />
                  <time dateTime={application.createdAt}>{formatDateTime(application.createdAt)}</time>
                </span>
              </Link>
            </li>
          ))}
        </ol>
      ) : null}
    </>
  )
}
