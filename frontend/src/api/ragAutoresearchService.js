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
};
