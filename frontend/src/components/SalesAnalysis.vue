<template>
  <main class="chat-main">
    <header class="chat-header">
      <!-- 仅移动端：展开左侧导航；桌面端导航常显，且非聊天页无历史列表可展开 -->
      <button
        v-if="chatStore.isMobile"
        class="menu-btn"
        @click="chatStore.toggleSidebar"
        title="打开导航"
      >
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <rect x="3" y="3" width="18" height="18" rx="2" />
          <path d="M9 3v18" />
          <polyline points="12 8 16 12 12 16" />
        </svg>
      </button>
      <ChatHeaderTitle title="销售分析" />
      <div class="header-actions">
        <div v-if="store.streaming" class="streaming-indicator">
          <span class="dot"></span>
          {{ store.status || "分析中..." }}
        </div>
        <button
          type="button"
          class="clear-btn"
          :class="{ invisible: !store.messages.length }"
          title="清空所有对话"
          :disabled="
            !store.messages.length || store.clearingChat || store.uploading
          "
          @click="handleClearChat"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
          >
            <polyline points="3 6 5 6 21 6" />
            <path
              d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"
            />
            <line x1="10" y1="11" x2="10" y2="17" />
            <line x1="14" y1="11" x2="14" y2="17" />
          </svg>
        </button>
      </div>
    </header>

    <div
      class="ws-toolbar"
      :class="{ dragover: isDragover }"
      @dragover.prevent="isDragover = true"
      @dragleave.prevent="isDragover = false"
      @drop.prevent="handleDrop"
    >
      <div class="ws-toolbar-inner">
        <div class="ws-materials">
          <button
            type="button"
            class="ws-upload-btn"
            :disabled="store.uploading || !store.ready"
            title="上传 Excel（.xlsx）"
            @click="triggerFileInput"
          >
            <span v-if="store.uploading" class="uploading-spinner"></span>
            <svg
              v-else
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
            >
              <path
                d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"
              />
            </svg>
            <span>{{ store.uploading ? "上传中" : "上传 Excel" }}</span>
          </button>

          <div class="ws-file-list">
            <div
              v-for="f in store.files"
              :key="f.id"
              class="ws-file-chip"
              :class="f.parse_status"
              :title="
                f.parse_error ||
                `${f.file_name} · ${f.sheet_count} 表 / ${f.row_count} 行`
              "
            >
              <span class="ws-file-name">{{ f.file_name }}</span>
              <span class="ws-file-status">{{ statusLabel(f) }}</span>
              <button
                type="button"
                class="ws-file-remove"
                :disabled="store.busy"
                title="移除"
                @click="store.removeFile(f.id)"
              >
                ×
              </button>
            </div>
            <span v-if="!store.files.length" class="ws-materials-hint">
              注意：目前仅支持一行表头的 .xlsx 文件。
            </span>
          </div>

          <input
            ref="fileInputRef"
            type="file"
            class="upload-input-hidden"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            @change="handleFileSelect"
          />
        </div>

        <div v-if="store.tables.length" class="sa-table-meta">
          <span
            v-for="t in store.tables"
            :key="t.table_id"
            class="sa-table-chip"
            :title="(t.columns || []).map((c) => c.name).join(', ')"
          >
            {{ t.sheet_name || `表${t.table_id}` }} · {{ t.row_count }} 行
          </span>
        </div>
      </div>
    </div>

    <div v-if="store.error" class="ws-error" role="alert">
      <span>{{ store.error }}</span>
      <button type="button" class="ws-error-dismiss" @click="store.error = ''">
        关闭
      </button>
    </div>

    <div class="message-area" ref="messageAreaRef">
      <div class="message-area-inner">
        <div v-if="!store.ready" class="loading-state">
          <div class="loading-spinner"></div>
          <p class="loading-text">正在打开销售分析区…</p>
        </div>

        <div v-else-if="!store.messages.length" class="welcome">
          <div class="welcome-icon">
            <svg
              width="48"
              height="48"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
            >
              <path d="M3 3v18h18" />
              <path d="M7 14l3-3 3 2 5-6" />
            </svg>
          </div>
          <h2>销售分析已就绪</h2>
          <p>
            上传一行表头的 Excel
            后，可查询数据、生成图表，并输出可下载的销售反馈报告。
          </p>
        </div>

        <MessageBubble
          v-for="(msg, i) in store.messages"
          :key="i"
          :message="msg"
          :is-streaming="
            store.streaming &&
            i === store.messages.length - 1 &&
            msg.role === 'assistant'
          "
          :status="
            store.streaming &&
            i === store.messages.length - 1 &&
            msg.role === 'assistant'
              ? store.status
              : ''
          "
          :approval-busy="store.approvalBusy"
          @decide="store.handleUserChoice"
        />
      </div>
    </div>

    <div v-if="store.ready" class="input-area">
      <div class="input-wrapper">
        <input
          v-model="store.input"
          type="text"
          class="chat-input"
          :placeholder="inputPlaceholder"
          :disabled="inputLocked"
          @keyup.enter="onEnter"
        />
        <div class="input-toolbar">
          <div class="intent-pills" aria-hidden="true"></div>
          <div class="input-actions">
            <button
              v-if="store.streaming"
              type="button"
              class="stop-btn"
              title="停止回答"
              aria-label="停止回答"
              @click="store.stopGeneration()"
            >
              <span class="stop-icon" aria-hidden="true"></span>
            </button>
            <button
              v-else
              type="button"
              class="send-btn"
              :disabled="!canSend || inputLocked"
              title="发送"
              aria-label="发送"
              @click="store.sendMessage()"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
              >
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import ChatHeaderTitle from "@/components/ChatHeaderTitle.vue";
import MessageBubble from "@/components/MessageBubble.vue";
import { useChatStore } from "@/stores/chat";
import { useSalesAnalysisStore } from "@/stores/salesAnalysis";
import { showConfirm } from "@/utils/dialog";

