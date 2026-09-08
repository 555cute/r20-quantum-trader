import { createApp } from 'vue'
import { createPinia } from 'pinia'
import router from './router'
import './style.css' // 迁移期兼容层：旧后台页面退役后删除
import './styles/index.css'
import App from './App.vue'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
