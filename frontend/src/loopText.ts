import type { LoopActuator } from "./api";

type Item = Pick<LoopActuator, "name" | "side" | "kind">;
const name = (a: Item) => `${a.name}${a.kind === "sensor" ? " (cascade)" : ""}`;

/**
 * What acts on each side of the target, as set on the controller: e.g. pH — below target Alkali (base buffer),
 * above target Addition 2 (acid buffer); dO2 — below target Air or stirrer up, above target N2 or stirrer down.
 */
export function sides(items: Item[] | undefined) {
  const list = items ?? [];
  return {
    below: list.filter((a) => a.side > 0).map(name),
    above: list.filter((a) => a.side < 0).map(name),
  };
}

export function sidesText(items: Item[] | undefined) {
  const s = sides(items);
  if (!s.below.length && !s.above.length) return "no actuator assigned";
  return `below target: ${s.below.join(", ") || "nothing"} · above target: ${s.above.join(", ") || "nothing"}`;
}