const MAX_BYTES = 20 * 1024 * 1024;

const chatStore = useChatStore();
const store = useSalesAnalysisStore();

const fileInputRef = ref<HTMLInputElement | null>(null);
const messageAreaRef = ref<HTMLElement | null>(null);
const isDragover = ref(false);

const inputLocked = computed(
  () =>
    store.streaming ||
    store.approvalBusy ||
    store.pendingApproval ||
    !store.ready,
);

const canSend = computed(
  () =>
    !!store.input.trim() &&
    store.hasReadyFile &&
    !store.streaming &&
    !store.approvalBusy &&
    !store.pendingApproval &&
    store.ready,
);

const inputPlaceholder = computed(() => {
  if (!store.hasReadyFile) return "请先上传并等待 Excel 解析完成…";
  if (store.approvalBusy || store.pendingApproval)
    return "请先确认或取消上方操作…";
  return "问销售数据、要图表或销售反馈…";
});

onMounted(() => {
  void store.ensureWorkspace();
});

onUnmounted(() => {
  store.stopPolling();
});

watch(
  () => [
    store.messages.length,
    store.messages[store.messages.length - 1]?.content,
    store.messages[store.messages.length - 1]?.charts?.length,
  ],
  async () => {
    await nextTick();
    const el = messageAreaRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  }
);

function statusLabel(f: {
  parse_status: string;
  sheet_count?: number;
  row_count?: number;
}) {
  if (f.parse_status === "done") {
    return `${f.sheet_count || 0}表/${f.row_count || 0}行`;
  }
  if (f.parse_status === "parsing" || f.parse_status === "pending")
    return "解析中";
  if (f.parse_status === "failed") return "失败";
  return f.parse_status;
}

function triggerFileInput() {
  fileInputRef.value?.click();
}

function isSupported(file: File) {
  return file.name.toLowerCase().endsWith(".xlsx");
}

async function takeFile(file: File | undefined) {
  if (!file || store.uploading) return;
  if (!isSupported(file)) {
    store.error = "仅支持 .xlsx（一行表头）";
    return;
  }
  if (file.size > MAX_BYTES) {
    store.error = "文件超过 20MB";
    return;
  }
  await store.upload(file);
}

function handleFileSelect(e: Event) {
  const target = e.target as HTMLInputElement;
  const file = target.files?.[0];
  target.value = "";
  void takeFile(file);
}

function handleDrop(e: DragEvent) {
  isDragover.value = false;
  void takeFile(e.dataTransfer?.files?.[0]);
}

function onEnter() {
  if (canSend.value && !inputLocked.value) void store.sendMessage();
}

async function handleClearChat() {
  if (!store.messages.length || store.clearingChat) return;
  const ok = await showConfirm(
    "确定清空销售分析全部对话吗？已上传的 Excel 数据会保留，对话记录将从数据库删除且不可恢复。",
    { title: "清空对话", confirmText: "清空" },
  );
  if (!ok) return;
  void store.clearChat();
}
</script>
