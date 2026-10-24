// frontend/src/hooks/useAgentRouter.js
// Hook for agent-based message routing
// Can be used alongside or instead of hardcoded detection in ChatPage
// Version 1.0
/* eslint-env browser */

import { useState, useCallback } from "react";
import { routeMessage, routeAndExecute } from "../api/toolsService";
import { useAppStore } from "../stores/useAppStore";
import { translate } from "../i18n";

/**
 * Route types from the backend
 */
export const RouteType = {
  TOOL_DIRECT: "tool_direct",
  AGENT_LOOP: "agent_loop",
  CHAT_ONLY: "chat_only",
  FILE_GENERATION: "file_gen",
};

/**
 * Convert a backend route to the detection shape ChatPage reads.
 *
 * A file route without a typed name gets one with the extension the request
 * asked for (tool_params.extension, from agent_router.requested_file): "create
 * a python file" proposes a .py. A generate_file request that names no
 * language proposes a .txt; codegen keeps .js.
 * @param {Object} routeDecision - Route decision from backend
 * @returns {Object} Detection result compatible with ChatPage
 */
export function routeToDetection(routeDecision) {
  if (!routeDecision || routeDecision.route_type === RouteType.CHAT_ONLY) {
    return { isCSVRequest: false, isCodeRequest: false };
  }

  const toolName = routeDecision.tool_name || "";
  const isCSV = ["generate_csv", "generate_bulk_csv", "generate_wordpress_content", "generate_enhanced_wordpress_content"].includes(toolName);
  const isCode = ["codegen", "generate_file"].includes(toolName) && !isCSV;
  const isBulk = toolName === "generate_bulk_csv";

  // Extract quantity from params if available
  const quantity = routeDecision.tool_params?.quantity || null;

  // Generate filename if not provided
  let filename = routeDecision.tool_params?.filename;
  if (!filename) {
    if (isCSV) {
      filename = `generated_data_${Date.now()}.csv`;
    } else if (toolName === "generate_file") {
      const extension = routeDecision.tool_params?.extension || "txt";
      filename = `generated_file_${Date.now()}.${extension}`;
    } else if (isCode) {
      filename = `generated_code_${Date.now()}.js`;
    }
  }

  return {
    isCSVRequest: isCSV,
    isCodeRequest: isCode,
    isBulkRequest: isBulk,
    filename,
    quantity,
    description: `Route: ${routeDecision.reasoning}`,
    toolName: routeDecision.tool_name,
    toolParams: routeDecision.tool_params,
    confidence: routeDecision.confidence,
  };
}

/**
 * Hook for agent-based message routing
 *
 * Usage:
 * ```jsx
 * const { route, execute, loading, lastRoute } = useAgentRouter();
 *
 * // Just get routing decision
 * const decision = await route("Generate 50 CSV pages about SEO");
 *
 * // Route and execute in one call
 * const result = await execute("Generate 50 CSV pages", { client: "Acme" });
 * ```
 */
