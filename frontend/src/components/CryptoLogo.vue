<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    size?: number | string
    className?: string
  }>(),
  {
    size: 24,
    className: '',
  }
)

const dimension = computed(() => {
  if (typeof props.size === 'number') {
    return `${props.size}px`
  }
  return props.size
})
</script>

<template>
  <svg
    :width="dimension"
    :height="dimension"
    viewBox="0 0 48 48"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    class="shrink-0 transition-transform duration-300 hover:scale-105 select-none"
    :class="className"
  >
    <defs>
      <!-- Quantum Neon Cyber Teal to Satoshi Gold/Amber gradient -->
      <linearGradient id="r20-logo-bg" x1="4" y1="4" x2="44" y2="44" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stop-color="#059669" stop-opacity="0.95" />
        <stop offset="50%" stop-color="#0284C7" stop-opacity="0.9" />
        <stop offset="100%" stop-color="#6366F1" stop-opacity="0.95" />
      </linearGradient>

      <!-- Token Rim Glow -->
      <linearGradient id="r20-logo-rim" x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stop-color="#34D399" stop-opacity="0.8" />
        <stop offset="100%" stop-color="#818CF8" stop-opacity="0.4" />
      </linearGradient>

      <!-- Isometric Cube Top Facet -->
      <linearGradient id="r20-facet-top" x1="24" y1="9" x2="24" y2="21" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stop-color="#FFFFFF" stop-opacity="0.9" />
        <stop offset="100%" stop-color="#E2E8F0" stop-opacity="0.75" />
      </linearGradient>

      <!-- Isometric Cube Left Facet -->
      <linearGradient id="r20-facet-left" x1="13" y1="16" x2="24" y2="34" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stop-color="#CBD5E1" stop-opacity="0.8" />
        <stop offset="100%" stop-color="#94A3B8" stop-opacity="0.6" />
      </linearGradient>

      <!-- Isometric Cube Right Facet -->
      <linearGradient id="r20-facet-right" x1="35" y1="16" x2="24" y2="34" gradientUnits="userSpaceOnUse">
        <stop offset="0%" stop-color="#E2E8F0" stop-opacity="0.85" />
        <stop offset="100%" stop-color="#64748B" stop-opacity="0.7" />
      </linearGradient>
    </defs>

    <!-- Outer Octagon / Crypto Quantum Node Shape -->
    <rect
      x="2.5"
      y="2.5"
      width="43"
      height="43"
      rx="12"
      fill="url(#r20-logo-bg)"
      stroke="url(#r20-logo-rim)"
      stroke-width="1.5"
    />

    <!-- Subtle Background Quantum Orbit Lines -->
    <circle cx="24" cy="24" r="15" stroke="rgba(255,255,255,0.18)" stroke-width="1" stroke-dasharray="2 3" />

    <!-- Central Isometric Blockchain / Quantum Calculus Diamond -->
    <!-- Top Face (Calculus Rhombus) -->
    <path
      d="M24 11L34.5 17.2L24 23.4L13.5 17.2Z"
      fill="url(#r20-facet-top)"
    />

    <!-- Left Face (Crypto Vault Wall) -->
    <path
      d="M13.5 17.2L24 23.4V35.6L13.5 29.4Z"
      fill="url(#r20-facet-left)"
    />

    <!-- Right Face (Alpha Node) -->
    <path
      d="M24 23.4L34.5 17.2V29.4L24 35.6Z"
      fill="url(#r20-facet-right)"
    />

    <!-- Stylized Crypto 'R' & '20' Quantum Core (Negative Space & Stencil) -->
    <!-- Distinctive R Shape on Front Left-Center -->
    <path
      d="M19 16.5V30M19 16.5H23C24.4 16.5 25.5 17.4 25.5 18.8C25.5 20.2 24.4 21.1 23 21.1H19M22.5 21.1L26 30"
      stroke="#0F172A"
      stroke-width="2.2"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <path
      d="M19 16.5V30M19 16.5H23C24.4 16.5 25.5 17.4 25.5 18.8C25.5 20.2 24.4 21.1 23 21.1H19M22.5 21.1L26 30"
      stroke="#FFFFFF"
      stroke-width="1.6"
      stroke-linecap="round"
      stroke-linejoin="round"
    />

    <!-- Micro Orbit Dynamic Pulse Dot (Autonomous 15m Loop / Satoshi Pulse) -->
    <circle cx="34.5" cy="17.2" r="2" fill="#34D399" />
    <circle cx="24" cy="35.6" r="1.5" fill="#38BDF8" />
  </svg>
</template>
