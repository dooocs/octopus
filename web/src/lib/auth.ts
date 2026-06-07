import { useCallback, useEffect, useMemo, useState } from 'react'
import type { Session } from '@supabase/supabase-js'
import type { AuthUser } from '../types'
import {
  ALLOWED_GITHUB_LOGIN,
  ALLOWED_SUPABASE_USER_ID,
  isSupabaseConfigured
} from './env'
import { supabase } from './supabase'

type AuthState = {
  loading: boolean
  busy: boolean
  user: AuthUser | null
  error: string
}

function readGithubLogin(session: Session) {
  const meta = session.user.user_metadata || {}
  return String(meta.user_name || meta.preferred_username || '').toLowerCase()
}

function toAuthUser(session: Session): AuthUser {
  const meta = session.user.user_metadata || {}
  const login = readGithubLogin(session)

  return {
    id: session.user.id,
    email: session.user.email,
    name: String(meta.full_name || meta.name || login || 'Admin'),
    login,
    avatarUrl: String(meta.avatar_url || '')
  }
}

function isAllowed(session: Session) {
  if (ALLOWED_SUPABASE_USER_ID) {
    return session.user.id === ALLOWED_SUPABASE_USER_ID
  }

  return readGithubLogin(session) === ALLOWED_GITHUB_LOGIN
}

function clearStaleAuthStorage() {
  Object.keys(window.localStorage)
    .filter((key) => key.startsWith('sb-'))
    .forEach((key) => window.localStorage.removeItem(key))
}

export function useAuth() {
  const [state, setState] = useState<AuthState>({
    loading: true,
    busy: false,
    user: null,
    error: ''
  })

  const finishSession = useCallback(async (session: Session | null) => {
    if (!supabase || !session?.user) {
      setState((current) => ({ ...current, loading: false, user: null }))
      return
    }

    if (!isAllowed(session)) {
      const login = readGithubLogin(session)
      await supabase.auth.signOut()
      clearStaleAuthStorage()
      setState({
        loading: false,
        busy: false,
        user: null,
        error: `用户 @${login || session.user.id} 无权访问此系统`
      })
      return
    }

    setState({
      loading: false,
      busy: false,
      user: toAuthUser(session),
      error: ''
    })
  }, [])

  useEffect(() => {
    if (!isSupabaseConfigured || !supabase) {
      setState((current) => ({ ...current, loading: false }))
      return
    }

    let mounted = true

    supabase.auth.getSession().then(({ data, error }) => {
      if (!mounted) return
      if (error) {
        setState({
          loading: false,
          busy: false,
          user: null,
          error: `读取登录状态失败: ${error.message}`
        })
        return
      }
      void finishSession(data.session)
    })

    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!mounted) return
      void finishSession(session)
    })

    return () => {
      mounted = false
      listener.subscription.unsubscribe()
    }
  }, [finishSession])

  const loginWithGithub = useCallback(async () => {
    if (!supabase) return

    setState((current) => ({ ...current, busy: true, error: '' }))
    try {
      clearStaleAuthStorage()
      const { data, error } = await supabase.auth.signInWithOAuth({
        provider: 'github',
        options: {
          redirectTo: window.location.origin
        }
      })

      if (error) throw error
      if (data.url) window.location.assign(data.url)
    } catch (error) {
      setState((current) => ({
        ...current,
        busy: false,
        error: `登录失败: ${error instanceof Error ? error.message : String(error)}`
      }))
    }
  }, [])

  const logout = useCallback(async () => {
    if (!supabase) return

    await supabase.auth.signOut()
    clearStaleAuthStorage()
    setState({
      loading: false,
      busy: false,
      user: null,
      error: ''
    })
  }, [])

  return useMemo(
    () => ({
      ...state,
      allowedGithubLogin: ALLOWED_GITHUB_LOGIN,
      hasStableUserIdGate: Boolean(ALLOWED_SUPABASE_USER_ID),
      loginWithGithub,
      logout
    }),
    [loginWithGithub, logout, state]
  )
}
