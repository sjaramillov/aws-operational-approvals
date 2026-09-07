import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { EmptyState, ErrorState, LoadingSkeleton } from '../components/AsyncStates'
import { PageHeader } from '../components/PageHeader'
import { StatusChip } from '../components/StatusChip'
import type { ApprovalSummary } from '../domain'
import { errorMessage, formatDateTime } from '../format'
import { useRuntime } from '../runtime'

export function ApprovalsPage() {
  const { api } = useRuntime()
  const [items, setItems] = useState<ApprovalSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setError(null)
    api
      .listApprovals(controller.signal)
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
        eyebrow="Flujo Manager"
        title="Aprobaciones pendientes"
        description="Solo aparecen solicitudes de más de 50 vehículos pertenecientes a tu tenant."
      />
      {error ? <ErrorState message={error} onRetry={() => setReload((value) => value + 1)} /> : null}
      {!error && items === null ? <LoadingSkeleton label="Cargando aprobaciones" /> : null}
      {!error && items?.length === 0 ? (
        <EmptyState title="No hay decisiones pendientes" detail="Cuando un Customer del tenant solicite más de 50 vehículos, la solicitud aparecerá aquí." />
      ) : null}
      {items && items.length > 0 ? (
        <ol className="record-list" aria-label="Aprobaciones del tenant">
          {items.map((approval) => (
            <li key={approval.applicationId}>
              <Link className="record-link" to={`/aprobaciones/${approval.applicationId}`}>
                <span className="record-main">
                  <strong>{approval.applicationId}</strong>
                  <span><b className="tabular">{approval.vehicleCount}</b> vehículos</span>
                </span>
                <span className="record-meta">
                  <StatusChip status={approval.status} />
                  <time dateTime={approval.createdAt}>{formatDateTime(approval.createdAt)}</time>
                </span>
              </Link>
            </li>
          ))}
        </ol>
      ) : null}
    </>
  )
}
