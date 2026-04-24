// frontend/src/pages/SettingsPage.jsx

import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Typography,
  Box,
  Select,
  MenuItem,
  Button,
  FormControl,
  InputLabel,
  CircularProgress,
  Paper,
  Grid,
  Tooltip,
  Switch,
  FormControlLabel,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Input,
  TextField,
  Stack,
  Avatar,
  Slider,
  IconButton,
} from "@mui/material";
import MuiAlert from "@mui/material/Alert";

// MUI Icons
import CheckCircleOutlineIcon from "@mui/icons-material/CheckCircleOutline";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import SyncProblemIcon from "@mui/icons-material/SyncProblem";
import StorageIcon from "@mui/icons-material/Storage";
import DnsIcon from "@mui/icons-material/Dns";
import SpeedIcon from "@mui/icons-material/Speed";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import FileUploadIcon from "@mui/icons-material/FileUpload";
import FileDownloadIcon from "@mui/icons-material/FileDownload";
import BackupIcon from "@mui/icons-material/Backup";
import RestoreIcon from "@mui/icons-material/Restore";
import AccountBoxIcon from "@mui/icons-material/AccountBox";
import WarningIcon from "@mui/icons-material/Warning";
import SecurityIcon from "@mui/icons-material/Security";
import FolderIcon from "@mui/icons-material/Folder";
import ChatIcon from "@mui/icons-material/Chat";
import ApiIcon from "@mui/icons-material/Api";
import SystemIcon from "@mui/icons-material/Computer";
import TrendingUpIcon from "@mui/icons-material/TrendingUp";
import BuildIcon from "@mui/icons-material/Build";
import DashboardIcon from "@mui/icons-material/Dashboard";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import ExtensionIcon from "@mui/icons-material/Extension";
import CodeIcon from "@mui/icons-material/Code";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ImageIcon from "@mui/icons-material/Image";
import MusicNoteIcon from "@mui/icons-material/MusicNote";
import ScienceIcon from "@mui/icons-material/Science";
import { Accordion, AccordionSummary, AccordionDetails, Chip, LinearProgress } from "@mui/material";

import { io } from "socket.io-client";
import CreateBackupModal from "../components/modals/CreateBackupModal";
import RestoreBackupModal from "../components/modals/RestoreBackupModal";
import ManageBackupsModal from "../components/modals/ManageBackupsModal";
import PurgeIndexModal from "../components/modals/PurgeIndexModal";
import ThemeSelectorModal from "../components/modals/ThemeSelectorModal";
import UncleClaudeSection from "../components/settings/UncleClaudeSection";
import MemoryManagementSection from "../components/settings/MemoryManagementSection";
import KillSwitchModal from "../components/modals/KillSwitchModal";
import RebootProgressModal from "../components/modals/RebootProgressModal";
import RAGDebugSection from "../components/settings/RAGDebugSection";
import ImageModelsModal from "../components/modals/ImageModelsModal";
import VideoModelsModal from "../components/modals/VideoModelsModal";
import VoiceModelsModal from "../components/modals/VoiceModelsModal";
import AgentsSettingsModal from "../components/modals/AgentsSettingsModal";
import InterconnectorSettingsModal from "../components/modals/InterconnectorSettingsModal";
import VoiceSettingsModal from "../components/modals/VoiceSettingsModal";
import SettingsRow from "../components/settings/SettingsRow";
import SettingsCardWrapper from "../components/settings/SettingsCardWrapper";
import { SOCKET_URL } from "../api/apiClient";
import PaletteIcon from "@mui/icons-material/Palette";
import SchoolIcon from "@mui/icons-material/School";
import TaskAltIcon from "@mui/icons-material/TaskAlt";
import { useNavigate } from "react-router-dom";
import { themes } from "../theme";
import {
  getBranding,
  updateBranding,
  getRagDebug,
  setRagDebug as setRagDebugAPI,
  getRagFeatures,
  updateRagFeatures,
  clearBehaviorLog,
  getMusicDirectory,
  setMusicDirectory as setMusicDirectoryAPI,
} from "../api/settingsService";
import { useAppStore } from "../stores/useAppStore";
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import { useSnackbar } from "../components/common/SnackbarProvider";
import * as interconnectorApi from "../api/interconnectorService";
import { useVoiceContext, useVoice } from "../contexts/VoiceContext";
import * as apiService from "../api";
import voiceService from "../api/voiceService";
import { updateAgentSettings } from "../api/agentsService";
import { ragAutoresearchService } from "../api/ragAutoresearchService";

// localStorage keys for persisting settings
const WEB_SEARCH_ENABLED_KEY = "guaardvark_webSearchEnabled";
const ADV_DEBUG_ENABLED_KEY = "guaardvark_advDebugEnabled";
const BEHAVIOR_LEARNING_ENABLED_KEY = "guaardvark_behaviorLearningEnabled";
const LLM_DEBUG_ENABLED_KEY = "guaardvark_llmDebugEnabled";
// Global "use RulesPage rules for chat" toggle. Backend is the source of
// truth (Setting table, key=rules_enabled); this localStorage mirror only
// speeds first paint before the API round-trip lands.
const RULES_ENABLED_KEY = "guaardvark_rulesEnabled";
// Used by ChatPage to enable backend agent routing integration
const AGENT_ROUTING_ENABLED_KEY = "use_agent_routing";
// Used by ChatPage to enable unified agentic chat (LLM with tool access)
const UNIFIED_CHAT_ENABLED_KEY = "use_unified_chat";

// Voice settings localStorage keys (must match key used by voice components)
const VOICE_SETTINGS_KEY = "guaardvark_voiceSettings";
const VOICE_CHAT_ENABLED_KEY = "guaardvark_voiceChatEnabled";

const Alert = React.forwardRef(function Alert(props, ref) {
  return <MuiAlert elevation={6} ref={ref} variant="filled" {...props} />;
});

