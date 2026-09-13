// frontend/src/pages/SettingsPage.jsx

import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  Typography,
  Box,
  Select,
  MenuItem,
  FormControl,
  InputLabel,
  Tooltip,
  TextField,
  Avatar,
} from "@mui/material";

// MUI Icons
import AccountBoxIcon from "@mui/icons-material/AccountBox";
import { LinearProgress } from "@mui/material";

import { io } from "socket.io-client";
import CreateBackupModal from "../components/modals/CreateBackupModal";
import RestoreBackupModal from "../components/modals/RestoreBackupModal";
import ManageBackupsModal from "../components/modals/ManageBackupsModal";
import PurgeIndexModal from "../components/modals/PurgeIndexModal";
import ThemeSelectorModal from "../components/modals/ThemeSelectorModal";
import UncleClaudeSection from "../components/settings/UncleClaudeSection";
import AgentDisplaySection from "../components/settings/AgentDisplaySection";
import KillSwitchModal from "../components/modals/KillSwitchModal";
import RebootProgressModal from "../components/modals/RebootProgressModal";
import ImageModelsModal from "../components/modals/ImageModelsModal";
import InfographicModelsModal from "../components/modals/InfographicModelsModal";
import VideoModelsModal from "../components/modals/VideoModelsModal";
import VoiceModelsModal from "../components/modals/VoiceModelsModal";
import InterconnectorSettingsModal from "../components/modals/InterconnectorSettingsModal";
import VoiceSettingsModal from "../components/modals/VoiceSettingsModal";
import ExportChatsButton from "../components/settings/ExportChatsButton";
import ProfileSection from "../components/settings/ProfileSection";
import { SOCKET_URL } from "../api/apiClient";
import { SUPPORT_LINKS } from "../config/constants";
import CoffeeIcon from "@mui/icons-material/Coffee";
import StarIcon from "@mui/icons-material/Star";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import { useNavigate } from "react-router-dom";
import {
  getBranding,
  updateBranding,
  getRagFeatures,
  updateRagFeatures,
  clearBehaviorLog,
  getMusicDirectory,
  setMusicDirectory as setMusicDirectoryAPI,
} from "../api/settingsService";
import { useAppStore } from "../stores/useAppStore";
import { useStatus } from "../contexts/StatusContext";
import PageLayout from "../components/layout/PageLayout";
import {
  SettingChip,
  ChoiceChips,
  StatusPill,
  ActionButton,
  SettingsPanel,
  Cluster,
  Line,
  Sep,
  Hint,
  ConfirmActionDialog,
  DashboardStrip,
  DashboardTile,
} from "../components/settings/ui";
import IndexProfileChips from "../components/settings/IndexProfileChips";
import RebuildIndexDialog from "../components/settings/RebuildIndexDialog";
import IndexProfileEditDialog from "../components/settings/IndexProfileEditDialog";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useTheme, useMediaQuery } from "@mui/material";
import { useSnackbar } from "../components/common/SnackbarProvider";
import * as interconnectorApi from "../api/interconnectorService";
import { useVoice } from "../contexts/VoiceContext";
import * as apiService from "../api";
import voiceService from "../api/voiceService";
import { ragAutoresearchService } from "../api/ragAutoresearchService";
import { NAV_CHROME } from "../config/navCatalog";

const debugLog = (...args) => {
  if (import.meta.env.DEV) {
    console.debug(...args);
  }
};

// localStorage keys for persisting settings
const WEB_SEARCH_ENABLED_KEY = "guaardvark_webSearchEnabled";

// model_api and system_api call success_response(message, data) with the
// arguments reversed, so their payload arrives under "message". The service
// layer compensates route by route; the raw fetches on this page do it here.
const payloadOf = (d) => {
  if (d && typeof d.data === "object" && d.data !== null) return d.data;
  if (d && typeof d.message === "object" && d.message !== null)
    return d.message;
  return d?.data ?? d;
};
const ADV_DEBUG_ENABLED_KEY = "guaardvark_advDebugEnabled";
const BEHAVIOR_LEARNING_ENABLED_KEY = "guaardvark_behaviorLearningEnabled";
const LLM_DEBUG_ENABLED_KEY = "guaardvark_llmDebugEnabled";
// Global "use RulesPage rules for chat" toggle. Backend is the source of
// truth (Setting table, key=rules_enabled); this localStorage mirror only
// speeds first paint before the API round-trip lands.
const RULES_ENABLED_KEY = "guaardvark_rulesEnabled";
// Used by ChatPage to enable backend agent routing integration
const _AGENT_ROUTING_ENABLED_KEY = "use_agent_routing";
// Used by ChatPage to enable unified agentic chat (LLM with tool access)
const _UNIFIED_CHAT_ENABLED_KEY = "use_unified_chat";

// Voice settings localStorage keys (must match key used by voice components)
const VOICE_SETTINGS_KEY = "guaardvark_voiceSettings";
const VOICE_CHAT_ENABLED_KEY = "guaardvark_voiceChatEnabled";

