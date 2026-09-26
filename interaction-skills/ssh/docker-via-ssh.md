# Docker-via-SSH — deploy a docker-compose service to a REMOTE host

> **The root logic for all docker operations in sql-harness.** You bring the
> `docker-compose.yml` (and config files) locally; `sql-harness ssh` uploads
> them to the remote host and runs `docker compose`. Everything runs **on the
> remote**, not locally. Mirrors how you'd deploy by hand over SSH, but
> scripted + repeatable.

## Detection

- You have an SSH workspace (`driver = "ssh"`) + a docker-compose template
- You want a service (mysql / redis / coturn / nginx / ...) running on the
  **remote** host, with its data + config cohesive in one dir
- You want it re-deployable / upgradable without re-typing

If you only have a DB workspace (no SSH), you can't deploy — first `sql-harness
add --name <host> --driver ssh --url ssh://...`.

## The canonical layout (REMOTE host)

Every service lives under `~/sql-harness/<service>/` **on the remote**, fully
self-contained (compose + config + data + logs all relative to that dir):

```
~/sql-harness/mysql/                ← on the remote host
├── docker-compose.yml             ← uploaded from local docker-services/mysql/
├── conf/                          ← uploaded (or templated at deploy time)
├── data/                          ← created on first `docker compose up`
└── logs/                          ← (optional)
```

**Why relative mounts + one-dir-per-service**: backup is `tar czf mysql.tgz
~/sql-harness/mysql/`; teardown is `rm -rf ~/sql-harness/mysql/` after
`docker compose down -v`; upgrade is re-upload + `up -d`. Cohesion = portability.

## The 4-step deploy loop

### Step 1 — write the compose locally

This mechanic assumes you have a local `docker-compose.yml` + any config files
you want to ship. The shipped template dir is gone in this build — bring your
own. All mounts must be **relative** to the service dir (`./data`, `./config`
— NOT `./<service>/data`, which double-nests).

### Step 2 — upload to the remote

```bash
# Create the remote service dir + upload every file in the template
sql-harness ssh -c 2026aliyun exec 'mkdir -p ~/sql-harness/mysql'
sql-harness ssh -c 2026aliyun upload docker-services/mysql/docker-compose.yml  ~/sql-harness/mysql/docker-compose.yml
```

For a whole dir (many config files), loop the uploads, or `tar`-pipe-via-ssh:
```bash
tar czf - -C docker-services/mysql . | sql-harness ssh -c 2026aliyun exec 'mkdir -p ~/sql-harness/mysql && tar xzf - -C ~/sql-harness/mysql'
```

### Step 3 — start it on the remote

```bash
sql-harness ssh -c 2026aliyun exec 'cd ~/sql-harness/mysql && docker compose up -d'
```

`docker compose` reads the compose file in `~/sql-harness/mysql/`; relative
mounts resolve there → `./data` = `~/sql-harness/mysql/data`. Cohesion holds.

### Step 4 — verify (read-only, on the remote)

```bash
sql-harness ssh -c 2026aliyun exec 'cd ~/sql-harness/mysql && docker compose ps'
sql-harness ssh -c 2026aliyun exec 'docker logs --tail 20 mysql'
sql-harness ssh -c 2026aliyun exec 'docker exec mysql mysqladmin ping -h localhost'   # mysql healthcheck
```

## Lifecycle (all via `sql-harness ssh exec`)

| Op | Command |
|---|---|
| status | `cd ~/sql-harness/<svc> && docker compose ps` |
| logs (follow) | `docker logs -f <container>` |
| restart | `cd ~/sql-harness/<svc> && docker compose restart` |
| upgrade image | edit compose (new tag) → re-upload → `docker compose up -d` |
| stop | `cd ~/sql-harness/<svc> && docker compose stop` |
| teardown (keep data) | `cd ~/sql-harness/<svc> && docker compose down` |
| teardown (wipe data) | `docker compose down -v && rm -rf ~/sql-harness/<svc>` ⚠️ |
| backup | `tar czf <svc>-$(date +%F).tgz ~/sql-harness/<svc>/` |

## Approach — one-liner deploy (after template exists)

```bash
SVC=mysql; HOST=2026aliyun
sql-harness ssh -c $HOST exec "mkdir -p ~/sql-harness/$SVC"
for f in docker-compose.yml; do
  sql-harness ssh -c $HOST upload docker-services/$SVC/$f ~/sql-harness/$SVC/$f
done
sql-harness ssh -c $HOST exec "cd ~/sql-harness/$SVC && docker compose up -d"
sql-harness ssh -c $HOST exec "cd ~/sql-harness/$SVC && docker compose ps"
```

Save this as a zone script (`sql-harness save deploy_mysql -c 2026aliyun`) —
then `sql-harness run deploy_mysql -c 2026aliyun` redeploys anytime. This is
the save→run cycle applied to infrastructure.

