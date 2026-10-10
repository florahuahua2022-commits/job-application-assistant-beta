# 积分支付 Schema 审计与数据模型设计

## 目标与边界

本设计覆盖两项工作：审计生产 schema 对应用启动时建表和补列逻辑的依赖；设计 Stripe Checkout Session、Stripe Event、积分发放、积分消耗归属及退款所需的数据模型。

本阶段只提交审计与设计，不修改 migration、应用启动逻辑或数据库对象，不访问生产数据库，不连接真实 Stripe，也不触发真实付款或退款。后续实现必须先写失败测试，并在 GitHub Actions 的 PostgreSQL 16 服务容器中验证并发行为。

## 现状审计

### 启动时写入行为

`backend/app/main.py` 的启动事件调用 `create_db_and_tables()`。该函数当前执行两类 schema 写入：

1. `SQLModel.metadata.create_all(engine)`：如果整张 ORM 表缺失，则在应用启动时创建。
2. 针对 PostgreSQL 和 SQLite 的运行时 `ALTER TABLE`：检查列是否存在，并在缺失时补列和回填部分数据。

`create_all` 不会给现有表补列。因此“依赖 `create_all` 的表”和“依赖运行时 DDL 的字段”必须分开看待。

### 受 create_all 兜底的表

当前 12 张 ORM 表都会被 `create_all` 兜底创建：

- `resume`
- `applicantprofile`
- `referee`
- `jobapplication`
- `jobsource`
- `generateddocument`
- `generationusage`
- `packcreditaccount`
- `packcreditledger`
- `globalmonthlyusage`
- `creditledger`
- `referral`

迁移历史中能够找到这些表的建表来源，因此在“全部 migration 已按顺序成功执行”的新数据库中，它们不应实际依赖 `create_all`。风险在于：生产环境若漏跑迁移，应用会静默补建缺失表，隐藏部署错误，并可能产生与 migration 中约束、索引、RLS、权限或注释不一致的表。

`purchase` 只存在于 `20260807_commercial_foundation.sql`，不属于当前 ORM 模型，因此不受 `create_all` 管理。它是早期为 Stripe 购买预留的订单表，已有用户、Checkout Session、PaymentIntent、状态、币种、金额和积分字段，但当前没有应用代码读写。它与原方案中的 `stripepaymentorder` 职责重复；本设计不再创建重复表，而是通过 migration 扩展并复用 `purchase`。

### 依赖运行时 DDL 的字段

启动函数会检查并补齐以下表的字段：

- `resume`：`experiences_json`、`ckb_json`、`experience_exclusions_json`
- `generateddocument`：`used_experiences_json`、`closing_styles_json`、`structured_content_json`、`reviewer_json`、`run_id`、`trace_json`
- `jobapplication`：`job_model_json`、`application_requirements_json`、`resume_snapshot_json`、`evidence_matches_json`、`application_decision_json`、`release_state_json`、`outcome_json`、`selection_plan_json`、`selection_confirmations_json`、`archived_at`
- `jobsource`：`discovery_context`
- `applicantprofile`：`target_direction`、`motivation`、`writing_tone`、`preferences_notes`
- `generationusage`：`completed_at`、`status`、`credit_cost`、`reserved_at`、`expires_at`、`released_at`、`usage_month`

其中只有以下两列在当前 migration 历史中没有等价定义，生产 schema 若已有它们，目前只能来自启动时 DDL 或人工变更：

- `resume.experience_exclusions_json`
- `jobapplication.resume_snapshot_json`

其余列已有 migration，但启动时仍重复兜底，会继续掩盖漏跑 migration 的问题。

## Schema 迁移治理方案

### 生产库只读比对

移除任何启动时 DDL 前，由产品负责人本人在生产库执行只读比对；Codex 不连接生产库。比对脚本只读取 `pg_catalog` 和 `information_schema`，不得创建临时表、函数或扩展，也不得修改 migration 历史。

只读比对至少输出：

