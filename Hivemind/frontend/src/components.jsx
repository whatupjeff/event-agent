import { useEffect, useState } from 'react'

const fmtDay = (iso) => new Date(iso).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
const fmtTime = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
export { fmtDay }

export function Platforms({ e }) {
  return (
    <span className="platforms">
      {e.platforms.map((p) => (
        <a key={p} className={`badge p-${p}`} href={e.urls[p]} target="_blank" rel="noreferrer">{p}</a>
      ))}
    </span>
  )
}

export function EventCard({ e, children }) {
  return (
    <div className="card event">
      <div className="when">
        <b>{fmtDay(e.start)}</b>
        <span>{fmtTime(e.start)}–{fmtTime(e.end)}</span>
      </div>
      <div className="what">
        <div className="title">
          {e.title} {e.status !== 'none' && <span className={`chip s-${e.status}`}>{e.status}</span>}
        </div>
        <div className="muted">{e.venue} · {e.city}</div>
        <div className="row"><Platforms e={e} />{e.tags.map((t) => <span key={t} className="tag">{t}</span>)}</div>
      </div>
      {children && <div className="side">{children}</div>}
    </div>
  )
}

export function ApprovalCard({ item, onApprove, onReject }) {
  const { action: a } = item
  const [showVm, setShowVm] = useState(false)
  const done = a.status !== 'pending'
  const vm = a.browser
  return (
    <div className={`card approval ${a.status}`}>
      <div className="row between">
        <b>{a.kind === 'rsvp' ? 'RSVP to' : 'Cancel'} {a.event_title}</b>
        <div className="row">
          {vm && <span className={`chip vm-${vm.status}`}>VM: {vm.sandbox_id.slice(0, 8)} · {vm.status}</span>}
          <span className={`badge p-${a.platform}`}>{a.platform}</span>
        </div>
      </div>
      <p className="muted small">
        {vm
          ? 'Registrar deployed a browser VM, filled this form, and paused. Inspect the live VM or approve below.'
          : 'Registrar filled this in and paused. Nothing is sent until you approve.'}
      </p>
      <table className="fields">
        <tbody>{Object.entries(a.fields).map(([k, v]) => <tr key={k}><td>{k}</td><td>{v}</td></tr>)}</tbody>
      </table>
      {vm?.viewer_url && !done && (
        <div className="vm-box">
          <div className="row">
            <a className="btn-link" href={vm.viewer_url} target="_blank" rel="noreferrer">Open Browser VM ↗</a>
            <button type="button" onClick={() => setShowVm((s) => !s)}>
              {showVm ? 'Hide Live Preview' : 'Show Live Preview'}
            </button>
          </div>
          {showVm && (
            <iframe
              className="vm-frame"
              src={vm.viewer_url}
              title={`Browser VM ${vm.sandbox_id}`}
              allow="clipboard-read; clipboard-write"
            />
          )}
        </div>
      )}
      {a.warnings.map((w) => <div key={w} className="warn">⚠ {w}</div>)}
      {done ? (
        <div className={`result ${a.status}`}>
          {a.status === 'submitted' ? `Done (${a.result?.mode}) · confirmation ${a.result?.confirmation}` : a.status}
          {a.result?.error && ` · ${a.result.error}`}
        </div>
      ) : (
        <div className="row">
          <button className="primary" onClick={() => onApprove(item)}>Approve</button>
          <button onClick={() => onReject(item)}>Reject</button>
        </div>
      )}
    </div>
  )
}

const TEAM = [
  { id: 'scout', label: 'Scout', job: 'reads Luma, Meetup, Eventbrite · merges duplicates' },
  { id: 'memory', label: 'Memory', job: 'learns your taste', partner: 'cognee' },
  { id: 'ranker', label: 'Ranker', job: 'scores events 0–100', partner: 'superlinked' },
  { id: 'conflicts', label: 'Conflicts', job: 'overlaps & travel time (rules, no LLM)' },
  { id: 'registrar', label: 'Registrar', job: 'fills RSVP forms, waits for you' },
]

export function AgentTeam({ trace, transport }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(t)
  }, [])
  const last = {}
  for (const ev of trace) for (const who of [ev.frm, ev.to]) last[who] = ev
  const active = (id) => last[id] && now - last[id].ts * 1000 < 2500
  return (
    <div className="team">
      <div className="row between">
        <h3>Agent team</h3>
        <span className="muted small">messages via {transport || '…'}</span>
      </div>
      {TEAM.map((a) => (
        <div key={a.id} className={`agent ${active(a.id) || (a.partner && active(a.partner)) ? 'on' : ''}`}>
          <div className="row between">
            <b><span className="dot" />{a.label}</b>
            {a.partner && <span className={`partner ${active(a.partner) ? 'on' : ''}`}>{a.partner}</span>}
          </div>
          <div className="muted small">{last[a.id] ? last[a.id].summary : a.job}</div>
        </div>
      ))}
    </div>
  )
}

export function TraceFeed({ trace }) {
  const shown = trace.filter((t) => !t.kind.endsWith('.reply')).slice(-40).reverse()
  return (
    <div className="trace">
      <h3>Live trace</h3>
      {shown.length === 0 && <div className="muted small">Hand-offs between agents show up here.</div>}
      {shown.map((t) => (
        <div key={t.id} className={`tline k-${t.kind.split('.')[0]}`}>
          <span className="mono">{new Date(t.ts * 1000).toLocaleTimeString()}</span>
          <span><b>{t.frm}</b> → <b>{t.to}</b> {t.summary.replace(`${t.frm} → ${t.to}: `, '')}</span>
        </div>
      ))}
    </div>
  )
}
