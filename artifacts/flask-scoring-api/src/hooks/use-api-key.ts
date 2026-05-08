import { useState, useEffect } from "react";

const STORAGE_KEY = "wow_api_key";

export function useApiKey() {
  const [apiKey, setApiKeyState] = useState<string>(() => {
    return localStorage.getItem(STORAGE_KEY) ?? "";
  });

  useEffect(() => {
    if (apiKey) {
      localStorage.setItem(STORAGE_KEY, apiKey);
    } else {
      localStorage.removeItem(STORAGE_KEY);
    }
  }, [apiKey]);

  return { apiKey, setApiKey: setApiKeyState };
}
