# Stripe 支付运营手册

## 上线前门槛

1. 先确认 Supabase 套餐是否提供自动备份/PITR；若没有，在 Dashboard 的 Table Editor 分别导出 `packcreditaccount`、`packcreditledger`、`generationusage`、`purchase` CSV，并保存到受控位置。
2. 由负责人在生产库执行只读预检 `supabase/diagnostics/stripe_checkout_preflight.sql`。后端数据库用户名只需提供连接串中 `postgresql://用户名:...` 的“用户名”部分，不提供密码。
3. 先执行生产 migration，成功后才能推送/合并 `main`。失败依赖事务自动回滚；若 2b 后发现异常，先暂停生成写入、停止新支付、保留事件，再按备份恢复并回滚应用版本。
4. Stripe Workbench Endpoint 固定 API 版本 `2026-09-30.endive`。优先使用受限密钥：Checkout Session 创建/读取；Event、PaymentIntent、Charge、Refund、Dispute 只读。本阶段不授予 Refund 写权限。
5. 必须先在测试模式确认下面的管理员脚本能列出失败事件、生成对账结果并补发测试 Session。不得使用真实卡、真实退款或生产密钥。

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
