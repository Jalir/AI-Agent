<template>
  <main class="kb-main um-main">
    <header class="kb-header">
      <div class="kb-header-left">
        <button
          v-if="chatStore.isMobile"
          class="menu-btn"
          title="打开导航"
          @click="chatStore.toggleSidebar"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <rect x="3" y="3" width="18" height="18" rx="2" />
            <path d="M9 3v18" />
            <polyline points="12 8 16 12 12 16" />
          </svg>
        </button>
        <h1 class="kb-title">用户管理</h1>
        <span v-if="store.users.length" class="kb-count">{{ store.users.length }} 位用户</span>
      </div>
      <button
        class="kb-create-btn um-refresh-btn"
        :disabled="store.loading"
        title="刷新列表"
        @click="store.fetchUsers()"
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          :class="{ 'um-spin': store.loading }"
        >
          <polyline points="23 4 23 10 17 10" />
          <polyline points="1 20 1 14 7 14" />
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
        </svg>
        <span>刷新</span>
      </button>
    </header>

    <div class="kb-body">
      <div v-if="store.error && !store.initialLoading" class="um-error">{{ store.error }}</div>

      <div v-if="store.initialLoading" class="kb-empty">
        <div class="loading-spinner"></div>
        <p>加载用户列表...</p>
      </div>

      <div v-else-if="store.users.length === 0" class="kb-empty">
        <div class="kb-empty-icon">
          <svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
        </div>
        <h3>暂无用户</h3>
        <p>系统中尚无后台用户记录。</p>
      </div>

      <div v-else class="kb-file-table um-table">
        <div class="kb-file-header um-header">
          <span class="um-col-user">用户</span>
          <span class="um-col-email">邮箱</span>
          <span class="um-col-role">角色</span>
          <span class="um-col-status">状态</span>
          <span class="um-col-date">注册时间</span>
          <span class="um-col-actions"></span>
        </div>
        <div
          v-for="user in store.users"
          :key="user.id"
          class="kb-file-row um-row"
        >
          <div class="um-col-user">
            <span class="um-avatar" :class="{ 'um-avatar-admin': user.role === 'admin' }">
              {{ userInitial(user.username) }}
            </span>
            <div class="um-user-meta">
              <span class="um-username">{{ user.username }}</span>
              <span class="um-userid">ID {{ user.id }}</span>
            </div>
          </div>
          <span class="um-col-email" :title="user.email || ''">{{ user.email || '—' }}</span>
          <span class="um-col-role">
            <span class="um-badge" :class="user.role === 'admin' ? 'um-badge-admin' : 'um-badge-user'">
              {{ roleLabel(user.role) }}
            </span>
          </span>
          <span class="um-col-status">
            <span class="um-badge" :class="user.is_active ? 'um-badge-active' : 'um-badge-inactive'">
              {{ user.is_active ? '正常' : '已禁用' }}
            </span>
          </span>
          <span class="um-col-date">{{ formatDate(user.created_at) }}</span>
          <button
            class="kb-file-delete um-delete"
            :disabled="deletingSelf(user) || store.deletingId === user.id"
            :title="deletingSelf(user) ? '不能删除当前账号' : '删除用户'"
            @click="handleDelete(user)"
          >
            <svg
              v-if="store.deletingId !== user.id"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
            >
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
            <span v-else class="uploading-spinner"></span>
          </button>
        </div>
      </div>
    </div>
  </main>
</template>

<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useChatStore } from '../stores/chat'
import { useUserManagementStore, type ManagedUser } from '../stores/userManagement'
import { showConfirm } from '@/utils/dialog'

const auth = useAuthStore()
const chatStore = useChatStore()
const store = useUserManagementStore()

onMounted(() => {
  if (!auth.isAdmin) {
    chatStore.activeView = 'chat'
    return
  }
  store.fetchUsers()
})

watch(
  () => auth.isAdmin,
  (isAdmin) => {
    if (!isAdmin && chatStore.activeView === 'userManagement') {
      chatStore.activeView = 'chat'
    }
  },
)

function deletingSelf(user: ManagedUser): boolean {
  return auth.user?.id === user.id
}

function userInitial(name: string): string {
  const n = (name || '?').trim()
  return n.slice(0, 2).toUpperCase()
}

function roleLabel(role: string): string {
  if (role === 'admin') return '管理员'
  if (role === 'user') return '用户'
  return role
}

function formatDate(dateStr?: string): string {
  if (!dateStr) return '—'
  const d = new Date(dateStr)
  if (Number.isNaN(d.getTime())) return '—'
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

async function handleDelete(user: ManagedUser) {
  if (deletingSelf(user)) return
  const ok = await showConfirm(
    `确定删除用户「${user.username}」吗？将同时清除其会话、文档/销售工作区、声音克隆等个人数据，且不可撤销。`,
    { title: '删除用户', confirmText: '删除' },
  )
  if (ok) {
    void store.deleteUser(user.id)
  }
}
</script>
