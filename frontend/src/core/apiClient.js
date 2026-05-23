export function createApiClient(candidates) {
  const apiCandidates = Array.isArray(candidates) && candidates.length
    ? candidates
    : ["http://127.0.0.1:8100"];
  let activeBase = apiCandidates[0];

  async function requestJson(path, options = {}) {
    let lastError = null;

    for (const base of apiCandidates) {
      try {
        const response = await fetch(`${base}${path}`, options);
        if (!response.ok) {
          let errorMessage = `${path} HTTP ${response.status}`;
          try {
            const body = await response.json();
            if (body.error && body.error.message) {
              errorMessage = body.error.message;
              if (body.error.hint) errorMessage += ` - ${body.error.hint}`;
            }
          } catch {
            // Use fallback message.
          }
          throw new Error(errorMessage);
        }
        activeBase = base;
        return response.json();
      } catch (error) {
        lastError = error;
      }
    }

    throw lastError || new Error(`${path} request failed`);
  }

  return {
    candidates: apiCandidates,
    get activeBase() {
      return activeBase;
    },
    requestJson,
    fetchJson(path) {
      return requestJson(path);
    },
    eventSourceUrl(path) {
      return `${activeBase}${path}`;
    },
  };
}
