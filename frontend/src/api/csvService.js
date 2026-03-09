// frontend/src/api/csvService.js
// Version 1.0: CSV comparison and generation utilities.
import { BASE_URL, handleResponse } from "./apiClient";

export const compareCsvFiles = async (file1, file2, keyColumn) => {
  const formData = new FormData();
  formData.append("file1", file1);
  formData.append("file2", file2);
  if (keyColumn) formData.append("key", keyColumn);
  try {
    const response = await fetch(`${BASE_URL}/csv/compare`, {
      method: "POST",
      body: formData,
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("csvService: Error comparing CSV files:", err.message);
    throw err;
  }
};

export const generateCsvFile = async (rows, columns, name) => {
  const payload = { rows: rows, columns: columns, name: name };
  try {
    const response = await fetch(`${BASE_URL}/csv/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await handleResponse(response);
    if (typeof data === "object" && data !== null && data.error)
      throw new Error(data.error);
    return data;
  } catch (err) {
    console.error("csvService: Error generating CSV file:", err.message);
    throw err;
  }
};
