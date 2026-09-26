# SSH — auth 选型与端口转发

`ssh.md` 写了怎么用 helpers（`ssh_exec / upload / download`）和 CLI。本篇覆盖两块它没写的：(1) 选哪种 auth（key / password / ssh-agent / `ssh+key` / env fallback），(2) 用 SSH workspace 做端口转发把"躲在堡垒机后面的 DB"暴露成本地端口。

> 全文代码块已注入的 helper：`ssh_exec / ssh_upload / ssh_download / use_workspace / ssh_info`。

## Detection

- `ssh_exec` 弹 `RuntimeError("...no tables...")` 或 paramiko `Auth failed` / `No valid authenticators` → 跳 [§1](#1-auth-选型)
- PG/MySQL workspace 直连 `internal-db-1.corp:5432` 超时，但 SSH workspace 能登堡垒机 → 跳 [§2](#2-端口转发tunneling)
- 远程主机有多个 SSH key 想用 ssh-agent 托管 → §1.3

---

## 1. Auth 选型

### 1.1 三种 scheme 对照

`drivers/ssh.py` 认这三种 URL 形式：

| URL 形式 | 何时用 | 认证方式 |
|---|---|---|
| `ssh://user@host:port` | 默认 | 显式 key（`?key=`/`$BH_SSH_KEY`）→ key；否则配了密码 → 纯密码；都没有 → 自动找 `~/.ssh` key |
| `ssh+key://user@host:port?key=/abs/path` | key 不在默认路径 | key 认证，密码作后备 |
| `ssh+password://user[:pw]@host:port` | 跳板 / 临时主机 | 纯密码认证；密码可嵌 URL，也可放字段/env |

**密码来源优先级**（高 → 低）：URL 内嵌 > 连接里的 `password` 字段 > `$BH_SSH_PASSWORD`。

**`password` 字段**（推荐——密码按连接存，切 workspace 不用动环境变量）：

```toml
[[connections]]
name = "iot_server"
driver = "ssh"
url = "ssh://113.44.193.72:22"
password = "${env:IOT_SSH_PASSWORD}"   # 也可直接写明文
```

**认证策略**（`_open_client`）：显式给 key（`?key=`/`$BH_SSH_KEY`）→ key 优先、密码兜底；没给 key 但配了密码 → **纯密码**，不翻 `~/.ssh`；都没有 → 自动发现 `~/.ssh/id_{ed25519,rsa,ecdsa}`。

> 第三条很关键：paramiko 开着 `look_for_keys` 会逐个试 `~/.ssh` 里的 key，只要里面有一把解析不了的（旧 DSA key 是常见雷），认证线程直接抛异常、**在密码之前**就中断握手。所以配了密码就不会被本地散装 key 劫持。

### 1.2 用哪个连接串

```
有 ed25519 key 在 ~/.ssh/ 且 authorized_keys 已加？
  ├─ 是 → ssh://user@host                                (默认 key path)
  └─ 否：
      有密码？ → 连接里加 password = "..."（driver 用 ssh 即可）
      key 在非默认路径？ → ssh://user@host?key=/path/to/k
      key 在 ssh-agent？ → §1.3
```

### 1.3 ssh-agent

paramiko 默认不会主动连 ssh-agent。两条路：

```python
# 路径 A（最常用）：导出私钥文件路径，给驱动
# connections.toml:
#   url = "ssh://app@bastion.corp?key=/home/me/.ssh/bastion_ed25519"
# 这是 ssh.md 默认推荐的。

# 路径 B（agent 转发）：用 ssh-agent 把 key 放进 socket，paramiko 通过
# SSHAgentKey 适配。当前 ssh-harness 没适配；如需要 agent 转发，得
# 自己 patch driver。阶段性建议 → 用路径 A。
```

### 1.4 key 不被接受（`Auth failed` / `No valid authenticators`）

8 成是这几个原因，按频率排：

1. **key 文件权限太开放**（paramiko 严格遵循 OpenSSH 规则）
   ```
   chmod 600 ~/.ssh/id_ed25519
   ```
2. **authorized_keys 里这 key 被加过 passphrase**——paramiko 不弹 UI。
   解决：`ssh-keygen -p -f ~/.ssh/id_ed25519` 清掉 passphrase，或 `ssh-add ~/.ssh/id_ed25519`。
3. **`?key=` 路径拼错**：相对路径相对 pwd——绝对路径最稳。
4. **用户名错了**：`ssh://root@host` vs `ssh://ubuntu@host`，老 OpenSSH 默认禁 root。

排查：

```python
use_workspace("bastion")
print(ssh_info())          # 看 user/host/port/key_path 解析对不对
ssh_exec("whoami")         # 验证通了
```

### 1.5 env fallback 默认 key 优先级

看 `drivers/ssh.py:KEY_FALLBACK = ("~/.ssh/id_ed25519", "~/.ssh/id_rsa", "~/.ssh/id_ecdsa")`：

```python
r = ssh_exec("ls -l ~/.ssh/id_ed25519 ~/.ssh/id_rsa 2>&1")
print(r["stdout"])
# 第一个存在的就是 driver 选的
```

---

## 2. 端口转发（Tunneling）

业务 DB 不对公网开、只对堡垒机的内网暴露时。三条路：

### 2.1 远程 `ssh -L`（堡垒机侧启端口）

```python
use_workspace("bastion")
ssh_exec(
    "ssh -fN -L 0.0.0.0:15432:internal-db.corp:5432 jump@internal-db.corp",
    timeout=5,
)
r = ssh_exec("ss -lntp | grep 15432", timeout=5)
assert "15432" in r["stdout"]
```

> 局限：转发启在堡垒机上，**你本机** psql 连不到堡垒机的 `15432`（除非堡垒机做了 reverse tunnel）。适合堡垒机里跑 docker-compose；不适合"我本地 psql 直连"。

### 2.2 paramiko `open_channel` 反向隧道

让堡垒机把 internal-db 的 5432 反向暴露到堡垒机的 15432，然后用堡垒机侧 `psql` / `mysql` 客户端工具调试：

```python
use_workspace("bastion")
import paramiko
ws = current_workspace()
transport = ws.engine.client.get_transport()
reverse = transport.request_port_forward("0.0.0.0", 15432,
    ("internal-db.corp", 5432))
try:
    r = ssh_exec("PGPASSWORD=... psql -h 127.0.0.1 -p 15432 -U app -c 'select 1'")
finally:
    transport.cancel_port_forward("0.0.0.0", 15432)
```

### 2.3 真要让你本机 SQL 客户端连

sql-harness 不内置 SSH SOCKS 代理；本机 `psql`/`TablePlus` 直连躲在堡垒机后的 DB，最干净是用 OpenSSH 客户端 `ssh -L 15432:internal-db.corp:5432 bastion`（sql-harness 之外的工具）。

如果坚持在 sql-harness 里搞，用 §2.2 + 堡垒机启端口 + 跑 `psql`。

---

## Gotchas

- **paramiko 不会自动用 ssh-agent**——见 §1.3。
- **密码优先级**：URL 内嵌 > `password` 字段 > `$BH_SSH_PASSWORD`。配了密码即纯密码认证，本地 key 不再参与（见 §1.1）。
- **服务端禁密码登录**：报 `Authentication failed` 时先确认服务端开了 `PasswordAuthentication`，别急着改本地配置。
- **堡垒机转发时 `ssh -fN` 在某些堡垒机受限**——`AllowTcpForwarding no`。失败就 §2.2 走 paramiko `request_port_forward`。
- **`request_port_forward` 返回的对象必须在 finally 里 cancel**，否则下次 `use_workspace` 复用 transport 报端口占用。
- **workspace disconnect 不自动清 tunnel**——硬规矩：tunnel 用 try/finally 包起来。
- **Windows → Linux 堡垒机的 key 权限**：`scp` 把 600 丢没的话 OpenSSH 服务端会拒。`scp` 后 `chmod 600` 或用 `ssh-copy-id`（用 sftp，保留权限）。
- **堡垒机禁用 SFTP subsystem**：`ssh_upload/ssh_download` 报 `RuntimeError("SFTP subsystem not available")`。回退 `tar -czf - | ssh host tar -xzf -` 经 `ssh_exec` 流式传。

## See also

- `interaction-skills/ssh/ssh.md` — mechanic（helpers / CLI / 基础 gotchas）
- `interaction-skills/ssh/docker-via-ssh.md` — 通过 SSH workspace 把 docker-compose 部署到远程
- 根 `README.md` — `ssh://` / `ssh+password://` / `ssh+key://` 连接示例（Drivers 表）