1. 预期表是否存在，以及 owner、RLS 状态和表类型。
2. 每张表的列名、类型、可空性、默认值和 identity 属性。
3. 主键、外键、唯一约束、检查约束及其定义。
4. 普通索引、唯一索引和部分索引定义。
5. RLS policy、表权限、序列权限和后端函数权限。
6. `public` 下现有函数的签名、执行权限和 `search_path`。
7. 已执行 migration 记录与仓库 `supabase/migrations` 文件列表的差异。
8. 两个已知缺口列是否存在，以及其类型、默认值和 `NOT NULL` 是否符合模型预期。

执行人将查询结果保存为脱敏文本，不包含用户数据、密钥或连接字符串。仓库侧用同一组只读查询检查由全部 migration 重建的 PostgreSQL 16 测试库，再进行结构化 diff。任何差异先通过新的 migration 修复，不直接手工改生产 schema。

可直接粘贴到 Supabase SQL 编辑器的只读脚本见文末附录 A。脚本只有 `SELECT`/CTE，不读取业务行，不创建临时对象，也不修改数据库。

### 补齐缺失 migration

新增 migration 仅补齐：

```sql
alter table public.resume
    add column if not exists experience_exclusions_json text not null default '[]';

alter table public.jobapplication
    add column if not exists resume_snapshot_json text not null default '{}';
```

两列使用 `IF NOT EXISTS`，使 migration 在已经由旧启动逻辑补列的环境中可安全重复执行。migration 不删除数据，不修改已有列类型，也不访问业务行内容以外的数据。

### 部署顺序

1. 在 GitHub Actions PostgreSQL 16 服务容器中，从空库重放全部 migration。
2. 在同一容器执行 schema 合同比对和后端测试。
3. 使用 Supabase CLI 对待部署 migration 做 dry-run；目标必须是本地或 CI 测试环境，不链接生产项目。
4. 由产品负责人执行生产库只读比对并确认差异清单。
5. 部署包含两列的幂等 migration，仍保留旧启动 DDL 作为一个发布周期的临时兜底。
6. 再次执行生产只读比对，确认 migration 历史与目标 schema 一致。
7. 另起独立提交移除生产启动路径中的 `create_all` 和所有运行时 DDL。
8. 部署新应用；启动只执行只读 schema 校验，校验失败时拒绝就绪并输出缺失对象名称，不尝试修复。

### 启动时只读 schema 校验

应用启动时读取 `information_schema`/SQLAlchemy inspector，核对一组版本化的必要对象：关键表、两列、支付表、关键唯一约束和后端函数签名。校验只回答“当前应用版本能否安全运行”，不承担完整迁移工具职责。

缺失或不兼容时，应用健康检查返回未就绪，日志列出对象名和预期，不执行 `CREATE`、`ALTER`、回填或权限修改。SQLite 单元测试可继续显式调用 `create_all`；生产应用启动不得调用。

移除 DDL 必须单独提交，不能和补列 migration 或支付模型混在同一提交中，以便独立回滚应用版本而不回滚 schema。

## 支付数据模型

### 金额规则

所有金额使用整数最小货币单位和 ISO 币种，例如 `1695` + `AUD`，不使用浮点数。订单保存结账时的套餐价格、GST、实付总额和币种快照，后续配置变化不影响历史订单。

Stripe 实际手续费不在创建订单时估算。退款计算开始时从 Stripe 对应交易的 Balance Transaction 读取实际手续费，保存到订单和退款记录的整数分字段。若将来确认 Stripe 已退还手续费，退款计算规则再通过独立 migration/业务变更调整。

### purchase 订单表

复用现有 `public.purchase`；一行表示一次 Checkout Session 对应的订单。migration 保留现有主键和唯一约束，并补充：

