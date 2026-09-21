export type MoveDirection = -1 | 1;

/** Return the adjacent destination for a model reference, or null at a boundary. */
export function adjacentMoveTarget(
  length: number,
  index: number,
  direction: MoveDirection,
): number | null {
  if (!Number.isInteger(length) || !Number.isInteger(index) || length <= 0) {
    return null;
  }
  if (index < 0 || index >= length) return null;
  const target = index + direction;
  return target >= 0 && target < length ? target : null;
}
