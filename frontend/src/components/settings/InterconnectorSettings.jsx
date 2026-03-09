// frontend/src/components/settings/InterconnectorSettings.jsx
// Interconnector Plugin Settings Component
// Manages network interconnection between Guaardvark instances

import React, { useState, useEffect, useRef } from "react";
import {
  Box,
  Typography,
  Paper,
  Grid,
  FormControl,
  FormControlLabel,
  Switch,
  Select,
  MenuItem,
  InputLabel,
  TextField,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Alert,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  IconButton,
  Tooltip,
  Card,
  CardContent,
  LinearProgress,
} from "@mui/material";
import {
  CloudSync as SyncIcon,
  CloudOff as DisconnectIcon,
  Refresh as RefreshIcon,
  CheckCircle as SuccessIcon,
  Error as ErrorIcon,
  Warning as WarningIcon,
  Storage as StorageIcon,
  Computer as ComputerIcon,
  Dns as NetworkIcon,
  VpnKey as KeyIcon,
  Sync as ManualSyncIcon,
  Settings as SettingsIcon,
} from "@mui/icons-material";
import * as interconnectorApi from "../../api/interconnectorService";
import { useSnackbar } from "../common/SnackbarProvider";
import { useAppStore } from "../../stores/useAppStore";
import ClientUpdatePanel from "./ClientUpdatePanel";

