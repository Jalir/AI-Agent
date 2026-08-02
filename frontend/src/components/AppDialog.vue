<template>
  <Teleport to="body">
    <Transition name="app-dialog">
      <div
        v-if="dialog.visible && dialog.current"
        class="app-dialog-overlay"
        @click.self="onOverlay"
      >
        <div
          class="app-dialog-card"
          role="dialog"
          aria-modal="true"
          :aria-labelledby="titleId"
          :aria-describedby="descId"
        >
          <div class="app-dialog-icon" :class="iconClass" aria-hidden="true">
            <svg
              v-if="dialog.current.mode === 'confirm' && dialog.current.danger"
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            <svg
              v-else-if="dialog.current.mode === 'confirm'"
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            <svg
              v-else
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="16" x2="12" y2="12" />
              <line x1="12" y1="8" x2="12.01" y2="8" />
            </svg>
          </div>

          <h3 :id="titleId" class="app-dialog-title">{{ dialog.current.title }}</h3>
          <p :id="descId" class="app-dialog-message">{{ dialog.current.message }}</p>

          <div class="app-dialog-actions">
            <button
              v-if="dialog.current.mode === 'confirm'"
              type="button"
              class="app-dialog-btn app-dialog-btn-ghost"
              @click="dialog.onCancel()"
            >
              {{ dialog.current.cancelText }}
            </button>
            <button
              ref="confirmBtnRef"
              type="button"
              class="app-dialog-btn"
              :class="dialog.current.danger && dialog.current.mode === 'confirm'
                ? 'app-dialog-btn-danger'
                : 'app-dialog-btn-primary'"
              @click="dialog.onConfirm()"
            >
              {{ dialog.current.confirmText }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useDialogStore } from '@/stores/dialog'

const dialog = useDialogStore()
const confirmBtnRef = ref<HTMLButtonElement | null>(null)
const titleId = 'app-dialog-title'
const descId = 'app-dialog-desc'

const iconClass = computed(() => {
  const cur = dialog.current
  if (!cur) return ''
  if (cur.mode === 'confirm' && cur.danger) return 'is-danger'
  if (cur.mode === 'confirm') return 'is-question'
  return 'is-info'
})

function onOverlay() {
  if (dialog.current?.mode === 'alert') {
    dialog.onCancel()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (!dialog.visible) return
  if (e.key === 'Escape') {
    e.preventDefault()
    dialog.onCancel()
  } else if (e.key === 'Enter' && dialog.current?.mode === 'alert') {
    e.preventDefault()
    dialog.onConfirm()
  }
}

watch(
  () => dialog.visible,
  async (open) => {
    if (open) {
      window.addEventListener('keydown', onKeydown)
      await nextTick()
      confirmBtnRef.value?.focus()
    } else {
      window.removeEventListener('keydown', onKeydown)
    }
  },
)

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>