const SettingsPage = () => {
  const [availableModels, setAvailableModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [isLoadingEmbeddingModel, setIsLoadingEmbeddingModel] = useState(true);

  // Reset selectedModel if it's not in available options
  useEffect(() => {
    if (selectedModel && availableModels.length > 0) {
      const isModelAvailable = availableModels.some(model => model.name === selectedModel);
      if (!isModelAvailable) {
        console.warn(`Model "${selectedModel}" is not available, resetting selection`);
        setSelectedModel("");
      }
    }
  }, [availableModels, selectedModel]);
  const [isLoading, setIsLoading] = useState(false); // General loading for initial data or major actions
  const [isTestingLLM, setIsTestingLLM] = useState(false); // Local state for Test LLM button
  const { showMessage, closeSnackbar } = useSnackbar();
  const navigate = useNavigate();
  const [ragDebug, setRagDebug] = useState(false);
  const [enhancedContext, setEnhancedContext] = useState(false);
  const [advancedRag, setAdvancedRag] = useState(false);
  const [advancedDebug, setAdvancedDebug] = useState(getInitialAdvancedDebug);
  const [llmDebug, setLlmDebugState] = useState(getInitialLlmDebug);
  const [behaviorLearningEnabled, setBehaviorLearningEnabled] = useState(
    getInitialBehaviorLearning,
  );
  const [rulesEnabled, setRulesEnabledState] = useState(getInitialRulesEnabled);
  const [appVersion, setAppVersion] = useState("");

  function getInitialWebSearch() {
    if (typeof window === "undefined") return false;
    try {
      const saved = localStorage.getItem(WEB_SEARCH_ENABLED_KEY);
      return saved === "true";
    } catch {
      return false;
    }
  }

  function getInitialAdvancedDebug() {
    if (typeof window === "undefined") return false;
    try {
      const saved = localStorage.getItem(ADV_DEBUG_ENABLED_KEY);
      return saved === "true";
    } catch {
      return false;
    }
  }
  function getInitialBehaviorLearning() {
    if (typeof window === "undefined") return false;
    try {
      const saved = localStorage.getItem(BEHAVIOR_LEARNING_ENABLED_KEY);
      return saved === "true";
    } catch {
      return false;
    }
  }
  function getInitialRulesEnabled() {
    if (typeof window === "undefined") return false;
    try {
      return localStorage.getItem(RULES_ENABLED_KEY) === "true";
    } catch {
      return false;
    }
  }
  function getInitialLlmDebug() {
    if (typeof window === "undefined") return false;
    try {
      const saved = localStorage.getItem(LLM_DEBUG_ENABLED_KEY);
      return saved === "true";
    } catch {
      return false;
    }
  }
  function getInitialAgentRouting() {
    if (typeof window === "undefined") return true;
    try {
      const saved = localStorage.getItem(AGENT_ROUTING_ENABLED_KEY);
      return saved === null || saved === "true"; // ON by default, matches ChatPage
    } catch {
      return true;
    }
  }
  function getInitialUnifiedChat() {
    if (typeof window === "undefined") return true;
    try {
      const saved = localStorage.getItem(UNIFIED_CHAT_ENABLED_KEY);
      return saved === null || saved === "true"; // ON by default, matches ChatPage
    } catch {
      return true;
    }
  }
  const [webSearchEnabled, setWebSearchEnabled] = useState(getInitialWebSearch);
  const [agentRoutingEnabled, setAgentRoutingEnabled] = useState(getInitialAgentRouting);
  const [unifiedChatEnabled, setUnifiedChatEnabled] = useState(getInitialUnifiedChat);
  const [isTesting, setIsTesting] = useState(false);
  const [testResults, setTestResults] = useState(null);
  const [isRunningTests, setIsRunningTests] = useState(false);
  const [testMode, setTestMode] = useState("basic");
  const [expandedCategories, setExpandedCategories] = useState({});
  const [testSuiteResults, setTestSuiteResults] = useState(null);
  // activeTab removed — all cards shown on single page
  const [musicDirectory, setMusicDirectory] = useState("");

  // Model switching async state
  const [modelSwitchStatus, setModelSwitchStatus] = useState("idle"); // idle, loading, complete, error
  const [modelSwitchMessage, setModelSwitchMessage] = useState("");
  const socketRef = useRef(null);

  // State for Import/Export
  const [isExporting, setIsExporting] = useState(false);
  const [isImporting, setIsImporting] = useState(false);
  const [selectedFileForImport, setSelectedFileForImport] = useState(null);
  const [selectedFileNameForImport, setSelectedFileNameForImport] =
    useState("");
  const fileImportInputRef = useRef(null);

  // Backup/Restore modal state
  const [createBackupOpen, setCreateBackupOpen] = useState(false);
  const [restoreBackupOpen, setRestoreBackupOpen] = useState(false);
  const [manageBackupsOpen, setManageBackupsOpen] = useState(false);
  const [isProcessingBackup, setIsProcessingBackup] = useState(false);
  const [backupList, setBackupList] = useState([]);

  const [isPurging, setIsPurging] = useState(false);
  const [purgeModalOpen, setPurgeModalOpen] = useState(false);

  const [themeModalOpen, setThemeModalOpen] = useState(false);
  const [voiceSettingsModalOpen, setVoiceSettingsModalOpen] = useState(false);
  const [agentsModalOpen, setAgentsModalOpen] = useState(false);
  const [interconnectorModalOpen, setInterconnectorModalOpen] = useState(false);
  const [interconnectorEnabled, setInterconnectorEnabled] = useState(false);
  const [interconnectorPendingCount, setInterconnectorPendingCount] = useState(0);
  const [interconnectorIsClient, setInterconnectorIsClient] = useState(false);
  const [interconnectorUpdateStatus, setInterconnectorUpdateStatus] = useState(null);
  const [voiceChatEnabled, setVoiceChatEnabled] = useState(() => {
    try {
      return localStorage.getItem(VOICE_CHAT_ENABLED_KEY) !== "false";
    } catch {
      return true;
    }
  });
  const [killSwitchOpen, setKillSwitchOpen] = useState(false);
  const [rebootDialogOpen, setRebootDialogOpen] = useState(false);
  const [rebootInProgress, setRebootInProgress] = useState(false);
  const [rebootProgressModalOpen, setRebootProgressModalOpen] = useState(false);
  const [imageModelsModalOpen, setImageModelsModalOpen] = useState(false);
  const [videoModelsModalOpen, setVideoModelsModalOpen] = useState(false);
  const [voiceModelsModalOpen, setVoiceModelsModalOpen] = useState(false);
  const setTrainerOpen = useAppStore((state) => state.setTrainerOpen);
  const [imageGenStatus, setImageGenStatus] = useState(null);

  // Resource monitor and embedding model state
  const [gpuResources, setGpuResources] = useState(null);
  const [embeddingModels, setEmbeddingModels] = useState([]);
  const [selectedEmbeddingModel, setSelectedEmbeddingModel] = useState("");
  const [isSwitchingEmbedding, setIsSwitchingEmbedding] = useState(false);
  const [embedDimFilter, setEmbedDimFilter] = useState(null); // null = all, or a number like 1024
  const [chatSizeFilter, setChatSizeFilter] = useState(null); // null = all, or "small"/"medium"/"large"

  // RAG Autoresearch settings state
  const [autoresearchSettings, setAutoresearchSettings] = useState({});

  const setSystemInfo = useAppStore((state) => state.setSystemInfo);
  const persistedSystemLogo = useAppStore((state) => state.systemLogo);
  const persistedSystemName = useAppStore((state) => state.systemName);

  const [brandingName, setBrandingName] = useState(persistedSystemName || "");
  const [brandingFile, setBrandingFile] = useState(null);
  const [systemLogo, setSystemLogo] = useState(persistedSystemLogo || null);

  // Keep local branding state in sync with the latest persisted values
  useEffect(() => {
    if (!brandingFile && persistedSystemLogo && systemLogo !== persistedSystemLogo) {
      setSystemLogo(persistedSystemLogo);
    }
  }, [brandingFile, persistedSystemLogo, systemLogo]);

  useEffect(() => {
    if (!brandingName && persistedSystemName) {
      setBrandingName(persistedSystemName);
    }
  }, [brandingName, persistedSystemName]);

  // Voice settings
  const [voiceSettings, setVoiceSettings] = useState(() => {
    try {
      // Migrate from old key if present (was "guaardvark_voiceSettings" before)
      const oldKey = "guaardvark_voiceSettings";
      const oldSaved = localStorage.getItem(oldKey);
      if (oldSaved && !localStorage.getItem(VOICE_SETTINGS_KEY)) {
        localStorage.setItem(VOICE_SETTINGS_KEY, oldSaved);
        localStorage.removeItem(oldKey);
      }

      const saved = localStorage.getItem(VOICE_SETTINGS_KEY);
      return saved ? JSON.parse(saved) : {
        voice: 'libritts',
        recordingQuality: 'medium',
        recordingVolume: 1.0,
        autoGainControl: true,
        noiseSuppression: true,
        echoCancellation: true,
        playbackVolume: 1.0,
        playbackSpeed: 1.0,
        maxRecordingDuration: 60,
        ttsEnabled: true,
        micEnabled: true,
        // Continuous listening mode settings
        silenceThreshold: 0.05,
        silenceTimeout: 2000,
        maxSegmentDuration: 30000
      };
    } catch (error) {
      console.warn('Failed to load voice settings from localStorage:', error);
      return {
        voice: 'libritts',
        recordingQuality: 'medium',
        recordingVolume: 1.0,
        autoGainControl: true,
        noiseSuppression: true,
        echoCancellation: true,
        playbackVolume: 1.0,
        playbackSpeed: 1.0,
        maxRecordingDuration: 60,
        ttsEnabled: true,
        micEnabled: true,
        // Continuous listening mode settings
        silenceThreshold: 0.05,
        silenceTimeout: 2000,
        maxSegmentDuration: 30000
      };
    }
  });

  const [availableVoices, setAvailableVoices] = useState([]);
  const [voiceStatus, setVoiceStatus] = useState(null);
  const [voiceError, setVoiceError] = useState(null);
  const [isVoiceLoading, setIsVoiceLoading] = useState(false);
  const [isVoiceTestPlaying, setIsVoiceTestPlaying] = useState(false);
  const [isInstallingVoice, setIsInstallingVoice] = useState(false);
  const [isInstallingWhisper, setIsInstallingWhisper] = useState(false);
  const [voiceModelsStatus, setVoiceModelsStatus] = useState(null);

  // Get VoiceContext to sync voice changes
  const voiceContext = useVoice();
  const setSelectedVoice = voiceContext?.setSelectedVoice || (() => { });

  // Load voice configuration
  useEffect(() => {
    loadVoiceConfiguration();
  }, []);

  // Load autoresearch settings
  useEffect(() => {
    ragAutoresearchService.getSettings().then(data => setAutoresearchSettings(data)).catch(() => {});
  }, []);

  const loadVoiceConfiguration = async () => {
    setIsVoiceLoading(true);
    setVoiceError(null);

    try {
      const [status, voices, modelsStatus] = await Promise.all([
        voiceService.getStatus(),
        voiceService.getVoices().catch(() => ({ voices: [] })),
        voiceService.getVoiceModelsStatus().catch(() => null)
      ]);

      setVoiceStatus(status);
      setAvailableVoices(voices.voices || voices.available_voices || []);
      setVoiceModelsStatus(modelsStatus);

      // Set default voice if not already set or if saved voice is not available
      if (voices.voices && voices.voices.length > 0) {
        const savedVoice = voiceSettings.voice;
        const isVoiceAvailable = voices.voices.some(v => v.id === savedVoice);

        if (!savedVoice || !isVoiceAvailable) {
          const defaultVoice = voices.default_voice || voices.voices[0].id;
          setVoiceSettings(prev => ({
            ...prev,
            voice: defaultVoice
          }));
        }
      } else {
        // If no voices are available, reset to empty string to avoid MUI warnings
        setVoiceSettings(prev => ({
          ...prev,
          voice: ''
        }));
      }
    } catch (error) {
      console.error('Failed to load voice configuration:', error);
      setVoiceError('Failed to load voice configuration');
      // Reset voice to empty string on error to avoid MUI warnings
      setVoiceSettings(prev => ({
        ...prev,
        voice: ''
      }));
    } finally {
      setIsVoiceLoading(false);
    }
  };

  const testVoice = async (voiceId) => {
    setIsVoiceTestPlaying(true);
    try {
      // Check if voice is available first
      const voice = availableVoices.find(v => v.id === voiceId);
      if (voice && voice.available === false) {
        showMessage(`Voice model "${voice.name}" is not installed. Please install it first.`, "warning");
        setIsVoiceTestPlaying(false);
        return;
      }

      const response = await voiceService.textToSpeech(
        "Hello! This is a test of the text-to-speech feature.",
        voiceId
      );

      if (response.audio_url) {
        const audio = new Audio(response.audio_url);
        audio.play();
        audio.onended = () => setIsVoiceTestPlaying(false);
      } else if (response.error) {
        showMessage(`Voice test failed: ${response.error}`, "error");
        setIsVoiceTestPlaying(false);
      }
    } catch (error) {
      console.error('Voice test failed:', error);
      const errorMessage = error.message || "Voice test failed";
      if (errorMessage.includes("not found") || errorMessage.includes("not installed")) {
        showMessage("Voice model is not installed. Please install voice models first.", "warning");
      } else {
        showMessage(`Voice test failed: ${errorMessage}`, "error");
      }
      setIsVoiceTestPlaying(false);
    }
  };

  const handleVoiceSettingChange = (setting, value) => {
    setVoiceSettings(prev => ({
      ...prev,
      [setting]: value
    }));

    // Update VoiceContext immediately — localStorage 'storage' events only fire
    // in OTHER tabs, so we must sync the context directly for same-tab updates.
    if (setting === 'voice' && value) {
      setSelectedVoice(value);
      const voiceName = availableVoices.find(v => v.id === value)?.name || value;
      showMessage(`Voice changed to ${voiceName}`, "success");
    } else if (setting === 'ttsEnabled') {
      if (voiceContext?.setTtsEnabled) {
        voiceContext.setTtsEnabled(value);
      }
      showMessage(`Text-to-Speech ${value ? 'enabled' : 'disabled'}`, "success");
    } else if (setting === 'micEnabled') {
      showMessage(`Microphone ${value ? 'enabled' : 'disabled'}`, "success");
    }
  };

  const installDefaultVoiceModel = async () => {
    setIsInstallingVoice(true);
    try {
      showMessage("Installing LibriTTS voice model... This may take a moment.", "info");
      const result = await voiceService.installVoiceModel('libritts');

      if (result.success) {
        if (result.already_installed) {
          showMessage("LibriTTS voice model is already installed.", "info");
        } else {
          showMessage(`Successfully installed LibriTTS voice model (${result.model_size_mb} MB)`, "success");
        }
        // Reload voice configuration to update the UI
        await loadVoiceConfiguration();
      } else {
        showMessage(`Failed to install voice model: ${result.error}`, "error");
      }
    } catch (error) {
      console.error('Failed to install voice model:', error);
      showMessage(`Failed to install voice model: ${error.message}`, "error");
    } finally {
      setIsInstallingVoice(false);
    }
  };

  const installWhisperCpp = async () => {
    setIsInstallingWhisper(true);
    try {
      showMessage("Installing Whisper.cpp... This will clone and build from source (may take 1-2 minutes).", "info");
      const result = await voiceService.installWhisper();

      if (result.success) {
        if (result.already_installed) {
          showMessage("Whisper.cpp is already installed.", "info");
        } else {
          showMessage("Whisper.cpp installed successfully! You can now use speech recognition.", "success");
        }
        await loadVoiceConfiguration();
      } else {
        showMessage(`Failed to install Whisper.cpp: ${result.error}`, "error");
      }
    } catch (error) {
      console.error('Failed to install Whisper.cpp:', error);
      showMessage(`Failed to install Whisper.cpp: ${error.message}`, "error");
    } finally {
      setIsInstallingWhisper(false);
    }
  };

  const installWhisperSpeechModel = async () => {
    setIsInstallingWhisper(true);
    try {
      showMessage("Downloading default Whisper speech model (tiny.en)...", "info");
      const result = await voiceService.installWhisperModel('tiny.en');

      if (result.success) {
        showMessage(`Whisper model ready (${result.model_size_mb} MB)`, "success");
        await loadVoiceConfiguration();
      } else {
        showMessage(`Failed to download model: ${result.error}`, "error");
      }
    } catch (error) {
      console.error('Failed to install whisper model:', error);
      showMessage(`Failed to download model: ${error.message}`, "error");
    } finally {
      setIsInstallingWhisper(false);
    }
  };

  const fetchBranding = useCallback(async () => {
    try {
      const response = await getBranding();
      console.log("Fetched branding response:", response);
      if (response && response.data) {
        const data = response.data;
        setBrandingName((prev) => data.system_name ?? prev ?? persistedSystemName ?? "");
        setSystemLogo((prevLogo) => data.logo_path ?? prevLogo ?? persistedSystemLogo ?? null);
        console.log("Updated branding state - name:", data.system_name ?? persistedSystemName, "logo:", data.logo_path ?? persistedSystemLogo);
        return data;
      }
    } catch (err) {
      console.warn("Failed to fetch branding", err);
    }
    return null;
  }, [persistedSystemLogo, persistedSystemName]);

  useEffect(() => {
    fetchBranding();
  }, [fetchBranding]);

  useEffect(() => {
    getMusicDirectory().then((res) => {
      if (res?.data?.music_directory !== undefined) {
        setMusicDirectory(res.data.music_directory);
      }
    }).catch(() => {});
  }, []);

  useEffect(() => {
    interconnectorApi.getInterconnectorConfig().then((res) => {
      const enabled = res?.data?.config?.is_enabled || res?.config?.is_enabled;
      if (enabled) {
        setInterconnectorEnabled(true);
        const nodeMode = res?.data?.config?.node_mode || res?.config?.node_mode;
        setInterconnectorIsClient(nodeMode === "client");
        // Check for pending approvals
        interconnectorApi.getPendingApprovals?.().then((approvals) => {
          setInterconnectorPendingCount(Array.isArray(approvals) ? approvals.length : 0);
        }).catch(() => {});
      } else if (!res?.error) {
        setInterconnectorEnabled(false);
        setInterconnectorPendingCount(0);
        setInterconnectorIsClient(false);
        setInterconnectorUpdateStatus(null);
      }
    }).catch(() => {});
  }, [interconnectorModalOpen]);

  // One-shot check for code updates when in client mode
  useEffect(() => {
    if (!interconnectorEnabled || !interconnectorIsClient) {
      setInterconnectorUpdateStatus(null);
      return;
    }
    const timer = setTimeout(() => {
      interconnectorApi.checkForUpdates().then((res) => {
        const data = res?.data || res;
        if (!data?.error) {
          setInterconnectorUpdateStatus({
            available: data.available || false,
            count: data.count || 0,
            summary: data.summary || { backend: 0, frontend: 0, other: 0 },
          });
        }
      }).catch(() => {});
    }, 1500);
    return () => clearTimeout(timer);
  }, [interconnectorEnabled, interconnectorIsClient, interconnectorModalOpen]);

  useEffect(() => {
    try {
      localStorage.setItem(VOICE_CHAT_ENABLED_KEY, String(voiceChatEnabled));
      window.dispatchEvent(new Event("voiceChatEnabledChanged"));
    } catch (e) {
      console.warn("Failed to persist voice chat setting:", e);
    }
  }, [voiceChatEnabled]);

  const themeName = useAppStore((state) => state.themeName);

  const { activeModel, isLoadingModel, modelError, refreshActiveModel } =
    useStatus();

  // Socket listener for async model switching events
  useEffect(() => {
    socketRef.current = io(SOCKET_URL, {
      transports: ["websocket", "polling"],
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
    });

    socketRef.current.on("connect", () => {
      console.log("SettingsPage: Socket connected for model switch events");
    });

    socketRef.current.on("model_switch", (data) => {
      console.log("SettingsPage: Received model_switch event:", data);

      if (data.status === "loading") {
        setModelSwitchStatus("loading");
        setModelSwitchMessage(data.message || `Loading ${data.model}...`);
        showMessage(data.message || `Loading ${data.model}...`, "info");
      } else if (data.status === "complete") {
        setModelSwitchStatus("complete");
        setModelSwitchMessage(data.message || `Model switched to ${data.model}`);
        showMessage(data.message || `Successfully switched to ${data.model}`, "success");
        // Refresh the active model display
        refreshActiveModel();
        // Reset status after a brief delay
        setTimeout(() => {
          setModelSwitchStatus("idle");
          setModelSwitchMessage("");
        }, 2000);
      } else if (data.status === "error") {
        setModelSwitchStatus("error");
        setModelSwitchMessage(data.message || "Failed to switch model");
        showMessage(data.message || "Failed to switch model", "error");
        // Reset status after showing error
        setTimeout(() => {
          setModelSwitchStatus("idle");
          setModelSwitchMessage("");
        }, 5000);
      }
    });

    socketRef.current.on("disconnect", () => {
      console.log("SettingsPage: Socket disconnected");
    });

    return () => {
      if (socketRef.current) {
        socketRef.current.disconnect();
        socketRef.current = null;
      }
    };
  }, [showMessage, refreshActiveModel]);

  const fetchAvailableModels = useCallback(async () => {
    // Avoid clearing import/export notifications when refreshing the list
    try {
      const modelsResult = await apiService.getAvailableModels();
      if (modelsResult?.error)
        throw new Error(`Available models fetch failed: ${modelsResult.error}`);
      let modelsList = Array.isArray(modelsResult)
        ? modelsResult.filter((m) => m && m.name)
        : Array.isArray(modelsResult?.models)
          ? modelsResult.models.filter((m) => m && m.name)
          : [];
      // Ensure the currently active model appears in the dropdown
      if (
        activeModel &&
        activeModel !== "Error" &&
        activeModel !== "N/A" &&
        !modelsList.some((m) => m.name === activeModel)
      ) {
        modelsList = [{ name: activeModel }, ...modelsList];
      }
      setAvailableModels(modelsList);
    } catch (err) {
      console.error("SettingsPage: Failed to load available models:", err);
      showMessage(`Failed to load available models: ${err.message}`, "error");
      setAvailableModels([]);
    }
  }, [activeModel, showMessage]);

  useEffect(() => {
    fetchAvailableModels();
  }, [fetchAvailableModels]);

  // Fetch embedding model info
  useEffect(() => {
    const fetchEmbeddingModel = async () => {
      setIsLoadingEmbeddingModel(true);
      try {
        const response = await fetch("/api/meta/index-info");
        if (response.ok) {
          const data = await response.json();
          setEmbeddingModel(data.embedding_model || "Not Set");
        } else {
          setEmbeddingModel("Not Available");
        }
      } catch (error) {
        console.error("Failed to fetch embedding model:", error);
        setEmbeddingModel("Not Available");
      } finally {
        setIsLoadingEmbeddingModel(false);
      }
    };
    fetchEmbeddingModel();
  }, []);

  // Fetch GPU resources and embedding model list
  const fetchResources = useCallback(async () => {
    // Don't poll if page is hidden or component unmounted
    if (document.hidden) return;
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 8000);
      const r = await fetch("/api/model/resources", { signal: controller.signal });
      clearTimeout(timeoutId);
      if (r.ok) {
        const d = await r.json();
        if (d.success) setGpuResources(d.data);
      }
    } catch (e) {
      if (e.name === 'AbortError') return; // Timeout or navigation — not an error
      // Silently skip network errors during polling — don't spam console
    }
  }, []);

  const fetchEmbeddingModels = useCallback(async () => {
    try {
      const r = await fetch("/api/model/embedding/list");
      if (r.ok) {
        const d = await r.json();
        if (d.success) {
          const models = d.data.models || [];
          setEmbeddingModels(models);
          if (d.data.active) {
            // Match active name to model list (handles "mxbai-embed-large" vs "mxbai-embed-large:latest")
            const active = d.data.active;
            const exact = models.find((m) => m.name === active);
            const partial = models.find((m) => m.name.split(":")[0] === active.split(":")[0]);
            setSelectedEmbeddingModel(exact ? exact.name : partial ? partial.name : active);
          }
        }
      }
    } catch (e) {
      console.warn("Failed to fetch embedding models:", e);
    }
  }, []);

  useEffect(() => {
    fetchResources();
    fetchEmbeddingModels();
    // Refresh resources periodically while on settings page
    const interval = setInterval(fetchResources, 15000);
    return () => clearInterval(interval);
  }, [fetchResources, fetchEmbeddingModels]);

  // Refresh resources after a model switch completes
  useEffect(() => {
    if (modelSwitchStatus === "complete") {
      fetchResources();
    }
  }, [modelSwitchStatus, fetchResources]);

  const handleAutoresearchSettingChange = async (key, value) => {
    const updated = { ...autoresearchSettings, [key]: String(value) };
    setAutoresearchSettings(updated);
    try {
      await ragAutoresearchService.updateSettings({ [key]: String(value) });
    } catch (e) {
      console.error('Failed to update autoresearch setting:', e);
    }
  };

  useEffect(() => {
    const fetchVersion = async () => {
      try {
        const result = await apiService.getVersion();
        if (result?.version) setAppVersion(result.version);
      } catch (err) {
        console.warn("Failed to fetch app version:", err);
      }
    };
    fetchVersion();
  }, []);

  // Listen for chat history cleared events
  useEffect(() => {
    const handleChatHistoryCleared = (event) => {
      console.log("SettingsPage: Chat history cleared event received", event.detail);
      // The chat components will handle their own state clearing via the event
    };

    window.addEventListener('chatHistoryCleared', handleChatHistoryCleared);

    return () => {
      window.removeEventListener('chatHistoryCleared', handleChatHistoryCleared);
    };
  }, []);

  // Fetch Image Generation Status
  useEffect(() => {
    const fetchImageGenStatus = async () => {
      try {
        const response = await fetch("/api/batch-image/status");
        const data = await response.json();
        if (data.success) {
          setImageGenStatus(data.data);
        } else {
          setImageGenStatus({ service_available: false, error: "Failed to get status" });
        }
      } catch (err) {
        setImageGenStatus({ service_available: false, error: err.message });
      }
    };
    fetchImageGenStatus();
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const saved = localStorage.getItem(WEB_SEARCH_ENABLED_KEY);
      if (saved !== null) setWebSearchEnabled(saved === "true");
    } catch (e) {
      console.warn("Failed to load web search setting:", e);
    }
  }, []);

  useEffect(() => {
    const fetchWebAccess = async () => {
      try {
        const result = await apiService.getWebAccess();
        if (result && typeof result.allow_web_search === "boolean") {
          setWebSearchEnabled(result.allow_web_search);
        }
      } catch (err) {
        console.warn("Failed to fetch web access setting from server:", err);
      }
    };
    fetchWebAccess();
  }, []);

  useEffect(() => {
    const fetchAdvDebug = async () => {
      try {
        const result = await apiService.getAdvancedDebug();
        if (result && typeof result.advanced_debug === "boolean") {
          setAdvancedDebug(result.advanced_debug);
        }
      } catch (err) {
        console.warn("Failed to fetch advanced debug setting:", err);
      }
    };
    fetchAdvDebug();
  }, []);

  useEffect(() => {
    const fetchBehaviorLearning = async () => {
      try {
        const result = await apiService.getBehaviorLearning();
        if (result && typeof result.behavior_learning_enabled === "boolean") {
          setBehaviorLearningEnabled(result.behavior_learning_enabled);
        }
      } catch (err) {
        console.warn("Failed to fetch behavior learning setting:", err);
      }
    };
    fetchBehaviorLearning();
  }, []);

  useEffect(() => {
    const fetchLlmDebug = async () => {
      try {
        const result = await apiService.getLlmDebug();
        const enabled = result?.data?.llm_debug ?? result?.llm_debug;
        if (typeof enabled === "boolean") {
          setLlmDebugState(enabled);
        }
      } catch (err) {
        console.warn("Failed to fetch LLM debug setting:", err);
      }
    };
    fetchLlmDebug();
  }, []);

  useEffect(() => {
    const fetchRulesEnabled = async () => {
      try {
        const result = await apiService.getRulesEnabled();
        const enabled = result?.data?.rules_enabled ?? result?.rules_enabled;
        if (typeof enabled === "boolean") {
          setRulesEnabledState(enabled);
          try {
            localStorage.setItem(RULES_ENABLED_KEY, String(enabled));
          } catch {
            // localStorage may be unavailable; the backend remains authoritative
          }
        }
      } catch (err) {
        console.warn("Failed to fetch rules_enabled setting:", err);
      }
    };
    fetchRulesEnabled();
  }, []);

  useEffect(() => {
    const fetchRagFeatures = async () => {
      try {
        // Load comprehensive RAG features
        const result = await getRagFeatures();
        if (result && !result.error) {
          // Extract data from the response wrapper
          const data = result.data || result;
          if (typeof data.enhanced_context === "boolean") {
            setEnhancedContext(data.enhanced_context);
          }
          if (typeof data.advanced_rag === "boolean") {
            setAdvancedRag(data.advanced_rag);
          }
          if (typeof data.rag_debug === "boolean") {
            setRagDebug(data.rag_debug);
          }
        } else {
          // Fallback to individual RAG debug call if new API fails
          const ragResult = await getRagDebug();
          if (ragResult && typeof ragResult.rag_debug_enabled === "boolean") {
            setRagDebug(ragResult.rag_debug_enabled);
          }
        }
      } catch (err) {
        console.warn("Failed to fetch RAG features:", err);
        // Fallback to individual call
        try {
          const ragResult = await getRagDebug();
          if (ragResult && typeof ragResult.rag_debug_enabled === "boolean") {
            setRagDebug(ragResult.rag_debug_enabled);
          }
        } catch (fallbackErr) {
          console.warn("Failed to fetch RAG debug setting:", fallbackErr);
        }
      }
    };
    fetchRagFeatures();
  }, []);

  // Persist web search toggle whenever it changes
  useEffect(() => {
    try {
      localStorage.setItem(WEB_SEARCH_ENABLED_KEY, String(webSearchEnabled));
    } catch (e) {
      console.warn("Failed to persist web search setting:", e);
    }
  }, [webSearchEnabled]);

  useEffect(() => {
    try {
      localStorage.setItem(ADV_DEBUG_ENABLED_KEY, String(advancedDebug));
    } catch (e) {
      console.warn("Failed to persist advanced debug setting:", e);
    }
  }, [advancedDebug]);

  useEffect(() => {
    try {
      localStorage.setItem(
        BEHAVIOR_LEARNING_ENABLED_KEY,
        String(behaviorLearningEnabled),
      );
    } catch (e) {
      console.warn("Failed to persist behavior learning setting:", e);
    }
  }, [behaviorLearningEnabled]);

  useEffect(() => {
    try {
      localStorage.setItem(LLM_DEBUG_ENABLED_KEY, String(llmDebug));
    } catch (e) {
      console.warn("Failed to persist LLM debug setting:", e);
    }
  }, [llmDebug]);

  // Auto-save voice settings whenever they change
  useEffect(() => {
    try {
      localStorage.setItem(VOICE_SETTINGS_KEY, JSON.stringify(voiceSettings));
      // Notify voice components in the same tab (storage events only fire cross-tab)
      window.dispatchEvent(new Event('voiceSettingsChanged'));
    } catch (e) {
      console.warn("Failed to persist voice settings:", e);
    }
  }, [voiceSettings]);

  useEffect(() => {
    if (
      !isLoadingModel &&
      activeModel &&
      activeModel !== "Error" &&
      activeModel !== "N/A" &&
      availableModels.length > 0
    ) {
      const modelExists = availableModels.some(
        (model) => model.name === activeModel,
      );
      if (modelExists) setSelectedModel(activeModel);
      else setSelectedModel(activeModel);
    } else if (
      !isLoadingModel &&
      (!activeModel || activeModel === "Error" || activeModel === "N/A")
    ) {
      setSelectedModel("");
    }
  }, [activeModel, isLoadingModel, availableModels]);

  const handleActionClick = async (
    actionFunction,
    actionArgs = [],
    confirmMessage,
    loadingMessage,
    successMessage,
    failureMessagePrefix,
  ) => {
    if (confirmMessage && !window.confirm(confirmMessage)) return;

    setIsLoading(true); // Use general isLoading for these actions as well
    showMessage(loadingMessage || "Processing...", "info");
    try {
      const result = await actionFunction(...actionArgs);
      if (result?.error && !result.warning && result.error !== "User aborted") {
        // Prevent error on user abort for import
        throw new Error(result.error.message || result.error);
      }
      const message =
        result?.warning ||
        result?.message ||
        successMessage ||
        "Action completed successfully.";
      const severity = result?.warning ? "warning" : "success";

      showMessage(message, severity);

      if (actionFunction === apiService.setModel) {
        refreshActiveModel();
      }
    } catch (err) {
      if (err.message !== "User aborted") {
        // Don't show error if user cancelled file dialog
        showMessage(`${failureMessagePrefix}: ${err.message}`, "error");
      }
    } finally {
      if (actionFunction !== apiService.triggerReboot) {
        setIsLoading(false);
      }
    }
  };

  const handleSetModelClick = async () => {
    if (!selectedModel) {
      showMessage("Please select a model first.", "warning");
      return;
    }

    // Model switching is now async - the backend returns 202 immediately
    // and sends socket events for progress updates
    setModelSwitchStatus("loading");
    setModelSwitchMessage(`Switching to ${selectedModel}...`);

    try {
      const result = await apiService.setModel(selectedModel);
      // Backend returns 202 for async processing
      if (result?.status === "switching" || result?.message?.includes("Switching")) {
        // Socket events will handle the rest
        console.log("Model switch initiated, waiting for socket events...");
      } else if (result?.error) {
        // Immediate error (e.g., model not found)
        setModelSwitchStatus("error");
        setModelSwitchMessage(result.error);
        showMessage(result.error, "error");
        setTimeout(() => {
          setModelSwitchStatus("idle");
          setModelSwitchMessage("");
        }, 5000);
      }
    } catch (err) {
      console.error("Failed to initiate model switch:", err);
      setModelSwitchStatus("error");
      setModelSwitchMessage(err.message || "Failed to switch model");
      showMessage(err.message || "Failed to switch model", "error");
      setTimeout(() => {
        setModelSwitchStatus("idle");
        setModelSwitchMessage("");
      }, 5000);
    }
  };
  const handleRebootClick = () => {
    setRebootDialogOpen(true);
  };

  const handleConfirmReboot = () => {
    // Close the confirmation dialog
    setRebootDialogOpen(false);

    // Open the progress modal (it will handle the reboot streaming itself)
    setRebootProgressModalOpen(true);
  };

  const handleCancelReboot = () => {
    if (!rebootInProgress) {
      setRebootDialogOpen(false);
    }
  };

  const handleRebootProgressModalClose = () => {
    setRebootProgressModalOpen(false);
    setRebootInProgress(false);
  };
  const handleClearChatHistoryClick = async () => {
    const counts = await apiService.getChatHistoryCounts();
    let confirmMsg = "Clear ALL chat history? This cannot be undone.";
    if (counts && !counts.error) {
      const parts = [];
      if (counts.messages) parts.push(`${counts.messages} messages`);
      if (counts.sessions) parts.push(`${counts.sessions} sessions`);
      const files = (counts.context_files || 0) + (counts.conversation_files || 0);
      if (files) parts.push(`${files} cached files`);
      if (parts.length > 0) {
        confirmMsg = `Clear ALL chat history (${parts.join(", ")})? This cannot be undone.`;
      } else {
        confirmMsg = "No chat history found. Clear anyway?";
      }
    }
    handleActionClick(
      apiService.clearChatHistory,
      ["all"],
      confirmMsg,
      "Clearing chat history...",
      "Chat history cleared successfully.",
      "Failed to clear chat history",
    );
  };
  const handleResetIndexClick = () => {
    /* ... (unchanged from v3.4) ... */
    handleActionClick(
      apiService.resetIndexStorage,
      [],
      "Reset the LlamaIndex vector store? ALL indexed document knowledge will be lost and require re-indexing.",
      "Resetting index storage...",
      "Index storage cleared. Please re-index documents.",
      "Failed to reset index storage",
    );
  };

  const handleOpenPurgeModal = () => {
    setPurgeModalOpen(true);
  };

  const handleClosePurgeModal = () => {
    if (!isPurging) setPurgeModalOpen(false);
  };

  const handleConfirmPurge = async (options) => {
    setIsPurging(true);
    await handleActionClick(
      apiService.purgeIndex,
      [options],
      null,
      "Purging index...",
      "Index purge completed.",
      "Failed to purge index",
    );
    setIsPurging(false);
    setPurgeModalOpen(false);
  };
  const handlePurgeBehaviorLearningClick = () => {
    handleActionClick(
      apiService.purgeBehaviorLearning,
      [],
      "Purge all learned behaviors? This cannot be undone.",
      "Purging learned behaviors...",
      "Learned behaviors purged.",
      "Failed to purge learned behaviors",
    );
  };

  const handleClearBehaviorLogClick = () => {
    handleActionClick(
      clearBehaviorLog,
      [],
      "Clear the user behavior log file? This cannot be undone.",
      "Clearing behavior log...",
      "Behavior log cleared successfully",
      "Failed to clear behavior log",
    );
  };

  // Support both Chip clicks (no checked field) and Switch/Checkbox events
  const deriveToggleValue = (eventOrValue, currentValue) => {
    if (typeof eventOrValue === "boolean") return eventOrValue;
    if (
      eventOrValue &&
      typeof eventOrValue === "object" &&
      typeof eventOrValue.target?.checked === "boolean"
    ) {
      return eventOrValue.target.checked;
    }
    return !currentValue;
  };
  const handleClearPycacheFoldersClick = async () => {
    if (
      !window.confirm(
        "Clear Python bytecode cache (__pycache__)? This can help apply code changes but does not affect data or memory."
      )
    ) {
      return;
    }

    setIsLoading(true);
    showMessage("Clearing Python cache folders...", "info");
    try {
      const result = await apiService.clearPycache();
      if (result?.error && !result.warning && result.error !== "User aborted") {
        throw new Error(result.error.message || result.error);
      }

      // Build detailed success message with statistics
      let message = result?.message || "Python cache folders cleared successfully.";

      if (result?.statistics) {
        const stats = result.statistics;
        const details = [];

        if (stats.directories_cleaned > 0) {
          details.push(
            `${stats.directories_cleaned} directory(ies) cleaned`
          );
        }

        if (stats.pyc_files_deleted > 0) {
          details.push(
            `${stats.pyc_files_deleted} .pyc file(s) deleted`
          );
        }

        if (stats.size_formatted) {
          details.push(`${stats.size_formatted} freed`);
        }

        if (result?.modules_purged_count > 0) {
          details.push(`${result.modules_purged_count} module(s) purged from memory`);
        }

        if (details.length > 0) {
          message = `Cache cleared: ${details.join(", ")}.`;
        }

        if (result?.locations_cleaned && result.locations_cleaned.length > 0) {
          const locationCount = result.locations_cleaned.length;
          if (locationCount <= 5) {
            message += ` Locations: ${result.locations_cleaned.join(", ")}.`;
          } else {
            message += ` ${locationCount} locations cleaned.`;
          }
        }
      }

      if (result?.errors && result.errors.length > 0) {
        message += ` Note: ${result.errors.length} error(s) encountered.`;
      }

      const severity = result?.warning ? "warning" : "success";
      showMessage(message, severity);
    } catch (err) {
      if (err.message !== "User aborted") {
        showMessage(`Failed to clear Python cache folders: ${err.message}`, "error");
      }
    } finally {
      setIsLoading(false);
    }
  };
  const handleRagDebugChange = async (event) => {
    const isEnabled = deriveToggleValue(event, ragDebug);

    try {
      // Update backend first
      const result = await setRagDebugAPI(isEnabled);

      if (result.error) {
        throw new Error(result.error);
      }

      // Update local state only if backend update succeeds
      setRagDebug(isEnabled);

      console.log("RAG Debug Mode Toggled:", isEnabled);
      showMessage(
        `RAG Debug mode ${isEnabled ? "enabled" : "disabled"}.`,
        "success",
      );
    } catch (err) {
      console.error("Failed to update RAG debug setting:", err);
      showMessage(
        `Failed to ${isEnabled ? "enable" : "disable"} RAG Debug mode: ${err.message}`,
        "error",
      );
      // Don't update local state if backend update failed
    }
  };

  const handleEnhancedContextChange = async (event) => {
    const isEnabled = deriveToggleValue(event, enhancedContext);

    try {
      const result = await updateRagFeatures({ enhanced_context: isEnabled });

      if (result.error) {
        throw new Error(result.error);
      }

      setEnhancedContext(isEnabled);

      showMessage(
        `Enhanced Context ${isEnabled ? "enabled" : "disabled"}. Advanced memory features are now ${isEnabled ? "active" : "inactive"}.`,
        "success",
      );
    } catch (err) {
      console.error("Failed to update Enhanced Context setting:", err);
      showMessage(
        `Failed to ${isEnabled ? "enable" : "disable"} Enhanced Context: ${err.message}`,
        "error",
      );
    }
  };

  const handleAdvancedRagChange = async (event) => {
    const isEnabled = deriveToggleValue(event, advancedRag);

    try {
      const result = await updateRagFeatures({ advanced_rag: isEnabled });

      if (result.error) {
        throw new Error(result.error);
      }

      setAdvancedRag(isEnabled);

      showMessage(
        `Advanced RAG ${isEnabled ? "enabled" : "disabled"}. Enhanced retrieval and chunking are now ${isEnabled ? "active" : "inactive"}.`,
        "success",
      );
    } catch (err) {
      console.error("Failed to update Advanced RAG setting:", err);
      showMessage(
        `Failed to ${isEnabled ? "enable" : "disable"} Advanced RAG: ${err.message}`,
        "error",
      );
    }
  };

  const handleWebSearchToggle = (nextValue) => {
    const isEnabled = typeof nextValue === "boolean"
      ? nextValue
      : !webSearchEnabled;

    setWebSearchEnabled(isEnabled);
    try {
      localStorage.setItem(WEB_SEARCH_ENABLED_KEY, String(isEnabled));
      apiService
        .setWebAccess(isEnabled)
        .catch((err) =>
          console.warn("Failed to update web access setting:", err),
        );
    } catch (e) {
      console.warn("Failed to persist web search setting:", e);
    }
    console.log("Web Search Toggled (local state):", isEnabled);
    showMessage(
      `Web Search ${isEnabled ? "enabled" : "disabled"} (UI only).`,
      "info",
    );
  };

  const handleAdvancedDebugToggle = (event) => {
    const isEnabled = deriveToggleValue(event, advancedDebug);
    setAdvancedDebug(isEnabled);
    try {
      localStorage.setItem(ADV_DEBUG_ENABLED_KEY, String(isEnabled));
      apiService
        .setAdvancedDebug(isEnabled)
        .catch((err) =>
          console.warn("Failed to update advanced debug setting:", err),
        );
    } catch (e) {
      console.warn("Failed to persist advanced debug setting:", e);
    }
    console.log("Advanced Debug Toggled (local state):", isEnabled);
    showMessage(
      `Advanced debugging ${isEnabled ? "enabled" : "disabled"}.`,
      "info",
    );
  };
  const handleLlmDebugToggle = (event) => {
    const isEnabled = deriveToggleValue(event, llmDebug);
    setLlmDebugState(isEnabled);
    try {
      localStorage.setItem(LLM_DEBUG_ENABLED_KEY, String(isEnabled));
      apiService
        .setLlmDebug(isEnabled)
        .catch((err) =>
          console.warn("Failed to update LLM debug setting:", err),
        );
    } catch (e) {
      console.warn("Failed to persist LLM debug setting:", e);
    }
    showMessage(
      `LLM debug logging ${isEnabled ? "enabled" : "disabled"}.`,
      "info",
    );
  };
  const handleBehaviorLearningToggle = (event) => {
    const isEnabled = deriveToggleValue(event, behaviorLearningEnabled);
    setBehaviorLearningEnabled(isEnabled);
    try {
      localStorage.setItem(BEHAVIOR_LEARNING_ENABLED_KEY, String(isEnabled));
      apiService
        .setBehaviorLearning(isEnabled)
        .catch((err) =>
          console.warn("Failed to update behavior learning setting:", err),
        );
    } catch (e) {
      console.warn("Failed to persist behavior learning setting:", e);
    }
    showMessage(
      `Behavior learning ${isEnabled ? "enabled" : "disabled"}.`,
      "info",
    );
  };

  // Helper function to get category icon
  const getCategoryIcon = (categoryKey) => {
    const iconMap = {
      core_system: <SystemIcon />,
      api_health: <ApiIcon />,
      file_processing: <FolderIcon />,
      chat_system: <ChatIcon />,
      security: <SecurityIcon />,
      performance: <TrendingUpIcon />
    };
    return iconMap[categoryKey] || <HelpOutlineIcon />;
  };

  // Helper function to get status color and icon
  const getStatusDisplay = (status) => {
    const statusMap = {
      pass: { color: "success", icon: <CheckCircleOutlineIcon />, label: "Pass" },
      fail: { color: "error", icon: <ErrorOutlineIcon />, label: "Failed" },
      warning: { color: "warning", icon: <WarningIcon />, label: "Warning" },
      error: { color: "error", icon: <SyncProblemIcon />, label: "Error" },
      critical: { color: "error", icon: <ErrorOutlineIcon />, label: "Critical" },
      partial: { color: "warning", icon: <WarningIcon />, label: "Partial" },
      skip: { color: "default", icon: <InfoOutlinedIcon />, label: "Skipped" },
    };
    const key = typeof status === "string" ? status.toLowerCase() : "";
    return statusMap[key] || { color: "default", icon: <InfoOutlinedIcon />, label: status || "Unknown" };
  };

  // Enhanced test runner with mode selection
  const handleRunSystemCheck = async (mode = "basic") => {
    // BUG FIX #1: Validate mode parameter
    const validModes = ["basic", "quick", "comprehensive"];
    const validatedMode = validModes.includes(mode) ? mode : "basic";

    setIsTesting(true);
    setTestResults(null);
    setTestMode(validatedMode);
    setExpandedCategories({}); // BUG FIX #7: Reset expanded categories

    const modeLabels = {
      basic: "basic system checks",
      quick: "quick validation",
      comprehensive: "comprehensive testing"
    };
    showMessage(`Running ${modeLabels[validatedMode] || "system checks"}...`, "info");

    try {
      // Call enhanced API with mode parameter
      const response = await apiService.runSelfTest({
        mode: mode,
        include_legacy: true
      });

      if (response?.error && typeof response.error === "string")
        throw new Error(response.error);
      if (response?.results && typeof response.results === "object") {
        setTestResults(response.results);

        // Enhanced success message with status
        const overallStatus = response.results.overall_status || "UNKNOWN";
        const statusDisplay = getStatusDisplay(overallStatus);
        let msg = `System Check Complete - ${statusDisplay.label}`;

        if (response.results.categories) {
          const categoryCount = Object.keys(response.results.categories).length;
          msg += ` (${categoryCount} categories tested)`;
        }

        const severity = overallStatus === "PASS" ? "success" :
          overallStatus === "WARNING" ? "warning" : "error";
        showMessage(msg, severity);
      } else {
        throw new Error("System check did not return valid results.");
      }
    } catch (error) {
      showMessage(
        `System Check Error: ${error.message || "Could not run system checks."}`,
        "error",
      );
      setTestResults({
        error: `Failed to run system checks: ${error.message}`,
      });
    } finally {
      setIsTesting(false);
    }
  };

  const handleRunAllTests = async () => {
    setIsRunningTests(true);
    setTestSuiteResults(null);
    showMessage("Running full test suite...", "info");
    try {
      const response = await apiService.runAllTests();
      if (response?.results) {
        setTestSuiteResults(response.results);
        const sev = response.results.returncode === 0 ? "success" : "error";
        showMessage("Test suite finished.", sev);
      } else {
        throw new Error("Invalid response");
      }
    } catch (err) {
      showMessage(`Test suite error: ${err.message}`, "error");
      setTestSuiteResults({ error: err.message });
    } finally {
      setIsRunningTests(false);
    }
  };
  const renderStatusIcon = (value, detailsForKey = "") => {
    /* ... (unchanged from v3.4) ... */
    const details = String(detailsForKey).toLowerCase();

    if (typeof value === "boolean") {
      return value ? (
        <CheckCircleOutlineIcon
          sx={{ color: "success.main", verticalAlign: "middle" }}
        />
      ) : (
        <ErrorOutlineIcon
          sx={{ color: "error.main", verticalAlign: "middle" }}
        />
      );
    }
    if (typeof value === "string") {
      const lowerValue = value.toLowerCase();
      if (
        [
          "ok",
          "good",
          "healthy",
          "accessible",
          "loadable",
          "active",
          "true",
          "responsive",
          "idle / queue empty",
          "no recent indexing errors found in db",
          "no error/critical messages in last ~200 lines",
        ].some((s) => lowerValue.includes(s))
      ) {
        return (
          <CheckCircleOutlineIcon
            sx={{ color: "success.main", verticalAlign: "middle" }}
          />
        );
      }
      if (
        [
          "error",
          "failed",
          "unhealthy",
          "inaccessible",
          "critical",
          "false",
          "db error",
        ].some((s) => lowerValue.includes(s)) ||
        lowerValue.startsWith("error:") ||
        lowerValue.includes("error(s). examples:")
      ) {
        return (
          <ErrorOutlineIcon
            sx={{ color: "error.main", verticalAlign: "middle" }}
          />
        );
      }
      if (
        [
          "warning",
          "degraded",
          "not configured",
          "configured but not responsive/empty response",
          "items pending/indexing",
        ].some((s) => lowerValue.includes(s))
      ) {
        return (
          <SyncProblemIcon
            sx={{ color: "warning.main", verticalAlign: "middle" }}
          />
        );
      }
      if (lowerValue.includes("unknown") || lowerValue.includes("n/a")) {
        return (
          <HelpOutlineIcon
            sx={{ color: "text.secondary", verticalAlign: "middle" }}
          />
        );
      }
    }
    if (details) {
      if (
        ["ok", "found", "connected", "accessible", "responsive", "idle"].some(
          (s) => details.includes(s),
        )
      ) {
        return (
          <CheckCircleOutlineIcon
            sx={{ color: "success.main", verticalAlign: "middle" }}
          />
        );
      }
      if (
        [
          "failed",
          "error",
          "inaccessible",
          "not found/empty",
          "not found or not active for the current model",
        ].some((s) => details.includes(s))
      ) {
        return (
          <ErrorOutlineIcon
            sx={{ color: "error.main", verticalAlign: "middle" }}
          />
        );
      }
      if (
        details.includes("pending/indexing") ||
        details.includes("not configured")
      ) {
        return (
          <SyncProblemIcon
            sx={{ color: "warning.main", verticalAlign: "middle" }}
          />
        );
      }
    }
    return (
      <InfoOutlinedIcon
        sx={{ color: "text.secondary", verticalAlign: "middle" }}
      />
    );
  };
  const systemCheckItems = [
    /* ... (unchanged from v3.4) ... */
    {
      key: "ollama_reachable",
      label: "Ollama Service Reachable",
      icon: <DnsIcon />,
      format: (v) => (v ? "OK" : "Failed"),
    },
    {
      key: "active_model_name",
      label: "Active LLM Name",
      icon: <DnsIcon />,
      format: (v) => v || "N/A",
    },
    {
      key: "active_model_status",
      label: "Active LLM Status",
      icon: <DnsIcon />,
      format: (v) => v || "Unknown",
    },
    {
      key: "active_model_health",
      label: "Ollama Model Loaded",
      icon: <DnsIcon />,
      format: (v) => v || "Unknown",
    },
    {
      key: "llm_basic_response",
      label: "LLM Basic Response Test",
      icon: <DnsIcon />,
      format: (v) => (v ? "OK" : "Failed/Empty"),
    },
    {
      key: "model_count",
      label: "Discovered Ollama Models",
      format: (v) => `${v ?? "N/A"} models found`,
    },
    {
      key: "db_connection",
      label: "Database Connection",
      icon: <StorageIcon />,
      format: (v) => (v ? "OK" : "Failed"),
    },
    {
      key: "document_count_db",
      label: "Document Count (DB)",
      format: (v) => `${v ?? "N/A"} documents in DB`,
    },
    {
      key: "storage_dir_accessible",
      label: "Storage Directory",
      icon: <StorageIcon />,
      format: (v, r) =>
        `${r.storage_dir_path || "N/A"} (${v ? "Accessible" : "Inaccessible"})`,
    },
    {
      key: "upload_dir_accessible",
      label: "Upload Directory",
      icon: <StorageIcon />,
      format: (v, r) =>
        `${r.upload_dir_path || "N/A"} (${v ? "Accessible" : "Inaccessible"})`,
    },
    {
      key: "output_dir_accessible",
      label: "Output Directory",
      icon: <StorageIcon />,
      format: (v, r) =>
        `${r.output_dir_path || "N/A"} (${v ? "Accessible" : "Inaccessible"})`,
    },
    {
      key: "index_storage_exists",
      label: "Index Storage Exists",
      icon: <SpeedIcon />,
      format: (v) => (v ? "OK" : "Not Found/Empty"),
    },
    {
      key: "qa_prompt_loadable",
      label: "QA Default Prompt",
      format: (v) => (v ? "Loadable" : "Not Found/Error"),
    },
    {
      key: "indexing_queue_status",
      label: "Indexing Queue",
      icon: <SpeedIcon />,
      format: (v) => v || "N/A",
    },
    {
      key: "recent_indexing_errors",
      label: "Recent Indexing Errors",
      icon: <ErrorOutlineIcon />,
      format: (v) => v || "N/A",
    },
    {
      key: "backend_log_errors",
      label: "Backend Log Criticals",
      icon: <ErrorOutlineIcon />,
      format: (v) => v || "N/A",
    },
    {
      key: "gpu_tools_available",
      label: "GPU Monitor Available",
      icon: <SpeedIcon />,
      format: (v) => (v ? "Available" : "Unavailable"),
    },
    {
      key: "last_metrics_fetch_status",
      label: "Last Metrics Fetch",
      icon: <SpeedIcon />,
      format: (v) => v || "N/A",
    },
  ];

  // --- NEW HANDLERS FOR IMPORT/EXPORT ---
  const handleExportRulesClick = async () => {
    setIsExporting(true);
    showMessage("Exporting rules...", "info");
    try {
      const result = await apiService.exportRules();
      if (result?.error) throw new Error(result.error);
      if (!result?.rules || !Array.isArray(result.rules))
        throw new Error("Invalid export format received from server.");

      const jsonString = JSON.stringify(result, null, 2); // result already contains {"rules": []}
      const blob = new Blob([jsonString], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const date = new Date().toISOString().slice(0, 19).replace(/:/g, "-");
      a.download = `guaardvark_rules_export_${date}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showMessage(
        `Successfully exported ${result.rules.length} rules.`,
        "success",
      );
    } catch (err) {
      console.error("Error exporting rules:", err);
      showMessage(`Export failed: ${err.message}`, "error");
    } finally {
      setIsExporting(false);
    }
  };

  const handleFileSelectForImport = (event) => {
    const file = event.target.files?.[0];
    if (file) {
      setSelectedFileForImport(file);
      setSelectedFileNameForImport(file.name);
      closeSnackbar();
    } else {
      setSelectedFileForImport(null);
      setSelectedFileNameForImport("");
    }
  };

  const triggerFileImportInput = () => {
    fileImportInputRef.current?.click();
  };

  const handleImportRulesClick = async () => {
    if (!selectedFileForImport) {
      showMessage("Please select a JSON file to import.", "warning");
      return;
    }
    setIsImporting(true);
    showMessage(`Importing rules from ${selectedFileNameForImport}...`, "info");

    try {
      const fileContent = await selectedFileForImport.text();
      const jsonData = JSON.parse(fileContent);
      if (!jsonData || !Array.isArray(jsonData.rules)) {
        throw new Error(
          "Invalid JSON format. Expected an object with a 'rules' array.",
        );
      }

      const result = await apiService.importRules({ rules: jsonData.rules });

      if (result?.error && !result.created && !result.updated) {
        throw new Error(
          result.error.message || result.error.details || result.error,
        );
      }

      let importSummary = result.message || "Import process completed.";
      if (result.created) importSummary += ` Created: ${result.created}.`;
      if (result.updated) importSummary += ` Updated: ${result.updated}.`;
      if (result.skipped)
        importSummary += ` Skipped/Errors: ${result.skipped}.`;
      if (result.errors && result.errors.length > 0) {
        console.error("Import errors:", result.errors);
        importSummary += ` Details: ${result.errors.join("; ")}`;
        showMessage(importSummary, "warning");
      } else {
        showMessage(importSummary, "success");
      }
    } catch (err) {
      console.error("Error importing rules:", err);
      showMessage(`Import failed: ${err.message}`, "error");
    } finally {
      setIsImporting(false);
      setSelectedFileForImport(null);
      setSelectedFileNameForImport("");
      if (fileImportInputRef.current) fileImportInputRef.current.value = "";
    }
  };

  const fetchBackupList = async () => {
    try {
      const res = await apiService.listServerBackups();
      setBackupList(res.backups || []);
    } catch (e) {
      console.error(e);
      setBackupList([]);
    }
  };
  const openCreateBackup = () => setCreateBackupOpen(true);
  const openRestoreBackup = async () => {
    await fetchBackupList();
    setRestoreBackupOpen(true);
  };
  const openManageBackups = async () => {
    await fetchBackupList();
    setManageBackupsOpen(true);
  };
  const closeManageBackups = () => setManageBackupsOpen(false);

  const handleCreateBackupConfirm = async ({ type, components, name, include_plugins }) => {
    setIsProcessingBackup(true);
    try {
      const res = await apiService.createServerBackup(type, components, name, include_plugins);
      showMessage(`Backup created: ${res.file}`, "success");
    } catch (e) {
      showMessage(e.message, "error");
    }
    setIsProcessingBackup(false);
    setCreateBackupOpen(false);
  };

  const handleRestoreConfirm = async (file) => {
    setIsProcessingBackup(true);
    try {
      await apiService.restoreServerBackup(file);
      showMessage("Restore complete", "success");
    } catch (e) {
      showMessage(e.message, "error");
    }
    setIsProcessingBackup(false);
    setRestoreBackupOpen(false);
  };
  // --- END NEW HANDLERS ---

  // Component to render categorized test results
  const renderCategorizedResults = (results) => {
    if (!results.categories) return null;

    return (
      <Box mt={2}>
        {/* Overall Status Banner */}
        {results.overall_status && (
          <Box mb={2}>
            <Chip
              icon={getStatusDisplay(results.overall_status).icon}
              label={`Overall Status: ${getStatusDisplay(results.overall_status).label}`}
              color={getStatusDisplay(results.overall_status).color}
              variant="outlined"
              size="medium"
              sx={{ mb: 1, mr: 1 }}
            />
            {results.execution_time && (
              <Chip
                icon={<SpeedIcon />}
                label={`${results.execution_time.toFixed(2)}s`}
                variant="outlined"
                size="small"
              />
            )}
          </Box>
        )}

        {/* Test Categories */}
        {Object.entries(results.categories).map(([categoryKey, categoryData]) => {
          const isExpanded = expandedCategories[categoryKey] || false;
          const statusDisplay = getStatusDisplay(categoryData.status);

          return (
            <Accordion
              key={categoryKey}
              expanded={isExpanded}
              onChange={() => setExpandedCategories(prev => ({
                ...prev,
                [categoryKey]: !prev[categoryKey]
              }))}
              sx={{ mb: 1 }}
            >
              <AccordionSummary
                expandIcon={<ExpandMoreIcon />}
                sx={{
                  backgroundColor: theme =>
                    statusDisplay.color === 'success' ? theme.palette.success.light :
                      statusDisplay.color === 'error' ? theme.palette.error.light :
                        statusDisplay.color === 'warning' ? theme.palette.warning.light :
                          'transparent',
                  '&.Mui-expanded': { minHeight: 48 }
                }}
              >
                <Box display="flex" alignItems="center" gap={1} width="100%">
                  {getCategoryIcon(categoryKey)}
                  <Typography variant="h6" sx={{ flexGrow: 1 }}>
                    {categoryData.name || categoryKey}
                  </Typography>
                  <Chip
                    icon={statusDisplay.icon}
                    label={statusDisplay.label}
                    color={statusDisplay.color}
                    size="small"
                    variant="filled"
                  />
                  <Typography variant="body2" color="text.secondary">
                    {categoryData.duration?.toFixed(2)}s
                  </Typography>
                </Box>
              </AccordionSummary>
              <AccordionDetails>
                <Typography variant="body2" color="text.secondary" gutterBottom>
                  {categoryData.summary}
                </Typography>

                {/* Individual Test Results */}
                {categoryData.tests && categoryData.tests.length > 0 && (
                  <TableContainer component={Paper} variant="outlined" sx={{ mt: 1 }}>
                    <Table size="small">
                      <TableHead>
                        <TableRow>
                          <TableCell>Status</TableCell>
                          <TableCell>Test</TableCell>
                          <TableCell>Duration</TableCell>
                          <TableCell>Details</TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {categoryData.tests.map((test, index) => {
                          const testStatus = getStatusDisplay(test.status);
                          return (
                            <TableRow key={index}>
                              <TableCell>
                                <Chip
                                  icon={testStatus.icon}
                                  label={testStatus.label}
                                  color={testStatus.color}
                                  size="small"
                                  variant="outlined"
                                />
                              </TableCell>
                              <TableCell>
                                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                                  {test.name.replace(/^.*\//, '')}
                                </Typography>
                              </TableCell>
                              <TableCell>
                                <Typography variant="body2">
                                  {test.duration?.toFixed(2)}s
                                </Typography>
                              </TableCell>
                              <TableCell>
                                <Typography variant="body2" color="text.secondary">
                                  {test.details || '-'}
                                  {test.error_message && (
                                    <Typography variant="caption" color="error" display="block">
                                      {test.error_message}
                                    </Typography>
                                  )}
                                </Typography>
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </TableContainer>
                )}
              </AccordionDetails>
            </Accordion>
          );
        })}
      </Box>
    );
  };

  return (
    <PageLayout
      title="Settings"
      variant="standard"
      actions={
        appVersion ? (
          <Typography variant="caption" color="text.disabled">v{appVersion}</Typography>
        ) : null
      }
    >
      <Box sx={{ overflow: "auto", p: { xs: 1.5, sm: 2.5 }, pb: 4 }}>
        {interconnectorPendingCount > 0 && (
          <MuiAlert
            severity="warning"
            icon={<WarningIcon fontSize="inherit" />}
            sx={{ mb: 2, cursor: "pointer" }}
            onClick={() => setInterconnectorModalOpen(true)}
          >
            Updates Available — {interconnectorPendingCount} Interconnector update{interconnectorPendingCount !== 1 ? "s" : ""} pending — click to review
          </MuiAlert>
        )}
        {interconnectorUpdateStatus?.available && (
          <Box
            onClick={() => setInterconnectorModalOpen(true)}
            sx={{
              bgcolor: "#FFD700",
              borderRadius: 1,
              px: 2,
              py: 1,
              mb: 2,
              display: "flex",
              alignItems: "center",
              gap: 1.5,
              cursor: "pointer",
              "&:hover": { bgcolor: "#E6C200" },
            }}
          >
            <FileDownloadIcon sx={{ fontSize: 22, color: "#000" }} />
            <Typography variant="body2" sx={{ color: "#000", fontWeight: 600 }}>
              {interconnectorUpdateStatus.count} Code Update{interconnectorUpdateStatus.count !== 1 ? "s" : ""} Available
            </Typography>
            <Box display="flex" gap={0.5} ml={0.5}>
              {interconnectorUpdateStatus.summary?.backend > 0 && (
                <Chip
                  label={`${interconnectorUpdateStatus.summary.backend} backend`}
                  size="small"
                  sx={{
                    bgcolor: "rgba(0,0,0,0.15)",
                    color: "#000",
                    borderRadius: 1,
                    height: 20,
                    "& .MuiChip-label": { px: 0.75, fontSize: "0.7rem" },
                  }}
                />
              )}
              {interconnectorUpdateStatus.summary?.frontend > 0 && (
                <Chip
                  label={`${interconnectorUpdateStatus.summary.frontend} frontend`}
                  size="small"
                  sx={{
                    bgcolor: "rgba(0,0,0,0.15)",
                    color: "#000",
                    borderRadius: 1,
                    height: 20,
                    "& .MuiChip-label": { px: 0.75, fontSize: "0.7rem" },
                  }}
                />
              )}
              {interconnectorUpdateStatus.summary?.other > 0 && (
                <Chip
                  label={`${interconnectorUpdateStatus.summary.other} other`}
                  size="small"
                  sx={{
                    bgcolor: "rgba(0,0,0,0.15)",
                    color: "#000",
                    borderRadius: 1,
                    height: 20,
                    "& .MuiChip-label": { px: 0.75, fontSize: "0.7rem" },
                  }}
                />
              )}
            </Box>
            <Typography variant="caption" sx={{ color: "rgba(0,0,0,0.6)", ml: "auto" }}>
              Click to review
            </Typography>
          </Box>
        )}
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: "20px", "& > *": { width: 840, flexShrink: 0 } }}>
          <SettingsCardWrapper title="System">
              <SettingsRow label="Profile">
                <input
                  type="file"
                  accept="image/*"
                  onChange={(e) => {
                    const file = e.target.files[0];
                    if (file) {
                      setBrandingFile(file);
                      (async () => {
                        try {
                          const fd = new FormData();
                          fd.append("logo", file);
                          if (brandingName.trim()) fd.append("system_name", brandingName.trim());
                          await updateBranding(fd);
                          setBrandingFile(null);
                          e.target.value = "";
                          const refreshed = await fetchBranding();
                          const latestLogo = refreshed?.logo_path ?? systemLogo ?? persistedSystemLogo ?? null;
                          setSystemInfo(brandingName || persistedSystemName || "", latestLogo);
                          showMessage("Profile image updated", "success");
                        } catch (err) {
                          showMessage("Failed to update image: " + err.message, "error");
                        }
                      })();
                    }
                  }}
                  style={{ display: "none" }}
                  id="logo-upload"
                />
                <Box sx={{ display: "flex", alignItems: "center", gap: 3 }}>
                  <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5, justifyContent: "center" }}>
                    <TextField
                      label="Nickname"
                      value={brandingName}
                      onChange={(e) => setBrandingName(e.target.value)}
                      size="small"
                      sx={{ width: 200 }}
                      onBlur={async () => {
                        const trimmedName = brandingName.trim();
                        if (!trimmedName) return;
                        try {
                          const fd = new FormData();
                          fd.append("system_name", trimmedName);
                          await updateBranding(fd);
                          const refreshed = await fetchBranding();
                          const latestName = refreshed?.system_name ?? trimmedName ?? persistedSystemName ?? "";
                          setSystemInfo(latestName, systemLogo || persistedSystemLogo || null);
                        } catch (err) {
                          showMessage("Failed to update nickname: " + err.message, "error");
                        }
                      }}
                      onKeyDown={(e) => { if (e.key === "Enter") e.target.blur(); }}
                    />
                    <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                      <Chip
                        label="Change Theme"
                        onClick={() => setThemeModalOpen(true)}
                        size="small"
                        variant="outlined"
                      />
                      <Typography variant="body2" color="text.secondary">{themeName}</Typography>
                    </Box>
                  </Box>
                  <label htmlFor="logo-upload" style={{ cursor: "pointer" }}>
                    <Avatar
                      src={
                        brandingFile
                          ? URL.createObjectURL(brandingFile)
                          : systemLogo
                            ? `/api/uploads/${systemLogo}`
                            : persistedSystemLogo
                              ? `/api/uploads/${persistedSystemLogo}`
                              : `/api/uploads/system/profile-default.png`
                      }
                      variant="rounded"
                      sx={{ width: 192, height: 192, border: 1, borderColor: "divider", cursor: "pointer", "&:hover": { opacity: 0.8 } }}
                    >
                      <AccountBoxIcon sx={{ fontSize: 64 }} />
                    </Avatar>
                  </label>
                </Box>
              </SettingsRow>
              <SettingsRow label="Media Library Path">
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <TextField
                    value={musicDirectory}
                    onChange={(e) => setMusicDirectory(e.target.value)}
                    size="small"
                    placeholder="~/Music"
                    sx={{ width: 240 }}
                  />
                  <Button
                    variant="outlined"
                    size="small"
                    onClick={async () => {
                      try {
                        const result = await setMusicDirectoryAPI(musicDirectory.trim());
                        if (result?.error) throw new Error(result.error);
                        showMessage("Saved", "success");
                      } catch (err) {
                        showMessage(`Failed: ${err.message}`, "error");
                      }
                    }}
                  >
                    Save
                  </Button>
                </Box>
              </SettingsRow>
              <Button
                variant="outlined"
                size="small"
                onClick={() => navigate("/dev-tools")}
                sx={{ mt: 1 }}
              >
                System Dashboard
              </Button>
          </SettingsCardWrapper>

          <SettingsCardWrapper title="Models">
              {/* GPU Resources Bar */}
              {gpuResources?.gpu?.total_mb > 0 && (
                <SettingsRow label="GPU Resources">
                  <Box sx={{ width: "100%", maxWidth: 320 }}>
                    <Box sx={{ display: "flex", justifyContent: "space-between", mb: 0.5 }}>
                      <Typography variant="caption" color="text.secondary">
                        VRAM: {gpuResources.gpu.used_mb.toLocaleString()} / {gpuResources.gpu.total_mb.toLocaleString()} MB
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {gpuResources.gpu.free_mb.toLocaleString()} MB free
                      </Typography>
                    </Box>
                    <LinearProgress
                      variant="determinate"
                      value={Math.min(gpuResources.gpu.utilization_pct, 100)}
                      sx={{
                        height: 8,
                        borderRadius: 1,
                        backgroundColor: "action.hover",
                        "& .MuiLinearProgress-bar": {
                          backgroundColor: gpuResources.gpu.utilization_pct > 90 ? "error.main" : gpuResources.gpu.utilization_pct > 70 ? "warning.main" : "success.main",
                        },
                      }}
                    />
                    {gpuResources.loaded_models?.length > 0 && (
                      <Box sx={{ mt: 0.5, display: "flex", gap: 0.5, flexWrap: "wrap" }}>
                        {gpuResources.loaded_models.map((m) => (
                          <Chip key={m.name} label={`${m.name} (${m.vram_mb}MB)`} size="small" variant="outlined" sx={{ fontSize: "0.7rem" }} />
                        ))}
                      </Box>
                    )}
                  </Box>
                </SettingsRow>
              )}
              <SettingsRow label="Chat Model">
                <Box sx={{ display: "flex", flexDirection: "column", gap: 1, width: "100%", maxWidth: 420 }}>
                  {/* Size filter chips */}
                  {(() => {
                    const getSize = (m) => {
                      const ps = m.details?.parameter_size || "";
                      const num = parseFloat(ps);
                      if (isNaN(num)) return null;
                      if (num <= 3) return "small";
                      if (num <= 10) return "medium";
                      return "large";
                    };
                    const sizes = [...new Set(availableModels.map(getSize).filter(Boolean))];
                    const sizeOrder = ["small", "medium", "large"];
                    const sizeLabels = { small: "≤3B", medium: "3-10B", large: ">10B" };
                    return sizes.length > 1 ? (
                      <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", mb: 0.5 }}>
                        <Chip label="All" size="small" variant={chatSizeFilter === null ? "filled" : "outlined"}
                          color={chatSizeFilter === null ? "primary" : "default"}
                          onClick={() => setChatSizeFilter(null)} sx={{ height: 24, fontSize: "0.75rem" }} />
                        {sizeOrder.filter((s) => sizes.includes(s)).map((s) => {
                          const count = availableModels.filter((m) => getSize(m) === s).length;
                          return (
                            <Chip key={s} label={`${sizeLabels[s]} (${count})`} size="small"
                              variant={chatSizeFilter === s ? "filled" : "outlined"}
                              color={chatSizeFilter === s ? "primary" : "default"}
                              onClick={() => setChatSizeFilter(chatSizeFilter === s ? null : s)}
                              sx={{ height: 24, fontSize: "0.75rem" }} />
                          );
                        })}
                      </Box>
                    ) : null;
                  })()}
                  <FormControl fullWidth size="small" disabled={isLoading || isLoadingModel}>
                    <InputLabel>Select Model</InputLabel>
                    <Select
                      value={selectedModel}
                      label="Select Model"
                      onChange={(e) => setSelectedModel(e.target.value)}
                      error={Boolean(selectedModel && !availableModels.some((m) => m.name === selectedModel))}
                    >
                      <MenuItem value="" disabled>
                        <em>{isLoadingModel ? "Loading..." : availableModels.length === 0 ? "No models" : "Select..."}</em>
                      </MenuItem>
                      {availableModels
                        .filter((m) => {
                          if (chatSizeFilter === null) return true;
                          const ps = m.details?.parameter_size || "";
                          const num = parseFloat(ps);
                          if (isNaN(num)) return chatSizeFilter === null;
                          if (chatSizeFilter === "small") return num <= 3;
                          if (chatSizeFilter === "medium") return num > 3 && num <= 10;
                          return num > 10;
                        })
                        .map((m) => {
                          const ps = m.details?.parameter_size;
                          const sizeMb = m.size ? Math.round(m.size / (1024 * 1024)) : null;
                          return (
                            <MenuItem key={m.name} value={m.name}>
                              {m.name}{ps ? ` (${ps}` : ""}{sizeMb ? `${ps ? ", " : " ("}${sizeMb >= 1024 ? (sizeMb / 1024).toFixed(1) + "GB" : sizeMb + "MB"}` : ""}{(ps || sizeMb) ? ")" : ""}
                            </MenuItem>
                          );
                        })}
                    </Select>
                  </FormControl>
                  <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
                    <Tooltip title={modelSwitchStatus === "loading" ? "Model switch in progress..." : isLoadingModel ? "Model is loading..." : isLoading ? "Loading..." : !selectedModel ? "Select a model first" : selectedModel === activeModel ? "This model is already active" : ""}>
                      <span>
                        <Button
                          variant="contained"
                          size="small"
                          onClick={handleSetModelClick}
                          disabled={isLoading || isLoadingModel || modelSwitchStatus === "loading" || !selectedModel || selectedModel === activeModel}
                        >
                          {modelSwitchStatus === "loading" ? <><CircularProgress size={16} sx={{ mr: 0.5 }} /> Switching...</> : "Set Active"}
                        </Button>
                      </span>
                    </Tooltip>
                    <Tooltip title={isLoadingModel ? "Model is loading..." : isLoading ? "Loading..." : ""}>
                      <span>
                        <Button variant="outlined" size="small" onClick={fetchAvailableModels} disabled={isLoadingModel || isLoading}>Refresh</Button>
                      </span>
                    </Tooltip>
                    <Tooltip title={isTestingLLM ? "Test in progress..." : isLoadingModel ? "Model is loading..." : isLoading ? "Loading..." : ""}>
                      <span>
                        <Button
                          variant="outlined"
                          size="small"
                          onClick={async () => {
                            setIsTestingLLM(true);
                            try {
                              const r = await apiService.testLLM();
                              showMessage(`LLM responded in ${r.duration_sec}s`, "info");
                            } catch (e) {
                              showMessage(`Test failed: ${e.message}`, "error");
                            } finally {
                              setIsTestingLLM(false);
                            }
                          }}
                          disabled={isLoadingModel || isLoading || isTestingLLM}
                          sx={{ minWidth: 80 }}
                        >
                          {isTestingLLM ? "Testing..." : "Test"}
                        </Button>
                      </span>
                    </Tooltip>
                  </Box>
                  {modelSwitchStatus === "loading" && (
                    <Box>
                      <LinearProgress />
                      <Typography variant="caption" color="text.secondary">{modelSwitchMessage}</Typography>
                    </Box>
                  )}
                </Box>
              </SettingsRow>
              {/* Embedding Model Switcher */}
              <SettingsRow label="Embedding Model">
                <Box sx={{ display: "flex", flexDirection: "column", gap: 1, width: "100%", maxWidth: 420 }}>
                  {/* Dimension filter chips */}
                  {(() => {
                    const dims = [...new Set(embeddingModels.map((m) => m.dimensions).filter(Boolean))].sort((a, b) => a - b);
                    return dims.length > 1 ? (
                      <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", mb: 0.5 }}>
                        <Chip label="All" size="small" variant={embedDimFilter === null ? "filled" : "outlined"}
                          color={embedDimFilter === null ? "primary" : "default"}
                          onClick={() => setEmbedDimFilter(null)} sx={{ height: 24, fontSize: "0.75rem" }} />
                        {dims.map((d) => {
                          const count = embeddingModels.filter((m) => m.dimensions === d).length;
                          return (
                            <Chip key={d} label={`${d}d (${count})`} size="small"
                              variant={embedDimFilter === d ? "filled" : "outlined"}
                              color={embedDimFilter === d ? "primary" : "default"}
                              onClick={() => setEmbedDimFilter(embedDimFilter === d ? null : d)}
                              sx={{ height: 24, fontSize: "0.75rem" }} />
                          );
                        })}
                      </Box>
                    ) : null;
                  })()}
                  <FormControl fullWidth size="small" disabled={isSwitchingEmbedding}>
                    <InputLabel>Embedding Model</InputLabel>
                    <Select
                      value={selectedEmbeddingModel}
                      label="Embedding Model"
                      onChange={(e) => setSelectedEmbeddingModel(e.target.value)}
                    >
                      {embeddingModels.length === 0 ? (
                        <MenuItem value="" disabled><em>No embedding models found</em></MenuItem>
                      ) : (
                        embeddingModels
                          .filter((m) => embedDimFilter === null || m.dimensions === embedDimFilter)
                          .map((m) => (
                          <MenuItem key={m.name} value={m.name}>
                            {m.name} ({m.size_mb}MB{m.dimensions ? `, ${m.dimensions}d` : ""})
                          </MenuItem>
                        ))
                      )}
                    </Select>
                  </FormControl>
                  <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
                    <Tooltip title={isSwitchingEmbedding ? "Embedding model switch in progress..." : !selectedEmbeddingModel ? "Select an embedding model first" : selectedEmbeddingModel === embeddingModel ? "This embedding model is already active" : ""}>
                      <span>
                        <Button
                          variant="contained"
                          size="small"
                          disabled={isSwitchingEmbedding || !selectedEmbeddingModel || selectedEmbeddingModel === embeddingModel}
                          onClick={async () => {
                            if (!window.confirm(
                              "Switching embedding models?\n\n" +
                              "If the new model produces the same dimension vectors, your existing index will be preserved.\n\n" +
                              "If the dimensions differ, the index will be cleared and you'll need to re-index.\n\nContinue?"
                            )) return;
                            setIsSwitchingEmbedding(true);
                            try {
                              const r = await fetch("/api/model/embedding/set", {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({ model: selectedEmbeddingModel }),
                              });
                              const d = await r.json();
                              if (d.success) {
                                setEmbeddingModel(selectedEmbeddingModel);
                                showMessage(`Embedding switched to ${selectedEmbeddingModel} (${d.data.dimensions}d).${d.data.index_cleared ? " Index cleared — please re-index your documents." : " Index preserved — same dimensions."}`, "success");
                                fetchResources();
                              } else {
                                showMessage(d.error || "Failed to switch embedding", "error");
                              }
                            } catch (e) {
                              showMessage(`Failed: ${e.message}`, "error");
                            } finally {
                              setIsSwitchingEmbedding(false);
                            }
                          }}
                        >
                          {isSwitchingEmbedding ? <><CircularProgress size={16} sx={{ mr: 0.5 }} /> Switching...</> : "Set Active"}
                        </Button>
                      </span>
                    </Tooltip>
                    <Button variant="outlined" size="small" onClick={fetchEmbeddingModels}>Refresh</Button>
                    {embeddingModel && embeddingModel !== "Not Available" && embeddingModel !== "Not Set" && (
                      <Chip label={`Active: ${embeddingModel}`} size="small" color="secondary" variant="outlined" />
                    )}
                  </Box>
                </Box>
              </SettingsRow>
          </SettingsCardWrapper>

          <SettingsCardWrapper title="RAG Autoresearch">
                <Typography variant="caption" color="text.secondary" sx={{ mb: 2, display: "block" }}>
                  Autonomous RAG optimization — experiments run while the system is idle
                </Typography>

                <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 2 }}>
                  <Typography variant="body2">Auto-optimize when idle</Typography>
                  <Switch
                    size="small"
                    checked={autoresearchSettings.rag_autoresearch_auto_enabled === "true"}
                    onChange={(e) => handleAutoresearchSettingChange("rag_autoresearch_auto_enabled", e.target.checked)}
                  />
                </Box>

                <Box sx={{ mb: 2 }}>
                  <Typography variant="body2" gutterBottom>
                    Idle threshold: {autoresearchSettings.rag_autoresearch_idle_minutes || 10} minutes
                  </Typography>
                  <Slider
                    value={parseInt(autoresearchSettings.rag_autoresearch_idle_minutes || "10")}
                    min={5}
                    max={120}
                    step={5}
                    marks={[{ value: 5, label: "5m" }, { value: 60, label: "60m" }, { value: 120, label: "120m" }]}
                    onChange={(e, val) => handleAutoresearchSettingChange("rag_autoresearch_idle_minutes", val)}
                    sx={{ width: "100%" }}
                  />
                </Box>

                <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", mb: 2 }}>
                  <Typography variant="body2">Max experiment phase</Typography>
                  <Select
                    value={autoresearchSettings.rag_autoresearch_phase_limit || "2"}
                    size="small"
                    onChange={(e) => handleAutoresearchSettingChange("rag_autoresearch_phase_limit", e.target.value)}
                    sx={{ minWidth: 160 }}
                  >
                    <MenuItem value="1">Phase 1 (Query)</MenuItem>
                    <MenuItem value="2">Phase 2 (Index)</MenuItem>
                    <MenuItem value="3">Phase 3 (Model)</MenuItem>
                  </Select>
                </Box>

                <Button
                  variant="outlined"
                  size="small"
                  onClick={async () => {
                    try {
                      await ragAutoresearchService.resetConfig();
                      const data = await ragAutoresearchService.getSettings();
                      setAutoresearchSettings(data);
                      showMessage("Autoresearch config reset to defaults", "success");
                    } catch (e) {
                      showMessage("Failed to reset autoresearch config", "error");
                    }
                  }}
                >
                  Reset to Defaults
                </Button>
          </SettingsCardWrapper>

          <SettingsCardWrapper title="A.I. Features">
              <SettingsRow label="Voice Chat">
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Chip
                    label={voiceChatEnabled ? "On" : "Off"}
                    onClick={() => {
                      setVoiceChatEnabled(!voiceChatEnabled);
                      showMessage(`Voice chat ${!voiceChatEnabled ? "enabled" : "disabled"}`, "info");
                    }}
                    size="small"
                    color={voiceChatEnabled ? "primary" : "default"}
                    variant={voiceChatEnabled ? "filled" : "outlined"}
                  />
                  <Typography
                    component="button"
                    variant="body2"
                    onClick={() => setVoiceSettingsModalOpen(true)}
                    sx={{ background: "none", border: "none", cursor: "pointer", color: "primary.main", textDecoration: "underline" }}
                  >
                    Settings
                  </Typography>
                </Box>
              </SettingsRow>
              <SettingsRow label="Image Generation">
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  {imageGenStatus === null ? (
                    <CircularProgress size={16} />
                  ) : (
                    <Chip
                      label={imageGenStatus.service_available ? "Available" : "Unavailable"}
                      color={imageGenStatus.service_available ? "success" : "default"}
                      size="small"
                      variant="outlined"
                    />
                  )}
                  <Button variant="outlined" size="small" onClick={() => setImageModelsModalOpen(true)}>
                    Image Models
                  </Button>
                  <Button variant="outlined" size="small" onClick={() => setVideoModelsModalOpen(true)}>
                    Video Models
                  </Button>
                  <Button variant="outlined" size="small" onClick={() => setVoiceModelsModalOpen(true)}>
                    Voice Models
                  </Button>
                </Box>
              </SettingsRow>
              {/* Agent Routing and Unified Agentic Chat toggles removed — always enabled */}
              <SettingsRow label="Agents">
                <Button variant="outlined" size="small" onClick={() => setAgentsModalOpen(true)}>
                  Open
                </Button>
              </SettingsRow>
              <SettingsRow label="Rules">
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Chip
                    label={rulesEnabled ? "On" : "Off"}
                    onClick={async () => {
                      const next = !rulesEnabled;
                      // Optimistic UI + localStorage mirror; roll back on failure.
                      setRulesEnabledState(next);
                      try {
                        localStorage.setItem(RULES_ENABLED_KEY, String(next));
                      } catch {
                        // non-fatal
                      }
                      try {
                        const result = await apiService.setRulesEnabled(next);
                        if (result?.error) throw new Error(result.error);
                        showMessage(
                          next
                            ? "Rules enabled — RulesPage active rules will apply"
                            : "Rules disabled — chat will use the hardcoded prompt",
                          "info",
                        );
                      } catch (err) {
                        console.error("Failed to update rules_enabled:", err);
                        setRulesEnabledState(!next);
                        try {
                          localStorage.setItem(RULES_ENABLED_KEY, String(!next));
                        } catch {
                          // non-fatal
                        }
                        showMessage("Failed to update Rules setting", "error");
                      }
                    }}
                    size="small"
                    color={rulesEnabled ? "primary" : "default"}
                    variant={rulesEnabled ? "filled" : "outlined"}
                  />
                  <Typography
                    component="button"
                    variant="body2"
                    onClick={() => navigate("/rules")}
                    sx={{ background: "none", border: "none", cursor: "pointer", color: "primary.main", textDecoration: "underline" }}
                  >
                    Manage
                  </Typography>
                </Box>
              </SettingsRow>
          </SettingsCardWrapper>

          <SettingsCardWrapper title="RAG Performance">
              <RAGDebugSection ragDebugEnabled={ragDebug} />
          </SettingsCardWrapper>

          <SettingsCardWrapper title="Uncle Claude">
              <UncleClaudeSection compact />
          </SettingsCardWrapper>

          <SettingsCardWrapper title="Agent Memory">
              <MemoryManagementSection />
          </SettingsCardWrapper>

          <SettingsCardWrapper title="Data">
              <SettingsRow label="System Backup / Restore">
                <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
                  <Tooltip title={isProcessingBackup ? "Backup operation in progress..." : isLoading ? "Loading..." : ""}>
                    <span>
                      <Button variant="outlined" size="small" onClick={openCreateBackup} disabled={isProcessingBackup || isLoading}>Create</Button>
                    </span>
                  </Tooltip>
                  <Tooltip title={isProcessingBackup ? "Backup operation in progress..." : isLoading ? "Loading..." : ""}>
                    <span>
                      <Button variant="contained" size="small" onClick={openRestoreBackup} disabled={isProcessingBackup || isLoading}>Restore</Button>
                    </span>
                  </Tooltip>
                  <Button variant="outlined" size="small" onClick={openManageBackups}>Manage</Button>
                </Box>
              </SettingsRow>
              <SettingsRow label="Rules Backup / Restore">
                <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
                  <Button variant="outlined" size="small" onClick={handleExportRulesClick} disabled={isExporting || isLoading}>
                    {isExporting ? <CircularProgress size={16} /> : "Export"}
                  </Button>
                  <input accept=".json" style={{ display: "none" }} id="import-rules-file" type="file" ref={fileImportInputRef} onChange={handleFileSelectForImport} />
                  <label htmlFor="import-rules-file">
                    <Button variant="outlined" size="small" component="span" disabled={isImporting || isLoading}>Choose File</Button>
                  </label>
                  <Tooltip title={isImporting ? "Import in progress..." : !selectedFileForImport ? "Choose a file first" : isLoading ? "Loading..." : ""}>
                    <span>
                      <Button variant="contained" size="small" onClick={handleImportRulesClick} disabled={!selectedFileForImport || isImporting || isLoading}>
                        {isImporting ? <CircularProgress size={16} color="inherit" /> : "Import"}
                      </Button>
                    </span>
                  </Tooltip>
                </Box>
              </SettingsRow>
              <SettingsRow label="Chat History">
                <Button variant="outlined" size="small" color="error" onClick={handleClearChatHistoryClick} disabled={isLoading}>Clear All Chat History</Button>
              </SettingsRow>
              <SettingsRow label="Index">
                <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
                  <Button variant="outlined" size="small" onClick={handleOpenPurgeModal} disabled={isLoading}>Purge</Button>
                  <Button variant="outlined" size="small" color="error" onClick={() => handleActionClick(apiService.resetIndexStorage, [], "Reset index? All indexed knowledge will be lost.", "Resetting...", "Index reset.", "Failed")} disabled={isLoading}>Reset</Button>
                  <Button variant="outlined" size="small" onClick={() => handleActionClick(apiService.optimizeIndex, [], null, "Optimizing...", "Index optimized.", "Failed")} disabled={isLoading}>Optimize</Button>
                </Box>
              </SettingsRow>
          </SettingsCardWrapper>

          <SettingsCardWrapper title="Network">
              <SettingsRow label="Web Access">
                <Chip
                  label={webSearchEnabled ? "On" : "Off"}
                  onClick={() => handleWebSearchToggle()}
                  size="small"
                  color={webSearchEnabled ? "primary" : "default"}
                  variant={webSearchEnabled ? "filled" : "outlined"}
                />
              </SettingsRow>
              <SettingsRow label="Interconnector">
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  <Switch
                    size="small"
                    checked={interconnectorEnabled}
                    onChange={async (e) => {
                      const newEnabled = e.target.checked;
                      try {
                        const currentConfig = await interconnectorApi.getInterconnectorConfig();
                        const cfg = currentConfig?.data?.config || currentConfig?.config || {};
                        await interconnectorApi.updateInterconnectorConfig({ ...cfg, is_enabled: newEnabled });
                        setInterconnectorEnabled(newEnabled);
                      } catch (err) {
                        console.error("Failed to toggle Interconnector:", err);
                      }
                    }}
                  />
                  <Typography
                    component="button"
                    variant="body2"
                    onClick={() => setInterconnectorModalOpen(true)}
                    sx={{ background: "none", border: "none", cursor: "pointer", color: "primary.main", textDecoration: "underline" }}
                  >
                    Configure
                  </Typography>
                </Box>
              </SettingsRow>
          </SettingsCardWrapper>

          <SettingsCardWrapper title="Maintenance">
              <SettingsRow label="Clear Cache">
                <Button variant="outlined" size="small" onClick={handleClearPycacheFoldersClick} disabled={isLoading}>Clear Cache</Button>
              </SettingsRow>
              <SettingsRow label="Diagnostics">
                <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
                  <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap" }}>
                    <Button variant={isTesting && testMode === "basic" ? "contained" : "outlined"} size="small" onClick={() => handleRunSystemCheck("basic")} disabled={isLoading || isTesting}>Basic</Button>
                    <Button variant={isTesting && testMode === "quick" ? "contained" : "outlined"} size="small" onClick={() => handleRunSystemCheck("quick")} disabled={isLoading || isTesting}>Quick</Button>
                    <Button variant={isTesting && testMode === "comprehensive" ? "contained" : "outlined"} size="small" onClick={() => handleRunSystemCheck("comprehensive")} disabled={isLoading || isTesting}>Full</Button>
                  </Box>
                  {testResults && (
                    <Box mt={2} p={2} border={1} borderColor="divider" borderRadius={1}>
                      {testResults.error && typeof testResults.error === "string" ? (
                        <MuiAlert severity="error">{testResults.error}</MuiAlert>
                      ) : (
                        <>
                          {testResults.categories && renderCategorizedResults(testResults)}
                          {(testResults.legacy_diagnostics || (!testResults.categories && testResults)) && (
                            <TableContainer component={Paper} variant="outlined" sx={{ mt: 2 }}>
                              <Table size="small">
                                <TableHead>
                                  <TableRow>
                                    <TableCell></TableCell>
                                    <TableCell>Component</TableCell>
                                    <TableCell>Status</TableCell>
                                  </TableRow>
                                </TableHead>
                                <TableBody>
                                  {systemCheckItems.map((item) => {
                                    const ds = testResults.legacy_diagnostics || testResults;
                                    const val = ds[item.key];
                                    const details = val !== undefined ? item.format(val, ds) : "N/A";
                                    return (
                                      <TableRow key={item.key}>
                                        <TableCell>{item.icon ? React.cloneElement(item.icon, { fontSize: "small" }) : renderStatusIcon(val, details)}</TableCell>
                                        <TableCell component="th">{item.label}</TableCell>
                                        <TableCell>{details}</TableCell>
                                      </TableRow>
                                    );
                                  })}
                                </TableBody>
                              </Table>
                            </TableContainer>
                          )}
                        </>
                      )}
                    </Box>
                  )}
                </Box>
              </SettingsRow>
              <SettingsRow label="Tests">
                <Button variant="outlined" size="small" onClick={handleRunAllTests} disabled={isRunningTests}>
                  {isRunningTests ? "Running..." : "Run Tests"}
                </Button>
                {testSuiteResults && (
                  <Box mt={2}>
                    {testSuiteResults.error ? (
                      <MuiAlert severity="error">{testSuiteResults.error}</MuiAlert>
                    ) : (
                      <Typography variant="body2" color="text.secondary">
                        Return code: {testSuiteResults.returncode}
                      </Typography>
                    )}
                  </Box>
                )}
              </SettingsRow>
              <SettingsRow label="Developer" stacked>
                <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.75 }}>
                  <Chip label="RAG Debug" onClick={handleRagDebugChange} size="small" color={ragDebug ? "primary" : "default"} variant={ragDebug ? "filled" : "outlined"} />
                  <Chip label="Enhanced Context" onClick={handleEnhancedContextChange} size="small" color={enhancedContext ? "primary" : "default"} variant={enhancedContext ? "filled" : "outlined"} />
                  <Chip label="Advanced RAG" onClick={handleAdvancedRagChange} size="small" color={advancedRag ? "primary" : "default"} variant={advancedRag ? "filled" : "outlined"} />
                  <Chip label="Behavior Learning" onClick={handleBehaviorLearningToggle} size="small" color={behaviorLearningEnabled ? "primary" : "default"} variant={behaviorLearningEnabled ? "filled" : "outlined"} />
                  <Chip label="Verbose Logging" onClick={handleAdvancedDebugToggle} size="small" color={advancedDebug ? "primary" : "default"} variant={advancedDebug ? "filled" : "outlined"} />
                  <Chip label="LLM Debug" onClick={handleLlmDebugToggle} size="small" color={llmDebug ? "success" : "default"} variant={llmDebug ? "filled" : "outlined"} />
                </Box>
              </SettingsRow>
              <Box sx={{ borderTop: 1, borderColor: "divider", mt: 2, pt: 2 }}>
                <Typography variant="overline" sx={{ fontSize: "0.75rem", color: "error.main" }}>System Control</Typography>
                <Box sx={{ display: "flex", gap: 1, mt: 1 }}>
                  <Tooltip title={isLoading ? "Please wait for current operation to finish" : ""}>
                    <span>
                      <Button variant="outlined" size="small" color="error" onClick={handleRebootClick} disabled={isLoading}>Reboot</Button>
                    </span>
                  </Tooltip>
                  <Tooltip title={isLoading ? "Please wait for current operation to finish" : ""}>
                    <span>
                      <Button variant="outlined" size="small" color="error" onClick={() => setKillSwitchOpen(true)} disabled={isLoading}>Kill Switch</Button>
                    </span>
                  </Tooltip>
                </Box>
              </Box>
          </SettingsCardWrapper>

          {/* Training */}
          <SettingsCardWrapper title="Training" icon={<SchoolIcon sx={{ fontSize: 18 }} />}>
            <SettingsRow label="Interactive Trainer">
              <Button
                variant="outlined"
                size="small"
                startIcon={<SchoolIcon sx={{ fontSize: 14 }} />}
                onClick={() => setTrainerOpen(true)}
                sx={{ fontSize: '0.75rem', textTransform: 'none' }}
              >
                Launch Trainer
              </Button>
            </SettingsRow>
            <SettingsRow label="Description">
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Record demonstrations on the virtual display, then watch the agent learn to replicate them with graduated autonomy.
              </Typography>
            </SettingsRow>
          </SettingsCardWrapper>
        </Box>
      </Box>

      {/* Modals */}
      <CreateBackupModal
        open={createBackupOpen}
        onClose={() => setCreateBackupOpen(false)}
        onCreate={handleCreateBackupConfirm}
        isProcessing={isProcessingBackup}
      />
      <RestoreBackupModal
        open={restoreBackupOpen}
        onClose={() => setRestoreBackupOpen(false)}
        onRestore={handleRestoreConfirm}
        isProcessing={isProcessingBackup}
        backups={backupList}
      />
      <ManageBackupsModal
        open={manageBackupsOpen}
        onClose={closeManageBackups}
        onRestore={async (name) => {
          await handleRestoreConfirm(name);
        }}
        onDelete={async (name) => {
          await apiService.deleteServerBackup(name);
          openManageBackups();
        }}
        onDownload={async (name) => {
          try {
            await apiService.downloadServerBackup(name);
          } catch (e) {
            console.error("Download failed:", e);
          }
        }}
        onRefresh={fetchBackupList}
        backups={backupList}
      />
      <ThemeSelectorModal
        open={themeModalOpen}
        onClose={() => setThemeModalOpen(false)}
      />
      <VoiceSettingsModal
        open={voiceSettingsModalOpen}
        onClose={() => setVoiceSettingsModalOpen(false)}
        voiceSettings={voiceSettings}
        availableVoices={availableVoices}
        voiceStatus={voiceStatus}
        voiceError={voiceError}
        isVoiceLoading={isVoiceLoading}
        isVoiceTestPlaying={isVoiceTestPlaying}
        isInstallingVoice={isInstallingVoice}
        isInstallingWhisper={isInstallingWhisper}
        voiceModelsStatus={voiceModelsStatus}
        handleVoiceSettingChange={handleVoiceSettingChange}
        installWhisperCpp={installWhisperCpp}
        installWhisperSpeechModel={installWhisperSpeechModel}
        installDefaultVoiceModel={installDefaultVoiceModel}
        testVoice={testVoice}
        systemName={persistedSystemName}
      />
      <AgentsSettingsModal open={agentsModalOpen} onClose={() => setAgentsModalOpen(false)} />
      <InterconnectorSettingsModal
        open={interconnectorModalOpen}
        onClose={() => {
          setInterconnectorModalOpen(false);
          interconnectorApi.getInterconnectorConfig().then((res) => {
            if (res?.data?.config?.is_enabled || res?.config?.is_enabled) setInterconnectorEnabled(true);
            else if (!res?.error) setInterconnectorEnabled(false);
          }).catch(() => {});
        }}
      />
      <Dialog
        open={rebootDialogOpen}
        onClose={handleCancelReboot}
        aria-labelledby="reboot-dialog-title"
      >
        <DialogTitle id="reboot-dialog-title">Confirm Reboot</DialogTitle>
        <DialogContent dividers>
          <DialogContentText component="div">
            <Typography variant="body2" gutterBottom>
              Backend services will restart. The UI will briefly disconnect and reload automatically.
            </Typography>
            <Typography variant="body2">
              Save any unsaved work before proceeding.
            </Typography>
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCancelReboot} disabled={rebootInProgress}>
            Cancel
          </Button>
          <Button
            onClick={handleConfirmReboot}
            color="error"
            variant="contained"
            disabled={rebootInProgress}
          >
            {rebootInProgress ? "Rebooting..." : "Reboot Now"}
          </Button>
        </DialogActions>
      </Dialog>
      {purgeModalOpen && (
        <PurgeIndexModal
          open={purgeModalOpen}
          onClose={handleClosePurgeModal}
          onConfirm={handleConfirmPurge}
          isProcessing={isPurging}
        />
      )}
      <KillSwitchModal
        open={killSwitchOpen}
        onClose={() => setKillSwitchOpen(false)}
      />
      <RebootProgressModal
        open={rebootProgressModalOpen}
        onClose={handleRebootProgressModalClose}
      />
      <ImageModelsModal
        open={imageModelsModalOpen}
        onClose={() => {
          setImageModelsModalOpen(false);
          fetch("/api/batch-image/status")
            .then(res => res.json())
            .then(data => data.success && setImageGenStatus(data.data))
            .catch(console.error);
        }}
        showMessage={showMessage}
      />
      <VideoModelsModal
        open={videoModelsModalOpen}
        onClose={() => setVideoModelsModalOpen(false)}
        showMessage={showMessage}
      />
      <VoiceModelsModal
        open={voiceModelsModalOpen}
        onClose={() => setVoiceModelsModalOpen(false)}
        showMessage={showMessage}
      />
    </PageLayout>
  );
};

export default SettingsPage;