export function useAgentRouter() {
  const [loading, setLoading] = useState(false);
  const [lastRoute, setLastRoute] = useState(null);
  const [error, setError] = useState(null);

  /**
   * Route a message to determine appropriate handling
   * @param {string} message - User message
   * @param {Object} context - Optional context (client, project_id, etc.)
   * @returns {Promise<Object>} Routing decision
   */
  const route = useCallback(async (message, context = {}) => {
    setLoading(true);
    setError(null);

    try {
      const response = await routeMessage(message, context);

      if (response.success) {
        setLastRoute(response.route);
        return response.route;
      } else {
        setError(response.error || translate("messages.agentRouter.routingFailed"));
        return null;
      }
    } catch (err) {
      setError(err.message || translate("messages.agentRouter.routingFailed"));
      console.error("Agent routing error:", err);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Route and execute a message in one call
   * @param {string} message - User message
   * @param {Object} context - Optional context
   * @returns {Promise<Object>} Execution result
   */
  const execute = useCallback(async (message, context = {}) => {
    setLoading(true);
    setError(null);

    try {
      const response = await routeAndExecute(message, context);

      if (response.success) {
        return response.result;
      } else {
        setError(response.error || translate("messages.agentRouter.executionFailed"));
        return null;
      }
    } catch (err) {
      setError(err.message || translate("messages.agentRouter.executionFailed"));
      console.error("Agent execution error:", err);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  /**
   * Check if a route decision suggests file generation
   * @param {Object} routeDecision - Route decision from backend
   * @returns {boolean}
   */
  const isFileGenerationRoute = useCallback((routeDecision) => {
    if (!routeDecision) return false;
    return routeDecision.route_type === RouteType.FILE_GENERATION ||
      (routeDecision.route_type === RouteType.TOOL_DIRECT &&
       ["generate_file", "generate_csv", "generate_bulk_csv", "codegen"].includes(routeDecision.tool_name));
  }, []);

  /**
   * Check if a route decision suggests tool execution
   * @param {Object} routeDecision - Route decision from backend
   * @returns {boolean}
   */
  const isToolRoute = useCallback((routeDecision) => {
    if (!routeDecision) return false;
    return routeDecision.route_type === RouteType.TOOL_DIRECT;
  }, []);

  /**
   * Check if a route decision requires full agent reasoning loop
   * @param {Object} routeDecision - Route decision from backend
   * @returns {boolean}
   */
  const isAgentLoopRoute = useCallback((routeDecision) => {
    if (!routeDecision) return false;
    return routeDecision.route_type === RouteType.AGENT_LOOP;
  }, []);

  /**
   * Legacy /tools/route-and-execute agent loops for browser/desktop automation
   * use AgentExecutor + browser_* tools — NOT the virtual-screen ACS loop.
   * Those must go through unified chat (Gemma4 direct when screen is active).
   */
  const isScreenAutomationRoute = useCallback((routeDecision) => {
    if (!routeDecision) return false;
    const reasoning = (routeDecision.reasoning || "").toLowerCase();
    return (
      reasoning.includes("browser_automation") ||
      reasoning.includes("desktop_automation")
    );
  }, []);

  /**
   * Whether this route should use the legacy /tools/route-and-execute path.
   * Screen automation, agent-screen sessions and routes the backend marks
   * execute_via "unified" (the AgentBrain preview, and agent-loop matches that
   * are not an explicit agent or MCP request; see is_explicit_agent_request in
   * backend/services/agent_router.py) need unified chat instead.
   */
  const shouldUseLegacyAgentLoop = useCallback((routeDecision, sessionId) => {
    if (!routeDecision || routeDecision.route_type !== RouteType.AGENT_LOOP) {
      return false;
    }
    if (routeDecision.execute_via === "unified") {
      return false;
    }
    const store = useAppStore.getState();
    const inAgentScreenContext =
      store.getSessionMode?.(sessionId) === "agent" ||
      store.agentScreenOpen === true;
    if (inAgentScreenContext || isScreenAutomationRoute(routeDecision)) {
      return false;
    }
    return true;
  }, [isScreenAutomationRoute]);

  /**
   * Check if a route decision is high confidence
   * @param {Object} routeDecision - Route decision from backend
   * @param {number} threshold - Confidence threshold (default 0.7)
   * @returns {boolean}
   */
  const isHighConfidence = useCallback((routeDecision, threshold = 0.7) => {
    if (!routeDecision) return false;
    return routeDecision.confidence >= threshold;
  }, []);

  /**
   * Convert backend route to ChatPage-compatible detection format
   * For backwards compatibility with existing ChatPage code
   * @param {Object} routeDecision - Route decision from backend
   * @returns {Object} Detection result compatible with ChatPage
   */
  const toDetectionFormat = useCallback(routeToDetection, []);

  return {
    route,
    execute,
    loading,
    lastRoute,
    error,
    isFileGenerationRoute,
    isToolRoute,
    isAgentLoopRoute,
    isScreenAutomationRoute,
    shouldUseLegacyAgentLoop,
    isHighConfidence,
    toDetectionFormat,
    RouteType,
  };
}

export default useAgentRouter;
