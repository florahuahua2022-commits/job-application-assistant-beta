# #4 / #5 完成后的暂停与同步说明

2026-10-25。执行基线：远端 main `dbce8ca`；独立工作分支：`codex/egress-read-design`。本次只完成 #4、#5，没有 push、分支 merge、部署或生产访问。测试证据见 `2026-10-25-egress-read-implementation.md`。

## 1. #1 的批准边界与新增验收要求

用户已批准同时间戳采用 `created_at DESC, id DESC`。该排序规则已是 #1 后续实施的确定要求，但**本次没有改 latest helper 或实现窗口查询**；#1 整体仍等待另行实施批准。

后续 #1 必须同时具备 SQLite 与 **GitHub Actions PostgreSQL 16 真实执行**，只做 SQL 方言 compile 不算通过：

- 在 CI 的临时 PostgreSQL 16 service 建立专用合成测试库，仅使用 CI 自建 fixture，禁止接入任何已有 Supabase/生产环境，不注入生产 secrets，不打印连接参数或凭据。
- 该 job 确认服务大版本为 16，真正建 fixture、执行窗口 SQL 并从真实 psycopg 游标读取结果；不是 mock，不得因 PostgreSQL 不可达而 skip 变绿。默认 SQLite job 保留。
- 与 SQLite 共同覆盖：每类 30/100 历史版本加大 trace；按用户/申请隔离；同时间戳最大 id 决胜；最新坏稿/过期稿不回退；SC optional/required、legacy fingerprint 契约；质量门禁、发布门禁、积分完结与重复完结。
- 断言实际返回文档行数 K、所选列与字节行为；窗口内部只取 id/type/rank 所需键，旧历史大列不能被外层全部读回。真实数据库可扫描元数据，不能把客户端 K 行声称为服务端只扫描 K 行。
- PostgreSQL job 需独立的无 dotenv/无生产凭据启动方式，只准访问本 job 的服务与测试库；不要直接复用 `run_offline.py` 去连接 PostgreSQL，因为当前运行器刻意拒绝非 SQLite。记录服务版本、计数与测试结果，不打印 URL、密码、token、参数或敏感异常上下文。
- 预计涉及 `.github/workflows/quality-gates.yml` 与 PostgreSQL 测试夹具/结果计数器；待 #1 实施时再写和验证。此刻 workflow 完全未改，也没有声称 Actions 已执行通过。

## 2. 支付分支未来如何同步 main

本地核对的支付分支为 `codex/payment-data-model` 的 `e2a2c378110c96a3c882de331cd7a7ae59b69aa2`，与本任务 main 基线的 merge base 为 `dbce8ca`。以下是未来操作说明，**没有现在执行**。

1. #4/#5 经用户批准进入 main 后，支付工作树先核对并保存自己的未提交内容，再从 GitHub fetch 最新 main；确认 main 已包含目标代码提交。不要把整个设计分支直接合并过去来代替同步 main。
2. 在支付分支合并已批准的 `origin/main`，保留支付提交历史；避免把共享提交任意 rebase 或同时 cherry-pick 又 merge 造成重复。若 main 是 squash 合并，以最终 diff/测试核对，不只按原提交 hash 判断。此步骤仍需用户届时批准，不在本次执行。
3. #1 以后进入 main 后再次按上述步骤同步。确认 latest/quality/release/credits 使用同一个受限查询，不保留支付分支旧的全历史查询作为第二条路径。
4. 在本地隔离库重跑新增读取测试、submission/release、generation requests、pack credits，以及支付分支自己的 credits lots/Stripe orders/webhook/幂等退款测试。需要 PostgreSQL 的部分使用临时 PostgreSQL 16，不使用支付分支环境文件或生产 secrets。不把本分支 627 项通过当成支付分支完整验收。

### 当前实际冲突核对

对当前工作树（含 #5）与支付分支的 `backend/app/main.py`，以 `dbce8ca` 为 base，用临时文件执行 `git merge-file -p` 三方文本检查，退出码 **0**。只在临时文本计算合并结果，没有 checkout、写支付工作树或合并分支，也没有打印合并正文。

| 文件 | 两侧情况 | 当前核对 / 后续风险 |
|---|---|---|
| `backend/app/main.py` | 支付分支加入支付入口等；本任务改 parse-ad、跨申请和 status 三处 | **当前 #4/#5 无文本冲突**，不是因为两边没有改同文件。#1 未写，未来仍须重新检查该文件及积分完结语义 |
| `backend/tests/test_egress_reads.py`、`egress_probe.py`、`run_offline.py` | 本分支新增，当前支付分支没有 | 当前无同名修改，不预期文本冲突；仍需在支付分支重跑实际测试 |
| `.github/workflows/quality-gates.yml` | 支付分支已改，本任务 #4/#5 没改；未来 #1 必须改 | **潜在冲突文件**，尚不能断言会有哪一行冲突。整合时同时保留支付 CI 和 PostgreSQL 16 真实执行 job，不能选 ours/theirs 覆盖一侧 |
| `backend/tests/test_pack_credits.py`、`test_submission.py` 等 | 本次 #4/#5 未改，当前支付相对 base 的改动也未列出这两文件；未来 #1 可能扩展 | 当前无冲突；届时看最终 diff，不预先宣称 #1 必定无冲突 |
| `AGENTS.md`、`docs/prod-access-log.md` | 当前支付分支无此规则/日志文件 | 规则提交单独隔离，等待用户批准后再决定同步。不要把别的分支副本变成第二份可写日志 |

因此：现在可证实的是 #4/#5 在唯一共同修改的业务文件上能自动文本合并；三项全部实现后的确切冲突清单无法提前证明。未来 #1 的 `main.py` 与 CI 文件需重新核对，文本无冲突也不等于语义验收通过。

## 3. 规则提交与唯一权威日志

规则修改独立提交 `1dca317`，且该提交只含 `AGENTS.md`、`docs/prod-access-log.md`。没有放进 main 或支付分支；权威文件访问条目仍为空。

唯一权威位置（两个文件逐字一致）：

`C:/Users/Administrator/.codex/worktrees/egress-read-design/job-application-assistant-beta/docs/prod-access-log.md`

所有分支/工作目录必须访问该绝对路径；同名副本只作规则参考，禁止追加事实。此工作树在用户另行批准迁移前不能归档。每次访问需先独占锁、记录明确授权，测量后追加实际结果；日志不可达或锁被占用就不访问。规则禁止在输出/聊天/任何日志泄露连接串、密码、token，生产写入、迁移、部署或环境变量修改须另有对应的明确批准。

未来先提交代码 PR 时，应从 main 新建集成分支，仅 cherry-pick #4、#5 的业务提交（连同其离线测试/执行文档），**不合并整个设计分支**：本分支较早的设计提交已经携带规则文件，整分支合并会把尚待批准的规则一起带入。规则是否放进 main/支付分支须单独批准，再单独选择对应文档提交；不得在同步过程中以旧分支快照覆盖权威日志事实。
