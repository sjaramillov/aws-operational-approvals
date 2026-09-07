import { REASON_LABELS, type AuditEvent } from '../domain'
import { formatDateTime, roleLabel } from '../format'

const ACTION_LABELS: Record<string, string> = {
  APPLICATION_SUBMITTED: 'Solicitud registrada',
  MANAGER_APPROVAL_REQUESTED: 'Aprobación del Manager solicitada',
  MANAGER_DECISION_RECORDED: 'Decisión del Manager registrada',
  CONTRACT_ACTIVATED: 'Contrato sintético activado',
  APPLICATION_REJECTED: 'Solicitud rechazada',
}

export function AuditTimeline({ events }: { events: AuditEvent[] }) {
  return (
    <ol className="audit-list" aria-label="Rastro de auditoría">
      {events.map((event, index) => (
        <li key={`${event.occurredAt}:${index}`}>
          <div className="audit-marker" aria-hidden="true">{index + 1}</div>
          <div>
            <h3>{ACTION_LABELS[event.action] ?? event.action}</h3>
            <dl className="audit-meta">
              <div>
                <dt>Actor</dt>
                <dd>{`${roleLabel(event.actorRole)} · ${event.actorId}`}</dd>
              </div>
              <div>
                <dt>Fecha</dt>
                <dd><time dateTime={event.occurredAt}>{formatDateTime(event.occurredAt)}</time></dd>
              </div>
              {event.decision ? (
                <div>
                  <dt>Decisión</dt>
                  <dd>{event.decision === 'APPROVE' ? 'Aprobar' : 'Rechazar'}</dd>
                </div>
              ) : null}
              {event.reasonCode ? (
                <div>
                  <dt>Razón</dt>
                  <dd>{REASON_LABELS[event.reasonCode] ?? event.reasonCode}</dd>
                </div>
              ) : null}
            </dl>
          </div>
        </li>
      ))}
    </ol>
  )
}
