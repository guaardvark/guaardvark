import { BASE_URL, handleResponse } from "./apiClient";

export const claudeAdvisorService = {
  async getStatus() {
    const res = await fetch(`${BASE_URL}/claude/status`);
    return handleResponse(res);
  },

  async testConnection() {
    const res = await fetch(`${BASE_URL}/claude/test-connection`, { method: "POST" });
    return handleResponse(res);
  },

  async escalate(message, history = [], systemContext = "") {
    const res = await fetch(`${BASE_URL}/claude/escalate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, history, system_context: systemContext }),
    });
    return handleResponse(res);
  },

  async getAdvice(systemState = {}) {
    const res = await fetch(`${BASE_URL}/claude/advise`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ system_state: systemState }),
    });
    return handleResponse(res);
  },

  async getUsage() {
    const res = await fetch(`${BASE_URL}/claude/usage`);
    return handleResponse(res);
  },

  async updateConfig(config) {
    const res = await fetch(`${BASE_URL}/claude/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(config),
    });
    return handleResponse(res);
  },
};
