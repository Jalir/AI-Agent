<template>
  <div :class="['app-layout', { 'is-mobile': store.isMobile, 'sidebar-open': store.sidebarOpen }]">
    <div
      v-if="store.isMobile && store.sidebarOpen"
      class="sidebar-backdrop"
      @click="store.closeSidebar"
    ></div>
    <ChatSidebar />
    <ChatMain v-if="store.activeView === 'chat'" />
    <KnowledgeBase v-else-if="store.activeView === 'knowledgeBase'" />
    <VoiceClone v-else-if="store.activeView === 'voiceClone'" />
    <TranscribeAudio v-else-if="store.activeView === 'transcribe'" />
    <DocumentAnalysis v-else-if="store.activeView === 'docAnalysis'" />
    <SalesAnalysis v-else-if="store.activeView === 'salesAnalysis'" />
    <UserManagement v-else-if="store.activeView === 'userManagement'" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import ChatSidebar from '@/components/ChatSidebar.vue'
import ChatMain from '@/components/ChatMain.vue'
import KnowledgeBase from '@/components/KnowledgeBase.vue'
import VoiceClone from '@/components/VoiceClone.vue'
import TranscribeAudio from '@/components/TranscribeAudio.vue'
import DocumentAnalysis from '@/components/DocumentAnalysis.vue'
import SalesAnalysis from '@/components/SalesAnalysis.vue'
import UserManagement from '@/components/UserManagement.vue'
import { useChatStore } from '@/stores/chat'

const store = useChatStore()

onMounted(async () => {
  store.initSidebarLayout()
  store.resetLocalState()
  await store.loadConversations()
  await store.restoreCurrentId()
  store.initialLoading = false
})

onUnmounted(() => {
  store.teardownSidebarLayout()
})
</script>
