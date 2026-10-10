# Stripe 支付运营手册

## 上线前门槛

1. 先确认 Supabase 套餐是否提供自动备份/PITR。无论套餐如何，migration 前都在 SQL Editor 执行 `supabase/operations/payment_backup_and_restore.sql` 的 BACKUP 部分，把 `packcreditaccount`、`packcreditledger`、`generationusage`、`purchase` 整表复制到私有 schema；核对四个行数后再继续。没有自动备份时，另外把四个备份表导出 CSV 到受控位置。
2. 由负责人在生产库执行唯一的只读预检 `supabase/diagnostics/payment_preflight.sql`。它只返回一个结果集；任何 `result=BLOCK` 都停止。后端数据库用户名只需提供连接串中 `postgresql://用户名:...` 的“用户名”部分，不提供密码。
3. 先执行生产 migration，成功后才能推送/合并 `main`。失败依赖事务自动回滚；若 2b 后发现异常，先暂停生成写入、停止新支付、保留事件，再按备份恢复并回滚应用版本。
4. Stripe Workbench Endpoint 固定 API 版本 `2026-09-30.endive`。优先使用受限密钥：Checkout Session 创建/读取；Event、PaymentIntent、Charge、Refund、Dispute 只读。本阶段不授予 Refund 写权限。
5. 必须先在测试模式确认下面的管理员脚本能列出失败事件、生成对账结果并补发测试 Session。不得使用真实卡、真实退款或生产密钥。

## 暂停生成写入、恢复与回滚

1. 在 Render 把 `MONTHLY_PACK_LIMIT_GLOBAL` 的原值记在变更单，临时改为 `0` 并重新部署；这只阻止新预留。
2. 在 SQL Editor 运行 `select count(*) from public.generationusage where status='reserved';`。结果必须为 `0`；否则等正在生成的任务完成或过期释放，不得开始 migration。
3. 在一个事务内执行 migration。失败时 PostgreSQL 自动回滚；保留私有备份 schema，不启动新版本。
4. migration 成功并完成只读核对后再部署应用；最后把 `MONTHLY_PACK_LIMIT_GLOBAL` 改回原值并重新部署。
5. 若 2b 后发现问题：再次设为 `0`、停止 Checkout、确认无 `reserved`；回滚应用版本；先做第二份现场备份，再由负责人取消恢复脚本 RESTORE 部分的注释并执行。恢复后重新运行预检，不得只手改账号余额。

## 用导出数据演练历史回填

1. 只导出四张表的业务列，不导出任何密钥；在本地离线把用户 ID 替换为测试项目中预先创建的占位用户 UUID，同一原用户在四表中保持相同映射。
2. 在独立 Supabase 测试项目按外键顺序导入：`packcreditaccount`、`purchase`、`generationusage`、`packcreditledger`。金额、时间、状态和 `pack_id` 保持原样。
3. 运行统一预检；有 `BLOCK` 时保留报告并停止。通过后在事务内演练 0902 回填，失败即回滚。
4. 核对每个占位账号的批次、分摊和恒等式，再演练恢复 SQL。数据不得回流生产，结束后删除测试项目或清空测试 schema。

## 管理员脚本

仅在当前终端临时设置 `PAYMENT_ADMIN_BASE_URL` 和 `PAYMENT_ADMIN_TOKEN`，不要写入仓库、聊天或日志：

```powershell
python backend/scripts/payment_admin.py failed
python backend/scripts/payment_admin.py reconcile
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