## Gotchas

- **`docker compose` vs `docker-compose`**: v2 (`docker compose`, space) ships
  with Docker Engine 20.10+. v1 (`docker-compose`, hyphen) is deprecated. Use v2.
- **Relative mounts are relative to the compose file's dir** — run `docker
  compose` from `~/sql-harness/<svc>/` (or with `-f ~/sql-harness/<svc>/docker-compose.yml`).
  Running from `~/` with the wrong cwd breaks mounts (this was the bug in the
  upstream `dr` repo's nginx.conf — double-nested `./nginx/nginx.conf`).
- **Config-as-dir gotcha**: if `./<config-file>` doesn't exist locally when you
  mount it, Docker creates a **directory** at that path (not a file) → the
  service fails to read config. Always upload the file BEFORE `up -d`, or
  pre-`touch` it.
- **Credentials in compose**: don't commit real secrets. The shipped templates
  use placeholders; substitute at deploy time or via `${env:VAR}` indirection
  (sql-harness expands these before upload — actually no, compose expands them
  on the remote; set them in a `.env` next to the compose, also uploaded).
- **ssh exec + docker compose need a login shell for PATH**: if `docker compose`
  is "command not found" over ssh, prefix with the full path or source the
  profile: `ssh exec 'source ~/.bashrc && docker compose ...'`.
- **Windows line endings in heredocs**: `\r\n` inside compose `.yml` causes
  yaml parse errors on the remote. Always strip CRLF before upload
  (`sed -i 's/\r$//' file.yml` or set `git config core.autocrlf false`).

## Service-specific gotchas — coturn

coturn (TURN/STUN relay) has war stories that bit production. All live here in the mechanic — not duplicated in the template dir.

**Key config knobs** (`turnserver.conf`):
- `realm` — your domain / public hostname (clients see this in their URL).
- `external-ip` — server's PUBLIC ip when behind NAT (Aliyun ECS is). Set as `AUTO` in shipped template; deploy script substitutes real public ip before `up -d`.
- `listening-ip=0.0.0.0` — bind interface (private is fine; only `external-ip` needs to be public).
- `listening-port=3478` (TURN/STUN), `tls-listening-port=5349` (TURNS over TLS).
- `min-port=49152` / `max-port=65535` — relay UDP range. Must match firewall.
- `user=<u>:<p>` + `lt-cred-mech` — long-term credential mechanism.

Test creds after deploy:
```bash
turnutils_uclient -u webrtc -w webrtc <server-ip> 3478
```

**Gotchas**:

- **`external-ip` behind NAT**: Aliyun ECS is behind NAT — coturn's `turnserver.conf` `external-ip=` must be the **public** ip, not the private one `listening-ip=0.0.0.0` binds. Templated as `AUTO`; substitute at deploy time (`sed -i "s/external-ip=AUTO/external-ip=$PUBIP/"`).

- **Firewall / security group**: docker publishes ports on the host, but Aliyun security group + host firewall (ufw/firewalld) must ALSO allow them. The coturn relay UDP range (49152-65535) is the easy one to forget. Open in security group, NOT via docker `ports:`.

- **Don't publish the relay port range via `ports:`** — publishing coturn's relay range `49152-65535` (~16k ports) makes Docker create ~16k iptables DNAT rules, which **OOM-kills the container start** (exit 137) on small hosts (Aliyun ECS). Use `network_mode: host` instead — coturn binds the range natively, zero iptables overhead. This is the official coturn/docker recommendation.

- **`network_mode: host` disables `ports:`** — the container binds host ports directly; don't also list `ports:` (compose rejects the combo). All port management moves to the host firewall + cloud security group.

- **Config-as-dir gotcha**: if `./turnserver.conf` doesn't exist locally when docker tries to mount it, Docker creates a **directory** at that path (not a file) → coturn fails to read config. Always upload the file BEFORE `up -d`, or pre-`touch` it on the remote.

**Why host networking (rationale)**: coturn's relay UDP range (49152-65535 = ~16k ports) must NOT be published via docker `-p` — Docker would create ~16k iptables DNAT rules, OOM-killing the daemon on small hosts. Host networking lets coturn bind the range natively, zero iptables overhead. Ports to open in host firewall / cloud security group: `3478/tcp+udp`, `5349/tcp+udp`, `49152-65535/udp` (relay range — open in security group, NOT in docker).

## See also

- This file's "Detection" / "Approach" sections (decision P: the legacy strategy stub in `agent-workspace/zones/meta/skills/docker-deploy.md` was collapsed into the mechanic; no separate doc).
- `interaction-skills/ssh/ssh.md` — the raw ssh_exec/upload/download helpers
- `interaction-skills/ssh/auth-and-tunnels.md` — SSH auth scheme 选型 + 端口转发