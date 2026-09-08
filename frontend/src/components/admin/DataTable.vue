<script setup lang="ts" generic="T extends Record<string, any>">
/** P2 shared: one table behavior for ledger/decisions/audit.
 *  - sticky header, dense 30px rows, hairline separators (no zebra)
 *  - numeric columns right-aligned + tabular-nums (column.align = 'right')
 *  - row actions via #actions slot — keep <= 2 inline, rest goes to a detail drawer
 *  - first column is the human-readable identity */
import { useSlots } from 'vue'

defineProps<{
  columns: Array<{ key: string; label: string; align?: 'left' | 'right' | 'center'; width?: string; mono?: boolean }>
  rows: T[]
  rowKey?: (row: T, i: number) => string | number
  emptyText?: string
}>()

defineSlots<{
  [name: `cell-${string}`]: (props: { row: any; value: any }) => any
  actions?: (props: { row: any }) => any
}>()

const slots = useSlots()
const has = (name: string) => !!slots[name]
</script>

<template>
  <div class="overflow-x-auto rounded-xl border" style="border-color: var(--border-subtle); background-color: var(--bg-card);">
    <table class="w-full text-xs font-mono border-collapse">
      <thead>
        <tr class="sticky top-0 z-10" style="background-color: var(--bg-card);">
          <th
            v-for="col in columns"
            :key="col.key"
            class="px-3 py-2 text-[11px] font-bold uppercase tracking-wider border-b whitespace-nowrap"
            :style="{ color: 'var(--text-faint)', borderColor: 'var(--border-subtle)', textAlign: col.align || 'left', width: col.width || 'auto' }"
          >
            {{ col.label }}
          </th>
          <th v-if="has('actions')" class="px-3 py-2 text-[11px] font-bold uppercase tracking-wider border-b text-right" style="color: var(--text-faint); border-color: var(--border-subtle);">
            ·
          </th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="(row, i) in rows"
          :key="rowKey ? rowKey(row, i) : i"
          class="transition-colors hover:bg-[var(--bg-card-hover)]"
          style="border-bottom: 1px solid var(--border-subtle);"
        >
          <td
            v-for="col in columns"
            :key="col.key"
            class="px-3 py-[7px] whitespace-nowrap"
            :class="col.align === 'right' || col.mono ? 'num-tabular' : ''"
            :style="{ textAlign: col.align || 'left', color: 'var(--text-main)' }"
          >
            <slot :name="`cell-${col.key}`" :row="row" :value="row[col.key]">
              {{ row[col.key] ?? '--' }}
            </slot>
          </td>
          <td v-if="has('actions')" class="px-3 py-[7px] text-right whitespace-nowrap">
            <div class="inline-flex items-center gap-2">
              <slot name="actions" :row="row" />
            </div>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + (has('actions') ? 1 : 0)" class="px-3 py-10 text-center" style="color: var(--text-faint);">
            {{ emptyText || '—' }}
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
