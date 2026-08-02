import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

export type DialogMode = 'alert' | 'confirm'

export interface DialogOptions {
  title?: string
  confirmText?: string
  cancelText?: string
  /** 确认按钮用危险色（删除类操作） */
  danger?: boolean
}

interface DialogRequest extends Required<DialogOptions> {
  mode: DialogMode
  message: string
}

let seq = 0

export const useDialogStore = defineStore('dialog', () => {
  const queue = ref<Array<DialogRequest & { id: number; resolve: (v: boolean) => void }>>([])

  const current = computed(() => queue.value[0] || null)
  const visible = computed(() => !!current.value)

  function _enqueue(req: DialogRequest): Promise<boolean> {
    return new Promise<boolean>((resolve) => {
      queue.value = [
        ...queue.value,
        {
          id: ++seq,
          ...req,
          resolve,
        },
      ]
    })
  }

  function _settle(result: boolean) {
    const head = queue.value[0]
    if (!head) return
    head.resolve(result)
    queue.value = queue.value.slice(1)
  }

  function alert(message: string, options: DialogOptions = {}): Promise<void> {
    return _enqueue({
      mode: 'alert',
      message,
      title: options.title || '提示',
      confirmText: options.confirmText || '知道了',
      cancelText: options.cancelText || '取消',
      danger: !!options.danger,
    }).then(() => undefined)
  }

  function confirm(message: string, options: DialogOptions = {}): Promise<boolean> {
    return _enqueue({
      mode: 'confirm',
      message,
      title: options.title || '请确认',
      confirmText: options.confirmText || '确定',
      cancelText: options.cancelText || '取消',
      danger: options.danger ?? true,
    })
  }

  function onConfirm() {
    _settle(true)
  }

  function onCancel() {
    const head = queue.value[0]
    if (!head) return
    _settle(head.mode === 'alert')
  }

  return {
    visible,
    current,
    alert,
    confirm,
    onConfirm,
    onCancel,
  }
})
