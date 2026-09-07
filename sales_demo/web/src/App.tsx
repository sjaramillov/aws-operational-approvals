import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import { AppShell } from './components/AppShell'
import type { UserRole } from './domain'
import { ApplicationDetailPage } from './pages/ApplicationDetailPage'
import { ApplicationsPage } from './pages/ApplicationsPage'
import { ApprovalDecisionPage } from './pages/ApprovalDecisionPage'
import { ApprovalsPage } from './pages/ApprovalsPage'
import { AuthCallbackPage } from './pages/AuthCallbackPage'
import { LoginPage } from './pages/LoginPage'
import { NewApplicationPage } from './pages/NewApplicationPage'
import { PlanPlusPage } from './pages/PlanPlusPage'
import type { PublicRuntimeConfig } from './runtimeConfig'

function RoleHome() {
  const { identity } = useAuth()
  if (!identity) return <Navigate to="/ingreso" replace />
  return <Navigate to={identity.role === 'CUSTOMER' ? '/solicitudes' : '/aprobaciones'} replace />
}

function RequireRole({ role, children }: { role: UserRole; children: React.ReactNode }) {
  const { identity } = useAuth()
  if (!identity) return <Navigate to="/ingreso" replace />
  if (identity.role !== role) return <Navigate to="/" replace />
  return children
}

function NotFoundPage() {
  return (
    <section className="not-found" aria-labelledby="route-not-found">
      <p className="eyebrow">404</p>
      <h1 id="route-not-found">Esta ruta no existe</h1>
      <p>Regresa al inicio del flujo correspondiente a tu rol.</p>
      <a className="button primary" href="/">Ir al inicio</a>
    </section>
  )
}

export function App({ runtimeConfig }: { runtimeConfig: PublicRuntimeConfig | null }) {
  return (
    <AuthProvider runtimeConfig={runtimeConfig}>
      <BrowserRouter>
        <Routes>
          <Route path="/ingreso" element={<LoginPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
          <Route element={<AppShell />}>
            <Route index element={<RoleHome />} />
            <Route path="solicitudes" element={<RequireRole role="CUSTOMER"><ApplicationsPage /></RequireRole>} />
            <Route path="solicitudes/nueva" element={<RequireRole role="CUSTOMER"><NewApplicationPage /></RequireRole>} />
            <Route path="solicitudes/:applicationId" element={<ApplicationDetailPage />} />
            <Route path="plan-plus" element={<RequireRole role="CUSTOMER"><PlanPlusPage /></RequireRole>} />
            <Route path="aprobaciones" element={<RequireRole role="MANAGER"><ApprovalsPage /></RequireRole>} />
            <Route path="aprobaciones/:applicationId" element={<RequireRole role="MANAGER"><ApprovalDecisionPage /></RequireRole>} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