- 内部 UUID 主键。
- `user_id`。
- 唯一 `stripe_checkout_session_id`。
- 可空且唯一的 `stripe_payment_intent_id`、`stripe_charge_id` 和 `stripe_balance_transaction_id`。
- `package_code`、`credits_purchased`。
- `subtotal_cents`、`gst_cents`、`total_paid_cents`、`currency`。
- `single_pack_price_cents`：该订单创建时 single 套餐的含税标价快照，退款不读取当前配置。
- 可空 `actual_stripe_fee_cents`，只保存 Stripe 实际值。
- `status`：`checkout_created`、`paid`、`expired`、`payment_failed`、`refunded`。
- `paid_at`、`created_at`、`updated_at`。
- `dispute_status`：默认 `none`；拒付事件置为 `pending_manual`，v1 不自动扣积分。

服务端按 `package_code` 写入金额和积分，绝不接受前端金额或 `user_id`。

`purchase` 现有的 authenticated 只读 policy 和 `GRANT SELECT` 将被撤销。订单只能由受信后端访问；前端若需要展示，必须通过校验当前用户身份的后端接口取得最小字段。

### stripeevent

一行表示一个 Stripe Event：

- `stripe_event_id` 为主键或唯一约束。
- `event_type`、可空订单外键。
- `status`：`received`、`processing`、`processed`、`failed`。
- `attempt_count`、`last_error`、`processed_at`、时间戳。

唯一 ID 不等于“永远跳过”。处理规则为：

1. 锁定该事件行。
2. 只有 `processed` 才直接作为重复事件返回成功。
3. `failed` 可以重新进入 `processing`，并递增 `attempt_count`。
4. 订单状态更新、积分发放、积分批次创建和事件置为 `processed` 必须在同一数据库事务提交。
5. 事务失败时所有业务写入回滚；随后用独立的短事务记录或更新 `failed`、错误摘要和尝试次数，使事件可重处理。

这样既不会把失败事件永久误判为重复，也不会出现事件已成功而积分未提交，或积分已提交而事件仍未处理的状态。

### 数据库权限

`purchase`、`stripeevent`、`paymentrefund`、`packcreditlot`、`packcreditallocation` 全部启用 RLS，不为 `anon` 或 `authenticated` 创建 policy，并显式 `REVOKE ALL`。表、序列和支付/充值函数只授予受信后端角色。

通用充值函数及现有管理员充值函数显式执行：

```sql
revoke all on function ... from public, anon, authenticated;
```

随后只向部署使用的受信后端数据库角色授予 `EXECUTE`。不能依赖“没有前端调用代码”作为权限边界。

### packcreditledger 关联

扩展流水允许区分：

- `grant_stripe_purchase`
- `grant_manual_topup`
- `grant_free`
- `debit_generation`
- `release`
- 后续退款所需的 `debit_refund` 和 `release_refund_failure`

Stripe 购买发放流水增加可空订单外键；管理员和免费发放必须没有订单外键。Stripe 发放的幂等键由订单 ID 或 Checkout Session ID 确定，并继续受现有流水唯一约束保护。

现有管理员充值函数将整理为一个通用的内部充值事务入口，管理员充值和 Stripe 购买都调用它。入口按来源验证参数并写入不同流水类型；管理员 API 保持原有权限和行为。不会复制一套第二充值算法。

## Pack 消耗归属

### 不采用订单字段直接推算

仅在订单上保存 `credits_used` 或 `credits_remaining` 无法解释一次 2 Pack 消耗跨越两个订单的情况，也无法可靠恢复生成失败释放的原始来源。仅用账户总余额和流水金额反推，在并发扣减和释放后也容易产生歧义。

### 采用积分批次和分摊流水

新增最小的积分批次与分摊结构：

- `packcreditlot`：每次正向发放生成一个批次，关联发放流水；保存来源、初始积分、剩余积分、可空支付订单和发放时间。
- `packcreditallocation`：将每次 `debit_generation` 流水分摊到一个或多个批次，保存每个批次消耗的积分数；释放时按原分摊返还。

