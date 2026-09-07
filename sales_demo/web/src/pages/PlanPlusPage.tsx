import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { EmptyState, ErrorState, LoadingSkeleton } from '../components/AsyncStates'
import { PageHeader } from '../components/PageHeader'
import type { PlanPlus } from '../domain'
import { errorMessage, formatDateTime } from '../format'
import { useRuntime } from '../runtime'

export function PlanPlusPage() {
  const { api } = useRuntime()
  const [plan, setPlan] = useState<PlanPlus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setError(null)
    api
      .getPlanPlus(controller.signal)
      .then(setPlan)
      .catch((caught) => {
        const message = errorMessage(caught)
        if (message) setError(message)
      })
    return () => controller.abort()
  }, [api, reload])

  return (
    <>
      <PageHeader
        eyebrow="Resultado comercial"
        title="Servicio"
        description="Vista mínima del resultado aprobado, sin telemetría ni aprovisionamiento real."
      />
      {error ? <ErrorState message={error} onRetry={() => setReload((value) => value + 1)} /> : null}
      {!error && !plan ? <LoadingSkeleton label="Cargando estado de Servicio" /> : null}
      {plan?.status === 'INACTIVE' ? (
        <EmptyState
          title="Servicio todavía no está activo"
          detail="Se activa al aprobar una solicitud de hasta 50 vehículos o después de la decisión positiva del Manager."
          action={<Link className="button primary" to="/solicitudes/nueva">Crear solicitud</Link>}
        />
      ) : null}
      {plan?.status === 'ACTIVE' ? (
        <section className="plan-active" aria-labelledby="plan-active-title">
          <div className="plan-seal" aria-hidden="true">✓</div>
          <div>
            <p className="eyebrow">Estado durable · ACTIVO</p>
            <h2 id="plan-active-title">Servicio sintético activado</h2>
            <p>Este resultado demuestra el flujo comercial. No activa vehículos, pagos ni servicios externos.</p>
            <dl className="facts-grid compact-grid">
              <div><dt>Solicitud</dt><dd><Link to={`/solicitudes/${plan.applicationId}`}>{plan.applicationId}</Link></dd></div>
              <div><dt>Contrato</dt><dd>{plan.contractId}</dd></div>
              <div><dt>Activado</dt><dd>{plan.activatedAt ? <time dateTime={plan.activatedAt}>{formatDateTime(plan.activatedAt)}</time> : 'Fecha no disponible'}</dd></div>
            </dl>
          </div>
        </section>
      ) : null}
    </>
  )
}
