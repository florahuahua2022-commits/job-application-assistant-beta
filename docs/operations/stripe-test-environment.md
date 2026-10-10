# Stripe 积分支付测试环境搭建手册

适用分支：`codex/payment-data-model`  
基准提交：`a8e114b`  
适用范围：全新的 Supabase 测试项目、全新的 Render 测试服务、Stripe 测试模式。

> 安全边界：不要在聊天、工单、截图或仓库中粘贴数据库连接串、Bearer Token、Stripe 密钥或 Webhook 签名密钥。本手册不访问生产数据库、生产 Render 服务或 Stripe 真实模式，也不触发真实付款或退款。

> `a8e114b` 已知上线阻断项：真实 PostgreSQL 路径中，Checkout 先写入的 `pending` 订单尚无 PaymentIntent，而 2a SQL 当前会把成功 Webhook 带来的首个 PaymentIntent 判为事实冲突。搭建环境可以继续，用于验证迁移、权限、失败审计和复现问题；在修复并补充 PostgreSQL 端到端测试前，不应把“成功付款发积分”判为验收通过，也不得进入生产。

## 0. 准备清单

准备以下账号和本机工具：

- 一个可以新建项目的 Supabase 账号；
- 一个可以新建服务的 Render 账号；
- 一个 Stripe 账号，并确认左上角处于 **Test mode / 测试模式**；
- Git、Python 3.12、Supabase CLI、Stripe CLI；
- 仓库功能分支 `codex/payment-data-model`。

本机 PowerShell 中运行下面命令，只读取当前分支和提交，不会修改文件：

```powershell
git switch codex/payment-data-model
git rev-parse --short HEAD
```

预期第二条输出为 `a8e114b`。如果不是，停止并让工程人员确认版本。

## 1. 新建独立 Supabase 测试项目

### 1.1 创建项目

1. 登录 Supabase Dashboard。
2. 点击 **New project**。
3. 项目名称加入明显的 `payment-test` 标记。
4. 选择与预期测试服务相同或相近的区域。
5. 生成独立数据库密码，并保存到密码管理器；不要发到聊天里。
6. 等项目状态变为可用。

影响：只创建一个空的测试项目，不影响任何已有项目。

### 1.2 先建立基础 schema

三个支付 migration 不是空库初始化脚本：`2026100901` 依赖早期 migration 已建立的 `auth.users`、`purchase`、`packcreditaccount`、`packcreditledger` 和 `generationusage`。新项目必须先应用仓库中排在 `2026100901` 之前的全部 migration。

在本机仓库根目录的 PowerShell 中运行：

```powershell
supabase login
supabase link --project-ref <仅填写测试项目的 Project Ref>
supabase db push --linked --dry-run --include-all
```

影响：前两条只让本机 CLI 连接测试项目；第三条只预演全部待执行 migration，不写数据库。逐项确认目标 Project Ref 是测试项目，且预演列表以 `20260804_online_beta.sql` 开始、包含三个 `20261009` 支付 migration。

确认无误后，在同一个 PowerShell 窗口运行：

```powershell
supabase db push --linked --include-all
```

影响：只在已链接的测试项目中按文件名顺序执行 migration。三个目标文件会依次执行：

1. `2026100901_payment_orders_events.sql`
2. `2026100902_credit_lots_refunds.sql`
3. `2026100903_stripe_checkout_webhook.sql`

任一 migration 失败时停止，不要手工跳过失败文件。每个目标脚本都使用事务；失败应回滚该脚本。

### 1.3 在 SQL Editor 执行两个只读预检

在 Supabase Dashboard 打开测试项目的 **SQL Editor**。从功能分支逐个打开下列文件，复制全文到新的查询窗口并点击 **Run**：

1. `supabase/diagnostics/payment_preflight.sql`
2. `supabase/diagnostics/stripe_checkout_preflight.sql`

影响：两份脚本都只读，不修改业务数据。第一份检查余额与流水、歧义释放、`purchase` 行/状态和数据库角色；第二份在只读事务中检查订单、Stripe 事件、重复 Session、RLS 和权限。

新测试项目的预期结果：

