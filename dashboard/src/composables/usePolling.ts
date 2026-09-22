import { onMounted, onUnmounted } from "vue";
export function usePolling(
  action: () => Promise<unknown>,
  enabled: () => boolean,
  interval = 15000,
) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  const tick = async () => {
    try {
      if (enabled() && !document.hidden) await action();
    } finally {
      if (!stopped) timer = setTimeout(tick, interval);
    }
  };
  onMounted(tick);
  onUnmounted(() => {
    stopped = true;
    clearTimeout(timer);
  });
}
