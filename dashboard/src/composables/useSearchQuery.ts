import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";

export function useSearchQuery() {
  const route = useRoute();
  const router = useRouter();
  return computed({
    get: () => String(route.query.q ?? ""),
    set: (value: string) => {
      void router.replace({
        query: { ...route.query, q: value || undefined },
      });
    },
  });
}
