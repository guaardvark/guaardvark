// frontend/src/api/wordpressService.js
// WordPress Integration API Service

import { BASE_URL, handleResponse } from "./apiClient";

const WORDPRESS_API_BASE = `${BASE_URL}/wordpress`;

/**
 * WordPress Sites API
 */
export const getWordPressSites = async () => {
  const response = await fetch(`${WORDPRESS_API_BASE}/sites`);
  return handleResponse(response);
};

export const getWordPressSite = async (siteId) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/sites/${siteId}`);
  return handleResponse(response);
};

export const registerWordPressSite = async (siteData) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/sites`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(siteData),
  });
  return handleResponse(response);
};

export const updateWordPressSite = async (siteId, siteData) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/sites/${siteId}`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(siteData),
  });
  return handleResponse(response);
};

export const deleteWordPressSite = async (siteId) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/sites/${siteId}`, {
    method: "DELETE",
  });
  return handleResponse(response);
};

export const testWordPressConnection = async (siteId) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/sites/${siteId}/test`, {
    method: "POST",
  });
  return handleResponse(response);
};

/**
 * WordPress Pull API
 */
export const pullPageList = async (siteId, options = {}) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/pull/list`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      site_id: siteId,
      post_type: options.post_type || "post",
      per_page: options.per_page || 100,
      max_pages: options.max_pages,
      filters: options.filters,
    }),
  });
  return handleResponse(response);
};

export const pullSinglePage = async (siteId, postId, options = {}) => {
  const response = await fetch(
    `${WORDPRESS_API_BASE}/pull/page/${siteId}/${postId}`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        post_type: options.post_type || "post",
        update_existing: options.update_existing !== false,
      }),
    }
  );
  return handleResponse(response);
};

export const pullBulkPages = async (siteId, postIds, options = {}) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/pull/bulk`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      site_id: siteId,
      post_ids: postIds,
      post_type: options.post_type || "post",
    }),
  });
  return handleResponse(response);
};

export const pullSitemap = async (siteId, options = {}) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/pull/sitemap`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      site_id: siteId,
      extract_post_ids: options.extract_post_ids || false,
    }),
  });
  return handleResponse(response);
};

export const getPullStatus = async (siteId) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/pull/status/${siteId}`);
  return handleResponse(response);
};

/**
 * WordPress Processing API
 */
export const processPage = async (pageId, processType = "full") => {
  const response = await fetch(`${WORDPRESS_API_BASE}/process/page/${pageId}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      type: processType, // full, content, seo, schema
    }),
  });
  return handleResponse(response);
};

export const queuePagesForProcessing = async (pageIds, options = {}) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/process/queue`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      page_ids: pageIds,
      type: options.type || "full",
      site_id: options.site_id,
    }),
  });
  return handleResponse(response);
};

export const executeProcessingQueue = async (options = {}) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/process/queue/execute`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      site_id: options.site_id,
      type: options.type || "full",
      max_pages: options.max_pages || 10,
    }),
  });
  return handleResponse(response);
};

export const getProcessingStatus = async (pageId) => {
  const response = await fetch(
    `${WORDPRESS_API_BASE}/process/status/${pageId}`
  );
  return handleResponse(response);
};

/**
 * WordPress Pages API
 */
export const getWordPressPages = async (filters = {}) => {
  const params = new URLSearchParams();
  if (filters.site_id) params.append("site_id", filters.site_id);
  if (filters.process_status) params.append("process_status", filters.process_status);
  if (filters.pull_status) params.append("pull_status", filters.pull_status);
  if (filters.post_type) params.append("post_type", filters.post_type);
  if (filters.limit) params.append("limit", filters.limit);
  if (filters.offset) params.append("offset", filters.offset);

  const response = await fetch(
    `${WORDPRESS_API_BASE}/pages?${params.toString()}`
  );
  return handleResponse(response);
};

export const getWordPressPage = async (pageId) => {
  const response = await fetch(`${WORDPRESS_API_BASE}/pages/${pageId}`);
  return handleResponse(response);
};

