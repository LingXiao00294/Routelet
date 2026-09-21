import { describe, expect, test } from "bun:test";
import { adjacentMoveTarget } from "../src/utils/modelOrdering";

describe("adjacentMoveTarget", () => {
  test("returns adjacent positions within the list", () => {
    expect(adjacentMoveTarget(3, 1, -1)).toBe(0);
    expect(adjacentMoveTarget(3, 1, 1)).toBe(2);
  });

  test("disables movement at either boundary", () => {
    expect(adjacentMoveTarget(3, 0, -1)).toBeNull();
    expect(adjacentMoveTarget(3, 2, 1)).toBeNull();
  });

  test("rejects empty lists and invalid source indexes", () => {
    expect(adjacentMoveTarget(0, 0, 1)).toBeNull();
    expect(adjacentMoveTarget(3, -1, 1)).toBeNull();
    expect(adjacentMoveTarget(3, 3, -1)).toBeNull();
  });
});
