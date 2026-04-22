import { BASE_URL, handleResponse } from "./apiClient";

export const selfImprovementService = {
  async getStatus() {
    const res = await fetch(`${BASE_URL}/self-improvement/status`);
    return handleResponse(res);
  },

  async toggle(enabled) {
    const res = await fetch(`${BASE_URL}/self-improvement/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    return handleResponse(res);
  },

  async lockCodebase(locked) {
    const res = await fetch(`${BASE_URL}/self-improvement/lock-codebase`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ locked }),
    });
    return handleResponse(res);
  },

  async getRuns(limit = 20, offset = 0) {
    const res = await fetch(`${BASE_URL}/self-improvement/runs?limit=${limit}&offset=${offset}`);
    return handleResponse(res);
  },

  async triggerRun() {
    const res = await fetch(`${BASE_URL}/self-improvement/trigger`, { method: "POST" });
    return handleResponse(res);
  },

  async submitTask(description, targetFiles = [], priority = "medium") {
    const res = await fetch(`${BASE_URL}/self-improvement/task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description, target_files: targetFiles, priority }),
    });
    return handleResponse(res);
  },

  async listPendingFixes({ status, limit = 50 } = {}) {
    const params = new URLSearchParams();
    if (status) params.set("status", status);
    if (limit) params.set("limit", String(limit));
    const query = params.toString();
    const res = await fetch(
      `${BASE_URL}/self-improvement/pending-fixes${query ? `?${query}` : ""}`,
    );
    return handleResponse(res);
  },

  async approveFix(fixId, { reviewer = "user", notes = "" } = {}) {
    const res = await fetch(`${BASE_URL}/self-improvement/pending-fixes/${fixId}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer, notes }),
    });
    return handleResponse(res);
  },

  async rejectFix(fixId, { reviewer = "user", notes = "" } = {}) {
    const res = await fetch(`${BASE_URL}/self-improvement/pending-fixes/${fixId}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reviewer, notes }),
    });
    return handleResponse(res);
  },

  async applyFix(fixId) {
    const res = await fetch(`${BASE_URL}/self-improvement/pending-fixes/${fixId}/apply`, {
      method: "POST",
    });
    return handleResponse(res);
  },
};
