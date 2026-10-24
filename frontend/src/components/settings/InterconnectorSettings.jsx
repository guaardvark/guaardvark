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
import InterconnectorEnrolments from "./InterconnectorEnrolments";
import { uiLocale, useUiTranslation } from "../../i18n";

const debugLog = (...args) => {
  if (import.meta.env.DEV) {
    console.debug(...args);
  }
};

const redactValue = (value) => {
  if (!value) return "";
  const text = String(value);
  return text.length <= 8 ? "***" : `${text.slice(0, 4)}...${text.slice(-4)}`;
};

// Client: whether the master has let this machine join, and the key
// fingerprint the person approving it there compares with.
const FleetMembership = ({ identity }) => {
  const { t } = useUiTranslation();
  if (!identity) return null;
  const fleet = identity.fleet || {};
  const key = identity.key_fingerprint;
  const keyLine = key ? t("settingsInterconnector.fleet.thisKey", { key }) : t("settingsInterconnector.fleet.keyMadeAtEnrolment");
  if (fleet.status === "approved") {
    return (
      <Alert severity="success">
        {t("settingsInterconnector.fleet.approved")}{" "}
        {fleet.master_fingerprint ? `${t("settingsInterconnector.fleet.masterKey", { fingerprint: fleet.master_fingerprint })} ` : ""}
        {keyLine}
      </Alert>
    );
  }
  if (fleet.status === "pending") {
    return (
      <Alert severity="info">
        {t("settingsInterconnector.fleet.pending")} {keyLine}
      </Alert>
    );
  }
  if (fleet.status === "refused") {
    return (
      <Alert severity="warning">
        {fleet.message ? t("settingsInterconnector.fleet.refusedWithReason", { reason: fleet.message }) : t("settingsInterconnector.fleet.refused")} {keyLine}
      </Alert>
    );
  }
  return (
    <Alert severity="info">
      {t("settingsInterconnector.fleet.notEnrolled")} {keyLine}
    </Alert>
  );
};

