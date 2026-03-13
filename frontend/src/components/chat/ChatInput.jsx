// frontend/src/components/chat/ChatInput.jsx
// Version 2.0: Unified API service integration
import AttachFileIcon from "@mui/icons-material/AttachFile";
import CloseIcon from "@mui/icons-material/Close";
import SendIcon from "@mui/icons-material/Send";
import StopIcon from "@mui/icons-material/Stop";
import {
  Alert,
  Box,
  Card,
  CardMedia,
  Chip,
  IconButton,
  TextField,
  Tooltip,
} from "@mui/material";
import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

// Voice mode driven by wakeWordEnabled setting
import * as apiService from "../../api";
import VoiceChatButton from "../voice/VoiceChatButton";
import ContinuousVoiceChat from "../voice/ContinuousVoiceChat";
import { useAppStore } from "../../stores/useAppStore";
import { useVoiceSettings } from "../../hooks/useVoiceSettings";

const WEB_SEARCH_ENABLED_KEY = "llamaX1_webSearchEnabled";

const fetchDuckDuckGoSnippet = async (query) => {
  try {
    const url = `https://api.duckduckgo.com/?q=${encodeURIComponent(
      query
    )}&format=json&no_redirect=1&no_html=1`;
    const response = await fetch(url);
    if (!response.ok) throw new Error("DuckDuckGo request failed");
    const data = await response.json();
    // Prefer AbstractText, fallback to first RelatedTopics
    if (data.AbstractText) return data.AbstractText;
    if (Array.isArray(data.RelatedTopics) && data.RelatedTopics.length > 0) {
      const first = data.RelatedTopics[0];
      if (typeof first === "object" && first.Text) return first.Text;
    }
    return "No relevant snippet found.";
  } catch (err) {
    return `Web search failed: ${err.message}`;
  }
};

const analyzeWebsite = async (url) => {
  try {
    // Normalize URL
    if (!url.startsWith("http")) {
      url = "https://" + url;
    }

    const response = await fetch(url, {
      method: "GET",
      headers: {
        "User-Agent": "Mozilla/5.0 (compatible; LlamaX-WebAnalyzer/1.0)",
      },
    });

    if (!response.ok)
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);

    const html = await response.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");

    // Extract basic site info
    const title = doc.querySelector("title")?.textContent?.trim() || "No title";
    const description =
      doc.querySelector('meta[name="description"]')?.getAttribute("content") ||
      "No description";
    const keywords =
      doc.querySelector('meta[name="keywords"]')?.getAttribute("content") ||
      "No keywords";

    // Extract navigation links
    const navLinks = Array.from(
      doc.querySelectorAll("nav a, .nav a, .navigation a, header a")
    )
      .map((a) => ({ text: a.textContent?.trim(), href: a.href }))
      .filter((link) => link.text && link.href)
      .slice(0, 10);

    // Extract main content areas
    const mainContent =
      doc
        .querySelector("main, .main, .content, #content")
        ?.textContent?.trim()
        .substring(0, 500) || "No main content found";

    // Look for sitemap
    const sitemapLink =
      doc.querySelector('link[rel="sitemap"]')?.getAttribute("href") ||
      doc.querySelector('a[href*="sitemap"]')?.getAttribute("href");

    return {
      url,
      title,
      description,
      keywords,
      navLinks,
      mainContent: mainContent + (mainContent.length >= 500 ? "..." : ""),
      sitemapUrl: sitemapLink ? new URL(sitemapLink, url).href : null,
    };
  } catch (err) {
    return { error: `Website analysis failed: ${err.message}` };
  }
};

