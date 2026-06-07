const env = import.meta.env

const DEFAULT_SUPABASE_URL = 'https://wyhpcfjtmtitorinkevj.supabase.co'
const DEFAULT_SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_Mhngg1gf4z4dkj-xh5TsMg_Pz3crwfo'

export const SUPABASE_URL = env.VITE_SUPABASE_URL || DEFAULT_SUPABASE_URL
export const SUPABASE_KEY =
  env.VITE_SUPABASE_PUBLISHABLE_KEY ||
  env.VITE_SUPABASE_ANON_KEY ||
  DEFAULT_SUPABASE_PUBLISHABLE_KEY

export const ALLOWED_GITHUB_LOGIN = String(
  env.VITE_ALLOWED_GITHUB_LOGIN || 'walihome'
)
  .trim()
  .toLowerCase()

export const ALLOWED_SUPABASE_USER_ID = String(
  env.VITE_ALLOWED_SUPABASE_USER_ID || ''
).trim()

export const isSupabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_KEY)
