// A board's saved arrangement lives in the backend's page-state store, one
// document per board (`/api/state/<key>`).

/**
 * The saved state of a board, or null when it has never been saved.
 * Throws an Error carrying `status` and `statusText` on any other failure.
 */
export async function loadBoardState(key) {
  const res = await fetch(`/api/state/${key}`);
  if (!res.ok) {
    if (res.status === 404) return null;
    const err = new Error(`${res.statusText} (${res.status})`);
    err.status = res.status;
    err.statusText = res.statusText;
    throw err;
  }
  return res.json();
}

/** Replace a board's saved state. Throws when the backend refuses it. */
export async function saveBoardState(key, state) {
  const res = await fetch(`/api/state/${key}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(state),
  });
  if (!res.ok) {
    const err = new Error(`(${res.status})`);
    err.status = res.status;
    throw err;
  }
}