消费事务先按全局加锁顺序取得锁，再按 `packcreditlot.created_at, id` 从早到晚锁定并扣减批次，直到满足本次 Pack 成本。订单已用 Pack 数等于该订单批次的初始积分减剩余积分，且可由 allocation 审计验证。

免费积分和管理员发放也创建批次，并按实际发放时间进入同一个 FIFO 队列。它们轮到时可以被正常消费，但没有支付订单关联，因此永远不计入可退款订单，也不会因退款被扣回。这个规则避免为了提高退款额而人为把非退款积分固定在队首或队尾。

### 历史批次回溯与对账

migration 按 `packcreditledger.created_at, id` 的稳定顺序回放历史：

1. `grant_free` 和 `grant_manual_topup` 各生成一个非退款批次。
2. `debit_generation` 按当时已存在批次的 FIFO 顺序生成历史 allocation。
3. `release` 通过相同 `pack_id` 找到对应 `debit_generation`，按原 allocation 逆向恢复；找不到唯一原扣减时 migration 失败，不猜测来源。
4. 历史数据没有 Stripe 购买流水，不会被错误关联到未来支付订单。

`packcreditaccount.balance` 定义为账号尚未最终消费的积分总数，包含正在生成中已预留的积分；可用余额为 `balance - active_reserved_credits`。生成预留先从批次可用量移入预留 allocation，完成后才扣减 `balance`，失败释放则回到原批次。

每个账号必须满足：

```text
packcreditaccount.balance
= sum(packcreditlot.remaining_credits)
  + sum(generationusage.status = 'reserved' 的预留积分)
```

migration 回溯完成后逐账号核对该等式；任何不一致都终止 migration，并输出账号 ID 和三项汇总值，不自动制造平账流水。支付功能启用后的启动只读校验和 CI 也执行同一核对查询。

## 退款数据模型与并发

### paymentrefund

退款记录包含：

- 内部 UUID 主键、订单外键、`user_id`。
- `status`：`pending`、`processing`、`reconciling`、`succeeded`、`failed`、`rejected`。
- `stripe_refund_id`，成功后唯一。
- 确定性的 `stripe_idempotency_key`，按订单生成并唯一。
- `total_paid_cents`、`actual_stripe_fee_cents`、`used_pack_count`、`single_pack_price_cents`、`refund_amount_cents`、`currency` 的计算快照；`single_pack_price_cents` 从订单快照复制。
- `credits_withheld`、`requested_at`、`succeeded_at`、`failed_at`、`rejected_at`、错误摘要。

订单唯一约束保证一个订单只有一个退款记录；失败后复用该记录重试，不创建第二个订单退款。`processing`、`reconciling` 和最近 30 天内的 `succeeded` 都占用账号退款额度；只有明确进入 `failed` 后才释放额度。`rejected` 不占用额度。

### 30 天一次的账号级锁

滚动 30 天窗口不能由普通唯一索引完整表达。退款申请事务先获取账号级 PostgreSQL advisory transaction lock，例如以 `refund:<user_id>` 派生锁键；随后再次查询该账号最近一次 `succeeded_at`。

检查额度时同时查询该账号所有 `processing`、`reconciling` 记录，以及最近 30 天内的 `succeeded`。若存在进行中退款，第二笔申请返回“已有退款处理中”；若未满 30 天，记录为 `rejected` 并返回最早可再次申请日期。账号级锁会串行化同一账号的并发订单退款；订单行锁和订单唯一约束再防止同一订单并发重复。不同账号互不阻塞。

### 退款状态机

