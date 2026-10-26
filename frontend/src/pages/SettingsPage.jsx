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
import InboundGuardSection from "../components/settings/InboundGuardSection";
import AgentDisplaySection from "../components/settings/AgentDisplaySection";
import KillSwitchModal from "../components/modals/KillSwitchModal";
import RebootProgressModal from "../components/modals/RebootProgressModal";
import ImageModelsModal from "../components/modals/ImageModelsModal";
import InfographicModelsModal from "../components/modals/InfographicModelsModal";
import VideoModelsModal from "../components/modals/VideoModelsModal";
import VoiceModelsModal from "../components/modals/VoiceModelsModal";
import AudioFoundryModelsModal from "../components/modals/AudioFoundryModelsModal";
import TrainingLibrariesModal from "../components/modals/TrainingLibrariesModal";
import InterconnectorSettingsModal from "../components/modals/InterconnectorSettingsModal";
import VoiceSettingsModal from "../components/modals/VoiceSettingsModal";
import ExportChatsButton from "../components/settings/ExportChatsButton";
import ProfileSection from "../components/settings/ProfileSection";
import TipsSetting from "../components/settings/TipsSetting";
import LanguageSetting from "../components/settings/LanguageSetting";
import JobOrderSetting from "../components/settings/JobOrderSetting";
import { SOCKET_URL, handleResponse } from "../api/apiClient";
import { SUPPORT_LINKS } from "../config/constants";
import CoffeeIcon from "@mui/icons-material/Coffee";
import StarIcon from "@mui/icons-material/Star";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import { useLocation, useNavigate } from "react-router-dom";
import ApiKeySection, { API_KEY_SECTION_ID } from "../components/settings/ApiKeySection";
import { onSessionChanged } from "../api/apiAuth";
import {
  getBranding,
  updateBranding,
  getRagFeatures,
  updateRagFeatures,
  clearBehaviorLog,
  getMusicDirectory,
  setMusicDirectory as setMusicDirectoryAPI,
  getChatKeepReady,
  setChatKeepReady,
} from "../api/settingsService";
import { enablePlugin, startPlugin } from "../api/pluginsService";
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
import RerankerControl from "../components/settings/RerankerControl";
import IdentityCheckControl from "../components/settings/IdentityCheckControl";
import RebuildIndexDialog from "../components/settings/RebuildIndexDialog";
import IndexProfileEditDialog from "../components/settings/IndexProfileEditDialog";
import RefreshIcon from "@mui/icons-material/Refresh";
import { useTheme, useMediaQuery } from "@mui/material";
import { useSnackbar } from "../components/common/SnackbarProvider";
import { formatUiError } from "../utils/uiError";
import * as interconnectorApi from "../api/interconnectorService";
import { useVoice } from "../contexts/VoiceContext";
import * as apiService from "../api";
import voiceService from "../api/voiceService";
import {
  VOICE_SETTINGS_EVENT,
  normalizeVoiceSettings,
  readVoiceSettings,
} from "../config/voiceDefaults";
import { ragAutoresearchService } from "../api/ragAutoresearchService";
import { getMcpStatus } from "../api/mcpService";
import { NAV_CHROME } from "../config/navCatalog";
import copyText from "../utils/copyText";
import { uiLocale, useUiTranslation } from "../i18n";

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
  const { t } = useUiTranslation();
  const [availableModels, setAvailableModels] = useState([]);
  // Ollama did not answer the last model list; the list then holds only the
  // saved model.
  const [ollamaOffline, setOllamaOffline] = useState(false);
  const [startingOllama, setStartingOllama] = useState(false);
  const [keepReady, setKeepReady] = useState(null);
  const [savingKeepReady, setSavingKeepReady] = useState(false);
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
  const location = useLocation();
  const [enhancedContext, setEnhancedContext] = useState(false);
  const [advancedRag, setAdvancedRag] = useState(false);
  const [advancedDebug, setAdvancedDebug] = useState(getInitialAdvancedDebug);
  const [llmDebug, setLlmDebugState] = useState(getInitialLlmDebug);
  const [verbatimPrompts, setVerbatimPromptsState] = useState(false);
  const [verbatimSaving, setVerbatimSaving] = useState(false);
  // VERBATIM_PROMPTS in the server environment overrides the toggle; when set
  // the chip shows on and cannot be changed here.
  const [verbatimForcedByEnv, setVerbatimForcedByEnv] = useState(false);
  // Minutes the image model stays loaded after a batch; 0 unloads it at once.
  const [imageKeepMinutes, setImageKeepMinutes] = useState(0);
  const [imageKeepSaving, setImageKeepSaving] = useState(false);
  // Media stack (stills / cast LoRA train / max quality) — Ollama-picker style
  // stills_model holds the person's choice, "auto" when nothing was chosen
  // (the backend's stills_chosen); the picker never shows the filled-in
  // default as if someone had picked it.
  const [mediaModels, setMediaModelsState] = useState({
    stills_model: "auto",
    cast_train_base: "zimage-turbo",
    max_quality_model: "flux-dev",
    train_profiles: [],
    stills_profiles: [],
  });
  const lastSavedLoraRef = useRef({});
  const [loraSaveState, setLoraSaveState] = useState({
    zimage: "saved",
    sdxl: "saved",
    flux: "saved",
  });
  const [behaviorLearningEnabled, setBehaviorLearningEnabled] = useState(
    getInitialBehaviorLearning,
  );
  const [rulesEnabled, setRulesEnabledState] = useState(getInitialRulesEnabled);
  // Global default for chain-of-thought on thinking models (gemma4:12b, qwen3).
  // Off = faster chat; per-chat /thinking on|off overrides. Backend authoritative.
  const [chatThinkingDefault, setChatThinkingDefaultState] = useState(false);
  const [planModeDefault, setPlanModeDefaultState] = useState(false);
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
          t("settings.interconnector.confirmApply"),
        )
      ) {
        return;
      }
      setInterconnectorApplying(true);
      try {
        const response = await interconnectorApi.applyUpdates([]);
        if (response?.error) {
          showMessage?.(t("settings.interconnector.updateFailed", { error: response.error }), "error");
        } else {
          const data = response?.data || response || {};
          const counts = { applied: data.applied || 0, created: data.created || 0, updated: data.updated || 0 };
          showMessage?.(
            data.restart_required ? t("settings.interconnector.updatedRestart", counts) : t("settings.interconnector.updated", counts),
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
        showMessage?.(t("settings.interconnector.updateFailed", { error: err.message }), "error");
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
  const [audioModelsModalOpen, setAudioModelsModalOpen] = useState(false);
  const [trainingLibrariesModalOpen, setTrainingLibrariesModalOpen] = useState(false);
  const [imageGenStatus, setImageGenStatus] = useState(null);
  // /api/batch-image/status reports service_available (the batch image service
  // loaded) and image_generator_available (its image pipeline loaded); usable
  // means both. Batch images run on the backend's own pipeline, not ComfyUI.
  const imageGenAvailable = Boolean(
    imageGenStatus?.service_available && imageGenStatus?.image_generator_available,
  );
  const imageGenUnavailableHint =
    imageGenStatus?.error || t("settings.generation.imageServiceNotLoaded");

  // Resource monitor and embedding model state
  const [gpuResources, setGpuResources] = useState(null);
  const [embeddingModels, setEmbeddingModels] = useState([]);
  const [selectedEmbeddingModel, setSelectedEmbeddingModel] = useState("");
  const [isSwitchingEmbedding, setIsSwitchingEmbedding] = useState(false);
  const [confirmEmbeddingOpen, setConfirmEmbeddingOpen] = useState(false);
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
  const [mcpStatus, setMcpStatus] = useState(null);
  const [confineToolPaths, setConfineToolPaths] = useState(false);
  const [musicDirectorySaved, setMusicDirectorySaved] = useState("");
  // "rules" clears rules marked LEARNED; "log" empties the behaviour log file.
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

      return readVoiceSettings();
    } catch (error) {
      console.warn("Failed to load voice settings from localStorage:", error);
      return normalizeVoiceSettings({});
    }
  });

  // The mic popover and the Voice page write the same settings; take their
  // changes so this page's next save does not put the old values back.
  useEffect(() => {
    const takeExternalChange = () => {
      const next = readVoiceSettings();
      setVoiceSettings((prev) =>
        JSON.stringify(normalizeVoiceSettings(prev)) === JSON.stringify(next) ? prev : next
      );
    };
    window.addEventListener(VOICE_SETTINGS_EVENT, takeExternalChange);
    return () => window.removeEventListener(VOICE_SETTINGS_EVENT, takeExternalChange);
  }, []);

  const [availableVoices, setAvailableVoices] = useState([]);
  const [voiceStatus, setVoiceStatus] = useState(null);
  const [voiceError, setVoiceError] = useState(null);
  const [isVoiceLoading, setIsVoiceLoading] = useState(false);
  const [isVoiceTestPlaying, setIsVoiceTestPlaying] = useState(false);
  const [isInstallingVoice, setIsInstallingVoice] = useState(false);
  const [isInstallingWhisper, setIsInstallingWhisper] = useState(false);
  const [whisperManualInstall, setWhisperManualInstall] = useState(null);
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
      setVoiceError(t("settings.voice.loadFailed"));
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
          t("settings.voice.voiceNotInstalled", { name: voice.name }),
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
        showMessage(t("settings.voice.testFailed", { error: response.error }), "error");
        setIsVoiceTestPlaying(false);
      }
    } catch (error) {
      console.error("Voice test failed:", error);
      const errorMessage = error.message || t("settings.voice.testFailedPlain");
      if (
        errorMessage.includes("not found") ||
        errorMessage.includes("not installed")
      ) {
        showMessage(
          t("settings.voice.modelsNotInstalled"),
          "warning",
        );
      } else {
        showMessage(t("settings.voice.testFailed", { error: errorMessage }), "error");
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
      showMessage(t("settings.voice.voiceChanged", { name: voiceName }), "success");
    } else if (setting === "ttsEnabled") {
      if (voiceContext?.setTtsEnabled) {
        voiceContext.setTtsEnabled(value);
      }
      showMessage(
        value ? t("settings.voice.ttsEnabled") : t("settings.voice.ttsDisabled"),
        "success",
      );
    } else if (setting === "micEnabled") {
      showMessage(value ? t("settings.voice.micEnabled") : t("settings.voice.micDisabled"), "success");
    }
  };

  const installDefaultVoiceModel = async () => {
    setIsInstallingVoice(true);
    try {
      showMessage(
        t("settings.voice.installingLibriTts"),
        "info",
      );
      const result = await voiceService.installVoiceModel("libritts");

      if (result.success) {
        if (result.already_installed) {
          showMessage(t("settings.voice.libriTtsInstalledAlready"), "info");
        } else {
          showMessage(
            t("settings.voice.libriTtsInstalled", { size: result.model_size_mb }),
            "success",
          );
        }
        // Reload voice configuration to update the UI
        await loadVoiceConfiguration();
      } else {
        showMessage(t("settings.voice.installFailed", { error: result.error }), "error");
      }
    } catch (error) {
      console.error("Failed to install voice model:", error);
      showMessage(t("settings.voice.installFailed", { error: error.message }), "error");
    } finally {
      setIsInstallingVoice(false);
    }
  };

  const installWhisperCpp = async () => {
    setIsInstallingWhisper(true);
    setWhisperManualInstall(null);
    try {
      showMessage(
        voiceStatus?.install_method === "pkexec"
          ? t("settings.voice.whisperApprovePassword")
          : t("settings.voice.whisperInstalling"),
        "info",
      );
      const result = await voiceService.installWhisper();

      if (result.success) {
        if (result.already_installed) {
          showMessage(t("settings.voice.whisperInstalledAlready"), "info");
        } else {
          showMessage(
            t("settings.voice.whisperInstalled"),
            "success",
          );
        }
        await loadVoiceConfiguration();
      } else if (result.needs_manual_install && result.manual_command) {
        setWhisperManualInstall({
          command: result.manual_command,
          reason: result.error,
        });
      } else {
        showMessage(t("settings.voice.whisperFailed", { error: result.error }), "error");
      }
    } catch (error) {
      console.error("Failed to install Whisper.cpp:", error);
      const data = error.data || {};
      if (data.needs_manual_install && data.manual_command) {
        setWhisperManualInstall({
          command: data.manual_command,
          reason: data.error || error.message,
        });
      } else {
        showMessage(t("settings.voice.whisperFailed", { error: error.message }), "error");
      }
    } finally {
      setIsInstallingWhisper(false);
    }
  };

  const copyWhisperCommand = async (command) => {
    // copyText falls back to execCommand: navigator.clipboard is absent over plain http on the LAN.
    if (await copyText(command)) {
      showMessage(t("settings.voice.commandCopied"), "success");
    } else {
      showMessage(t("settings.voice.copyFailed"), "warning");
    }
  };

  // Starts the speech model download, then opens the Voice models screen,
  // which shows its progress.
  const installWhisperSpeechModel = async () => {
    setIsInstallingWhisper(true);
    try {
      await voiceService.downloadVoiceModel(
        "whisper",
        voiceStatus?.speech_model_id || "tiny.en",
      );
    } catch (error) {
      // 409 means a voice model download is already running: show it.
      if (error.status !== 409) {
        console.error("Failed to start the speech model install:", error);
        showMessage(
          t("settings.voice.speechModelFailed", { error: error.message }),
          "error",
        );
        return;
      }
    } finally {
      setIsInstallingWhisper(false);
    }
    setVoiceModelsModalOpen(true);
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
        setModelSwitchMessage(data.message || t("settings.models.loading", { model: data.model }));
        showMessage(data.message || t("settings.models.loading", { model: data.model }), "info");
      } else if (data.status === "complete") {
        setModelSwitchStatus("complete");
        setModelSwitchMessage(
          data.message || t("settings.models.switchedTo", { model: data.model }),
        );
        showMessage(
          data.message || t("settings.models.switchedSuccess", { model: data.model }),
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
        setModelSwitchMessage(data.message || t("settings.models.switchFailed"));
        showMessage(data.message || t("settings.models.switchFailed"), "error");
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
      const modelsResult = await apiService.getChatModelList();
      if (modelsResult?.error)
        throw new Error(t("settings.models.listFetchFailed", { error: modelsResult.error }));
      setOllamaOffline(modelsResult.ollamaOffline);
      let modelsList = modelsResult.models.filter((m) => m && m.name);
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
      showMessage(t("settings.models.listLoadFailed", { error: err.message }), "error");
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

  const handleStartOllama = async () => {
    setStartingOllama(true);
    try {
      let result;
      try {
        result = await startPlugin("ollama");
      } catch (err) {
        // A switched-off plugin refuses to start; the button means "on".
        if (!/disabled/i.test(err.message || "")) throw err;
        await enablePlugin("ollama");
        result = await startPlugin("ollama");
      }
      const data = result?.data ?? result;
      if (data?.gated) {
        throw new Error(
          t("settings.models.ollamaCooldown", { seconds: Math.ceil(data.cooldown_remaining || 0) }),
        );
      }
      showMessage(t("settings.models.ollamaRunning"), "success");
      await fetchAvailableModels();
    } catch (err) {
      showMessage(t("settings.models.ollamaStartFailed", { error: err.message }), "error");
    } finally {
      setStartingOllama(false);
    }
  };

  const fetchKeepReady = useCallback(async () => {
    try {
      const result = await getChatKeepReady();
      setKeepReady(result?.data ?? result);
    } catch {
      // Older backend without Keep ready: the chip stays hidden.
    }
  }, []);

  useEffect(() => {
    fetchKeepReady();
    const id = setInterval(() => {
      if (!document.hidden) fetchKeepReady();
    }, 10000);
    return () => clearInterval(id);
  }, [fetchKeepReady]);

  const handleKeepReadyToggle = async (next) => {
    setSavingKeepReady(true);
    try {
      const result = await setChatKeepReady(next);
      setKeepReady(result?.data ?? result);
    } catch (err) {
      showMessage(t("settings.models.keepReadyFailed", { error: err.message }), "error");
    } finally {
      setSavingKeepReady(false);
    }
  };

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
        t("settings.knowledge.autoresearchSaveFailed"),
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
            error: t("settings.generation.imageStatusFailed"),
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
    apiService
      .getImageKeepLoaded()
      .then((result) => {
        const minutes = (result?.data ?? result)?.minutes;
        if (typeof minutes === "number") setImageKeepMinutes(minutes);
      })
      .catch(() => {});
  }, []);

  const handleImageKeepChange = async (value) => {
    const minutes = Number(value) || 0;
    setImageKeepSaving(true);
    try {
      const result = await apiService.setImageKeepLoaded(minutes);
      if (result?.error) throw new Error(result.error.message || result.error);
      const saved = (result?.data ?? result)?.minutes;
      if (typeof saved !== "number") throw new Error(t("settings.generation.keepNotConfirmed"));
      setImageKeepMinutes(saved);
      showMessage(
        saved
          ? t("settings.generation.keepMinutes", { minutes: saved })
          : t("settings.generation.unloadEachBatch"),
        "info",
      );
    } catch (err) {
      showMessage(t("settings.generation.saveFailed", { error: err.message }), "error");
    } finally {
      setImageKeepSaving(false);
    }
  };

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
          if (data.character_lora_strength && typeof data.character_lora_strength === "object") {
            lastSavedLoraRef.current = { ...data.character_lora_strength };
            setLoraSaveState({ zimage: "saved", sdxl: "saved", flux: "saved" });
          }
          setMediaModelsState((prev) => ({
            ...prev,
            stills_model: data.stills_chosen || "auto",
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
    apiService.getPlanModeDefault().then((result) => {
      const enabled = result?.data?.plan_mode_default ?? result?.plan_mode_default;
      if (typeof enabled === "boolean") setPlanModeDefaultState(enabled);
    });
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
    showMessage(loadingMessage || t("settings.actions.processing"), "info");
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
        t("settings.actions.done");
      const severity = result?.warning ? "warning" : "success";

      showMessage(message, severity);

      if (actionFunction === apiService.setModel) {
        refreshActiveModel();
      }
    } catch (err) {
      if (err.message !== "User aborted") {
        // Don't show error if user cancelled file dialog
        showMessage(t("settings.actions.failed", { action: failureMessagePrefix, error: err.message }), "error");
      }
    } finally {
      if (actionFunction !== apiService.triggerReboot) {
        setIsLoading(false);
      }
    }
  };

  const handleSetModelClick = async () => {
    if (!selectedModel) {
      showMessage(t("settings.models.selectFirst"), "warning");
      return;
    }

    // Model switching is now async - the backend returns 202 immediately
    // and sends socket events for progress updates
    setModelSwitchStatus("loading");
    setModelSwitchMessage(t("settings.models.switching", { model: selectedModel }));

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
      setModelSwitchMessage(err.message || t("settings.models.switchFailed"));
      showMessage(err.message || t("settings.models.switchFailed"), "error");
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
      t("settings.index.purging"),
      t("settings.index.purged"),
      t("settings.index.purgeFailed"),
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
    if (n >= 1024 ** 3) return t("settingsSections.units.gb", { size: (n / 1024 ** 3).toFixed(2) });
    if (n >= 1024 ** 2) return t("settingsSections.units.mb", { size: (n / 1024 ** 2).toFixed(1) });
    if (n >= 1024) return t("settingsSections.units.kb", { size: Math.round(n / 1024) });
    return t("settingsSections.units.b", { size: n });
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
    showMessage(t("settings.history.deleting"), "info");
    try {
      const result = await apiService.deleteGenerationHistory();
      const d = result?.deleted || {};
      const parts = [];
      if (d.images?.batches) parts.push(t("settings.history.imageBatches", { count: d.images.batches }));
      if (d.videos?.batches) parts.push(t("settings.history.videoBatches", { count: d.videos.batches }));
      if (d.audio?.files || d.audio?.jobs)
        parts.push(
          t("settings.history.audio", { files: d.audio.files || 0, jobs: d.audio.jobs || 0 }),
        );
      const comfyFiles =
        (d.comfyui?.output?.files || 0) + (d.comfyui?.input?.files || 0);
      if (comfyFiles) parts.push(t("settings.history.comfyFiles", { count: comfyFiles }));
      const dbRows =
        (d.documents || 0) + (d.folders || 0) + (d.job_history || 0);
      if (dbRows) parts.push(t("settings.history.dbRows", { count: dbRows }));
      let message = parts.length
        ? t("settings.history.deleted", { items: parts.join(", "), size: formatByteSize(result?.bytes_freed) })
        : t("settings.history.nothing");
      const skipped = result?.skipped || {};
      const skippedCount =
        (skipped.images?.length || 0) +
        (skipped.videos?.length || 0) +
        (skipped.audio?.length || 0);
      if (skippedCount) message += ` ${t("settings.history.skipped", { count: skippedCount })}`;
      if (skipped.comfyui?.length)
        message += ` ${t("settings.history.comfyRendering", { count: skipped.comfyui.length })}`;
      if (result?.sidecar_available === false)
        message +=
          ` ${t("settings.history.audioNotRunning")}`;
      if (result?.errors?.length)
        message += ` ${t("settings.history.errors", { count: result.errors.length, error: result.errors[0] })}`;
      showMessage(
        message,
        skippedCount || result?.errors?.length ? "warning" : "success",
      );
      setDeleteHistoryDialogOpen(false);
    } catch (err) {
      showMessage(
        t("settings.history.failed", { error: err.message }),
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
        t("settings.pycache.confirm"),
      )
    ) {
      return;
    }

    setIsLoading(true);
    showMessage(t("settings.pycache.clearing"), "info");
    try {
      const result = await apiService.clearPycache();
      if (result?.error && !result.warning && result.error !== "User aborted") {
        throw new Error(result.error.message || result.error);
      }

      // Build detailed success message with statistics
      let message =
        result?.message || t("settings.pycache.cleared");

      if (result?.statistics) {
        const stats = result.statistics;
        const details = [];

        if (stats.directories_cleaned > 0) {
          details.push(t("settings.pycache.directories", { count: stats.directories_cleaned }));
        }

        if (stats.pyc_files_deleted > 0) {
          details.push(t("settings.pycache.pycFiles", { count: stats.pyc_files_deleted }));
        }

        if (stats.size_formatted) {
          details.push(t("settings.pycache.freed", { size: stats.size_formatted }));
        }

        if (result?.modules_purged_count > 0) {
          details.push(
            t("settings.pycache.modules", { count: result.modules_purged_count }),
          );
        }

        if (details.length > 0) {
          message = t("settings.pycache.summary", { details: details.join(", ") });
        }

        if (result?.locations_cleaned && result.locations_cleaned.length > 0) {
          const locationCount = result.locations_cleaned.length;
          if (locationCount <= 5) {
            message += ` ${t("settings.pycache.locations", { locations: result.locations_cleaned.join(", ") })}`;
          } else {
            message += ` ${t("settings.pycache.locationCount", { count: locationCount })}`;
          }
        }
      }

      if (result?.errors && result.errors.length > 0) {
        message += ` ${t("settings.pycache.errorsNote", { count: result.errors.length })}`;
      }

      const severity = result?.warning ? "warning" : "success";
      showMessage(message, severity);
    } catch (err) {
      if (err.message !== "User aborted") {
        showMessage(
          t("settings.pycache.failed", { error: err.message }),
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
        isEnabled ? t("settings.rag.enhancedOn") : t("settings.rag.enhancedOff"),
        "success",
      );
    } catch (err) {
      console.error("Failed to update Enhanced Context setting:", err);
      showMessage(
        isEnabled
          ? t("settings.rag.enhancedEnableFailed", { error: err.message })
          : t("settings.rag.enhancedDisableFailed", { error: err.message }),
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
        isEnabled ? t("settings.rag.advancedOn") : t("settings.rag.advancedOff"),
        "success",
      );
    } catch (err) {
      console.error("Failed to update Advanced RAG setting:", err);
      showMessage(
        isEnabled
          ? t("settings.rag.advancedEnableFailed", { error: err.message })
          : t("settings.rag.advancedDisableFailed", { error: err.message }),
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
      // setWebAccess reports a refused save as {error}, not a throw; read it,
      // or the switch shows a state the server does not have.
      const result = await apiService.setWebAccess(isEnabled);
      if (result?.error) throw new Error(result.error);
    } catch (err) {
      console.warn("Failed to update web access setting:", err);
      setWebSearchEnabled(previous);
      try {
        localStorage.setItem(WEB_SEARCH_ENABLED_KEY, String(previous));
      } catch (e) {
        console.warn("Failed to restore web search setting:", e);
      }
      showMessage(
        err?.message?.trim()
          ? t("settings.web.saveFailedWithError", { error: err.message.trim() })
          : t("settings.web.saveFailed"),
        "error",
      );
      return;
    }
    debugLog("Web Search toggled", { isEnabled });
    showMessage(
      isEnabled
        ? t("settings.web.enabled")
        : t("settings.web.disabled"),
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
        t("settings.toggles.saveFailed", { error: err.message }),
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
      onText: t("settings.toggles.verboseOn"),
      offText: t("settings.toggles.verboseOff"),
    });
  const handleVerbatimPromptsToggle = async (event) => {
    if (verbatimSaving) return;
    if (verbatimForcedByEnv) {
      showMessage(
        t("settings.toggles.verbatimForced"),
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
        throw new Error(t("settings.toggles.verbatimNotConfirmed"));
      }
      setVerbatimPromptsState(payload.enabled);
      setVerbatimForcedByEnv(Boolean(payload.forced_by_env));
      showMessage(
        payload.enabled
          ? t("settings.toggles.verbatimOn")
          : t("settings.toggles.verbatimOff"),
        "info",
      );
    } catch (err) {
      showMessage(t("settings.toggles.verbatimFailed", { error: err.message }), "error");
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
      onText: t("settings.toggles.llmDebugOn"),
      offText: t("settings.toggles.llmDebugOff"),
    });
  const handleBehaviorLearningToggle = (event) =>
    persistToggle({
      next: deriveToggleValue(event, behaviorLearningEnabled),
      previous: behaviorLearningEnabled,
      setState: setBehaviorLearningEnabled,
      storageKey: BEHAVIOR_LEARNING_ENABLED_KEY,
      save: apiService.setBehaviorLearning,
      onText: t("settings.toggles.behaviourOn"),
      offText: t("settings.toggles.behaviourOff"),
    });

  // --- NEW HANDLERS FOR IMPORT/EXPORT ---
  const handleExportRulesClick = async () => {
    setIsExporting(true);
    showMessage(t("settings.rules.exporting"), "info");
    try {
      const result = await apiService.exportRules();
      if (result?.error) throw new Error(result.error);
      if (!result?.rules || !Array.isArray(result.rules))
        throw new Error(t("settings.rules.invalidExport"));

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
        t("settings.rules.exported", { count: result.rules.length }),
        "success",
      );
    } catch (err) {
      console.error("Error exporting rules:", err);
      showMessage(t("settings.rules.exportFailed", { error: err.message }), "error");
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
      showMessage(t("settings.rules.selectJson"), "warning");
      return;
    }
    setIsImporting(true);
    showMessage(t("settings.rules.importing", { name: selectedFileNameForImport }), "info");

    try {
      const fileContent = await selectedFileForImport.text();
      const jsonData = JSON.parse(fileContent);
      if (!jsonData || !Array.isArray(jsonData.rules)) {
        throw new Error(
          t("settings.rules.invalidJson"),
        );
      }

      const result = await apiService.importRules({ rules: jsonData.rules });

      if (result?.error && !result.created && !result.updated) {
        throw new Error(
          result.error.message || result.error.details || result.error,
        );
      }

      let importSummary = result.message || t("settings.rules.importCompleted");
      if (result.created) importSummary += ` ${t("settings.rules.created", { count: result.created })}`;
      if (result.updated) importSummary += ` ${t("settings.rules.updated", { count: result.updated })}`;
      if (result.skipped)
        importSummary += ` ${t("settings.rules.skipped", { count: result.skipped })}`;
      if (result.errors && result.errors.length > 0) {
        console.error("Import errors:", result.errors);
        importSummary += ` ${t("settings.rules.details", { details: result.errors.join("; ") })}`;
        showMessage(importSummary, "warning");
      } else {
        showMessage(importSummary, "success");
      }
    } catch (err) {
      console.error("Error importing rules:", err);
      showMessage(t("settings.rules.importFailed", { error: err.message }), "error");
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
      showMessage(t("settings.backups.created", { file: res.file }), "success");
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
      showMessage(t("settings.backups.restored"), "success");
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
      showMessage(t("settings.general.nicknameSaved"), "success");
    } catch (err) {
      showMessage(t("settings.general.nicknameFailed", { error: err.message }), "error");
    }
  };

  const saveMusicDirectory = async () => {
    try {
      const result = await setMusicDirectoryAPI(musicDirectory.trim());
      if (result?.error) throw new Error(result.error);
      setMusicDirectorySaved(musicDirectory);
      showMessage(
        t("settings.general.mediaPathSaved", { path: musicDirectory || t("settings.general.defaultPath") }),
        "success",
      );
    } catch (err) {
      showMessage(
        t("settings.general.mediaPathFailed", { error: err.message || err }),
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
          ? t("settings.rules.enabled")
          : t("settings.rules.disabled"),
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
      showMessage(t("settings.rules.toggleFailed"), "error");
    }
  };

  const handleChatThinkingToggle = async (next) => {
    setChatThinkingDefaultState(next);
    try {
      const result = await apiService.setChatThinkingDefault(next);
      if (result?.error) throw new Error(result.error);
      showMessage(
        next
          ? t("settings.chat.thinkingOn")
          : t("settings.chat.thinkingOff"),
        "info",
      );
    } catch (err) {
      console.error("Failed to update chat_thinking_default:", err);
      setChatThinkingDefaultState(!next);
      showMessage(t("settings.chat.thinkingFailed"), "error");
    }
  };

  const handlePlanModeDefaultToggle = async (next) => {
    setPlanModeDefaultState(next);
    const result = await apiService.setPlanModeDefault(next);
    if (result?.error) {
      setPlanModeDefaultState(!next);
      showMessage(t("settings.chat.planModeFailed"), "error");
      return;
    }
    showMessage(next ? t("settings.chat.planModeOn") : t("settings.chat.planModeOff"), "info");
  };

  // Asks first (ConfirmActionDialog below); switchEmbedding runs on confirm.
  const handleSetEmbeddingClick = () => setConfirmEmbeddingOpen(true);

  const switchEmbedding = async () => {
    setConfirmEmbeddingOpen(false);
    setIsSwitchingEmbedding(true);
    try {
      const r = await fetch("/api/model/embedding/set", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: selectedEmbeddingModel }),
      });
      // handleResponse: an error status or an offline backend becomes an
      // error with the server's reason instead of a JSON parse failure.
      const d = await handleResponse(r, { quiet: true });
      if (d.success) {
        setEmbeddingModel(selectedEmbeddingModel);
        const payload = payloadOf(d);
        const dims = payload?.dimensions;
        const queued = payload?.reindex_queued ?? 0;
        const pending = payload?.reindex_pending ?? 0;
        // Indexed text with no document behind it is not re-indexed; say what
        // and how to rebuild it.
        const notRebuilt = payload?.not_rebuilt || [];
        const notRebuiltText = notRebuilt.length
          ? ` ${t("settings.embedding.notRebuilt", {
              items: notRebuilt
                .map((n) => t("settings.embedding.notRebuiltItem", { rows: n.rows, kind: n.kind.replace(/_/g, " "), how: n.how }))
                .join(" "),
            })}`
          : "";
        showMessage(
          payload?.index_cleared
            ? (pending
                ? t("settings.embedding.switchedPending", { model: selectedEmbeddingModel, dims, queued, pending })
                : t("settings.embedding.switched", { model: selectedEmbeddingModel, dims, queued })) + notRebuiltText
            : t("settings.embedding.already", { model: selectedEmbeddingModel }),
          payload?.index_cleared && (pending || notRebuilt.length) ? "warning" : "success",
        );
        fetchResources();
        setProfilesReloadKey((k) => k + 1);
      } else {
        showMessage(formatUiError(d.error) || t("settings.embedding.switchFailed"), "error");
      }
    } catch (e) {
      showMessage(t("settings.embedding.notSwitched", { error: formatUiError(e) || t("settings.embedding.requestFailed") }), "error");
    } finally {
      setIsSwitchingEmbedding(false);
    }
  };

  // Media model selects: optimistic, rolled back when the server refuses.
  const saveMediaModel = async (field, value, label, failedText) => {
    const previous = mediaModels[field];
    setMediaModelsState((p) => ({ ...p, [field]: value }));
    try {
      const res = await apiService.setMediaModels({ [field]: value });
      const data = res?.data ?? res;
      if (res?.error || data?.error) throw new Error(res?.error || data?.error);
      showMessage(t("settings.media.saved", { label, model: value }), "success");
    } catch (err) {
      setMediaModelsState((p) => ({ ...p, [field]: previous }));
      showMessage(
        err.message || failedText,
        "error",
      );
    }
  };

  const saveLoraStrength = async (key, field, label, raw) => {
    const v = parseFloat(raw);
    if (Number.isNaN(v)) return;
    if (lastSavedLoraRef.current[key] === v) {
      setLoraSaveState((s) => ({ ...s, [key]: "saved" }));
      return;
    }
    try {
      const res = await apiService.setMediaModels({ [field]: v });
      if (res?.error) throw new Error(res.error);
      const data = res?.data ?? res;
      if (data?.character_lora_strength) {
        setMediaModelsState((p) => ({
          ...p,
          character_lora_strength: data.character_lora_strength,
        }));
        lastSavedLoraRef.current = { ...data.character_lora_strength };
      } else {
        lastSavedLoraRef.current = { ...lastSavedLoraRef.current, [key]: v };
      }
      setLoraSaveState((s) => ({ ...s, [key]: "saved" }));
      showMessage(t("settings.media.loraSaved", { model: label, strength: v }), "success");
    } catch (err) {
      setLoraSaveState((s) => ({ ...s, [key]: "error" }));
      showMessage(err.message || t("settings.media.loraFailed"), "error");
    }
  };

  const handleIndexingPausedToggle = async (next) => {
    try {
      if (next) {
        await apiService.pauseIndexing();
        setIndexingPaused(true);
        showMessage(
          t("settings.indexing.paused"),
          "warning",
        );
      } else {
        await apiService.resumeIndexing();
        setIndexingPaused(false);
        showMessage(t("settings.indexing.resumed"), "success");
      }
    } catch (e) {
      showMessage(
        t("settings.indexing.pauseFailed", { error: e.message || e }),
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
        throw new Error(t("settings.interconnector.readConfigFailed"));
      await interconnectorApi.updateInterconnectorConfig({
        ...cfg,
        is_enabled: next,
      });
      setInterconnectorEnabled(next);
      showMessage(
        next ? t("settings.interconnector.enabled") : t("settings.interconnector.disabled"),
        "info",
      );
    } catch (err) {
      console.error("Failed to toggle Interconnector:", err);
      showMessage(
        t("settings.interconnector.notChanged", { error: err.message || err }),
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
        result?.message || t("settings.data.chatCleared"),
        result?.warning ? "warning" : "success",
      );
      setClearChatOpen(false);
    } catch (err) {
      showMessage(t("settings.data.chatClearFailed", { error: err.message }), "error");
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

  const fetchMcpStatus = useCallback(() => {
    getMcpStatus()
      .then(setMcpStatus)
      .catch((err) => {
        console.warn("Failed to read MCP status:", err);
        // Refused: the backend answers MCP status only on its own machine, or
        // to this install's API key; err.message says which to use.
        if (err?.authRefused) setMcpStatus({ refused: err.message });
      });
  }, []);

  useEffect(() => {
    fetchMcpStatus();
    apiService.getConfineToolPaths().then((result) => {
      const on = result?.data?.confine_tool_paths ?? result?.confine_tool_paths;
      if (typeof on === "boolean") setConfineToolPaths(on);
    });
    // Signing in (or creating a key) in the API key panel may unlock it.
    return onSessionChanged(fetchMcpStatus);
  }, [fetchMcpStatus]);

  // /settings#settings-api-key (the link every refusal carries) and the other
  // panel ids: scroll there once the page has laid out, and again when the
  // hash changes while the page is open.
  useEffect(() => {
    const id = decodeURIComponent((location.hash || "").replace(/^#/, ""));
    if (!id) return undefined;
    // Voice settings live in a dialog off the Chat panel; the mic popover links here.
    const isVoice = id === "settings-voice";
    if (isVoice) setVoiceSettingsModalOpen(true);
    const timer = window.setTimeout(() => {
      document
        .getElementById(isVoice ? "settings-chat" : id)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 150);
    return () => window.clearTimeout(timer);
  }, [location.hash]);

  const handleConfineToolPathsToggle = async (next) => {
    const previous = confineToolPaths;
    setConfineToolPaths(next);
    const result = await apiService.setConfineToolPaths(next);
    if (result?.error) {
      setConfineToolPaths(previous);
      showMessage(t("settings.toggles.saveFailed", { error: result.error.message || result.error }), "error");
      return;
    }
    showMessage(
      next
        ? t("settings.agents.confineOn")
        : t("settings.agents.confineOff"),
      "info",
    );
  };

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
            ? t("settings.learning.rulesCleared")
            : t("settings.learning.logCleared")),
        "success",
      );
      setLearningClear(null);
    } catch (err) {
      showMessage(t("settings.learning.clearFailed", { error: err.message }), "error");
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
      showMessage(t("settings.memory.cleared"), "success");
      setClearMemoriesOpen(false);
      fetchMemoryCount();
    } catch (err) {
      showMessage(t("settings.memory.clearFailed", { error: err.message }), "error");
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
        label={t("settings.strip.chatModel")}
        help={t("settings.strip.chatModelHelp")}
        tone={isLoadingModel ? "warn" : activeModel ? "ok" : "off"}
        value={activeModel || t("settings.strip.none")}
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
        label={t("settings.strip.embedding")}
        help={t("settings.strip.embeddingHelp")}
        tone={
          embeddingModel &&
          embeddingModel !== "Not Set" &&
          embeddingModel !== "Not Available"
            ? "ok"
            : "off"
        }
        value={
          embeddingModel === "Not Set"
            ? t("settings.strip.notSet")
            : embeddingModel === "Not Available"
              ? t("settings.strip.notAvailable")
              : embeddingModel || t("settings.strip.none")
        }
        sub={
          activeEmbeddingEntry?.dimensions
            ? `${activeEmbeddingEntry.dimensions}d`
            : undefined
        }
      />
      {gpu?.total_mb > 0 && (
        <DashboardTile
          label={t("settings.strip.vram")}
          help={t("settings.strip.vramHelp")}
          value={t("settings.strip.vramValue", { used: (gpu.used_mb / 1024).toFixed(1), total: (gpu.total_mb / 1024).toFixed(1) })}
          progress={gpu.utilization_pct}
        />
      )}
      {gpuResources && (
        <DashboardTile
          label={t("settings.strip.loaded")}
          help={t("settings.strip.loadedHelp")}
          tone={gpuResources?.loaded_models?.length ? "ok" : "off"}
          value={
            gpuResources?.loaded_models?.length
              ? t("settings.strip.modelsLoaded", { count: gpuResources.loaded_models.length })
              : t("settings.strip.nothing")
          }
          sub={gpuResources?.loaded_models?.map((m) => m.name).join(", ")}
        />
      )}
      <DashboardTile
        label={t("settings.strip.index")}
        help={t("settings.strip.indexHelp")}
        tone={indexTotals.rows > 0 ? "ok" : "off"}
        value={
          indexTotals.rows > 0
            ? t("settings.strip.indexValue", { rows: indexTotals.rows.toLocaleString(uiLocale()), size: formatByteSize(indexTotals.bytes) })
            : t("settings.strip.empty")
        }
        sub={
          t("settings.strip.profilesActive", { active: indexTotals.active, total: indexProfiles.length }) +
          (indexingPaused ? ` · ${t("settings.strip.paused")}` : "")
        }
        onClick={() =>
          document
            .getElementById("settings-knowledge")
            ?.scrollIntoView({ behavior: "smooth", block: "start" })
        }
      />
      <DashboardTile
        label={t("settings.strip.imageGeneration")}
        help={t("settings.strip.imageGenerationHelp")}
        tone={
          imageGenAvailable
            ? "ok"
            : imageGenStatus === null
              ? "warn"
              : "off"
        }
        value={
          imageGenStatus === null
            ? t("settings.strip.checking")
            : imageGenAvailable
              ? t("settings.strip.available")
              : t("settings.strip.unavailable")
        }
        sub={
          imageGenAvailable ? undefined : imageGenUnavailableHint
        }
      />
      {interconnectorEnabled && (
        <DashboardTile
          label={t("settings.strip.interconnector")}
          help={t("settings.strip.interconnectorHelp")}
          tone={
            interconnectorPendingCount > 0 ||
            interconnectorUpdateStatus?.summary?.total > 0
              ? "warn"
              : "ok"
          }
          value={
            interconnectorUpdateStatus?.summary?.total > 0
              ? t("settings.strip.codeUpdates", { count: interconnectorUpdateStatus.summary.total })
              : interconnectorPendingCount > 0
                ? t("settings.strip.pending", { count: interconnectorPendingCount })
                : t("settings.strip.inSync")
          }
          onClick={() => setInterconnectorModalOpen(true)}
        />
      )}
    </DashboardStrip>
  );

  const generalPanel = (
    <SettingsPanel
      id="settings-general"
      title={t("settings.generalPanel.title")}
      help={t("settings.generalPanel.help")}
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
                showMessage(t("settings.generalPanel.imageUpdated"), "success");
              } catch (err) {
                showMessage(t("settings.generalPanel.imageFailed", { error: err.message }), "error");
              }
            })();
          }
        }}
        style={{ display: "none" }}
        id="logo-upload"
      />
      <Line nowrap>
        <Tooltip title={t("settings.generalPanel.changeImage")}>
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
          label={t("settings.generalPanel.nickname")}
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
          tooltip={t("settings.generalPanel.currentTheme", { theme: themeName })}
        >
          {t("settings.generalPanel.theme")}
        </ActionButton>
      </Line>
      <Cluster
        label={t("settings.generalPanel.navigation")}
        help={t("settings.generalPanel.navigationHelp")}
      >
        <Line>
          <ChoiceChips
            ariaLabel={t("settings.generalPanel.navigationAria")}
            value={navChrome}
            onChange={setNavChrome}
            options={[
              {
                value: NAV_CHROME.SIDEBAR,
                label: t("settings.generalPanel.sidebar"),
                tooltip: t("settings.generalPanel.sidebarTooltip"),
              },
              {
                value: NAV_CHROME.SOFTWARE,
                label: t("settings.generalPanel.workspaces"),
                tooltip: t("settings.generalPanel.workspacesTooltip"),
              },
            ]}
          />
        </Line>
      </Cluster>
      <LanguageSetting />
      <TipsSetting />
      <JobOrderSetting showMessage={showMessage} />
      <Cluster
        label={t("settings.generalPanel.mediaLibrary")}
        help={t("settings.generalPanel.mediaLibraryHelp")}
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
            {t("settings.generalPanel.save")}
          </ActionButton>
        </Line>
      </Cluster>
      <ProfileSection />
    </SettingsPanel>
  );

  const modelsPanel = (
    <SettingsPanel
      id="settings-models"
      title={t("settings.modelsPanel.title")}
      help={t("settings.modelsPanel.help")}
    >
      <Cluster
        label={t("settings.modelsPanel.chat")}
        help={t("settings.modelsPanel.chatHelp")}
      >
        {chatSizes.length > 1 && (
          <ChoiceChips
            ariaLabel={t("settings.modelsPanel.sizeFilterAria")}
            value={chatSizeFilter || "all"}
            onChange={(v) => setChatSizeFilter(v === "all" ? null : v)}
            options={[
              { value: "all", label: t("settings.modelsPanel.all") },
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
            <InputLabel>{t("settings.modelsPanel.chatModel")}</InputLabel>
            <Select
              value={selectedModel}
              label={t("settings.modelsPanel.chatModel")}
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
            disabled={!chatModelPending || isLoading || isLoadingModel || ollamaOffline}
            loading={isLoadingModel}
            tooltip={t("settings.modelsPanel.setActiveTooltip")}
          >
            {t("settings.modelsPanel.setActive")}
          </ActionButton>
          <ActionButton
            onClick={async () => {
              setIsTestingLLM(true);
              try {
                const result = await apiService.testLLM();
                if (result?.error) throw new Error(result.error);
                showMessage(
                  t("settings.modelsPanel.answered", { model: activeModel || t("settings.modelsPanel.activeModel"), seconds: result?.duration_sec ?? "?" }),
                  "success",
                );
              } catch (err) {
                showMessage(t("settings.modelsPanel.testFailed", { error: err.message }), "error");
              } finally {
                setIsTestingLLM(false);
              }
            }}
            loading={isTestingLLM}
            disabled={isLoading || isLoadingModel}
            tooltip={t("settings.modelsPanel.testTooltip")}
          >
            {t("settings.modelsPanel.test")}
          </ActionButton>
          <ActionButton
            onClick={fetchAvailableModels}
            disabled={isLoading}
            tooltip={t("settings.modelsPanel.refreshTooltip")}
            aria-label={t("settings.modelsPanel.refreshAria")}
            sx={{ px: 1, minWidth: 0 }}
          >
            <RefreshIcon sx={{ fontSize: 16 }} />
          </ActionButton>
        </Line>
        {modelSwitchStatus === "loading" && (
          <Box>
            <LinearProgress sx={{ height: 4, borderRadius: 2 }} />
            <Hint>{modelSwitchMessage || t("settings.modelsPanel.switching")}</Hint>
          </Box>
        )}
        {ollamaOffline && (
          <Line>
            <StatusPill
              tone="warn"
              label={t("settings.modelsPanel.ollamaOff")}
              tooltip={t("settings.modelsPanel.ollamaOffTooltip")}
            />
            <ActionButton
              kind="primary"
              onClick={handleStartOllama}
              loading={startingOllama}
              tooltip={t("settings.modelsPanel.startOllamaTooltip")}
            >
              {t("settings.modelsPanel.startOllama")}
            </ActionButton>
          </Line>
        )}
        {keepReady && (
          <Line>
            <SettingChip
              label={t("settings.modelsPanel.keepReady")}
              on={Boolean(keepReady.enabled)}
              onToggle={handleKeepReadyToggle}
              disabled={savingKeepReady || keepReady.env_writable === false}
              tooltip={t("settings.modelsPanel.keepReadyTooltip")}
            />
            {keepReady.enabled && (
              <StatusPill
                tone={
                  keepReady.state === "ready"
                    ? "ok"
                    : keepReady.state === "loading"
                      ? "info"
                      : "warn"
                }
                label={
                  keepReady.state === "ready"
                    ? t("settings.modelsPanel.keepReadyLoaded")
                    : keepReady.state === "loading"
                      ? t("settings.modelsPanel.keepReadyLoading")
                      : keepReady.state === "stepped aside"
                        ? t("settings.modelsPanel.keepReadySteppedAside")
                        : t("settings.modelsPanel.keepReadyWaiting")
                }
                tooltip={keepReady.detail || ""}
              />
            )}
            {keepReady.enabled && keepReady.detail && keepReady.state !== "ready" && (
              <Hint>{keepReady.detail}</Hint>
            )}
          </Line>
        )}
      </Cluster>

      <Cluster
        label={t("settings.modelsPanel.embedding")}
        help={t("settings.modelsPanel.embeddingHelp")}
      >
        {embedDims.length > 1 && (
          <ChoiceChips
            ariaLabel={t("settings.modelsPanel.dimFilterAria")}
            value={embedDimFilter === null ? "all" : String(embedDimFilter)}
            onChange={(v) => setEmbedDimFilter(v === "all" ? null : Number(v))}
            options={[
              { value: "all", label: t("settings.modelsPanel.all") },
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
            <InputLabel>{t("settings.modelsPanel.embeddingModel")}</InputLabel>
            <Select
              value={selectedEmbeddingModel}
              label={t("settings.modelsPanel.embeddingModel")}
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
            tooltip={t("settings.modelsPanel.embeddingSetTooltip")}
          >
            {t("settings.modelsPanel.setActive")}
          </ActionButton>
          <ActionButton
            onClick={fetchEmbeddingModels}
            tooltip={t("settings.modelsPanel.embeddingRefreshTooltip")}
            aria-label={t("settings.modelsPanel.embeddingRefreshAria")}
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
      title={t("settings.chatPanel.title")}
      help={t("settings.chatPanel.help")}
    >
      <Cluster
        label={t("settings.chatPanel.capabilities")}
        help={t("settings.chatPanel.capabilitiesHelp")}
      >
        <Line>
          <SettingChip
            label={t("settings.chatPanel.rules")}
            on={rulesEnabled}
            onToggle={handleRulesToggle}
            onSettings={() => navigate("/rules")}
            tooltip={t("settings.chatPanel.rulesTooltip")}
          />
          <SettingChip
            label={t("settings.chatPanel.thinking")}
            on={chatThinkingDefault}
            onToggle={handleChatThinkingToggle}
            tooltip={t("settings.chatPanel.thinkingTooltip")}
          />
          <SettingChip
            label={t("settings.chatPanel.planMode")}
            on={planModeDefault}
            onToggle={handlePlanModeDefaultToggle}
            tooltip={t("settings.chatPanel.planModeTooltip")}
          />
          <SettingChip
            label={t("settings.chatPanel.webAccess")}
            on={webSearchEnabled}
            onToggle={handleWebSearchToggle}
            tooltip={t("settings.chatPanel.webAccessTooltip")}
          />
          <SettingChip
            label={t("settings.chatPanel.voiceChat")}
            on={voiceChatEnabled}
            onToggle={setVoiceChatEnabled}
            onSettings={() => setVoiceSettingsModalOpen(true)}
            tooltip={t("settings.chatPanel.voiceChatTooltip")}
          />
        </Line>
      </Cluster>
      <Cluster
        label={t("settings.chatPanel.retrieval")}
        help={t("settings.chatPanel.retrievalHelp")}
      >
        <Line>
          <SettingChip
            label={t("settings.chatPanel.enhancedContext")}
            on={enhancedContext}
            onToggle={(next) => handleEnhancedContextChange(next)}
            tooltip={t("settings.chatPanel.enhancedContextTooltip")}
          />
          <SettingChip
            label={t("settings.chatPanel.advancedRag")}
            on={advancedRag}
            onToggle={(next) => handleAdvancedRagChange(next)}
            tooltip={t("settings.chatPanel.advancedRagTooltip")}
          />
          <SettingChip
            label={t("settings.chatPanel.behaviourLearning")}
            on={behaviorLearningEnabled}
            onToggle={(next) => handleBehaviorLearningToggle(next)}
            tooltip={t("settings.chatPanel.behaviourLearningTooltip")}
          />
        </Line>
      </Cluster>
    </SettingsPanel>
  );

  const generationPanel = (
    <SettingsPanel
      id="settings-generation"
      title={t("settings.generationPanel.title")}
      help={t("settings.generationPanel.help")}
    >
      <Cluster
        label={t("settings.generationPanel.prompts")}
        help={t("settings.generationPanel.promptsHelp")}
      >
        <Line>
          <SettingChip
            label={t("settings.generationPanel.verbatim")}
            on={verbatimPrompts}
            onToggle={() => handleVerbatimPromptsToggle()}
            disabled={verbatimForcedByEnv || verbatimSaving}
            note={verbatimForcedByEnv ? t("settings.generationPanel.forcedByEnv") : undefined}
            tooltip={
              verbatimForcedByEnv
                ? t("settings.generationPanel.verbatimForcedTooltip")
                : t("settings.generationPanel.verbatimTooltip")
            }
          />
        </Line>
      </Cluster>
      <Cluster
        label={t("settings.generationPanel.stillsModels")}
        help={t("settings.generationPanel.stillsModelsHelp")}
      >
        <Line>
          <FormControl size="small" className="grow">
            <InputLabel id="media-stills-label">{t("settings.generationPanel.stills")}</InputLabel>
            <Select
              labelId="media-stills-label"
              label={t("settings.generationPanel.stills")}
              value={mediaModels.stills_model || "auto"}
              onChange={(e) =>
                saveMediaModel(
                  "stills_model",
                  e.target.value,
                  t("settings.generationPanel.stillsModel"),
                  t("settings.generationPanel.stillsModelFailed"),
                )
              }
            >
              {[
                // Saves "auto": Auto picks render by card size again, the way
                // back once a model has been chosen.
                { id: "auto", name: t("settings.generationPanel.auto") },
                ...(mediaModels.stills_profiles?.length
                  ? mediaModels.stills_profiles
                  : [
                      { id: "zimage-turbo", name: "Z-Image Turbo" },
                      { id: "flux-dev", name: "FLUX.1 Dev" },
                      { id: "krea2-turbo", name: "Krea 2 Turbo" },
                      { id: "sdxl-legacy", name: "SDXL (Legacy)" },
                    ]),
              ].map((p) => (
                <MenuItem key={p.id} value={p.id}>
                  {p.name || p.id}
                  {p.recommended ? " ★" : ""}
                  {p.deprecated ? ` ${t("settings.generationPanel.legacy")}` : ""}
                </MenuItem>
              ))}
            </Select>
          </FormControl>
          <FormControl size="small" className="grow">
            <InputLabel id="media-train-label">{t("settings.generationPanel.castLoraBase")}</InputLabel>
            <Select
              labelId="media-train-label"
              label={t("settings.generationPanel.castLoraBase")}
              value={mediaModels.cast_train_base || "zimage-turbo"}
              onChange={(e) =>
                saveMediaModel(
                  "cast_train_base",
                  e.target.value,
                  t("settings.generationPanel.castTrainBase"),
                  t("settings.generationPanel.castTrainBaseFailed"),
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
            <InputLabel id="media-max-label">{t("settings.generationPanel.maxQuality")}</InputLabel>
            <Select
              labelId="media-max-label"
              label={t("settings.generationPanel.maxQuality")}
              value={mediaModels.max_quality_model || "flux-dev"}
              onChange={(e) =>
                saveMediaModel(
                  "max_quality_model",
                  e.target.value,
                  t("settings.generationPanel.maxQualityModel"),
                  t("settings.generationPanel.maxQualityModelFailed"),
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
        label={t("settings.generationPanel.betweenBatches")}
        help={t("settings.generationPanel.betweenBatchesHelp")}
      >
        <Line>
          <ChoiceChips
            ariaLabel={t("settings.generationPanel.betweenBatchesAria")}
            value={String(imageKeepMinutes)}
            onChange={handleImageKeepChange}
            disabled={imageKeepSaving}
            options={[
              {
                value: "0",
                label: t("settings.generationPanel.unload"),
                tooltip: t("settings.generationPanel.unloadTooltip"),
              },
              ...[10, 30, 60].map((m) => ({
                value: String(m),
                label: m === 60 ? t("settings.generationPanel.keepHour") : t("settings.generationPanel.keepMinutes", { minutes: m }),
                tooltip:
                  m === 60
                    ? t("settings.generationPanel.keepHourTooltip")
                    : t("settings.generationPanel.keepMinutesTooltip", { minutes: m }),
              })),
              ...(![0, 10, 30, 60].includes(imageKeepMinutes)
                ? [{ value: String(imageKeepMinutes), label: t("settings.generationPanel.keepMinutes", { minutes: imageKeepMinutes }) }]
                : []),
            ]}
          />
        </Line>
      </Cluster>
      <Cluster
        label={t("settings.generationPanel.loraStrength")}
        help={t("settings.generationPanel.loraStrengthHelp")}
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
            <Box key={key} className="grow" sx={{ display: "flex", flexDirection: "column", gap: 0.5, minWidth: 0 }}>
              <TextField
                size="small"
                type="number"
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
                  setLoraSaveState((s) => ({ ...s, [key]: "unsaved" }));
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    saveLoraStrength(key, field, label, e.target.value);
                  }
                }}
                onBlur={(e) => saveLoraStrength(key, field, label, e.target.value)}
              />
              <StatusPill
                tone={
                  loraSaveState[key] === "saved"
                    ? "ok"
                    : loraSaveState[key] === "error"
                      ? "error"
                      : "warn"
                }
                label={
                  loraSaveState[key] === "saved"
                    ? t("settings.generationPanel.saved")
                    : loraSaveState[key] === "error"
                      ? t("settings.generationPanel.notSaved")
                      : t("settings.generationPanel.unsaved")
                }
              />
            </Box>
          ))}
        </Line>
      </Cluster>
      <Cluster
        label={t("settings.generationPanel.identityCheck")}
        help={t("settings.generationPanel.identityCheckHelp")}
      >
        <IdentityCheckControl showMessage={showMessage} />
      </Cluster>
      <Cluster
        label={t("settings.generationPanel.libraries")}
        help={t("settings.generationPanel.librariesHelp")}
      >
        <Line>
          <StatusPill
            tone={
              imageGenAvailable
                ? "ok"
                : imageGenStatus === null
                  ? "warn"
                  : "neutral"
            }
            label={
              imageGenStatus === null
                ? t("settings.generationPanel.imageChecking")
                : imageGenAvailable
                  ? t("settings.generationPanel.imageAvailable")
                  : t("settings.generationPanel.imageUnavailable")
            }
            tooltip={
              imageGenAvailable
                ? ""
                : imageGenUnavailableHint
            }
          />
          <ActionButton onClick={() => setImageModelsModalOpen(true)}>
            {t("settings.generationPanel.image")}
          </ActionButton>
          <ActionButton onClick={() => setInfographicModelsModalOpen(true)}>
            {t("settings.generationPanel.infographic")}
          </ActionButton>
          <ActionButton onClick={() => setVideoModelsModalOpen(true)}>
            {t("settings.generationPanel.video")}
          </ActionButton>
          <ActionButton onClick={() => setVoiceModelsModalOpen(true)}>
            {t("settings.generationPanel.voice")}
          </ActionButton>
          <ActionButton onClick={() => setAudioModelsModalOpen(true)}>
            {t("settings.generationPanel.audio")}
          </ActionButton>
          <ActionButton
            onClick={() => setTrainingLibrariesModalOpen(true)}
            tooltip={t("settings.generationPanel.trainingTooltip")}
          >
            {t("settings.generationPanel.training")}
          </ActionButton>
        </Line>
      </Cluster>
    </SettingsPanel>
  );

  const knowledgePanel = (
    <SettingsPanel
      id="settings-knowledge"
      title={t("settings.knowledgePanel.title")}
      help={t("settings.knowledgePanel.help")}
    >
      <Cluster
        label={t("settings.knowledgePanel.profiles")}
        help={t("settings.knowledgePanel.profilesHelp")}
      >
        <IndexProfileChips
          onLoaded={setIndexProfiles}
          reloadKey={profilesReloadKey}
          showMessage={showMessage}
          onEdit={setEditProfile}
        />
      </Cluster>
      <Cluster
        label={t("settings.knowledgePanel.indexing")}
        help={t("settings.knowledgePanel.indexingHelp")}
      >
        <Line>
          <SettingChip
            label={t("settings.knowledgePanel.paused")}
            on={indexingPaused}
            onToggle={handleIndexingPausedToggle}
            tooltip={t("settings.knowledgePanel.pausedTooltip")}
          />
          <Sep />
          <ActionButton
            onClick={() =>
              handleActionClick(
                apiService.optimizeIndex,
                [],
                null,
                t("settings.knowledgePanel.optimizing"),
                t("settings.knowledgePanel.optimized"),
                t("settings.knowledgePanel.optimizeFailed"),
              )
            }
            disabled={isLoading}
            tooltip={t("settings.knowledgePanel.optimizeTooltip")}
          >
            {t("settings.knowledgePanel.optimize")}
          </ActionButton>
          <ActionButton
            onClick={() =>
              handleActionClick(
                apiService.buildCorpusSummaries,
                [{ replace: true }],
                t("settings.knowledgePanel.summariesConfirm"),
                t("settings.knowledgePanel.summariesQueuing"),
                t("settings.knowledgePanel.summariesQueued"),
                t("settings.knowledgePanel.summariesFailed"),
              )
            }
            disabled={isLoading}
            tooltip={t("settings.knowledgePanel.summariesTooltip")}
          >
            {t("settings.knowledgePanel.summaries")}
          </ActionButton>
          <ActionButton
            onClick={async () => {
              try {
                const res = await apiService.resumePendingIndexing();
                showMessage(
                  res?.message || t("settings.knowledgePanel.pendingQueued"),
                  "success",
                );
              } catch (e) {
                showMessage(
                  t("settings.knowledgePanel.pendingFailed", { error: e.message || e }),
                  "error",
                );
              }
            }}
            disabled={isLoading || indexingPaused}
            tooltip={
              indexingPaused
                ? t("settings.knowledgePanel.pendingPausedTooltip")
                : t("settings.knowledgePanel.pendingTooltip")
            }
          >
            {t("settings.knowledgePanel.pending")}
          </ActionButton>
        </Line>
      </Cluster>
      <Cluster
        label={t("settings.knowledgePanel.reranker")}
        help={t("settings.knowledgePanel.rerankerHelp")}
      >
        <RerankerControl showMessage={showMessage} />
      </Cluster>
      <Cluster
        label={t("settings.knowledgePanel.autoresearch")}
        help={t("settings.knowledgePanel.autoresearchHelp")}
      >
        <Line>
          <SettingChip
            label={t("settings.knowledgePanel.nightly")}
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
            tooltip={t("settings.knowledgePanel.nightlyTooltip")}
          />
          <ActionButton
            kind="link"
            startIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}
            onClick={() => navigate("/autoresearch")}
          >
            {t("settings.knowledgePanel.autoresearchLink")}
          </ActionButton>
          <ActionButton
            kind="link"
            startIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}
            onClick={() => navigate("/dev-tools")}
          >
            {t("settings.knowledgePanel.retrievalHealth")}
          </ActionButton>
        </Line>
      </Cluster>
    </SettingsPanel>
  );

  const agentsPanel = (
    <SettingsPanel
      id="settings-agents"
      title={t("settings.agentsPanel.title")}
      help={t("settings.agentsPanel.help")}
    >
      <UncleClaudeSection />
      <InboundGuardSection />
      <Cluster
        label={t("settings.agentsPanel.memory")}
        help={t("settings.agentsPanel.memoryHelp")}
      >
        <Line>
          <StatusPill
            tone={memoryCount > 0 ? "ok" : "neutral"}
            label={memoryCount === null ? t("settings.agentsPanel.counting") : t("settings.agentsPanel.memoryActive", { count: memoryCount })}
          />
          <ActionButton onClick={() => navigate("/agents/memory")}>
            {t("settings.agentsPanel.manageMemory")}
          </ActionButton>
        </Line>
      </Cluster>
      <Cluster
        label={t("settings.agentsPanel.fileAccess")}
        help={t("settings.agentsPanel.fileAccessHelp")}
      >
        <Line>
          <SettingChip
            label={t("settings.agentsPanel.projectOnly")}
            on={confineToolPaths}
            onToggle={handleConfineToolPathsToggle}
            tooltip={t("settings.agentsPanel.projectOnlyTooltip")}
          />
        </Line>
      </Cluster>
      <Cluster
        label={t("settings.agentsPanel.mcp")}
        help={t("settings.agentsPanel.mcpHelp")}
      >
        <Line>
          <StatusPill
            tone={
              mcpStatus?.refused
                ? "warn"
                : mcpStatus?.servers_connected > 0
                  ? "ok"
                  : "neutral"
            }
            label={
              mcpStatus?.refused
                ? t("settings.agentsPanel.needsKey")
                : mcpStatus
                  ? t("settings.agentsPanel.mcpConnected", { connected: mcpStatus.servers_connected, configured: mcpStatus.servers_configured })
                  : t("settings.agentsPanel.checking")
            }
            tooltip={mcpStatus?.refused || ""}
          />
          {mcpStatus?.refused ? (
            <ActionButton
              kind="link"
              onClick={() =>
                document
                  .getElementById(API_KEY_SECTION_ID)
                  ?.scrollIntoView({ behavior: "smooth", block: "start" })
              }
            >
              {t("settings.agentsPanel.accessLink")}
            </ActionButton>
          ) : (
            <ActionButton onClick={() => navigate("/agents/mcp")}>
              {t("settings.agentsPanel.manageMcp")}
            </ActionButton>
          )}
        </Line>
      </Cluster>
      <Cluster
        label={t("settings.agentsPanel.display")}
        help={t("settings.agentsPanel.displayHelp")}
      >
        <AgentDisplaySection showMessage={showMessage} />
      </Cluster>
    </SettingsPanel>
  );

  const syncPanel = (
    <SettingsPanel
      id="settings-sync"
      title={t("settings.syncPanel.title")}
      help={t("settings.syncPanel.help")}
    >
      <Line>
        <SettingChip
          label={t("settings.syncPanel.interconnector")}
          on={interconnectorEnabled}
          onToggle={handleInterconnectorToggle}
          onSettings={() => setInterconnectorModalOpen(true)}
          tooltip={t("settings.syncPanel.interconnectorTooltip")}
        />
        {interconnectorEnabled && interconnectorPendingCount > 0 && (
          <StatusPill
            tone="warn"
            label={t("settings.syncPanel.pending", { count: interconnectorPendingCount })}
            tooltip={t("settings.syncPanel.pendingTooltip")}
          />
        )}
        {interconnectorEnabled &&
          interconnectorIsClient &&
          interconnectorUpdateStatus?.summary?.total > 0 && (
            <>
              <StatusPill
                tone="warn"
                label={t("settings.syncPanel.codeUpdates", { count: interconnectorUpdateStatus.summary.total })}
                tooltip={t("settings.syncPanel.updatesBreakdown", {
                  backend: interconnectorUpdateStatus.summary.backend || 0,
                  frontend: interconnectorUpdateStatus.summary.frontend || 0,
                  other: interconnectorUpdateStatus.summary.other || 0,
                })}
              />
              <ActionButton
                onClick={handleApplyInterconnectorUpdates}
                loading={interconnectorApplying}
                tooltip={t("settings.syncPanel.applyTooltip")}
              >
                {t("settings.syncPanel.apply")}
              </ActionButton>
            </>
          )}
      </Line>
    </SettingsPanel>
  );

  const dataPanel = (
    <SettingsPanel
      id="settings-data"
      title={t("settings.dataPanel.title")}
      help={t("settings.dataPanel.help")}
    >
      <Cluster
        label={t("settings.dataPanel.backups")}
        help={t("settings.dataPanel.backupsHelp")}
      >
        <Line>
          <ActionButton
            onClick={openCreateBackup}
            disabled={isProcessingBackup || isLoading}
          >
            {t("settings.dataPanel.create")}
          </ActionButton>
          <ActionButton
            onClick={openRestoreBackup}
            disabled={isProcessingBackup || isLoading}
            tooltip={t("settings.dataPanel.restoreTooltip")}
          >
            {t("settings.dataPanel.restore")}
          </ActionButton>
          <ActionButton
            onClick={openManageBackups}
            disabled={isProcessingBackup || isLoading}
          >
            {t("settings.dataPanel.manage")}
          </ActionButton>
          <Sep />
          <ExportChatsButton showMessage={showMessage} disabled={isLoading} />
        </Line>
      </Cluster>
      <Cluster
        label={t("settings.dataPanel.rules")}
        help={t("settings.dataPanel.rulesHelp")}
      >
        <Line>
          <ActionButton
            onClick={handleExportRulesClick}
            loading={isExporting}
            disabled={isLoading}
          >
            {t("settings.dataPanel.exportRules")}
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
              {t("settings.dataPanel.chooseFile")}
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
            tooltip={t("settings.dataPanel.importTooltip")}
          >
            {t("settings.dataPanel.import")}
          </ActionButton>
        </Line>
      </Cluster>
    </SettingsPanel>
  );

  const dangerPanel = (
    <SettingsPanel
      id="settings-danger"
      title={t("settings.dangerPanel.title")}
      help={t("settings.dangerPanel.help")}
      danger
    >
      <Line>
        <ActionButton
          kind="destructive"
          onClick={openClearChatDialog}
          disabled={isLoading}
        >
          {t("settings.dangerPanel.clearChat")}
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={handleDeleteGenerationHistoryClick}
          disabled={isLoading}
        >
          {t("settings.dangerPanel.deleteHistory")}
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={() => setClearMemoriesOpen(true)}
          disabled={isLoading || !memoryCount}
        >
          {t("settings.dangerPanel.clearMemory")}
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={() => setRebuildDialogOpen(true)}
          disabled={isLoading}
        >
          {t("settings.dangerPanel.rebuildIndex")}
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={() => setLearningClear("rules")}
          disabled={isLoading}
          tooltip={t("settings.dangerPanel.clearLearnedTooltip")}
        >
          {t("settings.dangerPanel.clearLearned")}
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={() => setLearningClear("log")}
          disabled={isLoading}
          tooltip={t("settings.dangerPanel.clearLogTooltip")}
        >
          {t("settings.dangerPanel.clearLog")}
        </ActionButton>
        <Sep />
        <ActionButton
          kind="destructive"
          onClick={handleRebootClick}
          disabled={isLoading || rebootInProgress}
        >
          {t("settings.dangerPanel.reboot")}
        </ActionButton>
        <ActionButton
          kind="destructive"
          onClick={() => setKillSwitchOpen(true)}
          disabled={isLoading}
        >
          {t("settings.dangerPanel.killSwitch")}
        </ActionButton>
      </Line>
    </SettingsPanel>
  );

  const developerPanel = (
    <SettingsPanel
      id="settings-developer"
      title={t("settings.developerPanel.title")}
      help={t("settings.developerPanel.help")}
    >
      <Line>
        <SettingChip
          label={t("settings.developerPanel.verbose")}
          on={advancedDebug}
          onToggle={(next) => handleAdvancedDebugToggle(next)}
          tooltip={t("settings.developerPanel.verboseTooltip")}
        />
        <SettingChip
          label={t("settings.developerPanel.llmDebug")}
          on={llmDebug}
          onToggle={(next) => handleLlmDebugToggle(next)}
          tooltip={t("settings.developerPanel.llmDebugTooltip")}
        />
        <Sep />
        <ActionButton
          onClick={handleClearPycacheFoldersClick}
          disabled={isLoading}
          tooltip={t("settings.developerPanel.clearCacheTooltip")}
        >
          {t("settings.developerPanel.clearCache")}
        </ActionButton>
        <ActionButton
          kind="link"
          startIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}
          onClick={() => navigate("/dev-tools")}
        >
          {t("settings.developerPanel.diagnostics")}
        </ActionButton>
      </Line>
    </SettingsPanel>
  );

  const aboutPanel = (
    <SettingsPanel
      id="settings-about"
      title={t("settings.aboutPanel.title")}
    >
      <Cluster
        label={t("settings.aboutPanel.support")}
        help={t("settings.aboutPanel.supportHelp")}
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
            {t("settings.aboutPanel.sponsors")}
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

  const apiKeyPanel = <ApiKeySection />;

  const columnSets =
    columns === 3
      ? [
          [generalPanel, chatPanel, dataPanel, aboutPanel],
          [modelsPanel, knowledgePanel, apiKeyPanel, dangerPanel],
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
              apiKeyPanel,
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
              apiKeyPanel,
              dataPanel,
              dangerPanel,
              developerPanel,
              aboutPanel,
            ],
          ];

  const deleteHistoryFacts = deleteHistoryCounts
    ? [
        {
          label: t("settings.deleteHistory.images"),
          value: t("settings.deleteHistory.imagesValue", {
            batches: deleteHistoryCounts.images?.batches || 0,
            files: deleteHistoryCounts.images?.files || 0,
            size: formatByteSize(deleteHistoryCounts.images?.bytes),
          }),
        },
        {
          label: t("settings.deleteHistory.videos"),
          value: t("settings.deleteHistory.imagesValue", {
            batches: deleteHistoryCounts.videos?.batches || 0,
            files: deleteHistoryCounts.videos?.files || 0,
            size: formatByteSize(deleteHistoryCounts.videos?.bytes),
          }),
        },
        {
          label: t("settings.deleteHistory.audio"),
          value: t("settings.deleteHistory.audioValue", {
            files: deleteHistoryCounts.audio?.files || 0,
            jobs: deleteHistoryCounts.audio?.jobs || 0,
            size: formatByteSize(deleteHistoryCounts.audio?.bytes),
          }),
        },
        {
          label: t("settings.deleteHistory.comfy"),
          value: t("settings.deleteHistory.comfyValue", {
            output: deleteHistoryCounts.comfyui?.output?.files || 0,
            input: deleteHistoryCounts.comfyui?.input?.files || 0,
            size: formatByteSize(deleteHistoryCounts.comfyui?.bytes),
          }),
        },
        {
          label: t("settings.deleteHistory.database"),
          value: t("settings.deleteHistory.databaseValue", {
            documents: deleteHistoryCounts.db?.documents || 0,
            folders: deleteHistoryCounts.db?.folders || 0,
            jobs: deleteHistoryCounts.db?.job_history || 0,
          }),
        },
      ]
    : [];
  const deleteHistoryRunning =
    deleteHistoryCounts?.images?.running?.length ||
    deleteHistoryCounts?.videos?.running?.length ||
    deleteHistoryCounts?.audio?.running?.length;

  return (
    <PageLayout
      title={t("settings.pageTitle")}
      variant="standard"
      actions={
        appVersion ? (
          <Typography variant="caption" color="text.secondary">
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
        whisperManualInstall={whisperManualInstall}
        onCopyWhisperCommand={copyWhisperCommand}
        onManageVoiceModels={() => setVoiceModelsModalOpen(true)}
        showMessage={showMessage}
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
        open={confirmEmbeddingOpen}
        onClose={() => setConfirmEmbeddingOpen(false)}
        onConfirm={switchEmbedding}
        title={t("settings.dialogs.embeddingTitle")}
        description={t("settings.dialogs.embeddingDescription")}
        facts={[{ label: t("settings.dialogs.newModel"), value: selectedEmbeddingModel || "—" }]}
        keeps={t("settings.dialogs.embeddingKeeps")}
        confirmLabel={t("settings.dialogs.embeddingConfirm")}
        busy={isSwitchingEmbedding}
      />
      <ConfirmActionDialog
        open={rebootDialogOpen}
        onClose={handleCancelReboot}
        onConfirm={handleConfirmReboot}
        title={t("settings.dialogs.rebootTitle")}
        description={t("settings.dialogs.rebootDescription")}
        keeps={t("settings.dialogs.rebootKeeps")}
        confirmLabel={t("settings.dialogs.rebootConfirm")}
        busy={rebootInProgress}
      />
      <ConfirmActionDialog
        open={deleteHistoryDialogOpen}
        onClose={handleCancelDeleteHistory}
        onConfirm={handleConfirmDeleteHistory}
        title={t("settings.deleteHistory.title")}
        description={
          <>
            {t("settings.deleteHistory.description")}
            {deleteHistoryRunning
              ? ` ${t("settings.deleteHistory.runningSkipped")}`
              : ""}
            {deleteHistoryCounts?.comfyui?.running?.length
              ? ` ${t("settings.deleteHistory.comfyRendering")}`
              : ""}
            {!deleteHistoryCounts
              ? ` ${t("settings.deleteHistory.countsUnavailable")}`
              : ""}
          </>
        }
        facts={deleteHistoryFacts}
        keeps={t("settings.deleteHistory.keeps")}
        confirmLabel={t("settings.deleteHistory.confirm")}
        busy={deleteHistoryInProgress}
      />
      <ConfirmActionDialog
        open={clearChatOpen}
        onClose={() => !clearChatBusy && setClearChatOpen(false)}
        onConfirm={confirmClearChat}
        title={t("settings.dialogs.clearChatTitle")}
        description={t("settings.dialogs.clearChatDescription")}
        facts={
          chatHistoryCounts
            ? [
                { label: t("settings.dialogs.messages"), value: chatHistoryCounts.messages || 0 },
                { label: t("settings.dialogs.sessions"), value: chatHistoryCounts.sessions || 0 },
                {
                  label: t("settings.dialogs.cachedFiles"),
                  value:
                    (chatHistoryCounts.context_files || 0) +
                    (chatHistoryCounts.conversation_files || 0),
                },
              ]
            : []
        }
        keeps={t("settings.dialogs.clearChatKeeps")}
        confirmLabel={t("settings.dialogs.clearChatConfirm")}
        busy={clearChatBusy}
      />
      <ConfirmActionDialog
        open={clearMemoriesOpen}
        onClose={() => !clearMemoriesBusy && setClearMemoriesOpen(false)}
        onConfirm={confirmClearMemories}
        title={t("settings.dialogs.clearMemoryTitle")}
        description={t("settings.dialogs.clearMemoryDescription")}
        facts={
          memoryCount !== null
            ? [{ label: t("settings.dialogs.activeMemories"), value: memoryCount }]
            : []
        }
        keeps={t("settings.dialogs.clearMemoryKeeps")}
        confirmLabel={t("settings.dialogs.clearMemoryConfirm")}
        busy={clearMemoriesBusy}
      />
      <ConfirmActionDialog
        open={learningClear !== null}
        onClose={() => !learningBusy && setLearningClear(null)}
        onConfirm={confirmClearLearning}
        title={
          learningClear === "rules"
            ? t("settings.dialogs.clearRulesTitle")
            : t("settings.dialogs.clearLogTitle")
        }
        description={
          learningClear === "rules"
            ? t("settings.dialogs.clearRulesDescription")
            : t("settings.dialogs.clearLogDescription")
        }
        keeps={
          learningClear === "rules"
            ? t("settings.dialogs.clearRulesKeeps")
            : t("settings.dialogs.clearLogKeeps")
        }
        confirmLabel={
          learningClear === "rules" ? t("settings.dialogs.clearRulesConfirm") : t("settings.dialogs.clearLogConfirm")
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
        onClose={() => {
          setKillSwitchOpen(false);
          // The kill switch turns web access off; show the switch as it is now.
          apiService
            .getWebAccess()
            .then((result) => {
              const allowWeb = result?.data?.allow_web_search ?? result?.allow_web_search;
              if (typeof allowWeb === "boolean") setWebSearchEnabled(allowWeb);
            })
            .catch((err) => console.warn("Failed to refresh web access:", err));
        }}
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
        onClose={() => {
          setVoiceModelsModalOpen(false);
          // Clears the voice settings' install prompt once the model is in.
          loadVoiceConfiguration();
        }}
        showMessage={showMessage}
      />
      <AudioFoundryModelsModal
        open={audioModelsModalOpen}
        onClose={() => setAudioModelsModalOpen(false)}
        showMessage={showMessage}
      />
      <TrainingLibrariesModal
        open={trainingLibrariesModalOpen}
        onClose={() => setTrainingLibrariesModalOpen(false)}
        showMessage={showMessage}
      />
    </PageLayout>
  );
};

export default SettingsPage;
