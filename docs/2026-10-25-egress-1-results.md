# #1 最新文档限行：执行结果

日期：2026-10-25。单独实施此前批准的 #1，不包含 #4/#5 的再次实现、不修改积分或数据库 schema。

## 修改

- `latest_application_documents()` 在用户/申请范围内，用 `row_number()` 按文档类型分组，按 `created_at DESC, id DESC` 排序；子查询仅输出 id/rank，外层只取 rank=1 的完整文档。
- quality-check、pack-review 的独立全历史查询也复用该 helper，避免保留两条遗漏路径；history endpoint 继续返回全部版本。
- required/current 仍在选最新后判定，不先过滤通过稿或 current 稿；保留未知文档类型和本地 user_id=None 语义。每次 helper 实际返回 K 行，不承诺数据库内部只扫描 K 行。
- 新增代码行统一 CRLF。提交前 #4/#5 的旧变量引用已作 AST 核对：quality-check 无 other_application 名称，状态函数无 application 名称；质量检查自己的当前 application 正常保留。

## RED → GREEN 证据

SQLite：修改前验证 latest helper 与旧 main `dbce8ca` 的 AST 完全相同；新增断言实际读到 **91 行而非 4 行**，同时间戳也未按最大 id 决胜。结果为 7 个失败（含子测试），不是夹具运行错误。修改后同一批 6 项测试、6 项子测试通过。

真实 PostgreSQL 16：在新建的独立临时本地集群执行完全相同的六项测试；baseline 模式只装载 `dbce8ca` 的 latest/quality/pack-review 三个原始函数，产生 **7 个断言失败、0 个运行错误**；新代码 6 项全部通过。服务版本实际确认为 16，集群完成后已停止。不读取现有数据库配置，不继承生产凭据，不连接生产。

完整离线回归：**633 passed, 2 skipped, 198 subtests passed**，127 个原有弃用警告。跳过的两项仍是原有可选 PostgreSQL 积分测试；本次 PostgreSQL 16 六项验证已经另行真实执行，不能将这两项 skip 与新窗口验证混淆。

合成夹具：每类型 30/100 份历史，三类标准文档加未知类型；另有非 owner 和其他申请。最新结果只读四行，历史增长不增加返回的大字段字节量；覆盖同时间戳、旧通过/新过期不回退、required/current 积分条件、quality/release/pack-review 查询行数、历史列表及本地无 user 过滤。

## GitHub Actions

新增 `latest-documents-postgres-16` job，临时 `postgres:16` service，以专用合成数据库执行同一批旧代码 RED 和新代码 GREEN。该 job 不因缺少 PostgreSQL 而 skip；明确校验 server major=16。原 backend-tests job 改用相同的隔离运行器执行完整 SQLite 回归，并用 pipefail 传递测试退出状态。只额外允许本任务分支的 push 触发，不改部署或其他分支。

首次 Actions 中 PostgreSQL RED/GREEN 均成功，但完整 SQLite 回归为 40 failed / 593 passed：CI 无上界升级到 SQLModel 0.0.48 / SQLAlchemy 2.1.6 / Pydantic 2.14.1，对现有无时区日期触发新的校验。本次不改日期业务；新增仅供测试 job 使用的约束文件，固定到本地已验证的 SQLModel 0.0.39 / SQLAlchemy 2.0.52 / Pydantic 2.13.4，并在两个后端 job 共用。生产 requirements 范围未修改，更高版本兼容性仍应另行处理；不将约束后的通过解释为所有允许版本都通过。

Workflow 的执行状态以本次分支 push 后的 Actions 记录为准；本地运行通过不冒称 GitHub 已通过。没有将任何生产 secrets 注入 job，也不打印连接串、密码或 token。

## 权威日志与分支边界

唯一权威日志已由独立规则提交 `339687e` 迁往：

`D:/Users/Administrator/Documents/job-application-assistant-audit/prod-access-log.md`

仓库内同名文件仅为规则/位置引用，旧设计与交接文档中的工作树路径是历史记录，不能再作为有效位置。权威账本的生产访问条目为空。本次只推送 `codex/egress-read-design`，不合并 main、不修改支付分支、不部署。