1. `pending`：已创建申请，尚未扣留积分。
2. `processing`：在数据库短事务中获取账号锁、订单锁和余额锁，验证六个月期限、30 天规则、无进行中生成预留且订单无拒付，按订单批次计算已用 Pack，扣留该订单剩余积分并写退款扣减流水。设置 `processing_started_at` 和短期 `lease_expires_at`。
3. 调用 Stripe Refund API 时使用数据库中确定性的 `stripe_idempotency_key`。网络调用不放在数据库事务内。
4. Stripe 明确成功：短事务写入 `stripe_refund_id`、金额和 `succeeded_at`，订单置为 `refunded`。
5. Stripe 明确失败：补偿事务写 `release_refund_failure`，把扣留积分恢复到原订单批次，退款置为 `failed`，订单保持 `paid`，此时才释放账号 30 天额度占用。
6. Stripe 超时或结果未知：置为 `reconciling`，先按幂等键查询 Stripe 最终结果；在确认失败前不恢复积分、不释放额度，也不发起新的不同幂等键退款。
7. 资格不符：置为 `rejected`，不扣积分、不调用 Stripe、不占用 30 天额度。

`processing` 不得无限卡住。后台恢复任务扫描过期 lease：先把记录置为 `reconciling`，再用原 Stripe 幂等键查询结果；查到成功则完成，查到明确失败则补偿并置 `failed`，仍无法确认则保持 `reconciling` 并告警人工处理。恢复任务不得创建新幂等键。

退款金额为：

```text
max(0, total_paid_cents - used_pack_count * single_pack_price_cents - actual_stripe_fee_cents)
```

其中 `single_pack_price_cents` 使用订单购买时适用的 single 套餐含税快照。实际手续费在进入 Stripe 退款调用前读取并固化。

若实际手续费暂时读取不到，不使用估算值、不调用 Stripe 退款；记录进入 `reconciling`，使用同一订单和幂等键重试读取并告警。退款金额计算为 0 时仍扣回该订单剩余积分并把退款记录完成为 `succeeded`，但不调用 Stripe Refund API；该成功记录占用 30 天额度。

自助退款资格检查必须在创建退款记录和调用 Stripe 前同时读取锁定订单并确认：`refund_detected_at is null`，且 `dispute_status = 'none'`。任一条件不满足都拒绝自助退款并转人工处理；不得因本地退款记录尚不存在而绕过 Stripe 已退款或拒付状态。有 `needs_review`、`lost`、旧值 `pending_manual` 或其他非 `none` 状态的订单均不可自助退款。

### 拒付

拒付事件仍按 Stripe Event ID 幂等处理。v1 只把订单 `dispute_status` 标记为 `pending_manual`，保存事件关联并进入人工处理队列；不自动扣积分、不创建退款记录。后续自动化拒付处理不属于本次范围。

## 全局加锁顺序

所有会改变积分、订单或退款状态的数据库函数遵守同一顺序：

1. Stripe Event 行锁（仅 webhook；用于取得并保护事件幂等状态）。
2. 账号 advisory transaction lock，按 `user_id` 派生。
3. `packcreditaccount` 账号行。
4. `generationusage` 活跃预留，按 `pack_id` 排序。
5. `purchase` 订单行，按主键排序。
6. `paymentrefund` 行。
7. `packcreditlot` 行，按 `created_at, id` 排序。
8. 追加 ledger/allocation，最后更新事件完成状态。

任何路径不得逆序获取两个已有锁。多账号运维操作必须先按 `user_id` 排序。Stripe 网络调用不持有以上数据库锁。

## 流程与事务边界

### 支付成功事件

1. 验证 Stripe 签名后读取事件。
2. 锁定或创建 `stripeevent`。
3. 已 `processed` 则返回成功；`failed` 允许重试。
4. 锁定订单并验证 Session、用户、套餐、金额和币种快照。
5. 调用现有通用充值事务入口，写购买流水、积分批次和账户余额。
6. 更新订单为 `paid`，事件为 `processed`。
7. 同一数据库事务提交。

事件唯一约束、购买流水幂等键和订单状态共同保证重复或并发投递只发放一次。

### 退款

退款采用数据库短事务、Stripe 幂等调用、数据库完成或补偿事务三段式。禁止持有数据库行锁等待 Stripe 网络响应。任何不确定结果进入 `reconciling`，不得假定失败后直接发起另一笔退款。

