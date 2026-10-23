import { useEffect, useState } from 'react'
import { api, type RemoteEdits, type Run, type Summary } from '../api'
import { fmtWhen } from '../App'

export default function RunsView({ refreshKey, summary }: { refreshKey: number; summary: Summary | null }) {
  const [runs, setRuns] = useState<Run[]>([])
  const [phone, setPhone] = useState<RemoteEdits | null>(null)
  useEffect(() => {
    api.runs().then(setRuns)
    api.remoteEdits().then(setPhone).catch(() => setPhone(null))
  }, [refreshKey])
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
