import { createApiClient } from "./apiClient.js";
import { getStorageValue } from "./storage.js";

let _client = null;

export function getApiClient() {
  if (!_client) {
    const base = getStorageValue("apiBase");
    const candidates = base
      ? [base]
      : ["http://127.0.0.1:8100", "http://127.0.0.1:8101", "http://127.0.0.1:8102"];
    _client = createApiClient(candidates);
  }
  return _client;
}
