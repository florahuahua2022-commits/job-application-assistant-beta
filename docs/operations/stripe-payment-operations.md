# Stripe 支付运营手册

## 上线前门槛

1. 先确认 Supabase 套餐是否提供自动备份/PITR。无论套餐如何，migration 前都在 SQL Editor 执行 `supabase/operations/payment_backup_and_restore.sql`，把 `packcreditaccount`、`packcreditledger`、`generationusage`、`purchase`、`globalmonthlyusage` 整表复制到私有 schema；实时表与备份表五组行数必须全部为 `MATCH`。没有自动备份时，另外把五个备份表导出 CSV 到受控位置。备份只用于人工核对，不自动恢复。
2. 由负责人在生产库执行唯一的只读预检 `supabase/diagnostics/payment_preflight.sql`。它只返回一个结果集；任何 `result=BLOCK` 都停止。后端数据库用户名只需提供连接串中 `postgresql://用户名:...` 的“用户名”部分，不提供密码。
3. 先执行生产 migration，成功后才能推送/合并 `main`。失败依赖事务自动回滚；若提交后发现异常，先暂停生成写入、停止新支付、保留事件与备份，实施向前修复，不执行整表自动恢复。
4. Stripe Workbench Endpoint 固定 API 版本 `2026-09-30.endive`。优先使用受限密钥：Checkout Session 创建/读取；Event、PaymentIntent、Charge、Refund、Dispute 只读。本阶段不授予 Refund 写权限。
5. 必须先在测试模式确认下面的管理员脚本能列出失败事件、生成对账结果并补发测试 Session。不得使用真实卡、真实退款或生产密钥。

### 确认生产后端数据库角色授权

1. 只在自己的密码管理器或 Render 环境页面查看连接串；不要把连接串或密码贴到聊天、文档或工单。
2. 后端部署后，由管理员在受控终端运行 `python backend/scripts/payment_admin.py permissions`。脚本调用应用内只读接口 `/admin/payments/backend-privileges`，返回实际数据库角色，并核对 `globalmonthlyusage`、`packcreditaccount`、`packcreditledger`、`generationusage`、全部支付表、相关序列和函数。
3. `ready` 必须为 `true`。若后端角色不是 `service_role`，由数据库负责人在 migration 事务内只给该准确角色补齐报告中为 `false` 的权限，再运行一次。不得给 `anon` 或 `authenticated` 补权。

## 暂停生成写入与异常处理

1. 在 Render 把 `MONTHLY_PACK_LIMIT_GLOBAL` 的原值记在变更单，临时改为 `0` 并重新部署；这只阻止新预留。
2. 在 SQL Editor 运行 `select count(*) from public.generationusage where status='reserved' and expires_at > now();`。结果必须为 `0`；即没有未过期的预留。已过期记录可由既有释放任务处理，不阻断 migration。
3. 在一个事务内执行 migration。失败时 PostgreSQL 自动回滚；保留私有备份 schema，不启动新版本。
4. migration 成功并完成只读核对后再部署应用；最后把 `MONTHLY_PACK_LIMIT_GLOBAL` 改回原值并重新部署。
5. 若 2b 后发现问题：再次设为 `0`、停止 Checkout、确认没有未过期的预留；先做第二份现场备份并保留失败事件。不得把旧表整表覆盖回去，因为 migration 后的新付款、生成、流水和批次无法安全合并；由工程负责人编写最小向前修复 migration，经测试项目演练后执行，再运行预检和对账。不得只手改账号余额。

## 用导出数据演练历史回填

1. 只导出四张表的业务列，不导出任何密钥；在本地离线把用户 ID 替换为测试项目中预先创建的占位用户 UUID，同一原用户在四表中保持相同映射。
2. 在独立 Supabase 测试项目按外键顺序导入：`packcreditaccount`、`purchase`、`generationusage`、`packcreditledger`。金额、时间、状态和 `pack_id` 保持原样。
3. 运行统一预检；有 `BLOCK` 时保留报告并停止。通过后在事务内演练 0902 回填，失败即回滚。
4. 核对每个占位账号的批次、分摊和恒等式，并演练从备份数据人工定位差异。数据不得回流生产，结束后删除测试项目或清空测试 schema。

## 账号删除与财务记录保留（待会计确认期限）

在保留期限确定前，订单、退款和管理员支付审计均使用限制删除的外键：账号存在这些记录时删除会失败并转人工处理，不级联删除财务事实。

建议最终方案是新增不可登录的匿名支付主体：删除账号时在一个受审计事务内把订单、退款和审计记录的用户引用改到匿名主体，移除邮箱、姓名、Stripe Customer 等直接标识，只保留金额、币种、税额、时间、状态、Stripe 对象尾部或不可逆摘要及会计所需关联。匿名映射的访问仅限财务/合规角色，并按确认后的期限定时销毁。

影响：账号删除流程需要新增“有支付记录则脱敏归档”的分支；导出、对账和补发接口必须能识别已脱敏订单；退款窗口内若无法合法保留 Stripe 标识，退款只能人工处理。保留字段、期限、合法依据与删除证明格式须经会计/隐私意见确认后才能实施，当前不创建匿名主体或自动脱敏任务。

## 管理员脚本

仅在当前终端临时设置 `PAYMENT_ADMIN_BASE_URL` 和 `PAYMENT_ADMIN_TOKEN`，不要写入仓库、聊天或日志：

```powershell
python backend/scripts/payment_admin.py failed
python backend/scripts/payment_admin.py reconcile
python backend/scripts/payment_admin.py permissions
python backend/scripts/payment_admin.py replay-event evt_test_xxx
python backend/scripts/payment_admin.py replay-session cs_test_xxx
```

按 Event ID 补发受 Stripe Event 保留期限制；Stripe 已无法返回时停止并人工核对。按 Session ID 补发记录为 `admin_replay:<session_id>`，事件类型为 `admin.replay`。两种补发都复用 2a 幂等函数，不得手改余额。

## 失败、退款与拒付

- `failed` 暂时性错误返回 500 供 Stripe 重试；确定性冲突返回 200，但仍显示在管理员失败列表。
- `observed_pending` 退款/拒付先从 Stripe 重取当前对象，再核对订单、积分流水、退款状态和实际手续费。
- `charge.refunded` 只表示检测到外部退款；不得据此直接改余额。
- `needs_review` 或 `lost` 拒付订单不可退款。人工结论、管理员和时间必须留在操作审计。

## 测试模式联调

只允许 Stripe CLI 将测试事件转发到本地 Webhook，并确认所有订单/事件 `livemode=false`。结束后撤销临时 webhook secret。生产 migration、部署、推送或合并 `main` 都必须再次取得负责人明确同意。
