import { useEffect, useState } from 'react'
import { api, DEMO_PROMPTS } from './api'
import { AgentTeam, ApprovalCard, EventCard, TraceFeed, fmtDay } from './components'

const TABS = [['events', 'My events'], ['ask', 'Ask the agent'], ['profile', 'Your profile'], ['privacy', 'Privacy']]

export default function App() {
  const [tab, setTab] = useState('events')
  const [health, setHealth] = useState(null)
  const [trace, setTrace] = useState([])
  const [approvals, setApprovals] = useState([]) // [{action, token}] tokens live only in this tab's memory
  const [version, setVersion] = useState(0) // bump to reload data after an approved action
  const [error, setError] = useState('')
  const [userEmail, setUserEmail] = useState(() => localStorage.getItem('ea_email') || '')
  const [userName, setUserName] = useState(() => localStorage.getItem('ea_name') || '')
  const [needEmail, setNeedEmail] = useState(null) // {eventId, kind} when RSVP clicked without email
  const [emailInput, setEmailInput] = useState('')
  const [nameInput, setNameInput] = useState('')

  useEffect(() => { api.health().then(setHealth).catch((e) => setError(e.message)) }, [])
  useEffect(() => {
    const es = new EventSource('/api/trace')
    es.onmessage = (m) => setTrace((t) => [...t.slice(-300), JSON.parse(m.data)])
    return () => es.close()
  }, [])

  const run = (fn) => (...args) => fn(...args).then(() => setError('')).catch((e) => setError(e.message))
  const update = (a) => setApprovals((xs) => xs.map((x) => (x.action.id === a.id ? { ...x, action: a } : x)))

  const prepare = run(async (eventId, kind, overrideUser) => {
    const email = overrideUser?.email ?? userEmail
    const name = overrideUser?.name ?? userName
    if (kind === 'rsvp' && !email.trim()) {
      setEmailInput(userEmail)
      setNameInput(userName)
      setNeedEmail({ eventId, kind })
      return
    }
    const item = await api.prepare(eventId, kind, { email, name })
    setApprovals((xs) => [item, ...xs])
  })

  const saveEmailAndContinue = async (e) => {
    e.preventDefault()
    if (!emailInput.trim()) return
    const email = emailInput.trim()
    const name = nameInput.trim()
    localStorage.setItem('ea_email', email)
    if (name) localStorage.setItem('ea_name', name)
    setUserEmail(email)
    setUserName(name)
    const pendingReq = needEmail
    setNeedEmail(null)
    if (pendingReq) await prepare(pendingReq.eventId, pendingReq.kind, { email, name })
  }
  const approve = run(async (item) => {
    update(await api.approve(item.action.id, item.token))
    setVersion((v) => v + 1)
  })
  const reject = run(async (item) => update(await api.reject(item.action.id)))
  const pending = approvals.filter((x) => x.action.status === 'pending')

  return (
    <div className="app">
      <header>
        <div className="brand">Event Agent</div>
        <nav>
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? 'tab on' : 'tab'} onClick={() => setTab(id)}>{label}</button>
          ))}
        </nav>
        <div className="muted small">
          {health ? `${health.mode} mode · ${health.transport}` : 'connecting…'}
          {pending.length > 0 && <span className="pill">{pending.length} awaiting approval</span>}
        </div>
      </header>

      {error && <div className="error" onClick={() => setError('')}>{error} (click to dismiss)</div>}

      <main className={tab === 'ask' ? 'with-side' : ''}>
        <section>
          {needEmail && (
            <form className="card" onSubmit={saveEmailAndContinue} style={{ marginBottom: 12 }}>
              <b>Enter your details for this RSVP</b>
              <p className="muted small" style={{ margin: '4px 0 8px' }}>Used to fill the registration form instead of demo@example.com.</p>
              <div className="row" style={{ gap: 8 }}>
                <input type="text" placeholder="Your name" value={nameInput} onChange={(e) => setNameInput(e.target.value)} />
                <input type="email" required placeholder="you@email.com" value={emailInput} onChange={(e) => setEmailInput(e.target.value)} autoFocus />
                <button type="submit" className="primary">Continue</button>
                <button type="button" onClick={() => setNeedEmail(null)}>Cancel</button>
              </div>
            </form>
          )}
          {approvals.length > 0 && (
            <div className="approvals">
              {approvals.slice(0, 3).map((x) => <ApprovalCard key={x.action.id} item={x} onApprove={approve} onReject={reject} />)}
            </div>
          )}
          {tab === 'events' && <MyEvents version={version} onPrepare={prepare} onError={setError} />}
          {tab === 'ask' && <Ask onPrepare={prepare} onError={setError} approvals={approvals} />}
          {tab === 'profile' && <Profile version={version} onError={setError} />}
          {tab === 'privacy' && <Privacy health={health} />}
        </section>
        {tab === 'ask' && (
          <aside>
            <AgentTeam trace={trace} transport={health?.transport} />
            <TraceFeed trace={trace} />
          </aside>
        )}
      </main>
    </div>
  )
}

