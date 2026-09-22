import { createApp } from "vue";
import { createPinia } from "pinia";
import { createRouter, createWebHistory } from "vue-router";
import App from "./App.vue";
import "./styles/workbench.css";
const router = createRouter({
  history: createWebHistory(),
  scrollBehavior: () => ({ top: 0 }),
  routes: [
    {
      path: "/",
      component: () => import("./pages/Overview.vue"),
      meta: { title: "运行总览", section: "工作空间" },
    },
    {
      path: "/calls",
      component: () => import("./pages/Calls.vue"),
      meta: { title: "调用记录", section: "工作空间" },
    },
    {
      path: "/routes",
      component: () => import("./pages/Routes.vue"),
      meta: { title: "路由编排", section: "管理" },
    },
    {
      path: "/providers",
      component: () => import("./pages/Providers.vue"),
      meta: { title: "上游服务", section: "管理" },
    },
    {
      path: "/playground",
      component: () => import("./pages/Playground.vue"),
      meta: { title: "请求实验室", section: "工作空间" },
    },
    {
      path: "/settings",
      component: () => import("./pages/Settings.vue"),
      meta: { title: "系统设置", section: "管理" },
    },
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
  document.title = String(to.meta.title) + " · Agent Router";
});
createApp(App).use(createPinia()).use(router).mount("#app");