const analyzeSitemap = async (url) => {
  try {
    // Normalize URL
    if (!url.startsWith("http")) {
      url = "https://" + url;
    }

    const response = await fetch(url, {
      method: "GET",
      headers: {
        "User-Agent": "Mozilla/5.0 (compatible; LlamaX-SitemapAnalyzer/1.0)",
      },
    });

    if (!response.ok)
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);

    const xml = await response.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(xml, "text/xml");

    // Check for XML parsing errors
    const parseError = doc.querySelector("parsererror");
    if (parseError) throw new Error("Invalid XML format");

    // Extract URLs from sitemap
    const urls = Array.from(doc.querySelectorAll("url"))
      .map((url) => {
        const loc = url.querySelector("loc")?.textContent;
        const lastmod = url.querySelector("lastmod")?.textContent;
        const changefreq = url.querySelector("changefreq")?.textContent;
        const priority = url.querySelector("priority")?.textContent;

        return { loc, lastmod, changefreq, priority };
      })
      .filter((url) => url.loc);

    // If no <url> tags found, try alternative sitemap formats
    if (urls.length === 0) {
      const sitemapIndex = Array.from(doc.querySelectorAll("sitemap")).map(
        (sitemap) => {
          const loc = sitemap.querySelector("loc")?.textContent;
          const lastmod = sitemap.querySelector("lastmod")?.textContent;
          return { loc, lastmod, type: "sitemap" };
        }
      );

      if (sitemapIndex.length > 0) {
        return {
          type: "sitemap_index",
          sitemaps: sitemapIndex,
          totalSitemaps: sitemapIndex.length,
        };
      }
    }

    // Analyze URL patterns for landing page insights
    const urlPatterns = urls.map((url) => {
      const path = new URL(url.loc).pathname;
      const segments = path.split("/").filter((s) => s);
      return {
        url: url.loc,
        path,
        segments,
        depth: segments.length,
        lastmod: url.lastmod,
        priority: url.priority,
      };
    });

    // Group by depth and find landing pages
    const byDepth = {};
    urlPatterns.forEach((pattern) => {
      if (!byDepth[pattern.depth]) byDepth[pattern.depth] = [];
      byDepth[pattern.depth].push(pattern);
    });

    // Identify potential landing pages (depth 1, high priority)
    const landingPages = urlPatterns
      .filter((p) => p.depth === 1 && parseFloat(p.priority || 0) > 0.5)
      .slice(0, 5);

    return {
      type: "sitemap",
      totalUrls: urls.length,
      urls: urls.slice(0, 20), // Limit to first 20 for readability
      urlPatterns: urlPatterns.slice(0, 20),
      byDepth,
      landingPages,
      sitemapUrl: url,
    };
  } catch (err) {
    return { error: `Sitemap analysis failed: ${err.message}` };
  }
};

