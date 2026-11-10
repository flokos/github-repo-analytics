/** Sample API responses shared by the tests, shaped exactly like the real ones. */

export const django = {
  id: 1,
  github_id: 4164482,
  owner: 'django',
  name: 'django',
  full_name: 'django/django',
  html_url: 'https://github.com/django/django',
  is_active: true,
  created_at: '2026-11-01T09:00:00Z',
  deactivated_at: null,
  last_synced_at: '2026-11-10T06:00:05Z',
  latest_snapshot: {
    snapshot_date: '2026-11-10',
    stars: 91699,
    forks: 35213,
    open_issues: 534,
    collected_at: '2026-11-10T06:00:05Z',
  },
}

/** Tracked, but the pipeline has not collected it yet. */
export const airflow = {
  id: 2,
  github_id: 33884891,
  owner: 'apache',
  name: 'airflow',
  full_name: 'apache/airflow',
  html_url: 'https://github.com/apache/airflow',
  is_active: true,
  created_at: '2026-11-10T08:00:00Z',
  deactivated_at: null,
  last_synced_at: null,
  latest_snapshot: null,
}

export const removed = {
  id: 3,
  github_id: 11730342,
  owner: 'vuejs',
  name: 'vue',
  full_name: 'vuejs/vue',
  html_url: 'https://github.com/vuejs/vue',
  is_active: false,
  created_at: '2026-10-01T09:00:00Z',
  deactivated_at: '2026-11-09T12:30:00Z',
  last_synced_at: '2026-11-09T06:00:03Z',
  latest_snapshot: {
    snapshot_date: '2026-11-09',
    stars: 209000,
    forks: 33700,
    open_issues: 600,
    collected_at: '2026-11-09T06:00:03Z',
  },
}

export const summary = {
  active_repositories: 2,
  repositories_with_data: 1,
  total_stars: 91699,
  total_forks: 35213,
  total_open_issues: 534,
  last_synced_at: '2026-11-10T06:00:05Z',
}

export const completedReport = {
  id: 7,
  snapshot_date: '2026-11-10',
  status: 'completed',
  requested_at: '2026-11-10T06:00:20Z',
  started_at: '2026-11-10T06:00:21Z',
  finished_at: '2026-11-10T06:00:22Z',
  attempts: 1,
  error_message: '',
  repository_count: 2,
  total_stars: 131699,
  total_forks: 50213,
  total_open_issues: 1534,
  stars_delta: 42,
  forks_delta: -3,
  open_issues_delta: 5,
  repository_changes: [
    {
      repository_id: 1,
      full_name: 'django/django',
      stars: 91699,
      forks: 35213,
      open_issues: 534,
      stars_delta: 42,
      forks_delta: -3,
      open_issues_delta: 5,
      previous_snapshot_date: '2026-11-09',
    },
    {
      repository_id: 2,
      full_name: 'apache/airflow',
      stars: 40000,
      forks: 15000,
      open_issues: 1000,
      stars_delta: null,
      forks_delta: null,
      open_issues_delta: null,
      previous_snapshot_date: null,
    },
  ],
}

export const pipeline = {
  collection: {
    last_collected_at: '2026-11-10T06:00:05Z',
    latest_snapshot_date: '2026-11-10',
    repositories_collected: 1,
    active_repositories: 2,
  },
  latest_report: completedReport,
}

export const snapshots = [
  { snapshot_date: '2026-11-08', stars: 91600, forks: 35200, open_issues: 540, collected_at: '2026-11-08T06:00:04Z' },
  { snapshot_date: '2026-11-09', stars: 91657, forks: 35216, open_issues: 529, collected_at: '2026-11-09T06:00:03Z' },
  { snapshot_date: '2026-11-10', stars: 91699, forks: 35213, open_issues: 534, collected_at: '2026-11-10T06:00:05Z' },
]

/** A report in any state, based on the completed one. */
export function reportWith(overrides) {
  return { ...completedReport, ...overrides }
}
