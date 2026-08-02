import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiFetch } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import type { AuthUser } from '@/stores/auth'
import { showAlert } from '@/utils/dialog'
import { errorFromResponse, toUserError } from '@/utils/safeError'

export type ManagedUser = AuthUser & {
  created_at?: string
  updated_at?: string
}

export const useUserManagementStore = defineStore('userManagement', () => {
  const users = ref<ManagedUser[]>([])
  const initialLoading = ref(true)
  const loading = ref(false)
  const deletingId = ref<number | null>(null)
  const error = ref('')

  async function fetchUsers() {
    const auth = useAuthStore()
    if (!auth.isAdmin) {
      users.value = []
      initialLoading.value = false
      error.value = '需要管理员权限'
      return
    }

    loading.value = true
    error.value = ''
    try {
      const res = await apiFetch('/api/auth/admin/users?limit=200')
      if (!res.ok) {
        throw new Error(await errorFromResponse(res, '获取用户列表失败'))
      }
      users.value = (await res.json()) as ManagedUser[]
    } catch (err) {
      error.value = toUserError(err, '获取用户列表失败')
      console.error('获取用户列表失败', err)
    } finally {
      loading.value = false
      initialLoading.value = false
    }
  }

  async function deleteUser(userId: number) {
    const auth = useAuthStore()
    if (!auth.isAdmin) {
      await showAlert('仅管理员可以删除用户')
      return false
    }
    if (auth.user?.id === userId) {
      await showAlert('不能删除当前登录账号')
      return false
    }

    deletingId.value = userId
    error.value = ''
    try {
      const res = await apiFetch(`/api/auth/admin/users/${userId}`, {
        method: 'DELETE',
      })
      if (!res.ok) {
        throw new Error(await errorFromResponse(res, '删除用户失败'))
      }
      users.value = users.value.filter((u) => u.id !== userId)
      return true
    } catch (err) {
      const msg = toUserError(err, '删除用户失败')
      error.value = msg
      await showAlert(msg)
      return false
    } finally {
      deletingId.value = null
    }
  }

  function reset() {
    users.value = []
    initialLoading.value = true
    loading.value = false
    deletingId.value = null
    error.value = ''
  }

  return {
    users,
    initialLoading,
    loading,
    deletingId,
    error,
    fetchUsers,
    deleteUser,
    reset,
  }
})
