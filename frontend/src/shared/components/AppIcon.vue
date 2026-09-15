<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 })

const paths: Record<string, string[]> = {
  book: ['M4 4h6a3 3 0 0 1 3 3v14a4 4 0 0 0-4-2H4z', 'M13 7a3 3 0 0 1 3-3h4v15h-3a4 4 0 0 0-4 2'],
  library: ['M4 5v15M8 4v16M12 5v15M16 4l4 15', 'M3 8h6M11 8h3M17 8l3-.7'],
  chat: ['M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8z', 'M8 10h8M8 14h5'],
  file: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z', 'M14 2v6h6M8 13h8M8 17h5'],
  plus: ['M12 5v14M5 12h14'],
  upload: ['M12 16V4M7 9l5-5 5 5', 'M4 16v4h16v-4'],
  search: ['M21 21l-5-5', 'M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0'],
  chevron: ['m9 5 7 7-7 7'],
  down: ['m6 9 6 6 6-6'],
  arrow: ['M4 12h16m-6-6 6 6-6 6'],
  up: ['M12 19V5m-6 6 6-6 6 6'],
  close: ['m6 6 12 12M6 18 18 6'],
  check: ['m5 12 4 4L19 6'],
  clock: ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0', 'M12 7v5l3 2'],
  info: ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0', 'M12 11v5M12 8h.01'],
  alert: ['m12 3 10 18H2z', 'M12 9v4M12 17h.01'],
  refresh: ['M20 7v5h-5M4 17v-5h5', 'M6.1 6a8 8 0 0 1 13 3M4.9 15a8 8 0 0 0 13 3'],
  external: ['M15 3h6v6M10 14 21 3', 'M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5'],
  trash: ['M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7'],
  edit: ['m16 3 5 5-12 12-6 1 1-6zM13 6l5 5'],
  link: ['M10 13a5 5 0 0 0 7 .5l4-4a5 5 0 0 0-7-7l-2 2', 'M14 11a5 5 0 0 0-7-.5l-4 4a5 5 0 0 0 7 7l2-2'],
  spark: ['m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7z'],
  copy: ['M8 8h13v13H8z', 'M16 8V3H3v13h5'],
  arrowLeft: ['M20 12H4m6-6-6 6 6 6'],
  filter: ['M4 5h16M7 12h10M10 19h4'],
  quote: ['M10 5H4v7h5c0 4-2 6-5 7M21 5h-6v7h5c0 4-2 6-5 7'],
}
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="app-icon">
    <path v-for="(path, index) in paths[name] ?? paths.file" :key="index" :d="path" />
  </svg>
</template>
