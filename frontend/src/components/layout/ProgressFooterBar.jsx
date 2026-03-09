// frontend/src/components/layout/ProgressFooterBar.jsx
// Version 2.3: Enhanced with TaskQueueIndicator for unified scheduler
// Uses unified progress system with simple, clean UI

import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Box, LinearProgress, Typography, Divider, useTheme } from '@mui/material';
import { useUnifiedProgress } from '../../contexts/UnifiedProgressContext';
import TaskQueueIndicator from './TaskQueueIndicator';
import { useAppStore } from '../../stores/useAppStore';

const ProgressFooterBar = () => {
    const theme = useTheme();
    const { globalProgress, activeProcesses } = useUnifiedProgress();
    
    // Local state for UI
    const [active, setActive] = useState(false);
    const [progress, setProgress] = useState(0);
    const [maxProgress, setMaxProgress] = useState(0);
    const [statusText, setStatusText] = useState('Idle');
    const [generatedCount, setGeneratedCount] = useState(null);
    const [targetCount, setTargetCount] = useState(null);
    const progressRef = useRef(0);
    const lastProcessIdRef = useRef(null);
    const timeoutRef = useRef(null);
    const debounceTimeoutRef = useRef(null);

    // BUG FIX: Use ref to track current globalProgress.active state
    // This prevents stale closure issues in timeout callbacks
    const globalProgressActiveRef = useRef(globalProgress.active);

    // Conditional debugging - only in development
    useEffect(() => {
        if (process.env.NODE_ENV === 'development') {
            console.log('ProgressFooterBar: State update:', {
                globalProgressActive: globalProgress.active,
                activeProcessesCount: activeProcesses.size
            });
        }
    }, [globalProgress, activeProcesses]);

    // BUG FIX: Keep ref in sync with globalProgress.active
    // This allows timeout callbacks to check the CURRENT state, not stale closure
    useEffect(() => {
        globalProgressActiveRef.current = globalProgress.active;
    }, [globalProgress.active]);

    // Effect to sync with unified progress system ONLY
    useEffect(() => {
        if (globalProgress.active) {
            setActive(true);

            // BUG FIX #2: Safely convert activeProcesses to array with null check
            if (!activeProcesses || typeof activeProcesses.values !== 'function') {
                console.error('ProgressFooterBar: activeProcesses is invalid');
                setActive(false);
                setStatusText('Idle');
                setProgress(0);
                setMaxProgress(0);
                setGeneratedCount(null);
                setTargetCount(null);
                progressRef.current = 0;
                lastProcessIdRef.current = null;
                return;
            }

            const processes = Array.from(activeProcesses.values());
            if (processes.length === 0) {
                if (process.env.NODE_ENV === 'development') {
                    console.log('ProgressFooterBar: No active processes, setting idle');
                }
                setActive(false);
                setStatusText('Idle');
                setProgress(0);
                setMaxProgress(0);
                setGeneratedCount(null);
                setTargetCount(null);
                progressRef.current = 0;
                lastProcessIdRef.current = null;
                return;
            }
            
            // Find the most relevant process with priority order for ALL services
            const priorityOrder = [
                'indexing',      // Document indexing
                'image_generation', // Image generation
                'csv_processing', // CSV generation 
                'file_generation', // File generation
                'analysis',      // Analysis tasks (including CodeGen)
                'upload',        // File uploads
                'llm_processing', // LLM operations
                'web_scraping',  // Web search operations
                'backup',        // Backup operations
                'training',      // AI model training
                'task_processing', // General task processing
                'voice_processing', // Voice operations
                'processing',    // Generic processing
            ];
            
            // BUG FIX #8: Find highest priority active process with safety checks
            let currentProcess = null;
            for (const processType of priorityOrder) {
                currentProcess = processes.find(p => {
                    if (!p || typeof p !== 'object') return false;
                    return p.processType === processType ||
                           p.process_type === processType ||
                           (p.message && typeof p.message === 'string' &&
                            p.message.toLowerCase().includes(processType.replace('_', ' ')));
                });
                if (currentProcess) break;
            }

            // If no priority match, use the most recent process
            if (!currentProcess && processes.length > 0) {
                currentProcess = processes
                    .filter(p => p && typeof p === 'object')
                    .sort((a, b) =>
                        new Date(b.timestamp || 0) - new Date(a.timestamp || 0)
                    )[0];
            }
            
            if (currentProcess) {
                const newProgress = currentProcess.progress || 0;
                const message = currentProcess.message || 'Processing...';
                const processId = currentProcess.job_id;

                // Extract count data from additional_data if available
                const additionalData = currentProcess.additional_data || {};
                const currentGenerated = additionalData.generated_count;
                const currentTarget = additionalData.target_count;

                // Update count state
                if (currentGenerated !== undefined && currentTarget !== undefined) {
                    setGeneratedCount(currentGenerated);
                    setTargetCount(currentTarget);
                } else {
                    // Clear counts if not available
                    setGeneratedCount(null);
                    setTargetCount(null);
                }

                // Check if this is a new process
                const isNewProcess = processId !== lastProcessIdRef.current;
                if (isNewProcess) {
                    if (process.env.NODE_ENV === 'development') {
                        console.log('ProgressFooterBar: New process detected:', {
                            processId,
                            processType: currentProcess.processType,
                            status: currentProcess.status,
                            progress: newProgress,
                            message: message
                        });
                    }
                    lastProcessIdRef.current = processId;
                    progressRef.current = 0; // Reset for new process
                }
                
                // Enhanced status text with process type information
                const processType = currentProcess.processType || currentProcess.process_type || 'processing';
                const processTypeUpper = (processType || 'processing').toString().toUpperCase();
                const enhancedMessage = message.includes(processTypeUpper) ? 
                    message : 
                    `${processTypeUpper}: ${message}`;
                setStatusText(enhancedMessage);
                
                // BUG FIX #5: Properly validate and bound progress values
                const validatedProgress = typeof newProgress === 'number' && !isNaN(newProgress)
                    ? Math.max(0, Math.min(100, newProgress))
                    : 0;
                
                // Update progress - allow both increases and decreases for accurate tracking
                // Only update if there's an actual change to prevent unnecessary renders
                const progressChanged = validatedProgress !== progressRef.current;
                const shouldUpdate = isNewProcess || progressChanged || currentProcess.status === 'start';

                if (shouldUpdate) {
                    const oldProgress = progressRef.current;
                    progressRef.current = validatedProgress;

                    // Debounce rapid progress updates to prevent flicker
                    if (debounceTimeoutRef.current) {
                        clearTimeout(debounceTimeoutRef.current);
                    }

                    // Immediate update for significant changes, debounce for small increments
                    const significantChange = Math.abs(validatedProgress - progress) >= 5 || isNewProcess;
                    if (significantChange) {
                        setProgress(validatedProgress);
                        setMaxProgress(Math.max(maxProgress, validatedProgress));
                    } else {
                        debounceTimeoutRef.current = setTimeout(() => {
                            setProgress(validatedProgress);
                            setMaxProgress(Math.max(maxProgress, validatedProgress));
                        }, 100); // 100ms debounce for minor updates
                    }

                    if (process.env.NODE_ENV === 'development' && progressChanged) {
                        console.log('ProgressFooterBar: Progress updated:', {
                            processId,
                            oldProgress,
                            newProgress: validatedProgress,
                            isNewProcess,
                            debounced: !significantChange
                        });
                    }
                }
                
                // Handle completion
                if (currentProcess.status === 'complete' || currentProcess.status === 'end') {
                    if (process.env.NODE_ENV === 'development') {
                        console.log('ProgressFooterBar: Process completed:', processId);
                    }
                    progressRef.current = 100;
                    setProgress(100);
                    setMaxProgress(100);

                    // BUG FIX #3 & #6: Clear existing timeout with null check and fix race condition
                    if (timeoutRef.current !== null && timeoutRef.current !== undefined) {
                        clearTimeout(timeoutRef.current);
                        timeoutRef.current = null;
                    }

                    // Auto-hide after completion - capture current state to avoid race condition
                    const capturedProcessId = processId;
                    timeoutRef.current = setTimeout(() => {
                        // BUG FIX: Use REF for current globalProgress.active value, not stale closure
                        // This ensures we check the ACTUAL current state when timeout fires
                        if (lastProcessIdRef.current === capturedProcessId && !globalProgressActiveRef.current) {
                            if (process.env.NODE_ENV === 'development') {
                                console.log('ProgressFooterBar: Auto-hiding after completion');
                            }
                            setActive(false);
                            setStatusText('Idle');
                            setProgress(0);
                            setMaxProgress(0);
                            setGeneratedCount(null);
                            setTargetCount(null);
                            progressRef.current = 0;
                            lastProcessIdRef.current = null;
                        }
                        timeoutRef.current = null;
                    }, 2000);
                }
                
                // Handle errors
                if (currentProcess.status === 'error') {
                    if (process.env.NODE_ENV === 'development') {
                        console.log('ProgressFooterBar: Process error:', processId);
                    }
                    setStatusText(message || 'Error occurred');

                    // BUG FIX #3 & #7: Clear existing timeout with proper null check
                    if (timeoutRef.current !== null && timeoutRef.current !== undefined) {
                        clearTimeout(timeoutRef.current);
                        timeoutRef.current = null;
                    }

                    // Capture state to avoid race condition
                    const capturedProcessId = processId;
                    timeoutRef.current = setTimeout(() => {
                        // BUG FIX: Use REF for current globalProgress.active value, not stale closure
                        if (lastProcessIdRef.current === capturedProcessId && !globalProgressActiveRef.current) {
                            if (process.env.NODE_ENV === 'development') {
                                console.log('ProgressFooterBar: Auto-hiding after error');
                            }
                            setActive(false);
                            setStatusText('Idle');
                            setProgress(0);
                            setMaxProgress(0);
                            setGeneratedCount(null);
                            setTargetCount(null);
                            progressRef.current = 0;
                            lastProcessIdRef.current = null;
                        }
                        timeoutRef.current = null;
                    }, 3000);
                }
            }
        } else {
            // No active processes
            if (process.env.NODE_ENV === 'development') {
                console.log('ProgressFooterBar: No global progress active, setting idle');
            }
            setActive(false);
            setStatusText('Idle');
            setProgress(0);
            setMaxProgress(0);
            setGeneratedCount(null);
            setTargetCount(null);
            progressRef.current = 0;
            lastProcessIdRef.current = null;

            // BUG FIX #7: Clear any existing timeout with proper null check
            if (timeoutRef.current !== null && timeoutRef.current !== undefined) {
                clearTimeout(timeoutRef.current);
                timeoutRef.current = null;
            }
        }
    }, [globalProgress.active, activeProcesses]);

    // Cleanup timeouts on unmount
    useEffect(() => {
        return () => {
            // BUG FIX #3 & #8: Proper cleanup with null check for all timeouts
            if (timeoutRef.current !== null && timeoutRef.current !== undefined) {
                clearTimeout(timeoutRef.current);
                timeoutRef.current = null;
            }
            if (debounceTimeoutRef.current !== null && debounceTimeoutRef.current !== undefined) {
                clearTimeout(debounceTimeoutRef.current);
                debounceTimeoutRef.current = null;
            }
        };
    }, []);

    // Dynamic sidebar width from Zustand store
    const sidebarExpanded = useAppStore((state) => state.sidebarExpanded);
    const drawerWidth = sidebarExpanded ? 240 : 64;

    // BUG FIX #10: Error boundary for rendering
    try {
        return (
            <Box
            sx={{
                position: 'fixed',
                bottom: 0,
                left: drawerWidth, // Start after the sidebar
                right: 0,
                height: '24px', // Compact height like Cursor/VS Code
                zIndex: 9999, // Very high z-index to ensure it's on top
                backgroundColor: theme.palette.background.paper,
                borderTop: `1px solid ${theme.palette.divider}`,
                display: 'flex',
                alignItems: 'center',
                px: 2,
                // Debug: Add a subtle shadow to make it more visible
                boxShadow: '0 -2px 8px rgba(0,0,0,0.1)',
                // BUG FIX #9: Better visibility control - always show footer but adjust opacity
                opacity: active ? 1 : (process.env.NODE_ENV === 'development' ? 0.5 : 0.2),
                pointerEvents: active || process.env.NODE_ENV === 'development' ? 'auto' : 'none',
                transition: 'opacity 0.3s ease-in-out',
                // Debug: Add subtle border in development
                ...(process.env.NODE_ENV === 'development' && !active && {
                    borderTop: '1px solid rgba(100, 100, 100, 0.3)', // Subtle debug border
                }),
            }}
        >
            {active ? (
                // Show LinearProgress when active with percentage and count
                <>
                    <LinearProgress
                        color="info"
                        variant="determinate"
                        value={progress}
                        sx={{
                            height: '4px',
                            flexGrow: 1,
                            mr: 2,
                            borderRadius: '2px',
                            backgroundColor: theme.palette.mode === 'dark' ? 'grey.800' : 'grey.300',
                            '& .MuiLinearProgress-bar': {
                                borderRadius: '2px',
                            }
                        }}
                    />
                    {/* Show count if available (e.g., "15/100") */}
                    {generatedCount !== undefined && generatedCount !== null && targetCount !== undefined && targetCount !== null ? (
                        <Typography
                            variant="caption"
                            sx={{
                                color: 'info.main',
                                fontSize: '0.65rem',
                                fontWeight: 500,
                                mr: 1,
                                minWidth: '60px',
                                textAlign: 'right'
                            }}
                        >
                            Row {generatedCount} of {targetCount} ({Math.round(progress)}%)
                        </Typography>
                    ) : (
                        <Typography
                            variant="caption"
                            sx={{
                                color: 'info.main',
                                fontSize: '0.65rem',
                                fontWeight: 500,
                                mr: 1,
                                minWidth: '30px',
                                textAlign: 'right'
                            }}
                        >
                            {Math.round(progress)}%
                        </Typography>
                    )}
                </>
            ) : (
                // Show subtle idle bar when not active
                <Box 
                    sx={{
                        height: '4px', 
                        flexGrow: 1, 
                        mr: 2, 
                        backgroundColor: theme.palette.mode === 'dark' ? 'grey.800' : 'grey.300', 
                        borderRadius: '2px' 
                    }} 
                />
            )}
            <Typography
                variant="caption"
                sx={{
                    color: 'text.secondary',
                    whiteSpace: 'nowrap',
                    fontSize: '0.7rem', // Smaller font size
                    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif', // Cleaner font
                    fontWeight: 400,
                    letterSpacing: '0.02em',
                    flexGrow: 0,
                    mr: 1
                }}
            >
                {statusText}
                {process.env.NODE_ENV === 'development' && ' - Ready'}
            </Typography>

            {/* Task Queue Indicator - shows scheduled/queued tasks */}
            <Divider orientation="vertical" flexItem sx={{ mx: 1, height: 16, alignSelf: 'center' }} />
            <TaskQueueIndicator compact={true} />
        </Box>
        );
    } catch (error) {
        console.error('ProgressFooterBar rendering error:', error);
        // Fallback UI
        return (
            <Box
                sx={{
                    position: 'fixed',
                    bottom: 0,
                    left: drawerWidth,
                    right: 0,
                    height: '24px',
                    backgroundColor: 'error.main',
                    display: 'flex',
                    alignItems: 'center',
                    px: 2,
                    zIndex: 9999,
                }}
            >
                <Typography variant="caption" color="white">
                    Progress bar error - check console
                </Typography>
            </Box>
        );
    }
};

export default ProgressFooterBar;
