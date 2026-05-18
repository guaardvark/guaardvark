import React, { useState } from 'react';
import {
    Box,
    Paper,
    Typography,
    List,
    ListItem,
    ListItemText,
    ListItemIcon,
    Chip,
    Button,
    CircularProgress,
    Divider,
    Collapse
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import PendingIcon from '@mui/icons-material/Pending';
import PlayCircleOutlineIcon from '@mui/icons-material/PlayCircleOutline';
import ErrorIcon from '@mui/icons-material/Error';
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown';
import KeyboardArrowUpIcon from '@mui/icons-material/KeyboardArrowUp';
import { executePlan } from '../../api/orchestratorService';

const StepItem = ({ step, _index }) => {
    const [expanded, setExpanded] = useState(false);

    const getStatusIcon = (status) => {
        switch (status) {
            case 'completed': return <CheckCircleIcon color="success" />;
            case 'running': return <CircularProgress size={24} />;
            case 'failed': return <ErrorIcon color="error" />;
            default: return <PendingIcon color="disabled" />;
        }
    };

    return (
        <React.Fragment>
            <ListItem
                alignItems="flex-start"
                sx={{
                    bgcolor: step.status === 'running' ? 'action.hover' : 'inherit',
                    borderRadius: 1,
                    mb: 1
                }}
            >
                <ListItemIcon>
                    {getStatusIcon(step.status)}
                </ListItemIcon>
                <ListItemText
                    primary={
                        <Box display="flex" justifyContent="space-between" alignItems="center">
                            <Typography variant="subtitle1">
                                Step {step.id}: {step.description}
                            </Typography>
                            <Chip
                                label={step.assigned_agent}
                                size="small"
                                color="primary"
                                variant="outlined"
                            />
                        </Box>
                    }
                    secondary={
                        <React.Fragment>
                            <Typography component="span" variant="body2" color="text.secondary">
                                Status: {step.status}
                            </Typography>
                            {step.result && (
                                <Button
                                    size="small"
                                    onClick={() => setExpanded(!expanded)}
                                    endIcon={expanded ? <KeyboardArrowUpIcon /> : <KeyboardArrowDownIcon />}
                                    sx={{ ml: 1 }}
                                >
                                    View Result
                                </Button>
                            )}
                            {step.error && (
                                <Typography color="error" variant="body2" sx={{ mt: 1 }}>
                                    Error: {step.error}
                                </Typography>
                            )}
                        </React.Fragment>
                    }
                />
            </ListItem>
            <Collapse in={expanded} timeout="auto" unmountOnExit>
                <Box sx={{ ml: 9, mr: 2, mb: 2, p: 2, bgcolor: 'background.paper', border: 1, borderColor: 'divider', borderRadius: 1 }}>
                    <Typography variant="body2" style={{ whiteSpace: 'pre-wrap' }}>
                        {step.result}
                    </Typography>
                </Box>
            </Collapse>
        </React.Fragment>
    );
};

const OrchestratorPlanView = ({ plan, planId, onExecutionComplete }) => {
    const [currentPlan, setCurrentPlan] = useState(plan);
    const [executing, setExecuting] = useState(false);

    const handleExecute = async () => {
        if (!planId) {
            console.error("No plan ID provided");
            return;
        }
        setExecuting(true);
        try {
            const result = await executePlan(planId);
            if (result.success) {
                if (result.plan) {
                    setCurrentPlan(result.plan);
                }
                if (onExecutionComplete) {
                    onExecutionComplete(result);
                }
            }
        } catch (error) {
            console.error("Execution failed", error);
        } finally {
            setExecuting(false);
        }
    };

    return (
        <Paper elevation={3} sx={{ p: 2, mt: 2, mb: 2 }}>
            <Box display="flex" justifyContent="space-between" alignItems="center" mb={2}>
                <Typography variant="h6">Orchestration Plan</Typography>
                <Typography variant="subtitle2" color="text.secondary">
                    Status: {currentPlan.status}
                </Typography>
            </Box>

            <Divider sx={{ mb: 2 }} />

            <List>
                {currentPlan.steps.map((step, index) => (
                    <StepItem key={step.id || index} step={step} index={index} />
                ))}
            </List>

            {currentPlan.status === 'planning' && (
                <Box display="flex" justifyContent="flex-end" mt={2}>
                    <Button
                        variant="contained"
                        color="primary"
                        startIcon={executing ? <CircularProgress size={20} color="inherit" /> : <PlayCircleOutlineIcon />}
                        onClick={handleExecute}
                        disabled={executing}
                    >
                        {executing ? 'Executing...' : 'Execute Plan'}
                    </Button>
                </Box>
            )}
        </Paper>
    );
};

export default OrchestratorPlanView;
