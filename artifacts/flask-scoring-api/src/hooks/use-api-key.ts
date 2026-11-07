import { useSyncExternalStore } from "react";
import { getApiKey, setApiKey, subscribeApiKey } from "./api-key-store";

export function useApiKey() {
  const apiKey = useSyncExternalStore(subscribeApiKey, getApiKey, () => "");
  return { apiKey, setApiKey };
}
