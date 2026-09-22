import { ref } from "vue";
export interface Notice {
  id: number;
  message: string;
  tone: "success" | "error" | "info";
}
const notices = ref<Notice[]>([]);
let sequence = 0;
export function notify(message: string, tone: Notice["tone"] = "success") {
  const id = ++sequence;
  notices.value.push({ id, message, tone });
  setTimeout(() => dismiss(id), tone === "error" ? 12000 : 5500);
}
export function dismiss(id: number) {
  notices.value = notices.value.filter((item) => item.id !== id);
}
export { notices };
export async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    notify("已复制到剪贴板");
  } catch {
    notify("复制失败，请手动选择文本复制", "error");
  }
}
