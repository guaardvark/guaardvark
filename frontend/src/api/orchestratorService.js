import { BASE_URL, handleResponse } from './apiClient';

/**
 * Orchestrator Service
 * Handles communication with the backend Orchestrator API
 */

export const createPlan = async (request, context = {}) => {
    try {
        const response = await fetch(`${BASE_URL}/orchestrator/plan`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ request, context })
        });
        return handleResponse(response);
    } catch (error) {
        console.error('Error creating plan:', error);
        throw error;
    }
};

export const executePlan = async (planId, context = {}) => {
    try {
        const response = await fetch(`${BASE_URL}/orchestrator/execute`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ plan_id: planId, context })
        });
        return handleResponse(response);
    } catch (error) {
        console.error('Error executing plan:', error);
        throw error;
    }
};

export const getPlanStatus = async (planId) => {
    try {
        const response = await fetch(`${BASE_URL}/orchestrator/status/${planId}`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            }
        });
        return handleResponse(response);
    } catch (error) {
        console.error('Error getting plan status:', error);
        throw error;
    }
};

export const orchestratorService = {
    createPlan,
    executePlan,
    getPlanStatus
};

export default orchestratorService;
