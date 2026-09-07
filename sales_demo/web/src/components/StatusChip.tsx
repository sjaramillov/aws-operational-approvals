import { STATUS_LABELS, type ApplicationStatus, type PilotStatus } from '../domain'

export function StatusChip({ status }: { status: ApplicationStatus | PilotStatus }) {
  const label =
    status === 'ACTIVE'
      ? 'Piloto activo'
      : status === 'PREPARED'
        ? 'Piloto en preparación'
        : status === 'EXPIRED'
          ? 'Expirado'
          : STATUS_LABELS[status]
  const tone =
    status === 'ACTIVE' || status === 'APPROVED' || status === 'CONTRACT_ACTIVE'
      ? 'success'
      : status === 'REJECTED' || status === 'EXPIRED'
        ? 'danger'
        : 'pending'
  const symbol = tone === 'success' ? '✓' : tone === 'danger' ? '!' : '•'
  return (
    <span className={`status-chip ${tone}`} data-status={status}>
      <span aria-hidden="true">{symbol}</span>
      {label}
    </span>
  )
}
