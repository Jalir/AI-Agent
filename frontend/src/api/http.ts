/** 统一 HTTP：Bearer + credentials；401 时尝试 refresh 后重试一次。 */

const API_BASE = ''

type TokenGetter = () => string | null
type TokenSetter = (token: string | null) => void
type OnAuthLost = () => void

let getAccessToken: TokenGetter = () => null
let setAccessToken: TokenSetter = () => {}
let onAuthLost: OnAuthLost = () => {}
let refreshPromise: Promise<boolean> | null = null

export function configureAuthHandlers(opts: {
  getAccessToken: TokenGetter
  setAccessToken: TokenSetter
  onAuthLost: OnAuthLost
}) {
  getAccessToken = opts.getAccessToken
  setAccessToken = opts.setAccessToken
  onAuthLost = opts.onAuthLost
}

export function apiUrl(path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}

async function tryRefresh(): Promise<boolean> {
  if (refreshPromise) return refreshPromise
  refreshPromise = (async () => {
    try {
      const res = await fetch(apiUrl('/api/auth/refresh'), {
        method: 'POST',
        credentials: 'include',
      })
      if (!res.ok) {
        setAccessToken(null)
        return false
      }
      const data = (await res.json()) as { access_token?: string }
      if (!data.access_token) {
        setAccessToken(null)
        return false
      }
      setAccessToken(data.access_token)
      return true
    } catch {
      setAccessToken(null)
      return false
    } finally {
      refreshPromise = null
    }
  })()
  return refreshPromise
}

export async function apiFetch(
  path: string,
  init: RequestInit = {},
  opts: { skipAuth?: boolean; retry?: boolean } = {},
): Promise<Response> {
  const headers = new Headers(init.headers || {})
  const token = getAccessToken()
  if (!opts.skipAuth && token) {
    headers.set('Authorization', `Bearer ${token}`)
  }
  // FormData 时不要强行设 Content-Type，让浏览器带 boundary
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const res = await fetch(apiUrl(path), {
    ...init,
    headers,
    credentials: 'include',
  })

  if (res.status !== 401 || opts.skipAuth || opts.retry === false) {
    return res
  }

  const refreshed = await tryRefresh()
  if (!refreshed) {
    onAuthLost()
    return res
  }

  const retryHeaders = new Headers(init.headers || {})
  const next = getAccessToken()
  if (next) retryHeaders.set('Authorization', `Bearer ${next}`)
  if (init.body && !(init.body instanceof FormData) && !retryHeaders.has('Content-Type')) {
    retryHeaders.set('Content-Type', 'application/json')
  }

  return fetch(apiUrl(path), {
    ...init,
    headers: retryHeaders,
    credentials: 'include',
  })
}

export async function apiJson<T>(
  path: string,
  init: RequestInit = {},
  opts?: { skipAuth?: boolean },
): Promise<T> {
  const res = await apiFetch(path, init, opts)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}
