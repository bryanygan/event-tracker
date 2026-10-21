import { useEffect, useState } from 'react'
import { api, type Run } from '../api'
import { fmtWhen } from '../App'

export default function RunsView({ refreshKey }: { refreshKey: number }) {
  const [runs, setRuns] = useState<Run[]>([])
  useEffect(() => { api.runs().then(setRuns) }, [refreshKey])
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
    </div>
  )
}