function MyEvents({ version, onPrepare, onError }) {
  const [data, setData] = useState(null)
  useEffect(() => { api.events().then(setData).catch((e) => onError(e.message)) }, [version])
  if (!data) return <p className="muted">Scout is reading your platforms…</p>

  const byId = Object.fromEntries(data.events.map((e) => [e.id, e]))
  const days = {}
  for (const e of [...data.events].sort((a, b) => a.start.localeCompare(b.start))) (days[fmtDay(e.start)] ||= []).push(e)
  const merged = data.events.filter((e) => e.platforms.length > 1).length

  return (
    <>
      <h2>My events</h2>
      <p className="muted">
        {data.events.length} upcoming from Luma, Meetup and Eventbrite
        {merged > 0 && ` · ${merged} listed on several platforms, shown once`}
      </p>
      {data.clashes.length > 0 && (
        <div className="clashes">
          <h3>{data.clashes.length} clash{data.clashes.length > 1 && 'es'} need a decision</h3>
          {data.clashes.map((c) => {
            const drop = byId[c.keep_id === c.a_id ? c.b_id : c.a_id]
            return (
              <div key={c.a_id + c.b_id} className={`card clash ${c.kind}`}>
                <div className="row between">
                  <b>{byId[c.a_id].title} ↔ {byId[c.b_id].title}</b>
                  <span className="chip">{c.kind === 'travel' ? `${c.gap_min} min gap, ~${c.travel_min} min trip` : 'overlap'}</span>
                </div>
                <div>{c.reason}</div>
                <button onClick={() => onPrepare(drop.id, 'cancel')}>Cancel {drop.title}</button>
              </div>
            )
          })}
        </div>
      )}
      {Object.entries(days).map(([day, evs]) => (
        <div key={day}>
          <h3 className="day">{day}</h3>
          {evs.map((e) => (
            <EventCard key={e.id} e={e}>
              <button onClick={() => onPrepare(e.id, 'cancel')}>Cancel</button>
            </EventCard>
          ))}
        </div>
      ))}
    </>
  )
}

function Ask({ onPrepare, onError, approvals }) {
  const [prompt, setPrompt] = useState('')
  const [turns, setTurns] = useState([]) // [{prompt, results|null}]
  const [busy, setBusy] = useState(false)
  const asked = new Set(approvals.map((x) => x.action.event_id))

  const send = async (p) => {
    if (!p.trim() || busy) return
    setBusy(true)
    setPrompt('')
    setTurns((t) => [...t, { prompt: p, results: null }])
    try {
      const { results } = await api.ask(p)
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, results } : x)))
    } catch (e) {
      onError(e.message)
      setTurns((t) => t.slice(0, -1))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <h2>Ask the agent</h2>
      <div className="chips">
        {DEMO_PROMPTS.map((p) => <button key={p} className="chipbtn" onClick={() => send(p)} disabled={busy}>{p}</button>)}
      </div>
      {turns.map((t, i) => (
        <div key={i} className="turn">
          <div className="bubble me">{t.prompt}</div>
          {!t.results ? (
            <div className="bubble agent muted">Memory, Scout and Ranker are on it…</div>
          ) : (
            <div className="bubble agent">
              {t.results.slice(0, 6).map((r) => {
                const pageUrl = r.event.urls?.[r.event.platforms?.[0]] || Object.values(r.event.urls || {}).find(Boolean)
                return (
                <div key={r.event.id}>
                  <EventCard e={r.event}>
                    <div className="score"><b>{r.score}</b><div className="bar"><i style={{ width: `${r.score}%` }} /></div></div>
                    <button className="primary" disabled={asked.has(r.event.id)} onClick={() => onPrepare(r.event.id, 'rsvp')}>
                      {asked.has(r.event.id) ? 'Prepared' : 'RSVP'}
                    </button>
                    {pageUrl && <a href={pageUrl} target="_blank" rel="noreferrer" className="small">Event page ↗</a>}
                  </EventCard>
                  <div className="reason">{r.reason}</div>
                </div>
                )
              })}
            </div>
          )}
        </div>
      ))}
      <form className="askbar" onSubmit={(e) => { e.preventDefault(); send(prompt) }}>
        <input value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="e.g. agent hackathons next week" />
        <button className="primary" disabled={busy}>Ask</button>
      </form>
    </>
  )
}

