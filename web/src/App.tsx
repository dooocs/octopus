import { Loader2 } from 'lucide-react'
import LoginScreen from './components/LoginScreen'
import ScraperDashboard from './components/ScraperDashboard'
import { useAuth } from './lib/auth'

export default function App() {
  const auth = useAuth()

  if (auth.loading) {
    return (
      <main className="loading-shell" aria-live="polite">
        <Loader2 size={22} className="spin" aria-hidden="true" />
        <span>读取登录状态...</span>
      </main>
    )
  }

  if (!auth.user) {
    return (
      <LoginScreen
        busy={auth.busy}
        error={auth.error}
        allowedGithubLogin={auth.allowedGithubLogin}
        hasStableUserIdGate={auth.hasStableUserIdGate}
        onLogin={auth.loginWithGithub}
      />
    )
  }

  return <ScraperDashboard authUser={auth.user} onLogout={auth.logout} />
}
