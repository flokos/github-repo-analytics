import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/client.js'
import RepositoryForm from '../src/components/RepositoryForm.vue'

const URL = 'https://github.com/django/django'

function mountForm(submit) {
  return mount(RepositoryForm, { props: { submit } })
}

async function enter(wrapper, value) {
  await wrapper.get('input').setValue(value)
  await wrapper.get('form').trigger('submit')
}

function feedback(wrapper) {
  return wrapper.get('[data-test="form-feedback"]')
}

function rejection(code, { status = 400, message = 'From the API.', details = {} } = {}) {
  return vi.fn().mockRejectedValue(new ApiError({ status, code, message, details }))
}

describe('RepositoryForm', () => {
  it('submits the trimmed address and confirms the new repository', async () => {
    const submit = vi.fn().mockResolvedValue({ full_name: 'django/django', reactivated: false })
    const wrapper = mountForm(submit)

    await enter(wrapper, `  ${URL}  `)
    await flushPromises()

    expect(submit).toHaveBeenCalledExactlyOnceWith(URL)
    expect(feedback(wrapper).text()).toContain('django/django is now tracked')
    expect(feedback(wrapper).classes()).toContain('notice--success')
    expect(feedback(wrapper).attributes('role')).toBe('status')
    expect(wrapper.get('input').element.value).toBe('')
  })

  it('says so when a removed repository was brought back', async () => {
    const submit = vi.fn().mockResolvedValue({ full_name: 'django/django', reactivated: true })
    const wrapper = mountForm(submit)

    await enter(wrapper, URL)
    await flushPromises()

    expect(feedback(wrapper).text()).toContain('django/django is tracked again')
    expect(feedback(wrapper).text()).toContain('history was kept')
  })

  it('shows that GitHub is being checked and blocks a second submit meanwhile', async () => {
    let finish
    const submit = vi.fn(() => new Promise((resolve) => (finish = resolve)))
    const wrapper = mountForm(submit)

    await enter(wrapper, URL)

    const button = wrapper.get('button')
    expect(button.text()).toBe('Checking with GitHub…')
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.get('input').attributes('disabled')).toBeDefined()

    finish({ full_name: 'django/django', reactivated: false })
    await flushPromises()

    expect(button.text()).toBe('Add repository')
    expect(button.attributes('disabled')).toBeUndefined()
  })

  it('asks for an address instead of calling the API with nothing', async () => {
    const submit = vi.fn()
    const wrapper = mountForm(submit)

    await enter(wrapper, '   ')

    expect(submit).not.toHaveBeenCalled()
    expect(feedback(wrapper).text()).toContain('Paste the address')
    expect(feedback(wrapper).attributes('role')).toBe('alert')
  })

  it.each([
    [
      'an invalid address',
      'invalid_repository_url',
      400,
      'Only github.com repository addresses are supported.',
    ],
    ['a duplicate', 'duplicate_repository', 409, 'This repository is already being tracked.'],
    [
      'an unavailable or private repository',
      'repository_unavailable',
      422,
      'GitHub reports this repository as unavailable. It may not exist, or it may be private.',
    ],
    ['a repository that is not public', 'repository_not_public', 422, 'Only public repositories can be tracked.'],
  ])('explains %s and keeps the address for correction', async (_case, code, status, message) => {
    const wrapper = mountForm(rejection(code, { status, message }))

    await enter(wrapper, URL)
    await flushPromises()

    expect(feedback(wrapper).text()).toBe(message)
    expect(feedback(wrapper).classes()).toContain('notice--error')
    expect(feedback(wrapper).attributes('role')).toBe('alert')
    expect(wrapper.get('input').attributes('aria-invalid')).toBe('true')
    expect(wrapper.get('input').element.value).toBe(URL)
  })

  it('tells the user when to retry after a GitHub rate limit', async () => {
    const wrapper = mountForm(
      rejection('github_rate_limited', { status: 503, details: { retry_after: 1500 } }),
    )

    await enter(wrapper, URL)
    await flushPromises()

    expect(feedback(wrapper).text()).toContain("GitHub's rate limit has been reached")
    expect(feedback(wrapper).text()).toContain('in about 25 minutes')
    // A temporary problem is not presented as the user's mistake.
    expect(feedback(wrapper).classes()).toContain('notice--warning')
    expect(wrapper.get('input').attributes('aria-invalid')).toBe('false')
  })

  it.each([
    ['GitHub timing out', 'github_timeout', 504],
    ['GitHub being down', 'github_unavailable', 502],
    ['the server being unreachable', 'network_error', 0],
  ])('treats %s as temporary', async (_case, code, status) => {
    const wrapper = mountForm(rejection(code, { status }))

    await enter(wrapper, URL)
    await flushPromises()

    expect(feedback(wrapper).text()).toMatch(/try again/i)
    expect(feedback(wrapper).classes()).toContain('notice--warning')
    expect(wrapper.get('input').element.value).toBe(URL)
  })

  it('clears the previous message when a new attempt starts', async () => {
    const submit = rejection('duplicate_repository', { status: 409, message: 'Already tracked.' })
    const wrapper = mountForm(submit)
    await enter(wrapper, URL)
    await flushPromises()
    expect(feedback(wrapper).text()).toBe('Already tracked.')

    submit.mockReturnValue(new Promise(() => {}))
    await wrapper.get('form').trigger('submit')

    expect(feedback(wrapper).text()).toBe('')
  })
})
