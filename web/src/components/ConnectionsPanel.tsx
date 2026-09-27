import type { ConnectionsSummary } from "../lib/api";

/**
 * Structured view of `connections.toml`. Every value is rendered via
 * textContent-equivalent JSX (string children of host elements), so the
 * XSS posture is identical to the legacy app.js renderConfig().
 *
 * Passwords are never rendered — `password_set` is a boolean only; the
 * backend's `_connections_summary()` masks the URL and never returns the
 * raw secret.
 */
export default function ConnectionsPanel({ summary }: { summary: ConnectionsSummary }) {
  return (
    <section className="conn">
      <h2>连接池默认</h2>
      <dl>
        <dt>size</dt>
        <dd>{summary.pool_defaults.size}</dd>
        <dt>recycle</dt>
        <dd>{summary.pool_defaults.recycle}</dd>
        <dt>pre_ping</dt>
        <dd>{String(summary.pool_defaults.pre_ping)}</dd>
        <dt>echo</dt>
        <dd>{String(summary.pool_defaults.echo)}</dd>
      </dl>

      <h2>
        连接 <small>({summary.connections.length})</small>
      </h2>
      <ul className="cards">
        {summary.connections.map((c) => (
          <li key={c.name} className="card">
            <header className="card-head">
              <span className="name">{c.name}</span>
              <span className="driver">{c.driver}</span>
              {c.name === summary.default_workspace ? (
                <span className="pill def">默认</span>
              ) : null}
              <span className="spacer" />
              <span className={`pill ${c.read_only ? "ro" : "rw"}`}>
                {c.read_only ? "只读" : "可写"}
              </span>
            </header>
            <dl className="card-body">
              <dt>url</dt>
              <dd>{c.url}</dd>
              {c.description ? (
                <>
                  <dt>description</dt>
                  <dd>{c.description}</dd>
                </>
              ) : null}
              <dt>application_name</dt>
              <dd>{c.application_name}</dd>
              <dt>password</dt>
              <dd>{c.password_set ? "已设置(在 URL 或 password 字段)" : "未设置"}</dd>
              <dt>pool</dt>
              <dd>
                size={c.pool.size} recycle={c.pool.recycle} pre_ping=
                {String(c.pool.pre_ping)} echo={String(c.pool.echo)}
              </dd>
            </dl>
          </li>
        ))}
      </ul>

      <p className="raw-note">
        密码已打码显示。点「编辑」可直接修改原文 TOML,保存前后端会做一次解析校验。
      </p>
    </section>
  );
}