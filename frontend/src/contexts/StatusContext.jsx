// frontend/src/contexts/StatusContext.jsx
// Provides global application status, starting with the active LLM model.

import React, {
  createContext,
  useState,
  useEffect,
  useContext,
  useCallback,
  useRef,
} from "react";
import { getCurrentModel, getModelStatus } from "../api";

// 1. Create the Context
const StatusContext = createContext(null);

export const StatusProvider = ({ children }) => {
  const [activeModel, setActiveModel] = useState("");
  const [isLoadingModel, setIsLoadingModel] = useState(true);
  const [modelError, setModelError] = useState(null);
  const [modelStatus, setModelStatus] = useState(null);
  const [isLoadingStatus, setIsLoadingStatus] = useState(true);
  const [lastFetchTime, setLastFetchTime] = useState(0);
  const isInitializedRef = useRef(false);

  const fetchModel = useCallback(async () => {
    console.log("StatusContext: Fetching current model...");
    setIsLoadingModel(true);
    setModelError(null);
    try {
      let modelName = await getCurrentModel();

      // If the result is an object (e.g. raw active_model.json), extract the name
      if (modelName && typeof modelName === "object") {
        modelName = modelName.active_model || modelName.model || null;
      }
      // If the result is a JSON string, try to parse and extract
      if (typeof modelName === "string" && modelName.startsWith("{")) {
        try {
          const parsed = JSON.parse(modelName);
          modelName = parsed.active_model || parsed.model || modelName;
        } catch (_) {
          // Not valid JSON, use as-is
        }
      }

      setActiveModel(modelName || "N/A");
      console.log("StatusContext: Model fetch success:", modelName);
    } catch (err) {
      console.error("StatusContext: Failed to fetch model:", err);
      setModelError(err.message || "Failed to fetch model status.");
      setActiveModel("Error");
    } finally {
      setIsLoadingModel(false);
    }
  }, []);

  const fetchModelStatus = useCallback(async () => {
    // Prevent excessive polling - only fetch if more than 30 seconds have passed
    const now = Date.now();
    if (now - lastFetchTime < 30000) {
      console.log("StatusContext: Skipping status fetch - too recent");
      return;
    }
    
    console.log("StatusContext: Fetching model status...");
    setIsLoadingStatus(true);
    try {
      const status = await getModelStatus();
      setModelStatus(status);
      setLastFetchTime(now);
      console.log("StatusContext: Model status fetch success:", status);
    } catch (err) {
      console.error("StatusContext: Failed to fetch model status:", err);
      setModelStatus(null);
    } finally {
      setIsLoadingStatus(false);
    }
  }, [lastFetchTime]);

  useEffect(() => {
    // Only initialize once to prevent duplicate calls
    // Use ref instead of state to avoid race conditions
    if (isInitializedRef.current) return;

    const initializeStatus = async () => {
      // Mark as initialized immediately to prevent concurrent calls
      isInitializedRef.current = true;
      await Promise.all([fetchModel(), fetchModelStatus()]);
    };

    initializeStatus();
  }, []);

  // Value provided by the context
  const contextValue = {
    activeModel,
    isLoadingModel,
    modelError,
    modelStatus,
    isLoadingStatus,
    refreshActiveModel: fetchModel,
    refreshModelStatus: fetchModelStatus,
  };
  return (
    <StatusContext.Provider value={contextValue}>
      {children}
    </StatusContext.Provider>
  );
};

export const useStatus = () => {
  const context = useContext(StatusContext);
  if (context === undefined || context === null) {
    throw new Error("useStatus must be used within a StatusProvider");
  }
  return context;
};
