export function LoadingSkeleton({ label = 'Cargando contenido' }: { label?: string }) {
  return (
    <div className="skeleton-stack" aria-busy="true" aria-label={label}>
      <span className="sr-only">{label}</span>
      <span className="skeleton skeleton-title" aria-hidden="true" />
      <span className="skeleton" aria-hidden="true" />
      <span className="skeleton" aria-hidden="true" />
    </div>
  )
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <section className="state-panel state-error" role="alert" aria-labelledby="error-title">
      <p className="eyebrow">No se pudo completar</p>
      <h2 id="error-title">Revisa la operación</h2>
      <p>{message}</p>
      {onRetry ? (
        <button className="button secondary" type="button" onClick={onRetry}>
          Intentar de nuevo
        </button>
      ) : null}
    </section>
  )
}

export function EmptyState({ title, detail, action }: { title: string; detail: string; action?: React.ReactNode }) {
  return (
    <section className="state-panel" aria-labelledby="empty-title">
      <p className="eyebrow">Sin elementos</p>
      <h2 id="empty-title">{title}</h2>
      <p>{detail}</p>
      {action}
    </section>
  )
}
