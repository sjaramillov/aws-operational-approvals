import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiProblem, type Decision, type DecisionAccepted, type SalesApplication } from '../domain'
import { ErrorState, LoadingSkeleton } from '../components/AsyncStates'
import { PageHeader } from '../components/PageHeader'
import { StatusChip } from '../components/StatusChip'
import { errorMessage, formatDateTime } from '../format'
import { useRuntime } from '../runtime'
import { pollDecisionUntilFinal, type DecisionPollProgress } from '../api/pollDecision'

const REASONS: Record<Decision, { value: string; label: string }[]> = {
  APPROVE: [
    { value: 'CAPACITY_CONFIRMED', label: 'Capacidad operativa confirmada' },
    { value: 'POLICY_EXCEPTION_APPROVED', label: 'Excepción comercial aprobada' },
  ],
  REJECT: [
    { value: 'CAPACITY_NOT_AVAILABLE', label: 'Capacidad operativa no disponible' },
    { value: 'INCOMPLETE_COMMERCIAL_CASE', label: 'Caso comercial incompleto' },
  ],
}

export function ApprovalDecisionPage() {
  const { applicationId = '' } = useParams()
  const { api } = useRuntime()
  const navigate = useNavigate()
  const [application, setApplication] = useState<SalesApplication | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [decision, setDecision] = useState<Decision | ''>('')
  const [reasonCode, setReasonCode] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [decisionAccepted, setDecisionAccepted] = useState<DecisionAccepted | null>(null)
  const [pollProgress, setPollProgress] = useState<DecisionPollProgress | null>(null)
  const errorRef = useRef<HTMLDivElement>(null)
  const pollControllerRef = useRef<AbortController | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    api
      .getApplication(applicationId, controller.signal)
      .then(setApplication)
      .catch((caught) => {
        if (caught instanceof ApiProblem && caught.status === 404) setNotFound(true)
        else {
          const message = errorMessage(caught)
          if (message) setLoadError(message)
        }
      })
    return () => controller.abort()
  }, [api, applicationId])

  useEffect(() => () => pollControllerRef.current?.abort(), [])

  const selectDecision = (value: Decision) => {
    if (decisionAccepted) return
    setDecision(value)
    setReasonCode('')
    setFormError(null)
  }

  const verifyDurableState = async (accepted: DecisionAccepted) => {
    if (submitting) return
    setSubmitting(true)
    setFormError(null)
    const controller = new AbortController()
    pollControllerRef.current = controller
    setPollProgress({ attempt: 0, maxAttempts: 7, lastStatus: accepted.status, nextDelayMs: 0 })
    try {
      const decided = await pollDecisionUntilFinal(api, applicationId, {
        signal: controller.signal,
        onProgress: setPollProgress,
      })
      pollControllerRef.current = null
      navigate(`/solicitudes/${decided.applicationId}`, {
        state: {
          notice: decided.status === 'CONTRACT_ACTIVE'
            ? 'Decisión aprobada y estado durable confirmado.'
            : 'Rechazo registrado y estado durable confirmado.',
        },
      })
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      setFormError(errorMessage(caught))
      setSubmitting(false)
      setPollProgress(null)
      pollControllerRef.current = null
      window.setTimeout(() => errorRef.current?.focus(), 0)
    }
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (decisionAccepted || submitting) return
    if (!decision || !reasonCode) {
      setFormError('Selecciona una decisión y un código de razón.')
      window.setTimeout(() => errorRef.current?.focus(), 0)
      return
    }
    setSubmitting(true)
    setFormError(null)
    try {
      const accepted = await api.decideApplication(applicationId, decision, reasonCode)
      if (!accepted.accepted || accepted.applicationId !== applicationId || accepted.status !== 'PENDING_MANAGER') {
        throw new Error('El servicio no devolvió la aceptación segura esperada.')
      }
      setDecisionAccepted(accepted)
      setSubmitting(false)
      await verifyDurableState(accepted)
    } catch (caught) {
      setFormError(errorMessage(caught))
      setSubmitting(false)
      window.setTimeout(() => errorRef.current?.focus(), 0)
    }
  }

  if (notFound) {
    return (
      <section className="not-found" aria-labelledby="approval-not-found">
        <p className="eyebrow">404 · Recurso no disponible</p>
        <h1 id="approval-not-found">No se encontró la aprobación</h1>
        <p>El recurso no existe o pertenece a otro tenant. No se revela información adicional.</p>
        <Link className="button primary" to="/aprobaciones">Volver a aprobaciones</Link>
      </section>
    )
  }

  return (
    <>
      <PageHeader
        eyebrow="Decisión humana"
        title={`Decidir ${applicationId}`}
        description="La decisión y la razón quedan registradas con actor y fecha."
        action={<Link className="button secondary" to="/aprobaciones">Cancelar</Link>}
      />
      {loadError ? <ErrorState message={loadError} /> : null}
      {!loadError && !application ? <LoadingSkeleton label="Cargando solicitud para aprobación" /> : null}
      {application ? (
        <div className="decision-layout">
          <section className="decision-context surface" aria-labelledby="decision-context-title">
            <div className="summary-heading">
              <div>
                <p className="eyebrow">Solicitud del tenant</p>
                <h2 id="decision-context-title"><span className="tabular">{application.vehicleCount}</span> vehículos</h2>
              </div>
              <StatusChip status={application.status} />
            </div>
            <dl className="facts-grid compact-grid">
              <div><dt>Solicitud</dt><dd>{application.applicationId}</dd></div>
              <div><dt>Creada</dt><dd><time dateTime={application.createdAt}>{formatDateTime(application.createdAt)}</time></dd></div>
            </dl>
          </section>
          {application.status !== 'PENDING_MANAGER' ? (
            <section className="state-panel" role="status">
              <h2>Esta solicitud ya no está pendiente</h2>
              <p>La decisión durable actual es visible en el detalle. No se puede consumir dos veces.</p>
              <Link className="button primary" to={`/solicitudes/${application.applicationId}`}>Ver detalle</Link>
            </section>
          ) : (
            <form className="surface decision-form" onSubmit={submit} noValidate aria-busy={submitting}>
              {formError ? (
                <div className="form-error-summary" role="alert" tabIndex={-1} ref={errorRef}>
                  <strong>La operación requiere atención</strong>
                  <p>{formError}</p>
                </div>
              ) : null}
              {decisionAccepted ? (
                <div
                  className="processing-state"
                  role="status"
                  aria-live="polite"
                  aria-atomic="true"
                  data-decision-accepted="true"
                  data-poll-attempt={pollProgress?.attempt ?? 'paused'}
                >
                  <strong>Decisión aceptada</strong>
                  <p>
                    {pollProgress
                      ? 'Confirmando el estado durable mediante una lectura acotada.'
                      : 'El comando ya fue aceptado. No se reenviará; solo puedes volver a consultar su estado.'}
                  </p>
                  {pollProgress ? <small>Consulta {pollProgress.attempt + 1} de {pollProgress.maxAttempts}</small> : null}
                </div>
              ) : null}
              <fieldset className="decision-options" disabled={submitting || Boolean(decisionAccepted)}>
                <legend>Decisión del Manager</legend>
                <label>
                  <input type="radio" name="decision" value="APPROVE" checked={decision === 'APPROVE'} onChange={() => selectDecision('APPROVE')} />
                  <span><strong>Aprobar</strong><small>Activa contrato y Servicio sintéticos.</small></span>
                </label>
                <label>
                  <input type="radio" name="decision" value="REJECT" checked={decision === 'REJECT'} onChange={() => selectDecision('REJECT')} />
                  <span><strong>Rechazar</strong><small>Cierra la solicitud sin activación.</small></span>
                </label>
              </fieldset>
              <div className="field">
                <label htmlFor="reason-code">Código de razón</label>
                <select
                  id="reason-code"
                  value={reasonCode}
                  onChange={(event) => setReasonCode(event.target.value)}
                  disabled={!decision || submitting || Boolean(decisionAccepted)}
                  aria-describedby="reason-help"
                  aria-invalid={formError ? 'true' : undefined}
                >
                  <option value="">Selecciona una razón</option>
                  {decision ? REASONS[decision].map((reason) => <option key={reason.value} value={reason.value}>{reason.label}</option>) : null}
                </select>
                <p id="reason-help" className="field-help">No escribas texto libre ni datos personales.</p>
              </div>
              <button
                className="button primary"
                type="submit"
                disabled={submitting || Boolean(decisionAccepted)}
                aria-disabled={submitting || Boolean(decisionAccepted)}
              >
                {pollProgress
                  ? 'Esperando estado durable'
                  : decisionAccepted
                    ? 'Decisión ya aceptada'
                    : submitting
                      ? 'Enviando decisión'
                      : 'Confirmar decisión'}
              </button>
              {decisionAccepted && !submitting ? (
                <div className="verification-actions" aria-label="Acciones de verificación">
                  <button className="button secondary" type="button" onClick={() => void verifyDurableState(decisionAccepted)}>
                    Reintentar verificación
                  </button>
                  <Link className="button ghost" to={`/solicitudes/${applicationId}`}>Ver/recargar solicitud</Link>
                </div>
              ) : null}
            </form>
          )}
        </div>
      ) : null}
    </>
  )
}
