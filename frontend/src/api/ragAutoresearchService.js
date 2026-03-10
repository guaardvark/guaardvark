import apiClient from './apiClient';

export const getAutoresearchStatus = () => apiClient.get('/autoresearch/status');
export const startAutoresearch = (maxExperiments = 0) =>
  apiClient.post('/autoresearch/start', { max_experiments: maxExperiments });
export const stopAutoresearch = () => apiClient.post('/autoresearch/stop');
export const getAutoresearchHistory = (page = 1, perPage = 20) =>
  apiClient.get(`/autoresearch/history?page=${page}&per_page=${perPage}`);
export const getAutoresearchConfig = () => apiClient.get('/autoresearch/config');
export const resetAutoresearchConfig = () => apiClient.post('/autoresearch/config/reset');
export const getAutoresearchSettings = () => apiClient.get('/autoresearch/settings');
export const updateAutoresearchSettings = (settings) =>
  apiClient.put('/autoresearch/settings', settings);
