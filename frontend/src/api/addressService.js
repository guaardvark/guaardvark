// frontend/src/api/addressService.js
// Address suggestions: on-file addresses first, then an optional provider.
import { BASE_URL, handleResponse } from "./apiClient";

/**
 * Suggest addresses matching `q`.
 *
 * Never rejects — an address field must stay typeable when the lookup fails.
 *
 * @param {object} params
 * @param {string} params.q
 * @param {number} [params.limit]
 * @returns {Promise<{items: Array, attribution: string|null}>}
 */
export const suggestAddresses = async ({ q, limit } = {}) => {
  const query = (q || "").trim();
  if (query.length < 2) return { items: [], attribution: null };
  try {
    const params = new URLSearchParams({ q: query });
    if (limit) params.set("limit", String(limit));
    const response = await fetch(`${BASE_URL}/addresses?${params}`);
    const data = await handleResponse(response);
    return {
      items: Array.isArray(data?.items) ? data.items : [],
      attribution: data?.attribution ?? null,
    };
  } catch (err) {
    console.error("addressService: suggestion failed:", err.message);
    return { items: [], attribution: null };
  }
};

/**
 * Address-provider status. Never includes the stored key.
 *
 * @returns {Promise<{provider: string, has_key: boolean, available: boolean,
 *   unavailable_reason: string|null, attribution: string|null}>}
 */
export const getAddressProvider = async () => {
  try {
    const response = await fetch(`${BASE_URL}/settings/address_provider`);
    const data = await handleResponse(response);
    return data?.data ?? data ?? {};
  } catch (err) {
    console.error("addressService: provider status failed:", err.message);
    return { available: false, has_key: false, unavailable_reason: null };
  }
};

/** Store the provider key ("" clears it). Throws with the server's message. */
export const setAddressProvider = async (body) => {
  const response = await fetch(`${BASE_URL}/settings/address_provider`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await handleResponse(response);
  if (data?.error) {
    throw new Error(
      typeof data.error === "string" ? data.error : data.error.message || "Request failed",
    );
  }
  return data?.data ?? data;
};
