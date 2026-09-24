import { test, expect } from "@playwright/test";
import { installApi } from "./fixtures";

test("overview, time scope, command palette and theme", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await installApi(page);
  await page.goto("/");
  await expect(page.locator(".stat-value").first()).toContainText("24,861");
  await expect(page.getByText("服务已连接", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "7 天", exact: true }).click();
  await expect(page.locator(".traffic-panel")).toContainText("最近 7 天");
  await expect(page.locator(".stat-value").first()).toContainText("24,861");
  await page.getByRole("button", { name: "查看完整用量", exact: true }).click();
  const usage = page.getByRole("dialog");
  await expect(usage.locator("tbody tr")).toHaveCount(3);
  await usage.getByRole("button", { name: "实际模型", exact: true }).click();
  await usage.getByLabel("筛选用量模型").fill("model-a");
  await expect(usage.locator("tbody tr")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await page.screenshot({
    path: "test-results/overview-desktop.png",
    fullPage: true,
  });
  await page.keyboard.press("Control+k");
  await page
    .getByRole("textbox", { name: "搜索页面、Router 或 Provider" })
    .fill("coding-assistant");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/routes\?q=coding-assistant/);
  await expect(page.locator(".route-card")).toHaveCount(1);
  await page.getByRole("button", { name: "切换深色模式" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({
    path: "test-results/routes-dark.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("provider and router search follows the URL and browser history", async ({
  page,
}) => {
  await installApi(page);
  await page.goto("/providers?view=catalog&q=ProviderA");
  const providerSearch = page.getByRole("textbox", {
    name: "搜索 Provider 或模型",
  });
  await expect(providerSearch).toHaveValue("ProviderA");
  await providerSearch.fill("ProviderB");
  await expect(page).toHaveURL(/providers\?view=catalog&q=ProviderB/);
  await expect(page.locator(".provider-card")).toHaveCount(1);

  await page.getByRole("link", { name: /^Routers/ }).click();
  const routerSearch = page.getByRole("textbox", { name: "搜索 Router" });
  await routerSearch.fill("fast-response");
  await expect(page).toHaveURL(/routes\?q=fast-response/);
  await expect(page.locator(".route-card")).toHaveCount(1);

  await page.goBack();
  await expect(providerSearch).toHaveValue("ProviderB");
  await page.goForward();
  await expect(routerSearch).toHaveValue("fast-response");
});

test("call pagination, structured filtering, inspector and CSV download", async ({
  page,
}) => {
  await installApi(page);
  await page.goto("/calls");
  await expect(page.locator("tbody tr")).toHaveCount(20);
  await page.getByRole("button", { name: "下一页" }).click();
  await expect(page).toHaveURL(/page=2/);
  await page.getByLabel("请求状态").selectOption("error");
  await expect(page).not.toHaveURL(/page=2/);
  await expect(page.locator("tbody tr")).toHaveCount(5);
  await page
    .getByRole("button", { name: /^查看调用 / })
    .first()
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("timeout");
  await expect(dialog).toContainText("$0.0000");
  await dialog.getByRole("button", { name: "请求正文", exact: true }).click();
  await expect(dialog).toContainText("这是用于验证界面的模拟请求");
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await page.getByLabel("Provider", { exact: true }).selectOption("ProviderB");
  await page
    .getByLabel("实际模型", { exact: true })
    .selectOption("model-b-pro");
  await expect(page).toHaveURL(/provider=ProviderB/);
  await expect(page).toHaveURL(/provider_model=model-b-pro/);
  await page
    .getByRole("button", { name: /清除筛选/ })
    .first()
    .click();
  await expect(page.locator("tbody tr")).toHaveCount(20);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出当前页" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("routelet-calls-page-1.csv");
});

test("empty workspace onboarding through provider, catalog, route and publish", async ({
  page,
}) => {
  const state = await installApi(page, true);
  await page.goto("/");
  await page.getByRole("link", { name: /连接第一个 Provider/ }).click();
  await page
    .getByRole("button", { name: "添加 Provider", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel(/Provider 名称/).fill("test-provider");
  await dialog.getByLabel(/Base URL/).fill("https://api.example.test");
  await dialog.getByLabel(/API Key/).fill("test-only-secret");
  await dialog.getByRole("button", { name: "应用到草稿" }).click();
  await expect(page.locator(".draft-bar")).toContainText("1 项");
  await page.getByRole("button", { name: "添加模型", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel(/实际模型名称/).fill("test/model");
  await dialog.getByLabel("输入", { exact: true }).fill("0");
  await dialog.getByLabel("输出", { exact: true }).fill("3.5");
  await dialog.getByRole("button", { name: "应用到草稿" }).click();
  await page.getByRole("link", { name: /^Routers/ }).click();
  await page
    .getByRole("button", { name: "新建 Router", exact: true })
    .first()
    .click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel(/Router 名称/).fill("first-route");
  await dialog.getByLabel("选择候选模型").selectOption({ label: "test/model" });
  await dialog.getByRole("button", { name: "添加", exact: true }).click();
  await dialog.getByRole("button", { name: "应用到草稿" }).click();
  await expect(page.locator(".draft-bar")).toContainText("2 项");
  await page.getByRole("button", { name: "检查并发布" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.locator(".draft-bar")).toHaveCount(0);
  expect(state.writes).toHaveLength(1);
  expect(
    state.writes[0].providers["test-provider"].models["test/model"]
      .input_price_per_million,
  ).toBe(0);
  expect(state.writes[0].models["first-route"].pinned_model).toEqual({
    provider: "test-provider",
    model: "test/model",
  });
  await page.getByRole("link", { name: "请求实验室", exact: true }).click();
  await expect(page.getByLabel("Router", { exact: true })).toHaveValue(
    "first-route",
  );
});

test("routing switch persists in both directions and supports keyboard", async ({
  page,
}) => {
  const state = await installApi(page);
  await page.goto("/routes");
  const toggle = page.getByRole("switch", {
    name: "自动故障转移",
    exact: true,
  });
  await expect(toggle).toBeChecked();
  await toggle.focus();
  await page.keyboard.press("Space");
  await expect(toggle).not.toBeChecked();
  await expect(page.locator("#routing-description")).toContainText(
    "报错直接返回",
  );
  expect(state.writes).toHaveLength(0);
  await page.getByRole("button", { name: "检查并发布" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.locator(".draft-bar")).toHaveCount(0);
  expect(state.writes[0].router.mode).toBe("sticky");
  for (const model of Object.values(state.writes[0].models))
    expect(model.models).toContainEqual(model.pinned_model);
  await page.reload();
  await expect(toggle).not.toBeChecked();
  await page.screenshot({
    path: "test-results/routing-disabled.png",
    fullPage: true,
  });
  await toggle.click();
  await page.getByRole("button", { name: "检查并发布" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.locator(".draft-bar")).toHaveCount(0);
  expect(state.writes[1].router.mode).toBe("failover");
  await page.reload();
  await expect(toggle).toBeChecked();
});

test("route reorder and pin survive publication", async ({ page }) => {
  const state = await installApi(page);
  await page.goto("/routes");
  await page.getByRole("switch", { name: "自动故障转移", exact: true }).click();
  await page
    .locator(".route-card")
    .first()
    .getByRole("button", { name: "编辑 Router" })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog
    .getByRole("button", { name: "下移 model-a-pro", exact: true })
    .click();
  await dialog
    .getByRole("button", { name: "固定 ProviderB/model-b-pro", exact: true })
    .click();
  await dialog.getByRole("button", { name: "应用到草稿" }).click();
  await page.getByRole("button", { name: "检查并发布" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.locator(".draft-bar")).toHaveCount(0);
  expect(state.writes[0].router.mode).toBe("sticky");
  expect(state.writes[0].models["coding-assistant"].models[0].provider).toBe(
    "ProviderB",
  );
  expect(
    state.writes[0].models["coding-assistant"].pinned_model?.provider,
  ).toBe("ProviderB");
});

test("route dragging previews the destination, animates and persists the order", async ({
  page,
}) => {
  const state = await installApi(page);
  await page.goto("/routes");
  await page.getByRole("button", { name: "切换深色模式" }).click();
  await page
    .locator(".route-card")
    .first()
    .getByRole("button", { name: "编辑 Router" })
    .click();
  const dialog = page.getByRole("dialog");
  const rows = dialog.locator(".route-editor-row");
  const names = rows.locator(".route-editor-name strong");
  const original = await names.allTextContents();
  const handle = await rows.first().locator(".route-drag-handle").boundingBox();
  const last = await rows.last().boundingBox();
  if (!handle || !last) throw new Error("Missing drag geometry");
  await page.mouse.move(
    handle.x + handle.width / 2,
    handle.y + handle.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(handle.x + 90, last.y + last.height - 8, { steps: 12 });
  await expect(dialog.locator(".route-drop-indicator")).toHaveText(
    "放到第 3 位",
  );
  await expect(dialog.locator(".route-drag-preview")).toContainText(
    original[0],
  );
  await expect(names).toHaveText(original);
  await page.screenshot({
    path: "test-results/route-drag-preview.png",
    fullPage: true,
  });
  await page.mouse.up();
  await expect(dialog.locator(".route-sort-move").first()).toBeAttached();
  await expect(names).toHaveText([original[1], original[2], original[0]]);
  await expect(dialog.locator(".route-drag-preview")).toHaveCount(0);
  await expect(rows.last()).toHaveClass(/just-moved/);
  await expect(
    rows.last().getByRole("button", { name: /^固定 / }),
  ).toHaveAttribute("aria-pressed", "true");
  await dialog.getByRole("button", { name: "应用到草稿" }).click();
  await page.getByRole("button", { name: "检查并发布" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.locator(".draft-bar")).toHaveCount(0);
  expect(
    state.writes[0].models["coding-assistant"].models.map((ref) => ref.model),
  ).toEqual([original[1], original[2], original[0]]);
  expect(state.writes[0].models["coding-assistant"].pinned_model?.model).toBe(
    original[0],
  );
});

test("route dragging cancels safely and supports moving upward and keyboard", async ({
  page,
}) => {
  await installApi(page);
  await page.goto("/routes");
  await page
    .locator(".route-card")
    .first()
    .getByRole("button", { name: "编辑 Router" })
    .click();
  const dialog = page.getByRole("dialog");
  const rows = dialog.locator(".route-editor-row");
  const names = rows.locator(".route-editor-name strong");
  const original = await names.allTextContents();
  const handle = await rows.last().locator(".route-drag-handle").boundingBox();
  const first = await rows.first().boundingBox();
  if (!handle || !first) throw new Error("Missing drag geometry");
  const start = async () => {
    await page.mouse.move(handle.x + 12, handle.y + 12);
    await page.mouse.down();
    await page.mouse.move(handle.x + 60, first.y + 5, { steps: 8 });
  };
  await start();
  await expect(dialog.locator(".route-drop-indicator")).toHaveText(
    "放到第 1 位",
  );
  await page.keyboard.press("Escape");
  await page.mouse.up();
  await expect(dialog).toBeVisible();
  await expect(names).toHaveText(original);
  await expect(dialog.locator(".route-drag-preview")).toHaveCount(0);
  await start();
  await page.mouse.move(2, 2);
  await page.mouse.up();
  await expect(names).toHaveText(original);
  await start();
  await page.mouse.up();
  await expect(names).toHaveText([original[2], original[0], original[1]]);
  await rows.first().locator(".route-drag-handle").focus();
  await page.keyboard.press("ArrowDown");
  await expect(names).toHaveText([original[0], original[2], original[1]]);
});

test("route editor fits mobile and positions the select chevron inside the field", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installApi(page);
  await page.goto("/routes");
  await page
    .locator(".route-card")
    .first()
    .getByRole("button", { name: "编辑 Router" })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("选择候选模型").scrollIntoViewIfNeeded();
  const select = await dialog.getByLabel("选择候选模型").boundingBox();
  const icon = await dialog.locator(".candidate-select > svg").boundingBox();
  if (!select || !icon) throw new Error("Missing select geometry");
  expect(select.x + select.width - icon.x - icon.width).toBeCloseTo(14, 0);
  expect(icon.y + icon.height / 2).toBeCloseTo(select.y + select.height / 2, 0);
  expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
    true,
  );
  await page.screenshot({
    path: "test-results/route-editor-mobile.png",
    fullPage: true,
  });
});

test("cascade deletion gives impact preview and remains a draft", async ({
  page,
}) => {
  const state = await installApi(page);
  await page.goto("/providers");
  await page
    .getByRole("button", { name: "删除 Provider ProviderA", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("2 个 Router");
  await page.getByRole("button", { name: "从草稿中删除" }).click();
  expect(state.writes).toHaveLength(0);
  await page.getByRole("link", { name: /^Routers/ }).click();
  await expect(
    page.locator(".chain-node").filter({ hasText: "ProviderA" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "放弃草稿", exact: true }).click();
  await page.getByRole("button", { name: "放弃并重新读取" }).click();
  await expect(page.locator(".draft-bar")).toHaveCount(0);
  await expect(
    page.locator(".chain-node").filter({ hasText: "ProviderA" }),
  ).toHaveCount(2);
});

test("remote conflict blocks writes and failed writes preserve drafts", async ({
  page,
}) => {
  const state = await installApi(page);
  await page.goto("/settings");
  await page.getByLabel("连续失败阈值", { exact: true }).fill("8");
  state.config.server.port = 9555;
  await page.getByRole("button", { name: "检查并发布" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "服务器配置已被其他操作修改",
  );
  expect(state.writes).toHaveLength(0);
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await page.getByRole("button", { name: "放弃草稿", exact: true }).click();
  await page.getByRole("button", { name: "放弃并重新读取" }).click();
  await expect(page.getByLabel("端口", { exact: true })).toHaveValue("9555");
  state.failedSave = true;
  await page.getByLabel("连续失败阈值", { exact: true }).fill("7");
  await page.getByRole("button", { name: "检查并发布" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.getByRole("dialog")).toContainText("模拟配置校验失败");
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(page.getByLabel("连续失败阈值", { exact: true })).toHaveValue(
    "7",
  );
});

test("body recording requires confirmation and can be stopped immediately", async ({
  page,
}) => {
  const state = await installApi(page);
  await page.goto("/settings");
  await expect(page.getByText("已关闭（默认）")).toBeVisible();
  await page.getByLabel("开启时长").selectOption("60");
  await page.getByRole("button", { name: "开启正文记录" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("记录响应会显著增加数据库大小");
  await dialog.getByRole("button", { name: "确认开启" }).click();
  await expect(page.getByText("已开启", { exact: true })).toBeVisible();
  expect(state.bodyRecording.enabled).toBe(true);
  expect(state.writes).toHaveLength(0);
  await page.getByRole("button", { name: "立即关闭" }).click();
  await expect(page.getByText("已关闭（默认）")).toBeVisible();
  expect(state.bodyRecording.enabled).toBe(false);
});

test("playground select chevron stays inset and centered in both themes and viewport sizes", async ({
  page,
}) => {
  await installApi(page);
  for (const theme of ["light", "dark"]) {
    await page.addInitScript(
      (value) => localStorage.setItem("routelet-theme", value),
      theme,
    );
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/playground");
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      const select = page.getByLabel("Router", { exact: true });
      await expect(select).toHaveValue("coding-assistant");
      await expect(select).toHaveCSS("appearance", "none");
      const arrow = page.locator(".select-control > svg");
      await expect(arrow).toHaveCSS("pointer-events", "none");
      const field = await select.boundingBox();
      const icon = await arrow.boundingBox();
      if (!field || !icon)
        throw new Error("Missing playground select geometry");
      expect(field.x + field.width - icon.x - icon.width).toBeCloseTo(14, 0);
      expect(icon.y + icon.height / 2).toBeCloseTo(
        field.y + field.height / 2,
        0,
      );
      await page.screenshot({
        path: `test-results/playground-select-${theme}-${width}.png`,
        fullPage: true,
      });
    }
  }
});

test("playground handles both streamed and JSON responses", async ({
  page,
}) => {
  await installApi(page);
  await page.goto("/playground");
  await expect(page.getByLabel("Router", { exact: true })).toHaveValue(
    "coding-assistant",
  );
  await page.getByRole("button", { name: "发送请求", exact: true }).click();
  await expect(page.locator(".response-text")).toContainText(
    "这个 Router 已成功连接",
  );
  await expect(page.locator(".playground-response")).toContainText("已完成");
  await expect(page.locator(".response-stats")).toContainText("24");
  await page.getByRole("button", { name: "原始响应", exact: true }).click();
  await expect(page.locator(".response-content")).toContainText("message_stop");
  await page.getByRole("button", { name: "文本", exact: true }).click();
  await page.getByLabel(/流式输出/).uncheck();
  await page.getByRole("button", { name: "发送请求", exact: true }).click();
  await expect(page.locator(".response-text")).toContainText(
    "通过 Routelet 连接",
  );
});

test("offline state remains visible and recovers on retry", async ({
  page,
}) => {
  const state = await installApi(page);
  await page.goto("/");
  await expect(page.getByText("服务已连接", { exact: true })).toBeVisible();
  state.offline = true;
  await page.getByRole("button", { name: "刷新监控数据" }).click();
  await expect(page.getByText("连接中断", { exact: true })).toBeVisible();
  await expect(page.locator(".global-alert")).toContainText(
    "已展示的数据可能过期",
  );
  state.offline = false;
  await page.getByRole("button", { name: "重试连接" }).click();
  await expect(page.getByText("服务已连接", { exact: true })).toBeVisible();
  await expect(page.locator(".global-alert")).toHaveCount(0);
});

test("mobile layouts, navigation and dialog focus remain usable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installApi(page);
  await page.goto("/");
  await expect(page.locator(".stat-value").first()).toContainText("24,861");
  await page.screenshot({
    path: "test-results/overview-mobile.png",
    fullPage: true,
  });
  for (const path of [
    "/",
    "/calls",
    "/providers",
    "/routes",
    "/settings",
    "/playground",
  ]) {
    await page.goto(path);
    await expect(page.locator("h1")).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("link", { name: "Providers", exact: true }).click();
  await page
    .getByRole("button", { name: "添加 Provider", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  for (let index = 0; index < 15; index++) await page.keyboard.press("Tab");
  expect(
    await page.evaluate(() => !!document.activeElement?.closest("dialog")),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.screenshot({
    path: "test-results/providers-mobile.png",
    fullPage: true,
  });
});

test("all desktop pages load without runtime errors or horizontal overflow", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await installApi(page);
  for (const path of [
    "/providers",
    "/routes",
    "/settings",
    "/playground",
    "/unknown-page",
  ]) {
    await page.goto(path);
    await expect(page.getByText("服务已连接", { exact: true })).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    if (path === "/routes")
      await page.screenshot({
        path: "test-results/routes-desktop.png",
        fullPage: true,
      });
    if (path === "/providers")
      await page.screenshot({
        path: "test-results/providers-desktop.png",
        fullPage: true,
      });
  }
  await expect(page.getByRole("link", { name: "返回运行总览" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("cyberpunk palettes share typography and fit desktop and mobile pages", async ({
  page,
}) => {
  await installApi(page);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  for (const theme of ["light", "dark"]) {
    await page.addInitScript(
      (value) => localStorage.setItem("routelet-theme", value),
      theme,
    );
    for (const mobile of [false, true]) {
      await page.setViewportSize(
        mobile ? { width: 390, height: 844 } : { width: 1440, height: 1100 },
      );
      for (const path of [
        "/",
        "/calls",
        "/routes",
        "/providers",
        "/playground",
        "/settings",
      ]) {
        await page.goto(path);
        await expect(
          page.getByText("服务已连接", { exact: true }),
        ).toBeVisible();
        await page.evaluate(() => document.fonts.ready);
        await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
        expect(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
        ).toBe(true);
        expect(
          await page
            .locator("h1")
            .evaluate((el) => getComputedStyle(el).fontFamily),
        ).toContain("Oxanium");
        if (path === "/") {
          expect(
            await page.evaluate(() =>
              document.fonts.check('600 24px "Oxanium"'),
            ),
          ).toBe(true);
          await page.screenshot({
            path: `test-results/cyberpunk-${theme}-${mobile ? "mobile" : "desktop"}.png`,
            fullPage: true,
          });
        }
        if (!mobile && path === "/routes") {
          await page
            .locator(".route-card")
            .first()
            .getByRole("button", { name: "编辑 Router" })
            .click();
          await expect(page.getByRole("dialog")).toBeVisible();
          await page.screenshot({
            path: `test-results/cyberpunk-${theme}-editor.png`,
            fullPage: true,
          });
          await page.keyboard.press("Escape");
          await expect(page.getByRole("dialog")).toHaveCount(0);
        }
      }
    }
  }
  expect(errors).toEqual([]);
});

test("stale calls cannot replace the latest filter result", async ({
  page,
}) => {
  const state = await installApi(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  let started = false,
    settled = false;
  await page.route("**/api/calls?**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("status") !== "error") {
      await route.fallback();
      return;
    }
    started = true;
    await gate;
    await route
      .fulfill({
        json: {
          data: state.calls.filter((call) => call.status === "error"),
          total: 5,
          page: 1,
          size: 20,
          pages: 1,
        },
      })
      .catch(() => {});
    settled = true;
  });
  await page.goto("/calls");
  await expect(page.locator("tbody tr")).toHaveCount(20);
  await page.getByLabel("请求状态").selectOption("error");
  await expect.poll(() => started).toBe(true);
  await page.getByLabel("请求状态").selectOption("success");
  await expect(page.locator("tbody tr")).toHaveCount(20);
  release();
  await expect.poll(() => settled).toBe(true);
  await expect(page.locator("tbody .badge.red")).toHaveCount(0);
  await expect(page.getByLabel("请求状态")).toHaveValue("success");
});

test("playground exposes stream errors and can cancel an active request", async ({
  page,
}) => {
  await installApi(page);
  await page.goto("/playground");
  await page.route("**/v1/messages", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: 'event: error\ndata: {"type":"error","error":{"message":"test stream failure"}}\n\n',
    }),
  );
  await page.getByRole("button", { name: "发送请求", exact: true }).click();
  await expect(page.locator(".playground-response .alert.error")).toContainText(
    "test stream failure",
  );
  await expect(page.locator(".playground-response")).not.toContainText(
    "已完成",
  );
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/v1/messages", async (route) => {
    await gate;
    await route.abort().catch(() => {});
  });
  await page.getByRole("button", { name: "发送请求", exact: true }).click();
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await expect(page.locator(".playground-response .alert.error")).toContainText(
    "请求已停止",
  );
  await expect(
    page.getByRole("button", { name: "发送请求", exact: true }),
  ).toBeEnabled();
  release();
});

test("configuration validation and masked credentials survive provider editing", async ({
  page,
}) => {
  const state = await installApi(page);
  await page.goto("/providers");
  await page
    .getByRole("button", { name: "编辑 Provider ProviderA", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel(/API Key/)).toHaveValue("");
  await dialog.getByLabel(/API Key/).fill("abcd****wxyz");
  await dialog.getByRole("button", { name: "应用到草稿" }).click();
  await expect(dialog).toContainText("不能使用脱敏占位符");
  expect(state.writes).toHaveLength(0);
  await dialog.getByLabel(/API Key/).fill("");
  await dialog.getByLabel(/Base URL/).fill("https://updated.example.test");
  await dialog.getByRole("button", { name: "应用到草稿" }).click();
  await page.getByRole("button", { name: "检查并发布" }).click();
  await page.getByRole("button", { name: "确认发布" }).click();
  await expect(page.locator(".draft-bar")).toHaveCount(0);
  expect(state.writes[0].providers.ProviderA.api_key).toBe("sk-a********test");
  await page.getByRole("link", { name: "系统设置", exact: true }).click();
  await page.getByLabel("端口", { exact: true }).fill("99999");
  await page.getByRole("button", { name: "检查并发布" }).click();
  await expect(page.getByRole("dialog")).toContainText("端口应为");
  await expect(page.getByRole("button", { name: "确认发布" })).toBeDisabled();
});

test("circuit reset acts on runtime only after confirmation", async ({
  page,
}) => {
  await installApi(page);
  let open = true,
    resets = 0;
  await page.route("**/api/circuit-breaker**", async (route) => {
    if (route.request().method() === "POST") {
      resets++;
      open = false;
      await route.fulfill({ json: { status: "ok" } });
    } else
      await route.fulfill({ json: { ProviderA: open ? "open" : "closed" } });
  });
  await page.goto("/providers");
  await expect(page.locator(".circuit-alert")).toContainText("熔断中");
  await page
    .getByRole("button", { name: "重置 Provider 保护状态 ProviderA" })
    .click();
  expect(resets).toBe(0);
  await page.getByRole("button", { name: "确认重置", exact: true }).click();
  await expect(page.locator(".circuit-alert")).toHaveCount(0);
  expect(resets).toBe(1);
  await page
    .getByRole("button", { name: "重置 Provider 保护状态 ProviderA" })
    .click();
  expect(resets).toBe(1);
  await page.getByRole("button", { name: "确认重置", exact: true }).click();
  await expect.poll(() => resets).toBe(2);
  await expect(page.locator(".draft-bar")).toHaveCount(0);
});
