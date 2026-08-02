<template>
  <div
    :class="[
      'sidebar-shell',
      {
        'sidebar-open': store.sidebarOpen,
        'history-visible': historyVisible,
      },
    ]"
  >
    <aside class="nav-rail" aria-label="主导航">
      <div class="nav-rail-brand" title="LangGraph">
        <div class="nav-rail-logo" aria-hidden="true">
          <BrandMark :size="18" />
        </div>
      </div>

      <nav class="nav-rail-nav">
        <button
          type="button"
          :class="['nav-rail-btn', { active: store.activeView === 'chat' }]"
          title="聊天"
          @click="goChat"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
        </button>
        <button
          type="button"
          :class="['nav-rail-btn', { active: store.activeView === 'knowledgeBase' }]"
          title="知识库"
          @click="goView('knowledgeBase')"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
          </svg>
        </button>
        <button
          type="button"
          :class="['nav-rail-btn', { active: store.activeView === 'voiceClone' }]"
          title="声音克隆"
          @click="goView('voiceClone')"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
            <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            <line x1="12" y1="19" x2="12" y2="23" />
            <line x1="8" y1="23" x2="16" y2="23" />
          </svg>
        </button>
        <button
          type="button"
          :class="['nav-rail-btn', { active: store.activeView === 'transcribe' }]"
          title="转录音频"
          @click="goView('transcribe')"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="8" y1="13" x2="16" y2="13" />
            <line x1="8" y1="17" x2="14" y2="17" />
          </svg>
        </button>
        <button
          type="button"
          :class="['nav-rail-btn', { active: store.activeView === 'docAnalysis' }]"
          title="文档分析"
          @click="goView('docAnalysis')"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <path d="M8 13h2" />
            <path d="M8 17h8" />
            <path d="M14 13h2l-1 4h-2l1-4z" />
          </svg>
        </button>
        <button
          type="button"
          :class="['nav-rail-btn', { active: store.activeView === 'salesAnalysis' }]"
          title="销售分析"
          @click="goView('salesAnalysis')"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 3v18h18" />
            <path d="M7 14l3-3 3 2 5-6" />
          </svg>
        </button>
        <button
          v-if="auth.isAdmin"
          type="button"
          :class="['nav-rail-btn', { active: store.activeView === 'userManagement' }]"
          title="用户管理"
          @click="goView('userManagement')"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
        </button>
      </nav>

      <div class="nav-rail-footer">
        <button
          type="button"
          class="nav-rail-btn"
          :title="theme === 'light' ? '切换深色模式' : '切换浅色模式'"
          @click="toggleTheme"
        >
          <svg v-if="theme === 'light'" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
          </svg>
          <svg v-else width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
            <circle cx="12" cy="12" r="5" />
            <line x1="12" y1="1" x2="12" y2="3" />
            <line x1="12" y1="21" x2="12" y2="23" />
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
            <line x1="1" y1="12" x2="3" y2="12" />
            <line x1="21" y1="12" x2="23" y2="12" />
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
          </svg>
        </button>
        <div v-if="auth.user" class="nav-rail-user" :title="auth.user.username">
          <span class="nav-rail-avatar">{{ userInitial }}</span>
          <button type="button" class="nav-rail-logout" title="退出登录" @click="handleLogout">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
          </button>
        </div>
      </div>
    </aside>

    <aside
      class="history-panel"
      :aria-hidden="!historyVisible"
      :class="{ 'is-open': historyVisible }"
    >
      <div class="history-panel-inner">
        <div class="history-top">
          <h2 class="history-title">聊天</h2>
          <button
            type="button"
            class="icon-btn"
            :disabled="store.conversations.length === 0"
            :title="store.conversations.length === 0 ? '暂无对话' : '清空所有对话'"
            @click="handleClearAll"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
            </svg>
          </button>
        </div>

        <button type="button" class="new-chat-btn" @click="handleNew">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          <span>新建对话</span>
        </button>

        <div class="conversation-list">
          <div
            v-for="conv in store.sortedConversations"
            :key="conv.id"
            :class="[
              'conv-item',
              {
                active: conv.id === store.currentId && store.activeView === 'chat',
                busy: store.isConversationStreaming(conv.id),
              },
            ]"
            @click="handleSelect(conv.id)"
          >
            <div class="conv-main">
              <div class="conv-title">
                <span
                  v-if="store.isConversationStreaming(conv.id)"
                  class="conv-busy-dot"
                  title="生成中"
                  aria-label="生成中"
                ></span>
                <span class="conv-title-text">{{ conv.title }}</span>
              </div>
              <span class="conv-time">{{ formatConvTime(conv.updatedAt) }}</span>
            </div>
            <button
              type="button"
              class="conv-delete"
              title="删除对话"
              @click.stop="handleDelete(conv.id)"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
              </svg>
            </button>
          </div>

          <div v-if="store.conversationsLoadError" class="list-error">
            <p class="list-error-text">加载失败</p>
            <button
              type="button"
              class="list-error-retry"
              :disabled="reloading"
              @click="handleReload"
            >
              {{ reloading ? '刷新中…' : '刷新' }}
            </button>
          </div>
          <div
            v-else-if="!store.initialLoading && store.conversations.length === 0"
            class="empty-hint"
          >
            暂无对话记录
          </div>
        </div>
      </div>
    </aside>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { useAuthStore } from '../stores/auth'