const InterconnectorSettings = () => {
  const { showMessage } = useSnackbar();
  const systemName = useAppStore((state) => state.systemName);

  // State for configuration
  const [config, setConfig] = useState({
    is_enabled: false,
    node_mode: "client",
    node_name: "",
    master_url: "",
    master_api_key: "",
    api_key_hash: "",
    require_api_key: true,  // Require API key authentication
    auto_sync_enabled: false,
    sync_interval_seconds: 300,
    sync_entities: ["clients", "projects", "websites"],
  });

  // Track last-saved config for cancel/revert
  const savedConfigRef = useRef(null);

  // State for status
  const [status, setStatus] = useState(null);
  const [nodes, setNodes] = useState([]);

  // UI state
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isSyncingData, setIsSyncingData] = useState(false);
  const [isSyncingCode, setIsSyncingCode] = useState(false);
  const [isLoadingOutputs, setIsLoadingOutputs] = useState(false);
  const [generatedApiKey, setGeneratedApiKey] = useState(null);
  const [showApiKey, setShowApiKey] = useState(false);
  const [dataSyncResults, setDataSyncResults] = useState(null);
  const [codeSyncResults, setCodeSyncResults] = useState(null);
  const [outputsIndex, setOutputsIndex] = useState(null);
  const [connectionTestResult, setConnectionTestResult] = useState(null);
  const [isTestingConnection, setIsTestingConnection] = useState(false);
  const [isTestingFileScan, setIsTestingFileScan] = useState(false);
  const [fileScanTestResult, setFileScanTestResult] = useState(null);
  const [testingClientNodes, setTestingClientNodes] = useState(new Set());
  const [clientTestResults, setClientTestResults] = useState({});
  const [profiles, setProfiles] = useState([]);
  const [selectedProfile, setSelectedProfile] = useState(null);
  const [pendingApprovals, setPendingApprovals] = useState([]);
  const [isLoadingApprovals, setIsLoadingApprovals] = useState(false);
  const [broadcastResult, setBroadcastResult] = useState(null);
  
  // Client simplified view mode - show simple update panel by default for clients
  const [showAdvancedClientView, setShowAdvancedClientView] = useState(false);
  
  // Node ID for client registration (stored in localStorage for persistence)
  const [nodeId, setNodeId] = useState(() => {
    const stored = localStorage.getItem('interconnector_node_id');
    return stored || null;
  });

  // Load initial data
  useEffect(() => {
    loadConfiguration();
    loadProfiles();
    loadApprovals();
  }, []);

  // Auto-refresh status and nodes when enabled
  useEffect(() => {
    if (!config.is_enabled) {
      return;
    }

    loadStatus();
    if (config.node_mode === "master") {
      loadNodes();
    }

    // Refresh every 30 seconds
    const interval = setInterval(() => {
      loadStatus();
      // Use a ref to avoid stale closure by checking current mode each time
      const currentMode = config.node_mode;
      if (currentMode === "master") {
        loadNodes();
      }
    }, 30000);

    return () => clearInterval(interval);
  }, [config.is_enabled, config.node_mode]);

  useEffect(() => {
    if (!config.is_enabled) {
      return;
    }
    loadApprovals();
    const interval = setInterval(() => {
      loadApprovals();
    }, 30000);
    return () => clearInterval(interval);
  }, [config.is_enabled]);

  // Client node: Automatic registration and heartbeat
  useEffect(() => {
    if (!config.is_enabled || config.node_mode !== "client") {
      return;
    }

    if (!config.master_url || !config.master_api_key || !config.node_name) {
      return;
    }

    // Register with master on mount or when config changes
    const registerClient = async () => {
      try {
        // Get the actual network IP from the backend
        let networkIp = null;
        let networkPort = 5000;
        try {
          const networkInfo = await interconnectorApi.getNetworkInfo();
          if (networkInfo.data) {
            networkIp = networkInfo.data.network_ip;
            networkPort = networkInfo.data.port || 5000;
            console.log("[INTERCONNECTOR] Got network info:", networkInfo.data);
          }
        } catch (netErr) {
          console.warn("[INTERCONNECTOR] Could not get network info, will rely on server detection:", netErr);
        }
        
        // Detect basic capabilities
        const capabilities = {
          cpu_cores: navigator.hardwareConcurrency || 4,
          memory_mb: (navigator.deviceMemory || 4) * 1024,
          gpu_available: false, // Could be enhanced with WebGL detection
        };

        const registrationData = {
          node_name: config.node_name,
          node_id: nodeId, // Use existing node ID if available
          node_mode: "client",
          sync_entities: config.sync_entities || ["clients", "projects"],
          capabilities: capabilities,
          // Use actual network IP from backend (not window.location which may be localhost)
          client_ip: networkIp,
          client_port: networkPort,
        };

        console.log("[INTERCONNECTOR] Registration data:", registrationData);

        const response = await interconnectorApi.registerWithMaster(
          config.master_url,
          config.master_api_key,
          registrationData
        );

        console.log("[INTERCONNECTOR] Registration response:", response);

        if (response.error) {
          console.error("[INTERCONNECTOR] Failed to register with master:", response.error);
          console.error("[INTERCONNECTOR] Registration error details:", {
            error: response.error,
            message: response.error?.message,
            data: response.data
          });
          return;
        }

        // Store node ID for future heartbeats
        const returnedNodeId = response.data?.node_id;
        console.log("[INTERCONNECTOR] Registration successful, node_id:", returnedNodeId);
        if (returnedNodeId && returnedNodeId !== nodeId) {
          setNodeId(returnedNodeId);
          localStorage.setItem('interconnector_node_id', returnedNodeId);
          console.log("[INTERCONNECTOR] Stored node_id:", returnedNodeId);
        }
      } catch (error) {
        console.error("[INTERCONNECTOR] Error registering client:", error);
        console.error("[INTERCONNECTOR] Registration exception:", {
          name: error.name,
          message: error.message,
          stack: error.stack
        });
      }
    };

    registerClient();

    // Track if component is still mounted to prevent state updates after unmount
    let isMounted = true;

    // Send heartbeat every 60 seconds
    const heartbeatInterval = setInterval(async () => {
      if (!isMounted) return; // Skip if unmounted
      if (nodeId && config.master_url && config.master_api_key) {
        try {
          console.log(`[INTERCONNECTOR] Sending heartbeat for node_id: ${nodeId}`);
          const heartbeatResponse = await interconnectorApi.sendHeartbeat(
            config.master_url,
            config.master_api_key,
            nodeId
          );
          if (!isMounted) return; // Check again after await
          console.log("[INTERCONNECTOR] Heartbeat response:", heartbeatResponse);
          if (heartbeatResponse.error) {
            console.warn("[INTERCONNECTOR] Heartbeat failed:", heartbeatResponse.error);
          }
        } catch (error) {
          if (!isMounted) return; // Check after await
          console.error("[INTERCONNECTOR] Heartbeat failed:", error);
          console.error("[INTERCONNECTOR] Heartbeat error details:", {
            name: error.name,
            message: error.message
          });
        }
      } else {
        console.warn("[INTERCONNECTOR] Cannot send heartbeat - missing nodeId, master_url, or api_key");
      }
    }, 60000); // 60 seconds

    return () => {
      isMounted = false;
      clearInterval(heartbeatInterval);
    };
  }, [config.is_enabled, config.node_mode, config.master_url, config.master_api_key, config.node_name, config.sync_entities, nodeId]);

  // Auto-fill node name from system branding name when node_name is empty
  useEffect(() => {
    if (!config.node_name && systemName) {
      setConfig(prev => ({ ...prev, node_name: systemName }));
    }
  }, [systemName]); // Only run when systemName changes (e.g. on initial load)

  // Auto-sync if enabled
  useEffect(() => {
    if (!config.is_enabled || config.node_mode !== "client") {
      return;
    }

    if (!config.auto_sync_enabled) {
      return;
    }

    const syncInterval = config.sync_interval_seconds
      ? config.sync_interval_seconds * 1000
      : 300000; // Default 5 minutes

    const autoSyncInterval = setInterval(async () => {
      if (!isSyncingData) {
        try {
          // Auto-sync only syncs entities, not files (files are manual only for safety)
          await interconnectorApi.triggerManualSync("bidirectional", config.sync_entities, false, null);
          console.log("Auto-sync completed");
        } catch (error) {
          console.error("Auto-sync failed:", error);
        }
      }
    }, syncInterval);

    return () => clearInterval(autoSyncInterval);
  }, [config.is_enabled, config.node_mode, config.auto_sync_enabled, config.sync_interval_seconds, config.sync_entities, isSyncingData]);

  const loadConfiguration = async () => {
    setIsLoading(true);
    try {
      const response = await interconnectorApi.getInterconnectorConfig();
      if (response.error === 'Not Found') {
        // Plugin is not enabled or available - this is OK
        console.warn('Interconnector plugin not enabled');
        // Keep default config - don't call setConfig since state is already initialized
      } else if (response.error) {
        showMessage(`Failed to load configuration: ${response.error}`, "error");
      } else if (response.data?.config) {
        const loadedConfig = response.data.config;
        setConfig(loadedConfig);
        savedConfigRef.current = { ...loadedConfig };

        // If client mode and enabled, trigger immediate registration
        if (loadedConfig.is_enabled && loadedConfig.node_mode === "client" && 
            loadedConfig.master_url && loadedConfig.master_api_key && loadedConfig.node_name) {
          // Trigger registration after a short delay to ensure state is updated
          setTimeout(() => {
            const registerClient = async () => {
              try {
                // Get actual network IP from backend
                let networkIp = null;
                let networkPort = 5000;
                try {
                  const networkInfo = await interconnectorApi.getNetworkInfo();
                  if (networkInfo.data) {
                    networkIp = networkInfo.data.network_ip;
                    networkPort = networkInfo.data.port || 5000;
                  }
                } catch (netErr) {
                  console.warn("[INTERCONNECTOR] Could not get network info:", netErr);
                }
                
                const capabilities = {
                  cpu_cores: navigator.hardwareConcurrency || 4,
                  memory_mb: (navigator.deviceMemory || 4) * 1024,
                  gpu_available: false,
                };

                const registrationData = {
                  node_name: loadedConfig.node_name,
                  node_id: nodeId,
                  node_mode: "client",
                  sync_entities: loadedConfig.sync_entities || ["clients", "projects"],
                  capabilities: capabilities,
                  // Use actual network IP from backend
                  client_ip: networkIp,
                  client_port: networkPort,
                };

                const regResponse = await interconnectorApi.registerWithMaster(
                  loadedConfig.master_url,
                  loadedConfig.master_api_key,
                  registrationData
                );

                if (!regResponse.error) {
                  const returnedNodeId = regResponse.data?.node_id;
                  if (returnedNodeId && returnedNodeId !== nodeId) {
                    setNodeId(returnedNodeId);
                    localStorage.setItem('interconnector_node_id', returnedNodeId);
                  }
                }
              } catch (error) {
                console.error("Error registering client on load:", error);
              }
            };
            registerClient();
          }, 500);
        }
      } else if (response.config) {
        // Fallback for direct config in response
        setConfig(response.config);
      }
    } catch (error) {
      // Silently handle if plugin is not enabled
      if (error.message !== 'Not Found') {
        showMessage(`Error loading configuration: ${error.message}`, "error");
      }
    } finally {
      setIsLoading(false);
    }
  };

  const loadStatus = async () => {
    try {
      const response = await interconnectorApi.getInterconnectorStatus();
      if (response.error === 'Not Found') {
        // Plugin is not enabled - this is OK
        return;
      }
      if (!response.error && (response.data?.status || response.status)) {
        setStatus(response.data?.status || response.status);
      }
    } catch (error) {
      const errorMsg = error.message || error.data?.message || '';
      if (errorMsg.includes('not enabled') || errorMsg.includes('Not enabled') || error.message === 'Not Found') {
        // Silently handle disabled state
        return;
      }
      console.error("Error loading status:", error);
    }
  };

  const loadNodes = async () => {
    try {
      console.log("[INTERCONNECTOR] Loading nodes list...");
      const response = await interconnectorApi.getInterconnectorNodes();
      console.log("[INTERCONNECTOR] Nodes response:", response);
      
      if (response.error === 'Not Found') {
        // Plugin is not enabled - this is OK
        setNodes([]);
        return;
      }
      if (!response.error && (response.data?.nodes || response.nodes)) {
        const nodesList = response.data?.nodes || response.nodes;
        console.log(`[INTERCONNECTOR] Loaded ${nodesList.length} nodes:`, nodesList);
        setNodes(nodesList);
      } else {
        console.warn("[INTERCONNECTOR] No nodes in response:", response);
        setNodes([]);
      }
    } catch (error) {
      const errorMsg = error.message || error.data?.message || '';
      if (errorMsg.includes('not enabled') || errorMsg.includes('Not enabled') || error.message === 'Not Found') {
        // Silently handle disabled state
        setNodes([]);
        return;
      }
      console.error("[INTERCONNECTOR] Error loading nodes:", error);
      setNodes([]);
    }
  };

  const loadProfiles = async () => {
    try {
      const response = await interconnectorApi.getSyncProfiles();
      const list = response.data?.profiles || response.profiles || [];
      setProfiles(list);
      if (!selectedProfile && list.length > 0) {
        const defaultProfile = list.find(p => p.is_default) || list[0];
        setSelectedProfile(defaultProfile?.name);
      }
    } catch (error) {
      console.warn("[INTERCONNECTOR] Failed to load sync profiles:", error.message);
    }
  };

  const loadApprovals = async () => {
    try {
      setIsLoadingApprovals(true);
      const response = await interconnectorApi.getPendingApprovals();
      const approvals = response.data?.approvals || response.approvals || [];
      setPendingApprovals(approvals);
    } catch (error) {
      console.warn("[INTERCONNECTOR] Failed to load approvals:", error.message);
    } finally {
      setIsLoadingApprovals(false);
    }
  };

  const handleToggleEnabled = () => {
    const newEnabledState = !config.is_enabled;
    // Auto-fill node name from system branding name if empty when enabling
    let updatedNodeName = config.node_name;
    if (newEnabledState && !config.node_name?.trim()) {
      if (systemName) {
        updatedNodeName = systemName;
      } else {
        showMessage("Please set a System Name in Branding before enabling the interconnector", "warning");
        return;
      }
    }

    if (!newEnabledState) {
      // Disabling — save immediately (no validation issues when disabling)
      handleDisableInterconnector();
    } else {
      // Enabling — just show the config fields so user can fill them in, then Save
      setConfig(prev => ({ ...prev, is_enabled: true, node_name: updatedNodeName }));
    }
  };

  const handleDisableInterconnector = async () => {
    setIsSaving(true);
    try {
      const configToSave = { ...config, is_enabled: false };
      const response = await interconnectorApi.updateInterconnectorConfig(configToSave);
      if (response.error === 'Not Found') {
        showMessage("Interconnector plugin is not enabled on this server", "warning");
        setIsSaving(false);
        return;
      }
      if (response.error) {
        throw new Error(response.error?.message || response.error || 'Unknown error');
      }
      const newConfig = response.data?.config || response.config || configToSave;
      setConfig(newConfig);
      savedConfigRef.current = { ...newConfig };
      showMessage("Network Interconnector disabled", "success");
    } catch (error) {
      setConfig(prev => ({ ...prev, is_enabled: true }));
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      showMessage(`Failed to disable: ${errorMsg}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleCancelConfiguration = () => {
    if (savedConfigRef.current) {
      setConfig({ ...savedConfigRef.current });
    } else {
      // No saved config — revert to defaults (disabled)
      setConfig({
        is_enabled: false,
        node_mode: "client",
        node_name: "",
        master_url: "",
        master_api_key: "",
        api_key_hash: "",
        require_api_key: true,
        auto_sync_enabled: false,
        sync_interval_seconds: 300,
        sync_entities: ["clients", "projects", "websites"],
      });
    }
  };

  const handleSaveConfiguration = async () => {
    setIsSaving(true);
    try {
      // Auto-format master URL if provided (add http:// and port if missing)
      const configToSave = { ...config };
      if (configToSave.master_url) {
        configToSave.master_url = interconnectorApi.formatMasterUrl(configToSave.master_url);
        // Update local state with formatted URL
        setConfig(prev => ({ ...prev, master_url: configToSave.master_url }));
      }
      
      const response = await interconnectorApi.updateInterconnectorConfig(configToSave);
      if (response.error === 'Not Found') {
        showMessage("Interconnector plugin is not enabled on this server", "warning");
        setIsSaving(false);
        return;
      }
      if (response.error) {
        throw new Error(response.error?.message || response.error || 'Unknown error');
      }
      
      // Update config with saved values if returned
      if (response.data?.config) {
        setConfig(response.data.config);
      } else if (response.config) {
        setConfig(response.config);
      }
      
      // Update savedConfigRef so Cancel reverts to this state
      savedConfigRef.current = { ...config, ...configToSave };
      if (response.data?.config) savedConfigRef.current = { ...response.data.config };
      else if (response.config) savedConfigRef.current = { ...response.config };

      showMessage("Configuration saved successfully", "success");

      // Reload status after enabling
      if (configToSave.is_enabled) {
        await loadStatus();
        if (configToSave.node_mode === "master") {
          await loadNodes();
        }
      }

      // If client mode, automatically register with master
      if (configToSave.is_enabled && configToSave.node_mode === "client" && configToSave.master_url && configToSave.master_api_key && configToSave.node_name) {
        try {
          // Get actual network IP from backend
          let networkIp = null;
          let networkPort = 5000;
          try {
            const networkInfo = await interconnectorApi.getNetworkInfo();
            if (networkInfo.data) {
              networkIp = networkInfo.data.network_ip;
              networkPort = networkInfo.data.port || 5000;
            }
          } catch (netErr) {
            console.warn("[INTERCONNECTOR] Could not get network info:", netErr);
          }
          
          const capabilities = {
            cpu_cores: navigator.hardwareConcurrency || 4,
            memory_mb: (navigator.deviceMemory || 4) * 1024,
            gpu_available: false,
          };

          const registrationData = {
            node_name: configToSave.node_name,
            node_id: nodeId,
            node_mode: "client",
            sync_entities: configToSave.sync_entities || ["clients", "projects"],
            capabilities: capabilities,
            // Use actual network IP from backend
            client_ip: networkIp,
            client_port: networkPort,
          };

          const regResponse = await interconnectorApi.registerWithMaster(
            configToSave.master_url,
            configToSave.master_api_key,
            registrationData
          );

          if (regResponse.error) {
            console.warn("Failed to register with master:", regResponse.error);
            showMessage("Configuration saved, but registration with master failed. Please check connection.", "warning");
          } else {
            const returnedNodeId = regResponse.data?.node_id;
            if (returnedNodeId && returnedNodeId !== nodeId) {
              setNodeId(returnedNodeId);
              localStorage.setItem('interconnector_node_id', returnedNodeId);
            }
            showMessage("Configuration saved and registered with master successfully", "success");
          }
        } catch (error) {
          console.error("Error registering client:", error);
          showMessage("Configuration saved, but registration failed. Please check connection.", "warning");
        }
      }

      // Reload status after saving
      if (config.is_enabled) {
        await loadStatus();
        if (config.node_mode === "master") {
          await loadNodes();
        }
      }
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      showMessage(`Failed to save configuration: ${errorMsg}`, "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleGenerateApiKey = async () => {
    try {
      const response = await interconnectorApi.generateInterconnectorApiKey();
      if (response.error) {
        throw new Error(response.error?.message || response.error || 'Unknown error');
      }

      const apiData = response.data || response;
      setGeneratedApiKey(apiData.api_key);
      setShowApiKey(true);

      // Update config with new hash
      setConfig(prev => ({
        ...prev,
        api_key_hash: apiData.api_key_hash,
      }));

      showMessage("API key generated successfully. Copy it now - it won't be shown again!", "success");
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      showMessage(`Failed to generate API key: ${errorMsg}`, "error");
    }
  };

  const handleDisconnectNode = async (nodeId) => {
    if (!window.confirm(`Disconnect node ${nodeId}?`)) return;

    try {
      await interconnectorApi.disconnectInterconnectorNode(nodeId);
      showMessage(`Node ${nodeId} disconnected`, "success");
      await loadNodes();
    } catch (error) {
      showMessage(`Failed to disconnect node: ${error.message}`, "error");
    }
  };

  const handleSyncFiles = async () => {
    setIsSyncingCode(true);
    setCodeSyncResults(null);

    console.log("[INTERCONNECTOR] Starting file sync operation...");
    console.log("[INTERCONNECTOR] Config:", {
      master_url: config.master_url,
      node_name: config.node_name,
      node_mode: config.node_mode
    });

    try {
      // Sync files only (pull direction, files only, no entities)
      console.log("[INTERCONNECTOR] Calling triggerManualSync with:", {
        direction: "pull",
        entities: [],
        syncFiles: true,
        filePaths: null,
        profile: selectedProfile
      });

      const response = await interconnectorApi.triggerManualSync(
        "pull",  // Pull files from master
        [],      // No entities to sync
        true,    // Sync files flag
        null,    // Use default file paths
        { profile: selectedProfile }
      );

      console.log("[INTERCONNECTOR] Sync response received:", response);

      if (response.error) {
        console.error("[INTERCONNECTOR] Sync response contains error:", response.error);
        throw new Error(response.error?.message || response.error || 'Unknown error');
      }

      const syncData = response.data || response;
      setCodeSyncResults(syncData);

      console.log("[INTERCONNECTOR] Sync data parsed:", syncData);

      // Check for file sync results
      const fileSummary = syncData.files?.summary || {};
      const fileProcessed = fileSummary.total_processed || 0;
      const fileCreated = fileSummary.total_created || 0;
      const fileUpdated = fileSummary.total_updated || 0;
      const fileBackedUp = fileSummary.total_backed_up || 0;

      console.log("[INTERCONNECTOR] File sync summary:", {
        processed: fileProcessed,
        created: fileCreated,
        updated: fileUpdated,
        backedUp: fileBackedUp,
        errors: fileSummary.total_errors || 0
      });

      let message = `File sync complete: ${fileProcessed} files processed`;
      if (fileCreated > 0) message += `, ${fileCreated} created`;
      if (fileUpdated > 0) message += `, ${fileUpdated} updated`;
      if (fileBackedUp > 0) message += `, ${fileBackedUp} backed up`;

      showMessage(message, "success");
    } catch (error) {
      console.error("[INTERCONNECTOR] File sync error caught:", error);
      console.error("[INTERCONNECTOR] Error details:", {
        message: error.message,
        data: error.data,
        stack: error.stack
      });
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      showMessage(`File sync failed: ${errorMsg}`, "error");
      setCodeSyncResults({ error: errorMsg });
    } finally {
      setIsSyncingCode(false);
      console.log("[INTERCONNECTOR] File sync operation completed");
    }
  };

  const handleManualSync = async (direction = "bidirectional") => {
    setIsSyncingData(true);
    setDataSyncResults(null);

    console.log("[INTERCONNECTOR] Starting manual sync operation...");
    console.log("[INTERCONNECTOR] Sync parameters:", {
      direction,
      entities: config.sync_entities,
      node_name: config.node_name,
      master_url: config.master_url
    });

    try {
      const response = await interconnectorApi.triggerManualSync(
        direction, 
        config.sync_entities,
        false,  // Data sync only
        null,  // Use default file paths
        { profile: selectedProfile }
      );

      console.log("[INTERCONNECTOR] Manual sync response received:", response);

      if (response.error) {
        console.error("[INTERCONNECTOR] Sync response contains error:", response.error);
        throw new Error(response.error?.message || response.error || 'Unknown error');
      }

      const syncData = response.data || response;
      setDataSyncResults(syncData);

      console.log("[INTERCONNECTOR] Sync data parsed:", syncData);

      const summary = syncData.summary || {};
      const totalProcessed = summary.total_processed || 0;
      const totalCreated = summary.total_created || 0;
      const totalUpdated = summary.total_updated || 0;
      
      // Check for file sync results
      console.log("[INTERCONNECTOR] Entity sync summary:", summary);

      let message = `Sync complete: ${totalProcessed} entities processed, ${totalCreated} created, ${totalUpdated} updated`;

      showMessage(message, "success");
    } catch (error) {
      console.error("[INTERCONNECTOR] Manual sync error caught:", error);
      console.error("[INTERCONNECTOR] Error details:", {
        message: error.message,
        data: error.data,
        stack: error.stack
      });
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      showMessage(`Sync failed: ${errorMsg}`, "error");
      setDataSyncResults({ error: errorMsg });
    } finally {
      setIsSyncingData(false);
      console.log("[INTERCONNECTOR] Manual sync operation completed");
    }
  };

  const handleApprovalDecision = async (approvalId, decision, approvedFiles = []) => {
    try {
      setIsLoadingApprovals(true);
      const payload = { decision };
      if (approvedFiles && approvedFiles.length > 0) {
        payload.approved_files = approvedFiles;
      }
      await interconnectorApi.decideApproval(approvalId, payload);
      showMessage(`Approval ${decision}d`, "success");
      await loadApprovals();
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      showMessage(`Failed to ${decision} approval: ${errorMsg}`, "error");
    } finally {
      setIsLoadingApprovals(false);
    }
  };

  const handleBroadcastAll = async () => {
    try {
      setBroadcastResult(null);
      const payload = {
        target_clients: "all",
        sync_type: "both",
        profile: selectedProfile,
        require_approval: true,
      };
      const res = await interconnectorApi.broadcastPush(payload);
      setBroadcastResult(res.data || res);
      showMessage("Broadcast started", "success");
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      showMessage(`Failed to start broadcast: ${errorMsg}`, "error");
    }
  };

  const handleTestFileScanning = async () => {
    setIsTestingFileScan(true);
    setFileScanTestResult(null);

    console.log("[INTERCONNECTOR] Starting file scan test...");

    try {
      const response = await interconnectorApi.testFileScanning();

      if (response.error) {
        const errorMsg = response.error?.message || response.error || 'Unknown error';
        setFileScanTestResult({ success: false, message: errorMsg });
        showMessage(`File scan test failed: ${errorMsg}`, "error");
      } else {
        const testData = response.data || response;
        const totalFiles = testData.total_files || 0;
        const totalSizeMB = testData.total_size_mb || 0;
        const projectRoot = testData.project_root || "unknown";
        
        const message = `File scan test successful: Found ${totalFiles} files (${totalSizeMB} MB)`;
        setFileScanTestResult({ 
          success: true, 
          message,
          details: testData
        });
        showMessage(message, "success");
      }
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      setFileScanTestResult({ success: false, message: errorMsg });
      showMessage(`File scan test failed: ${errorMsg}`, "error");
    } finally {
      setIsTestingFileScan(false);
    }
  };

  const handleLoadOutputsIndex = async () => {
    setIsLoadingOutputs(true);
    setOutputsIndex(null);

    console.log("[INTERCONNECTOR] Loading outputs index...");

    try {
      const response = await interconnectorApi.fetchOutputsIndex(100);

      if (response.error) {
        const errorMsg = response.error?.message || response.error || 'Unknown error';
        showMessage(`Failed to load outputs index: ${errorMsg}`, "error");
        setOutputsIndex({ error: errorMsg });
      } else {
        const data = response.data || response;
        setOutputsIndex(data);
        const count = data.count || (data.files ? data.files.length : 0);
        showMessage(`Outputs available: ${count} file(s)`, "success");
      }
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      showMessage(`Failed to load outputs index: ${errorMsg}`, "error");
      setOutputsIndex({ error: errorMsg });
    } finally {
      setIsLoadingOutputs(false);
    }
  };

  const handleTestClientConnection = async (nodeId, nodeName) => {
    const testingSet = new Set(testingClientNodes);
    testingSet.add(nodeId);
    setTestingClientNodes(testingSet);

    console.log(`[INTERCONNECTOR] Testing connection to client node: ${nodeName} (${nodeId})`);

    try {
      const response = await interconnectorApi.testClientConnection(nodeId);

      if (response.error) {
        const errorMsg = response.error?.message || response.error || 'Unknown error';
        setClientTestResults(prev => ({
          ...prev,
          [nodeId]: { success: false, message: errorMsg }
        }));
        showMessage(`Connection test failed for ${nodeName}: ${errorMsg}`, "error");
      } else {
        const testData = response.data || response;
        const status = testData.connection_status || "unknown";
        const latency = testData.latency_ms || 0;
        
        let message = `Connection test ${status === 'success' ? 'successful' : status}`;
        if (latency > 0) {
          message += ` (${latency}ms latency)`;
        }
        
        setClientTestResults(prev => ({
          ...prev,
          [nodeId]: {
            success: status === 'success',
            message,
            details: testData
          }
        }));
        
        if (status === 'success') {
          showMessage(`Successfully connected to ${nodeName}: ${latency}ms`, "success");
        } else {
          showMessage(`Connection test ${status} for ${nodeName}`, "warning");
        }
      }
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      setClientTestResults(prev => ({
        ...prev,
        [nodeId]: { success: false, message: errorMsg }
      }));
      showMessage(`Connection test failed for ${nodeName}: ${errorMsg}`, "error");
    } finally {
      const testingSet = new Set(testingClientNodes);
      testingSet.delete(nodeId);
      setTestingClientNodes(testingSet);
    }
  };

  const handleTestConnection = async () => {
    if (!config.master_url || !config.master_api_key) {
      showMessage("Please enter Master URL and API Key first", "warning");
      return;
    }

    setIsTestingConnection(true);
    setConnectionTestResult(null);

    try {
      const response = await interconnectorApi.testMasterConnection(
        config.master_url,
        config.master_api_key
      );

      if (response.error) {
        const errorMsg = response.error?.message || response.error || 'Unknown error';
        setConnectionTestResult({ success: false, message: errorMsg });
        showMessage(`Connection test failed: ${errorMsg}`, "error");
      } else {
        const testData = response.data || response;
        const quality = testData.quality || '';
        const latency = testData.latency_ms || 0;
        const message = quality 
          ? `Connection successful (${quality}, ${latency}ms)` 
          : "Connection successful";
        setConnectionTestResult({ success: true, message });
        showMessage(message, "success");
      }
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || 'Unknown error';
      setConnectionTestResult({ success: false, message: errorMsg });
      showMessage(`Connection test failed: ${errorMsg}`, "error");
    } finally {
      setIsTestingConnection(false);
    }
  };

  const getStatusColor = (nodeStatus) => {
    if (!nodeStatus) return "default";
    if (nodeStatus === "active") return "success";
    if (nodeStatus === "inactive") return "error";
    return "warning";
  };

  const getStatusIcon = (nodeStatus) => {
    if (!nodeStatus) return <WarningIcon />;
    if (nodeStatus === "active") return <SuccessIcon />;
    if (nodeStatus === "inactive") return <ErrorIcon />;
    return <WarningIcon />;
  };

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" p={4}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box>
      {/* Header */}
      <Box display="flex" alignItems="center" gap={2} mb={2}>
        <NetworkIcon fontSize="large" color="primary" />
        <Box>
          <Typography variant="h6">Network Interconnector</Typography>
          <Typography variant="body2" color="text.secondary">
            Connect multiple Guaardvark instances on your local network
          </Typography>
        </Box>
      </Box>

      <Divider sx={{ mb: 3 }} />

      {/* Main Enable/Disable Toggle */}
      <Paper elevation={2} sx={{ p: 2, mb: 3 }}>
        <Typography variant="subtitle1" gutterBottom>
          Network Interconnector Status
        </Typography>
        <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mb: 1 }}>
          <Chip
            label={config.is_enabled ? "Disable Network Interconnector" : "Enable Network Interconnector"}
            color={config.is_enabled ? 'primary' : 'default'}
            onClick={handleToggleEnabled}
            disabled={isSaving}
            variant={config.is_enabled ? 'filled' : 'outlined'}
            size="small"
            sx={{
              '& .MuiChip-label': {
                color: config.is_enabled ? 'inherit' : 'text.secondary'
              }
            }}
            icon={isSaving ? <CircularProgress size={16} /> : undefined}
          />
        </Box>
        <Typography variant="caption" color="text.secondary">
          Allow this instance to connect with other Guaardvark nodes
        </Typography>
      </Paper>

      {config.is_enabled && (
      <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" gutterBottom>
          Configuration
        </Typography>

        <Grid container spacing={3}>
          {/* Node Mode Selection */}
          <Grid item xs={12} md={6}>
            <FormControl fullWidth>
              <InputLabel>Node Mode</InputLabel>
              <Select
                value={config.node_mode}
                label="Node Mode"
                onChange={(e) => setConfig({ ...config, node_mode: e.target.value })}
              >
                <MenuItem value="master">Master (Server)</MenuItem>
                <MenuItem value="client">Client</MenuItem>
              </Select>
            </FormControl>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
              {config.node_mode === "master"
                ? "This system will act as the central server. Other systems will connect to it."
                : "This system will connect to a master server and sync data with it."}
            </Typography>
          </Grid>

          {/* Node Name */}
          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              label="Node Name"
              value={config.node_name}
              onChange={(e) => setConfig({ ...config, node_name: e.target.value })}
              placeholder={systemName || "My-LLM-System"}
              helperText={!config.node_name ? "Required - will use System Name from Branding if empty" : "A friendly name to identify this node"}
              error={!config.node_name && config.is_enabled}
            />
          </Grid>

              {/* Master Mode Configuration */}
              {config.node_mode === "master" && (
                <>
                  <Grid item xs={12}>
                    <Divider sx={{ my: 1 }}>
                      <Chip label="Master Server Settings" size="small" />
                    </Divider>
                  </Grid>

                  <Grid item xs={12}>
                    <Box>
                      <Typography variant="subtitle2" gutterBottom>
                        API Key Authentication
                      </Typography>
                      <FormControlLabel
                        control={
                          <Switch
                            checked={config.require_api_key !== false}
                            onChange={(e) => setConfig({ ...config, require_api_key: e.target.checked })}
                          />
                        }
                        label={
                          <Box>
                            <Typography variant="body2" fontWeight="bold">
                              Require API Key Authentication
                            </Typography>
                            <Typography variant="caption" color="text.secondary">
                              Disable for trusted local networks (less secure)
                            </Typography>
                          </Box>
                        }
                      />
                      {!config.require_api_key && (
                        <Alert severity="warning" sx={{ mt: 1 }}>
                          API key authentication is disabled. Only use this on trusted local networks.
                        </Alert>
                      )}
                    </Box>
                  </Grid>

                  <Grid item xs={12}>
                    <Box>
                      <Typography variant="subtitle2" gutterBottom>
                        API Key (for clients to connect)
                      </Typography>
                      {config.api_key_hash ? (
                        <Box>
                          <Alert severity="success" sx={{ mb: 2 }}>
                            API Key is configured (hash: {config.api_key_hash.substring(0, 16)}...)
                          </Alert>
                          {generatedApiKey && showApiKey && (
                            <Alert severity="warning" sx={{ mb: 2 }}>
                              <Typography variant="body2" fontWeight="bold">
                                Save this API key now - it won't be shown again:
                              </Typography>
                              <Typography
                                variant="body2"
                                sx={{
                                  fontFamily: "monospace",
                                  bgcolor: "background.paper",
                                  p: 1,
                                  borderRadius: 1,
                                  mt: 1,
                                }}
                              >
                                {generatedApiKey}
                              </Typography>
                            </Alert>
                          )}
                        </Box>
                      ) : (
                        <Alert severity="info" sx={{ mb: 2 }}>
                          No API key configured. Generate one to allow clients to connect.
                        </Alert>
                      )}
                      <Box display="flex" gap={2} alignItems="center" flexWrap="wrap" sx={{ mb: 2 }}>
                        <Button
                          variant="outlined"
                          size="small"
                          startIcon={<KeyIcon />}
                          onClick={handleGenerateApiKey}
                        >
                          {config.api_key_hash ? "Regenerate API Key" : "Generate API Key"}
                        </Button>
                        <Button
                          variant="outlined"
                          size="small"
                          onClick={handleTestFileScanning}
                          disabled={isTestingFileScan}
                          startIcon={isTestingFileScan ? <CircularProgress size={16} /> : <ManualSyncIcon />}
                        >
                          {isTestingFileScan ? "Testing..." : "Test File Scanning"}
                        </Button>
                        {fileScanTestResult && (
                          <Chip
                            label={fileScanTestResult.message}
                            color={fileScanTestResult.success ? "success" : "error"}
                            icon={fileScanTestResult.success ? <SuccessIcon /> : <ErrorIcon />}
                            size="small"
                          />
                        )}
                      </Box>
                      {fileScanTestResult && fileScanTestResult.success && fileScanTestResult.details && (
                        <Box sx={{ mt: 2 }}>
                          <Alert severity="success">
                            <Typography variant="body2" fontWeight="bold">
                              File Scan Results:
                            </Typography>
                            <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                              Total Files: {fileScanTestResult.details.total_files || 0}
                            </Typography>
                            <Typography variant="caption" display="block">
                              Total Size: {fileScanTestResult.details.total_size_mb || 0} MB
                            </Typography>
                            <Typography variant="caption" display="block">
                              Project Root: {fileScanTestResult.details.project_root || "unknown"}
                            </Typography>
                            {fileScanTestResult.details.critical_files && (
                              <>
                                <Typography variant="caption" display="block" sx={{ mt: 1, fontWeight: "bold" }}>
                                  Critical Files Check:
                                </Typography>
                                {fileScanTestResult.details.critical_files.found && fileScanTestResult.details.critical_files.found.length > 0 && (
                                  <Typography variant="caption" display="block" sx={{ ml: 2, color: "success.main" }}>
                                    Found: {fileScanTestResult.details.critical_files.found.join(", ")}
                                  </Typography>
                                )}
                                {fileScanTestResult.details.critical_files.missing && fileScanTestResult.details.critical_files.missing.length > 0 && (
                                  <Typography variant="caption" display="block" sx={{ ml: 2, color: "error.main" }}>
                                    Missing: {fileScanTestResult.details.critical_files.missing.join(", ")}
                                  </Typography>
                                )}
                              </>
                            )}
                            {fileScanTestResult.details.by_directory && Object.keys(fileScanTestResult.details.by_directory).length > 0 && (
                              <>
                                <Typography variant="caption" display="block" sx={{ mt: 1, fontWeight: "bold" }}>
                                  Files by Directory:
                                </Typography>
                                {Object.entries(fileScanTestResult.details.by_directory)
                                  .slice(0, 5)
                                  .map(([dir, info]) => (
                                    <Typography key={dir} variant="caption" display="block" sx={{ ml: 2 }}>
                                      {dir}: {info.count} files ({Math.round(info.size / 1024)} KB)
                                    </Typography>
                                  ))}
                              </>
                            )}
                            {fileScanTestResult.details.sample_files && fileScanTestResult.details.sample_files.length > 0 && (
                              <>
                                <Typography variant="caption" display="block" sx={{ mt: 1, fontWeight: "bold" }}>
                                  Sample Files (paths):
                                </Typography>
                                {fileScanTestResult.details.sample_files.slice(0, 5).map((f, idx) => (
                                  <Typography key={idx} variant="caption" display="block" sx={{ ml: 2, fontFamily: "monospace", fontSize: "0.7rem" }}>
                                    {f.path}
                                  </Typography>
                                ))}
                                {fileScanTestResult.details.sample_files.length > 5 && (
                                  <Typography variant="caption" display="block" sx={{ ml: 2, fontStyle: "italic" }}>
                                    ... and {fileScanTestResult.details.sample_files.length - 5} more
                                  </Typography>
                                )}
                              </>
                            )}
                          </Alert>
                        </Box>
                      )}
                    </Box>
                  </Grid>
                </>
              )}

              {/* Client Mode Configuration */}
              {config.node_mode === "client" && (
                <>
                  <Grid item xs={12}>
                    <Divider sx={{ my: 1 }}>
                      <Chip label="Master Server Connection" size="small" />
                    </Divider>
                  </Grid>

                  <Grid item xs={12} md={6}>
                    <TextField
                      fullWidth
                      label="Master Server Address"
                      value={config.master_url}
                      onChange={(e) => setConfig({ ...config, master_url: e.target.value })}
                      placeholder="192.168.1.100"
                      helperText="IP or hostname (http:// and :5000 added automatically if omitted)"
                    />
                  </Grid>

                  <Grid item xs={12} md={6}>
                    <TextField
                      fullWidth
                      label="Master API Key"
                      type="password"
                      value={config.master_api_key}
                      onChange={(e) => setConfig({ ...config, master_api_key: e.target.value })}
                      placeholder="Enter API key from master"
                      helperText="API key provided by the master server"
                    />
                  </Grid>

                  <Grid item xs={12}>
                    <Box display="flex" gap={2} alignItems="center" flexWrap="wrap">
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={handleTestConnection}
                        disabled={isTestingConnection || !config.master_url || (config.require_api_key !== false && !config.master_api_key)}
                        startIcon={isTestingConnection ? <CircularProgress size={16} /> : <NetworkIcon />}
                      >
                        Test Connection
                      </Button>
                      {connectionTestResult && (
                        <Chip
                          label={connectionTestResult.message}
                          color={connectionTestResult.success ? "success" : "error"}
                          icon={connectionTestResult.success ? <SuccessIcon /> : <ErrorIcon />}
                          size="small"
                        />
                      )}
                    </Box>
                  </Grid>
                </>
              )}

              {/* Sync Settings */}
              <Grid item xs={12}>
                <Divider sx={{ my: 1 }}>
                  <Chip label="Sync Settings" size="small" />
                </Divider>
              </Grid>

              <Grid item xs={12} md={6}>
                <Typography variant="body2" gutterBottom>
                  Sync Options
                </Typography>
                <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mb: 1 }}>
                  <Chip
                    label="Enable Auto-Sync"
                    color={config.auto_sync_enabled ? 'primary' : 'default'}
                    onClick={(e) => setConfig({ ...config, auto_sync_enabled: !config.auto_sync_enabled })}
                    variant={config.auto_sync_enabled ? 'filled' : 'outlined'}
                    size="small"
                    sx={{
                      '& .MuiChip-label': {
                        color: config.auto_sync_enabled ? 'inherit' : 'text.secondary'
                      }
                    }}
                  />
                </Box>
                <Typography variant="caption" color="text.secondary" display="block">
                  Automatically sync data at regular intervals
                </Typography>
              </Grid>

              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  type="number"
                  label="Sync Interval (seconds)"
                  value={config.sync_interval_seconds}
                  onChange={(e) => {
                    const value = e.target.value;
                    // Handle empty string and parse value
                    const parsed = value === '' ? 300 : parseInt(value, 10);
                    // Ensure value is within valid range and not NaN
                    const clamped = isNaN(parsed) ? 300 : Math.max(60, Math.min(3600, parsed));
                    setConfig({ ...config, sync_interval_seconds: clamped });
                  }}
                  disabled={!config.auto_sync_enabled}
                  inputProps={{ min: 60, max: 3600 }}
                  helperText="How often to sync (60-3600 seconds)"
                />
              </Grid>

              <Grid item xs={12}>
                <Typography variant="subtitle2" gutterBottom>
                  Entities to Sync
                </Typography>
                <Box display="flex" gap={1} flexWrap="wrap">
                {["clients", "projects", "websites"].map((entity) => (
                    <Chip
                      key={entity}
                      label={entity.charAt(0).toUpperCase() + entity.slice(1)}
                      color={config.sync_entities.includes(entity) ? "primary" : "default"}
                      onClick={() => {
                        const newEntities = config.sync_entities.includes(entity)
                          ? config.sync_entities.filter((e) => e !== entity)
                          : [...config.sync_entities, entity];
                        setConfig({ ...config, sync_entities: newEntities });
                      }}
                      variant={config.sync_entities.includes(entity) ? "filled" : "outlined"}
                    />
                  ))}
                </Box>
              </Grid>

          {/* Save / Cancel Buttons */}
          <Grid item xs={12}>
            <Box sx={{ display: 'flex', gap: 1 }}>
              <Button
                variant="contained"
                size="small"
                onClick={handleSaveConfiguration}
                disabled={isSaving || !config.node_name}
                startIcon={isSaving ? <CircularProgress size={16} /> : <SuccessIcon />}
              >
                {isSaving ? "Saving..." : "Save Configuration"}
              </Button>
              <Button
                variant="outlined"
                size="small"
                onClick={handleCancelConfiguration}
                disabled={isSaving}
              >
                Cancel
              </Button>
            </Box>
          </Grid>
        </Grid>
      </Paper>
      )}

      {config.is_enabled && (
        <>
          {/* Status Card */}
          {status && (
            <Card sx={{ mb: 3, bgcolor: "background.paper" }}>
              <CardContent>
                <Typography variant="subtitle1" gutterBottom>
                  Current Status
                </Typography>
                <Grid container spacing={2}>
                  <Grid item xs={12} sm={6} md={3}>
                    <Box>
                      <Typography variant="caption" color="text.secondary">
                        Mode
                      </Typography>
                      <Chip
                        label={status.node_mode || "N/A"}
                        color={status.node_mode === "master" ? "primary" : "secondary"}
                        size="small"
                        icon={status.node_mode === "master" ? <StorageIcon /> : <ComputerIcon />}
                      />
                    </Box>
                  </Grid>
                  <Grid item xs={12} sm={6} md={3}>
                    <Box>
                      <Typography variant="caption" color="text.secondary">
                        Node Name
                      </Typography>
                      <Typography variant="body2">{status.node_name || "Not Set"}</Typography>
                    </Box>
                  </Grid>
                  <Grid item xs={12} sm={6} md={3}>
                    <Box>
                      <Typography variant="caption" color="text.secondary">
                        Auto-Sync
                      </Typography>
                      <Chip
                        label={status.auto_sync_enabled ? "Enabled" : "Disabled"}
                        color={status.auto_sync_enabled ? "success" : "default"}
                        size="small"
                      />
                    </Box>
                  </Grid>
                  <Grid item xs={12} sm={6} md={3}>
                    <Box>
                      <Typography variant="caption" color="text.secondary">
                        Last Sync
                      </Typography>
                      <Typography variant="body2">
                        {status.last_sync_time
                          ? new Date(status.last_sync_time).toLocaleString()
                          : "Never"}
                      </Typography>
                    </Box>
                  </Grid>
                </Grid>
              </CardContent>
            </Card>
          )}

          {/* Client Mode: Simplified Update Panel + Optional Advanced Controls */}
          {config.node_mode === "client" && config.master_url && (
            <>
              {/* Simplified Code Update Panel - Always shown for clients */}
              <ClientUpdatePanel
                masterUrl={config.master_url}
                masterApiKey={config.master_api_key}
                isEnabled={config.is_enabled}
              />

              {/* Toggle for Advanced Options */}
              <Box sx={{ mt: 2, mb: 1 }}>
                <Button
                  variant="text"
                  size="small"
                  startIcon={<SettingsIcon />}
                  onClick={() => setShowAdvancedClientView(!showAdvancedClientView)}
                  sx={{ color: "text.secondary" }}
                >
                  {showAdvancedClientView ? "Hide Advanced Options" : "Show Advanced Options (Data Sync)"}
                </Button>
              </Box>

              {/* Advanced Manual Sync Controls - Hidden by default */}
              {showAdvancedClientView && config.master_api_key && (
                <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
                  <Typography variant="h6" gutterBottom>
                    Advanced Sync Options
                  </Typography>
                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    Manual data synchronization and advanced file operations
                  </Typography>

                  <Box sx={{ mb: 2 }}>
                    <Typography variant="subtitle2" gutterBottom>
                      Sync Profile
                    </Typography>
                    <FormControl fullWidth size="small" sx={{ maxWidth: 320 }}>
                      <InputLabel>Profile</InputLabel>
                      <Select
                        label="Profile"
                        value={selectedProfile || ""}
                        onChange={(e) => setSelectedProfile(e.target.value || null)}
                      >
                        <MenuItem value="">(default)</MenuItem>
                        {profiles.map((p) => (
                          <MenuItem key={p.id} value={p.name}>
                            {p.name} {p.is_default ? "(default)" : ""}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                    <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
                      Profiles scope which entities and file paths are synced.
                    </Typography>
                  </Box>

                  {/* Data sync */}
                  <Box sx={{ mb: 3 }}>
                    <Typography variant="subtitle2" gutterBottom>
                      Data Sync
                    </Typography>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      Sync clients, projects, and websites with the master
                    </Typography>
                    <Box display="flex" gap={1} flexWrap="wrap" mt={1}>
                      <Button
                        variant="contained"
                        size="small"
                        onClick={() => handleManualSync("bidirectional")}
                        disabled={isSyncingData}
                        startIcon={isSyncingData ? <CircularProgress size={16} /> : <SyncIcon />}
                      >
                        Sync (Bidirectional)
                      </Button>
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={() => handleManualSync("pull")}
                        disabled={isSyncingData}
                      >
                        Pull from Master
                      </Button>
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={() => handleManualSync("push")}
                        disabled={isSyncingData}
                      >
                        Push to Master
                      </Button>
                    </Box>

                    {isSyncingData && (
                      <Box mt={2}>
                        <LinearProgress />
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1 }}>
                          Synchronizing data...
                        </Typography>
                      </Box>
                    )}

                    {dataSyncResults && !isSyncingData && (
                      <Box mt={2}>
                        {dataSyncResults.error ? (
                          <Alert severity="error">{dataSyncResults.error}</Alert>
                        ) : (
                          <Alert severity="success">
                            <Typography variant="body2">
                              Data sync completed
                            </Typography>
                            {dataSyncResults.summary && (
                              <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                                Processed: {dataSyncResults.summary.total_processed || 0} |
                                Created: {dataSyncResults.summary.total_created || 0} |
                                Updated: {dataSyncResults.summary.total_updated || 0}
                                {dataSyncResults.summary.total_conflicts > 0 &&
                                  ` | Conflicts: ${dataSyncResults.summary.total_conflicts}`}
                              </Typography>
                            )}
                          </Alert>
                        )}
                      </Box>
                    )}
                  </Box>

                  <Divider sx={{ my: 2 }} />

                  {/* Outputs browse */}
                  <Box sx={{ mb: 3 }}>
                    <Typography variant="subtitle2" gutterBottom>
                      Outputs (browse-only)
                    </Typography>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      View generated outputs from the master without copying files
                    </Typography>
                    <Button
                      variant="outlined"
                      size="small"
                      onClick={handleLoadOutputsIndex}
                      disabled={isLoadingOutputs}
                      startIcon={isLoadingOutputs ? <CircularProgress size={16} /> : <StorageIcon />}
                      sx={{ mr: 1 }}
                    >
                      {isLoadingOutputs ? "Loading..." : "Load Outputs Index"}
                    </Button>
                    {outputsIndex && (
                      <Box sx={{ mt: 2 }}>
                        {outputsIndex.error ? (
                          <Alert severity="error">{outputsIndex.error}</Alert>
                        ) : (
                          <Alert severity="info">
                            <Typography variant="caption">
                              {`Found ${outputsIndex.count || (outputsIndex.files ? outputsIndex.files.length : 0)} file(s)`}
                            </Typography>
                            {outputsIndex.files && outputsIndex.files.length > 0 && (
                              <Box sx={{ mt: 1 }}>
                                {outputsIndex.files.slice(0, 5).map((f, idx) => (
                                  <Typography key={idx} variant="caption" display="block" sx={{ fontFamily: "monospace" }}>
                                    {f.path} ({Math.round((f.size || 0) / 1024)} KB)
                                  </Typography>
                                ))}
                                {outputsIndex.files.length > 5 && (
                                  <Typography variant="caption" display="block" sx={{ fontStyle: "italic" }}>
                                    ...and {outputsIndex.files.length - 5} more
                                  </Typography>
                                )}
                              </Box>
                            )}
                          </Alert>
                        )}
                      </Box>
                    )}
                  </Box>

                  <Divider sx={{ my: 2 }} />

                  {/* Legacy Code sync (kept for backward compatibility) */}
                  <Box>
                    <Typography variant="subtitle2" gutterBottom>
                      Legacy Code Sync
                    </Typography>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      Alternative method to pull code updates (use the simplified panel above instead)
                    </Typography>
                    <Button
                      variant="outlined"
                      color="secondary"
                      size="small"
                      onClick={handleSyncFiles}
                      disabled={isSyncingCode}
                      sx={{ mb: 1 }}
                    >
                      {isSyncingCode ? "Pulling Code..." : "Pull Code (Legacy)"}
                    </Button>

                    {isSyncingCode && (
                      <Box mt={2}>
                        <LinearProgress />
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1 }}>
                          Pulling code updates...
                        </Typography>
                      </Box>
                    )}

                    {codeSyncResults && !isSyncingCode && (
                      <Box mt={2}>
                        {codeSyncResults.error ? (
                          <Alert severity="error">{codeSyncResults.error}</Alert>
                        ) : (
                          <Alert severity="success">
                            <Typography variant="body2">
                              Code sync completed
                            </Typography>
                            {codeSyncResults.files && (
                              <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                                Files: {codeSyncResults.files.summary?.total_processed || 0} processed |
                                {codeSyncResults.files.summary?.total_created || 0} created |
                                {codeSyncResults.files.summary?.total_updated || 0} updated
                                {codeSyncResults.files.summary?.total_backed_up > 0 &&
                                  ` | ${codeSyncResults.files.summary.total_backed_up} backed up`}
                              </Typography>
                            )}
                          </Alert>
                        )}
                      </Box>
                    )}
                  </Box>

                  <Divider sx={{ my: 2 }} />

                  {/* Pending Approvals */}
                  <Box>
                    <Box display="flex" justifyContent="space-between" alignItems="center" mb={1}>
                      <Typography variant="subtitle2">Pending File Approvals</Typography>
                      <Button size="small" onClick={loadApprovals} startIcon={<RefreshIcon />} disabled={isLoadingApprovals}>
                        Refresh
                      </Button>
                    </Box>
                    {isLoadingApprovals ? (
                      <LinearProgress />
                    ) : pendingApprovals.length === 0 ? (
                      <Alert severity="info">No pending approvals</Alert>
                    ) : (
                      <TableContainer>
                        <Table size="small">
                          <TableHead>
                            <TableRow>
                              <TableCell>ID</TableCell>
                              <TableCell>Files</TableCell>
                              <TableCell>Received</TableCell>
                              <TableCell align="right">Actions</TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {pendingApprovals.map((appr) => {
                              const files = appr.files_data || [];
                              return (
                                <TableRow key={appr.id}>
                                  <TableCell>{appr.id}</TableCell>
                                  <TableCell>{files.length} files</TableCell>
                                  <TableCell>
                                    {appr.received_at ? new Date(appr.received_at).toLocaleString() : "-"}
                                  </TableCell>
                                  <TableCell align="right">
                                    <Button
                                      size="small"
                                      variant="contained"
                                      color="success"
                                      onClick={() => handleApprovalDecision(appr.id, "approve")}
                                      sx={{ mr: 1 }}
                                    >
                                      Approve
                                    </Button>
                                    <Button
                                      size="small"
                                      variant="outlined"
                                      color="error"
                                      onClick={() => handleApprovalDecision(appr.id, "decline")}
                                    >
                                      Decline
                                    </Button>
                                  </TableCell>
                                </TableRow>
                              );
                            })}
                          </TableBody>
                        </Table>
                      </TableContainer>
                    )}
                  </Box>
                </Paper>
              )}
            </>
          )}

          {/* Connected Nodes (Master Mode Only) */}
          {config.node_mode === "master" && (
            <Paper elevation={2} sx={{ p: 3 }}>
              <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
                <Typography variant="h6">Connected Nodes</Typography>
                <Box display="flex" gap={1} alignItems="center">
                  <Button variant="contained" size="small" onClick={handleBroadcastAll}>
                    Broadcast Sync (all)
                  </Button>
                  <IconButton onClick={loadNodes} size="small">
                    <RefreshIcon />
                  </IconButton>
                </Box>
              </Box>

              {nodes.length === 0 ? (
                <Alert severity="info">No client nodes connected yet</Alert>
              ) : (
                <TableContainer>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>Status</TableCell>
                        <TableCell>Node Name</TableCell>
                        <TableCell>Host</TableCell>
                        <TableCell>Last Heartbeat</TableCell>
                        <TableCell align="right">Actions</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {nodes.map((node) => {
                        const isTesting = testingClientNodes.has(node.node_id);
                        const testResult = clientTestResults[node.node_id];
                        return (
                          <TableRow key={node.node_id}>
                            <TableCell>
                              <Chip
                                label={node.status || "unknown"}
                                color={getStatusColor(node.status)}
                                icon={getStatusIcon(node.status)}
                                size="small"
                              />
                            </TableCell>
                            <TableCell>{node.node_name || node.node_id}</TableCell>
                            <TableCell>
                              {node.host}:{node.port}
                            </TableCell>
                            <TableCell>
                              {node.last_heartbeat
                                ? new Date(node.last_heartbeat).toLocaleString()
                                : "Never"}
                            </TableCell>
                            <TableCell align="right">
                              <Box display="flex" gap={1} justifyContent="flex-end" alignItems="center">
                                <Tooltip title="Test connection to client">
                                  <IconButton
                                    size="small"
                                    onClick={() => handleTestClientConnection(node.node_id, node.node_name)}
                                    disabled={isTesting}
                                    color={testResult?.success ? "success" : testResult?.success === false ? "error" : "default"}
                                  >
                                    {isTesting ? (
                                      <CircularProgress size={16} />
                                    ) : (
                                      <NetworkIcon fontSize="small" />
                                    )}
                                  </IconButton>
                                </Tooltip>
                                {testResult && (
                                  <Tooltip title={testResult.message}>
                                    <Chip
                                      label={testResult.success ? "Connected" : "Failed"}
                                      color={testResult.success ? "success" : "error"}
                                      size="small"
                                      sx={{ height: 24 }}
                                    />
                                  </Tooltip>
                                )}
                                <Tooltip title="Disconnect node">
                                  <IconButton
                                    size="small"
                                    onClick={() => handleDisconnectNode(node.node_id)}
                                    color="error"
                                  >
                                    <DisconnectIcon fontSize="small" />
                                  </IconButton>
                                </Tooltip>
                              </Box>
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </TableContainer>
              )}
              {broadcastResult && (
                <Alert severity="info" sx={{ mt: 2 }}>
                  Broadcast started: {broadcastResult.broadcast_id || broadcastResult.data?.broadcast_id || "pending"}
                </Alert>
              )}
            </Paper>
          )}
        </>
      )}
    </Box>
  );
};

export default InterconnectorSettings;

