import { beforeEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '../src/api/client.js'

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function errorResponse(status, code, message, details = {}) {
  return jsonResponse({ error: { code, message, details } }, status)
}

describe('API client', () => {
  let fetchMock

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('reads repositories from the same-origin API', async () => {
    fetchMock.mockResolvedValue(jsonResponse([{ id: 1 }]))

    const repositories = await api.listRepositories('all')

    expect(repositories).toEqual([{ id: 1 }])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/repositories/?status=all',
      expect.objectContaining({ method: 'GET' }),
    )
  })

  it('posts the address as JSON when adding a repository', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 1, full_name: 'django/django' }, 201))

    await api.addRepository('https://github.com/django/django')

    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/repositories/')
    expect(options.method).toBe('POST')
    expect(options.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(options.body)).toEqual({ url: 'https://github.com/django/django' })
  })

  it('treats 204 No Content as success when removing a repository', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))

    await expect(api.removeRepository(7)).resolves.toBeNull()
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/repositories/7/',
      expect.objectContaining({ method: 'DELETE' }),
    )
  })

  it('builds the other read endpoints', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse([])))

    await api.getSnapshots(3, 90)
    await api.getSummary()
    await api.listReports(5)
    await api.getPipelineStatus()

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/repositories/3/snapshots/?days=90',
      '/api/dashboard/summary/',
      '/api/reports/?limit=5',
      '/api/pipeline/status/',
    ])
  })

  it('turns the API error envelope into an ApiError', async () => {
    fetchMock.mockResolvedValue(
      errorResponse(503, 'github_rate_limited', 'GitHub rate limit reached.', { retry_after: 120 }),
    )

    const error = await api.addRepository('https://github.com/django/django').catch((e) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 503,
      code: 'github_rate_limited',
      message: 'GitHub rate limit reached.',
      details: { retry_after: 120 },
    })
  })

  it('still produces an ApiError when the error body is not JSON', async () => {
    fetchMock.mockResolvedValue(new Response('<html>Bad Gateway</html>', { status: 502 }))

    const error = await api.getSummary().catch((e) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 502, code: 'unexpected_response' })
    expect(error.message).toContain('502')
  })

  it('rejects a successful response that is not JSON', async () => {
    fetchMock.mockResolvedValue(new Response('<html>hello</html>', { status: 200 }))

    await expect(api.getSummary()).rejects.toMatchObject({ code: 'unexpected_response' })
  })

  it('reports an unreachable server as a network error', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const error = await api.listRepositories().catch((e) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 0, code: 'network_error' })
  })
})
