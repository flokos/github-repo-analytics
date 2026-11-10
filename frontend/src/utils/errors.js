import { ApiError } from '../api/client.js'

/**
 * Turns a failed API call into something to show the user.
 *
 * `temporary` is true when the same action may work if tried again later (GitHub or the server
 * is having trouble) and false when the user has to change something (a bad address, a
 * duplicate, a private repository).
 *
 * @returns {{ message: string, temporary: boolean }}
 */
export function describeError(error) {
  if (!(error instanceof ApiError)) {
    return { message: 'Something went wrong. Try again.', temporary: true }
  }

  switch (error.code) {
    case 'validation_error':
      return {
        message:
          'Paste the address of a GitHub repository, for example https://github.com/owner/repository.',
        temporary: false,
      }

    case 'github_rate_limited': {
      const minutes = Math.ceil((error.details?.retry_after ?? 0) / 60)
      const when = minutes > 0 ? `in about ${minutes} minute${minutes === 1 ? '' : 's'}` : 'later'
      return {
        message: `GitHub's rate limit has been reached, so the repository could not be checked. Nothing was saved. Try again ${when}.`,
        temporary: true,
      }
    }

    case 'github_timeout':
    case 'github_unavailable':
      return {
        message:
          'GitHub is not responding right now, so the repository could not be checked. Nothing was saved. Try again in a moment.',
        temporary: true,
      }

    case 'throttled':
      return {
        message: 'Too many attempts in a short time. Wait a moment, then try again.',
        temporary: true,
      }

    case 'network_error':
      return {
        message: 'The server cannot be reached. Check that the application is running, then try again.',
        temporary: true,
      }

    default:
      // Everything else (invalid address, duplicate, unavailable or private repository, not
      // found) arrives with a message written for people by the API.
      return {
        message: error.message || 'The request failed.',
        temporary: error.status >= 500,
      }
  }
}
