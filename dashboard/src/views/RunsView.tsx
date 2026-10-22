import { useEffect, useState } from 'react'
import { api, type RemoteEdits, type Run } from '../api'
import { fmtWhen } from '../App'

export default function RunsView({ refreshKey }: { refreshKey: number }) {
  const [runs, setRuns] = useState<Run[]>([])
  const [phone, setPhone] = useState<RemoteEdits | null>(null)
  useEffect(() => {
    api.runs().then(setRuns)
    api.remoteEdits().then(setPhone).catch(() => setPhone(null))
  }, [refreshKey])
  return (
    <div className="table-wrap">
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
