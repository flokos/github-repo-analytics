/**
 * The only module that talks to the Django API.
 *
 * Every failure, whatever its cause, surfaces as an `ApiError` carrying the stable `code` from
 * the API's error envelope (`{"error": {"code", "message", "details"}}`), so components can
 * decide what to tell the user without looking at HTTP details.
 */

// Same origin by default: nginx serves the dashboard and proxies /api to Django.
// The Vite dev server sets VITE_API_BASE_URL to call Django directly (cross-origin, via CORS).
const BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/, '')

export class ApiError extends Error {
  constructor({ status, code, message, details = {} }) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

async function request(path, { method = 'GET', body } = {}) {
  const headers = { Accept: 'application/json' }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  let response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError({
      status: 0,
      code: 'network_error',
      message: 'The server cannot be reached.',
    })
  }

  if (response.status === 204) {
    return null
  }

  let payload = null
  try {
    payload = await response.json()
  } catch {
    // A body that is not JSON (a proxy error page, for example) is handled below.
  }

  if (!response.ok) {
    const error = payload?.error ?? {}
    throw new ApiError({
      status: response.status,
      code: error.code ?? 'unexpected_response',
      message: error.message ?? `The request failed (HTTP ${response.status}).`,
      details: error.details ?? {},
    })
  }
  if (payload === null) {
    throw new ApiError({
      status: response.status,
      code: 'unexpected_response',
      message: 'The server sent a response that could not be read.',
    })
  }
  return payload
}

export const api = {
  /** @param {'active' | 'inactive' | 'all'} status */
  listRepositories(status = 'active') {
    return request(`/repositories/?status=${encodeURIComponent(status)}`)
  },
  /** Validated by the backend against GitHub before anything is stored. */
  addRepository(url) {
    return request('/repositories/', { method: 'POST', body: { url } })
  },
  /** Soft delete: the repository leaves active tracking, its history stays. */
  removeRepository(id) {
    return request(`/repositories/${id}/`, { method: 'DELETE' })
  },
  getSnapshots(id, days) {
    return request(`/repositories/${id}/snapshots/?days=${days}`)
  },
  getSummary() {
    return request('/dashboard/summary/')
  },
  listReports(limit = 14) {
    return request(`/reports/?limit=${limit}`)
  },
  getPipelineStatus() {
    return request('/pipeline/status/')
  },
}