import { useTheme } from '../composables/useTheme'
import { useKnowledgeBaseStore } from '../stores/knowledgeBase'
import { useVoiceCloneStore } from '../stores/voiceClone'
import { useTranscribeStore } from '../stores/transcribe'
import { useDocAnalysisStore } from '../stores/docAnalysis'
import { useSalesAnalysisStore } from '../stores/salesAnalysis'
import { useUserManagementStore } from '../stores/userManagement'
import { showConfirm } from '@/utils/dialog'
import BrandMark from './BrandMark.vue'

const store = useChatStore()
const auth = useAuthStore()
const kb = useKnowledgeBaseStore()
const voiceClone = useVoiceCloneStore()
const transcribe = useTranscribeStore()
const docAnalysis = useDocAnalysisStore()
const salesAnalysis = useSalesAnalysisStore()
const userManagement = useUserManagementStore()
const router = useRouter()
const { theme, toggleTheme } = useTheme()
const reloading = ref(false)

/** 聊天页 + sidebarOpen 时展示会话历史 */
const historyVisible = computed(
  () => store.activeView === 'chat' && store.sidebarOpen,
)

const userInitial = computed(() => {
  const name = auth.user?.username?.trim() || '?'
  return name.slice(0, 2).toUpperCase()
})

function formatConvTime(ts: number): string {
  if (!ts) return ''
  const d = new Date(ts)
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  if (sameDay) {
    return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  }
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (
    d.getFullYear() === yesterday.getFullYear() &&
    d.getMonth() === yesterday.getMonth() &&
    d.getDate() === yesterday.getDate()
  ) {
    return '昨天'
  }
  if (d.getFullYear() === now.getFullYear()) {
    return `${d.getMonth() + 1}/${d.getDate()}`
  }
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
}

async function handleReload() {
  if (reloading.value) return
  reloading.value = true
  try {
    await store.loadConversations()
    if (!store.conversationsLoadError) {
      await store.restoreCurrentId()
    }
  } finally {
    reloading.value = false
  }
}

function handleNew() {
  store.activeView = 'chat'
  store.newConversation()
  store.closeSidebarIfMobile()
}

function handleSelect(id: string) {
  store.activeView = 'chat'
  store.selectConversation(id)
  store.closeSidebarIfMobile()
}

function goChat() {
  store.activeView = 'chat'
  if (!store.sidebarOpen) store.openSidebar()
}

function goView(
  view:
    | 'knowledgeBase'
    | 'voiceClone'
    | 'transcribe'
    | 'docAnalysis'
    | 'salesAnalysis'
    | 'userManagement',
) {
  if (view === 'userManagement' && !auth.isAdmin) return
  store.activeView = view
  store.closeSidebarIfMobile()
}

async function handleClearAll() {
  const ok = await showConfirm('确定删除所有对话吗？此操作不可撤销。', {
    title: '清空全部对话',
    confirmText: '删除全部',
  })
  if (ok) store.deleteAllConversations()
}

async function handleDelete(id: string) {
  const ok = await showConfirm('确定删除这个对话吗？', {
    title: '删除对话',
    confirmText: '删除',
  })
  if (ok) store.deleteConversation(id)
}

async function handleLogout() {
  kb.stopPolling()
  store.resetLocalState()
  voiceClone.resetAll()
  transcribe.resetAll()
  docAnalysis.resetAll()
  salesAnalysis.resetAll()
  userManagement.reset()
  await auth.logout()
  await router.replace({ name: 'login' })
}
</script>
