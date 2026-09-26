import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import './styles/index.css'
import App from './App.vue'

const app = createApp(App)

app.config.errorHandler = (err, _instance, info) => {
  console.error('[AstraQuant Global Error]', err, info)
}

if (typeof window !== 'undefined') {
  window.addEventListener('unhandledrejection', (event) => {
    console.error('[AstraQuant Unhandled Rejection]', event.reason)
  })
}

app.use(createPinia())
app.use(router)
app.mount('#app')
