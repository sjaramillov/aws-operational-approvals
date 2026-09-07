import { useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { PageHeader } from '../components/PageHeader'
import { errorMessage } from '../format'
import { useRuntime } from '../runtime'

export function NewApplicationPage() {
  const { api } = useRuntime()
  const navigate = useNavigate()
  const [vehicleCount, setVehicleCount] = useState('')
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const errorRef = useRef<HTMLDivElement>(null)
  const idempotencyKey = useRef(crypto.randomUUID())

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setFieldError(null)
    setSubmitError(null)
    const parsed = Number(vehicleCount)
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > 10_000) {
      setFieldError('Ingresa un número entero entre 1 y 10.000.')
      window.setTimeout(() => errorRef.current?.focus(), 0)
      return
    }
    setSubmitting(true)
    try {
      const result = await api.createApplication(parsed, idempotencyKey.current)
      navigate(`/solicitudes/${result.application.applicationId}`, {
        state: { notice: result.replayed ? 'La solicitud ya existía. No se creó un duplicado.' : 'Solicitud registrada.' },
      })
    } catch (caught) {
      setSubmitError(errorMessage(caught))
      window.setTimeout(() => errorRef.current?.focus(), 0)
      setSubmitting(false)
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Nueva solicitud"
        title="Registrar flota sintética"
        description="El único dato requerido es la cantidad de vehículos. No ingreses nombres, placas ni otra PII."
      />
      <div className="form-layout">
        <form className="surface form-surface" onSubmit={submit} noValidate>
          {(fieldError || submitError) ? (
            <div className="form-error-summary" role="alert" tabIndex={-1} ref={errorRef}>
              <strong>Corrige la solicitud</strong>
              <p>{fieldError ?? submitError}</p>
            </div>
          ) : null}
          <div className="field">
            <label htmlFor="vehicle-count">Cantidad de vehículos</label>
            <input
              id="vehicle-count"
              name="vehicleCount"
              type="number"
              inputMode="numeric"
              min="1"
              max="10000"
              step="1"
              value={vehicleCount}
              onChange={(event) => setVehicleCount(event.target.value)}
              aria-invalid={fieldError ? 'true' : undefined}
              aria-describedby="vehicle-help vehicle-error"
              autoFocus
            />
            <p id="vehicle-help" className="field-help">50 se aprueba automáticamente. Más de 50 requiere decisión del Manager.</p>
            <span id="vehicle-error" className="field-error">{fieldError}</span>
          </div>
          <div className="boundary-examples" aria-label="Casos demostrables">
            <span>Casos de borde:</span>
            {[50, 51, 52].map((count) => (
              <button className="button ghost compact" type="button" key={count} onClick={() => setVehicleCount(String(count))}>
                Usar {count}
              </button>
            ))}
          </div>
          <div className="form-actions">
            <Link className="button secondary" to="/solicitudes">Cancelar</Link>
            <button className="button primary" type="submit" disabled={submitting} aria-disabled={submitting}>
              {submitting ? 'Registrando solicitud' : 'Registrar solicitud'}
            </button>
          </div>
        </form>
        <aside className="decision-guide" aria-labelledby="decision-guide-title">
          <p className="eyebrow">Regla verificable</p>
          <h2 id="decision-guide-title">Límite 50/51</h2>
          <dl>
            <div><dt>1 a 50</dt><dd>Aprobación automática y contrato sintético activo.</dd></div>
            <div><dt>51 o más</dt><dd>Estado durable pendiente hasta que un Manager del mismo tenant decida.</dd></div>
          </dl>
        </aside>
      </div>
    </>
  )
}
