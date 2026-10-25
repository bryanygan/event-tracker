import { useEffect, useState } from 'react'
import { api, type RemoteEdits, type Run, type SourceCheck, type Summary } from '../api'
import { fmtWhen } from '../App'

export default function RunsView({ refreshKey, summary }: { refreshKey: number; summary: Summary | null }) {
  const [runs, setRuns] = useState<Run[]>([])
  const [phone, setPhone] = useState<RemoteEdits | null>(null)
  const [checks, setChecks] = useState<SourceCheck[]>([])
  useEffect(() => {
    api.runs().then(setRuns)
    api.health().then(setChecks).catch(() => setChecks([]))
    api.remoteEdits().then(setPhone).catch(() => setPhone(null))
  }, [refreshKey])
  const broken = checks.filter((c) => c.problem).length
  const d = summary?.drafting
  return (
    <div className="table-wrap">
      {d && (
        <p className="muted small">
          {d.enabled
            ? <>Drafts by {d.model}: {d.today} of {d.cap} calls today · {d.month_calls} this month, ${d.month_cost_usd.toFixed(d.month_cost_usd < 1 ? 3 : 2)}.
                Over the cap or on an API error, Ollama writes them.</>
            : <>Drafts by Ollama ({summary?.model}). Add ANTHROPIC_API_KEY to .env to use Claude.</>}
        </p>
      )}
      <table className="events">
        <thead><tr><th>#</th><th>Started</th><th>Took</th><th>Events</th><th>API calls</th><th>Result</th></tr></thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td>{fmtWhen(r.started_at)}</td>
              <td>{r.finished_at ? `${Math.round((+new Date(r.finished_at) - +new Date(r.started_at)) / 1000)}s` : '…'}</td>
              <td>{r.events_fetched}</td>
              <td>{r.api_calls}</td>
              <td>{r.finished_at ? (r.ok ? 'ok' : <span className="error-text">{r.error}</span>) : 'running'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Full logs: <code>logs\pipeline.log</code></p>

      {checks.length > 0 && (
        <>
          <h3>Sources</h3>
          <p className="muted small">
            {broken ? `${broken} of ${checks.length} need a look.` : `All ${checks.length} working.`} From what the
            runs recorded; nothing extra is fetched.
          </p>
          <table className="events">
            <thead><tr><th>Source</th><th>Last good read</th><th>Found</th><th>Status</th></tr></thead>
            <tbody>
              {checks.map((c) => (
                <tr key={`${c.group}:${c.name}`}>
                  <td><span className="muted small">{c.group} · </span>{c.name}</td>
                  <td>{c.last_ok ? fmtWhen(c.last_ok) : 'never'}</td>
                  <td>{c.found == null ? '' : `${c.found} ${c.what}`}</td>
                  <td>{c.problem ? <span className="error-text">{c.problem}</span> : 'ok'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {phone?.enabled && (
        <>
          <h3>Phone edits</h3>
          <p className="muted small">Changes made through {phone.host} (Cloudflare Access login), newest first.</p>
          {phone.edits.length === 0 ? <p className="muted small">None yet.</p> : (
            <table className="events">
              <thead><tr><th>When</th><th>Who</th><th>Change</th><th>Result</th></tr></thead>
              <tbody>
                {phone.edits.map((e) => (
                  <tr key={e.id}>
                    <td>{fmtWhen(e.at)}</td>
                    <td>{e.email}</td>
                    <td><code>{e.method} {e.path}</code></td>
                    <td>{e.status < 400 ? 'ok' : <span className="error-text">{e.status}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}
