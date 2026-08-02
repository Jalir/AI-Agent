import { ref, watchEffect } from 'vue'

type Theme = 'light' | 'dark'

const stored = (localStorage.getItem('app-theme') as Theme) || 'light'
const theme = ref<Theme>(stored)
applyTheme(stored)

function applyTheme(t: Theme) {
  document.documentElement.setAttribute('data-theme', t)
}

export function useTheme() {
  function toggleTheme() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
    localStorage.setItem('app-theme', theme.value)
  }

  watchEffect(() => {
    applyTheme(theme.value)
  })

  return { theme, toggleTheme }
}