- 余额与流水不一致查询返回 0 行；
- 歧义释放查询返回 0 行；
- `purchase_rows` 和 `stripeevent_rows` 均为 `0`；
- 重复 `stripe_checkout_session_id` 查询返回 0 行；
- `purchase`、`stripeevent` 的 `rowsecurity` 为 `true`；
- `anon`、`authenticated` 没有支付表权限。

### 1.4 验证表、RLS 和权限

在测试项目 SQL Editor 中运行以下查询。影响：只读验证，不改数据。

```sql
select tablename, rowsecurity
from pg_tables
where schemaname = 'public'
  and tablename in (
    'purchase', 'stripeevent', 'packcreditlot', 'packcreditallocation',
    'paymentrefund', 'paymentcheckoutrate', 'paymentoperationaudit'
  )
order by tablename;
```

预期返回 7 行，且 `rowsecurity` 全部为 `true`。

```sql
select grantee, table_name, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name in (
    'purchase', 'stripeevent', 'packcreditlot', 'packcreditallocation',
    'paymentrefund', 'paymentcheckoutrate', 'paymentoperationaudit'
  )
  and grantee in ('PUBLIC', 'anon', 'authenticated')
order by table_name, grantee, privilege_type;
```

预期返回 0 行。

```sql
select
  has_function_privilege('authenticated',
    'public.process_stripe_purchase_event(text,text,text,text,uuid,text,integer,integer,integer,integer,integer,text)',
    'execute') as authenticated_can_process,
  has_function_privilege('authenticated',
    'public.grant_manual_pack_topup(uuid,text,integer,integer,text,uuid,text)',
    'execute') as authenticated_can_topup;
```

预期两列都为 `false`。若任一检查不符，停止搭建，不进入 Render 或 Stripe 步骤。

## 2. 新建独立 Render 测试服务

### 2.1 创建后端测试服务

1. 在 Render 创建新的 **Web Service**，名称加入 `payment-test`。
2. 连接本仓库。
3. Branch 选择 `codex/payment-data-model`。
4. 关闭 Pull Request Preview；不要选择 `main`。
5. Root Directory 填 `backend`。
6. Build Command 填：`pip install -r requirements.txt`。
7. Start Command 填：`uvicorn app.main:app --host 0.0.0.0 --port $PORT`。
8. 暂时关闭 Auto-Deploy，等环境变量配置完成后再手工部署。

影响：创建独立测试后端，不改变生产 Render 服务。

### 2.2 配置后端环境变量

在测试 Render 服务的 **Environment** 页面配置以下名称。敏感值只从相应服务的测试项目复制到 Render，不经过聊天：

- `DEPLOYMENT_MODE`（测试服务使用 `online`）
- `DATABASE_URL`
- `FRONTEND_ORIGIN`
- `SUPABASE_URL`
- `SUPABASE_JWT_ISSUER`
- `SUPABASE_JWT_AUDIENCE`
- `SUPABASE_SERVICE_ROLE_KEY`
- `ADMIN_USER_IDS`
- `STRIPE_SECRET_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_MODE`（必须为 `test`）
- `STRIPE_API_VERSION`（记录为 `2026-09-30.endive`；当前代码也固定使用该版本）
- `STRIPE_GST_ENABLED`（首轮测试使用 `false`）

`DATABASE_URL` 必须来自新建的 Supabase 测试项目。优先使用 Supabase 提供的测试项目连接串，并保留 SSL 要求。不要使用生产连接串。

如果还要在浏览器登录测试前端，另建独立 Render Static Site/Web Service，同样部署功能分支，并配置：

- `NEXT_PUBLIC_API_BASE_URL`
- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
- `NEXT_PUBLIC_BETA_SUPPORT_CONTACT`

影响：环境变量只作用于新测试服务。保存后手工部署一次，并用浏览器访问测试后端 `/health`；预期 HTTP 200。

## 3. 配置 Stripe 测试模式

### 3.1 创建受限密钥

1. 登录 Stripe Dashboard，确认顶部显示 **Test mode**。
2. 打开 **Developers → API keys → Create restricted key**。
3. 仅授予下列最小权限：
   - Checkout Sessions：Write（创建）和 Read（查询）；
   - Events：Read；
   - PaymentIntents：Read；
   - Charges：Read；
   - Refunds：Read；
   - Disputes：Read。
