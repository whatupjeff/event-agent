async function call(path, { method = 'GET', body } = {}) {
  const r = await fetch('/api' + path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || `${r.status} ${r.statusText}`)
  return data
}

export const api = {
  health: () => call('/health'),
  events: () => call('/events'),
  ask: (prompt) => call('/ask', { method: 'POST', body: { prompt } }),
  profile: () => call('/profile'),
  addNote: (text) => call('/profile/notes', { method: 'POST', body: { text } }),
  prepare: (event_id, kind, user = {}) => call('/actions', { method: 'POST', body: { event_id, kind, ...user } }),
  approve: (id, token) => call(`/actions/${id}/approve`, { method: 'POST', body: { token } }),
  reject: (id) => call(`/actions/${id}/reject`, { method: 'POST' }),
}

export const DEMO_PROMPTS = [
  'agent hackathons next week',
  'something design-related this week',
  'AI events in Palo Alto',
  'outdoor stuff this weekend',
]
