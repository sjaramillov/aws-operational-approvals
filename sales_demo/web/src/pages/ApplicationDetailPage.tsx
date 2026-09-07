import { useEffect, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { ApiProblem, REASON_LABELS, type SalesApplication } from '../domain'
import { AuditTimeline } from '../components/AuditTimeline'
import { ErrorState, LoadingSkeleton } from '../components/AsyncStates'
import { PageHeader } from '../components/PageHeader'
import { StatusChip } from '../components/StatusChip'
import { errorMessage, formatDateTime } from '../format'
import { useRuntime } from '../runtime'

export function ApplicationDetailPage() {
  const { applicationId = '' } = useParams()
  const location = useLocation()
  const { api, identity } = useRuntime()
  const [application, setApplication] = useState<SalesApplication | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [reload, setReload] = useState(0)
  const notice = (location.state as { notice?: string } | null)?.notice

  useEffect(() => {
    const controller = new AbortController()
    setError(null)
    setNotFound(false)
    api
      .getApplication(applicationId, controller.signal)
      .then(setApplication)
      .catch((caught) => {
        if (caught instanceof ApiProblem && caught.status === 404) {
          setNotFound(true)
          return
        }
        const message = errorMessage(caught)
        if (message) setError(message)
      })
    return () => controller.abort()
  }, [api, applicationId, reload])

  const listPath = identity.role === 'CUSTOMER' ? '/solicitudes' : '/aprobaciones'
  if (notFound) {
    return (
      <section className="not-found" aria-labelledby="not-found-title">
        <p className="eyebrow">404 · Recurso no disponible</p>
        <h1 id="not-found-title">No se encontró la solicitud</h1>
        <p>El recurso no existe o no pertenece a tu tenant. La respuesta no revela cuál de las dos condiciones aplica.</p>
        <Link className="button primary" to={listPath}>Volver al listado</Link>
      </section>
    )
  }

  return (
    <>
      <PageHeader
        eyebrow="Detalle durable"
        title={application?.applicationId ?? 'Solicitud'}
        description="Estado, decisión y auditoría del recurso dentro del tenant autenticado."
        action={<Link className="button secondary" to={listPath}>Volver</Link>}
      />
      {notice ? <div className="notice success" role="status" aria-live="polite">✓ {notice}</div> : null}
      {error ? <ErrorState message={error} onRetry={() => setReload((value) => value + 1)} /> : null}
      {!error && !application ? <LoadingSkeleton label="Cargando detalle de solicitud" /> : null}
      {application ? (
        <>
          <section className="application-summary surface" aria-labelledby="summary-title">
            <div className="summary-heading">
              <div>
                <p className="eyebrow">Estado actual</p>
                <h2 id="summary-title">{application.vehicleCount} vehículos</h2>
              </div>
              <StatusChip status={application.status} />
            </div>
            <dl className="facts-grid">
              <div><dt>Solicitud</dt><dd>{application.applicationId}</dd></div>
              <div><dt>Creada</dt><dd><time dateTime={application.createdAt}>{formatDateTime(application.createdAt)}</time></dd></div>
              <div><dt>Actualizada</dt><dd><time dateTime={application.updatedAt}>{formatDateTime(application.updatedAt)}</time></dd></div>
              <div><dt>Decisión</dt><dd>{application.decision ? (application.decision === 'APPROVE' ? 'Aprobar' : 'Rechazar') : 'Pendiente'}</dd></div>
              <div><dt>Código de razón</dt><dd>{application.reasonCode ? (REASON_LABELS[application.reasonCode] ?? application.reasonCode) : 'No aplica todavía'}</dd></div>
              <div><dt>Tenant</dt><dd>{identity.tenantName ?? identity.tenantId}</dd></div>
            </dl>
          </section>
          {application.status === 'CONTRACT_ACTIVE' ? (
            <section className="contract-strip" aria-labelledby="contract-title">
              <div>
                <p className="eyebrow">Resultado sintético</p>
                <h2 id="contract-title">Contrato y Servicio activos</h2>
                <p>Contrato <strong>{application.contractId}</strong>. No constituye firma legal ni aprovisionamiento real.</p>
              </div>
              {identity.role === 'CUSTOMER' ? <Link className="button primary" to="/plan-plus">Ver Servicio</Link> : null}
            </section>
          ) : null}
          <section className="audit-section" aria-labelledby="audit-title">
            <p className="eyebrow">Evidencia operativa</p>
            <h2 id="audit-title">Rastro de auditoría</h2>
            <AuditTimeline events={application.auditTrail} />
          </section>
        </>
      ) : null}
    </>
  )
}