账号存在任一 `generationusage.status = 'reserved'` 时暂不受理退款，提示生成任务完成或释放后重试。这样退款不会和生成预留争夺同一批次。生成失败释放必须按 allocation 返回原批次；若目标批次所属订单已退款，函数拒绝恢复并进入一致性告警，绝不把积分还回已退款批次。正常流程中退款前置检查应使该防御分支不可达。

## 测试与验证

### GitHub Actions PostgreSQL 16

新增 CI job 使用官方 `postgres:16` 服务容器和独立测试数据库。测试连接串只指向 Actions 服务容器，数据库名必须包含 `test`；测试会重建测试 schema，不接受任何生产或共享数据库 URL。

至少验证：

- 从空 PostgreSQL 16 重放全部 migration。
- 两列 migration 重复执行不会失败。
- 同一 Stripe Event 两个并发连接只有一次积分发放和一个购买批次。
- 失败事件可重处理，成功事件重复投递不再执行。
- 事件处理状态、订单状态、积分流水和余额同事务提交或同事务回滚。
- FIFO 跨批次消耗及失败释放回到原批次。
- 免费、管理员和 Stripe 批次按发放时间消费，但只有 Stripe 订单参与退款。
- 同一订单并发退款只进入一次处理。
- 同一账号不同订单并发退款，在账号级锁下最多一个成功。
- 第一笔退款已进入 `processing` 并阻塞在 Stripe 调用时，第二笔退款被“已有退款处理中”拒绝；第一笔明确失败后第二笔才可申请。
- Stripe 明确失败时积分补偿、订单保持 `paid`，失败不占用 30 天额度。
- 超时进入 `reconciling`，不会重复发起不同幂等键的退款。
- 有活跃生成预留时退款被拒绝；释放严格回到原批次，已退款批次不可恢复。
- 历史回溯后逐账号满足“账号余额 = 批次剩余之和 + 进行中预留”。
- 有未关闭拒付的订单不可退款；退款金额为 0 时不调用 Stripe；手续费不可得时不使用估算值。

外部 Stripe 调用全部使用测试替身；测试不需要密钥，不触发真实付款或退款。

### Supabase CLI dry-run

当前开发机没有 Supabase CLI、Docker、`psql` 或本地 PostgreSQL 16。实现阶段不在此机器连接生产来补足环境。Supabase CLI dry-run 应在 GitHub Actions 或经明确批准的隔离环境中执行，并保存命令、CLI 版本、目标环境证明和输出作为提交验证记录。

在 dry-run 成功前，不宣称 migration 可部署；在 PostgreSQL 16 并发测试成功前，不宣称幂等或退款并发保护完成。

## 附录 A 生产库只读比对脚本

以下脚本可直接粘贴到 Supabase SQL 编辑器运行。它只返回 schema 元数据，不返回业务数据：

