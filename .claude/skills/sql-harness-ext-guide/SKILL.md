---
name: sql-harness-ext-guide
description: sql-harness 仓库的开发者指南与演化手册(本仓库 .claude/skills/ 专属,随仓库走)。当用户说"给 sql-harness 加功能/加 driver/加审计"、"sql-harness 的目录规范是什么"、"这个文件该放哪"、"更新 ext-guide"、"sql-harness 支持哪些审计/怎么扩展审计"、"改了 SKILL.md 要不要重新 build"时读取。覆盖:项目介绍与目录家规(A01)、五类扩展方法 SOP(A02)、当前审计能力与扩展点(A03)。
---

# sql-harness-ext-guide — sql-harness 开发者指南

> **本 skill 的定位**:sql-harness 仓库开发建设知识的**唯一归档地**(2026-09 精简决策:仓库内 `docs/` 与根 `AGENTS.md` 已删除,开发文档全部沉淀在本 skill 的 references/)。本 skill 位于 `sql-harness/.claude/skills/`,**随仓库走**,只在 sql-harness 工作目录的会话中可见。
>
> **前身**:`py-ee/.claude/skills/sql-harness-ext-guide/`(monorepo 时代,内容已按 A 序列拆分迁入本 skill;那份仅作历史对照,不再维护)。
>
> 原则:**主 SKILL.md 只做路由;任何场景的执行细节一律在 references/。** 先定位目录再动手——放错位置 = agent 找不到 = 等于没写。

## 项目精髓:文档管理与增长

> **本 skill 的存在理由**:sql-harness 的核心价值 = 文档质量。代码本身只是载体,**文档才是产品**。
>
> **反向警示**:文档一旦腐蚀,项目价值立即减弱 —— agent 找不到正确入口,用户照着过时文档做错,贡献者无法判断"这个文件该不该存在"。
>
> **本 skill 的四个治理责任**(任何时候都不可妥协):
>
> 1. **唯一归档地**:仓库里 90% 的开发知识必须能在这一个 skill 里找到(避免 docs/ + AGENTS.md + README + CLAUDE.md 多处散落)
> 2. **零孤儿**:每个 ref / 每个文件必须被引用、被路由、被读到(A01 §3 + B01 闸口)
> 3. **入口质量**:主 SKILL.md 的触发条件必须能命中用户的真实问题,不空转
> 4. **增长受控**:加 ref 必须有真实场景触发,不为拆而拆;ref 总数 > 5 时按 B 序列扩展

## 触发条件

| 用户说 | 动作 | 加载 ref |
| --- | --- | --- |
| "sql-harness 是什么" / "介绍下这个项目" / "目录规范" / "这个文件该放哪" | 读项目介绍 + 目录家规 | [[A01-项目介绍与目录规范]] |
| "给 sql-harness 加 driver / 加 helper / 加子命令 / 加 interaction-skill / 加 meta skill" | 按扩展类型走 SOP | [[A02-扩展方法]] |
| "sql-harness 支持哪些审计" / "analytics 怎么关" / "日志在哪" / "read_only 怎么实现的" | 读审计能力清单 | [[A03-审计功能]] |
| "加一种新的事件 / 给 analytics 挂自定义 listener" | 审计扩展点 | [[A03-审计功能]] §扩展 |
| "改了 SKILL.md 要不要重新 build" / "发布流程" / "wheel 里多了临时文件" | 构建/发布 SOP | [[A02-扩展方法]] §7 |
| "这个文件给谁用 / 用户能拿到吗" / "这个文件没人引用吧" / "清一下孤儿" | 动手前的必要性闸口 + 动手后的孤儿清零 | [[B01-用户视角审视与孤儿清零]] |
| "文档是不是太多了 / 哪个 ref 没人读 / 文档开始腐烂了 / 入口怎么没人触发" | 文档健康度巡检(空转 / 矛盾 / 路径过期 / 膨胀) | [[B01-用户视角审视与孤儿清零]] §7 |
| 功能做完后同步本 skill / 检查 skill 与代码一致 | 走 key_board 的 [[A03-内容同步]],目标 skill = 本 skill | key_board(外部) |

## 序列总览

ref 按主题分为三个代号;每个 ref 是独立主题的完整 SOP,进入前先看路由表。

| 代号 | 主题 | 文件 | 一句话 |
| --- | --- | --- | --- |
| **A01** | 项目介绍与目录规范 | references/A01-项目介绍与目录规范.md | 项目是什么 + 每个目录一种家规 + 文档归属速查 + 双轴判定 |
| **A02** | 扩展方法 | references/A02-扩展方法.md | 五类扩展的端到端 SOP(driver / interaction-skill / meta skill / helper / CLI 子命令)+ 构建发布 |
| **A03** | 审计功能 | references/A03-审计功能.md | analytics 事件 + NDJSON 日志 + spill + read_only 守卫,及各自的扩展点 |
| **B01** | 用户视角审视与孤儿清零 | references/B01-用户视角审视与孤儿清零.md | 动手前的必要性闸口(用户能否拿到/用到)+ 动手后的孤儿清零(全仓 0 引用则删) |

