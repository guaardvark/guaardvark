import { BASE_URL, handleResponse } from "./apiClient";

export const getBackendHealth = async () => {
  const response = await fetch(`${BASE_URL}/health`);
  return handleResponse(response);
};

export const getDbHealth = async () => {
  const response = await fetch(`${BASE_URL}/health/db`);
  return handleResponse(response);
};

export const getCeleryHealth = async () => {
  const response = await fetch(`${BASE_URL}/health/celery`);
  return handleResponse(response);
};

export const getRedisHealth = async () => {
  const response = await fetch(`${BASE_URL}/health/redis`);
  return handleResponse(response);
};

export const getCeleryTasks = async () => {
  const response = await fetch(`${BASE_URL}/celery/tasks`);
  return handleResponse(response);
};
