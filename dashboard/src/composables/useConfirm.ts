import { inject, provide, ref, type InjectionKey, type Ref } from "vue";

interface ConfirmOptions {
  title: string;
  message: string;
  confirmText?: string;
  danger?: boolean;
}

interface ConfirmState extends ConfirmOptions {
  resolve: (ok: boolean) => void;
}

interface ConfirmApi {
  state: Ref<ConfirmState | null>;
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
  answer: (ok: boolean) => void;
}

const KEY: InjectionKey<ConfirmApi> = Symbol("confirm");

export function createConfirmController(): ConfirmApi {
  const state = ref<ConfirmState | null>(null);

  function answer(ok: boolean) {
    const current = state.value;
    if (!current) return;
    state.value = null;
    current.resolve(ok);
  }

  function confirm(opts: ConfirmOptions): Promise<boolean> {
    answer(false);
    return new Promise((resolve) => {
      state.value = { ...opts, resolve };
    });
  }

  return { state, confirm, answer };
}

export function provideConfirm(): ConfirmApi {
  const api = createConfirmController();
  provide(KEY, api);
  return api;
}

export function useConfirm(): ConfirmApi {
  const api = inject(KEY);
  if (!api) throw new Error("Confirm not provided");
  return api;
}
