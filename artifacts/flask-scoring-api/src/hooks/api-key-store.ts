const STORAGE_KEY = "wow_api_key";
const CHANGE_EVENT = "wow-api-key-changed";
let memoryKey = "";

export function getApiKey(): string {
  try { return localStorage.getItem(STORAGE_KEY) ?? memoryKey; }
  catch { return memoryKey; }
}

export function setApiKey(value: string): void {
  memoryKey = value;
  try {
    if (value) localStorage.setItem(STORAGE_KEY, value);
    else localStorage.removeItem(STORAGE_KEY);
  } catch { /* Storage can be unavailable; mounted consumers still synchronize. */ }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function subscribeApiKey(listener: () => void) {
  const onStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY || event.key === null) {
      memoryKey = "";
      listener();
    }
  };
  window.addEventListener(CHANGE_EVENT, listener);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGE_EVENT, listener);
    window.removeEventListener("storage", onStorage);
  };
}