> 编号规则沿用 key_board 的 A 序列约定:本 skill 是项目专属 skill,不复用 key_board 的 A01/A02/A03(那三个是 skill 生命周期动作);代号在本 skill 内重新映射为主题。

## 路由优先级

1. **动手前先过 [[B01-用户视角审视与孤儿清零]]**(必要性闸口 + 孤儿清零),判定"该不该加/留"。
2. 信号是否落在 A01 / A02 / A03 主题内?→ 加载对应 ref 走 SOP。
3. 是 skill 结构问题(拆 ref / 合并 / 同步)而非 sql-harness 内容问题?→ 切换到 key_board skill。
4. 都不是 → 别硬套,直接回答。

> B01 是 A01-A03 的**上游闸口**:任何动作执行前先判必要性,执行后判是否产生孤儿。

## 主流程骨架(5 步)

1. **识别诉求**:用户要改什么?(动手前先过 [[B01-用户视角审视与孤儿清零]] 判必要性)
2. **加载 ref**:按路由表读对应 references(B01 闸口通过后再选 A01 / A02 / A03)。
3. **执行**:按 ref 内 SOP 操作;改了文档结构 → 同步本 skill 路由表;改了根 SKILL.md → 重 build。
4. **校验**:对照 ref 末尾检查清单逐项过;动手后回到 B01 复核孤儿。
5. **同步**:本 skill 内容与代码不一致时,走 key_board 的 [[A03-内容同步]] 增量同步。

## 易错和坑(高频 5 条)

| 错误 | 根因 | 预防 |
| --- | --- | --- |
| 往 `agent-workspace/zones/meta/skills/` 丢临时文件 | 整目录会被 force-include 打进 wheel 发布 | 测试产物即删;发布前 `unzip -l dist/*.whl` 检查 |
| 改了根 `SKILL.md` 没重新 `uv build` | `sql-harness skill` 输出旧内容,agent 注册到过期版本 | 改完即 build;注册副本用 `skill install` 刷新 |
| 把 mechanic 菜谱写进 README / docs/ | agent 不读 README 找卡点解法 | mechanic → `interaction-skills/`;开发知识 → 本 skill references/ |
| 跨 SQL 通论文档没排除 Redis/SSH | agent 在 Redis workspace 套 `with_transaction()` 撞 NotImplementedError | 通论开头写明"适用 postgres/mysql/sqlite" |
| 重建 `docs/` 或根 `AGENTS.md` | 推翻 2026-09 精简决策,文档散落两处 | 开发知识只进本 skill references/ |

## 成功标准检查清单

- [ ] 路由表与 references/ 文件一一对应(无孤儿 ref,无空 ref)
- [ ] 主 SKILL.md 只含原则与路由,无场景级细节
- [ ] 改动落在正确的目录(对照 [[A01-项目介绍与目录规范]] 速查表)
- [ ] 涉及 wheel 打包的改动已确认 force-include 影响
- [ ] 改了根 SKILL.md → 已 `uv build`;改了 meta skills → 已提醒重跑 `sql-harness init`
- [ ] **文档未腐蚀**(对照 [[B01-用户视角审视与孤儿清零]] §7):入口仍能命中真实诉求,ref 仍答得对问题,没有"半年没人读"的空 ref

## 何时**不**触发本 skill

- 用户想用 sql-harness 查库/跑 SQL/SSH(用户视角)→ 用 `sql-harness` skill 本体,不是本 skill
- 用户想管理 skill 结构(拆/合/同步)→ key_board skill
- 与 sql-harness 无关的项目 → 不适用

## Ref 索引

| ref | 何时读取 | 路径 |
| --- | --- | --- |
| [[A01-项目介绍与目录规范]] | 认识项目、判断文件归属、查目录家规时 | references/A01-项目介绍与目录规范.md |
| [[A02-扩展方法]] | 新增 driver / helper / 子命令 / interaction-skill / meta skill,或构建发布时 | references/A02-扩展方法.md |
| [[A03-审计功能]] | 查审计能力(analytics / 日志 / spill / read_only)或扩展审计时 | references/A03-审计功能.md |
| [[B01-用户视角审视与孤儿清零]] | 动手改 sql-harness 任意代码/文档前(判必要性);改动后(查孤儿) | references/B01-用户视角审视与孤儿清零.md |
