<template>
  <div class="auth-page">
    <div class="auth-panel">
      <div class="auth-brand">
        <div class="auth-brand-icon" aria-hidden="true">
          <BrandMark :size="22" />
        </div>
        <h1 class="auth-title">LangGraph</h1>
        <p class="auth-sub">登录后继续使用</p>
      </div>

      <form class="auth-form" @submit.prevent="onSubmit">
        <label class="auth-field">
          <span>用户名</span>
          <input v-model.trim="username" type="text" autocomplete="username" required maxlength="32" />
        </label>
        <label class="auth-field">
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="current-password" required maxlength="128" />
        </label>
        <p v-if="error" class="auth-error">{{ error }}</p>
        <button class="auth-submit" type="submit" :disabled="auth.busy">
          {{ auth.busy ? '登录中…' : '登录' }}
        </button>
      </form>

      <p class="auth-footer">
        还没有账号？
        <router-link to="/register">注册</router-link>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BrandMark from '@/components/BrandMark.vue'
import { useAuthStore } from '@/stores/auth'
import { toUserError } from '@/utils/safeError'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const error = ref('')

async function onSubmit() {
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect || '/')
  } catch (e) {
    error.value = toUserError((e as Error).message, '登录失败，请重试')
  }
}
</script>