const clean = (s) => s.replace(/\*\*/g, '').replace(/^\s*\*\s*/gm, '').split('\n').filter(Boolean)

function Profile({ version, onError }) {
  const [p, setP] = useState(null)
  const [note, setNote] = useState('')
  useEffect(() => { api.profile().then(setP).catch((e) => onError(e.message)) }, [version])
  if (!p) return <p className="muted">Loading what Memory learned…</p>

  const add = async (e) => {
    e.preventDefault()
    if (!note.trim()) return
    try { setP(await api.addNote(note)); setNote('') } catch (err) { onError(err.message) }
  }
  const topics = Object.entries(p.topics).sort((a, b) => b[1] - a[1])
  const max = Math.max(...topics.map(([, w]) => Math.abs(w)), 1)

  return (
    <>
      <h2>Your profile</h2>
      <div className="card">
        <h3>Tell the agent about yourself</h3>
        <form className="askbar" onSubmit={add}>
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. I prefer evening events, no early mornings" />
          <button className="primary">Save</button>
        </form>
        {p.notes.map((n) => <div key={n} className="note">“{n}”</div>)}
      </div>
      <div className="card">
        <h3>What I learned about you</h3>
        <p className="muted small">From your event history on Luma, Meetup and Eventbrite (went, RSVP'd, waitlisted, skipped).</p>
        {p.insights.length > 0 && (
          <div className="insight">
            <span className="partner on">cognee</span>
            {p.insights.flatMap(clean).map((l) => <div key={l}>{l}</div>)}
          </div>
        )}
        {p.facts.map((f) => <div key={f}>• {f}</div>)}
      </div>
      <div className="card">
        <h3>Topics</h3>
        {topics.map(([t, w]) => (
          <div key={t} className="topic">
            <span>{t}</span>
            <div className="tbar"><i className={w < 0 ? 'neg' : ''} style={{ width: `${(Math.abs(w) / max) * 100}%` }} /></div>
            <span className="mono small">{w > 0 ? '+' : ''}{w.toFixed(1)}</span>
          </div>
        ))}
      </div>
    </>
  )
}

function Privacy({ health }) {
  return (
    <>
      <h2>Privacy</h2>
      <div className="card">
        <ul className="promises">
          <li><b>Runs on your machine.</b> The orchestrator and all five agents run locally.</li>
          <li><b>No stored passwords.</b> The agent never asks for or keeps platform passwords.</li>
          <li><b>Nothing is submitted without you.</b> Every RSVP or cancellation waits for your Approve click, with a one-time token
            {health && ` that expires after ${Math.round(health.approval_ttl_s / 60)} minutes`}.</li>
          <li><b>Your memory stays local.</b> cognee's memory graph lives in <code>{health?.memory_dir || '~/.event-agent/cognee'}</code>, outside any synced folder.</li>
          <li><b>Model calls go to Superlinked only</b>, and only in live or record mode. The demo replays recorded answers offline.</li>
        </ul>
      </div>
      {health && (
        <div className="card">
          <h3>Right now</h3>
          <table className="fields"><tbody>
            <tr><td>Data mode</td><td>{health.mode}</td></tr>
            <tr><td>Agent messages</td><td>{health.transport}</td></tr>
            <tr><td>Form submission</td><td>{health.submit}</td></tr>
            <tr><td>Agents</td><td>{health.agents.join(', ')}</td></tr>
          </tbody></table>
        </div>
      )}
    </>
  )
}
