import { Github, LockKeyhole, LogIn, ShieldAlert } from 'lucide-react'
import { isSupabaseConfigured } from '../lib/env'

type LoginScreenProps = {
  busy: boolean
  error: string
  allowedGithubLogin: string
  hasStableUserIdGate: boolean
  onLogin: () => void
}

export default function LoginScreen({
  busy,
  error,
  allowedGithubLogin,
  hasStableUserIdGate,
  onLogin
}: LoginScreenProps) {
  return (
    <main className="login-shell">
      <section className="login-panel" aria-labelledby="login-title">
        <div className="brand-mark">
          <LockKeyhole size={24} aria-hidden="true" />
        </div>
        <h1 id="login-title">Octopus Admin</h1>
        <p className="login-subtitle">使用 GitHub 登录后管理抓取配置</p>

        {!isSupabaseConfigured ? (
          <div className="notice notice-warning">
            <ShieldAlert size={18} aria-hidden="true" />
            <div>
              <strong>缺少 Supabase 环境变量</strong>
              <span>请在 Vercel 或本地 `.env.local` 配置 URL 和 publishable key。</span>
            </div>
          </div>
        ) : (
          <>
            <button className="github-login" type="button" disabled={busy} onClick={onLogin}>
              <Github size={18} aria-hidden="true" />
              <span>{busy ? '跳转中...' : '使用 GitHub 登录'}</span>
              <LogIn size={16} aria-hidden="true" />
            </button>
            <div className="login-footnote">
              当前仅允许 @{allowedGithubLogin} 登录
              {hasStableUserIdGate ? '，并已启用 Supabase 用户 ID 校验。' : '。'}
            </div>
          </>
        )}

        {error ? <div className="login-error">{error}</div> : null}
      </section>
    </main>
  )
}
