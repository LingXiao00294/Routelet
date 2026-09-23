import { createApp } from "vue";
import { createPinia } from "pinia";
import { createRouter, createWebHistory } from "vue-router";
import { navigation } from "./domain/navigation";
import App from "./App.vue";
import "./styles/workbench.css";
import "./styles/cyberpunk.css";
const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    ...navigation.map(({ path, component, label, group }) => ({
      path,
      component,
      meta: { title: label, section: group },
    })),
    {
      path: "/config/:section?",
      redirect: (to) => ({
        path:
          to.params.section === "providers"
            ? "/providers"
            : to.params.section === "models"
              ? "/routes"
              : "/settings",
      }),
    },
    {
      path: "/:pathMatch(.*)*",
      component: () => import("./pages/NotFound.vue"),
      meta: { title: "页面不存在", section: "工作空间" },
    },
  ],
});
router.afterEach((to) => {
  document.title = String(to.meta.title) + " · Routelet";
});
createApp(App).use(createPinia()).use(router).mount("#app");
