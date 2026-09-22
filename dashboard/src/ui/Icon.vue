<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 });
const paths: Record<string, string> = {
  overview: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
  activity: "M2 12h4l3-8 6 16 3-8h4",
  routes:
    "M5 4v12a4 4 0 0 0 4 4h10 M5 9h10a4 4 0 0 0 4-4V4 M16 17l3 3-3 3 M16 7l3-3 3 3",
  providers: "M4 3h16v6H4z M4 15h16v6H4z M8 6h.01 M8 18h.01 M12 9v6",
  lab: "M9 3h6 M10 3v6L4 19a1.4 1.4 0 0 0 1 2h14a1.4 1.4 0 0 0 1-2L14 9V3 M7 15h10",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z",
  search: "M21 21l-5-5 M10.5 3a7.5 7.5 0 1 0 0 15 7.5 7.5 0 0 0 0-15",
  arrow: "M5 12h14 M14 7l5 5-5 5",
  arrowup: "M7 17 17 7 M7 7h10v10",
  chevron: "m9 5 7 7-7 7",
  down: "m6 9 6 6 6-6",
  up: "m6 15 6-6 6 6",
  plus: "M12 5v14 M5 12h14",
  close: "m6 6 12 12 M6 18 18 6",
  check: "m5 12 4 4L19 6",
  refresh: "M20 7a8 8 0 1 0 1 8 M20 3v5h-5",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l3 2",
  coin: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M15 8h-4a2 2 0 0 0 0 4h2a2 2 0 0 1 0 4H9 M12 6v12",
  token: "m12 2 9 5v10l-9 5-9-5V7z M3 7l9 5 9-5 M12 12v10",
  shield: "m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z m-4 9 3 3 5-6",
  copy: "M9 9h12v12H9z M15 5V3H3v12h2",
  edit: "m15 4 5 5 M4 20l5-1L21 7l-4-4L5 15z",
  trash: "M3 6h18 M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7",
  key: "M7 3a5 5 0 1 0 0 10 5 5 0 0 0 0-10 M11 11l10 10 M15 15l3-3 M18 18l3-3",
  pin: "m8 3 8 0-1 6 4 4v2H5v-2l4-4z M12 15v7",
  terminal: "m5 7 5 5-5 5 M13 17h6",
  moon: "M20 14a8.5 8.5 0 0 1-10-10A9 9 0 1 0 20 14z",
  sun: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1 1 M18 18l1 1 M5 19l1-1 M18 6l1-1",
  menu: "M4 6h16 M4 12h16 M4 18h16",
  warning: "m12 3 10 18H2z M12 9v5 M12 17h.01",
  download: "M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5",
  play: "m7 4 14 8-14 8z",
  stop: "M6 6h12v12H6z",
  link: "m9 15 6-6 M7 14l-2 2a3 3 0 0 0 4 4l4-4 M11 8l4-4a3 3 0 0 1 4 4l-2 2",
  book: "M12 5v16 M3 3c4-1 6 0 9 2 3-2 5-3 9-2v16c-4-1-6 0-9 2-3-2-5-3-9-2z",
  dots: "M5 12h.01 M12 12h.01 M19 12h.01",
};
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] ?? paths.token" />
  </svg>
</template>
