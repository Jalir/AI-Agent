import { useDialogStore, type DialogOptions } from '@/stores/dialog'

/** 信息提示（白色卡片） */
export function showAlert(message: string, options?: DialogOptions): Promise<void> {
  return useDialogStore().alert(message, options)
}

/** 确认对话框；确定返回 true，取消返回 false */
export function showConfirm(message: string, options?: DialogOptions): Promise<boolean> {
  return useDialogStore().confirm(message, options)
}
