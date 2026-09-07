import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { createDemoApi, DEMO_IDENTITIES } from '../api/demo'
import { createHttpApi } from '../api/http'
import type { Identity, SalesApi } from '../domain'
import { IS_DEMO_MODE, type PublicRuntimeConfig } from '../runtimeConfig'
import {
  beginCognitoLogin,
  clearAccessToken,
  cognitoLogoutUrl,
  completeCognitoLogin,
  getAccessToken,
  getValidAccessToken,
} from './pkce'

const DEMO_IDENTITY_KEY = 'approvals-demo-identity'

interface AuthValue {
  identity: Identity | null
  api: SalesApi | null
  loading: boolean
  error: string | null
  isDemo: boolean
  demoIdentities: Identity[]
  loginDemo(subject: string): void
  loginCognito(): Promise<void>
  completeCallback(search: string): Promise<void>
  logout(): void
}

const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children, runtimeConfig }: { children: ReactNode; runtimeConfig: PublicRuntimeConfig | null }) {
  const [identity, setIdentity] = useState<Identity | null>(null)
  const [api, setApi] = useState<SalesApi | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const authenticationFailed = useCallback(() => {
    clearAccessToken()
    setIdentity(null)
    setApi(null)
    setError('La sesión expiró y no pudo renovarse. Ingresa nuevamente.')
  }, [])

  const hydrateHttpIdentity = useCallback(async () => {
    if (!runtimeConfig) throw new Error('La configuración pública de despliegue no está disponible.')
    const httpApi = createHttpApi(
      {
        getAccessToken: (forceRefresh = false) => getValidAccessToken(runtimeConfig, forceRefresh),
        onAuthenticationFailure: authenticationFailed,
      },
      runtimeConfig.apiBaseUrl,
    )
    const me = await httpApi.getMe()
    setApi(httpApi)
    setIdentity(me)
    setError(null)
  }, [authenticationFailed, runtimeConfig])

  useEffect(() => {
    const hydrate = async () => {
      try {
        if (IS_DEMO_MODE) {
          const subject = sessionStorage.getItem(DEMO_IDENTITY_KEY)
          const selected = DEMO_IDENTITIES.find((candidate) => candidate.subject === subject) ?? null
          if (selected) {
            setIdentity(selected)
            setApi(createDemoApi(selected))
          }
          return
        }
        const token = getAccessToken()
        if (token) await hydrateHttpIdentity()
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : 'No se pudo restaurar la sesión.')
      } finally {
        setLoading(false)
      }
    }
    void hydrate()
  }, [hydrateHttpIdentity])

  const loginDemo = useCallback((subject: string) => {
    const selected = DEMO_IDENTITIES.find((candidate) => candidate.subject === subject)
    if (!selected) {
      setError('La persona sintética seleccionada no existe.')
      return
    }
    sessionStorage.setItem(DEMO_IDENTITY_KEY, selected.subject)
    setIdentity(selected)
    setApi(createDemoApi(selected))
    setError(null)
  }, [])

  const logout = useCallback(() => {
    setIdentity(null)
    setApi(null)
    setError(null)
    if (IS_DEMO_MODE) {
      sessionStorage.removeItem(DEMO_IDENTITY_KEY)
      return
    }
    clearAccessToken()
    if (runtimeConfig) window.location.assign(cognitoLogoutUrl(runtimeConfig))
  }, [runtimeConfig])

  const completeCallback = useCallback(
    async (search: string) => {
      setLoading(true)
      setError(null)
      try {
        if (!runtimeConfig) throw new Error('La configuración pública de Cognito no está disponible.')
        await completeCognitoLogin(search, runtimeConfig)
        const token = getAccessToken()
        if (!token) throw new Error('La sesión autenticada no quedó disponible.')
        await hydrateHttpIdentity()
      } finally {
        setLoading(false)
      }
    },
    [hydrateHttpIdentity, runtimeConfig],
  )

  const loginCognito = useCallback(async () => {
    if (!runtimeConfig) throw new Error('La configuración pública de Cognito no está disponible.')
    await beginCognitoLogin(runtimeConfig)
  }, [runtimeConfig])

  const value = useMemo<AuthValue>(
    () => ({
      identity,
      api,
      loading,
      error,
      isDemo: IS_DEMO_MODE,
      demoIdentities: DEMO_IDENTITIES,
      loginDemo,
      loginCognito,
      completeCallback,
      logout,
    }),
    [api, completeCallback, error, identity, loading, loginCognito, loginDemo, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthValue {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth debe usarse dentro de AuthProvider.')
  return context
}
