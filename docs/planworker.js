/* Bend-order planning off the page's main thread (a part with many bends takes a few seconds). */
import { prepare, planSequence } from "./press.js";

self.onmessage = (e) => {
  const { id, pc, t } = e.data;
  try {
    const plan = planSequence(prepare(pc, t));
    self.postMessage({ id, plan });
  } catch (err) {
    self.postMessage({ id, error: String(err && err.message || err) });
  }
};