const ChatInput = forwardRef(
  ({ onSendMessage, onStop, disabled = false, sessionId = "default", codeGenMode = false, onVoiceStateChange = () => { } }, ref) => {
    const [inputText, setInputText] = useState("");
    const fileRef = useRef(null);
    const inputRef = useRef(null);

    const systemName = useAppStore((s) => s.systemName);
    const voiceSettings = useVoiceSettings();
    const wakeWordEnabled = voiceSettings.wakeWordEnabled || false;

    // Voice state for parent component
    const [voiceState, setVoiceState] = useState({
      isListening: false,
      isUserSpeaking: false,
      isAISpeaking: false,
      audioLevels: [],
    });

    // Show initial mode status on component mount only
    useEffect(() => {
      if (window.showMessage) {
        setTimeout(() => {
          // Show initial status for Universal RAG (no specific mode active)
          window.showMessage(
            `Chat interface ready - **UNIVERSAL RAG** active (all data accessible, no mode restrictions)`,
            "info"
          );
        }, 1000); // Delay to ensure other startup messages are shown first
      }
    }, []); // Only run on mount

    // File upload state
    const [fileUploadState, setFileUploadState] = useState({
      uploading: false,
      progress: 0,
      fileName: null,
      error: null,
    });

    // Image upload and paste state (supports multiple images)
    const MAX_IMAGES = 4;
    const [imageState, setImageState] = useState({
      images: [],        // Array of { file, preview, id }
      analyzing: false,
      error: null,
    });
    // Backwards-compatible getters for single-image code paths
    const _selectedImage = imageState.images.length > 0 ? imageState.images[0].file : null;
    const _imagePreview = imageState.images.length > 0 ? imageState.images[0].preview : null;

    // Voice chat error state
    const [voiceError, setVoiceError] = useState(null);

    useImperativeHandle(ref, () => ({
      focus: () => {
        inputRef.current?.focus();
      },
    }));

    // Auto-send timeout tracking to prevent race conditions
    const autoSendTimeoutRef = useRef(null);
    const lastAutoSendRef = useRef(null);

    // Voice chat button ref for state access
    const voiceChatButtonRef = useRef(null);

    // Propagate voice state changes to parent
    useEffect(() => {
      onVoiceStateChange(voiceState);
    }, [voiceState, onVoiceStateChange]);

    // Handle voice state updates from VoiceChatButton
    const handleVoiceStateUpdate = useCallback((state) => {
      setVoiceState(prev => ({
        ...prev,
        isListening: state.isRecording || false,
        isUserSpeaking: state.speechDetected || (state.volume > 0.1) || false,
        audioLevels: state.audioLevels || [],
      }));
    }, []);

    // Handle voice transcription received - wrapped in useCallback for stability
    const handleTranscriptionReceived = useCallback((transcriptionData) => {
      console.log("CHAT_DEBUG: ChatInput received transcription data:", {
        hasText: !!transcriptionData?.text,
        hasUserMessage: !!transcriptionData?.userMessage,
        hasAiResponse: !!transcriptionData?.aiResponse,
        isVoiceStream: transcriptionData?.isVoiceStream
      });

      // Clear any pending auto-send to prevent race conditions
      if (autoSendTimeoutRef.current) {
        console.log("CHAT_DEBUG: Clearing previous auto-send timeout");
        clearTimeout(autoSendTimeoutRef.current);
        autoSendTimeoutRef.current = null;
      }

      if (transcriptionData && transcriptionData.text) {
        // Legacy format - just text transcription - send immediately
        console.log("CHAT_DEBUG: Processing legacy text transcription - sending immediately");
        setInputText(transcriptionData.text);

        // Send immediately without timeout to prevent duplicates
        setTimeout(() => {
          console.log("CHAT_DEBUG: Sending legacy transcription immediately");
          // Use onSendMessage directly for legacy format
          onSendMessage(transcriptionData.text, null);
        }, 100); // Minimal delay to ensure state update
      } else if (transcriptionData && transcriptionData.userMessage) {
        // New voice stream format - includes user message and AI response
        console.log("CHAT_DEBUG: Processing voice stream response:", {
          userMessage: transcriptionData.userMessage?.substring(0, 50) + "...",
          hasAiResponse: !!transcriptionData.aiResponse,
          aiResponseLength: transcriptionData.aiResponse?.length || 0,
          isVoiceStream: transcriptionData.isVoiceStream,
        });

        if (transcriptionData.isVoiceStream && transcriptionData.aiResponse) {
          // Voice stream with pre-generated AI response - send directly to chat
          console.log(
            "CHAT_DEBUG: Sending voice stream with AI response directly to chat"
          );

          // Send the user message and AI response as a voice stream
          onSendMessage(
            transcriptionData.userMessage,
            null, // no file
            {
              isVoiceMessage: true,
              aiResponse: transcriptionData.aiResponse,
            }
          );
        } else if (transcriptionData.isVoiceStream) {
          // Voice stream without AI response - send to backend for processing
          console.log(
            "CHAT_DEBUG: Sending voice stream without AI response to backend"
          );

          // Send the user message to backend for processing (no auto-send timeout)
          onSendMessage(
            transcriptionData.userMessage,
            null, // no file
            {
              isVoiceMessage: true,
              // No aiResponse - backend will generate response
            }
          );
        } else {
          // Regular transcription - send immediately
          console.log("CHAT_DEBUG: Processing regular transcription - sending immediately");
          setInputText(transcriptionData.userMessage);

          // Send immediately without timeout to prevent duplicates
          setTimeout(() => {
            console.log("CHAT_DEBUG: Sending regular transcription immediately");
            onSendMessage(transcriptionData.userMessage, null);
          }, 100); // Minimal delay to ensure state update
        }
      } else {
        console.warn(
          "CHAT_DEBUG: Invalid transcription data format:",
          transcriptionData
        );
      }
    }, [onSendMessage, setInputText]);

    // Bridge ContinuousVoiceChat's onMessageReceived to the existing voice pipeline.
    // When response is null, the message flows through the normal streaming chat path.
    const handleContinuousVoiceMessage = useCallback(({ transcription, response }) => {
      if (!transcription || !transcription.trim()) return;
      if (response) {
        // Pre-computed response (legacy path)
        onSendMessage(transcription.trim(), null, {
          isVoiceMessage: true,
          aiResponse: response,
          skipTTS: true,
        });
      } else {
        // Transcription only — send through normal chat pipeline for streaming
        onSendMessage(transcription.trim(), null);
      }
    }, [onSendMessage]);

    // Handle continuous voice state for BackgroundWaveform
    const handleContinuousVoiceStateChange = useCallback((state) => {
      setVoiceState(prev => ({
        ...prev,
        isListening: state.isListening || false,
        isUserSpeaking: state.speechDetected || false,
        audioLevels: state.audioLevels || [],
      }));
    }, []);

    // Enhanced file upload handler using unified API service
    const handleFileUpload = async (file) => {
      console.log("Starting file upload:", file.name, file.size);

      setFileUploadState({
        uploading: true,
        progress: 0,
        fileName: file.name,
        error: null,
      });

      try {
        // Add session ID to tags for proper file association
        const extension = "." + file.name.split(".").pop().toLowerCase();
        const tags = `chat-upload,file-upload,${sessionId}`;

        // Upload file using unified API service
        const result = await apiService.uploadFile(
          file,
          null, // projectId
          tags,
          {},
          null, // signal
          (progressData) => {
            setFileUploadState((prev) => ({
              ...prev,
              progress: progressData.percentage,
            }));
          }
        );

        if (result.error) {
          throw new Error(result.error);
        }

        console.log("Upload result:", result);

        // Enhanced file storage: Check if this is a code file
        const fileType = file.name.split(".").pop().toLowerCase();
        const codeFileExtensions = [
          ".js",
          ".jsx",
          ".ts",
          ".tsx",
          ".py",
          ".java",
          ".cpp",
          ".c",
          ".h",
          ".hpp",
          ".cs",
          ".php",
          ".rb",
          ".go",
          ".rs",
          ".swift",
          ".kt",
          ".scala",
          ".sh",
          ".bash",
          ".sql",
          ".css",
          ".scss",
          ".sass",
          ".html",
          ".htm",
          ".xml",
          ".json",
          ".yaml",
          ".yml",
          ".vue",
          ".svelte",
          ".dart",
          ".r",
          ".lua",
        ];
        const isCodeFile = codeFileExtensions.includes("." + fileType);

        setFileUploadState((prev) => ({
          ...prev,
          progress: 75,
          fileName: file.name,
          error: null,
        }));

        // Wait for indexing to complete before sending chat message
        let indexingComplete = false;
        let attempts = 0;
        const maxAttempts = 30; // 30 seconds max wait

        while (!indexingComplete && attempts < maxAttempts) {
          await new Promise((resolve) => setTimeout(resolve, 1000)); // Wait 1 second
          attempts++;

          try {
            // Check document indexing status
            const statusResponse = await fetch(
              `/api/docs/${result.document_id}`
            );
            if (statusResponse.ok) {
              const docData = await statusResponse.json();
              if (
                docData.index_status === "INDEXED" ||
                docData.index_status === "STORED"
              ) {
                indexingComplete = true;
                break;
              } else if (docData.index_status === "ERROR") {
                console.warn("Document indexing failed");
                break;
              }
            }
          } catch (error) {
            console.warn("Error checking indexing status:", error);
          }

          // Update progress to show we're waiting
          setFileUploadState((prev) => ({
            ...prev,
            progress: 75 + (attempts / maxAttempts) * 20,
            fileName: `${file.name} (indexing...)`,
          }));
        }

        setFileUploadState((prev) => ({ ...prev, progress: 100 }));

        // Enhanced success message based on file type and indexing status
        const fileSizeKB = (file.size / 1024).toFixed(1);
        const indexingStatus = indexingComplete
          ? "Uploaded and indexed successfully"
          : "Uploaded (indexing in progress)";

        let uploadMessage;
        if (codeGenMode && isCodeFile && indexingComplete) {
          // For CodeGen mode, send processing request instead of notification
          uploadMessage = `/codegen

Please analyze and refactor the uploaded code file: ${file.name}

Requirements:
- Analyze the complete file content (${fileSizeKB} KB)
- Provide clean, refactored code only (no commentary)
- Maintain all functionality while improving code structure
- Fix any obvious issues or inefficiencies

Document ID: ${result.document_id}`;
        } else if (codeGenMode && !indexingComplete) {
          // If indexing not complete in CodeGen mode, inform user to wait
          uploadMessage = `**File Upload Complete - Indexing in Progress**

Please wait for indexing to complete before processing. File: ${file.name} (${fileSizeKB} KB)`;
        } else if (isCodeFile) {
          // Regular notification for non-CodeGen mode
          uploadMessage = `**Code File Uploaded Successfully**

**File Details:**
- **Name:** ${file.name}
- **Type:** ${fileType.toUpperCase()} (Code File)
- **Size:** ${fileSizeKB} KB
- **Document ID:** ${result.document_id || "N/A"}

**Status:** ${indexingStatus}
**Enhanced Analysis:** Code content is ${indexingComplete ? "now" : "being"
            } indexed and ${indexingComplete ? "available" : "will be available"
            } for search and discussion.

${indexingComplete
              ? "You can ask questions about this code file and I'll analyze the complete content!"
              : "Please wait a moment for indexing to complete, then ask questions about the code."
            }`;
        } else {
          uploadMessage = `**Document Uploaded Successfully**

**File Details:**
- **Name:** ${file.name}
- **Type:** ${fileType.toUpperCase()}
- **Size:** ${fileSizeKB} KB
- **Document ID:** ${result.document_id || "N/A"}

**Status:** ${indexingStatus}
**RAG Integration:** The document is ${indexingComplete ? "now" : "being"
            } indexed and ${indexingComplete ? "available" : "will be available"
            } for search and context retrieval.`;
        }

        // Send message to chat
        onSendMessage(uploadMessage, null);

        // Clear file input
        if (fileRef.current) {
          fileRef.current.value = "";
        }

        console.log("File upload process completed successfully");
      } catch (error) {
        console.error("File upload failed:", error);

        setFileUploadState({
          uploading: false,
          progress: 0,
          fileName: null,
          error: error.message,
        });

        // Send error message to chat
        const errorMessage = `**File Upload Failed**

**File:** ${file.name}
**Error:** ${error.message}

Please try uploading the file again or contact support if the issue persists.`;

        onSendMessage(errorMessage, null);
      } finally {
        // Reset upload state after delay
        setTimeout(() => {
          setFileUploadState({
            uploading: false,
            progress: 0,
            fileName: null,
            error: null,
          });
        }, 2000);
      }
    };

    // Supported file types for chat analysis
    const supportedFileTypes = {
      // Programming files
      ".py": "Python",
      ".js": "JavaScript",
      ".jsx": "React JSX",
      ".ts": "TypeScript",
      ".tsx": "TypeScript React",
      ".html": "HTML",
      ".css": "CSS",
      ".scss": "SCSS",
      ".json": "JSON",
      ".xml": "XML",
      ".yaml": "YAML",
      ".yml": "YAML",
      ".toml": "TOML",
      ".ini": "INI Config",
      ".conf": "Config",
      ".env": "Environment",

      // Data files
      ".csv": "CSV Data",
      ".xlsx": "Excel",
      ".xls": "Excel",

      // Documents
      ".pdf": "PDF Document",
      ".txt": "Text File",
      ".md": "Markdown",
      ".rst": "ReStructuredText",

      // Other common formats
      ".sql": "SQL",
      ".sh": "Shell Script",
      ".bat": "Batch File",
      ".ps1": "PowerShell",
      ".dockerfile": "Dockerfile",
      ".gitignore": "Git Ignore",
      ".gitattributes": "Git Attributes",
    };

    // File selection handler
    const handleFileSelect = (event) => {
      const file = event.target.files?.[0];
      if (file) {
        console.log("File selected:", file.name, file.size);

        // Check if it's an image first
        if (file.type.startsWith("image/")) {
          handleImageUpload(file);
          return;
        }

        // Validate file size (100MB limit)
        const maxSize = 100 * 1024 * 1024;
        if (file.size > maxSize) {
          const errorMessage = `**File Too Large**

**File:** ${file.name}
**Size:** ${(file.size / 1024 / 1024).toFixed(1)} MB
**Limit:** 100 MB

Please select a smaller file or compress the file before uploading.`;

          onSendMessage(errorMessage, null);
          return;
        }

        // Validate file type
        const extension = "." + file.name.split(".").pop().toLowerCase();
        const isSupported =
          supportedFileTypes[extension] || file.type.startsWith("text/");

        if (!isSupported) {
          const supportedTypes = Object.keys(supportedFileTypes).join(", ");
          const errorMessage = `**Unsupported File Type**

**File:** ${file.name}
**Type:** ${extension}
**Supported Types:** ${supportedTypes}, images

Please select a supported file type.`;

          onSendMessage(errorMessage, null);
          return;
        }

        // Start upload process
        handleFileUpload(file);
      }
    };

    // Handle voice errors
    const handleVoiceError = (error) => {
      console.error("ChatInput: Voice error:", error);
      // Convert Error object to string to avoid React rendering issues
      const errorMessage =
        error instanceof Error ? error.message : String(error);
      setVoiceError(errorMessage);

      // Clear error after 5 seconds
      setTimeout(() => {
        setVoiceError(null);
      }, 5000);
    };

    // Cleanup timeouts on unmount
    useEffect(() => {
      return () => {
        if (autoSendTimeoutRef.current) {
          console.log("CHAT_DEBUG: Cleaning up auto-send timeout on unmount");
          clearTimeout(autoSendTimeoutRef.current);
        }
      };
    }, []);

    // Image handling functions
    const handleImageUpload = (file) => {
      if (!file) return;

      // Check if it's an image
      if (!file.type.startsWith("image/")) {
        setImageState((prev) => ({
          ...prev,
          error: "Please select an image file",
        }));
        return;
      }

      // Check file size (20MB limit)
      const maxSize = 20 * 1024 * 1024; // 20MB
      if (file.size > maxSize) {
        setImageState((prev) => ({
          ...prev,
          error: "Image file too large. Maximum size is 20MB.",
        }));
        return;
      }

      // Check max images
      if (imageState.images.length >= MAX_IMAGES) {
        setImageState((prev) => ({
          ...prev,
          error: `Maximum ${MAX_IMAGES} images allowed`,
        }));
        return;
      }

      // Create preview and accumulate
      const reader = new FileReader();
      reader.onload = (e) => {
        setImageState((prev) => ({
          ...prev,
          images: [
            ...prev.images,
            {
              file,
              preview: e.target.result,
              id: `img_${Date.now()}_${Math.random().toString(36).substr(2, 5)}`,
            },
          ],
          error: null,
        }));
      };
      reader.readAsDataURL(file);
    };

    const handleImagePaste = (event) => {
      const items = event.clipboardData?.items;
      if (!items) return;

      for (let i = 0; i < items.length; i++) {
        const item = items[i];
        if (item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file) {
            handleImageUpload(file);
            event.preventDefault();
          }
          break;
        }
      }
    };

    const clearImage = (imageId) => {
      if (imageId) {
        // Remove a specific image
        setImageState((prev) => ({
          ...prev,
          images: prev.images.filter((img) => img.id !== imageId),
          error: null,
        }));
      } else {
        // Clear all images
        setImageState({ images: [], analyzing: false, error: null });
      }
    };

    const analyzeImage = async () => {
      if (imageState.images.length === 0) return;

      const primaryImage = imageState.images[0];
      const useUnified = localStorage.getItem("use_unified_chat") !== "false";

      setImageState((prev) => ({ ...prev, analyzing: true, error: null }));

      try {
        if (useUnified) {
          // Unified chat path: send base64 image through the ReACT loop
          const base64 = primaryImage.preview.split(",")[1];
          const messageText =
            inputText || `Describe this image: ${primaryImage.file.name}`;
          const fileNames = imageState.images.map((img) => img.file.name).join(", ");

          onSendMessage(messageText, null, {
            isImageAnalysis: true,
            imageBase64: base64,
            imageFileName: fileNames,
            imagePreview: primaryImage.preview,
          });

          clearImage();
          setInputText("");
        } else {
          // Legacy vision endpoint path (single image only)
          const formData = new FormData();
          formData.append("image", primaryImage.file);
          formData.append("session_id", sessionId);
          formData.append("message", inputText);

          const response = await fetch("/api/enhanced-chat/vision/analyze", {
            method: "POST",
            body: formData,
          });

          if (!response.ok) {
            throw new Error(`Analysis failed: ${response.status}`);
          }

          const result = await response.json();

          if (result.success) {
            onSendMessage("", null, {
              isImageAnalysis: true,
              imageFileName: primaryImage.file.name,
              analysisResponse: result.response,
              analysisDetails: result.analysis_details,
              imageUrl: result.image_url,
              permanentFileName: result.image_filename,
            });

            clearImage();
            setInputText("");
          } else {
            throw new Error(result.error || "Analysis failed");
          }
        }
      } catch (error) {
        console.error("Image analysis error:", error);

        const errorMessage = `**Vision Analysis Failed**

**Image:** ${primaryImage.file.name}
**Error:** ${error.message}

Please try a different image or check if the vision model is properly loaded.`;
        onSendMessage(errorMessage, null);

        clearImage();
        setInputText("");
      } finally {
        setImageState((prev) => ({ ...prev, analyzing: false }));
      }
    };

    // Add paste event listener
    useEffect(() => {
      const handlePasteEvent = (event) => {
        // Only handle paste if the input is focused or if we're in the chat area
        const activeElement = document.activeElement;
        const isInputFocused = activeElement === inputRef.current;
        const isChatArea =
          activeElement?.closest(".chat-container") || !activeElement;

        if (isInputFocused || isChatArea) {
          handleImagePaste(event);
        }
      };

      document.addEventListener("paste", handlePasteEvent);
      return () => {
        document.removeEventListener("paste", handlePasteEvent);
      };
    }, []);

    const handleSend = async () => {
      // Check if there are images to analyze
      if (imageState.images.length > 0) {
        await analyzeImage();
        return;
      }

      // Fallback: If programmatic input bypassed React state, grab from DOM
      let currentText = inputText;
      if (!currentText && inputRef.current && inputRef.current.value) {
        currentText = inputRef.current.value;
      }

      const file = fileRef.current?.files?.[0] || null;
      if (!currentText.trim() && !file) return;

      // Input validation and sanitization
      const sanitizedInput = currentText.trim();
      const maxLength = 100000; // 100k character limit for file analysis

      if (sanitizedInput.length > maxLength) {
        onSendMessage(
          `Message too long. Please limit to ${maxLength} characters. Current length: ${sanitizedInput.length}`,
          null
        );
        return;
      }

      // /websearch command handling
      if (sanitizedInput.toLowerCase().startsWith("/websearch")) {
        const webSearchEnabled =
          localStorage.getItem(WEB_SEARCH_ENABLED_KEY) === "true";
        if (!webSearchEnabled) {
          onSendMessage("Web search is currently disabled in settings.", null);
          setInputText("");
          return;
        }

        const query = currentText.replace(/^\/websearch\s*/i, "").trim();
        if (!query) {
          onSendMessage(
            "Please provide a search query after /websearch.",
            null
          );
          setInputText("");
          return;
        }

        // Check for special commands
        if (query.toLowerCase().startsWith("site:")) {
          const url = query.replace(/^site:\s*/i, "").trim();
          if (!url) {
            onSendMessage("Please provide a URL after site:", null);
            setInputText("");
            return;
          }
          onSendMessage(`Analyzing website: ${url}`, null);
          setInputText("");
          const analysis = await analyzeWebsite(url);
          if (analysis.error) {
            onSendMessage(`Website Analysis Error: ${analysis.error}`, null);
          } else {
            const report = `Website Analysis for ${analysis.url}:
Title: ${analysis.title}
Description: ${analysis.description}
Keywords: ${analysis.keywords}
Navigation Links: ${analysis.navLinks
                .map((l) => `${l.text} (${l.href})`)
                .join(", ")}
Main Content Preview: ${analysis.mainContent}
Sitemap URL: ${analysis.sitemapUrl || "Not found"}`;
            onSendMessage(`Website Analysis Report:\n${report}`, null);
          }
          return;
        }

        if (query.toLowerCase().startsWith("sitemap:")) {
          const url = query.replace(/^sitemap:\s*/i, "").trim();
          if (!url) {
            onSendMessage("Please provide a sitemap URL after sitemap:", null);
            setInputText("");
            return;
          }
          onSendMessage(`Analyzing sitemap: ${url}`, null);
          setInputText("");
          const analysis = await analyzeSitemap(url);
          if (analysis.error) {
            onSendMessage(`Sitemap Analysis Error: ${analysis.error}`, null);
          } else {
            let report = `Sitemap Analysis for ${analysis.sitemapUrl}:
Type: ${analysis.type}
Total URLs: ${analysis.totalUrls}`;

            if (analysis.type === "sitemap_index") {
              report += `\nSitemap Index with ${analysis.totalSitemaps} sitemaps:`;
              analysis.sitemaps.forEach((sitemap, i) => {
                report += `\n${i + 1}. ${sitemap.loc} (Last modified: ${sitemap.lastmod || "Unknown"
                  })`;
              });
            } else {
              report += `\nLanding Pages (depth 1, high priority):`;
              analysis.landingPages.forEach((page, i) => {
                report += `\n${i + 1}. ${page.url} (Priority: ${page.priority || "Unknown"
                  })`;
              });
              report += `\nURL Structure by Depth:`;
              Object.entries(analysis.byDepth).forEach(([depth, urls]) => {
                report += `\nDepth ${depth}: ${urls.length} URLs`;
              });
            }
            onSendMessage(`Sitemap Analysis Report:\n${report}`, null);
          }
          return;
        }

        // Regular web search
        onSendMessage("Searching the web for: " + query, null);
        setInputText("");
        const snippet = await fetchDuckDuckGoSnippet(query);
        onSendMessage(`Web Search Result: ${snippet}`, null);
        return;
      }

      // Pass intelligent mode to parent (automatic mode selection)
      onSendMessage(currentText, file, { chatMode: "analyze" });
      setInputText("");
      if (fileRef.current) fileRef.current.value = "";
      if (inputRef.current) {
        inputRef.current.value = "";
        inputRef.current.focus();
      }
    };

    const handleKeyPress = (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    };

    return (
      <Box
        sx={{
          p: 2,
          borderTop: 1,
          borderColor: "divider",
          display: "flex",
          flexDirection: "column",
          gap: 1,
        }}
      >
        {/* Voice chat error alert */}
        {voiceError && (
          <Alert
            severity="error"
            sx={{ mb: 1 }}
            onClose={() => setVoiceError(null)}
          >
            {voiceError}
          </Alert>
        )}

        {/* File upload error */}
        {fileUploadState.error && (
          <Alert
            severity="error"
            sx={{ mb: 1 }}
            onClose={() =>
              setFileUploadState((prev) => ({ ...prev, error: null }))
            }
          >
            Upload failed: {fileUploadState.error}
          </Alert>
        )}

        {/* Image preview thumbnails */}
        {imageState.images.length > 0 && (
          <Card sx={{ mb: 1, p: 1.5 }}>
            <Box sx={{ display: "flex", gap: 1, flexWrap: "wrap", alignItems: "center" }}>
              {imageState.images.map((img) => (
                <Box key={img.id} sx={{ position: "relative", width: 80, height: 80 }}>
                  <CardMedia
                    component="img"
                    sx={{
                      width: 80,
                      height: 80,
                      objectFit: "cover",
                      borderRadius: 1,
                      border: "1px solid #e0e0e0",
                    }}
                    image={img.preview}
                    alt={img.file.name}
                  />
                  <IconButton
                    size="small"
                    onClick={() => clearImage(img.id)}
                    disabled={imageState.analyzing}
                    sx={{
                      position: "absolute",
                      top: -8,
                      right: -8,
                      bgcolor: "background.paper",
                      border: "1px solid",
                      borderColor: "divider",
                      width: 20,
                      height: 20,
                      "&:hover": { bgcolor: "error.light", color: "white" },
                    }}
                  >
                    <CloseIcon sx={{ fontSize: 12 }} />
                  </IconButton>
                </Box>
              ))}
              {imageState.images.length < MAX_IMAGES && (
                <Typography variant="caption" color="text.secondary">
                  +{MAX_IMAGES - imageState.images.length} more
                </Typography>
              )}
            </Box>
            <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mt: 1 }}>
              <Chip
                icon={<AttachFileIcon />}
                label={`${imageState.images.length} image${imageState.images.length > 1 ? "s" : ""} selected`}
                size="small"
                variant="outlined"
              />
              <IconButton
                size="small"
                onClick={() => clearImage()}
                disabled={imageState.analyzing}
              >
                <CloseIcon />
              </IconButton>
            </Box>

            {imageState.analyzing && (
              <Box sx={{ mt: 1 }}>
                <Box
                  sx={{
                    color: "text.secondary",
                    fontSize: "0.875rem",
                    mt: 0.5,
                  }}
                >
                  Analyzing image...
                </Box>
              </Box>
            )}
          </Card>
        )}

        {/* Image error */}
        {imageState.error && (
          <Alert
            severity="error"
            sx={{ mb: 1 }}
            onClose={() => setImageState((prev) => ({ ...prev, error: null }))}
          >
            {imageState.error}
          </Alert>
        )}

        <Box sx={{ display: "flex", gap: 1, alignItems: "flex-end" }}>
          <input
            type="file"
            hidden
            ref={fileRef}
            onChange={handleFileSelect}
            accept=".pdf,.txt,.csv,.docx,.md,.json,.py,.js,.jsx,.ts,.tsx,.html,.css,.xml,.yaml,.yml,image/*"
          />

          {/* File attachment button */}
          <Tooltip title="Attach file or image">
            {disabled ? (
              <span>
                <IconButton
                  onClick={() => fileRef.current?.click()}
                  disabled={disabled}
                  sx={{ color: "text.secondary" }}
                >
                  <AttachFileIcon />
                </IconButton>
              </span>
            ) : (
              <IconButton
                onClick={() => fileRef.current?.click()}
                disabled={disabled}
                sx={{ color: "text.secondary" }}
              >
                <AttachFileIcon />
              </IconButton>
            )}
          </Tooltip>

          {/* Voice input: settings-driven (wakeWordEnabled → continuous listener, otherwise push-to-talk) */}
          {wakeWordEnabled ? (
            <ContinuousVoiceChat
              sessionId={sessionId}
              onMessageReceived={handleContinuousVoiceMessage}
              onError={handleVoiceError}
              onStateChange={handleContinuousVoiceStateChange}
              compact={true}
              wakeWordEnabled={true}
              systemName={systemName || 'Guaardvark'}
              onWakeWordDetected={() => {}}
            />
          ) : (
            <VoiceChatButton
              onTranscriptionReceived={handleTranscriptionReceived}
              onError={handleVoiceError}
              onStateChange={handleVoiceStateUpdate}
              disabled={disabled}
              sessionId={sessionId}
              compact
            />
          )}

          {/* Text input field */}
          <TextField
            fullWidth
            size="small"
            placeholder={
              imageState.images.length > 0
                ? "Ask about this image..."
                : "Type your message, paste an image, or use voice..."
            }
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            onKeyPress={handleKeyPress}
            inputRef={inputRef}
            multiline
            disabled={disabled || imageState.analyzing}
            sx={{ minHeight: "40px" }}
          />

          {/* Send button */}
          <Tooltip
            title={
              disabled
                ? "Stop"
                : imageState.analyzing
                  ? "Analyzing image..."
                  : imageState.images.length > 0
                    ? "Analyze image"
                    : "Send message"
            }
          >
            {imageState.analyzing ? (
              <span>
                <IconButton
                  color="primary"
                  onClick={disabled ? onStop : handleSend}
                  disabled={imageState.analyzing} // Disable during analysis
                >
                  {disabled ? <StopIcon /> : <SendIcon />}
                </IconButton>
              </span>
            ) : (
              <IconButton
                color="primary"
                onClick={disabled ? onStop : handleSend}
                disabled={imageState.analyzing} // Disable during analysis
              >
                {disabled ? <StopIcon /> : <SendIcon />}
              </IconButton>
            )}
          </Tooltip>
        </Box>
      </Box>
    );
  }
);

ChatInput.displayName = "ChatInput";

export default ChatInput;