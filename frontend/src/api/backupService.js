import { BASE_URL, handleResponse } from "./apiClient";

export const createServerBackup = async (type, components = [], name = null, include_plugins = false) => {
  const response = await fetch(`${BASE_URL}/backups/create`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, components, name, include_plugins }),
  });
  return handleResponse(response);
};

export const restoreServerBackup = async (input) => {
  if (input instanceof File) {
    const fd = new FormData();
    fd.append("file", input);
    const response = await fetch(`${BASE_URL}/backups/restore`, {
      method: "POST",
      body: fd,
    });
    return handleResponse(response);
  } else {
    const response = await fetch(`${BASE_URL}/backups/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: input }),
    });
    return handleResponse(response);
  }
};

export const listServerBackups = async () => {
  const res = await fetch(`${BASE_URL}/backups`);
  return handleResponse(res);
};

export const deleteServerBackup = async (name) => {
  const res = await fetch(`${BASE_URL}/backups/${encodeURIComponent(name)}`, { method: "DELETE" });
  return handleResponse(res);
};

export const downloadServerBackup = async (name) => {
  const res = await fetch(`${BASE_URL}/backups/${encodeURIComponent(name)}/download`);
  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}));
    throw new Error(errorData.error || `Download failed: ${res.status}`);
  }
  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
};
