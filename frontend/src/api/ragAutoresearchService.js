import { BASE_URL, handleResponse } from "./apiClient";

export const ragAutoresearchService = {
  async getStatus() {
    const res = await fetch(`${BASE_URL}/autoresearch/status`);
    return handleResponse(res);
  },

  async start(maxExperiments = 0) {
    const res = await fetch(`${BASE_URL}/autoresearch/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ max_experiments: maxExperiments }),
    });
    return handleResponse(res);
  },

  async stop() {
    const res = await fetch(`${BASE_URL}/autoresearch/stop`, {
      method: "POST",
    });
    return handleResponse(res);
  },

  async getHistory(page = 1, perPage = 20) {
    const res = await fetch(
      `${BASE_URL}/autoresearch/history?page=${page}&per_page=${perPage}`,
    );
    return handleResponse(res);
  },

  async getConfig() {
    const res = await fetch(`${BASE_URL}/autoresearch/config`);
    return handleResponse(res);
  },

  async resetConfig() {
    const res = await fetch(`${BASE_URL}/autoresearch/config/reset`, {
      method: "POST",
    });
    return handleResponse(res);
  },

  async getSettings() {
    const res = await fetch(`${BASE_URL}/autoresearch/settings`);
    return handleResponse(res);
  },

  async updateSettings(settings) {
    const res = await fetch(`${BASE_URL}/autoresearch/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
    return handleResponse(res);
  },

  async getPromotions() {
    const res = await fetch(`${BASE_URL}/autoresearch/promotions`);
    return handleResponse(res);
  },

  async activatePromotion(configId) {
    const res = await fetch(
      `${BASE_URL}/autoresearch/promotions/${configId}/activate`,
      { method: "POST" },
    );
    return handleResponse(res);
  },

  async revertPromotion() {
    const res = await fetch(`${BASE_URL}/autoresearch/promotions/revert`, {
      method: "POST",
    });
    return handleResponse(res);
  },

  async getMetrics(limit = 50) {
    const res = await fetch(`${BASE_URL}/autoresearch/metrics?limit=${limit}`);
    return handleResponse(res);
  },

  async getEvalPairs() {
    const res = await fetch(`${BASE_URL}/autoresearch/eval-pairs`);
    return handleResponse(res);
  },

  async regenerateEvalPairs(count) {
    const res = await fetch(`${BASE_URL}/autoresearch/eval-pairs/regenerate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(count ? { count } : {}),
    });
    return handleResponse(res);
  },

  // Kicks off a research run now ("research tonight"). Backend responds 202
  // on success and 409 when a run is already in progress — handleResponse
  // throws on 409 with error.status set, so callers can branch on it.
  async createRun({ mode = "unified", budget_hours: budgetHours } = {}) {
    const res = await fetch(`${BASE_URL}/autoresearch/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, budget_hours: budgetHours }),
    });
    return handleResponse(res);
  },

  async listRuns() {
    const res = await fetch(`${BASE_URL}/autoresearch/runs`);
    return handleResponse(res);
  },

  // Includes report_md + program_snapshot.
  async getRun(runId) {
    const res = await fetch(`${BASE_URL}/autoresearch/runs/${runId}`);
    return handleResponse(res);
  },

  // Direct URL to the run's TSV experiment ledger (for download links).
  getRunLedgerUrl(runId) {
    return `${BASE_URL}/autoresearch/runs/${runId}/ledger`;
  },
};
