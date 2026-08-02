import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { apiFetch, apiJson, configureAuthHandlers } from '@/api/http'

export interface AuthUser {
  id: number
  username: string
  email: string | null
  role: 'admin' | 'user' | string
  is_active: boolean
  /** RBAC 权限码，如 email.send */
  permissions?: string[]
}

interface TokenResponse {
  access_token: string
  token_type: string
  user: AuthUser
}

export const useAuthStore = defineStore('auth', () => {
  const accessToken = ref<string | null>(null)
  const user = ref<AuthUser | null>(null)
  const bootstrapped = ref(false)
  const busy = ref(false)

  const isAuthenticated = computed(() => !!accessToken.value && !!user.value)
  const isAdmin = computed(() => user.value?.role === 'admin')
  const permissions = computed(() => user.value?.permissions || [])

  function hasPermission(code: string): boolean {
    return permissions.value.includes(code)
  }

  configureAuthHandlers({
    getAccessToken: () => accessToken.value,
    setAccessToken: (t) => {
      accessToken.value = t
    },
    onAuthLost: () => {
      accessToken.value = null
      user.value = null
      void import('@/stores/voiceClone').then(({ useVoiceCloneStore }) => {
        try {
          useVoiceCloneStore().resetAll()
        } catch {
          /* pinia 可能尚未就绪 */
        }
      })
      void import('@/router').then(({ default: router }) => {
        if (router.currentRoute.value.meta.requiresAuth) {
          void router.replace({ name: 'login' })
        }
      })
    },
  })

  function _applyTokenResponse(data: TokenResponse) {
    accessToken.value = data.access_token
    user.value = data.user
  }

  async function bootstrap() {
    if (bootstrapped.value) return
    try {
      const res = await apiFetch('/api/auth/refresh', { method: 'POST' }, { skipAuth: true })
      if (res.ok) {
        const data = (await res.json()) as TokenResponse
        _applyTokenResponse(data)
      }
    } catch {
      /* 未登录 */
    } finally {
      bootstrapped.value = true
    }
  }

  async function login(username: string, password: string) {
    busy.value = true
    try {
      const data = await apiJson<TokenResponse>(
        '/api/auth/login',
        {
          method: 'POST',
          body: JSON.stringify({ username, password }),
        },
        { skipAuth: true },
      )
      _applyTokenResponse(data)
    } finally {
      busy.value = false
    }
  }

  async function register(username: string, password: string, email?: string) {
    busy.value = true
    try {
      const payload: Record<string, string> = { username, password }
      if (email?.trim()) payload.email = email.trim()
      const data = await apiJson<TokenResponse>(
        '/api/auth/register',
        {
          method: 'POST',
          body: JSON.stringify(payload),
        },
        { skipAuth: true },
      )
      _applyTokenResponse(data)
    } finally {
      busy.value = false
    }
  }

  async function logout() {
    try {
      await apiFetch('/api/auth/logout', { method: 'POST' }, { skipAuth: true })
    } catch {
      /* ignore */
    }
    accessToken.value = null
    user.value = null
    try {
      const { useVoiceCloneStore } = await import('@/stores/voiceClone')
      useVoiceCloneStore().resetAll()
    } catch {
      /* ignore */
    }
  }

  async function fetchMe() {
    if (!accessToken.value) return
    user.value = await apiJson<AuthUser>('/api/auth/me')
  }

  return {
    accessToken,
    user,
    bootstrapped,
    busy,
    isAuthenticated,
    isAdmin,
    permissions,
    hasPermission,
    bootstrap,
    login,
    register,
    logout,
    fetchMe,
  }
})
