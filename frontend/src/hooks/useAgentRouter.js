// frontend/src/hooks/useAgentRouter.js
// Hook for agent-based message routing
// Can be used alongside or instead of hardcoded detection in ChatPage
// Version 1.0
/* eslint-env browser */

import { useState, useCallback } from "react";
import { routeMessage, routeAndExecute } from "../api/toolsService";

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
        setError(response.error || "Routing failed");
        return null;
      }
    } catch (err) {
      setError(err.message || "Routing failed");
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
        setError(response.error || "Execution failed");
        return null;
      }
    } catch (err) {
      setError(err.message || "Execution failed");
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
  const toDetectionFormat = useCallback((routeDecision) => {
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
  }, []);

  return {
    route,
    execute,
    loading,
    lastRoute,
    error,
    isFileGenerationRoute,
    isToolRoute,
    isAgentLoopRoute,
    isHighConfidence,
    toDetectionFormat,
    RouteType,
  };
}

export default useAgentRouter;