const SettingsPage = () => {
  const [availableModels, setAvailableModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [_isLoadingEmbeddingModel, setIsLoadingEmbeddingModel] = useState(true);

  // Reset selectedModel if it's not in available options
  useEffect(() => {
    if (selectedModel && availableModels.length > 0) {
      const isModelAvailable = availableModels.some(
        (model) => model.name === selectedModel,
      );
      if (!isModelAvailable) {
        console.warn(
          `Model "${selectedModel}" is not available, resetting selection`,
        );
        setSelectedModel("");
      }
    }
  }, [availableModels, selectedModel]);
  const [isLoading, setIsLoading] = useState(false); // General loading for initial data or major actions
  const [isTestingLLM, setIsTestingLLM] = useState(false); // Local state for Test LLM button
  const { showMessage, closeSnackbar } = useSnackbar();
  const navigate = useNavigate();
  const [enhancedContext, setEnhancedContext] = useState(false);
  const [advancedRag, setAdvancedRag] = useState(false);
  const [advancedDebug, setAdvancedDebug] = useState(getInitialAdvancedDebug);
  const [llmDebug, setLlmDebugState] = useState(getInitialLlmDebug);
  const [verbatimPrompts, setVerbatimPromptsState] = useState(false);
  const [verbatimSaving, setVerbatimSaving] = useState(false);
  // VERBATIM_PROMPTS in the server environment overrides the toggle; when set
  // the chip shows on and cannot be changed here.
  const [verbatimForcedByEnv, setVerbatimForcedByEnv] = useState(false);
  // Media stack (stills / cast LoRA train / max quality) — Ollama-picker style
  const [mediaModels, setMediaModelsState] = useState({
    stills_model: "zimage-turbo",
    cast_train_base: "zimage-turbo",
    max_quality_model: "flux-dev",
    train_profiles: [],
    stills_profiles: [],
  });
  const [behaviorLearningEnabled, setBehaviorLearningEnabled] = useState(
    getInitialBehaviorLearning,
  );
  const [rulesEnabled, setRulesEnabledState] = useState(getInitialRulesEnabled);
  // Global default for chain-of-thought on thinking models (gemma4:12b, qwen3).
  // Off = faster chat; per-chat /thinking on|off overrides. Backend authoritative.
  const [chatThinkingDefault, setChatThinkingDefaultState] = useState(false);
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
  const [webSearchEnabled, setWebSearchEnabled] = useState(getInitialWebSearch);
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
  const [indexingPaused, setIndexingPaused] = useState(false);

  const [themeModalOpen, setThemeModalOpen] = useState(false);
  const [voiceSettingsModalOpen, setVoiceSettingsModalOpen] = useState(false);
  const [interconnectorModalOpen, setInterconnectorModalOpen] = useState(false);
  const [interconnectorEnabled, setInterconnectorEnabled] = useState(false);
  const [interconnectorPendingCount, setInterconnectorPendingCount] =
    useState(0);
  const [interconnectorIsClient, setInterconnectorIsClient] = useState(false);
  const [interconnectorUpdateStatus, setInterconnectorUpdateStatus] =
    useState(null);
  const [interconnectorApplying, setInterconnectorApplying] = useState(false);

  // Inline apply: lets the top banner's UPDATE button push the updates straight
  // through without making the user open the modal. Mirrors the same call the
  // ClientUpdatePanel makes (interconnectorApi.applyUpdates([])) plus a confirm.
  const handleApplyInterconnectorUpdates = useCallback(
    async (e) => {
      if (e?.stopPropagation) e.stopPropagation();
      if (interconnectorApplying) return;
      if (
        !window.confirm(
          "Apply all interconnector updates? Existing files will be backed up automatically.",
        )
      ) {
        return;
      }
      setInterconnectorApplying(true);
      try {
        const response = await interconnectorApi.applyUpdates([]);
        if (response?.error) {
          showMessage?.(`Update failed: ${response.error}`, "error");
        } else {
          const data = response?.data || response || {};
          showMessage?.(
            `Updated ${data.applied || 0} files (${data.created || 0} new, ${data.updated || 0} modified)`,
            "success",
          );
          // Clear the banner; a follow-up checkForUpdates will repopulate if more remain.
          setInterconnectorUpdateStatus((prev) =>
            prev ? { ...prev, available: false, count: 0 } : prev,
          );
          setTimeout(() => {
            interconnectorApi
              .checkForUpdates?.()
              .then((res) => {
                if (res && !res.error)
                  setInterconnectorUpdateStatus(res.data || res);
              })
              .catch(() => {});
          }, 1000);
        }
      } catch (err) {
        showMessage?.(`Update failed: ${err.message}`, "error");
      } finally {
        setInterconnectorApplying(false);
      }
    },
    [interconnectorApplying],
  );
  const [voiceChatEnabled, setVoiceChatEnabled] = useState(() => {
    try {
      return localStorage.getItem(VOICE_CHAT_ENABLED_KEY) !== "false";
    } catch {
      return true;
    }
  });
  const [killSwitchOpen, setKillSwitchOpen] = useState(false);
  const [rebootDialogOpen, setRebootDialogOpen] = useState(false);
  const [deleteHistoryDialogOpen, setDeleteHistoryDialogOpen] = useState(false);
  const [deleteHistoryCounts, setDeleteHistoryCounts] = useState(null);
  const [deleteHistoryInProgress, setDeleteHistoryInProgress] = useState(false);
  const [rebootInProgress, setRebootInProgress] = useState(false);
  const [rebootProgressModalOpen, setRebootProgressModalOpen] = useState(false);
  const [imageModelsModalOpen, setImageModelsModalOpen] = useState(false);
  const [infographicModelsModalOpen, setInfographicModelsModalOpen] =
    useState(false);
  const [videoModelsModalOpen, setVideoModelsModalOpen] = useState(false);
  const [voiceModelsModalOpen, setVoiceModelsModalOpen] = useState(false);
  const [imageGenStatus, setImageGenStatus] = useState(null);

  // Resource monitor and embedding model state
  const [gpuResources, setGpuResources] = useState(null);
  const [embeddingModels, setEmbeddingModels] = useState([]);
  const [selectedEmbeddingModel, setSelectedEmbeddingModel] = useState("");
  const [isSwitchingEmbedding, setIsSwitchingEmbedding] = useState(false);
  const [embedDimFilter, setEmbedDimFilter] = useState(null); // null = all, or a number like 1024
  const [chatSizeFilter, setChatSizeFilter] = useState(null); // null = all, or "small"/"medium"/"large"

  // ── v3 layout and danger-zone state ──
  const theme = useTheme();
  const isXl = useMediaQuery(theme.breakpoints.up("xl"));
  const isMd = useMediaQuery(theme.breakpoints.up("md"));
  const [indexProfiles, setIndexProfiles] = useState([]);
  const [profilesReloadKey, setProfilesReloadKey] = useState(0);
  const [rebuildDialogOpen, setRebuildDialogOpen] = useState(false);
  const [clearChatOpen, setClearChatOpen] = useState(false);
  const [clearChatBusy, setClearChatBusy] = useState(false);
  const [chatHistoryCounts, setChatHistoryCounts] = useState(null);
  const [clearMemoriesOpen, setClearMemoriesOpen] = useState(false);
  const [clearMemoriesBusy, setClearMemoriesBusy] = useState(false);
  const [memoryCount, setMemoryCount] = useState(null);
  const [musicDirectorySaved, setMusicDirectorySaved] = useState("");
  // "rules" clears rules the chat learned; "log" empties the behaviour log file.
  const [learningClear, setLearningClear] = useState(null);
  const [learningBusy, setLearningBusy] = useState(false);
  const [editProfile, setEditProfile] = useState(null);

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
    if (
      !brandingFile &&
      persistedSystemLogo &&
      systemLogo !== persistedSystemLogo
    ) {
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
      return saved
        ? JSON.parse(saved)
        : {
            voice: "libritts",
            recordingQuality: "medium",
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
            maxSegmentDuration: 30000,
          };
    } catch (error) {
      console.warn("Failed to load voice settings from localStorage:", error);
      return {
        voice: "libritts",
        recordingQuality: "medium",
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
        maxSegmentDuration: 30000,
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
  const setSelectedVoice = voiceContext?.setSelectedVoice || (() => {});

  // Load voice configuration
  useEffect(() => {
    loadVoiceConfiguration();
  }, []);

  // Load autoresearch settings
  useEffect(() => {
    ragAutoresearchService
      .getSettings()
      .then((data) => setAutoresearchSettings(data))
      .catch(() => {});
  }, []);

  const loadVoiceConfiguration = async () => {
    setIsVoiceLoading(true);
    setVoiceError(null);

    try {
      const [status, voices, modelsStatus] = await Promise.all([
        voiceService.getStatus(),
        voiceService.getVoices().catch(() => ({ voices: [] })),
        voiceService.getVoiceModelsStatus().catch(() => null),
      ]);

      setVoiceStatus(status);
      setAvailableVoices(voices.voices || voices.available_voices || []);
      setVoiceModelsStatus(modelsStatus);

      // Set default voice if not already set or if saved voice is not available
      if (voices.voices && voices.voices.length > 0) {
        const savedVoice = voiceSettings.voice;
        const isVoiceAvailable = voices.voices.some((v) => v.id === savedVoice);

        if (!savedVoice || !isVoiceAvailable) {
          const defaultVoice = voices.default_voice || voices.voices[0].id;
          setVoiceSettings((prev) => ({
            ...prev,
            voice: defaultVoice,
          }));
        }
      } else {
        // If no voices are available, reset to empty string to avoid MUI warnings
        setVoiceSettings((prev) => ({
          ...prev,
          voice: "",
        }));
      }
    } catch (error) {
      console.error("Failed to load voice configuration:", error);
      setVoiceError("Failed to load voice configuration");
      // Reset voice to empty string on error to avoid MUI warnings
      setVoiceSettings((prev) => ({
        ...prev,
        voice: "",
      }));
    } finally {
      setIsVoiceLoading(false);
    }
  };

  const testVoice = async (voiceId) => {
    setIsVoiceTestPlaying(true);
    try {
      // Check if voice is available first
      const voice = availableVoices.find((v) => v.id === voiceId);
      if (voice && voice.available === false) {
        showMessage(
          `Voice model "${voice.name}" is not installed. Please install it first.`,
          "warning",
        );
        setIsVoiceTestPlaying(false);
        return;
      }

      const response = await voiceService.textToSpeech(
        "Hello! This is a test of the text-to-speech feature.",
        voiceId,
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
      console.error("Voice test failed:", error);
      const errorMessage = error.message || "Voice test failed";
      if (
        errorMessage.includes("not found") ||
        errorMessage.includes("not installed")
      ) {
        showMessage(
          "Voice model is not installed. Please install voice models first.",
          "warning",
        );
      } else {
        showMessage(`Voice test failed: ${errorMessage}`, "error");
      }
      setIsVoiceTestPlaying(false);
    }
  };

  const handleVoiceSettingChange = (setting, value) => {
    setVoiceSettings((prev) => ({
      ...prev,
      [setting]: value,
    }));

    // Update VoiceContext immediately — localStorage 'storage' events only fire
    // in OTHER tabs, so we must sync the context directly for same-tab updates.
    if (setting === "voice" && value) {
      setSelectedVoice(value);
      const voiceName =
        availableVoices.find((v) => v.id === value)?.name || value;
      showMessage(`Voice changed to ${voiceName}`, "success");
    } else if (setting === "ttsEnabled") {
      if (voiceContext?.setTtsEnabled) {
        voiceContext.setTtsEnabled(value);
      }
      showMessage(
        `Text-to-Speech ${value ? "enabled" : "disabled"}`,
        "success",
      );
    } else if (setting === "micEnabled") {
      showMessage(`Microphone ${value ? "enabled" : "disabled"}`, "success");
    }
  };

  const installDefaultVoiceModel = async () => {
    setIsInstallingVoice(true);
    try {
      showMessage(
        "Installing LibriTTS voice model... This may take a moment.",
        "info",
      );
      const result = await voiceService.installVoiceModel("libritts");

      if (result.success) {
        if (result.already_installed) {
          showMessage("LibriTTS voice model is already installed.", "info");
        } else {
          showMessage(
            `Successfully installed LibriTTS voice model (${result.model_size_mb} MB)`,
            "success",
          );
        }
        // Reload voice configuration to update the UI
        await loadVoiceConfiguration();
      } else {
        showMessage(`Failed to install voice model: ${result.error}`, "error");
      }
    } catch (error) {
      console.error("Failed to install voice model:", error);
      showMessage(`Failed to install voice model: ${error.message}`, "error");
    } finally {
      setIsInstallingVoice(false);
    }
  };

  const installWhisperCpp = async () => {
    setIsInstallingWhisper(true);
    try {
      showMessage(
        "Installing Whisper.cpp... This will clone and build from source (may take 1-2 minutes).",
        "info",
      );
      const result = await voiceService.installWhisper();

      if (result.success) {
        if (result.already_installed) {
          showMessage("Whisper.cpp is already installed.", "info");
        } else {
          showMessage(
            "Whisper.cpp installed successfully! You can now use speech recognition.",
            "success",
          );
        }
        await loadVoiceConfiguration();
      } else {
        showMessage(`Failed to install Whisper.cpp: ${result.error}`, "error");
      }
    } catch (error) {
      console.error("Failed to install Whisper.cpp:", error);
      showMessage(`Failed to install Whisper.cpp: ${error.message}`, "error");
    } finally {
      setIsInstallingWhisper(false);
    }
  };

  const installWhisperSpeechModel = async () => {
    setIsInstallingWhisper(true);
    try {
      showMessage(
        "Downloading default Whisper speech model (tiny.en)...",
        "info",
      );
      const result = await voiceService.installWhisperModel("tiny.en");

      if (result.success) {
        showMessage(
          `Whisper model ready (${result.model_size_mb} MB)`,
          "success",
        );
        await loadVoiceConfiguration();
      } else {
        showMessage(`Failed to download model: ${result.error}`, "error");
      }
    } catch (error) {
      console.error("Failed to install whisper model:", error);
      showMessage(`Failed to download model: ${error.message}`, "error");
    } finally {
      setIsInstallingWhisper(false);
    }
  };

  const fetchBranding = useCallback(async () => {
    try {
      const response = await getBranding();
      debugLog("Fetched branding response", {
        hasData: Boolean(response?.data),
      });
      if (response && response.data) {
        const data = response.data;
        setBrandingName(
          (prev) => data.system_name ?? prev ?? persistedSystemName ?? "",
        );
        setSystemLogo(
          (prevLogo) =>
            data.logo_path ?? prevLogo ?? persistedSystemLogo ?? null,
        );
        debugLog("Updated branding state", {
          hasName: Boolean(data.system_name ?? persistedSystemName),
          hasLogo: Boolean(data.logo_path ?? persistedSystemLogo),
        });
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
    getMusicDirectory()
      .then((res) => {
        if (res?.data?.music_directory !== undefined) {
          setMusicDirectory(res.data.music_directory);
          setMusicDirectorySaved(res.data.music_directory);
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    interconnectorApi
      .getInterconnectorConfig()
      .then((res) => {
        const enabled =
          res?.data?.config?.is_enabled || res?.config?.is_enabled;
        if (enabled) {
          setInterconnectorEnabled(true);
          const nodeMode =
            res?.data?.config?.node_mode || res?.config?.node_mode;
          setInterconnectorIsClient(nodeMode === "client");
          // Check for pending approvals
          interconnectorApi
            .getPendingApprovals?.()
            .then((approvals) => {
              setInterconnectorPendingCount(
                Array.isArray(approvals) ? approvals.length : 0,
              );
            })
            .catch(() => {});
        } else if (!res?.error) {
          setInterconnectorEnabled(false);
          setInterconnectorPendingCount(0);
          setInterconnectorIsClient(false);
          setInterconnectorUpdateStatus(null);
        }
      })
      .catch(() => {});
  }, [interconnectorModalOpen]);

  // One-shot check for code updates when in client mode
  useEffect(() => {
    if (!interconnectorEnabled || !interconnectorIsClient) {
      setInterconnectorUpdateStatus(null);
      return;
    }
    const timer = setTimeout(() => {
      interconnectorApi
        .checkForUpdates()
        .then((res) => {
          const data = res?.data || res;
          if (!data?.error) {
            setInterconnectorUpdateStatus({
              available: data.available || false,
              count: data.count || 0,
              summary: data.summary || { backend: 0, frontend: 0, other: 0 },
            });
          }
        })
        .catch(() => {});
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
  const navChrome = useAppStore((state) => state.navChrome);
  const setNavChrome = useAppStore((state) => state.setNavChrome);

  const {
    activeModel,
    isLoadingModel,
    modelError: _modelError,
    refreshActiveModel,
  } = useStatus();

  // Socket listener for async model switching events
  useEffect(() => {
    socketRef.current = io(SOCKET_URL, {
      transports: ["polling", "websocket"],
      reconnection: true,
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
    });

    socketRef.current.on("connect", () => {
      debugLog("SettingsPage: Socket connected for model switch events");
    });

    socketRef.current.on("model_switch", (data) => {
      debugLog("SettingsPage: Received model_switch event", {
        status: data?.status,
        model: data?.model,
      });

      if (data.status === "loading") {
        setModelSwitchStatus("loading");
        setModelSwitchMessage(data.message || `Loading ${data.model}...`);
        showMessage(data.message || `Loading ${data.model}...`, "info");
      } else if (data.status === "complete") {
        setModelSwitchStatus("complete");
        setModelSwitchMessage(
          data.message || `Model switched to ${data.model}`,
        );
        showMessage(
          data.message || `Successfully switched to ${data.model}`,
          "success",
        );
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
      debugLog("SettingsPage: Socket disconnected");
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

  // Fetch paused state on mount (and can refresh)
  useEffect(() => {
    (async () => {
      try {
        const p = await apiService.getIndexingPaused();
        setIndexingPaused(!!p);
      } catch (_) {
        // Ignore errors fetching paused state
      }
    })();
  }, []);

  // Fetch GPU resources and embedding model list
  const fetchResources = useCallback(async () => {
    // Don't poll if page is hidden or component unmounted
    if (document.hidden) return;
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 8000);
      const r = await fetch("/api/model/resources", {
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      if (r.ok) {
        const d = await r.json();
        if (d.success) setGpuResources(payloadOf(d));
      }
    } catch (e) {
      if (e.name === "AbortError") return; // Timeout or navigation — not an error
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
            const partial = models.find(
              (m) => m.name.split(":")[0] === active.split(":")[0],
            );
            setSelectedEmbeddingModel(
              exact ? exact.name : partial ? partial.name : active,
            );
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
    const previous = autoresearchSettings;
    setAutoresearchSettings({ ...autoresearchSettings, [key]: String(value) });
    try {
      await ragAutoresearchService.updateSettings({ [key]: String(value) });
    } catch (e) {
      console.error("Failed to update autoresearch setting:", e);
      setAutoresearchSettings(previous);
      showMessage(
        "Could not save the autoresearch setting; it was not changed.",
        "error",
      );
    }
  };

  useEffect(() => {
    const fetchVersion = async () => {
      try {
        const result = await apiService.getVersion();
        const version = payloadOf(result)?.version ?? result?.version;
        if (version) setAppVersion(version);
      } catch (err) {
        console.warn("Failed to fetch app version:", err);
      }
    };
    fetchVersion();
  }, []);

  // Listen for chat history cleared events
  useEffect(() => {
    const handleChatHistoryCleared = (event) => {
      debugLog("SettingsPage: Chat history cleared event received", {
        hasDetail: Boolean(event.detail),
      });
      // The chat components will handle their own state clearing via the event
    };

    window.addEventListener("chatHistoryCleared", handleChatHistoryCleared);

    return () => {
      window.removeEventListener(
        "chatHistoryCleared",
        handleChatHistoryCleared,
      );
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
          setImageGenStatus({
            service_available: false,
            error: "Failed to get status",
          });
        }
      } catch (err) {
        setImageGenStatus({ service_available: false, error: err.message });
      }
    };
    fetchImageGenStatus();
    // ComfyUI can be started from the Plugins page while this one is open.
    const timer = setInterval(() => {
      if (!document.hidden) fetchImageGenStatus();
    }, 30000);
    return () => clearInterval(timer);
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
        const allowWeb =
          result?.data?.allow_web_search ?? result?.allow_web_search;
        if (typeof allowWeb === "boolean") {
          setWebSearchEnabled(allowWeb);
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
        const advDebug = result?.data?.advanced_debug ?? result?.advanced_debug;
        if (typeof advDebug === "boolean") {
          setAdvancedDebug(advDebug);
        }
      } catch (err) {
        console.warn("Failed to fetch advanced debug setting:", err);
      }
    };
    fetchAdvDebug();
  }, []);

  useEffect(() => {
    const fetchVerbatim = async () => {
      try {
        const result = await apiService.getVerbatimPrompts();
        const payload = result?.data ?? result;
        const verbatimOn = payload?.enabled;
        if (typeof verbatimOn === "boolean") {
          setVerbatimPromptsState(verbatimOn);
        }
        setVerbatimForcedByEnv(Boolean(payload?.forced_by_env));
      } catch (err) {
        console.warn("Failed to fetch verbatim prompts setting:", err);
      }
    };
    fetchVerbatim();
  }, []);

  useEffect(() => {
    const fetchMediaModels = async () => {
      try {
        const result = await apiService.getMediaModels();
        const data = result?.data ?? result;
        if (data && typeof data === "object" && !data.error) {
          setMediaModelsState((prev) => ({
            ...prev,
            stills_model: data.stills_model || prev.stills_model,
            cast_train_base: data.cast_train_base || prev.cast_train_base,
            max_quality_model: data.max_quality_model || prev.max_quality_model,
            character_lora_strength:
              data.character_lora_strength || prev.character_lora_strength,
            train_profiles: data.train_profiles || data.profiles || [],
            stills_profiles: data.stills_profiles || data.profiles || [],
          }));
        }
      } catch (err) {
        console.warn("Failed to fetch media models:", err);
      }
    };
    fetchMediaModels();
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
    const fetchChatThinkingDefault = async () => {
      try {
        const result = await apiService.getChatThinkingDefault();
        const enabled =
          result?.data?.chat_thinking_default ?? result?.chat_thinking_default;
        if (typeof enabled === "boolean") setChatThinkingDefaultState(enabled);
      } catch (err) {
        console.warn("Failed to fetch chat_thinking_default setting:", err);
      }
    };
    fetchChatThinkingDefault();
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
        }
      } catch (err) {
        console.warn("Failed to fetch RAG features:", err);
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
      window.dispatchEvent(new Event("voiceSettingsChanged"));
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
      if (
        result?.status === "switching" ||
        result?.message?.includes("Switching")
      ) {
        // Socket events will handle the rest
        debugLog("Model switch initiated, waiting for socket events");
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
  const formatByteSize = (bytes) => {
    const n = Number(bytes) || 0;
    if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
    if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
    if (n >= 1024) return `${Math.round(n / 1024)} KB`;
    return `${n} B`;
  };

  // Delete History: batch image, batch video and audio generation history —
  // records and media, plus the DB rows that mirror them. The dialog shows
  // real counts first because this frees disk space that cannot come back.
  const handleDeleteGenerationHistoryClick = async () => {
    setIsLoading(true);
    try {
      const counts = await apiService.getGenerationHistoryCounts();
      setDeleteHistoryCounts(counts && !counts.error ? counts : null);
      setDeleteHistoryDialogOpen(true);
    } finally {
      setIsLoading(false);
    }
  };

  const handleCancelDeleteHistory = () => {
    if (deleteHistoryInProgress) return;
    setDeleteHistoryDialogOpen(false);
  };

  const handleConfirmDeleteHistory = async () => {
    setDeleteHistoryInProgress(true);
    setIsLoading(true);
    showMessage("Deleting generation history...", "info");
    try {
      const result = await apiService.deleteGenerationHistory();
      const d = result?.deleted || {};
      const parts = [];
      if (d.images?.batches) parts.push(`${d.images.batches} image batch(es)`);
      if (d.videos?.batches) parts.push(`${d.videos.batches} video batch(es)`);
      if (d.audio?.files || d.audio?.jobs)
        parts.push(
          `${d.audio.files || 0} audio file(s), ${d.audio.jobs || 0} audio job(s)`,
        );
      const comfyFiles =
        (d.comfyui?.output?.files || 0) + (d.comfyui?.input?.files || 0);
      if (comfyFiles) parts.push(`${comfyFiles} ComfyUI scratch file(s)`);
      const dbRows =
        (d.documents || 0) + (d.folders || 0) + (d.job_history || 0);
      if (dbRows) parts.push(`${dbRows} database row(s)`);
      let message = parts.length
        ? `Deleted ${parts.join(", ")} (${formatByteSize(result?.bytes_freed)} freed).`
        : "No generation history to delete.";
      const skipped = result?.skipped || {};
      const skippedCount =
        (skipped.images?.length || 0) +
        (skipped.videos?.length || 0) +
        (skipped.audio?.length || 0);
      if (skippedCount) message += ` Skipped ${skippedCount} running job(s).`;
      if (skipped.comfyui?.length)
        message += ` ComfyUI is still rendering ${skipped.comfyui.length} prompt(s); its output and input folders were left alone.`;
      if (result?.sidecar_available === false)
        message +=
          " Audio service was not running; its job records were removed directly.";
      if (result?.errors?.length)
        message += ` ${result.errors.length} error(s): ${result.errors[0]}`;
      showMessage(
        message,
        skippedCount || result?.errors?.length ? "warning" : "success",
      );
      setDeleteHistoryDialogOpen(false);
    } catch (err) {
      showMessage(
        `Failed to delete generation history: ${err.message}`,
        "error",
      );
    } finally {
      setDeleteHistoryInProgress(false);
      setIsLoading(false);
    }
  };

  const handleClearPycacheFoldersClick = async () => {
    if (
      !window.confirm(
        "Clear Python bytecode cache (__pycache__)? This can help apply code changes but does not affect data or memory.",
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
      let message =
        result?.message || "Python cache folders cleared successfully.";

      if (result?.statistics) {
        const stats = result.statistics;
        const details = [];

        if (stats.directories_cleaned > 0) {
          details.push(`${stats.directories_cleaned} directory(ies) cleaned`);
        }

        if (stats.pyc_files_deleted > 0) {
          details.push(`${stats.pyc_files_deleted} .pyc file(s) deleted`);
        }

        if (stats.size_formatted) {
          details.push(`${stats.size_formatted} freed`);
        }

        if (result?.modules_purged_count > 0) {
          details.push(
            `${result.modules_purged_count} module(s) purged from memory`,
          );
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
        showMessage(
          `Failed to clear Python cache folders: ${err.message}`,
          "error",
        );
      }
    } finally {
      setIsLoading(false);
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

  // Web access is a server-side gate: allow_web_search is read by the web,
  // browser and address-lookup tools. The localStorage copy only seeds the
  // first paint, so a failed save has to roll both back or they disagree.
  const handleWebSearchToggle = async (nextValue) => {
    const previous = webSearchEnabled;
    const isEnabled =
      typeof nextValue === "boolean" ? nextValue : !webSearchEnabled;

    setWebSearchEnabled(isEnabled);
    try {
      localStorage.setItem(WEB_SEARCH_ENABLED_KEY, String(isEnabled));
    } catch (e) {
      console.warn("Failed to persist web search setting:", e);
    }
    try {
      await apiService.setWebAccess(isEnabled);
    } catch (err) {
      console.warn("Failed to update web access setting:", err);
      setWebSearchEnabled(previous);
      try {
        localStorage.setItem(WEB_SEARCH_ENABLED_KEY, String(previous));
      } catch (e) {
        console.warn("Failed to restore web search setting:", e);
      }
      showMessage(
        "Could not save web access; the setting was not changed.",
        "error",
      );
      return;
    }
    debugLog("Web Search toggled", { isEnabled });
    showMessage(
      isEnabled
        ? "Web access enabled: tools may fetch pages and search the web."
        : "Web access disabled: web and browser tools are blocked.",
      "info",
    );
  };

  // A toggle that mirrors to localStorage for first paint and persists to the
  // server. On failure both roll back, so the chip never shows a state the
  // server does not have.
  const persistToggle = async ({
    next,
    previous,
    setState,
    storageKey,
    save,
    onText,
    offText,
  }) => {
    setState(next);
    try {
      localStorage.setItem(storageKey, String(next));
    } catch {
      // non-fatal
    }
    try {
      const result = await save(next);
      if (result?.error) throw new Error(result.error.message || result.error);
      showMessage(next ? onText : offText, "info");
    } catch (err) {
      setState(previous);
      try {
        localStorage.setItem(storageKey, String(previous));
      } catch {
        // non-fatal
      }
      showMessage(
        `Could not save: ${err.message}. The setting was not changed.`,
        "error",
      );
    }
  };

  const handleAdvancedDebugToggle = (event) =>
    persistToggle({
      next: deriveToggleValue(event, advancedDebug),
      previous: advancedDebug,
      setState: setAdvancedDebug,
      storageKey: ADV_DEBUG_ENABLED_KEY,
      save: apiService.setAdvancedDebug,
      onText: "Verbose logging enabled.",
      offText: "Verbose logging disabled.",
    });
  const handleVerbatimPromptsToggle = async (event) => {
    if (verbatimSaving) return;
    if (verbatimForcedByEnv) {
      showMessage(
        "Verbatim prompts are forced on by VERBATIM_PROMPTS in the server environment. Remove that variable and restart to control it here.",
        "info",
      );
      return;
    }
    const isEnabled = deriveToggleValue(event, verbatimPrompts);
    setVerbatimSaving(true);
    try {
      const result = await apiService.setVerbatimPrompts(isEnabled);
      if (result?.error) throw new Error(result.error.message || result.error);
      const payload = result?.data ?? result;
      if (typeof payload?.enabled !== "boolean") {
        throw new Error("Server did not confirm the prompt setting");
      }
      setVerbatimPromptsState(payload.enabled);
      setVerbatimForcedByEnv(Boolean(payload.forced_by_env));
      showMessage(
        payload.enabled
          ? "Verbatim prompts ON — AI prompt rewriting is disabled."
          : "Verbatim prompts OFF — prompts get AI enhancement again.",
        "info",
      );
    } catch (err) {
      showMessage(`Could not save Verbatim prompts: ${err.message}`, "error");
    } finally {
      setVerbatimSaving(false);
    }
  };
  const handleLlmDebugToggle = (event) =>
    persistToggle({
      next: deriveToggleValue(event, llmDebug),
      previous: llmDebug,
      setState: setLlmDebugState,
      storageKey: LLM_DEBUG_ENABLED_KEY,
      save: apiService.setLlmDebug,
      onText: "LLM debug logging enabled.",
      offText: "LLM debug logging disabled.",
    });
  const handleBehaviorLearningToggle = (event) =>
    persistToggle({
      next: deriveToggleValue(event, behaviorLearningEnabled),
      previous: behaviorLearningEnabled,
      setState: setBehaviorLearningEnabled,
      storageKey: BEHAVIOR_LEARNING_ENABLED_KEY,
      save: apiService.setBehaviorLearning,
      onText: "Behaviour learning enabled.",
      offText: "Behaviour learning disabled.",
    });

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

  const handleCreateBackupConfirm = async ({
    type,
    components,
    name,
    include_plugins,
  }) => {
    setIsProcessingBackup(true);
    try {
      const res = await apiService.createServerBackup(
        type,
        components,
        name,
        include_plugins,
      );
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

  // ── v3 handlers: lifted from inline JSX so the panels stay declarative ──
  const saveNickname = async () => {
    const trimmedName = brandingName.trim();
    if (!trimmedName) return;
    if (trimmedName === (persistedSystemName || "")) return;
    try {
      const fd = new FormData();
      fd.append("system_name", trimmedName);
      await updateBranding(fd);
      const refreshed = await fetchBranding();
      const latestName =
        refreshed?.system_name ?? trimmedName ?? persistedSystemName ?? "";
      setSystemInfo(latestName, systemLogo || persistedSystemLogo || null);
      showMessage("Nickname saved", "success");
    } catch (err) {
      showMessage("Failed to update nickname: " + err.message, "error");
    }
  };

  const saveMusicDirectory = async () => {
    try {
      const result = await setMusicDirectoryAPI(musicDirectory.trim());
      if (result?.error) throw new Error(result.error);
      setMusicDirectorySaved(musicDirectory);
      showMessage(
        `Media library path saved: ${musicDirectory || "(default)"}`,
        "success",
      );
    } catch (err) {
      showMessage(
        "Failed to save media library path: " + (err.message || err),
        "error",
      );
    }
  };

  const handleRulesToggle = async (next) => {
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
          ? "Rules enabled: the active rules from the Rules page apply"
          : "Rules disabled: chat uses the built-in prompt",
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
  };

  const handleChatThinkingToggle = async (next) => {
    setChatThinkingDefaultState(next);
    try {
      const result = await apiService.setChatThinkingDefault(next);
      if (result?.error) throw new Error(result.error);
      showMessage(
        next
          ? "Thinking on by default: thinking models reason step by step (slower). Use /thinking off per chat."
          : "Thinking off by default: faster replies. Use /thinking on per chat.",
        "info",
      );
    } catch (err) {
      console.error("Failed to update chat_thinking_default:", err);
      setChatThinkingDefaultState(!next);
      showMessage("Failed to update Chat thinking setting", "error");
    }
  };

  const handleSetEmbeddingClick = async () => {
    if (
      !window.confirm(
        "Switch the embedding model?\n\n" +
          "Same vector width: the existing index is kept.\n" +
          "Different width: the index is emptied and every document must be re-indexed.\n\nContinue?",
      )
    )
      return;
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
        const payload = payloadOf(d);
        const dims = payload?.dimensions;
        showMessage(
          payload?.index_cleared
            ? `Embedding switched to ${selectedEmbeddingModel} (${dims}d). The index was emptied; re-index your documents.`
            : `Embedding switched to ${selectedEmbeddingModel} (${dims}d). Same width, index kept.`,
          payload?.index_cleared ? "warning" : "success",
        );
        fetchResources();
        setProfilesReloadKey((k) => k + 1);
      } else {
        showMessage(d.error || "Failed to switch embedding", "error");
      }
    } catch (e) {
      showMessage(`Failed: ${e.message}`, "error");
    } finally {
      setIsSwitchingEmbedding(false);
    }
  };

  // Media model selects: optimistic, rolled back when the server refuses.
  const saveMediaModel = async (field, value, label) => {
    const previous = mediaModels[field];
    setMediaModelsState((p) => ({ ...p, [field]: value }));
    try {
      const res = await apiService.setMediaModels({ [field]: value });
      const data = res?.data ?? res;
      if (res?.error || data?.error) throw new Error(res?.error || data?.error);
      showMessage(`${label}: ${value}`, "success");
    } catch (err) {
      setMediaModelsState((p) => ({ ...p, [field]: previous }));
      showMessage(
        err.message || `Failed to save ${label.toLowerCase()}`,
        "error",
      );
    }
  };

  const saveLoraStrength = async (field, label, raw) => {
    const v = parseFloat(raw);
    if (Number.isNaN(v)) return;
    try {
      const res = await apiService.setMediaModels({ [field]: v });
      const data = res?.data ?? res;
      if (data?.character_lora_strength) {
        setMediaModelsState((p) => ({
          ...p,
          character_lora_strength: data.character_lora_strength,
        }));
      }
      showMessage(`${label} LoRA strength: ${v}`, "success");
    } catch (err) {
      showMessage(err.message || "Failed to save LoRA strength", "error");
    }
  };

  const handleIndexingPausedToggle = async (next) => {
    try {
      if (next) {
        await apiService.pauseIndexing();
        setIndexingPaused(true);
        showMessage(
          "Indexing paused: new documents wait until you resume",
          "warning",
        );
      } else {
        await apiService.resumeIndexing();
        setIndexingPaused(false);
        showMessage("Indexing resumed", "success");
      }
    } catch (e) {
      showMessage(
        "Failed to change indexing pause: " + (e.message || e),
        "error",
      );
    }
  };

  // Read-modify-write on the plugin config. If the read fails we stop rather
  // than write a config that contains only is_enabled.
  const handleInterconnectorToggle = async (next) => {
    try {
      const currentConfig = await interconnectorApi.getInterconnectorConfig();
      const cfg = currentConfig?.data?.config || currentConfig?.config;
      if (!cfg)
        throw new Error("could not read the current Interconnector config");
      await interconnectorApi.updateInterconnectorConfig({
        ...cfg,
        is_enabled: next,
      });
      setInterconnectorEnabled(next);
      showMessage(
        next ? "Interconnector enabled" : "Interconnector disabled",
        "info",
      );
    } catch (err) {
      console.error("Failed to toggle Interconnector:", err);
      showMessage(
        `Interconnector was not changed: ${err.message || err}`,
        "error",
      );
    }
  };

  const openClearChatDialog = async () => {
    try {
      const counts = await apiService.getChatHistoryCounts();
      setChatHistoryCounts(counts && !counts.error ? counts : null);
    } catch {
      setChatHistoryCounts(null);
    }
    setClearChatOpen(true);
  };

  const confirmClearChat = async () => {
    setClearChatBusy(true);
    try {
      const result = await apiService.clearChatHistory("all");
      if (result?.error && !result.warning)
        throw new Error(result.error.message || result.error);
      showMessage(
        result?.message || "Chat history cleared.",
        result?.warning ? "warning" : "success",
      );
      setClearChatOpen(false);
    } catch (err) {
      showMessage(`Failed to clear chat history: ${err.message}`, "error");
    } finally {
      setClearChatBusy(false);
    }
  };

  const fetchMemoryCount = useCallback(async () => {
    try {
      const res = await fetch("/api/memory?status=active&limit=1");
      const body = await res.json();
      const data = body?.data ?? body;
      if (typeof data?.total === "number") setMemoryCount(data.total);
    } catch (err) {
      console.warn("Failed to count memories:", err);
    }
  }, []);

  useEffect(() => {
    fetchMemoryCount();
  }, [fetchMemoryCount]);

  const confirmClearLearning = async () => {
    setLearningBusy(true);
    try {
      const result =
        learningClear === "rules"
          ? await apiService.purgeBehaviorLearning()
          : await clearBehaviorLog();
      if (result?.error) throw new Error(result.error.message || result.error);
      showMessage(
        result?.message ||
          (learningClear === "rules"
            ? "Learned rules cleared."
            : "Behaviour log cleared."),
        "success",
      );
      setLearningClear(null);
    } catch (err) {
      showMessage(`Failed to clear: ${err.message}`, "error");
    } finally {
      setLearningBusy(false);
    }
  };

  const confirmClearMemories = async () => {
    setClearMemoriesBusy(true);
    try {
      const res = await fetch("/api/memory/clear", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "CLEAR_MEMORIES" }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok)
        throw new Error(body?.error || body?.message || `HTTP ${res.status}`);
      showMessage("Agent memory cleared.", "success");
      setClearMemoriesOpen(false);
      fetchMemoryCount();
    } catch (err) {
      showMessage(`Failed to clear agent memory: ${err.message}`, "error");
    } finally {
      setClearMemoriesBusy(false);
    }
  };

  // ── Layout: independent stacked columns so panels reflow instead of
  // aligning to a row. 3 columns from xl (1536), 2 from md (900), else 1.
  const columns = isXl ? 3 : isMd ? 2 : 1;

  const activeModelEntry = availableModels.find((m) => m.name === activeModel);
  const activeEmbeddingEntry = embeddingModels.find(
    (m) => m.name === embeddingModel,
  );
  const gpu = gpuResources?.gpu;
  const indexTotals = indexProfiles.reduce(
    (acc, p) => {
      const proj = p.projection || {};
      if (proj.exists) {
        acc.rows += proj.rows || 0;
        acc.bytes += proj.size_bytes || 0;
      }
      if (p.active) acc.active += 1;
      return acc;
    },
    { rows: 0, bytes: 0, active: 0 },
  );
  const chatModelPending =
    Boolean(selectedModel) && selectedModel !== activeModel;
  const embeddingPending =
    Boolean(selectedEmbeddingModel) &&
    selectedEmbeddingModel !== embeddingModel;
  const musicDirectoryPending =
    (musicDirectory || "") !== (musicDirectorySaved || "");

  const chatSizeOf = (m) => {
    const num = parseFloat(m.details?.parameter_size || "");
    if (Number.isNaN(num)) return null;
    if (num <= 3) return "small";
    if (num <= 10) return "medium";
    return "large";
  };
  const chatSizes = [
    ...new Set(availableModels.map(chatSizeOf).filter(Boolean)),
  ];
  const chatSizeLabels = { small: "≤3B", medium: "3–10B", large: ">10B" };
  const embedDims = [
    ...new Set(embeddingModels.map((m) => m.dimensions).filter(Boolean)),
  ].sort((a, b) => a - b);

  const strip = (
    <DashboardStrip>
      <DashboardTile
        label="Chat model"
        tone={isLoadingModel ? "warn" : activeModel ? "ok" : "off"}
        value={activeModel || "none"}
        sub={
          activeModelEntry?.details?.parameter_size
            ? `${activeModelEntry.details.parameter_size} · ${formatByteSize(activeModelEntry.size)}`
            : undefined
        }
        onClick={() =>
          document
            .getElementById("settings-models")
            ?.scrollIntoView({ behavior: "smooth", block: "start" })
        }
      />
      <DashboardTile
        label="Embedding"
        tone={
          embeddingModel &&
          embeddingModel !== "Not Set" &&
          embeddingModel !== "Not Available"
            ? "ok"
            : "off"
        }
        value={embeddingModel || "none"}
        sub={
          activeEmbeddingEntry?.dimensions
            ? `${activeEmbeddingEntry.dimensions}d`
            : undefined
        }
      />
      {gpu?.total_mb > 0 && (
        <DashboardTile
          label="VRAM"
          value={`${(gpu.used_mb / 1024).toFixed(1)} / ${(gpu.total_mb / 1024).toFixed(1)} GB`}
          progress={gpu.utilization_pct}
        />
      )}
      {gpuResources && (
        <DashboardTile
          label="Loaded in VRAM"
          tone={gpuResources?.loaded_models?.length ? "ok" : "off"}
          value={
            gpuResources?.loaded_models?.length
              ? `${gpuResources.loaded_models.length} model${gpuResources.loaded_models.length === 1 ? "" : "s"}`
              : "nothing"
          }
          sub={gpuResources?.loaded_models?.map((m) => m.name).join(", ")}
        />
      )}
      <DashboardTile
        label="Index"
        tone={indexTotals.rows > 0 ? "ok" : "off"}
        value={
          indexTotals.rows > 0
            ? `${indexTotals.rows.toLocaleString()} vectors · ${formatByteSize(indexTotals.bytes)}`
            : "empty"
        }
        sub={`${indexTotals.active} of ${indexProfiles.length} profiles active${indexingPaused ? " · paused" : ""}`}
        onClick={() =>
          document
            .getElementById("settings-knowledge")
            ?.scrollIntoView({ behavior: "smooth", block: "start" })
        }
      />
      <DashboardTile
        label="Image generation"
        tone={
          imageGenStatus?.available
            ? "ok"
            : imageGenStatus === null
              ? "warn"
              : "off"
        }
        value={
          imageGenStatus === null
            ? "checking"
            : imageGenStatus?.available
              ? "Available"
              : "Unavailable"
        }
        sub={
          imageGenStatus?.available ? undefined : "start ComfyUI from Plugins"
        }
      />
      {interconnectorEnabled && (
        <DashboardTile
          label="Interconnector"
          tone={
            interconnectorPendingCount > 0 ||
            interconnectorUpdateStatus?.summary?.total > 0
              ? "warn"
              : "ok"
          }
          value={
            interconnectorUpdateStatus?.summary?.total > 0
              ? `${interconnectorUpdateStatus.summary.total} code updates`
              : interconnectorPendingCount > 0
                ? `${interconnectorPendingCount} pending`
                : "In sync"
          }
          onClick={() => setInterconnectorModalOpen(true)}
        />
      )}
    </DashboardStrip>
  );

  const generalPanel = (
    <SettingsPanel
      id="settings-general"
      title="General"
      description="Identity, appearance, paths."
    >
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
                if (brandingName.trim())
                  fd.append("system_name", brandingName.trim());
                await updateBranding(fd);
                setBrandingFile(null);
                e.target.value = "";
                const refreshed = await fetchBranding();
                const latestLogo =
                  refreshed?.logo_path ??
                  systemLogo ??
                  persistedSystemLogo ??
                  null;
                setSystemInfo(
                  brandingName || persistedSystemName || "",
                  latestLogo,
                );
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
      <Line nowrap>
        <Tooltip title="Change profile image">
          <label
            htmlFor="logo-upload"
            style={{ cursor: "pointer", display: "inline-flex" }}
          >
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
              sx={{
                width: 40,
                height: 40,
                border: 1,
                borderColor: "divider",
                "&:hover": { opacity: 0.8 },
              }}
            >
              <AccountBoxIcon />
            </Avatar>
          </label>
        </Tooltip>
        <TextField
          label="Nickname"
          value={brandingName}
          onChange={(e) => setBrandingName(e.target.value)}
          size="small"
          className="grow"
          onBlur={saveNickname}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.target.blur();
          }}
        />
        <ActionButton
          onClick={() => setThemeModalOpen(true)}
          tooltip={`Current theme: ${themeName}`}
        >
          Theme
        </ActionButton>
      </Line>
      <Cluster label="Navigation" note="how pages are listed">
        <Line>
          <ChoiceChips
            ariaLabel="Navigation mode"
            value={navChrome}
            onChange={setNavChrome}
            options={[
              {
                value: NAV_CHROME.SIDEBAR,
                label: "Sidebar",
                tooltip: "Every page in one list",
              },
              {
                value: NAV_CHROME.SOFTWARE,
                label: "Workspaces",
                tooltip: "Pages grouped by workspace in the top bar",
              },
            ]}
          />
        </Line>
      </Cluster>
      <Cluster
        label="Media library"
        note="where Audio Studio and Music Video look for tracks"
      >
        <Line nowrap>
          <TextField
            size="small"
            className="grow"
            value={musicDirectory}
            onChange={(e) => setMusicDirectory(e.target.value)}
            placeholder="~/Music"
            onKeyDown={(e) => {
              if (e.key === "Enter" && musicDirectoryPending)
                saveMusicDirectory();
            }}
          />
          <ActionButton
            kind={musicDirectoryPending ? "primary" : "neutral"}
            disabled={!musicDirectoryPending}
            onClick={saveMusicDirectory}
          >
            Save
          </ActionButton>
        </Line>
      </Cluster>
      <ProfileSection />
    </SettingsPanel>
  );

  const modelsPanel = (
    <SettingsPanel
      id="settings-models"
      title="Models"
      description="Which models answer chat and index documents."
    >
      <Cluster
        label="Chat"
        note={chatSizes.length > 1 ? "filter by size" : undefined}
      >
        {chatSizes.length > 1 && (
          <ChoiceChips
            ariaLabel="Chat model size filter"
            value={chatSizeFilter || "all"}
            onChange={(v) => setChatSizeFilter(v === "all" ? null : v)}
            options={[
              { value: "all", label: "All" },
              ...["small", "medium", "large"]
                .filter((s) => chatSizes.includes(s))
                .map((s) => ({
                  value: s,
                  label: `${chatSizeLabels[s]} · ${availableModels.filter((m) => chatSizeOf(m) === s).length}`,
                })),
            ]}
          />
        )}
        <Line nowrap>
          <FormControl
            size="small"
            className="grow"
            disabled={isLoading || isLoadingModel}
          >
            <InputLabel>Chat model</InputLabel>
            <Select
              value={selectedModel}
              label="Chat model"
              onChange={(e) => setSelectedModel(e.target.value)}
              error={
                availableModels.length > 0 &&
                !availableModels.some((m) => m.name === selectedModel)
              }
            >
              {availableModels
                .filter(
                  (m) =>
                    chatSizeFilter === null ||
                    chatSizeOf(m) === chatSizeFilter ||
                    m.name === activeModel,
                )
                .map((m) => (
                  <MenuItem key={m.name} value={m.name}>
                    {m.name}
                    {m.details?.parameter_size
                      ? ` (${m.details.parameter_size}`
                      : ""}
                    {m.size
                      ? `${m.details?.parameter_size ? ", " : " ("}${formatByteSize(m.size)})`
                      : m.details?.parameter_size
                        ? ")"
                        : ""}
                  </MenuItem>
                ))}
            </Select>
          </FormControl>
          <ActionButton
            kind={chatModelPending ? "primary" : "neutral"}
            onClick={handleSetModelClick}
            disabled={!chatModelPending || isLoading || isLoadingModel}
            loading={isLoadingModel}
            tooltip="Loads the model, unloads the previous one and rebuilds the query engine. Can take minutes on a cold GPU."
          >
            Set active
          </ActionButton>
          <ActionButton
            onClick={async () => {
              setIsTestingLLM(true);
              try {
                const result = await apiService.testLLM();
                if (result?.error) throw new Error(result.error);
                showMessage(
                  `${activeModel || "Active model"} answered in ${result?.duration_sec ?? "?"}s`,
                  "success",
                );
              } catch (err) {
                showMessage(`LLM test failed: ${err.message}`, "error");
              } finally {
                setIsTestingLLM(false);
              }
            }}
            loading={isTestingLLM}
            disabled={isLoading || isLoadingModel}
            tooltip="Sends one short prompt to the ACTIVE model, not the one selected above"
          >
            Test
          </ActionButton>
          <ActionButton
            onClick={fetchAvailableModels}
            disabled={isLoading}
            tooltip="Refresh the model list from Ollama"
            aria-label="Refresh chat models"
            sx={{ px: 1, minWidth: 0 }}
          >
            <RefreshIcon sx={{ fontSize: 16 }} />
          </ActionButton>
        </Line>
        {modelSwitchStatus === "loading" && (
          <Box>
            <LinearProgress sx={{ height: 4, borderRadius: 2 }} />
            <Hint>{modelSwitchMessage || "Switching model…"}</Hint>
          </Box>
        )}
      </Cluster>

      <Cluster
        label="Embedding"
        note={embedDims.length > 1 ? "filter by width" : undefined}
      >
        {embedDims.length > 1 && (
          <ChoiceChips
            ariaLabel="Embedding dimension filter"
            value={embedDimFilter === null ? "all" : String(embedDimFilter)}
            onChange={(v) => setEmbedDimFilter(v === "all" ? null : Number(v))}
            options={[
              { value: "all", label: "All" },
              ...embedDims.map((d) => ({
                value: String(d),
                label: `${d}d · ${embeddingModels.filter((m) => m.dimensions === d).length}`,
              })),
            ]}
          />
        )}
        <Line nowrap>
          <FormControl
            size="small"
            className="grow"
            disabled={isSwitchingEmbedding}
          >
            <InputLabel>Embedding model</InputLabel>
            <Select
              value={selectedEmbeddingModel}
              label="Embedding model"
              onChange={(e) => setSelectedEmbeddingModel(e.target.value)}
            >
              {embeddingModels
                .filter(
                  (m) =>
                    embedDimFilter === null ||
                    m.dimensions === embedDimFilter ||
                    m.name === embeddingModel,
                )
                .map((m) => (
                  <MenuItem key={m.name} value={m.name}>
                    {m.name}
                    {m.size_mb ? ` (${m.size_mb}MB` : ""}
                    {m.dimensions
                      ? `${m.size_mb ? ", " : " ("}${m.dimensions}d)`
                      : m.size_mb
                        ? ")"
                        : ""}
                  </MenuItem>
                ))}
            </Select>
          </FormControl>
          <ActionButton
            kind={embeddingPending ? "primary" : "neutral"}
            disabled={!embeddingPending}
            loading={isSwitchingEmbedding}
            onClick={handleSetEmbeddingClick}
            tooltip="Same vector width keeps the index. A different width empties it and every document must be re-indexed."
          >
            Set active
          </ActionButton>
          <ActionButton
            onClick={fetchEmbeddingModels}
            tooltip="Refresh embedding models (probes each one, can take a moment)"
            aria-label="Refresh embedding models"
            sx={{ px: 1, minWidth: 0 }}
          >
            <RefreshIcon sx={{ fontSize: 16 }} />
          </ActionButton>
        </Line>
      </Cluster>
    </SettingsPanel>
  );

  const chatPanel = (
    <SettingsPanel
      id="settings-chat"
      title="Chat"
      description="What every conversation can do by default."
    >
      <Cluster label="Capabilities" note="gear opens that feature's settings">
        <Line>
          <SettingChip
            label="Rules"
            on={rulesEnabled}
            onToggle={handleRulesToggle}
            onSettings={() => navigate("/rules")}
            tooltip="Persona and behaviour rules from the Rules page. Off swaps them for a plain built-in prompt."
          />
          <SettingChip
            label="Chat thinking"
            on={chatThinkingDefault}
            onToggle={handleChatThinkingToggle}
            tooltip="Default for thinking models. Per chat: /thinking on or off."
          />
          <SettingChip
            label="Web access"
            on={webSearchEnabled}
            onToggle={handleWebSearchToggle}
            tooltip="Lets the web, browser and address tools reach the internet."
          />
          <SettingChip
            label="Voice chat"
            on={voiceChatEnabled}
            onToggle={setVoiceChatEnabled}
            onSettings={() => setVoiceSettingsModalOpen(true)}
            tooltip="Speak to the assistant and hear replies. Stored in this browser."
          />
        </Line>
      </Cluster>
      <Cluster label="Retrieval" note="how answers use your documents">
        <Line>
          <SettingChip
            label="Enhanced context"
            on={enhancedContext}
            onToggle={(next) => handleEnhancedContextChange(next)}
            tooltip="Adds conversation and document context to every prompt. Applies from the next message."
          />
          <SettingChip
            label="Advanced RAG"
            on={advancedRag}
            onToggle={(next) => handleAdvancedRagChange(next)}
            tooltip="Query rewriting and reranking before retrieval. Applies from the next message."
          />
          <SettingChip
            label="Behaviour learning"
            on={behaviorLearningEnabled}
            onToggle={(next) => handleBehaviorLearningToggle(next)}
            tooltip="Learns from corrections and preferences across chats."
          />
        </Line>
      </Cluster>
    </SettingsPanel>
  );

  const generationPanel = (
    <SettingsPanel
      id="settings-generation"
      title="Generation"
      description="Defaults for images, video and voice."
    >
      <Cluster label="Prompts">
        <Line>
          <SettingChip
            label="Verbatim prompts"
            on={verbatimPrompts}
            onToggle={() => handleVerbatimPromptsToggle()}
            disabled={verbatimForcedByEnv || verbatimSaving}
            note={verbatimForcedByEnv ? "forced by environment" : undefined}
            tooltip={
              verbatimForcedByEnv
                ? "VERBATIM_PROMPTS is set in the server environment. Remove it and restart to control this here."
                : "On: disables AI prompt rewriting and automatic style additions. Model prompt limits and instruction-following still apply. Off: allows prompt enhancement."
            }
          />
        </Line>
      </Cluster>
      <Cluster
        label="Stills models"
        note="Z-Image Turbo by default; FLUX for max quality; the train base must match the LoRA family"
      >
        <Line>
          <FormControl size="small" className="grow">
            <InputLabel id="media-stills-label">Stills</InputLabel>
            <Select
              labelId="media-stills-label"
              label="Stills"
              value={mediaModels.stills_model || "zimage-turbo"}
              onChange={(e) =>
                saveMediaModel("stills_model", e.target.value, "Stills model")
              }
            >
              {(mediaModels.stills_profiles?.length
                ? mediaModels.stills_profiles
                : [
                    { id: "zimage-turbo", name: "Z-Image Turbo" },
                    { id: "flux-dev", name: "FLUX.1 Dev" },
                    { id: "krea2-turbo", name: "Krea 2 Turbo" },
                    { id: "sdxl-legacy", name: "SDXL (Legacy)" },
                  ]
              ).map((p) => (
                <MenuItem key={p.id} value={p.id}>
                  {p.name || p.id}
                  {p.recommended ? " ★" : ""}
                  {p.deprecated ? " (legacy)" : ""}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" className="grow">
            <InputLabel id="media-train-label">Cast LoRA base</InputLabel>
            <Select
              labelId="media-train-label"
              label="Cast LoRA base"
              value={mediaModels.cast_train_base || "zimage-turbo"}
              onChange={(e) =>
                saveMediaModel(
                  "cast_train_base",
                  e.target.value,
                  "Cast train base",
                )
              }
            >
              {(mediaModels.train_profiles?.length
                ? mediaModels.train_profiles
                : [
                    { id: "zimage-turbo", name: "Z-Image Turbo" },
                    { id: "sdxl-legacy", name: "SDXL (Legacy)" },
                  ]
              ).map((p) => (
                <MenuItem key={p.id} value={p.id}>
                  {p.name || p.id}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" className="grow">
            <InputLabel id="media-max-label">Max quality</InputLabel>
            <Select
              labelId="media-max-label"
              label="Max quality"
              value={mediaModels.max_quality_model || "flux-dev"}
              onChange={(e) =>
                saveMediaModel(
                  "max_quality_model",
                  e.target.value,
                  "Max quality model",
                )
              }
            >
              <MenuItem value="flux-dev">FLUX.1 Dev</MenuItem>
              <MenuItem value="zimage-turbo">Z-Image Turbo</MenuItem>
            </Select>
          </FormControl>
        </Line>
      </Cluster>
      <Cluster
        label="Character LoRA strength"
        note="for stills and keyframes; video motion models keep the identity baked into the still"
      >
        <Line>
          {[
            {
              key: "zimage",
              label: "Z-Image",
              field: "character_lora_strength_zimage",
              def: 0.9,
            },
            {
              key: "sdxl",
              label: "SDXL",
              field: "character_lora_strength_sdxl",
              def: 0.25,
            },
            {
              key: "flux",
              label: "FLUX",
              field: "character_lora_strength_flux",
              def: 0.9,
            },
          ].map(({ key, label, field, def }) => (
            <TextField
              key={key}
              size="small"
              type="number"
              className="grow"
              label={label}
              inputProps={{ min: 0, max: 1.5, step: 0.05 }}
              value={mediaModels.character_lora_strength?.[key] ?? def}
              onChange={(e) => {
                const v = e.target.value;
                setMediaModelsState((p) => ({
                  ...p,
                  character_lora_strength: {
                    ...(p.character_lora_strength || {}),
                    [key]: v,
                  },
                }));
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") e.target.blur();
              }}
              onBlur={(e) => saveLoraStrength(field, label, e.target.value)}
            />
          ))}
        </Line>
      </Cluster>
      <Cluster
        label="Model libraries"
        note="download and manage the models behind each studio"
      >
        <Line>
          <StatusPill
            tone={
              imageGenStatus?.available
                ? "ok"
                : imageGenStatus === null
                  ? "warn"
                  : "neutral"
            }
            label={
              imageGenStatus === null
                ? "Image generation: checking"
                : imageGenStatus?.available
                  ? "Image generation available"
                  : "Image generation unavailable"
            }
            tooltip={
              imageGenStatus?.available
                ? ""
                : "Start ComfyUI from the Plugins page"
            }
          />
          <ActionButton onClick={() => setImageModelsModalOpen(true)}>
            Image
          </ActionButton>
          <ActionButton onClick={() => setInfographicModelsModalOpen(true)}>
            Infographic
          </ActionButton>
          <ActionButton onClick={() => setVideoModelsModalOpen(true)}>
            Video
          </ActionButton>
          <ActionButton onClick={() => setVoiceModelsModalOpen(true)}>
            Voice
          </ActionButton>
        </Line>
      </Cluster>
    </SettingsPanel>
  );

  const knowledgePanel = (
    <SettingsPanel
      id="settings-knowledge"
      title="Knowledge"
      description="Index profiles, indexing, nightly research."
    >
      <Cluster
        label="Index profiles"
        note="one corpus, several projections; lit = built and queried"
      >
        <IndexProfileChips
          onLoaded={setIndexProfiles}
          reloadKey={profilesReloadKey}
          showMessage={showMessage}
          onEdit={setEditProfile}
        />
      </Cluster>
      <Cluster label="Indexing">
        <Line>
          <SettingChip
            label="Indexing paused"
            on={indexingPaused}
            onToggle={handleIndexingPausedToggle}
            tooltip="Paused: new documents wait instead of embedding. Useful during heavy GPU work."
          />
          <Sep />
          <ActionButton
            onClick={() =>
              handleActionClick(
                apiService.optimizeIndex,
                [],
                null,
                "Optimizing index...",
                "Index optimized.",
                "Failed to optimize index",
              )
            }
            disabled={isLoading}
            tooltip="Removes vectors whose document is gone. Nothing else changes."
          >
            Optimize
          </ActionButton>
          <ActionButton
            onClick={() =>
              handleActionClick(
                apiService.buildCorpusSummaries,
                [{ replace: true }],
                "Build corpus summaries? This replaces the previous summary tree and uses the GPU for several minutes.",
                "Queuing summary build...",
                "Summary build queued.",
                "Failed to queue the summary build",
              )
            }
            disabled={isLoading}
            tooltip="Builds the RAPTOR summary tree on the indexing queue. Several minutes of GPU."
          >
            Build summaries
          </ActionButton>
          <ActionButton
            onClick={async () => {
              try {
                const res = await apiService.resumePendingIndexing();
                showMessage(
                  res?.message || "Pending documents queued for indexing",
                  "success",
                );
              } catch (e) {
                showMessage(
                  "Failed to resume pending: " + (e.message || e),
                  "error",
                );
              }
            }}
            disabled={isLoading || indexingPaused}
            tooltip={
              indexingPaused
                ? "Turn off Indexing paused first"
                : "Queues every document still pending or in error"
            }
          >
            Index pending
          </ActionButton>
        </Line>
      </Cluster>
      <Cluster
        label="Autoresearch"
        note="overnight retrieval tuning; parameters and history on its own page"
      >
        <Line>
          <SettingChip
            label="Nightly auto-start"
            on={autoresearchSettings?.rag_autoresearch_auto_enabled === "true"}
            onToggle={(next) =>
              handleAutoresearchSettingChange(
                "rag_autoresearch_auto_enabled",
                next,
              )
            }
            note={
              autoresearchSettings?.autoresearch_nightly_window || undefined
            }
            tooltip="Runs one research pass in the nightly window when the machine is idle."
          />
          <ActionButton
            kind="link"
            startIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}
            onClick={() => navigate("/autoresearch")}
          >
            Autoresearch
          </ActionButton>
          <ActionButton
            kind="link"
            startIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}
            onClick={() => navigate("/dev-tools")}
          >
            Retrieval health
          </ActionButton>
        </Line>
      </Cluster>
    </SettingsPanel>
  );

  const agentsPanel = (
    <SettingsPanel
      id="settings-agents"
      title="Agents"
      description="The mentor, what it remembers, where it can see."
    >
      <UncleClaudeSection />
      <Cluster
        label="Memory"
        note="facts, preferences and lessons the agent has learned"
      >
        <Line>
          <StatusPill
            tone={memoryCount > 0 ? "ok" : "neutral"}
            label={memoryCount === null ? "counting" : `${memoryCount} active`}
          />
          <ActionButton onClick={() => navigate("/agents/memory")}>
            Manage memory
          </ActionButton>
        </Line>
      </Cluster>
      <Cluster label="Display" note="the virtual screen agents act on">
        <AgentDisplaySection showMessage={showMessage} />
      </Cluster>
    </SettingsPanel>
  );

  const syncPanel = (
    <SettingsPanel
      id="settings-sync"
      title="Sync"
      description="Other Guaardvark machines on your network."
    >
      <Line>
        <SettingChip
          label="Interconnector"
          on={interconnectorEnabled}
          onToggle={handleInterconnectorToggle}
          onSettings={() => setInterconnectorModalOpen(true)}
          tooltip="Sync rules, memories and code with the other machines you have connected."
        />
        {interconnectorEnabled && interconnectorPendingCount > 0 && (
          <StatusPill
            tone="warn"
            label={`${interconnectorPendingCount} pending`}
            tooltip="Changes waiting for review"
          />
        )}
        {interconnectorEnabled &&
          interconnectorIsClient &&
          interconnectorUpdateStatus?.summary?.total > 0 && (
            <>
              <StatusPill
                tone="warn"
                label={`${interconnectorUpdateStatus.summary.total} code updates`}
                tooltip={`${interconnectorUpdateStatus.summary.backend || 0} backend · ${interconnectorUpdateStatus.summary.frontend || 0} frontend · ${interconnectorUpdateStatus.summary.other || 0} other`}
              />
              <ActionButton
                onClick={handleApplyInterconnectorUpdates}
                loading={interconnectorApplying}
                tooltip="Pulls every pending file from the master. Existing files are backed up first; a frontend change needs a rebuild."
              >
                Apply updates
              </ActionButton>
            </>
          )}
      </Line>
    </SettingsPanel>
  );

  const dataPanel = (
    <SettingsPanel
      id="settings-data"
      title="Data"
      description="Backups, exports and imports."
    >
      <Cluster label="Backups">
        <Line>
          <ActionButton
            onClick={openCreateBackup}
            disabled={isProcessingBackup || isLoading}
          >
            Create
          </ActionButton>
          <ActionButton
            onClick={openRestoreBackup}
            disabled={isProcessingBackup || isLoading}
            tooltip="Overwrites live data with a backup. Confirmed inside."
          >
            Restore
          </ActionButton>
          <ActionButton
            onClick={openManageBackups}
            disabled={isProcessingBackup || isLoading}
          >
            Manage
          </ActionButton>
          <Sep />
          <ExportChatsButton showMessage={showMessage} disabled={isLoading} />
        </Line>
      </Cluster>
      <Cluster label="Rules">
        <Line>
          <ActionButton
            onClick={handleExportRulesClick}
            loading={isExporting}
            disabled={isLoading}
          >
            Export rules
          </ActionButton>
          <input
            type="file"
            accept=".json"
            onChange={handleFileSelectForImport}
            style={{ display: "none" }}
            id="import-rules-input"
          />
          <label htmlFor="import-rules-input">
            <ActionButton component="span" disabled={isImporting}>
              Choose file
            </ActionButton>
          </label>
          {selectedFileNameForImport && (
            <Hint>{selectedFileNameForImport}</Hint>
          )}
          <ActionButton
            kind={selectedFileForImport ? "primary" : "neutral"}
            onClick={handleImportRulesClick}
            loading={isImporting}
            disabled={!selectedFileForImport || isLoading}
            tooltip="Creates new rules and updates existing ones with the same name"
          >
            Import
          </ActionButton>
        </Line>
      </Cluster>
    </SettingsPanel>
  );

  const dangerPanel = (
    <SettingsPanel
      id="settings-danger"
      title="Danger zone"
      description="Each asks first and says what it removes."
      danger
    >
      <Line>
        <ActionButton
          kind="destructive"
          onClick={openClearChatDialog}
          disabled={isLoading}
        >
          Clear chat history
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={handleDeleteGenerationHistoryClick}
          disabled={isLoading}
        >
          Delete generation history
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={() => setClearMemoriesOpen(true)}
          disabled={isLoading || !memoryCount}
        >
          Clear agent memory
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={() => setRebuildDialogOpen(true)}
          disabled={isLoading}
        >
          Rebuild index
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={() => setLearningClear("rules")}
          disabled={isLoading}
          tooltip="Deletes every rule the chat learned on its own; rules you wrote stay"
        >
          Clear learned rules
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={() => setLearningClear("log")}
          disabled={isLoading}
          tooltip="Empties the behaviour log that Behaviour learning reads from"
        >
          Clear behaviour log
        </ActionButton>
        <Sep />
        <ActionButton
          kind="destructive"
          onClick={handleRebootClick}
          disabled={isLoading || rebootInProgress}
        >
          Reboot
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={() => setKillSwitchOpen(true)}
          disabled={isLoading}
        >
          Kill switch
        </ActionButton>
      </Line>
    </SettingsPanel>
  );

  const developerPanel = (
    <SettingsPanel
      id="settings-developer"
      title="Developer"
      description="Logging and diagnostics."
    >
      <Line>
        <SettingChip
          label="Verbose logging"
          on={advancedDebug}
          onToggle={(next) => handleAdvancedDebugToggle(next)}
          tooltip="Adds a debug log sink live; no restart."
        />
        <SettingChip
          label="LLM debug"
          on={llmDebug}
          onToggle={(next) => handleLlmDebugToggle(next)}
          tooltip="Logs every prompt and completion."
        />
        <Sep />
        <ActionButton
          onClick={handleClearPycacheFoldersClick}
          disabled={isLoading}
          tooltip="Deletes Python bytecode caches. No data is touched."
        >
          Clear cache
        </ActionButton>
        <ActionButton
          kind="link"
          startIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}
          onClick={() => navigate("/dev-tools")}
        >
          Diagnostics and tests
        </ActionButton>
      </Line>
    </SettingsPanel>
  );

  const aboutPanel = (
    <SettingsPanel
      id="settings-about"
      title="About"
      description={appVersion ? `Guaardvark v${appVersion}` : "Guaardvark"}
    >
      <Cluster
        label="Support the project"
        note="built with love by a solo developer"
      >
        <Line>
          <ActionButton
            startIcon={<StarIcon sx={{ fontSize: 14 }} />}
            onClick={() =>
              window.open(
                SUPPORT_LINKS.githubRepo,
                "_blank",
                "noopener,noreferrer",
              )
            }
          >
            GitHub
          </ActionButton>
          <ActionButton
            onClick={() =>
              window.open(
                SUPPORT_LINKS.githubSponsors,
                "_blank",
                "noopener,noreferrer",
              )
            }
          >
            Sponsors
          </ActionButton>
          <ActionButton
            startIcon={<CoffeeIcon sx={{ fontSize: 14 }} />}
            onClick={() =>
              window.open(
                SUPPORT_LINKS.buyMeACoffee,
                "_blank",
                "noopener,noreferrer",
              )
            }
          >
            Buy me a coffee
          </ActionButton>
          <ActionButton
            onClick={() =>
              window.open(SUPPORT_LINKS.koFi, "_blank", "noopener,noreferrer")
            }
          >
            Ko-fi
          </ActionButton>
          <ActionButton
            onClick={() =>
              window.open(SUPPORT_LINKS.paypal, "_blank", "noopener,noreferrer")
            }
          >
            PayPal
          </ActionButton>
          <ActionButton
            onClick={() =>
              window.open(SUPPORT_LINKS.venmo, "_blank", "noopener,noreferrer")
            }
          >
            Venmo
          </ActionButton>
          <ActionButton
            onClick={() =>
              window.open(
                SUPPORT_LINKS.cashApp,
                "_blank",
                "noopener,noreferrer",
              )
            }
          >
            Cash App
          </ActionButton>
        </Line>
      </Cluster>
    </SettingsPanel>
  );

  const columnSets =
    columns === 3
      ? [
          [generalPanel, chatPanel, dataPanel, aboutPanel],
          [modelsPanel, knowledgePanel, dangerPanel],
          [generationPanel, agentsPanel, syncPanel, developerPanel],
        ]
      : columns === 2
        ? [
            [
              generalPanel,
              chatPanel,
              generationPanel,
              agentsPanel,
              developerPanel,
            ],
            [
              modelsPanel,
              knowledgePanel,
              syncPanel,
              dataPanel,
              dangerPanel,
              aboutPanel,
            ],
          ]
        : [
            [
              generalPanel,
              modelsPanel,
              chatPanel,
              generationPanel,
              knowledgePanel,
              agentsPanel,
              syncPanel,
              dataPanel,
              dangerPanel,
              developerPanel,
              aboutPanel,
            ],
          ];

  const deleteHistoryFacts = deleteHistoryCounts
    ? [
        {
          label: "Images",
          value: `${deleteHistoryCounts.images?.batches || 0} batches · ${deleteHistoryCounts.images?.files || 0} files · ${formatByteSize(deleteHistoryCounts.images?.bytes)}`,
        },
        {
          label: "Videos",
          value: `${deleteHistoryCounts.videos?.batches || 0} batches · ${deleteHistoryCounts.videos?.files || 0} files · ${formatByteSize(deleteHistoryCounts.videos?.bytes)}`,
        },
        {
          label: "Audio",
          value: `${deleteHistoryCounts.audio?.files || 0} files · ${deleteHistoryCounts.audio?.jobs || 0} job records · ${formatByteSize(deleteHistoryCounts.audio?.bytes)}`,
        },
        {
          label: "ComfyUI scratch",
          value: `${deleteHistoryCounts.comfyui?.output?.files || 0} output · ${deleteHistoryCounts.comfyui?.input?.files || 0} input files · ${formatByteSize(deleteHistoryCounts.comfyui?.bytes)}`,
        },
        {
          label: "Database",
          value: `${deleteHistoryCounts.db?.documents || 0} documents · ${deleteHistoryCounts.db?.folders || 0} folders · ${deleteHistoryCounts.db?.job_history || 0} job history rows`,
        },
      ]
    : [];
  const deleteHistoryRunning =
    deleteHistoryCounts?.images?.running?.length ||
    deleteHistoryCounts?.videos?.running?.length ||
    deleteHistoryCounts?.audio?.running?.length;

  return (
    <PageLayout
      title="Settings"
      variant="standard"
      actions={
        appVersion ? (
          <Typography variant="caption" color="text.disabled">
            v{appVersion}
          </Typography>
        ) : null
      }
    >
      <Box sx={{ display: "flex", flexDirection: "column", gap: 1.75, pb: 4 }}>
        {strip}
        <Box sx={{ display: "flex", gap: 1.75, alignItems: "flex-start" }}>
          {columnSets.map((set, i) => (
            <Box
              key={i}
              sx={{
                flex: 1,
                minWidth: 0,
                display: "flex",
                flexDirection: "column",
                gap: 1.75,
              }}
            >
              {set.map((panel, j) => (
                <React.Fragment key={j}>{panel}</React.Fragment>
              ))}
            </Box>
          ))}
        </Box>
      </Box>

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
      <InterconnectorSettingsModal
        open={interconnectorModalOpen}
        onClose={() => {
          setInterconnectorModalOpen(false);
          interconnectorApi
            .getInterconnectorConfig()
            .then((res) => {
              if (res?.data?.config?.is_enabled || res?.config?.is_enabled)
                setInterconnectorEnabled(true);
              else if (!res?.error) setInterconnectorEnabled(false);
            })
            .catch(() => {});
        }}
      />
      <ConfirmActionDialog
        open={rebootDialogOpen}
        onClose={handleCancelReboot}
        onConfirm={handleConfirmReboot}
        title="Reboot Guaardvark"
        description="Restarts the backend, the workers and ComfyUI. Every generation, index job and chat reply in flight is lost, and this page will disconnect until the services are back."
        keeps="documents, chats, media, rules and settings."
        confirmLabel="Reboot now"
        busy={rebootInProgress}
      />
      <ConfirmActionDialog
        open={deleteHistoryDialogOpen}
        onClose={handleCancelDeleteHistory}
        onConfirm={handleConfirmDeleteHistory}
        title="Delete generation history"
        description={
          <>
            Permanently deletes every batch image, batch video and audio
            generation: the files, their entries in Documents and Job History,
            and ComfyUI&apos;s own output and input folders.
            {deleteHistoryRunning
              ? " Batches still generating are skipped and left in place."
              : ""}
            {deleteHistoryCounts?.comfyui?.running?.length
              ? " ComfyUI is still rendering; its folders are left alone this time."
              : ""}
            {!deleteHistoryCounts
              ? " Could not read the current counts; deletion still works."
              : ""}
          </>
        }
        facts={deleteHistoryFacts}
        keeps="Film Crew productions, video editor projects, the cast library, trained LoRAs and chat history."
        confirmLabel="Delete history"
        busy={deleteHistoryInProgress}
      />
      <ConfirmActionDialog
        open={clearChatOpen}
        onClose={() => !clearChatBusy && setClearChatOpen(false)}
        onConfirm={confirmClearChat}
        title="Clear all chat history"
        description="Deletes every conversation and message, the cached context files, and this browser's session ids."
        facts={
          chatHistoryCounts
            ? [
                { label: "Messages", value: chatHistoryCounts.messages || 0 },
                { label: "Sessions", value: chatHistoryCounts.sessions || 0 },
                {
                  label: "Cached files",
                  value:
                    (chatHistoryCounts.context_files || 0) +
                    (chatHistoryCounts.conversation_files || 0),
                },
              ]
            : []
        }
        keeps="documents, the index, generated media and rules."
        confirmLabel="Clear chat history"
        busy={clearChatBusy}
      />
      <ConfirmActionDialog
        open={clearMemoriesOpen}
        onClose={() => !clearMemoriesBusy && setClearMemoriesOpen(false)}
        onConfirm={confirmClearMemories}
        title="Clear agent memory"
        description="Deletes every memory the agent has stored: facts, preferences and lessons, whatever their status. The filters on the memory page do not narrow this."
        facts={
          memoryCount !== null
            ? [{ label: "Active memories", value: memoryCount }]
            : []
        }
        keeps="rules, chats and documents."
        confirmLabel="Clear memory"
        busy={clearMemoriesBusy}
      />
      <ConfirmActionDialog
        open={learningClear !== null}
        onClose={() => !learningBusy && setLearningClear(null)}
        onConfirm={confirmClearLearning}
        title={
          learningClear === "rules"
            ? "Clear learned rules"
            : "Clear behaviour log"
        }
        description={
          learningClear === "rules"
            ? "Deletes every rule marked as learned, the ones the chat added on its own from corrections and preferences."
            : "Empties the behaviour log file that Behaviour learning reads to adapt replies. Learning starts again from nothing."
        }
        keeps={
          learningClear === "rules"
            ? "rules you wrote or imported, chats, memories."
            : "learned rules, chats, memories."
        }
        confirmLabel={
          learningClear === "rules" ? "Clear learned rules" : "Clear log"
        }
        busy={learningBusy}
      />
      <IndexProfileEditDialog
        open={editProfile !== null}
        profile={editProfile}
        onClose={() => setEditProfile(null)}
        onSaved={(message) => {
          showMessage(message, "success");
          setProfilesReloadKey((k) => k + 1);
        }}
      />
      <RebuildIndexDialog
        open={rebuildDialogOpen}
        profiles={indexProfiles}
        onClose={() => setRebuildDialogOpen(false)}
        onOpenPurge={handleOpenPurgeModal}
        onDone={(message, severity) => {
          showMessage(message, severity);
          setProfilesReloadKey((k) => k + 1);
        }}
      />
      <PurgeIndexModal
        open={purgeModalOpen}
        onClose={handleClosePurgeModal}
        onConfirm={handleConfirmPurge}
        isProcessing={isPurging}
      />
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
            .then((res) => res.json())
            .then((data) => data.success && setImageGenStatus(data.data))
            .catch(console.error);
        }}
        showMessage={showMessage}
      />
      <InfographicModelsModal
        open={infographicModelsModalOpen}
        onClose={() => setInfographicModelsModalOpen(false)}
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
