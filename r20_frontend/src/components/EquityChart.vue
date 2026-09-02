<script setup lang="ts">
import { Chart, registerables } from 'chart.js'
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useThemeStore } from '../stores/theme'
Chart.register(...registerables)
const props = defineProps<{ snapshots:any[]; initialCapital:number }>()
const canvas = ref<HTMLCanvasElement|null>(null)
const theme = useThemeStore()
let chart: Chart | null = null
function draw(){
  nextTick(()=>{
    if(!canvas.value) return
    chart?.destroy()
    const light=theme.isLight
    const points=props.snapshots?.length?props.snapshots:[{time:new Date().toLocaleString('zh-CN',{hour12:false}),total_eq:props.initialCapital}]
    chart=new Chart(canvas.value,{type:'line',data:{labels:points.map(p=>String(p.time||'').slice(5,16)),datasets:[{label:'账户总权益',data:points.map(p=>Number(p.total_eq??p.equity??props.initialCapital)),borderColor:light?'#07875a':'#0ecb81',backgroundColor:light?'rgba(7,135,90,.08)':'rgba(14,203,129,.08)',fill:true,tension:.24,borderWidth:2,pointRadius:points.length<12?3:0},{label:'初始本金',data:points.map(()=>props.initialCapital),borderColor:light?'#64748b':'#707e94',borderDash:[5,5],borderWidth:1,pointRadius:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{backgroundColor:light?'#fff':'#0e121b',titleColor:light?'#172033':'#eaecef',bodyColor:light?'#172033':'#eaecef',borderColor:light?'#d8e0eb':'#1a2232',borderWidth:1}},scales:{x:{ticks:{color:light?'#64748b':'#707e94',maxTicksLimit:7},grid:{color:light?'rgba(15,23,42,.07)':'rgba(255,255,255,.04)'}},y:{ticks:{color:light?'#07875a':'#0ecb81',callback:v=>`${v} U`},grid:{color:light?'rgba(15,23,42,.07)':'rgba(255,255,255,.04)'}}}}})
  })
}
watch(()=>[props.snapshots,props.initialCapital,theme.theme],draw,{deep:true,immediate:true})
onBeforeUnmount(()=>chart?.destroy())
</script>
<template><div class="h-[260px] sm:h-[320px]"><canvas ref="canvas"></canvas></div></template>
