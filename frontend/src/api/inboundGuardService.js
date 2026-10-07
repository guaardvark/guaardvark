import { BASE_URL, handleResponse } from "./apiClient";

const ROOT = `${BASE_URL}/settings/inbound_guard`;

const post = async (url, body) =>
  handleResponse(
    await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    }),
  );

export const inboundGuardService = {
  async getState() {
    return handleResponse(await fetch(ROOT));
  },

  async setMode(mode) {
    return post(ROOT, { mode });
  },

  // git: include changes the git side recorded, read from the whole ledger. Leave
  // it off when polling: the database list alone is cheap. offset and gitOffset
  // page the two lists; the response carries scans_total and git_total.
  async listScans(status = "open", limit = 50, { git = true, offset = 0, gitLimit = limit, gitOffset = 0 } = {}) {
    const params = new URLSearchParams({
      status,
      limit: String(limit),
      offset: String(offset),
      git: git ? "1" : "0",
      git_limit: String(gitLimit),
      git_offset: String(gitOffset),
    });
    return handleResponse(await fetch(`${ROOT}/scans?${params}`));
  },

  async approveScan(id, { note = "", overrideBlock = false } = {}) {
    return post(`${ROOT}/scans/${id}/approve`, { note, override_block: overrideBlock });
  },

  async rejectScan(id, { note = "" } = {}) {
    return post(`${ROOT}/scans/${id}/reject`, { note });
  },

  async getScan(id) {
    return handleResponse(await fetch(`${ROOT}/scans/${id}`));
  },

  async decide(id, decision, { note = "", overrideBlock = false } = {}) {
    return post(`${ROOT}/scans/${id}/decide`, { decision, note, override_block: overrideBlock });
  },

  async sweep() {
    return post(`${ROOT}/sweep`, {});
  },

  async approveGit(digest, note = "", { overrideBlock = false } = {}) {
    return post(`${ROOT}/git/${encodeURIComponent(digest)}/approve`, { note, override_block: overrideBlock });
  },

  // Decided item by item on the server; blocks are skipped unless includeBlocks.
  // The response holds a result per item plus approved/skipped/failed counts.
  async approveBatch({ ids = [], gitDigests = [], note = "", includeBlocks = false } = {}) {
    return post(`${ROOT}/scans/approve-batch`, { ids, git_digests: gitDigests, note, include_blocks: includeBlocks });
  },

  async rejectBatch({ ids = [], gitDigests = [], note = "" } = {}) {
    return post(`${ROOT}/scans/reject-batch`, { ids, git_digests: gitDigests, note });
  },
};
