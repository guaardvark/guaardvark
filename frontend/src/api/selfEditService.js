import { BASE_URL, handleResponse } from "./apiClient";

/** Changes Guaardvark made to its own code (backups/self_edits). */
export const listSelfEdits = async (limit = 50) =>
  handleResponse(await fetch(`${BASE_URL}/self-edits?limit=${limit}`));

/**
 * Put back one change, or every change of a group (a plan step or a chat
 * turn). Without force, a file changed again since is not touched and the
 * error names it (status 409, error.data.conflicts).
 */
export const revertSelfEdit = async (editOrGroup, { force = false } = {}) =>
  handleResponse(await fetch(`${BASE_URL}/self-edits/${encodeURIComponent(editOrGroup)}/revert`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force }),
  }));