4. 其他资源保持 None；本阶段不要授予 Refunds Write。
5. 将测试受限密钥直接保存到测试 Render 的 `STRIPE_SECRET_KEY`。

影响：密钥只能操作 Stripe 测试数据，且权限限制在支付创建和只读核对。

### 3.2 创建 Webhook 端点

1. 在 Stripe 测试模式打开 **Developers → Webhooks / Workbench → Create endpoint**。
2. Endpoint URL 填测试后端：`https://<测试服务域名>/payments/stripe/webhook`。
3. API version 固定选择 `2026-09-30.endive`。
4. 订阅：
   - `checkout.session.completed`
   - `checkout.session.async_payment_succeeded`
   - `checkout.session.async_payment_failed`
   - `checkout.session.expired`
   - `refund.created`
   - `refund.updated`
   - `charge.refunded`
   - `charge.dispute.created`
   - `charge.dispute.updated`
   - `charge.dispute.closed`
5. 创建后显示的 Signing secret 直接保存到测试 Render 的 `STRIPE_WEBHOOK_SECRET`，然后重新部署测试服务。

影响：Stripe 只把测试模式事件发送到独立测试服务。Webhook 签名密钥只放在 Render Secret Environment Variable 和密码管理器，不放 Supabase、不放前端、不写仓库。

## 4. 准备测试用户和创建 Checkout Session

1. 在 Supabase 测试项目 **Authentication → Users** 创建测试用户。
2. 在测试前端登录该用户。
3. 本阶段没有购买页面；在浏览器开发者工具的 Network 面板中，从任一已登录 API 请求复制 `Authorization: Bearer ...` 的测试 Token。Token 只保留在本机临时工具中。
4. 在 Postman 或 Insomnia 新建请求：
   - Method：POST
   - URL：`https://<测试服务域名>/payments/checkout-sessions`
   - Header `Authorization`：测试用户 Bearer Token
   - Header `Idempotency-Key`：每次新结账使用一个新的随机值
   - JSON Body：`{"package_code":"single"}`
5. 发送后复制响应中的 `checkout_url` 到浏览器打开。

影响：在 Stripe 测试模式创建一个 Checkout Session，并在测试库创建一条 `purchase.pending`；不会真实扣款。

## 5. 四种完整流程

每一步都只使用 Stripe 官方测试卡。有效期可填未来任意月份，CVC 和邮编可填 Stripe 允许的测试值。

### 5.1 成功付款

1. 按第 4 节创建新的 Session。
2. 在 Checkout 使用成功卡号 `4242 4242 4242 4242` 完成付款。
3. 在 Stripe Dashboard 确认 Payment 状态成功、Webhook 投递返回 200。
4. 在 Supabase 测试项目 SQL Editor 运行下方“核对查询”。

预期：订单 `paid`；成功事件 `processed`；`packcreditledger` 新增一条 `grant_stripe_purchase`；账号余额增加对应积分；重复发送同一事件不会重复增加。

### 5.2 取消付款

1. 创建新的 Session。
2. 在 Checkout 点击返回/取消，不输入卡号。
3. 回到测试前端或取消页。

预期：不会产生积分流水；订单暂时保持 `pending`，直到 Session 到期事件把它改为 `cancelled`。

### 5.3 付款失败

1. 创建新的 Session。
2. 使用 Stripe 通用拒付测试卡 `4000 0000 0000 0002`。
3. 确认 Checkout 显示付款失败，不要改用真实卡。

预期：没有 `grant_stripe_purchase`；订单不会变成 `paid`。卡支付失败通常不会产生异步成功事件，订单可继续重试，最终到期后转为 `cancelled`。

### 5.4 会话过期

1. 创建新的 Session，记下 `cs_test_...` Session ID。
2. 等待配置的 30 分钟到期；或在安装并登录 Stripe CLI 的本机 PowerShell 运行：

```powershell
stripe checkout sessions expire <cs_test_测试SessionID>
```

运行位置：本机 PowerShell。影响：只立即终止指定的 Stripe 测试 Session，使 Stripe 发送 `checkout.session.expired`；不要填生产 Session ID。

预期：Webhook 返回 200；测试库订单由 `pending` 变为 `cancelled`；事件状态为 `processed`；没有积分发放。

