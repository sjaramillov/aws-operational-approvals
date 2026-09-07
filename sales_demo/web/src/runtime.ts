import { useOutletContext } from 'react-router-dom'
import type { Health, Identity, SalesApi } from './domain'

export interface RuntimeContext {
  identity: Identity
  api: SalesApi
  health: Health | null
}

export function useRuntime(): RuntimeContext {
  return useOutletContext<RuntimeContext>()
}
