import { BASE_URL, handleResponse } from "./apiClient";

/**
 * Plan mode: a chat's plan-mode switch and the plans it writes.
 * The session row is the source of truth for the switch; useAppStore's
 * sessionPlanModes only caches it for the input's chrome.
 */

const json = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

const sessionPath = (sessionId) => `${BASE_URL}/chat-sessions/${encodeURIComponent(sessionId)}`;
const planPath = (planId) => `${BASE_URL}/chat/plans/${encodeURIComponent(planId)}`;

/** {mode, plan_mode, plan_mode_available, plan_mode_reason} for a chat. */
export const getSessionModes = async (sessionId) =>
  handleResponse(await fetch(`${sessionPath(sessionId)}/mode`));

export const setPlanMode = async (sessionId, on) =>
  handleResponse(await fetch(`${sessionPath(sessionId)}/plan-mode`, json("PATCH", { on })));

export const getPlan = async (planId) => handleResponse(await fetch(planPath(planId)));

/** mode: "ask" (a card before each action) or "allow_plan_tools". */
export const approvePlan = async (planId, { mode, revision }) =>
  handleResponse(await fetch(`${planPath(planId)}/approve`, json("POST", { mode, revision })));

export const rejectPlan = async (planId) =>
  handleResponse(await fetch(`${planPath(planId)}/reject`, json("POST", {})));

/** action: "retry" or "skip", for a plan paused at a step. */
export const continuePlan = async (planId, action) =>
  handleResponse(await fetch(`${planPath(planId)}/continue`, json("POST", { action })));

export const replanPlan = async (planId) =>
  handleResponse(await fetch(`${planPath(planId)}/replan`, json("POST", {})));

/** steps: [{title, action, kind, tools}]; saved as the next revision. */
export const editPlan = async (planId, { revision, goal, steps }) =>
  handleResponse(await fetch(`${planPath(planId)}/edit`, json("POST", { revision, goal, steps })));
