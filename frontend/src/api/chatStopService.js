/**
 * Stopping a chat turn: the one place the UI asks the backend to stop.
 *
 * Stop sends two requests: abort the session's chat turn, and kill any agent
 * task it started (the socket abort alone leaves the see-think-act loop
 * running). Both go through handleResponse, so an offline backend or a
 * refused request comes back as a specific error instead of nothing.
 */
import { BASE_URL, handleResponse } from "./apiClient";
import { translate } from "../i18n";

const post = (url) =>
  fetch(url, { method: "POST" }).then((res) => handleResponse(res, { quiet: true }));

/**
 * Ask the backend to stop the session's turn and any agent task.
 * Never throws. Resolves to `{ ok: true }`, or `{ ok: false, error }` with
 * a message naming what could not be stopped.
 */
export async function stopChatTurn(sessionId) {
  const results = await Promise.allSettled([
    sessionId
      ? post(`${BASE_URL}/chat/unified/${encodeURIComponent(sessionId)}/abort`)
      : Promise.resolve(null),
    post(`${BASE_URL}/agent-control/kill`),
  ]);
  const [abort, kill] = results;
  const failures = [];
  if (abort.status === "rejected") failures.push(["chatTurn", abort.reason]);
  if (kill.status === "rejected") failures.push(["agentTask", kill.reason]);
  if (!failures.length) return { ok: true };
  const offline = failures.some(([, err]) => err?.backendOffline);
  const why = offline
    ? translate("messages.chatStop.backendUnreachable")
    : failures.map(([, err]) => err?.message || String(err)).join("; ");
  const error =
    failures.length > 1
      ? translate("messages.chatStop.notReachedBoth", { reason: why })
      : failures[0][0] === "chatTurn"
        ? translate("messages.chatStop.notReachedChatTurn", { reason: why })
        : translate("messages.chatStop.notReachedAgentTask", { reason: why });
  return { ok: false, error };
}
