<template>
  <div class="auth-page">
    <div class="auth-panel">
      <div class="auth-brand">
        <div class="auth-brand-icon" aria-hidden="true">
          <BrandMark :size="22" />
        </div>
        <h1 class="auth-title">创建账号</h1>
        <p class="auth-sub">注册后即可使用对话与知识库</p>
      </div>

      <form class="auth-form" @submit.prevent="onSubmit">
        <label class="auth-field">
          <span>用户名</span>
          <input v-model.trim="username" type="text" autocomplete="username" required maxlength="32" />
        </label>
        <label class="auth-field">
          <span>邮箱（可选）</span>
          <input v-model.trim="email" type="email" autocomplete="email" maxlength="255" />
        </label>
        <label class="auth-field">
          <span>密码</span>
          <input v-model="password" type="password" autocomplete="new-password" required minlength="6" maxlength="128" />
        </label>
        <label class="auth-field">
          <span>确认密码</span>
          <input v-model="password2" type="password" autocomplete="new-password" required minlength="6" maxlength="128" />
        </label>
        <p v-if="error" class="auth-error">{{ error }}</p>
        <button class="auth-submit" type="submit" :disabled="auth.busy">
          {{ auth.busy ? '注册中…' : '注册' }}
        </button>
      </form>

      <p class="auth-footer">
        已有账号？
        <router-link to="/login">登录</router-link>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import BrandMark from '@/components/BrandMark.vue'
import { useAuthStore } from '@/stores/auth'
import { toUserError } from '@/utils/safeError'

const auth = useAuthStore()
const router = useRouter()

const username = ref('')
const email = ref('')
const password = ref('')
const password2 = ref('')
const error = ref('')

async function onSubmit() {
  error.value = ''
  if (password.value !== password2.value) {
    error.value = '两次输入的密码不一致'
    return
  }
  try {
    await auth.register(username.value, password.value, email.value || undefined)
    await router.replace('/')
  } catch (e) {
    error.value = toUserError((e as Error).message, '注册失败，请重试')
  }
}
</script>