```sql
with expected_tables(table_name) as (
    values
        ('resume'), ('applicantprofile'), ('referee'), ('jobapplication'),
        ('jobsource'), ('generateddocument'), ('generationusage'),
        ('packcreditaccount'), ('packcreditledger'), ('globalmonthlyusage'),
        ('creditledger'), ('referral'), ('purchase')
), report as (
    select
        'table'::text as section,
        e.table_name::text as object_name,
        jsonb_build_object(
            'exists', c.oid is not null,
            'owner', pg_get_userbyid(c.relowner),
            'rls_enabled', c.relrowsecurity,
            'kind', c.relkind
        ) as details
    from expected_tables e
    left join pg_class c
        on c.relname = e.table_name
       and c.relnamespace = 'public'::regnamespace

    union all

    select
        'column',
        cols.table_name || '.' || cols.column_name,
        jsonb_build_object(
            'type', cols.data_type,
            'udt', cols.udt_name,
            'nullable', cols.is_nullable,
            'default', cols.column_default,
            'identity', cols.is_identity
        )
    from information_schema.columns cols
    join expected_tables e using (table_name)
    where cols.table_schema = 'public'

    union all

    select
        'constraint',
        con.conrelid::regclass::text || '.' || con.conname,
        jsonb_build_object('type', con.contype, 'definition', pg_get_constraintdef(con.oid))
    from pg_constraint con
    where con.connamespace = 'public'::regnamespace
      and con.conrelid::regclass::text = any (
          select 'public.' || table_name from expected_tables
      )

    union all

    select
        'index',
        schemaname || '.' || indexname,
        jsonb_build_object('table', tablename, 'definition', indexdef)
    from pg_indexes
    where schemaname = 'public'
      and tablename in (select table_name from expected_tables)

    union all

    select
        'policy',
        schemaname || '.' || tablename || '.' || policyname,
        jsonb_build_object(
            'roles', roles, 'command', cmd, 'using', qual, 'check', with_check
        )
    from pg_policies
    where schemaname = 'public'
      and tablename in (select table_name from expected_tables)

    union all

    select
        'grant',
        table_schema || '.' || table_name || ':' || grantee,
        jsonb_build_object('privilege', privilege_type)
    from information_schema.role_table_grants
    where table_schema = 'public'
      and table_name in (select table_name from expected_tables)

    union all

    select
        'function',
        n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
        jsonb_build_object(
            'owner', pg_get_userbyid(p.proowner),
            'security_definer', p.prosecdef,
            'config', p.proconfig,
            'acl', p.proacl
        )
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'

    union all

    select
        'migration',
        version::text,
        jsonb_build_object('name', name)
    from supabase_migrations.schema_migrations
)
select section, object_name, details
from report
order by section, object_name;
```

运行前确认 SQL 编辑器顶部显示的是生产项目名称；脚本中不得加入 `INSERT`、`UPDATE`、`DELETE`、`CREATE`、`ALTER`、`DROP` 或 `DO`。运行后下载 CSV，并人工检查 `resume.experience_exclusions_json`、`jobapplication.resume_snapshot_json`、所有表的 RLS、约束、索引、权限和 migration 版本。

## 附录 B 两列幂等 migration SQL

```sql
alter table public.resume
    add column if not exists experience_exclusions_json text not null default '[]';

alter table public.jobapplication
    add column if not exists resume_snapshot_json text not null default '{}';
```

## 附录 C 非工程人员执行步骤

1. 登录 Supabase，确认打开的是正确项目；不要复制或发送数据库密码、API key 或连接字符串。
2. 打开 SQL Editor，新建空白查询。
3. 完整复制附录 A，只运行一次；它只读取结构信息，不改数据。
4. 点击下载结果为 CSV，文件名标注执行日期；不要截图或导出业务表。
5. 把 CSV 放到约定的私密位置，通知工程人员比对；不要在生产库手工补列。
6. 工程人员在 GitHub Actions PostgreSQL 16 验证附录 B、全部 migration 和并发测试，并完成 Supabase CLI dry-run。
7. 验证通过后，按发布流程部署附录 B 对应 migration；不要直接在 SQL Editor 手工运行附录 B，除非正式发布流程明确要求且已有备份和回滚安排。
8. migration 部署后，再运行附录 A 并下载第二份 CSV，用于确认两列和 migration 版本已出现。
9. 等工程人员确认结构一致后，下一次独立发布才移除应用启动时 DDL；若启动只读校验失败，停止发布，不让应用自动修库。

## 提交边界

1. 本提交：现状审计与设计文档，不实施。
2. 后续支付数据模型提交：先提交失败测试，再添加 migration、模型和最小函数改动；执行 PostgreSQL 16 测试和 Supabase CLI dry-run。
3. Schema 治理实施拆分：补列 migration 与移除启动 DDL 分开提交；移除 DDL 必须等待生产只读比对和补列 migration 部署完成。

Checkout API、Webhook HTTP 接口、前端、真实 Stripe 接入和退款 HTTP 接口均不属于本次数据模型提交。