### 5.5 每步使用的核对查询

在 Supabase 测试项目 SQL Editor 运行。影响：只读，只查看最近测试记录。

```sql
select id, stripe_checkout_session_id, status, package_code, credits,
       subtotal_cents, gst_cents, total_paid_cents, livemode,
       expires_at, refund_detected_at, dispute_status, created_at
from public.purchase
order by created_at desc
limit 20;

select stripe_event_id, event_type, status, failure_reason_code,
       stripe_object_id, livemode, attempt_count, created_at
from public.stripeevent
order by created_at desc
limit 20;

select id, user_id, entry_type, credits_delta, package_code,
       amount_cents, idempotency_key, purchase_id, created_at
from public.packcreditledger
where entry_type = 'grant_stripe_purchase'
order by created_at desc
limit 20;

select user_id, balance, updated_at
from public.packcreditaccount
order by updated_at desc
limit 20;
```

所有测试记录都应为 `livemode=false`。发现 `true` 时立即停止联调并撤销测试密钥。

## 6. 管理员 CLI 演练

在本机仓库根目录 PowerShell 临时设置变量。影响：变量只存在于当前 PowerShell 进程；不要截图或复制其值到聊天。

```powershell
$env:PAYMENT_ADMIN_BASE_URL = 'https://<测试服务域名>'
$env:PAYMENT_ADMIN_TOKEN = '<测试管理员登录Token>'
```

`ADMIN_USER_IDS` 必须包含该测试管理员的 Supabase User ID。

### 6.1 查看失败与待处理事件

```powershell
python backend/scripts/payment_admin.py failed
```

影响：只读调用管理员接口。预期列出 `failed` 和 `observed_pending`，不显示 Token 或完整 Webhook 正文。

### 6.2 运行本地对账

```powershell
python backend/scripts/payment_admin.py reconcile
```

影响：只读查询测试数据库。预期正常成功订单不出现在 `paid_without_grant`；异常 ID 会列在对应数组中。

### 6.3 演练 Session 补发

选择一笔 Stripe 已成功、但本地尚未发积分的测试 Session；不要对正常订单反复制造异常。运行：

```powershell
python backend/scripts/payment_admin.py replay-session cs_test_测试SessionID
```

影响：从 Stripe 测试模式重新读取该 Session，通过 2a 幂等函数尝试补发，并写管理员审计。预期事件身份为 `admin_replay:cs_test_测试SessionID`、类型为 `admin.replay`；再次运行余额不再增加。

按 Event ID 演练：

```powershell
python backend/scripts/payment_admin.py replay-event evt_测试事件ID
```

影响：从 Stripe 测试模式重新读取 Event 并尝试补发。Event 超出 Stripe 保留期时应停止，改用 Session ID 核对，不得伪造 Event。

演练结束后清除本机 Token：

```powershell
Remove-Item Env:PAYMENT_ADMIN_TOKEN
Remove-Item Env:PAYMENT_ADMIN_BASE_URL
```

影响：只清除当前 PowerShell 的临时变量。

## 7. 联调结束清理清单

按顺序完成：

1. Stripe 测试模式删除 Webhook Endpoint；影响：停止向测试 Render 投递事件。
2. Stripe 测试模式撤销受限密钥；影响：测试 Render 不能再创建或读取 Stripe 测试对象。
3. Render 删除 `STRIPE_SECRET_KEY`、`STRIPE_WEBHOOK_SECRET`、数据库连接和 Supabase Secret；影响：测试服务失去外部访问能力。
4. 停止或删除独立 Render 测试服务；影响：测试 URL 下线，不影响生产。
5. 导出需要保留的测试证据后，在 Supabase Dashboard 删除整个明确标记为 `payment-test` 的项目；影响：永久删除该测试项目的用户、订单、事件和积分数据。删除前再次核对 Project Ref，绝不能选择生产项目。
6. 删除本机临时 Token、Stripe CLI 临时登录和下载的日志；保留不含密钥的测试结果。
7. 确认 Stripe Dashboard 仍处于测试模式，且没有遗留指向测试 Render 的端点或有效受限密钥。

清理完成后，不要把测试配置复制到生产。生产 migration、Render 变更、PR、合并或推送 `main` 仍需负责人另行明确批准。
