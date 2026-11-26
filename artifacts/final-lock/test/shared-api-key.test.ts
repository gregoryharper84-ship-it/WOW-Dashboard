import assert from "node:assert/strict";
import { test } from "node:test";
import { getApiKey, setApiKey, subscribeApiKey } from "../../flask-scoring-api/src/hooks/api-key-store.ts";

test("layout and page consumers receive key updates in the same tab and across tabs", () => {
  const values = new Map<string, string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) };
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage });
  Object.defineProperty(globalThis, "window", { configurable: true, value: new EventTarget() });
  const layout: string[] = [];
  const page: string[] = [];
  const unsubscribeLayout = subscribeApiKey(() => layout.push(getApiKey()));
  const unsubscribePage = subscribeApiKey(() => page.push(getApiKey()));
  setApiKey("first");
  setApiKey("second");
  setApiKey("");
  values.set("wow_api_key", "other-tab");
  const event = new Event("storage");
  Object.defineProperty(event, "key", { value: "wow_api_key" });
  window.dispatchEvent(event);
  assert.deepEqual(layout, ["first", "second", "", "other-tab"]);
  assert.deepEqual(page, layout);
  unsubscribeLayout(); unsubscribePage();
  setApiKey("after-unsubscribe");
  assert.equal(page.length, 4);
});
