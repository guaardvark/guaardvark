// frontend/src/pages/BatchImageGeneratorPage.jsx
// Batch Image Generator - Mass image generation with progress tracking
// Integrates with unified progress system and real-time updates

import React, { useState, useCallback, useEffect, useRef } from 'react';
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  TextField,
  Grid,
  Chip,
  LinearProgress,
  Alert,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Slider,
  Switch,
  FormControlLabel,
  Paper,
  ImageList,
  ImageListItem,
  ImageListItemBar,
  IconButton,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  useTheme,
  useMediaQuery
} from '@mui/material';
import {
  ExpandMore,
  Upload,
  PlayArrow,
  Download,
  GetApp,
  Visibility,
  Cancel
} from '@mui/icons-material';

import { useUnifiedProgress } from '../contexts/UnifiedProgressContext';
import { useSearchParams, useNavigate } from 'react-router-dom';
import PageLayout from '../components/layout/PageLayout';

const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api';

// Utility function to sanitize text for display
const sanitizeText = (text) => {
  if (!text) return '';
  return text.replace(/[<>&"]/g, (match) => {
    const escape = {
      '<': '&lt;',
      '>': '&gt;',
      '&': '&amp;',
      '"': '&quot;'
    };
    return escape[match];
  });
};

const BatchImageGeneratorPage = ({ embedded = false }) => {
  const theme = useTheme();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const isXs = useMediaQuery(theme.breakpoints.down('sm'));
  const isSm = useMediaQuery(theme.breakpoints.between('sm', 'md'));

  // Calculate responsive columns for ImageList
  const imageListCols = isXs ? 2 : isSm ? 3 : 4;

  // State management
  const [inputMode, setInputMode] = useState('bulk'); // 'bulk', 'csv', or 'blueprint'
  const [batchItems, setBatchItems] = useState(''); // Bulk textarea input like FileGenerationPage
  const [lookAndFeel, setLookAndFeel] = useState(''); // Style/aesthetic to apply to all prompts
  const [csvFile, setCsvFile] = useState(null);
  const [blueprintFile, setBlueprintFile] = useState(null);
  const [quantity, setQuantity] = useState(1); // Number of images to generate
  const [activeBatch, setActiveBatch] = useState(null);
  const [batchHistory, setBatchHistory] = useState([]);
  const [generatedImages, setGeneratedImages] = useState([]);
  const [selectedImage, setSelectedImage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [showPromptPreview, setShowPromptPreview] = useState(false);

  // New: Content presets and quality enhancement state
  const [contentPresets, setContentPresets] = useState({});
  const [selectedPreset, setSelectedPreset] = useState('auto'); // 'auto' = auto-detect
  const [autoEnhance, _setAutoEnhance] = useState(true);
  const [enhanceAnatomy, _setEnhanceAnatomy] = useState(true);
  const [enhanceFaces, _setEnhanceFaces] = useState(true);
  const [enhanceHands, _setEnhanceHands] = useState(true);
  const [_contentDetection, setContentDetection] = useState(null);
  const [_analyzingPrompt, setAnalyzingPrompt] = useState(false);

  // Generation parameters
  const [params, setParams] = useState({
    model: 'sd-1.5',
    style: 'realistic',
    quality_preset: 'standard',
    width: 512,
    height: 512,
    steps: 20,
    guidance: 7.5,
    max_workers: 2,
    preserve_order: true,
    generate_thumbnails: true,
    save_metadata: true
  });

  // UI state
  const [showImageViewer, setShowImageViewer] = useState(false);

  // Refs
  const fileInputRef = useRef(null);
  const blueprintFileInputRef = useRef(null);
  const pollingRef = useRef(null);

  // Progress system integration
  const { activeProcesses } = useUnifiedProgress();

  // Style options
  const styleOptions = [
    { value: 'realistic', label: 'Realistic' },
    { value: 'artistic', label: 'Artistic' },
    { value: 'cartoon', label: 'Cartoon' },
    { value: 'sketch', label: 'Sketch' },
    { value: 'infographic', label: 'Infographic' },
    { value: 'technical', label: 'Technical' }
  ];

  // Model options
  const modelOptions = [
    { value: 'sd-1.5', label: 'SD 1.5 (Standard)', description: 'Reliable baseline, 512x512' },
    { value: 'sd-turbo', label: 'SD Turbo (Fast)', description: 'Super fast generation, 4 steps' },
    { value: 'dreamlike', label: 'Dreamlike Photoreal', description: 'Excellent photorealism' },
    { value: 'deliberate', label: 'Deliberate v2', description: 'Versatile, multiple styles' },
    { value: 'realistic-vision', label: 'Realistic Vision', description: 'Outstanding photorealism' },
    { value: 'epic-realism', label: 'Epic Realism', description: 'Cinematic quality' },
    { value: 'sd-xl', label: 'SD XL Base', description: 'High resolution, 1024x1024' },
    { value: 'sdxl-turbo', label: 'SD XL Turbo', description: 'Fast high resolution' },
    { value: 'openjourney', label: 'OpenJourney', description: 'Artistic, Midjourney-style' },
    { value: 'analog', label: 'Analog Diffusion', description: 'Film photography aesthetic' },
    { value: 'anything-v3', label: 'Anything v3', description: 'Anime/illustration style' }
  ];

  // Quality presets
  const qualityPresets = [
    { value: 'fast', label: 'Fast', steps: 15, guidance: 7.0, description: 'Quick generation, good for testing' },
    { value: 'standard', label: 'Standard', steps: 20, guidance: 7.5, description: 'Balanced quality and speed' },
    { value: 'high', label: 'High Quality', steps: 30, guidance: 8.0, description: 'High quality, slower generation' },
    { value: 'professional', label: 'Professional', steps: 25, guidance: 7.5, description: 'Professional quality for final output' }
  ];

  // Dimension presets
  const dimensionPresets = [
    // SD 1.5 / Standard presets
    { label: 'Square (512x512)', width: 512, height: 512 },
    { label: 'Portrait (512x768)', width: 512, height: 768 },
    { label: 'Landscape (768x512)', width: 768, height: 512 },
    { label: 'Large Square (768x768)', width: 768, height: 768 },
    { label: 'HD Portrait (512x1024)', width: 512, height: 1024 },
    { label: 'HD Landscape (1024x512)', width: 1024, height: 512 },
    // SDXL presets
    { label: 'XL Square (1024x1024)', width: 1024, height: 1024 },
    { label: 'XL Portrait (832x1216)', width: 832, height: 1216 },
    { label: 'XL Landscape (1216x832)', width: 1216, height: 832 },
    { label: 'XL Wide (1344x768)', width: 1344, height: 768 },
    { label: 'XL Tall (768x1344)', width: 768, height: 1344 }
  ];

  // Load service status and presets on mount
  useEffect(() => {
    checkServiceStatus();
    loadBatchHistory();
    loadContentPresets();
  }, []);

  // Analyze current prompt for content detection
  const analyzeCurrentPrompt = useCallback(async (prompt) => {
    if (!prompt) return;

    setAnalyzingPrompt(true);
    try {
      const response = await fetch(`${API_BASE}/batch-image/analyze-prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt })
      });

      if (!response.ok) return;

      const data = await response.json();
      if (data.success && data.data.detection) {
        setContentDetection(data.data.detection);

        // If auto-detect mode, apply recommended settings
        if (selectedPreset === 'auto' && data.data.detection.recommended_preset) {
          const preset = contentPresets[data.data.detection.recommended_preset];
          if (preset) {
            setParams(prev => ({
              ...prev,
              steps: preset.recommended_steps || prev.steps,
              guidance: preset.recommended_guidance || prev.guidance
            }));
          }
        }
      }
    } catch (err) {
      console.error('Failed to analyze prompt:', err);
    } finally {
      setAnalyzingPrompt(false);
    }
  }, [selectedPreset, contentPresets]);

  // Analyze prompt when it changes (debounced)
  useEffect(() => {
    const firstPrompt = batchItems.split('\n').find(line => line.trim());
    if (!firstPrompt || !autoEnhance) {
      setContentDetection(null);
      return;
    }

    const timeoutId = setTimeout(() => {
      analyzeCurrentPrompt(firstPrompt.trim());
    }, 500); // Debounce 500ms

    return () => clearTimeout(timeoutId);
  }, [batchItems, autoEnhance, analyzeCurrentPrompt]);

  const checkServiceStatus = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/status`);

      // Check if response is ok before parsing JSON
      if (!response.ok) {
        setError(`Service status check failed: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        setError('Service status response is not JSON');
        return;
      }

      const data = await response.json();

      if (!data.success) {
        setError('Batch image generation service is not available');
      }
    } catch (err) {
      setError('Failed to check service status');
    }
  }, []);

  // Load content presets from API
  const loadContentPresets = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/presets`);
      if (!response.ok) return;

      const data = await response.json();
      if (data.success && data.data.presets) {
        setContentPresets(data.data.presets);
      }
    } catch (err) {
      console.error('Failed to load content presets:', err);
    }
  }, []);

  // Handle preset selection
  const handlePresetChange = useCallback((presetName) => {
    setSelectedPreset(presetName);

    if (presetName !== 'auto' && contentPresets[presetName]) {
      const preset = contentPresets[presetName];
      setParams(prev => ({
        ...prev,
        steps: preset.recommended_steps || prev.steps,
        guidance: preset.recommended_guidance || prev.guidance,
        width: preset.recommended_dimensions?.[0] || prev.width,
        height: preset.recommended_dimensions?.[1] || prev.height
      }));
    }
  }, [contentPresets]);

  const loadBatchHistory = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/list`);

      // Check if response is ok before parsing JSON
      if (!response.ok) {
        console.error(`Failed to load batch history: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        console.error('Response is not JSON:', contentType);
        return;
      }

      const data = await response.json();

      if (data.success) {
        setBatchHistory(data.data.batches || []);
      }
    } catch (err) {
      console.error('Failed to load batch history:', err);
    }
  }, []);

  const loadBatchById = useCallback(async (batchId) => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);

      if (!response.ok) {
        setError(`Failed to load batch: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        setError('Batch response is not JSON');
        return;
      }

      const data = await response.json();

      if (data.success) {
        const batchStatus = data.data;
        setActiveBatch(batchStatus);

        // Load generated images
        if (batchStatus.results) {
          const images = batchStatus.results
            .filter(r => r.success && r.image_path)
            .map(r => {
              // Extract filename from path for use in API URLs
              const getFilename = (path) => {
                if (!path) return null;
                // Handle both Windows and Unix paths
                const parts = path.replace(/\\/g, '/').split('/');
                return parts[parts.length - 1];
              };

              return {
                id: r.prompt_id,
                path: r.image_path,
                thumbnail: r.thumbnail_path,
                imageFilename: getFilename(r.image_path),
                thumbnailFilename: getFilename(r.thumbnail_path),
                prompt: r.metadata?.original_prompt || '',
                metadata: r.metadata
              };
            });
          setGeneratedImages(images);
        }

        // Start polling if batch is still running (will be handled by the activeBatch useEffect)
        // No need to call startPolling here as the useEffect will handle it
      }
    } catch (err) {
      setError(`Failed to load batch: ${err.message}`);
    }
  }, []);

  const startPolling = useCallback((batchId) => {
    stopPolling();

    pollingRef.current = setInterval(async () => {
      try {
        const response = await fetch(`${API_BASE}/batch-image/status/${batchId}?include_results=true`);

        // Check if component is still mounted and batchId is still current
        if (!pollingRef.current) {
          return;
        }

        if (!response.ok) {
          console.error('Polling error: HTTP', response.status);
          if (response.status === 404) {
            stopPolling();
            setError('Batch not found');
          }
          return;
        }

        const data = await response.json();

        if (data.success) {
          const batchStatus = data.data;

          // Double-check we're still polling and batch ID matches
          if (!pollingRef.current || batchStatus.batch_id !== batchId) {
            return;
          }

          setActiveBatch(batchStatus);

          // Load generated images
          if (batchStatus.results) {
            const images = batchStatus.results
              .filter(r => r.success && r.image_path)
              .map(r => {
                // Extract filename from path for use in API URLs
                const getFilename = (path) => {
                  if (!path) return null;
                  // Handle both Windows and Unix paths
                  const parts = path.replace(/\\/g, '/').split('/');
                  return parts[parts.length - 1];
                };

                return {
                  id: r.prompt_id,
                  path: r.image_path,
                  thumbnail: r.thumbnail_path,
                  imageFilename: getFilename(r.image_path),
                  thumbnailFilename: getFilename(r.thumbnail_path),
                  prompt: r.metadata?.original_prompt || '',
                  metadata: r.metadata
                };
              });
            setGeneratedImages(images);
          }

          // Stop polling if batch is complete
          if (['completed', 'error', 'cancelled'].includes(batchStatus.status)) {
            stopPolling();
            setSuccess(`Batch generation ${batchStatus.status}`);
            loadBatchHistory();
          }
        }
      } catch (err) {
        console.error('Polling error:', err);
        // Don't stop polling on network errors, just log them
      }
    }, 2000);
  }, []);

  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  // Load specific batch if batch_id is in URL params (from ContentLibraryPage)
  useEffect(() => {
    const batchId = searchParams.get('batch');
    if (batchId) {
      loadBatchById(batchId);
    }
  }, [searchParams]);

  // Monitor active batch progress
  useEffect(() => {
    if (activeBatch && activeBatch.status === 'running') {
      startPolling(activeBatch.batch_id);
    } else {
      stopPolling();
    }

    return () => {
      // Cleanup function to prevent race conditions
      stopPolling();
    };
  }, [activeBatch]);

  // Monitor progress system for batch updates
  const completionHandledRef = useRef(null);
  useEffect(() => {
    const batchProcesses = Array.from(activeProcesses.values()).filter(
      process => (process.processType === 'image_generation' || process.process_type === 'image_generation') &&
        process.additional_data?.batch_id
    );

    if (batchProcesses.length > 0 && activeBatch) {
      const batchProcess = batchProcesses.find(
        p => p.additional_data.batch_id === activeBatch.batch_id
      );

      if (batchProcess) {
        // Update active batch with progress info from SocketIO
        setActiveBatch(prev => prev ? {
          ...prev,
          completed_images: batchProcess.additional_data.completed || prev.completed_images,
          progress_percentage: batchProcess.progress || prev.progress_percentage
        } : null);

        // On completion/error/cancel, do an immediate final poll to get all results
        if (['complete', 'end', 'error', 'cancelled'].includes(batchProcess.status) &&
            completionHandledRef.current !== activeBatch.batch_id) {
          completionHandledRef.current = activeBatch.batch_id;
          loadBatchById(activeBatch.batch_id);
          loadBatchHistory();
        }
      }
    }
  }, [activeProcesses, activeBatch, loadBatchById]);

  const handleBatchItemsChange = (event) => {
    setBatchItems(event.target.value);
  };

  const parseBatchItems = () => {
    // Split by lines and filter out empty lines
    const topics = batchItems
      .split('\n')
      .map(item => item.trim())
      .filter(item => item.length > 0);

    // If look & feel is provided, combine it with each topic
    if (lookAndFeel.trim()) {
      return topics.map(topic => `${topic}, ${lookAndFeel.trim()}`);
    }

    return topics;
  };

  // Handle quality preset changes
  const handleQualityPresetChange = (presetValue) => {
    const preset = qualityPresets.find(p => p.value === presetValue);
    if (preset) {
      setParams(prev => ({
        ...prev,
        quality_preset: presetValue,
        steps: preset.steps,
        guidance: preset.guidance
      }));
    }
  };

  // Handle model changes
  const handleModelChange = (modelValue) => {
    setParams(prev => {
      let newParams = { ...prev, model: modelValue };

      // Adjust dimensions based on model capabilities
      if (modelValue.includes('xl') || modelValue === 'sd-xl' || modelValue === 'sdxl-turbo') {
        newParams.width = 1024;
        newParams.height = 1024;
      } else if (modelValue === 'sd-turbo') {
        newParams.width = 512;
        newParams.height = 512;
        newParams.steps = 4; // Turbo models work best with 1-4 steps
        newParams.guidance = 7.0;
      } else {
        newParams.width = 512;
        newParams.height = 512;
      }

      return newParams;
    });
  };

  const handleFileUpload = (event) => {
    const file = event.target.files[0];
    if (file && file.type === 'text/csv') {
      setCsvFile(file);
      setError('');
    } else {
      setError('Please select a valid CSV file');
      setCsvFile(null);
    }
  };

  const handleBlueprintFileUpload = (event) => {
    const file = event.target.files[0];
    const isCsv = file && (file.type === 'text/csv' || file.name.toLowerCase().endsWith('.csv'));
    if (isCsv) {
      setBlueprintFile(file);
      setError('');
    } else {
      setError('Please select a valid CSV file for blueprints');
      setBlueprintFile(null);
    }
  };

  const downloadTemplate = async () => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/template`);

      if (!response.ok) {
        setError(`Failed to download template: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (contentType && !contentType.includes('text/csv') && !contentType.includes('application/octet-stream')) {
        setError('Template response is not a valid CSV file');
        return;
      }

      const blob = await response.blob();

      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'batch_generation_template.csv';
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      setError('Failed to download template: ' + err.message);
    }
  };

  const startGeneration = async () => {
    setLoading(true);
    setError('');
    setGeneratedImages([]);
    setSuccess('');

    try {
      let response;

      if (inputMode === 'blueprint') {
        if (!blueprintFile) {
          setError('Please select a CSV file for blueprint generation');
          setLoading(false);
          return;
        }

        const formData = new FormData();
        formData.append('file', blueprintFile);

        response = await fetch(`${API_BASE}/batch-image/generate/blueprints`, {
          method: 'POST',
          body: formData
        });
      } else if (inputMode === 'csv' && csvFile) {
        // CSV upload
        const formData = new FormData();
        formData.append('file', csvFile);

        // Add parameters
        Object.entries(params).forEach(([key, value]) => {
          formData.append(key, value.toString());
        });

        response = await fetch(`${API_BASE}/batch-image/generate/csv`, {
          method: 'POST',
          body: formData
        });
      } else if (inputMode === 'csv' && !csvFile) {
        setError('Please select a CSV file for upload');
        setLoading(false);
        return;
      } else {
        // Bulk input
        const validPrompts = parseBatchItems();
        if (validPrompts.length === 0) {
          setError('Please provide at least one prompt or topic');
          setLoading(false);
          return;
        }

        // Duplicate prompts based on quantity
        let promptsToGenerate = validPrompts;
        if (quantity > 1) {
          promptsToGenerate = [];
          validPrompts.forEach(prompt => {
            for (let i = 0; i < quantity; i++) {
              promptsToGenerate.push(`${prompt} (${i + 1})`);
            }
          });
        }

        console.log('--- DEBUG: PROMPTS BEING SENT TO BACKEND:', promptsToGenerate);

        response = await fetch(`${API_BASE}/batch-image/generate/prompts`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompts: promptsToGenerate,
            ...params,
            // Quality enhancement parameters
            content_preset: selectedPreset === 'auto' ? null : selectedPreset,
            auto_enhance: autoEnhance,
            enhance_anatomy: enhanceAnatomy,
            enhance_faces: enhanceFaces,
            enhance_hands: enhanceHands
          })
        });
      }

      // Check if response is ok before parsing JSON
      if (!response.ok) {
        // Try to parse error response
        let errorMessage = `Failed to start generation: HTTP ${response.status}`;
        try {
          const errorData = await response.json();
          if (errorData.error) {
            if (typeof errorData.error === 'object' && errorData.error.message) {
              errorMessage = errorData.error.message;
            } else if (typeof errorData.error === 'string') {
              errorMessage = errorData.error;
            }
          } else if (errorData.message) {
            errorMessage = errorData.message;
          }
        } catch (e) {
          // If JSON parsing fails, use status text
          errorMessage = `Failed to start generation: HTTP ${response.status} ${response.statusText}`;
        }
        setError(errorMessage);
        setLoading(false);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        const text = await response.text();
        setError(`Generation response is not JSON. Response: ${text.substring(0, 200)}`);
        setLoading(false);
        return;
      }

      const data = await response.json();

      if (data.success) {
        const batchId = data.data?.batch_id;
        if (!batchId) {
          setError('Batch generation started but no batch ID returned');
          setLoading(false);
          return;
        }

        if (inputMode === 'blueprint') {
          // Blueprint runs in background (avoids timeout for 1000+ rows); poll like CSV/bulk
          setActiveBatch({
            batch_id: batchId,
            status: 'running',
            total_images: data.data?.total_images ?? 0,
            completed_images: 0,
            failed_images: 0,
            progress_percentage: 0
          });
          setSuccess(data.data?.message || 'Blueprint batch started. Results will appear as generation completes.');
          if (batchId) {
            startPolling(batchId);
          }
        } else {
          setActiveBatch({
            batch_id: batchId,
            status: 'running',
            // promptsToGenerate is scoped to the bulk submit branch above and out of scope here;
            // 0 falls back to indeterminate progress which is fine.
            total_images: data.data.prompt_count || data.data.total_images || 0,
            completed_images: 0,
            failed_images: 0,
            progress_percentage: 0
          });
          setSuccess('Batch generation started successfully');

          // Start polling for status updates
          if (batchId) {
            startPolling(batchId);
          }
        }
      } else {
        const errorMsg = data.error?.message || data.error || data.message || 'Failed to start generation';
        setError(errorMsg);
      }
    } catch (err) {
      console.error('Generation error:', err);
      setError('Failed to start generation: ' + (err.message || String(err)));
    } finally {
      setLoading(false);
    }
  };

  const cancelGeneration = async () => {
    if (!activeBatch) return;

    try {
      const response = await fetch(`${API_BASE}/batch-image/cancel/${activeBatch.batch_id}`, {
        method: 'POST'
      });

      if (!response.ok) {
        setError(`Failed to cancel generation: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('application/json')) {
        setError('Cancel response is not JSON');
        return;
      }

      const data = await response.json();
      if (data.success) {
        setSuccess('Batch generation cancelled');
        stopPolling();
        setActiveBatch(prev => prev ? { ...prev, status: 'cancelled' } : null);
      } else {
        setError(data.error || 'Failed to cancel generation');
      }
    } catch (err) {
      setError('Failed to cancel generation: ' + err.message);
    }
  };

  const downloadResults = async (batchId) => {
    try {
      const response = await fetch(`${API_BASE}/batch-image/download/${batchId}`);

      if (!response.ok) {
        setError(`Failed to download results: HTTP ${response.status}`);
        return;
      }

      const contentType = response.headers.get('content-type');
      if (contentType && !contentType.includes('application/zip') && !contentType.includes('application/octet-stream')) {
        setError('Download response is not a valid ZIP file');
        return;
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `batch_${batchId}_results.zip`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      setError('Failed to download results: ' + err.message);
    }
  };

  const openImageViewer = (image) => {
    setSelectedImage(image);
    setShowImageViewer(true);
  };

  return (
    <PageLayout title={embedded ? undefined : "Image Generator"} variant={embedded ? "fullscreen" : "standard"} noPadding={embedded}>

      {/* Error/Success Messages */}
      {error && (
        <Alert severity="error" sx={{ mb: 3 }} onClose={() => setError('')}>
          {error}
        </Alert>
      )}

      {success && (
        <Alert severity="success" sx={{ mb: 3 }} onClose={() => setSuccess('')}>
          {success}
        </Alert>
      )}

      <Grid container spacing={3}>
        {/* Input Section */}
        <Grid item xs={12} lg={6}>
          <Card sx={{
            height: 'fit-content',
            boxShadow: 2,
            borderRadius: 2
          }}>
            <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 600,
                  mb: 3,
                  color: 'text.primary'
                }}
              >
                Generation Settings
              </Typography>

              {/* Content Preset Selector - Primary control for better images */}
              <Box sx={{ mb: 3, p: 2, bgcolor: 'primary.50', borderRadius: 2, border: '1px solid', borderColor: 'primary.200' }}>
                <Typography variant="subtitle2" sx={{ mb: 1.5, fontWeight: 600, color: 'primary.main' }}>
                  Content Type (for better quality)
                </Typography>
                <FormControl fullWidth size="small">
                  <Select
                    value={selectedPreset}
                    onChange={(e) => handlePresetChange(e.target.value)}
                    sx={{ bgcolor: 'background.paper' }}
                  >
                    <MenuItem value="auto">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Auto-detect (Recommended)</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Automatically optimizes settings based on your prompt
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_portrait">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Person - Portrait</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Headshots, face close-ups, profile photos
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_full_body">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Person - Full Body</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Standing, sitting, or posed full-body shots
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="person_working">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Person - Working/Action</Typography>
                        <Typography variant="caption" color="text.secondary">
                          People doing activities, using tools, interacting with objects
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="product_photo">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Product Photo</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Clean product shots, commercial photography
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="landscape">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Landscape/Scenery</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Nature, cityscapes, outdoor scenes
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="infographic_preset">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>Infographic/Diagram</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Flat design, icons, vector graphics, charts
                        </Typography>
                      </Box>
                    </MenuItem>
                    <MenuItem value="general">
                      <Box>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>General Purpose</Typography>
                        <Typography variant="caption" color="text.secondary">
                          Default settings for any content
                        </Typography>
                      </Box>
                    </MenuItem>
                  </Select>
                </FormControl>
              </Box>

              {/* Input Mode Selection */}
              <Box sx={{ mb: 3 }}>
                <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                  Input Method
                </Typography>
                <Box sx={{ display: 'flex', gap: 1 }}>
                  <Button
                    variant={inputMode === 'bulk' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('bulk')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'bulk' ? 600 : 400
                    }}
                  >
                    Bulk Input
                  </Button>
                  <Button
                    variant={inputMode === 'csv' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('csv')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'csv' ? 600 : 400
                    }}
                  >
                    CSV Upload
                  </Button>
                  <Button
                    variant={inputMode === 'blueprint' ? 'contained' : 'outlined'}
                    onClick={() => setInputMode('blueprint')}
                    size="small"
                    sx={{
                      flex: 1,
                      textTransform: 'none',
                      fontWeight: inputMode === 'blueprint' ? 600 : 400
                    }}
                  >
                    Offline Blueprints
                  </Button>
                </Box>
              </Box>

              {/* Bulk Input */}
              {inputMode === 'bulk' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    Image Topics/Prompts
                  </Typography>
                  <TextField
                    fullWidth
                    multiline
                    rows={8}
                    placeholder="Enter image topics or prompts, one per line:&#10;&#10;A majestic mountain landscape at sunset&#10;A cat sitting on a windowsill&#10;Abstract geometric patterns in blue&#10;Portrait of a wise old wizard&#10;A futuristic city skyline&#10;..."
                    value={batchItems}
                    onChange={handleBatchItemsChange}
                    variant="outlined"
                    sx={{
                      mb: 2,
                      '& .MuiOutlinedInput-root': {
                        borderRadius: 1,
                        fontFamily: 'monospace',
                        fontSize: '0.9rem'
                      }
                    }}
                  />
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
                    <Typography variant="caption" color="text.secondary">
                      {parseBatchItems().length} prompts ready for generation
                    </Typography>
                    <Button
                      variant="text"
                      size="small"
                      onClick={() => {
                        setBatchItems(
                          'A majestic mountain landscape at sunset\n' +
                          'A cat sitting on a windowsill\n' +
                          'Abstract geometric patterns in blue\n' +
                          'Portrait of a wise old wizard\n' +
                          'A futuristic city skyline'
                        );
                        setLookAndFeel('photorealistic, professional photography, sharp focus, natural lighting');
                      }}
                      sx={{
                        textTransform: 'none',
                        fontSize: '0.75rem',
                        minWidth: 'auto',
                        px: 1,
                        py: 0.25
                      }}
                    >
                      Load Examples
                    </Button>
                  </Box>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    Tip: Each line becomes a separate image prompt
                  </Typography>

                  {/* Look & Feel Field */}
                  <Box sx={{ mt: 3 }}>
                    <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>
                      Look & Feel (Optional)
                    </Typography>
                    <TextField
                      fullWidth
                      multiline
                      rows={3}
                      placeholder="Describe the visual style to apply to all topics above:&#10;&#10;Examples:&#10;• In shades of blue, no text, darker colors&#10;• Minimalist black and white, clean lines&#10;• Professional infographic style, flat design&#10;• Photorealistic, dramatic lighting"
                      value={lookAndFeel}
                      onChange={(e) => setLookAndFeel(e.target.value)}
                      variant="outlined"
                      sx={{
                        mb: 1,
                        '& .MuiOutlinedInput-root': {
                          borderRadius: 1
                        }
                      }}
                    />
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <Typography variant="caption" color="text.secondary">
                        This style will be applied to all {parseBatchItems().length} prompts above
                      </Typography>
                      {parseBatchItems().length > 0 && (
                        <Button
                          variant="outlined"
                          size="small"
                          onClick={() => setShowPromptPreview(true)}
                          sx={{
                            textTransform: 'none',
                            fontSize: '0.75rem',
                            minWidth: 'auto',
                            px: 1.5,
                            py: 0.5
                          }}
                        >
                          Preview Prompts
                        </Button>
                      )}
                    </Box>
                  </Box>
                </Box>
              )}

              {/* CSV Upload */}
              {inputMode === 'csv' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    CSV File Upload
                  </Typography>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={handleFileUpload}
                    ref={fileInputRef}
                    style={{ display: 'none' }}
                  />

                  <Button
                    startIcon={<Upload />}
                    onClick={() => fileInputRef.current?.click()}
                    variant="outlined"
                    fullWidth
                    sx={{
                      mb: 2,
                      textTransform: 'none',
                      borderRadius: 1,
                      py: 1.5
                    }}
                  >
                    Upload CSV File
                  </Button>

                  {csvFile && (
                    <Alert severity="info" sx={{ mb: 2, borderRadius: 1 }}>
                      File selected: {csvFile.name}
                    </Alert>
                  )}

                  <Button
                    startIcon={<GetApp />}
                    onClick={downloadTemplate}
                    variant="text"
                    size="small"
                    sx={{
                      textTransform: 'none',
                      borderRadius: 1
                    }}
                  >
                    Download CSV Template
                  </Button>
                </Box>
              )}

              {/* Offline Blueprint Upload */}
              {inputMode === 'blueprint' && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 2, fontWeight: 500 }}>
                    Offline Blueprint CSV Upload
                  </Typography>
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    Upload a CSV file with 'city' and 'count' (or 'patents') columns to generate data blueprints (CPU-only). Large batches run in the background; poll for status until complete.
                  </Typography>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={handleBlueprintFileUpload}
                    ref={blueprintFileInputRef}
                    style={{ display: 'none' }}
                  />

                  <Button
                    startIcon={<Upload />}
                    onClick={() => blueprintFileInputRef.current?.click()}
                    variant="outlined"
                    fullWidth
                    sx={{
                      mb: 2,
                      textTransform: 'none',
                      borderRadius: 1,
                      py: 1.5
                    }}
                  >
                    Upload Blueprint CSV
                  </Button>

                  {blueprintFile && (
                    <Alert severity="info" sx={{ mb: 2, borderRadius: 1 }}>
                      File selected: {blueprintFile.name}
                    </Alert>
                  )}

                  <Typography variant="caption" color="text.secondary">
                    Accepted format: .csv (columns like city/name and count/patents/value)
                  </Typography>
                </Box>
              )}

              {inputMode !== 'blueprint' && (
                <>

                  {/* Current Settings Display */}
                  <Box sx={{ mt: 3, p: 2, backgroundColor: 'background.paper', borderRadius: 1, border: '1px solid', borderColor: 'divider' }}>
                    <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 500 }}>Current Settings</Typography>
                    <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                      <Chip
                        label={`Model: ${modelOptions.find(m => m.value === params.model)?.label || params.model}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Style: ${params.style}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Quality: ${params.quality_preset}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Size: ${params.width}x${params.height}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Steps: ${params.steps}`}
                        size="small"
                        variant="outlined"
                      />
                      <Chip
                        label={`Workers: ${params.max_workers}`}
                        size="small"
                        variant="outlined"
                      />
                    </Box>
                  </Box>

                  {/* Generation Settings */}
                  <Accordion sx={{ mt: 3, borderRadius: 1 }}>
                    <AccordionSummary
                      expandIcon={<ExpandMore />}
                      sx={{
                        borderRadius: 1,
                        '&.Mui-expanded': {
                          borderRadius: '8px 8px 0 0'
                        }
                      }}
                    >
                      <Typography sx={{ fontWeight: 500 }}>Advanced Settings</Typography>
                    </AccordionSummary>
                    <AccordionDetails sx={{ pt: 2 }}>
                      <Grid container spacing={2}>
                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Model</InputLabel>
                            <Select
                              value={params.model}
                              onChange={(e) => handleModelChange(e.target.value)}
                            >
                              {modelOptions.map(option => (
                                <MenuItem key={option.value} value={option.value}>
                                  <Box>
                                    <Typography variant="body2">{option.label}</Typography>
                                    <Typography variant="caption" color="text.secondary">
                                      {option.description}
                                    </Typography>
                                  </Box>
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Quality Preset</InputLabel>
                            <Select
                              value={params.quality_preset}
                              onChange={(e) => handleQualityPresetChange(e.target.value)}
                            >
                              {qualityPresets.map(option => (
                                <MenuItem key={option.value} value={option.value}>
                                  <Box>
                                    <Typography variant="body2">{option.label}</Typography>
                                    <Typography variant="caption" color="text.secondary">
                                      {option.description}
                                    </Typography>
                                  </Box>
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Style</InputLabel>
                            <Select
                              value={params.style}
                              onChange={(e) => setParams({ ...params, style: e.target.value })}
                            >
                              {styleOptions.map(option => (
                                <MenuItem key={option.value} value={option.value}>
                                  {option.label}
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <TextField
                            fullWidth
                            label="Number of Images per Prompt"
                            type="number"
                            value={quantity}
                            onChange={(e) => setQuantity(Math.max(1, parseInt(e.target.value) || 1))}
                            inputProps={{ min: 1, max: 100 }}
                            helperText="Generates X versions for each prompt"
                          />
                        </Grid>

                        <Grid item xs={12} sm={6} md={4}>
                          <FormControl fullWidth>
                            <InputLabel>Dimensions</InputLabel>
                            <Select
                              value={`${params.width}x${params.height}`}
                              onChange={(e) => {
                                const [width, height] = e.target.value.split('x').map(Number);
                                setParams({ ...params, width, height });
                              }}
                            >
                              {dimensionPresets.map(preset => (
                                <MenuItem key={`${preset.width}x${preset.height}`} value={`${preset.width}x${preset.height}`}>
                                  {preset.label}
                                </MenuItem>
                              ))}
                            </Select>
                          </FormControl>
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>Steps: {params.steps}</Typography>
                          <Slider
                            value={params.steps}
                            onChange={(e, value) => setParams({ ...params, steps: value })}
                            min={10}
                            max={50}
                            step={5}
                            marks
                          />
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>Guidance Scale: {params.guidance}</Typography>
                          <Slider
                            value={params.guidance}
                            onChange={(e, value) => setParams({ ...params, guidance: value })}
                            min={1}
                            max={20}
                            step={0.5}
                            marks
                          />
                        </Grid>

                        <Grid item xs={12}>
                          <Typography gutterBottom>Max Workers: {params.max_workers}</Typography>
                          <Slider
                            value={params.max_workers}
                            onChange={(e, value) => setParams({ ...params, max_workers: value })}
                            min={1}
                            max={4}
                            step={1}
                            marks
                          />
                        </Grid>

                        <Grid item xs={12}>
                          <FormControlLabel
                            control={
                              <Switch
                                checked={params.generate_thumbnails}
                                onChange={(e) => setParams({ ...params, generate_thumbnails: e.target.checked })}
                              />
                            }
                            label="Generate Thumbnails"
                          />
                        </Grid>
                      </Grid>
                    </AccordionDetails>
                  </Accordion>
                </>
              )}

              {/* Action Buttons */}
              <Box sx={{ mt: 3, display: 'flex', gap: 2 }}>
                <Button
                  variant="contained"
                  onClick={startGeneration}
                  disabled={loading || (activeBatch && activeBatch.status === 'running')}
                  startIcon={<PlayArrow />}
                  fullWidth
                  size="large"
                  sx={{
                    textTransform: 'none',
                    borderRadius: 1,
                    py: 1.5,
                    fontWeight: 600
                  }}
                >
                  {loading ? 'Starting...' : 'Start Generation'}
                </Button>

                {activeBatch && activeBatch.status === 'running' && (
                  <Button
                    variant="outlined"
                    onClick={cancelGeneration}
                    startIcon={<Cancel />}
                    color="error"
                    size="large"
                    sx={{
                      textTransform: 'none',
                      borderRadius: 1,
                      py: 1.5,
                      fontWeight: 600
                    }}
                  >
                    Cancel
                  </Button>
                )}
              </Box>
            </CardContent>
          </Card>
        </Grid >

        {/* Progress and Results Section */}
        < Grid item xs={12} lg={6} >
          {/* Active Batch Progress */}
          {
            activeBatch && (
              <Card sx={{
                mb: 3,
                boxShadow: 2,
                borderRadius: 2
              }}>
                <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                  <Typography
                    variant="h6"
                    sx={{
                      fontWeight: 600,
                      mb: 2,
                      color: 'text.primary'
                    }}
                  >
                    Current Progress
                  </Typography>

                  <Box sx={{ mb: 2 }}>
                    <Typography variant="body2" color="text.secondary">
                      Batch ID: {activeBatch.batch_id}
                    </Typography>
                    <Chip
                      label={activeBatch.status.toUpperCase()}
                      color={activeBatch.status === 'running' ? 'primary' :
                        activeBatch.status === 'completed' ? 'success' : 'default'}
                      size="small"
                      sx={{ mt: 1 }}
                    />
                  </Box>

                  <Box sx={{ mb: 2 }}>
                    <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
                      <Typography variant="body2">
                        Progress: {activeBatch.completed_images || 0}/{activeBatch.total_images || 0}
                      </Typography>
                      <Typography variant="body2">
                        {activeBatch.progress_percentage || 0}%
                      </Typography>
                    </Box>
                    <LinearProgress
                      variant="determinate"
                      value={activeBatch.progress_percentage || 0}
                    />
                  </Box>

                  {activeBatch.status === 'completed' && (
                    <Button
                      startIcon={<Download />}
                      onClick={() => downloadResults(activeBatch.batch_id)}
                      variant="outlined"
                      size="small"
                    >
                      Download Results
                    </Button>
                  )}
                </CardContent>
              </Card>
            )
          }

          {/* Generated Images Gallery */}
          {
            generatedImages.length > 0 && (
              <Card sx={{
                boxShadow: 2,
                borderRadius: 2
              }}>
                <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
                  <Typography
                    variant="h6"
                    sx={{
                      fontWeight: 600,
                      mb: 2,
                      color: 'text.primary'
                    }}
                  >
                    Generated Images ({generatedImages.length})
                  </Typography>

                  <ImageList
                    cols={imageListCols}
                    gap={8}
                    sx={{
                      '& .MuiImageListItem-root': {
                        borderRadius: 1,
                        overflow: 'hidden'
                      }
                    }}
                  >
                    {generatedImages.map((image) => {
                      // Use thumbnail filename if available, otherwise fallback to image filename
                      const thumbnailUrl = activeBatch && image.thumbnailFilename
                        ? `${API_BASE}/batch-image/image/${activeBatch.batch_id}/${image.thumbnailFilename}?thumbnail=true`
                        : activeBatch && image.imageFilename
                          ? `${API_BASE}/batch-image/image/${activeBatch.batch_id}/${image.imageFilename}?thumbnail=true`
                          : '';

                      return (
                        <ImageListItem key={image.id}>
                          <img
                            src={thumbnailUrl}
                            alt={image.prompt || 'Generated image'}
                            loading="lazy"
                            style={{ cursor: 'pointer' }}
                            onClick={() => openImageViewer(image)}
                            onError={(e) => {
                              console.error('Failed to load image thumbnail:', image.id, thumbnailUrl);
                              // Fallback to full image if thumbnail fails
                              if (image.imageFilename && !e.target.dataset.fallbackAttempted) {
                                e.target.src = activeBatch ? `${API_BASE}/batch-image/image/${activeBatch.batch_id}/${image.imageFilename}` : '';
                                e.target.dataset.fallbackAttempted = 'true';
                              } else {
                                e.target.style.display = 'none';
                              }
                            }}
                            role="button"
                            tabIndex={0}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                openImageViewer(image);
                              }
                            }}
                          />
                          <ImageListItemBar
                            title={image.prompt ? sanitizeText(image.prompt).substring(0, 30) + '...' : 'No prompt'}
                            actionIcon={
                              <IconButton
                                sx={{ color: 'rgba(255, 255, 255, 0.54)' }}
                                onClick={() => openImageViewer(image)}
                                aria-label={`View full image: ${sanitizeText(image.prompt) || 'Generated image'}`}
                              >
                                <Visibility />
                              </IconButton>
                            }
                          />
                        </ImageListItem>
                      );
                    })}
                  </ImageList>
                </CardContent>
              </Card>
            )
          }
        </Grid >
      </Grid >

      {/* Batch History */}
      {
        batchHistory.length > 0 && (
          <Card sx={{
            mt: 3,
            boxShadow: 2,
            borderRadius: 2
          }}>
            <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 600,
                  mb: 2,
                  color: 'text.primary'
                }}
              >
                Recent Batches
              </Typography>

              <Grid container spacing={2}>
                {batchHistory.slice(0, 10).map((batch) => (
                  <Grid item xs={12} sm={6} lg={4} key={batch.batch_id}>
                    <Paper sx={{
                      p: 2,
                      borderRadius: 1,
                      border: '1px solid',
                      borderColor: 'divider',
                      '&:hover': {
                        boxShadow: 1
                      }
                    }}>
                      {/* Overlapping thumbnail covers */}
                      {batch.thumbnail_urls?.length > 0 && (
                        <Box sx={{ mb: 1.5, height: 48, position: 'relative', minWidth: Math.min(batch.thumbnail_urls.length, 4) * 32 + 16 }}>
                          {batch.thumbnail_urls.slice(0, 4).map((url, idx) => (
                            <Box
                              key={idx}
                              component="img"
                              src={url}
                              alt=""
                              sx={{
                                width: 48,
                                height: 48,
                                borderRadius: 1,
                                objectFit: 'cover',
                                border: '2px solid',
                                borderColor: 'background.paper',
                                position: 'absolute',
                                left: idx * 32,
                                zIndex: 4 - idx,
                                boxShadow: '0 1px 3px rgba(0,0,0,0.2)',
                              }}
                            />
                          ))}
                        </Box>
                      )}

                      <Typography
                        variant="subtitle2"
                        sx={{
                          fontWeight: 600,
                          mb: 1,
                          color: batch.folder_id ? 'primary.main' : 'text.primary',
                          cursor: batch.folder_id ? 'pointer' : 'default',
                          '&:hover': batch.folder_id ? { textDecoration: 'underline' } : {},
                        }}
                        onClick={() => {
                          if (batch.folder_id) {
                            navigate('/images');
                          }
                        }}
                      >
                        {batch.batch_id}
                      </Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
                        Status: {batch.status}
                      </Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                        Images: {batch.completed_images}/{batch.total_images}
                      </Typography>

                      {batch.status === 'completed' && (
                        <Box sx={{ display: 'flex', gap: 1, mt: 1 }}>
                          {batch.folder_id && (
                            <Button
                              size="small"
                              startIcon={<Visibility />}
                              onClick={() => navigate('/images')}
                              sx={{
                                textTransform: 'none',
                                borderRadius: 1
                              }}
                            >
                              Gallery
                            </Button>
                          )}
                          <Button
                            size="small"
                            startIcon={<Download />}
                            onClick={() => downloadResults(batch.batch_id)}
                            sx={{
                              textTransform: 'none',
                              borderRadius: 1
                            }}
                          >
                            Download
                          </Button>
                        </Box>
                      )}
                    </Paper>
                  </Grid>
                ))}
              </Grid>
            </CardContent>
          </Card>
        )
      }

      {/* Image Viewer Dialog */}
      <Dialog
        open={showImageViewer}
        onClose={() => setShowImageViewer(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          {sanitizeText(selectedImage?.prompt)}
        </DialogTitle>
        <DialogContent>
          {selectedImage && (
            <Box sx={{ textAlign: 'center' }}>
              <img
                src={activeBatch && selectedImage.imageFilename
                  ? `${API_BASE}/batch-image/image/${activeBatch.batch_id}/${selectedImage.imageFilename}`
                  : ''}
                alt={selectedImage.prompt}
                style={{ maxWidth: '100%', height: 'auto' }}
                onError={(e) => {
                  console.error('Failed to load full-size image:', selectedImage.id);
                  // Fallback to thumbnail if full image fails
                  if (selectedImage.thumbnailFilename && !e.target.dataset.fallbackAttempted) {
                    e.target.src = activeBatch ? `${API_BASE}/batch-image/image/${activeBatch.batch_id}/${selectedImage.thumbnailFilename}?thumbnail=true` : '';
                    e.target.dataset.fallbackAttempted = 'true';
                  } else {
                    e.target.style.display = 'none';
                    setError('Failed to load image');
                  }
                }}
              />

              {selectedImage.metadata && (
                <Box sx={{ mt: 2, textAlign: 'left' }}>
                  <Typography variant="subtitle2">Metadata:</Typography>
                  <Typography variant="body2">
                    Style: {selectedImage.metadata.style}<br />
                    Dimensions: {selectedImage.metadata.dimensions}<br />
                    Steps: {selectedImage.metadata.steps}<br />
                    Guidance: {selectedImage.metadata.guidance}<br />
                    {selectedImage.metadata.seed_used && `Seed: ${selectedImage.metadata.seed_used}`}
                  </Typography>
                </Box>
              )}
            </Box>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowImageViewer(false)}>Close</Button>
        </DialogActions>
      </Dialog>

      {/* Prompt Preview Dialog */}
      <Dialog
        open={showPromptPreview}
        onClose={() => setShowPromptPreview(false)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle>
          Preview Generated Prompts ({parseBatchItems().length})
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            These are the final prompts that will be sent to the image generator:
          </Typography>
          <Box sx={{ maxHeight: 400, overflow: 'auto' }}>
            {parseBatchItems().map((prompt, index) => (
              <Paper
                key={index}
                variant="outlined"
                sx={{
                  p: 2,
                  mb: 1,
                  backgroundColor: 'background.default',
                  border: '1px solid',
                  borderColor: 'divider'
                }}
              >
                <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                  <strong>{index + 1}.</strong> {sanitizeText(prompt)}
                </Typography>
              </Paper>
            ))}
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShowPromptPreview(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </PageLayout>
  );
};

export default BatchImageGeneratorPage;