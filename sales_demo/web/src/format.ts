import type { UserRole } from './domain'

export function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat('es-CO', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'America/Bogota',
    timeZoneName: 'short',
  }).format(new Date(value))
}

export function roleLabel(role: UserRole): string {
  return role === 'CUSTOMER' ? 'Customer' : 'Manager'
}

export function errorMessage(caught: unknown): string {
  if (caught instanceof DOMException && caught.name === 'AbortError') return ''
  return caught instanceof Error ? caught.message : 'Ocurrió un error inesperado.'
}
