import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './assets/css/variables.css'
import './assets/css/base.css'
import './assets/css/components.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