const InterconnectorSettings = () => {
  const { t } = useUiTranslation();
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

  // Last-saved config: the ref backs Cancel; the state drives everything that
  // runs on its own (registration, heartbeat, auto-sync), so the form's
  // unsaved values never reach the master and only take effect on Save.
  const savedConfigRef = useRef(null);
  const [savedConfig, setSavedConfig] = useState(null);
  const rememberSaved = (cfg) => {
    savedConfigRef.current = { ...cfg };
    setSavedConfig({ ...cfg });
  };

  // The registration last sent to the master (or in flight), so the same one
  // is not sent twice: once by Save, which registers itself to report the
  // result, and again by the registration effect reacting to the new saved
  // config; or again after the master hands back this node's id.
  const lastRegistration = useRef(null);
  const registrationKey = (cfg, id) => JSON.stringify([
    cfg.master_url, cfg.master_api_key, cfg.node_name, cfg.sync_entities || null, id || null,
  ]);

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
  
  // Node ID for client registration. Seeded from localStorage, but upgraded to
  // this machine's STABLE, IP-independent id (~/.guaardvark/node_id) below so a
  // node keeps one identity across browsers, cache clears, DHCP leases, and VPN
  // paths — instead of registering as a brand-new node each time.
  const [nodeId, setNodeId] = useState(() => {
    const stored = localStorage.getItem('interconnector_node_id');
    return stored || null;
  });

  // This machine's key fingerprint and its place in the fleet (waiting for
  // approval, joined, refused), from its own backend.
  const [localIdentity, setLocalIdentity] = useState(null);

  const refreshLocalIdentity = async () => {
    const result = await interconnectorApi.getLocalIdentity();
    if (result?.data) {
      setLocalIdentity(result.data);
    }
    return result?.data || null;
  };

  // Prefer the machine-stable identity from this box's own backend.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const identity = await refreshLocalIdentity();
      // The id the master enrolled this machine under, else the machine id.
      const machineId = identity?.fleet?.enrolled_node_id || identity?.node_id;
      if (!cancelled && machineId) {
        setNodeId(machineId);
        localStorage.setItem('interconnector_node_id', machineId);
      }
    })();
    return () => { cancelled = true; };
  }, []);

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

  // Client node: enrolment and heartbeat, from the saved configuration only.
  // This machine's backend sends both, signed with its node key; the page
  // asks it to on load and when this node's id changes (Save asks on its
  // own), then every minute while open so the status shown stays current.
  useEffect(() => {
    const config = savedConfig; // the saved copy, not the form being edited
    if (!config?.is_enabled || config.node_mode !== "client") {
      return;
    }

    if (!config.master_url || !config.master_api_key || !config.node_name) {
      return;
    }

    // Track if component is still mounted to prevent state updates after unmount
    let isMounted = true;

    const key = registrationKey(config, nodeId);
    const enrolStep = async () => {
      const response = await interconnectorApi.enrolWithMaster();
      if (!isMounted) return;
      if (response.error) {
        if (lastRegistration.current === key) lastRegistration.current = null;
        console.warn("[INTERCONNECTOR] Enrolment step failed:", response.error);
      }
      const identity = await refreshLocalIdentity();
      const enrolledId = response.data?.node_id || identity?.fleet?.enrolled_node_id;
      if (isMounted && enrolledId && enrolledId !== nodeId) {
        // The master knows this machine by the id it assigned.
        lastRegistration.current = registrationKey(config, enrolledId);
        setNodeId(enrolledId);
        localStorage.setItem('interconnector_node_id', enrolledId);
        debugLog("[INTERCONNECTOR] Stored node_id", { nodeId: redactValue(enrolledId) });
      }
    };

    if (lastRegistration.current !== key) {
      lastRegistration.current = key;
      enrolStep();
    }

    const heartbeatInterval = setInterval(() => {
      if (isMounted) enrolStep();
    }, 60000); // 60 seconds

    return () => {
      isMounted = false;
      clearInterval(heartbeatInterval);
    };
  }, [savedConfig, nodeId]);

  // Auto-fill node name from system branding name when node_name is empty
  useEffect(() => {
    if (!config.node_name && systemName) {
      setConfig(prev => ({ ...prev, node_name: systemName }));
    }
  }, [systemName]); // Only run when systemName changes (e.g. on initial load)

  // Auto-sync if enabled in the saved configuration: the toggle, interval and
  // entity choices take effect on Save, like registration.
  useEffect(() => {
    const config = savedConfig; // the saved copy, not the form being edited
    if (!config?.is_enabled || config.node_mode !== "client") {
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
          debugLog("Auto-sync completed");
        } catch (error) {
          console.error("Auto-sync failed:", error);
        }
      }
    }, syncInterval);

    return () => clearInterval(autoSyncInterval);
  }, [savedConfig, isSyncingData]);

  const loadConfiguration = async () => {
    setIsLoading(true);
    try {
      const response = await interconnectorApi.getInterconnectorConfig();
      if (response.error === 'Not Found') {
        // Plugin is not enabled or available - this is OK
        console.warn('Interconnector plugin not enabled');
        // Keep default config - don't call setConfig since state is already initialized
      } else if (response.error) {
        showMessage(t("settingsInterconnector.messages.loadConfigFailed", { error: response.error }), "error");
      } else if (response.data?.config) {
        const loadedConfig = response.data.config;
        setConfig(loadedConfig);
        // The registration effect registers a client node from this saved
        // config; nothing is sent from here.
        rememberSaved(loadedConfig);
      } else if (response.config) {
        // Fallback for direct config in response
        setConfig(response.config);
        rememberSaved(response.config);
      }
    } catch (error) {
      // Silently handle if plugin is not enabled
      if (error.message !== 'Not Found') {
        showMessage(t("settingsInterconnector.messages.loadConfigError", { error: error.message }), "error");
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
      debugLog("[INTERCONNECTOR] Loading nodes list");
      const response = await interconnectorApi.getInterconnectorNodes();
      debugLog("[INTERCONNECTOR] Nodes response", {
        success: response?.success,
        count: response?.data?.nodes?.length || response?.nodes?.length || 0,
      });
      
      if (response.error === 'Not Found') {
        // Plugin is not enabled - this is OK
        setNodes([]);
        return;
      }
      if (!response.error && (response.data?.nodes || response.nodes)) {
        const nodesList = response.data?.nodes || response.nodes;
        debugLog("[INTERCONNECTOR] Loaded nodes", { count: nodesList.length });
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
      // Otherwise the node list just looks empty.
      showMessage(t("settingsInterconnector.messages.nodesFailed", { error: errorMsg || t("settingsInterconnector.requestFailed") }), "error");
    }
  };

  // A disabled interconnector plugin answers "Not Found" / "not enabled":
  // that is the off state, not a failure to report.
  const isDisabledPluginError = (error) => {
    const msg = error?.message || error?.data?.message || "";
    return msg === "Not Found" || /not enabled/i.test(msg);
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
      if (!isDisabledPluginError(error)) {
        showMessage(t("settingsInterconnector.messages.profilesFailed", { error: error.message || t("settingsInterconnector.requestFailed") }), "error");
      }
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
      if (!isDisabledPluginError(error)) {
        showMessage(t("settingsInterconnector.messages.approvalsFailed", { error: error.message || t("settingsInterconnector.requestFailed") }), "error");
      }
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
        showMessage(t("settingsInterconnector.messages.setSystemName"), "warning");
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
        showMessage(t("settingsInterconnector.messages.pluginDisabled"), "warning");
        setIsSaving(false);
        return;
      }
      if (response.error) {
        throw new Error(response.error?.message || response.error || t("settingsInterconnector.unknownError"));
      }
      const newConfig = response.data?.config || response.config || configToSave;
      setConfig(newConfig);
      rememberSaved(newConfig);
      showMessage(t("settingsInterconnector.messages.disabled"), "success");
    } catch (error) {
      setConfig(prev => ({ ...prev, is_enabled: true }));
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      showMessage(t("settingsInterconnector.messages.disableFailed", { error: errorMsg }), "error");
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
        showMessage(t("settingsInterconnector.messages.pluginDisabled"), "warning");
        setIsSaving(false);
        return;
      }
      if (response.error) {
        throw new Error(response.error?.message || response.error || t("settingsInterconnector.unknownError"));
      }
      
      // Update config with saved values if returned
      if (response.data?.config) {
        setConfig(response.data.config);
      } else if (response.config) {
        setConfig(response.config);
      }
      
      // Cancel reverts to this, and registration, heartbeat and auto-sync now
      // run from it. A client node is registered below, by this handler, so it
      // can report the result; claiming the registration first keeps the
      // registration effect from sending the same one when it sees the save.
      const saved = response.data?.config || response.config || { ...config, ...configToSave };
      const registersNow = saved.is_enabled && saved.node_mode === "client"
        && saved.master_url && saved.master_api_key && saved.node_name;
      if (registersNow) lastRegistration.current = registrationKey(saved, nodeId);
      rememberSaved(saved);

      showMessage(t("settingsInterconnector.messages.saved"), "success");

      // Reload status after enabling
      if (configToSave.is_enabled) {
        await loadStatus();
        if (configToSave.node_mode === "master") {
          await loadNodes();
        }
      }

      // If client mode, enrol with the master now (this machine's backend
      // signs the request with its node key)
      if (registersNow) {
        const key = lastRegistration.current;
        const enrolment = await interconnectorApi.enrolWithMaster();
        const step = enrolment.data || {};
        await refreshLocalIdentity();
        if (enrolment.error || step.error) {
          if (lastRegistration.current === key) lastRegistration.current = null;
          const reason = step.message || enrolment.error || step.error;
          showMessage(t("settingsInterconnector.messages.savedNotEnrolled", { reason }), "warning");
        } else {
          if (step.node_id && step.node_id !== nodeId) {
            lastRegistration.current = registrationKey(saved, step.node_id);
            setNodeId(step.node_id);
            localStorage.setItem('interconnector_node_id', step.node_id);
          }
          if (step.pending) {
            showMessage(
              t("settingsInterconnector.messages.savedPending", { fingerprint: step.fingerprint }),
              "info"
            );
          } else {
            showMessage(t("settingsInterconnector.messages.savedInFleet"), "success");
          }
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
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      showMessage(t("settingsInterconnector.messages.saveFailed", { error: errorMsg }), "error");
    } finally {
      setIsSaving(false);
    }
  };

  const handleGenerateApiKey = async () => {
    try {
      const response = await interconnectorApi.generateInterconnectorApiKey();
      if (response.error) {
        throw new Error(response.error?.message || response.error || t("settingsInterconnector.unknownError"));
      }

      const apiData = response.data || response;
      setGeneratedApiKey(apiData.api_key);
      setShowApiKey(true);

      // Update config with new hash
      setConfig(prev => ({
        ...prev,
        api_key_hash: apiData.api_key_hash,
      }));

      showMessage(t("settingsInterconnector.messages.keyGenerated"), "success");
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      showMessage(t("settingsInterconnector.messages.keyFailed", { error: errorMsg }), "error");
    }
  };

  const handleDisconnectNode = async (nodeId) => {
    if (!window.confirm(t("settingsInterconnector.messages.confirmDisconnect", { node: nodeId }))) return;

    try {
      await interconnectorApi.disconnectInterconnectorNode(nodeId);
      showMessage(t("settingsInterconnector.messages.nodeDisconnected", { node: nodeId }), "success");
      await loadNodes();
    } catch (error) {
      showMessage(t("settingsInterconnector.messages.disconnectFailed", { error: error.message }), "error");
    }
  };

  const handleSyncFiles = async () => {
    setIsSyncingCode(true);
    setCodeSyncResults(null);

    debugLog("[INTERCONNECTOR] Starting file sync operation", {
      masterUrl: redactValue(config.master_url),
      node_name: config.node_name,
      node_mode: config.node_mode
    });

    try {
      // Sync files only (pull direction, files only, no entities)
      debugLog("[INTERCONNECTOR] Calling triggerManualSync", {
        direction: "pull",
        entityCount: 0,
        syncFiles: true,
        profile: selectedProfile
      });

      const response = await interconnectorApi.triggerManualSync(
        "pull",  // Pull files from master
        [],      // No entities to sync
        true,    // Sync files flag
        null,    // Use default file paths
        { profile: selectedProfile }
      );

      debugLog("[INTERCONNECTOR] Sync response received", {
        success: response?.success,
        hasData: Boolean(response?.data),
      });

      if (response.error) {
        console.error("[INTERCONNECTOR] Sync response contains error:", response.error);
        throw new Error(response.error?.message || response.error || t("settingsInterconnector.unknownError"));
      }

      const syncData = response.data || response;
      setCodeSyncResults(syncData);

      debugLog("[INTERCONNECTOR] Sync data parsed", {
        hasFiles: Boolean(syncData.files),
      });

      // Check for file sync results
      const fileSummary = syncData.files?.summary || {};
      const fileProcessed = fileSummary.total_processed || 0;
      const fileCreated = fileSummary.total_created || 0;
      const fileUpdated = fileSummary.total_updated || 0;
      const fileBackedUp = fileSummary.total_backed_up || 0;

      debugLog("[INTERCONNECTOR] File sync summary", {
        processed: fileProcessed,
        created: fileCreated,
        updated: fileUpdated,
        backedUp: fileBackedUp,
        errors: fileSummary.total_errors || 0
      });

      const message = [
        t("settingsInterconnector.messages.fileSyncComplete", { count: fileProcessed }),
        fileCreated > 0 && t("settingsInterconnector.messages.filesCreated", { count: fileCreated }),
        fileUpdated > 0 && t("settingsInterconnector.messages.filesUpdated", { count: fileUpdated }),
        fileBackedUp > 0 && t("settingsInterconnector.messages.filesBackedUp", { count: fileBackedUp }),
      ].filter(Boolean).join(", ");

      showMessage(message, "success");
    } catch (error) {
      console.error("[INTERCONNECTOR] File sync error caught:", error);
      console.error("[INTERCONNECTOR] Error details:", {
        message: error.message,
        data: error.data,
        stack: error.stack
      });
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      showMessage(t("settingsInterconnector.messages.fileSyncFailed", { error: errorMsg }), "error");
      setCodeSyncResults({ error: errorMsg });
    } finally {
      setIsSyncingCode(false);
      debugLog("[INTERCONNECTOR] File sync operation completed");
    }
  };

  const handleManualSync = async (direction = "bidirectional") => {
    setIsSyncingData(true);
    setDataSyncResults(null);

    debugLog("[INTERCONNECTOR] Starting manual sync operation", {
      direction,
      entityCount: config.sync_entities?.length || 0,
      node_name: config.node_name,
      masterUrl: redactValue(config.master_url)
    });

    try {
      const response = await interconnectorApi.triggerManualSync(
        direction, 
        config.sync_entities,
        false,  // Data sync only
        null,  // Use default file paths
        { profile: selectedProfile }
      );

      debugLog("[INTERCONNECTOR] Manual sync response received", {
        success: response?.success,
        hasData: Boolean(response?.data),
      });

      if (response.error) {
        console.error("[INTERCONNECTOR] Sync response contains error:", response.error);
        throw new Error(response.error?.message || response.error || t("settingsInterconnector.unknownError"));
      }

      const syncData = response.data || response;
      setDataSyncResults(syncData);

      debugLog("[INTERCONNECTOR] Sync data parsed", {
        hasSummary: Boolean(syncData.summary),
      });

      const summary = syncData.summary || {};
      const totalProcessed = summary.total_processed || 0;
      const totalCreated = summary.total_created || 0;
      const totalUpdated = summary.total_updated || 0;
      
      // Check for file sync results
      debugLog("[INTERCONNECTOR] Entity sync summary", summary);

      let message = t("settingsInterconnector.messages.syncComplete", { processed: totalProcessed, created: totalCreated, updated: totalUpdated });

      showMessage(message, "success");
    } catch (error) {
      console.error("[INTERCONNECTOR] Manual sync error caught:", error);
      console.error("[INTERCONNECTOR] Error details:", {
        message: error.message,
        data: error.data,
        stack: error.stack
      });
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      showMessage(t("settingsInterconnector.messages.syncFailed", { error: errorMsg }), "error");
      setDataSyncResults({ error: errorMsg });
    } finally {
      setIsSyncingData(false);
      debugLog("[INTERCONNECTOR] Manual sync operation completed");
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
      showMessage(
        { approve: t("settingsInterconnector.messages.approvalDone.approve"), decline: t("settingsInterconnector.messages.approvalDone.decline") }[decision],
        "success",
      );
      await loadApprovals();
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      showMessage(
        {
          approve: t("settingsInterconnector.messages.approvalFailed.approve", { error: errorMsg }),
          decline: t("settingsInterconnector.messages.approvalFailed.decline", { error: errorMsg }),
        }[decision],
        "error",
      );
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
      showMessage(t("settingsInterconnector.messages.broadcastStarted"), "success");
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      showMessage(t("settingsInterconnector.messages.broadcastFailed", { error: errorMsg }), "error");
    }
  };

  const handleTestFileScanning = async () => {
    setIsTestingFileScan(true);
    setFileScanTestResult(null);

    debugLog("[INTERCONNECTOR] Starting file scan test");

    try {
      const response = await interconnectorApi.testFileScanning();

      if (response.error) {
        const errorMsg = response.error?.message || response.error || t("settingsInterconnector.unknownError");
        setFileScanTestResult({ success: false, message: errorMsg });
        showMessage(t("settingsInterconnector.messages.fileScanFailed", { error: errorMsg }), "error");
      } else {
        const testData = response.data || response;
        const totalFiles = testData.total_files || 0;
        const totalSizeMB = testData.total_size_mb || 0;
        const _projectRoot = testData.project_root || "unknown";
        
        const message = t("settingsInterconnector.messages.fileScanOk", { count: totalFiles, size: totalSizeMB });
        setFileScanTestResult({ 
          success: true, 
          message,
          details: testData
        });
        showMessage(message, "success");
      }
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      setFileScanTestResult({ success: false, message: errorMsg });
      showMessage(t("settingsInterconnector.messages.fileScanFailed", { error: errorMsg }), "error");
    } finally {
      setIsTestingFileScan(false);
    }
  };

  const handleLoadOutputsIndex = async () => {
    setIsLoadingOutputs(true);
    setOutputsIndex(null);

    debugLog("[INTERCONNECTOR] Loading outputs index");

    try {
      const response = await interconnectorApi.fetchOutputsIndex(100);

      if (response.error) {
        const errorMsg = response.error?.message || response.error || t("settingsInterconnector.unknownError");
        showMessage(t("settingsInterconnector.messages.outputsFailed", { error: errorMsg }), "error");
        setOutputsIndex({ error: errorMsg });
      } else {
        const data = response.data || response;
        setOutputsIndex(data);
        const count = data.count || (data.files ? data.files.length : 0);
        showMessage(t("settingsInterconnector.messages.outputsAvailable", { count }), "success");
      }
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      showMessage(t("settingsInterconnector.messages.outputsFailed", { error: errorMsg }), "error");
      setOutputsIndex({ error: errorMsg });
    } finally {
      setIsLoadingOutputs(false);
    }
  };

  const handleTestClientConnection = async (nodeId, nodeName) => {
    const testingSet = new Set(testingClientNodes);
    testingSet.add(nodeId);
    setTestingClientNodes(testingSet);

    debugLog("[INTERCONNECTOR] Testing connection to client node", {
      nodeName,
      nodeId: redactValue(nodeId),
    });

    try {
      const response = await interconnectorApi.testClientConnection(nodeId);

      if (response.error) {
        const errorMsg = response.error?.message || response.error || t("settingsInterconnector.unknownError");
        setClientTestResults(prev => ({
          ...prev,
          [nodeId]: { success: false, message: errorMsg }
        }));
        showMessage(t("settingsInterconnector.messages.clientTestFailed", { node: nodeName, error: errorMsg }), "error");
      } else {
        const testData = response.data || response;
        const status = testData.connection_status || "unknown";
        const latency = testData.latency_ms || 0;
        
        let message;
        if (status === 'success') {
          message = latency > 0 ? t("settingsInterconnector.messages.clientTestOkLatency", { latency }) : t("settingsInterconnector.messages.clientTestOk");
        } else {
          message = latency > 0
            ? t("settingsInterconnector.messages.clientTestStatusLatency", { status, latency })
            : t("settingsInterconnector.messages.clientTestStatus", { status });
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
          showMessage(t("settingsInterconnector.messages.clientConnected", { node: nodeName, latency }), "success");
        } else {
          showMessage(t("settingsInterconnector.messages.clientTestStatusFor", { status, node: nodeName }), "warning");
        }
      }
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      setClientTestResults(prev => ({
        ...prev,
        [nodeId]: { success: false, message: errorMsg }
      }));
      showMessage(t("settingsInterconnector.messages.clientTestFailed", { node: nodeName, error: errorMsg }), "error");
    } finally {
      const testingSet = new Set(testingClientNodes);
      testingSet.delete(nodeId);
      setTestingClientNodes(testingSet);
    }
  };

  const handleTestConnection = async () => {
    if (!config.master_url || !config.master_api_key) {
      showMessage(t("settingsInterconnector.messages.enterMasterFirst"), "warning");
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
        const errorMsg = response.error?.message || response.error || t("settingsInterconnector.unknownError");
        setConnectionTestResult({ success: false, message: errorMsg });
        showMessage(t("settingsInterconnector.messages.testFailed", { error: errorMsg }), "error");
      } else {
        const testData = response.data || response;
        const quality = testData.quality || '';
        const latency = testData.latency_ms || 0;
        const message = quality 
          ? t("settingsInterconnector.messages.testOkQuality", { quality, latency })
          : t("settingsInterconnector.messages.testOk");
        setConnectionTestResult({ success: true, message });
        showMessage(message, "success");
      }
    } catch (error) {
      const errorMsg = error.data?.error?.message || error.data?.message || error.message || t("settingsInterconnector.unknownError");
      setConnectionTestResult({ success: false, message: errorMsg });
      showMessage(t("settingsInterconnector.messages.testFailed", { error: errorMsg }), "error");
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
      {/* Update panel pinned to the top so users in client mode can apply
          updates without scrolling past server config. Same component as
          before — just hoisted out of the deeper client-mode block. */}
      {config.node_mode === "client" && config.master_url && (
        <Box sx={{ mb: 3 }}>
          <ClientUpdatePanel
            masterUrl={config.master_url}
            masterApiKey={config.master_api_key}
            isEnabled={config.is_enabled}
          />
        </Box>
      )}

      {/* Header */}
      <Box display="flex" alignItems="center" gap={2} mb={2}>
        <NetworkIcon fontSize="large" color="primary" />
        <Box>
          <Typography variant="h6">{t("settingsInterconnector.ui.title")}</Typography>
          <Typography variant="body2" color="text.secondary">
            {t("settingsInterconnector.ui.subtitle")}
          </Typography>
        </Box>
      </Box>

      <Divider sx={{ mb: 3 }} />

      {/* Main Enable/Disable Toggle */}
      <Paper elevation={2} sx={{ p: 2, mb: 3 }}>
        <Typography variant="subtitle1" gutterBottom>
          {t("settingsInterconnector.ui.status")}
        </Typography>
        <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mb: 1 }}>
          <Chip
            label={config.is_enabled ? t("settingsInterconnector.ui.disable") : t("settingsInterconnector.ui.enable")}
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
          {t("settingsInterconnector.ui.statusHelp")}
        </Typography>
      </Paper>

      {config.is_enabled && (
      <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
        <Typography variant="h6" gutterBottom>
          {t("settingsInterconnector.ui.configuration")}
        </Typography>

        <Grid container spacing={3}>
          {/* Node Mode Selection */}
          <Grid item xs={12} md={6}>
            <FormControl fullWidth>
              <InputLabel>{t("settingsInterconnector.ui.nodeMode")}</InputLabel>
              <Select
                value={config.node_mode}
                label={t("settingsInterconnector.ui.nodeMode")}
                onChange={(e) => setConfig({ ...config, node_mode: e.target.value })}
              >
                <MenuItem value="master">{t("settingsInterconnector.ui.modeMaster")}</MenuItem>
                <MenuItem value="client">{t("settingsInterconnector.ui.modeClient")}</MenuItem>
              </Select>
            </FormControl>
            <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 1 }}>
              {config.node_mode === "master"
                ? t("settingsInterconnector.ui.masterHelp")
                : t("settingsInterconnector.ui.clientHelp")}
            </Typography>
          </Grid>

          {/* Node Name */}
          <Grid item xs={12} md={6}>
            <TextField
              fullWidth
              label={t("settingsInterconnector.ui.nodeName")}
              value={config.node_name}
              onChange={(e) => setConfig({ ...config, node_name: e.target.value })}
              placeholder={systemName || "My-LLM-System"}
              helperText={!config.node_name ? t("settingsInterconnector.ui.nodeNameRequired") : t("settingsInterconnector.ui.nodeNameHelp")}
              error={!config.node_name && config.is_enabled}
            />
          </Grid>

              {/* Master Mode Configuration */}
              {config.node_mode === "master" && (
                <>
                  <Grid item xs={12}>
                    <Divider sx={{ my: 1 }}>
                      <Chip label={t("settingsInterconnector.ui.masterSettings")} size="small" />
                    </Divider>
                  </Grid>

                  <Grid item xs={12}>
                    <Box>
                      <Typography variant="subtitle2" gutterBottom>
                        {t("settingsInterconnector.ui.apiKeyAuth")}
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
                              {t("settingsInterconnector.ui.requireApiKey")}
                            </Typography>
                            <Typography variant="caption" color="text.secondary">
                              {t("settingsInterconnector.ui.requireApiKeyHelp")}
                            </Typography>
                          </Box>
                        }
                      />
                      {!config.require_api_key && (
                        <Alert severity="warning" sx={{ mt: 1 }}>
                          {t("settingsInterconnector.ui.apiKeyAuthOff")}
                        </Alert>
                      )}
                    </Box>
                  </Grid>

                  <Grid item xs={12}>
                    <Box>
                      <Typography variant="subtitle2" gutterBottom>
                        {t("settingsInterconnector.ui.apiKeyForClients")}
                      </Typography>
                      {config.api_key_hash ? (
                        <Box>
                          <Alert severity="success" sx={{ mb: 2 }}>
                            {t("settingsInterconnector.ui.apiKeyConfigured", { hash: config.api_key_hash.substring(0, 16) })}
                          </Alert>
                          {generatedApiKey && showApiKey && (
                            <Alert severity="warning" sx={{ mb: 2 }}>
                              <Typography variant="body2" fontWeight="bold">
                                {t("settingsInterconnector.ui.saveKeyNow")}
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
                          {t("settingsInterconnector.ui.noApiKey")}
                        </Alert>
                      )}
                      <Box display="flex" gap={2} alignItems="center" flexWrap="wrap" sx={{ mb: 2 }}>
                        <Button
                          variant="outlined"
                          size="small"
                          startIcon={<KeyIcon />}
                          onClick={handleGenerateApiKey}
                        >
                          {config.api_key_hash ? t("settingsInterconnector.ui.regenerateKey") : t("settingsInterconnector.ui.generateKey")}
                        </Button>
                        <Button
                          variant="outlined"
                          size="small"
                          onClick={handleTestFileScanning}
                          disabled={isTestingFileScan}
                          startIcon={isTestingFileScan ? <CircularProgress size={16} /> : <ManualSyncIcon />}
                        >
                          {isTestingFileScan ? t("settingsInterconnector.ui.testing") : t("settingsInterconnector.ui.testFileScan")}
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
                              {t("settingsInterconnector.ui.scanResults")}
                            </Typography>
                            <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                              {t("settingsInterconnector.ui.totalFiles", { count: fileScanTestResult.details.total_files || 0 })}
                            </Typography>
                            <Typography variant="caption" display="block">
                              {t("settingsInterconnector.ui.totalSize", { size: fileScanTestResult.details.total_size_mb || 0 })}
                            </Typography>
                            <Typography variant="caption" display="block">
                              {t("settingsInterconnector.ui.projectRoot", { root: fileScanTestResult.details.project_root || t("settingsInterconnector.ui.unknown") })}
                            </Typography>
                            {fileScanTestResult.details.critical_files && (
                              <>
                                <Typography variant="caption" display="block" sx={{ mt: 1, fontWeight: "bold" }}>
                                  {t("settingsInterconnector.ui.criticalFiles")}
                                </Typography>
                                {fileScanTestResult.details.critical_files.found && fileScanTestResult.details.critical_files.found.length > 0 && (
                                  <Typography variant="caption" display="block" sx={{ ml: 2, color: "success.main" }}>
                                    {t("settingsInterconnector.ui.found", { files: fileScanTestResult.details.critical_files.found.join(", ") })}
                                  </Typography>
                                )}
                                {fileScanTestResult.details.critical_files.missing && fileScanTestResult.details.critical_files.missing.length > 0 && (
                                  <Typography variant="caption" display="block" sx={{ ml: 2, color: "error.main" }}>
                                    {t("settingsInterconnector.ui.missing", { files: fileScanTestResult.details.critical_files.missing.join(", ") })}
                                  </Typography>
                                )}
                              </>
                            )}
                            {fileScanTestResult.details.by_directory && Object.keys(fileScanTestResult.details.by_directory).length > 0 && (
                              <>
                                <Typography variant="caption" display="block" sx={{ mt: 1, fontWeight: "bold" }}>
                                  {t("settingsInterconnector.ui.byDirectory")}
                                </Typography>
                                {Object.entries(fileScanTestResult.details.by_directory)
                                  .slice(0, 5)
                                  .map(([dir, info]) => (
                                    <Typography key={dir} variant="caption" display="block" sx={{ ml: 2 }}>
                                      {t("settingsInterconnector.ui.directoryLine", { dir, count: info.count, size: Math.round(info.size / 1024) })}
                                    </Typography>
                                  ))}
                              </>
                            )}
                            {fileScanTestResult.details.sample_files && fileScanTestResult.details.sample_files.length > 0 && (
                              <>
                                <Typography variant="caption" display="block" sx={{ mt: 1, fontWeight: "bold" }}>
                                  {t("settingsInterconnector.ui.sampleFiles")}
                                </Typography>
                                {fileScanTestResult.details.sample_files.slice(0, 5).map((f, idx) => (
                                  <Typography key={idx} variant="caption" display="block" sx={{ ml: 2, fontFamily: "monospace", fontSize: "0.7rem" }}>
                                    {f.path}
                                  </Typography>
                                ))}
                                {fileScanTestResult.details.sample_files.length > 5 && (
                                  <Typography variant="caption" display="block" sx={{ ml: 2, fontStyle: "italic" }}>
                                    {t("settingsInterconnector.ui.andMoreSpaced", { count: fileScanTestResult.details.sample_files.length - 5 })}
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
                      <Chip label={t("settingsInterconnector.ui.masterConnection")} size="small" />
                    </Divider>
                  </Grid>

                  <Grid item xs={12} md={6}>
                    <TextField
                      fullWidth
                      label={t("settingsInterconnector.ui.masterAddress")}
                      value={config.master_url}
                      onChange={(e) => setConfig({ ...config, master_url: e.target.value })}
                      placeholder="192.168.1.100"
                      helperText={t("settingsInterconnector.ui.masterAddressHelp")}
                    />
                  </Grid>

                  <Grid item xs={12} md={6}>
                    <TextField
                      fullWidth
                      label={t("settingsInterconnector.ui.masterApiKey")}
                      type="password"
                      value={config.master_api_key}
                      onChange={(e) => setConfig({ ...config, master_api_key: e.target.value })}
                      placeholder={t("settingsInterconnector.ui.masterApiKeyPlaceholder")}
                      helperText={t("settingsInterconnector.ui.masterApiKeyHelp")}
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
                        {t("settingsInterconnector.ui.testConnection")}
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

                  <Grid item xs={12}>
                    <FleetMembership identity={localIdentity} />
                  </Grid>
                </>
              )}

              {/* Sync Settings */}
              <Grid item xs={12}>
                <Divider sx={{ my: 1 }}>
                  <Chip label={t("settingsInterconnector.ui.syncSettings")} size="small" />
                </Divider>
              </Grid>

              <Grid item xs={12} md={6}>
                <Typography variant="body2" gutterBottom>
                  {t("settingsInterconnector.ui.syncOptions")}
                </Typography>
                <Box sx={{ display: 'flex', gap: 0.5, flexWrap: 'wrap', mb: 1 }}>
                  <Chip
                    label={t("settingsInterconnector.ui.enableAutoSync")}
                    color={config.auto_sync_enabled ? 'primary' : 'default'}
                    onClick={(_e) => setConfig({ ...config, auto_sync_enabled: !config.auto_sync_enabled })}
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
                  {t("settingsInterconnector.ui.autoSyncHelp")}
                </Typography>
              </Grid>

              <Grid item xs={12} md={6}>
                <TextField
                  fullWidth
                  type="number"
                  label={t("settingsInterconnector.ui.syncInterval")}
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
                  helperText={t("settingsInterconnector.ui.syncIntervalHelp")}
                />
              </Grid>

              <Grid item xs={12}>
                <Typography variant="subtitle2" gutterBottom>
                  {t("settingsInterconnector.ui.entitiesToSync")}
                </Typography>
                <Box display="flex" gap={1} flexWrap="wrap">
                {["clients", "projects", "websites"].map((entity) => (
                    <Chip
                      key={entity}
                      label={{
                        clients: t("settingsInterconnector.ui.entities.clients"),
                        projects: t("settingsInterconnector.ui.entities.projects"),
                        websites: t("settingsInterconnector.ui.entities.websites"),
                      }[entity]}
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
                {isSaving ? t("settingsInterconnector.ui.saving") : t("settingsInterconnector.ui.saveConfiguration")}
              </Button>
              <Button
                variant="outlined"
                size="small"
                onClick={handleCancelConfiguration}
                disabled={isSaving}
              >
                {t("settingsInterconnector.ui.cancel")}
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
                  {t("settingsInterconnector.ui.currentStatus")}
                </Typography>
                <Grid container spacing={2}>
                  <Grid item xs={12} sm={6} md={3}>
                    <Box>
                      <Typography variant="caption" color="text.secondary">
                        {t("settingsInterconnector.ui.mode")}
                      </Typography>
                      <Chip
                        label={status.node_mode || t("settingsInterconnector.ui.notAvailable")}
                        color={status.node_mode === "master" ? "primary" : "secondary"}
                        size="small"
                        icon={status.node_mode === "master" ? <StorageIcon /> : <ComputerIcon />}
                      />
                    </Box>
                  </Grid>
                  <Grid item xs={12} sm={6} md={3}>
                    <Box>
                      <Typography variant="caption" color="text.secondary">
                        {t("settingsInterconnector.ui.nodeName")}
                      </Typography>
                      <Typography variant="body2">{status.node_name || t("settingsInterconnector.ui.notSet")}</Typography>
                    </Box>
                  </Grid>
                  <Grid item xs={12} sm={6} md={3}>
                    <Box>
                      <Typography variant="caption" color="text.secondary">
                        {t("settingsInterconnector.ui.autoSync")}
                      </Typography>
                      <Chip
                        label={status.auto_sync_enabled ? t("settingsInterconnector.ui.enabled") : t("settingsInterconnector.ui.disabled")}
                        color={status.auto_sync_enabled ? "success" : "default"}
                        size="small"
                      />
                    </Box>
                  </Grid>
                  <Grid item xs={12} sm={6} md={3}>
                    <Box>
                      <Typography variant="caption" color="text.secondary">
                        {t("settingsInterconnector.ui.lastSync")}
                      </Typography>
                      <Typography variant="body2">
                        {status.last_sync_time
                          ? new Date(status.last_sync_time).toLocaleString(uiLocale())
                          : t("settingsInterconnector.ui.never")}
                      </Typography>
                    </Box>
                  </Grid>
                </Grid>
              </CardContent>
            </Card>
          )}

          {/* Client Mode: Advanced Controls only. The Code Update panel is
              now rendered at the very top of this component so the user
              doesn't have to scroll past server config to apply updates. */}
          {config.node_mode === "client" && config.master_url && (
            <>
              {/* Toggle for Advanced Options */}
              <Box sx={{ mt: 2, mb: 1 }}>
                <Button
                  variant="text"
                  size="small"
                  startIcon={<SettingsIcon />}
                  onClick={() => setShowAdvancedClientView(!showAdvancedClientView)}
                  sx={{ color: "text.secondary" }}
                >
                  {showAdvancedClientView ? t("settingsInterconnector.ui.hideAdvanced") : t("settingsInterconnector.ui.showAdvanced")}
                </Button>
              </Box>

              {/* Advanced Manual Sync Controls - Hidden by default */}
              {showAdvancedClientView && config.master_api_key && (
                <Paper elevation={2} sx={{ p: 3, mb: 3 }}>
                  <Typography variant="h6" gutterBottom>
                    {t("settingsInterconnector.ui.advancedTitle")}
                  </Typography>
                  <Typography variant="body2" color="text.secondary" gutterBottom>
                    {t("settingsInterconnector.ui.advancedHelp")}
                  </Typography>

                  <Box sx={{ mb: 2 }}>
                    <Typography variant="subtitle2" gutterBottom>
                      {t("settingsInterconnector.ui.syncProfile")}
                    </Typography>
                    <FormControl fullWidth size="small" sx={{ maxWidth: 320 }}>
                      <InputLabel>{t("settingsInterconnector.ui.profile")}</InputLabel>
                      <Select
                        label={t("settingsInterconnector.ui.profile")}
                        value={selectedProfile || ""}
                        onChange={(e) => setSelectedProfile(e.target.value || null)}
                      >
                        <MenuItem value="">{t("settingsInterconnector.ui.default")}</MenuItem>
                        {profiles.map((p) => (
                          <MenuItem key={p.id} value={p.name}>
                            {p.name} {p.is_default ? t("settingsInterconnector.ui.default") : ""}
                          </MenuItem>
                        ))}
                      </Select>
                    </FormControl>
                    <Typography variant="caption" color="text.secondary" display="block" sx={{ mt: 0.5 }}>
                      {t("settingsInterconnector.ui.profilesHelp")}
                    </Typography>
                  </Box>

                  {/* Data sync */}
                  <Box sx={{ mb: 3 }}>
                    <Typography variant="subtitle2" gutterBottom>
                      {t("settingsInterconnector.ui.dataSync")}
                    </Typography>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      {t("settingsInterconnector.ui.dataSyncHelp")}
                    </Typography>
                    <Box display="flex" gap={1} flexWrap="wrap" mt={1}>
                      <Button
                        variant="contained"
                        size="small"
                        onClick={() => handleManualSync("bidirectional")}
                        disabled={isSyncingData}
                        startIcon={isSyncingData ? <CircularProgress size={16} /> : <SyncIcon />}
                      >
                        {t("settingsInterconnector.ui.syncBidirectional")}
                      </Button>
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={() => handleManualSync("pull")}
                        disabled={isSyncingData}
                      >
                        {t("settingsInterconnector.ui.pullFromMaster")}
                      </Button>
                      <Button
                        variant="outlined"
                        size="small"
                        onClick={() => handleManualSync("push")}
                        disabled={isSyncingData}
                      >
                        {t("settingsInterconnector.ui.pushToMaster")}
                      </Button>
                    </Box>

                    {isSyncingData && (
                      <Box mt={2}>
                        <LinearProgress />
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1 }}>
                          {t("settingsInterconnector.ui.synchronizing")}
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
                              {t("settingsInterconnector.ui.dataSyncCompleted")}
                            </Typography>
                            {dataSyncResults.summary && (
                              <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                                {t("settingsInterconnector.ui.dataSummary", {
                                  processed: dataSyncResults.summary.total_processed || 0,
                                  created: dataSyncResults.summary.total_created || 0,
                                  updated: dataSyncResults.summary.total_updated || 0,
                                })}
                                {dataSyncResults.summary.total_conflicts > 0 &&
                                  ` | ${t("settingsInterconnector.ui.conflicts", { count: dataSyncResults.summary.total_conflicts })}`}
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
                      {t("settingsInterconnector.ui.outputs")}
                    </Typography>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      {t("settingsInterconnector.ui.outputsHelp")}
                    </Typography>
                    <Button
                      variant="outlined"
                      size="small"
                      onClick={handleLoadOutputsIndex}
                      disabled={isLoadingOutputs}
                      startIcon={isLoadingOutputs ? <CircularProgress size={16} /> : <StorageIcon />}
                      sx={{ mr: 1 }}
                    >
                      {isLoadingOutputs ? t("settingsInterconnector.ui.loading") : t("settingsInterconnector.ui.loadOutputs")}
                    </Button>
                    {outputsIndex && (
                      <Box sx={{ mt: 2 }}>
                        {outputsIndex.error ? (
                          <Alert severity="error">{outputsIndex.error}</Alert>
                        ) : (
                          <Alert severity="info">
                            <Typography variant="caption">
                              {t("settingsInterconnector.ui.foundFiles", { count: outputsIndex.count || (outputsIndex.files ? outputsIndex.files.length : 0) })}
                            </Typography>
                            {outputsIndex.files && outputsIndex.files.length > 0 && (
                              <Box sx={{ mt: 1 }}>
                                {outputsIndex.files.slice(0, 5).map((f, idx) => (
                                  <Typography key={idx} variant="caption" display="block" sx={{ fontFamily: "monospace" }}>
                                    {t("settingsInterconnector.ui.outputFile", { path: f.path, size: Math.round((f.size || 0) / 1024) })}
                                  </Typography>
                                ))}
                                {outputsIndex.files.length > 5 && (
                                  <Typography variant="caption" display="block" sx={{ fontStyle: "italic" }}>
                                    {t("settingsInterconnector.ui.andMore", { count: outputsIndex.files.length - 5 })}
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
                      {t("settingsInterconnector.ui.legacyCodeSync")}
                    </Typography>
                    <Typography variant="body2" color="text.secondary" gutterBottom>
                      {t("settingsInterconnector.ui.legacyHelp")}
                    </Typography>
                    <Button
                      variant="outlined"
                      color="secondary"
                      size="small"
                      onClick={handleSyncFiles}
                      disabled={isSyncingCode}
                      sx={{ mb: 1 }}
                    >
                      {isSyncingCode ? t("settingsInterconnector.ui.pullingCode") : t("settingsInterconnector.ui.pullCode")}
                    </Button>

                    {isSyncingCode && (
                      <Box mt={2}>
                        <LinearProgress />
                        <Typography variant="caption" color="text.secondary" sx={{ mt: 1 }}>
                          {t("settingsInterconnector.ui.pullingUpdates")}
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
                              {t("settingsInterconnector.ui.codeSyncCompleted")}
                            </Typography>
                            {codeSyncResults.files && (
                              <Typography variant="caption" display="block" sx={{ mt: 1 }}>
                                {t("settingsInterconnector.ui.codeSummary", {
                                  processed: codeSyncResults.files.summary?.total_processed || 0,
                                  created: codeSyncResults.files.summary?.total_created || 0,
                                  updated: codeSyncResults.files.summary?.total_updated || 0,
                                })}
                                {codeSyncResults.files.summary?.total_backed_up > 0 &&
                                  ` | ${t("settingsInterconnector.ui.backedUp", { count: codeSyncResults.files.summary.total_backed_up })}`}
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
                      <Typography variant="subtitle2">{t("settingsInterconnector.ui.pendingApprovals")}</Typography>
                      <Button size="small" onClick={loadApprovals} startIcon={<RefreshIcon />} disabled={isLoadingApprovals}>
                        {t("settingsInterconnector.ui.refresh")}
                      </Button>
                    </Box>
                    {isLoadingApprovals ? (
                      <LinearProgress />
                    ) : pendingApprovals.length === 0 ? (
                      <Alert severity="info">{t("settingsInterconnector.ui.noApprovals")}</Alert>
                    ) : (
                      <TableContainer>
                        <Table size="small">
                          <TableHead>
                            <TableRow>
                              <TableCell>{t("settingsInterconnector.ui.approvalColumns.id")}</TableCell>
                              <TableCell>{t("settingsInterconnector.ui.approvalColumns.files")}</TableCell>
                              <TableCell>{t("settingsInterconnector.ui.approvalColumns.received")}</TableCell>
                              <TableCell align="right">{t("settingsInterconnector.ui.approvalColumns.actions")}</TableCell>
                            </TableRow>
                          </TableHead>
                          <TableBody>
                            {pendingApprovals.map((appr) => {
                              const files = appr.files_data || [];
                              return (
                                <TableRow key={appr.id}>
                                  <TableCell>{appr.id}</TableCell>
                                  <TableCell>{t("settingsInterconnector.ui.fileCount", { count: files.length })}</TableCell>
                                  <TableCell>
                                    {appr.received_at ? new Date(appr.received_at).toLocaleString(uiLocale()) : "-"}
                                  </TableCell>
                                  <TableCell align="right">
                                    <Button
                                      size="small"
                                      variant="contained"
                                      color="success"
                                      onClick={() => handleApprovalDecision(appr.id, "approve")}
                                      sx={{ mr: 1 }}
                                    >
                                      {t("settingsInterconnector.ui.approve")}
                                    </Button>
                                    <Button
                                      size="small"
                                      variant="outlined"
                                      color="error"
                                      onClick={() => handleApprovalDecision(appr.id, "decline")}
                                    >
                                      {t("settingsInterconnector.ui.decline")}
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

          {/* Machines asking to join (Master Mode Only) */}
          {config.node_mode === "master" && <InterconnectorEnrolments />}

          {/* Connected Nodes (Master Mode Only) */}
          {config.node_mode === "master" && (
            <Paper elevation={2} sx={{ p: 3 }}>
              <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
                <Typography variant="h6">{t("settingsInterconnector.ui.connectedNodes")}</Typography>
                <Box display="flex" gap={1} alignItems="center">
                  <Button variant="contained" size="small" onClick={handleBroadcastAll}>
                    {t("settingsInterconnector.ui.broadcastAll")}
                  </Button>
                  <IconButton onClick={loadNodes} size="small">
                    <RefreshIcon />
                  </IconButton>
                </Box>
              </Box>

              {nodes.length === 0 ? (
                <Alert severity="info">{t("settingsInterconnector.ui.noNodes")}</Alert>
              ) : (
                <TableContainer>
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>{t("settingsInterconnector.ui.nodeColumns.status")}</TableCell>
                        <TableCell>{t("settingsInterconnector.ui.nodeColumns.nodeName")}</TableCell>
                        <TableCell>{t("settingsInterconnector.ui.nodeColumns.host")}</TableCell>
                        <TableCell>{t("settingsInterconnector.ui.nodeColumns.lastHeartbeat")}</TableCell>
                        <TableCell align="right">{t("settingsInterconnector.ui.nodeColumns.actions")}</TableCell>
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
                                label={node.status || t("settingsInterconnector.ui.unknown")}
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
                                ? new Date(node.last_heartbeat).toLocaleString(uiLocale())
                                : t("settingsInterconnector.ui.never")}
                            </TableCell>
                            <TableCell align="right">
                              <Box display="flex" gap={1} justifyContent="flex-end" alignItems="center">
                                <Tooltip title={t("settingsInterconnector.ui.testClient")}>
                                  <span>
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
                                  </span>
                                </Tooltip>
                                {testResult && (
                                  <Tooltip title={testResult.message}>
                                    <Chip
                                      label={testResult.success ? t("settingsInterconnector.ui.connected") : t("settingsInterconnector.ui.failed")}
                                      color={testResult.success ? "success" : "error"}
                                      size="small"
                                      sx={{ height: 24 }}
                                    />
                                  </Tooltip>
                                )}
                                <Tooltip title={t("settingsInterconnector.ui.disconnectNode")}>
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
                  {t("settingsInterconnector.ui.broadcastStartedId", {
                    id: broadcastResult.broadcast_id || broadcastResult.data?.broadcast_id || t("settingsInterconnector.ui.pending"),
                  })}
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

