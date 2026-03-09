// frontend/src/api/progressService.js
// Progress job monitoring service

import { BASE_URL, handleResponse } from './apiClient';

/**
 * Get active progress jobs with enhanced stuck job detection
 * @returns {Promise<{active_jobs: Array, stuck_jobs: Array, stuck_count: number, system_healthy: boolean}>} Enhanced jobs data
 */
export const getProgressJobs = async () => {
  try {
    const response = await fetch(`${BASE_URL}/meta/active_jobs`, {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await handleResponse(response);
    return data;
  } catch (error) {
    console.error('Error fetching progress jobs:', error);
    // Return safe defaults on error to prevent UI crashes
    return { 
      active_jobs: [], 
      stuck_jobs: [], 
      stuck_count: 0, 
      total_jobs: 0,
      celery_tasks_count: 0,
      system_healthy: true 
    };
  }
};

/**
 * Cancel a specific job
 * @param {string} jobId - The job ID to cancel
 * @returns {Promise} Cancel response
 */
export const cancelJob = async (jobId) => {
  try {
    const response = await fetch(`${BASE_URL}/meta/cancel_job/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await handleResponse(response);
    if (typeof data === 'object' && data !== null && data.error) {
      throw new Error(data.error);
    }
    return data;
  } catch (error) {
    console.error('Error cancelling job:', error);
    throw error;
  }
};

/**
 * Delete a specific job (removes from progress system)
 * @param {string} jobId - The job ID to delete
 * @returns {Promise} Delete response
 */
export const deleteJob = async (jobId) => {
  try {
    const response = await fetch(`${BASE_URL}/meta/delete_job/${jobId}`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await handleResponse(response);
    if (typeof data === 'object' && data !== null && data.error) {
      throw new Error(data.error);
    }
    return data;
  } catch (error) {
    console.error('Error deleting job:', error);
    throw error;
  }
};

/**
 * Retry a failed job
 * @param {string} jobId - The job ID to retry
 * @returns {Promise} Retry response
 */
export const retryJob = async (jobId) => {
  try {
    const response = await fetch(`${BASE_URL}/meta/retry_job/${jobId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await handleResponse(response);
    if (typeof data === 'object' && data !== null && data.error) {
      throw new Error(data.error);
    }
    return data;
  } catch (error) {
    console.error('Error retrying job:', error);
    throw error;
  }
};

/**
 * Clean up stuck jobs that have no active Celery tasks
 * @returns {Promise<{message: string, cleaned_count: number, stuck_jobs_found: number}>} Cleanup response
 */
export const cleanupStuckJobs = async () => {
  try {
    const response = await fetch(`${BASE_URL}/meta/cleanup_stuck_jobs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await handleResponse(response);
    if (typeof data === 'object' && data !== null && data.error) {
      throw new Error(data.error);
    }
    return data;
  } catch (error) {
    console.error('Error cleaning up stuck jobs:', error);
    throw error;
  }
}; 