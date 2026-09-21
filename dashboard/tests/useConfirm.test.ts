import { expect, test } from "bun:test";
import { createConfirmController } from "../src/composables/useConfirm";

const firstOptions = {
  title: "离开？",
  message: "第一项确认",
};

test("a newer confirmation cancels the previous pending request", async () => {
  const confirm = createConfirmController();
  const first = confirm.confirm(firstOptions);
  const second = confirm.confirm({
    title: "删除？",
    message: "第二项确认",
    danger: true,
  });

  expect(await first).toBe(false);
  expect(confirm.state.value?.title).toBe("删除？");

  confirm.answer(true);

  expect(await second).toBe(true);
  expect(confirm.state.value).toBeNull();
});

test("answer without a pending confirmation is a no-op", () => {
  const confirm = createConfirmController();

  confirm.answer(false);

  expect(confirm.state.value).toBeNull();
});
