<script setup lang="ts">
import { onMounted, onUnmounted, ref, useId } from "vue";
import Icon from "./Icon.vue";
const props = withDefaults(
  defineProps<{
    title: string;
    eyebrow?: string;
    wide?: boolean;
    busy?: boolean;
    guard?: boolean;
  }>(),
  { eyebrow: "" },
);
const emit = defineEmits<{ close: [] }>();
const element = ref<HTMLDialogElement>();
const titleId = useId();
const previousOverflow = document.body.style.overflow;
const previous = document.activeElement as HTMLElement | null;
function close() {
  if (props.busy) return;
  if (props.guard && !window.confirm("放弃此窗口中尚未应用的编辑？")) return;
  emit("close");
}
onMounted(() => {
  document.body.style.overflow = "hidden";
  element.value?.showModal();
});
onUnmounted(() => {
  element.value?.close();
  document.body.style.overflow = previousOverflow;
  if (previous?.isConnected) previous.focus();
});
</script>
<template>
  <dialog
    ref="element"
    class="modal"
    :class="{ wide }"
    :aria-labelledby="titleId"
    @cancel.prevent="close"
    @click="
      (event) => {
        if (event.target === element) close();
      }
    "
  >
    <div class="modal-inner">
      <header class="modal-head">
        <div>
          <div v-if="eyebrow" class="eyebrow">{{ eyebrow }}</div>
          <h2 :id="titleId">{{ title }}</h2>
        </div>
        <button
          class="icon-button"
          aria-label="关闭窗口"
          :disabled="busy"
          @click="close"
        >
          <Icon name="close" />
        </button>
      </header>
      <div class="modal-body"><slot /></div>
      <footer v-if="$slots.footer" class="modal-foot">
        <slot name="footer" />
      </footer>
    </div>
  </dialog>
</template>
