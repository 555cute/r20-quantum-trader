<script setup lang="ts">
import { ref } from 'vue'
import {
  Code,
  BookOpen,
  Layers,
  ShieldCheck,
  Cpu,
  Sparkles,
  ExternalLink,
  Copy,
  Check,
  X,
} from 'lucide-vue-next'

defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const copiedTarget = ref<string | null>(null)

async function copyToClipboard(text: string, targetName: string) {
  try {
    await navigator.clipboard.writeText(text)
    copiedTarget.value = targetName
    setTimeout(() => {
      if (copiedTarget.value === targetName) {
        copiedTarget.value = null
      }
    }, 2000)
  } catch (err) {
    console.error('Copy failed:', err)
  }
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="visible"
      class="fixed inset-0 z-[9999] flex items-center justify-center p-3 sm:p-4 bg-black/60 backdrop-blur-md transition-all animate-fade-in overflow-y-auto"
      @click.self="emit('close')"
    >
      <div
        class="border rounded-2xl w-full max-w-xl shadow-2xl p-5 sm:p-6 space-y-4 font-mono text-xs animate-scale-up my-auto max-h-[88dvh] overflow-y-auto"
        style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
      >
        <!-- Modal Header -->
        <div class="flex items-center justify-between pb-3 border-b" style="border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2.5">
            <div
              class="w-7 h-7 rounded-lg flex items-center justify-center font-bold border"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-medium); color: var(--text-main);"
            >
              <Code class="w-4 h-4" />
            </div>
            <div>
              <h3 class="text-sm font-bold uppercase tracking-wide flex items-center gap-2" style="color: var(--text-main);">
                <span>R20 Quantum Trader</span>
                <span
                  class="px-1.5 py-0.2 rounded text-[10px] font-mono font-bold border"
                  style="background-color: var(--color-brand-bg); color: var(--color-brand); border-color: var(--color-brand-border);"
                >
                  v7.4.1
                </span>
              </h3>
            </div>
          </div>
          <button
            @click="emit('close')"
            class="w-7 h-7 rounded-lg border transition-colors cursor-pointer flex items-center justify-center"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-faint);"
            title="关闭"
          >
            <X class="w-4 h-4" />
          </button>
        </div>

        <!-- Description -->
        <p class="text-xs font-sans leading-relaxed" style="color: var(--text-muted);">
          面向 OKX 永续合约的 LLM 原生高频量化交易系统。集成高阶微积分物理动能推演、多模型委员会协同决策、100% 交易所云端 OCO 止盈止损防线、智能 Maker 挂单与每日 20:00 闭环自进化认知复盘。
        </p>

        <!-- Links Grid -->
        <div class="space-y-2">
          <!-- System Documentation Link -->
          <a
            href="/docs"
            class="p-3 rounded-xl border flex items-center justify-between transition-all group cursor-pointer"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
            @click="emit('close')"
          >
            <div class="flex items-center space-x-2.5 min-w-0">
              <div
                class="w-6 h-6 rounded-lg border flex items-center justify-center shrink-0"
                style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
              >
                <BookOpen class="w-3.5 h-3.5" />
              </div>
              <div class="truncate">
                <span class="font-bold block truncate" style="color: var(--text-main);">
                  系统开发与使用文档 (Docs)
                </span>
                <span class="text-[10px] truncate block" style="color: var(--text-faint);">
                  架构说明 · 提示词变量插槽 · 物理拦截插件规范
                </span>
              </div>
            </div>
            <div class="flex items-center space-x-1 shrink-0 font-medium" style="color: var(--color-brand);">
              <span class="text-[11px]">查看文档</span>
              <ExternalLink class="w-3 h-3" />
            </div>
          </a>

          <!-- GitHub Repo Link -->
          <a
            href="https://github.com/555cute/r20-quantum-trader"
            target="_blank"
            rel="noopener noreferrer"
            class="p-3 rounded-xl border flex items-center justify-between transition-all group cursor-pointer"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
          >
            <div class="flex items-center space-x-2.5 min-w-0">
              <div
                class="w-6 h-6 rounded-lg border flex items-center justify-center shrink-0"
                style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
              >
                <Layers class="w-3.5 h-3.5" />
              </div>
              <div class="truncate">
                <span class="font-bold block truncate" style="color: var(--text-main);">
                  GitHub 官方开源主仓
                </span>
                <span class="text-[10px] truncate block" style="color: var(--text-faint);">
                  555cute/r20-quantum-trader (MIT License)
                </span>
              </div>
            </div>
            <div class="flex items-center space-x-1 shrink-0 font-medium" style="color: var(--color-brand);">
              <span class="text-[11px]">访问仓库</span>
              <ExternalLink class="w-3 h-3" />
            </div>
          </a>
        </div>

        <!-- Quick Commands -->
        <div class="space-y-2 pt-2 border-t" style="border-color: var(--border-subtle);">
          <div class="text-[11px] font-bold uppercase" style="color: var(--text-faint);">
            常用运维命令
          </div>

          <div class="space-y-1.5">
            <div
              class="flex items-center justify-between p-2 rounded-lg border"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
            >
              <span class="truncate pr-2 font-mono text-[11px]" style="color: var(--text-muted);">
                cd frontend && npm run build
              </span>
              <button
                @click="copyToClipboard('cd frontend && npm run build', 'build')"
                class="px-2 py-1 rounded border text-[10px] font-mono cursor-pointer transition-colors shrink-0 flex items-center space-x-1"
                style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
              >
                <Check v-if="copiedTarget === 'build'" class="w-3 h-3 text-emerald-500" />
                <Copy v-else class="w-3 h-3" />
                <span>{{ copiedTarget === 'build' ? '已复制' : '复制' }}</span>
              </button>
            </div>

            <div
              class="flex items-center justify-between p-2 rounded-lg border"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"
            >
              <span class="truncate pr-2 font-mono text-[11px]" style="color: var(--text-muted);">
                /app/venv/bin/python3 -m unittest discover -s tests
              </span>
              <button
                @click="copyToClipboard('/app/venv/bin/python3 -m unittest discover -s tests -p &quot;test_*.py&quot;', 'test')"
                class="px-2 py-1 rounded border text-[10px] font-mono cursor-pointer transition-colors shrink-0 flex items-center space-x-1"
                style="background-color: var(--bg-card); border-color: var(--border-subtle); color: var(--text-main);"
              >
                <Check v-if="copiedTarget === 'test'" class="w-3 h-3 text-emerald-500" />
                <Copy v-else class="w-3 h-3" />
                <span>{{ copiedTarget === 'test' ? '已复制' : '复制' }}</span>
              </button>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div class="pt-2 text-center text-[10px]" style="color: var(--text-faint);">
          R20 QUANTUM TRADER · ENTERPRISE QUANTITATIVE FRAMEWORK
        </div>
      </div>
    </div>
  </Teleport>
</template>
