import axios from "axios";

/**
 * Production Service for Film Crew page.
 * Handles productions, subjects (casting), and storyboard approvals.
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export const listProductions = async () => {
  const response = await axios.get(`${API_BASE}/production`);
  return response.data;
};

export const getProduction = async (id) => {
  const response = await axios.get(`${API_BASE}/production/${id}`);
  return response.data;
};

export const listProductionSubjects = async (id) => {
  const response = await axios.get(`${API_BASE}/production/${id}/subjects`);
  return response.data;
};

export const createProduction = async (data) => {
  const response = await axios.post(`${API_BASE}/production`, data);
  return response.data;
};

export const castSubject = async (productionId, subjectId, data) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/cast/${subjectId}`, data);
  return response.data;
};

export const confirmCasting = async (productionId) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/casting/confirm`);
  return response.data;
};

export const approveStoryboard = async (productionId) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/storyboard/approve`);
  return response.data;
};

export const regenerateShot = async (productionId, shotId, data) => {
  const response = await axios.post(`${API_BASE}/production/${productionId}/storyboard/shot/${shotId}/regenerate`, data);
  return response.data;
};

export const listCastLibrary = async () => {
  const response = await axios.get(`${API_BASE}/cast-library`);
  return response.data;
};

export const createCastSubject = async (data) => {
  const response = await axios.post(`${API_BASE}/cast-library/subjects`, data);
  return response.data;
};

export const deleteCastSubject = async (id) => {
  await axios.delete(`${API_BASE}/cast-library/subjects/${id}`);
};

const productionService = {
  listProductions,
  getProduction,
  listProductionSubjects,
  createProduction,
  castSubject,
  confirmCasting,
  approveStoryboard,
  regenerateShot,
  listCastLibrary,
  createCastSubject,
  deleteCastSubject,
};

export default productionService;
