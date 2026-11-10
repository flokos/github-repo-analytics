import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import RepositoryList from '../src/components/RepositoryList.vue'
import { airflow, django, removed } from './fixtures.js'

function mountList(props = {}) {
  return mount(RepositoryList, {
    props: { repositories: [django, airflow], removed: [], ...props },
  })
}

describe('RepositoryList', () => {
  it('shows the current metrics and the last sync of a collected repository', () => {
    const card = mountList().get('[data-test="repository-1"]')

    expect(card.get('a').text()).toBe('django/django')
    expect(card.get('a').attributes('href')).toBe('https://github.com/django/django')
    expect(card.text()).toContain('91,699')
    expect(card.text()).toContain('35,213')
    expect(card.text()).toContain('534')
    expect(card.get('[data-test="last-synced"]').text()).toContain('10 Nov 2026, 06:00 UTC')
  })

  it('explains that a repository has not been collected yet', () => {
    const card = mountList().get('[data-test="repository-2"]')

    expect(card.get('[data-test="no-metrics"]').text()).toContain('No metrics yet')
    expect(card.get('[data-test="last-synced"]').text()).toBe('Never synced')
  })

  it('asks to remove a repository', async () => {
    const wrapper = mountList()

    await wrapper.get('[aria-label="Remove django/django from tracking"]').trigger('click')

    expect(wrapper.emitted('remove')).toEqual([[django]])
  })

  it('shows a removal in progress and prevents a second click', () => {
    const wrapper = mountList({ busyIds: [1] })
    const button = wrapper.get('[aria-label="Remove django/django from tracking"]')

    expect(button.text()).toBe('Removing…')
    expect(button.attributes('disabled')).toBeDefined()
  })

  it('marks the selected repository and lets another one be selected', async () => {
    const wrapper = mountList({ selectedId: 1 })
    const selected = wrapper.get('[data-test="repository-1"]')
    const other = wrapper.get('[data-test="repository-2"]')

    expect(selected.classes()).toContain('repository--selected')
    expect(selected.get('[aria-pressed]').attributes('aria-pressed')).toBe('true')
    expect(other.get('[aria-pressed]').attributes('aria-pressed')).toBe('false')

    await other.get('[aria-pressed]').trigger('click')

    expect(wrapper.emitted('select')).toEqual([[airflow]])
  })

  it('has a loading state', () => {
    const wrapper = mountList({ repositories: [], loading: true })

    expect(wrapper.find('[data-test="repositories-loading"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="repositories-empty"]').exists()).toBe(false)
  })

  it('has an empty state that says what to do', () => {
    const wrapper = mountList({ repositories: [] })

    expect(wrapper.get('[data-test="repositories-empty"]').text()).toContain(
      'No repositories are tracked yet',
    )
  })

  it('lists removed repositories separately and offers to restore them', async () => {
    const wrapper = mountList({ removed: [removed] })
    const section = wrapper.get('[data-test="removed-repositories"]')

    expect(section.get('summary').text()).toBe('Removed repositories (1)')
    expect(section.text()).toContain('vuejs/vue')
    expect(section.text()).toContain('9 Nov 2026, 12:30 UTC')
    expect(wrapper.find('[data-test="repository-3"]').exists()).toBe(false)

    await section.get('[aria-label="Restore vuejs/vue"]').trigger('click')

    expect(wrapper.emitted('restore')).toEqual([[removed]])
  })

  it('hides the removed section when nothing was removed', () => {
    expect(mountList().find('[data-test="removed-repositories"]').exists()).toBe(false)
  })
})
