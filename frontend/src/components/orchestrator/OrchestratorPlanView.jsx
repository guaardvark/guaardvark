import React, { useState, useEffect } from 'react';
import PropTypes from 'prop-types';
import {
    Box,
    Typography,
    List,
    ListItem,
    ListItemText,
    ListItemIcon,
    Chip,
    Button,
    CircularProgress,
    Divider,
    Collapse,
    IconButton,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import PendingIcon from '@mui/icons-material/Pending';
import PlayCircleOutlineIcon from '@mui/icons-material/PlayCircleOutline';
import ErrorIcon from '@mui/icons-material/Error';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import { executePlan, getPlanStatus } from '../../api/orchestratorService';
import { translate, useUiTranslation } from '../../i18n';

// Step and plan statuses as shown; the values from the API stay the ones compared.
const statusLabel = (status) =>
    ({
        pending: translate('agents.plan.status.pending'),
        running: translate('agents.plan.status.running'),
        completed: translate('agents.plan.status.completed'),
        failed: translate('agents.plan.status.failed'),
        planning: translate('agents.plan.status.planning'),
        executing: translate('agents.plan.status.executing'),
    })[status] ?? status;

const StepItem = ({ step, embedded }) => {
    const { t } = useUiTranslation();
    const [expanded, setExpanded] = useState(false);

    const getStatusIcon = (status) => {
        const size = embedded ? 18 : 24;
        switch (status) {
            case 'completed': return <CheckCircleIcon color="success" sx={{ fontSize: size }} />;
            case 'running': return <CircularProgress size={size} />;
            case 'failed': return <ErrorIcon color="error" sx={{ fontSize: size }} />;
            default: return <PendingIcon color="disabled" sx={{ fontSize: size }} />;
        }
    };

    return (
        <React.Fragment>
            <ListItem
                alignItems="flex-start"
                dense={embedded}
                sx={{
                    bgcolor: step.status === 'running' ? 'action.hover' : 'inherit',
                    borderRadius: 1,
                    mb: embedded ? 0.5 : 1,
                    py: embedded ? 0.5 : 1,
                    px: embedded ? 0 : undefined,
                }}
            >
                <ListItemIcon sx={{ minWidth: embedded ? 28 : 56 }}>
                    {getStatusIcon(step.status)}
                </ListItemIcon>
                <ListItemText
                    primary={
                        <Box display="flex" justifyContent="space-between" alignItems="flex-start" gap={1}>
                            <Typography variant={embedded ? 'caption' : 'subtitle1'} sx={{ fontWeight: embedded ? 600 : undefined }}>
                                {t('agents.plan.step', { id: step.id, description: step.description })}
                            </Typography>
                            <Chip
                                label={step.assigned_agent}
                                size="small"
                                color="primary"
                                variant="outlined"
                                sx={embedded ? { height: 18, fontSize: '0.6rem' } : undefined}
                            />
                        </Box>
                    }
                    secondary={
                        <React.Fragment>
                            <Typography component="span" variant="caption" color="text.secondary">
                                {t('agents.plan.stepStatus', { status: statusLabel(step.status) })}
                            </Typography>
                            {step.result && (
                                <Button
                                    size="small"
                                    onClick={() => setExpanded(!expanded)}
                                    endIcon={expanded ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
                                    sx={{ ml: 1, minWidth: 0, fontSize: embedded ? '0.65rem' : undefined }}
                                >
                                    {t('agents.plan.viewResult')}
                                </Button>
                            )}
                            {step.error && (
                                <Typography color="error" variant="caption" sx={{ mt: 0.5, display: 'block' }}>
                                    {t('agents.plan.stepError', { error: step.error })}
                                </Typography>
                            )}
                        </React.Fragment>
                    }
                />
            </ListItem>
            <Collapse in={expanded} timeout="auto" unmountOnExit>
                <Box sx={{
                    ml: embedded ? 3.5 : 9,
                    mr: embedded ? 0 : 2,
                    mb: embedded ? 1 : 2,
                    p: embedded ? 1 : 2,
                    bgcolor: 'background.paper',
                    border: 1,
                    borderColor: 'divider',
                    borderRadius: 1,
                }}>
                    <Typography variant="caption" style={{ whiteSpace: 'pre-wrap' }}>
                        {step.result}
                    </Typography>
                </Box>
            </Collapse>
        </React.Fragment>
    );
};

StepItem.propTypes = {
    step: PropTypes.object.isRequired,
    embedded: PropTypes.bool,
};

const OrchestratorPlanView = ({ plan, planId, onExecutionComplete, embedded = false }) => {
    const { t } = useUiTranslation();
    const [currentPlan, setCurrentPlan] = useState(plan);
    const [executing, setExecuting] = useState(false);
    const [expanded, setExpanded] = useState(
        plan ? (plan.status !== 'completed' && plan.status !== 'failed') : true
    );

    useEffect(() => {
        setCurrentPlan(plan);
    }, [plan]);

    useEffect(() => {
        if (currentPlan?.status === 'executing') {
            setExpanded(true);
        }
    }, [currentPlan?.status]);

    useEffect(() => {
        let timer;
        const checkStatus = async () => {
            if (!planId) return;
            try {
                const response = await getPlanStatus(planId);
                if (response.success && response.plan) {
                    setCurrentPlan(response.plan);
                    if (response.plan.status === 'completed' || response.plan.status === 'failed') {
                        if (onExecutionComplete) {
                            onExecutionComplete({
                                success: response.plan.status === 'completed',
                                plan: response.plan,
                                final_answer: response.plan.final_answer || (response.plan.status === 'completed' ? 'Plan executed successfully.' : undefined)
                            });
                        }
                    }
                }
            } catch (error) {
                console.error("Error polling plan status:", error);
            }
        };

        if (currentPlan && currentPlan.status === 'executing') {
            timer = setInterval(checkStatus, 3000);
            checkStatus();
        }

        return () => {
            if (timer) clearInterval(timer);
        };
    }, [currentPlan?.status, planId, onExecutionComplete]);

    const handleExecute = async () => {
        if (!planId) {
            console.error("No plan ID provided");
            return;
        }
        setExecuting(true);
        setCurrentPlan((prev) => ({ ...prev, status: 'executing' }));
        try {
            const result = await executePlan(planId);
            if (result.success) {
                const payload = result.result ?? result;
                if (payload.plan) {
                    setCurrentPlan(payload.plan);
                }
                if (onExecutionComplete) {
                    onExecutionComplete(payload);
                }
            }
        } catch (error) {
            console.error("Execution failed", error);
        } finally {
            setExecuting(false);
        }
    };

    if (!currentPlan) return null;

    const getStatusColor = (status) => {
        switch (status) {
            case 'completed': return 'success.main';
            case 'executing': return 'warning.main';
            case 'failed': return 'error.main';
            default: return 'primary.main';
        }
    };

    const stepCount = currentPlan.steps?.length || 0;

    return (
        <Box
            sx={{
                my: embedded ? 0.5 : 1.5,
                borderLeft: 3,
                borderColor: getStatusColor(currentPlan.status),
                borderRadius: 1,
                bgcolor: "action.hover",
                overflow: "hidden",
                opacity: embedded ? 0.95 : 0.98,
                width: '100%',
            }}
        >
            <Box
                sx={{
                    display: "flex",
                    alignItems: "center",
                    gap: embedded ? 0.5 : 1,
                    px: embedded ? 1 : 2,
                    py: embedded ? 0.5 : 1,
                    cursor: "pointer",
                    "&:hover": { bgcolor: "action.selected" },
                }}
                onClick={() => setExpanded((prev) => !prev)}
            >
                <AccountTreeIcon sx={{ fontSize: embedded ? 14 : 18, color: getStatusColor(currentPlan.status) }} />
                <Typography
                    variant={embedded ? 'caption' : 'subtitle2'}
                    sx={{
                        fontWeight: 600,
                        fontFamily: embedded ? 'monospace' : undefined,
                        color: "text.primary",
                        flex: 1,
                    }}
                >
                    {t('agents.plan.heading', { count: stepCount })}
                </Typography>

                <Box display="flex" alignItems="center" gap={embedded ? 0.5 : 1.5}>
                    <Chip
                        label={statusLabel(currentPlan.status)}
                        size="small"
                        color={
                            currentPlan.status === 'completed' ? 'success' :
                            currentPlan.status === 'executing' ? 'warning' :
                            currentPlan.status === 'failed' ? 'error' : 'default'
                        }
                        sx={{ height: embedded ? 18 : 20, fontSize: embedded ? '0.6rem' : '0.7rem', textTransform: 'capitalize' }}
                    />
                    {embedded ? (
                        <IconButton size="small" sx={{ p: 0 }}>
                            {expanded ? (
                                <ExpandLessIcon sx={{ fontSize: 16, color: "text.secondary" }} />
                            ) : (
                                <ExpandMoreIcon sx={{ fontSize: 16, color: "text.secondary" }} />
                            )}
                        </IconButton>
                    ) : expanded ? (
                        <KeyboardArrowUpIcon sx={{ fontSize: 18, color: "text.secondary" }} />
                    ) : (
                        <KeyboardArrowDownIcon sx={{ fontSize: 18, color: "text.secondary" }} />
                    )}
                </Box>
            </Box>

            <Collapse in={expanded}>
                {!embedded && <Divider />}
                <Box sx={{ p: embedded ? 1 : 2, pt: embedded ? 0.25 : 2 }}>
                    <List sx={{ p: 0 }}>
                        {(currentPlan.steps || []).map((step, index) => (
                            <StepItem key={step.id || index} step={step} embedded={embedded} />
                        ))}
                    </List>

                    {currentPlan.status === 'planning' && (
                        <Box display="flex" justifyContent="flex-end" mt={embedded ? 1 : 2}>
                            <Button
                                variant="contained"
                                color="primary"
                                size={embedded ? 'small' : 'medium'}
                                startIcon={executing ? <CircularProgress size={16} color="inherit" /> : <PlayCircleOutlineIcon />}
                                onClick={(e) => {
                                    e.stopPropagation();
                                    handleExecute();
                                }}
                                disabled={executing}
                            >
                                {executing ? t('agents.plan.executing') : t('agents.plan.execute')}
                            </Button>
                        </Box>
                    )}
                </Box>
            </Collapse>
        </Box>
    );
};

OrchestratorPlanView.propTypes = {
    plan: PropTypes.object,
    planId: PropTypes.string,
    onExecutionComplete: PropTypes.func,
    embedded: PropTypes.bool,
};

export default OrchestratorPlanView;
