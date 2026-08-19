(function (global) {
  'use strict';

  const API_BASE = '';

  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
      this.payload = payload;
    }
  }

  async function request(path, options = {}) {
    const config = {
      method: options.method || 'GET',
      headers: { ...(options.headers || {}) },
    };

    if (options.body !== undefined) {
      config.body = JSON.stringify(options.body);
      config.headers['Content-Type'] = 'application/json';
    }

    const response = await fetch(`${API_BASE}${path}`, config);
    let payload = null;
    const contentType = response.headers.get('content-type') || '';

    if (contentType.includes('application/json')) {
      payload = await response.json();
    } else {
      const text = await response.text();
      if (text) {
        try {
          payload = JSON.parse(text);
        } catch {
          payload = text;
        }
      }
    }

    if (!response.ok) {
      const detail =
        payload && typeof payload === 'object' ? payload.detail : null;
      throw new ApiError(
        detail || response.statusText || `请求失败 (${response.status})`,
        response.status,
        payload
      );
    }

    return payload;
  }

  function query(params) {
    const search = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        search.append(key, String(value));
      }
    });
    const value = search.toString();
    return value ? `?${value}` : '';
  }

  const api = {
    request,
    ApiError,

    listRoots: () => request('/api/roots'),
    addRoot: (page_id) =>
      request('/api/roots', { method: 'POST', body: { page_id } }),
    deleteRoot: (root_id) =>
      request(`/api/roots/${encodeURIComponent(root_id)}`, { method: 'DELETE' }),
    getRootTree: (root_id) =>
      request(`/api/roots/${encodeURIComponent(root_id)}/tree`),
    refreshRootTree: (root_id) =>
      request(`/api/roots/${encodeURIComponent(root_id)}/refresh`, {
        method: 'POST',
      }),

    listPages: () => request('/api/pages'),
    getPageDetail: (page_id) =>
      request(`/api/pages/${encodeURIComponent(page_id)}`),
    syncPage: (page_id) =>
      request(`/api/pages/${encodeURIComponent(page_id)}/sync`, {
        method: 'POST',
      }),
    toggleVector: (page_id) =>
      request(`/api/pages/${encodeURIComponent(page_id)}/vector-toggle`, {
        method: 'POST',
      }),
    triggerVectorize: (page_id) =>
      request(`/api/pages/${encodeURIComponent(page_id)}/vectorize`, {
        method: 'POST',
      }),

    syncPages: (page_ids, force_full = false) =>
      request('/api/sync', {
        method: 'POST',
        body: { page_ids, force_full },
      }),
    getSyncProgress: (sync_id) =>
      request(`/api/sync/${encodeURIComponent(sync_id)}/progress`),

    debugSearch: (params) =>
      request('/api/search/debug', { method: 'POST', body: params }),
    summarizeSearch: (params) =>
      request('/api/search/summarize', { method: 'POST', body: params }),
    listHistory: (limit = 500, source = null) =>
      request(`/api/search/history${query({ limit, source })}`),
    getHistoryStats: () => request('/api/search/history/stats'),
    getHistory: (history_id) =>
      request(`/api/search/history/${encodeURIComponent(history_id)}`),
    replayHistory: (history_id) =>
      request(`/api/search/history/${encodeURIComponent(history_id)}/replay`, {
        method: 'POST',
      }),
    deleteHistory: (history_id) =>
      request(`/api/search/history/${encodeURIComponent(history_id)}`, {
        method: 'DELETE',
      }),
    clearHistory: () =>
      request('/api/search/history?clear=all', { method: 'DELETE' }),

    getWorkerStatus: () => request('/api/worker/status'),
  };

  global.RagApi = api;
})(window);
