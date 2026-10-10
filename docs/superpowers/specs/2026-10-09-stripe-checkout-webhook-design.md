# Stripe Checkout Session 与 Webhook 接口设计

## 1. 目标与边界

本阶段只设计两个后端接口，不实施、不接真实支付、不要求任何 Stripe 密钥：

- 登录用户创建 Stripe Checkout Session；
- Stripe Webhook 验签、分类并将支付成功事件交给 2a 的原子处理函数。

价格、币种、积分数和用户身份均由服务端决定。客户端不能提交金额、币种、积分数、Stripe Price ID 或 `user_id`。本阶段不设计前端购买页面、退款执行、拒付处置或生产部署。

## 2. 方案选择

采用一个独立的 `payments.py` 支付适配模块，FastAPI 路由只处理 HTTP 输入输出，积分与订单事务继续由 `pack_credits.process_stripe_purchase_event` 管理。这样可避免把 Stripe SDK、签名验证和事件字段解析塞进已经较大的 `main.py`，同时不复制 2a 的幂等与失败事务逻辑。

不采用以下方案：

- 不让前端传金额或 Price ID，避免价格篡改和客户端版本漂移；
- 不在 Webhook 路由中直接写订单、事件或积分表，避免产生第二套事务语义；
- 不把 Stripe 网络调用放进数据库事务，避免长事务和网络超时占锁；
- 不保存完整 Stripe Webhook JSON；只保存事件 ID、类型、事实指纹和必要的 Stripe 对象标识，减少支付数据与个人信息留存。

## 3. 服务端套餐目录

`package_code` 的唯一权威来源仍为服务端 `PACK_CATALOG`。可购买套餐仅为 `single`、`starter`、`job_search`；管理员使用的 `custom` 明确禁止进入 Checkout。

每个套餐至少包含：

- `credits`；
- `subtotal_cents`（未税金额）；
- `gst_cents`；
- `total_cents`；
- `currency = AUD`；
- `single_pack_price_cents`；
- `catalog_version`。

首版目录标价（未另加 GST）为：`single = 1695`、`starter = 10995`、`job_search = 19900`。`STRIPE_GST_ENABLED` 默认关闭；关闭时 `gst_cents=0`、`total_cents=subtotal_cents`。打开时只对整单计算一次 `gst_cents = subtotal_cents * 10%`，使用十进制 `ROUND_HALF_UP` 四舍五入到整数分，再令 `total_cents=subtotal_cents+gst_cents`，不得逐积分或逐行舍入。按当前标价，打开后总价分别为 `1865`、`12095`、`21890`。

订单保存 `gst_enabled` 快照。所有订单的 `single_pack_price_cents` 均保存购买时、按同一开关状态计算的 `single` 含税总价：关闭为 `1695`，打开为 `1865`；它不是订单总价除以积分数。

Checkout 使用服务端金额创建 `line_items.price_data`，不接收客户端 Stripe Price ID。订单保存上述快照，后续目录改价不影响历史订单。金额全部使用整数分；Webhook 必须核对 Stripe 的 `amount_total`、`currency` 和服务端订单快照一致。

## 4. 创建 Checkout Session 接口

### 4.1 HTTP 合约

`POST /payments/checkout-sessions`

请求体：

```json
{"package_code":"starter"}
```

请求头：

- Supabase Bearer Token，沿用 `get_current_user`；
- `Idempotency-Key`，由客户端为一次“开始结账”动作生成，不得包含用户信息。

成功响应：

```json
{"checkout_session_id":"cs_test_...","checkout_url":"https://checkout.stripe.com/..."}
```

### 4.2 处理流程

1. 将认证结果扩展为只读 `AuthenticatedUser(id, email)`；`user_id` 和 `customer_email` 均来自已验证的 Supabase 登录声明。未登录返回 `401`。请求体即使出现 `user_id`、email、金额、币种、积分数或 Price ID，也因严格请求模型而返回 `422`。登录声明没有 email 时省略 Stripe 的 `customer_email`，不接受客户端补填。
2. 用 `package_code` 查询服务端目录；未知套餐和 `custom` 返回 `400`。
3. 对每个用户执行滑动窗口限频：10 分钟最多创建 5 个 Checkout Session。超过限制返回 `429` 和 `Retry-After`，不调用 Stripe。重复同一幂等键不重复计数。
4. success/cancel URL 使用服务端配置的固定前端来源和固定路径，不接受任意回跳 URL。
5. 调用 Stripe Checkout Session Create，固定 `mode=payment`、`payment_method_types=['card']`，使用服务端 `price_data`，预填登录声明中的 `customer_email`，并把 `expires_at` 固定为创建后 30 分钟。Stripe metadata 写入 `user_id`、`package_code`、`credits`、`catalog_version`；`client_reference_id` 同样使用 `user_id`，但 Webhook 不仅凭 metadata 授权。
6. Stripe 请求的幂等键为服务端命名空间、登录用户和请求 `Idempotency-Key` 的哈希；日志只记录哈希前缀，不记录原值。数据库保存该哈希及对应 `package_code`：同一用户和幂等键重复同一套餐返回原 Session，搭配不同套餐返回 `409`，不得调用 Stripe。
7. Stripe 返回后，以 Session ID 创建或核对 `purchase` 的 `pending` 订单快照，并保存 `expires_at`、`livemode` 和幂等键哈希。相同 Session 的事实不一致时返回冲突并报警。
8. 返回 Session ID 和 Stripe 托管的 Checkout URL。Secret、PaymentIntent client secret 和完整 Stripe 对象不得返回。

Stripe 创建调用本身失败时必须释放刚占用的限频名额，但保留幂等键事实，使同一请求可安全重试。Stripe 创建成功但本地订单写入失败时不释放名额并返回 `500`；相同幂等键重试会从 Stripe 获得同一个 Session，再次尝试本地落单，不会创建第二笔付款会话或重复计数。

## 5. Webhook 接口

### 5.1 HTTP 合约与签名

`POST /payments/stripe/webhook`

该接口不使用登录会话。它必须：

1. 在读取正文前检查 `Content-Length`，并在流式读取时再次执行 256 KiB 硬限制；超限返回 `413`，不验签、不写数据库；
2. 读取未经 JSON 解析或重编码的原始请求体；
3. 读取 `Stripe-Signature`；
4. 使用 Stripe 官方签名构造函数和服务端 webhook secret 验签，默认时间容差 300 秒；
5. 缺少签名、签名错误、时间戳过期或正文损坏时返回 `400`，且不得写数据库；
6. 只有验签通过后才解析和分类事件。

服务端未配置 webhook secret 时启动校验失败；不能以“跳过验签”方式继续运行。

### 5.2 现在处理的事件

| Stripe 事件 | 条件 | 当前动作 |
|---|---|---|
| `checkout.session.completed` | `mode=payment` 且 `payment_status=paid` | 校验事实后调用 2a 成功处理包装函数并发积分 |
| `checkout.session.completed` | `payment_status=unpaid` | 记录为已观察事件，订单保持 `pending`，不发积分，等待异步结果 |
| `checkout.session.async_payment_succeeded` | `mode=payment` 且金额/币种/订单事实匹配 | 调用同一个 2a 成功处理包装函数并发积分 |
| `checkout.session.async_payment_failed` | 订单事实匹配 | 记录事件，订单置现有约束允许的 `failed`，不发积分 |
| `checkout.session.expired` | 订单仍为 `pending` | 记录事件，订单置 `cancelled`，保存过期时间，不发积分 |

即时成功和异步成功可能对应不同 Stripe Event ID，但共享同一 Checkout Session。积分流水幂等键继续使用 Checkout Session ID，所以乱序或重复投递最多发放一次。

为了保留真实事件类型，实施时给 2a 的 Python/SQL 包装函数增加必填 `stripe_event_type` 参数；除此之外不改变它的事务语义。成功事件仍由 `process_stripe_purchase_event` 在一个事务中写事件、订单和积分。若该事务失败，Python 包装函数必须先回滚，再调用 `record_stripe_event_failure` 保存 `failed` 和错误摘要，最后重新抛出；Webhook 返回 `500` 让 Stripe 重试。只有 `processed` 才视为重复成功，`failed` 可重处理。

异步支付失败和 Session 过期属于支付业务结果，不是处理异常，因此不能调用 `record_stripe_event_failure`。它们通过一个受信后端专用的“记录已观察事件并推进订单状态”短事务写入。订单自动状态机只允许 `pending -> paid`、`pending -> failed`、`pending -> cancelled`；尤其只有 `pending` 可变成 `failed`。`paid`、`refunded` 或 `cancelled` 不得被异步失败/过期事件降级。晚到且与终态冲突的事件进入确定性冲突路径和人工队列。

### 5.3 现在只记录的事件

以下事件验签后写 `stripeevent` 的事件 ID、真实事件类型、对象标识和事实指纹，状态记为新增的 `observed_pending`，明确表示“已观察、尚待业务处理”，不能借用代表业务完成的 `processed`：

- 退款：`refund.created`、`refund.updated`、`charge.refunded`；
- 拒付：`charge.dispute.created`、`charge.dispute.updated`、`charge.dispute.closed`。

退款事件本阶段不计算退款、不改积分、不推进 `paymentrefund`。`charge.refunded` 必须给匹配订单设置 `refund_detected_at`，作为“Stripe 已检测到退款、待人工核对”的标记。后续任何自助退款入口必须先锁定订单并同时确认 `refund_detected_at is null` 与 `dispute_status = 'none'`，否则拒绝并转人工处理。

拒付事件本阶段不自动扣回积分，但必须更新订单现有的 `dispute_status`：`charge.dispute.created/updated` 置为 `needs_review`，`charge.dispute.closed` 根据 Stripe 结果置为 `won` 或 `lost`；无法匹配订单时仍保存 `observed_pending` 并进入告警列表。存在 `needs_review` 或 `lost` 的订单禁止进入自动退款流程。

`observed_pending` 事件不依赖原始 payload 继续处理。后续人工处理或对账必须凭保存的 Stripe 对象 ID，使用固定 API 版本从 Stripe 重新读取当前对象后再决策，避免用过期快照推进资金状态。

其他已验签但未列出的 Stripe 事件返回 `200`，只写结构化运行日志和指标，不写支付表。这样不会因订阅无关事件形成无限数据增长。

## 6. Webhook 事实校验

在调用 2a 包装函数前必须同时满足：

- Event ID 和事件类型存在；
- Checkout Session ID 存在，`mode=payment`；
- 已有 `purchase` 的 Session ID、用户和套餐快照匹配；若是创建接口写库失败后的恢复路径，可使用验签后的 metadata 建单，但仍须重新用当前目录核对 `package_code`，并核对 Stripe 实收金额和币种；
- metadata 中的 `user_id` 是有效 UUID，并与订单用户一致；不能使用当前 HTTP 用户，因为 Webhook 没有登录会话；
- `currency=AUD`，`amount_total` 等于订单 `total_paid_cents`；
- credits、GST、single 套餐含税单价全部取订单/服务端目录，不信任客户端；
- PaymentIntent ID 存在于成功事件，且与同一订单历史值不冲突。
- Stripe Event 和 Checkout Session 的 `livemode` 一致，并与服务端 `STRIPE_MODE` 一致。订单和事件均保存不可变 `livemode`。任何模式不一致都禁止发放积分。

任何事实冲突都不发积分，并进入下述失败分类。除签名/正文无效外不使用 `400`，避免绕过统一失败审计。

## 7. 失败分类与告警

`stripeevent` 增加可空 `failure_reason_code`，错误摘要继续写 `last_error`。失败必须分成两类：

| 类别 | 示例原因码 | 保存 | HTTP | 后续动作 |
|---|---|---|---|---|
| 暂时性 | `database_unavailable`、`lock_timeout`、`internal_error` | `status=failed`、原因码、错误摘要 | `500` | Stripe 自动重试；下一次可重新进入 processing |
| 确定性冲突 | `event_facts_conflict`、`session_facts_conflict`、`amount_mismatch`、`currency_mismatch`、`user_mismatch`、`package_mismatch`、`livemode_mismatch`、`order_state_conflict` | `status=failed`、原因码、冲突摘要 | `200` | 不依赖 Stripe 重试；进入管理员告警和人工处置 |

确定性冲突返回 `200` 是为了停止无意义的 Stripe 重投，但它绝不等于处理成功；只有 `processed` 表示支付事件完成，`observed_pending` 表示业务待处理。签名失败没有可信 Event ID，因此不写 `stripeevent`，只写安全日志。

最低告警渠道为管理员只读接口 `GET /admin/payments/failed-events`：只允许 `require_admin_user`，默认列出 `failed` 与 `observed_pending`，展示事件 ID、类型、原因码、Session/对象 ID、订单 ID、尝试次数和时间，不返回原始正文或敏感字段。相同信息同时进入现有结构化 operations 日志，供 Render 日志告警；管理员列表是本阶段必须可用的兜底渠道。

## 8. 状态与响应规则

- 验签失败：`400`，无数据库写入；
- 支持事件处理成功或已 `processed`：`200`；
- 已安全记录为 `observed_pending`：`200`；
- 已验签但不在订阅范围：`200`；
- 可重试的数据库/内部错误：2a 包装记录 `failed` 后返回 `500`；
- 确定性冲突：记录 `failed`、原因码并告警后返回 `200`；
- Stripe SDK 暂时不可用只影响 Checkout 创建接口，返回 `502/503`，不能创建本地“已支付”记录；
- 日志不得包含签名、Bearer Token、Webhook secret、完整原始正文或 Checkout URL。

## 9. 对账、补发与人工处置

### 9.1 管理员只读对账

`GET /admin/payments/reconciliation` 只允许管理员，支持按创建时间、事件 ID、Checkout Session ID、订单状态、事件状态和 `livemode` 过滤。报表只读，不在请求中修复数据，至少列出：

- Stripe 成功但本地订单未 `paid`；
- 本地订单 `paid` 但没有唯一的 `grant_stripe_purchase` 流水；
- 有积分流水但事件未 `processed`；
- `failed` 与 `observed_pending` 事件；
- Event/Session/订单 `livemode` 不一致；
- 过期时间已到但订单仍为 `pending`；
- 检测到退款、拒付待处理或 Stripe 当前对象与本地状态不一致。

报表从本地数据库生成；需要核对 Stripe 当前状态时由管理员对单个对象发起显式刷新，不允许列表请求隐式产生大量 Stripe 调用。

### 9.2 按事件或会话补发

`POST /admin/payments/replay` 只允许管理员，请求体必须且只能提供一个 `stripe_event_id` 或 `checkout_session_id`。服务端用固定 API 版本从 Stripe 重新读取当前 Event/Session，重新执行签名后等价的事实校验，再调用 2a 的 `process_stripe_purchase_event` 包装函数；禁止直接插入积分流水或修改余额。

按 Event ID 补发依赖 Stripe 仍能返回该 Event，受 Stripe Event API 保留期限制；取不到时必须转为人工核对，不能伪造原事件。按 Session ID 补发使用本地确定性事件身份 `admin_replay:<checkout_session_id>`，事件类型固定为 `admin.replay`，并把管理员和目标写入独立审计记录。该身份与真实 `evt_...` 分开，重复补发仍由 Session 级积分幂等约束保证只发一次。

补发依赖 2a 的 Checkout Session 级积分幂等键：已成功发放返回现有结果，未成功且事实一致才发放，事实冲突继续进入确定性失败和告警。每次补发保存管理员 ID、目标、前后状态、结果和时间的审计记录。补发只处理支付成功事件，不处理退款或拒付。

### 9.3 人工处理步骤

1. 管理员先在失败事件列表或对账报表定位事件，核对 `livemode`、订单、金额、币种和用户。
2. 对支付成功但本地未发放的事件，使用补发入口；不得手工改余额。
3. 对 `charge.refunded`，从 Stripe 重取 Charge/Refund，核对 `paymentrefund` 和实际手续费，再进入后续退款状态机；本阶段只把事件标记为已人工核对或继续保留 `observed_pending`。
4. 对拒付，从 Stripe 重取 Dispute，记录证据处理结果；`needs_review/lost` 订单保持不可退款。关闭且赢得拒付时可把订单更新为 `won`，但不自动改变积分。
5. 完成处置后写管理员、备注和完成时间；只有有明确处置结论的观察事件才从 `observed_pending` 转为 `processed`。

## 10. 配置与权限

后续实施只增加以下服务端配置名，不在仓库保存值：

- `STRIPE_SECRET_KEY`；
- `STRIPE_WEBHOOK_SECRET`；
- `STRIPE_MODE=test|live`，默认和非生产环境必须为 `test`；
- `STRIPE_GST_ENABLED=false`，默认关闭；改变时只影响新订单快照；
- `STRIPE_API_VERSION=2026-09-30.endive`，代码、Stripe Workbench Webhook Endpoint 和 Stripe CLI 联调必须使用同一版本；
- 固定 Checkout 成功/取消路径可由现有 `FRONTEND_ORIGIN` 拼接。

生产启动必须拒绝测试/生产模式与密钥前缀不一致的组合。前端永远不能读取 secret。Stripe 密钥优先使用受限密钥，最小权限为 Checkout Session 创建/读取，以及 Event、PaymentIntent、Charge、Refund、Dispute 的只读访问；本阶段不需要 Refund 写权限。若 Stripe 控制台的权限名称变化，按这些 API 动作逐项映射并在测试模式验证，不授予全局写权限。数据库仍只允许已确认的生产后端角色执行 2a 支付函数。API 版本固定为 Stripe 官方在 2026-10-09 标示的当前版本 `2026-09-30.endive`；升级必须单独测试并改配置、SDK 和 Workbench Endpoint，不跟随账户默认版本漂移。

## 11. 联调与测试设计

所有自动化测试均使用本地构造的 Stripe 对象、模拟客户端和测试用虚构签名 secret；不访问 Stripe 网络、不触发付款或退款、不需要用户提供任何密钥。

### Checkout Session

- 未登录、非法套餐、`custom`、额外的 `user_id`/金额/Price ID 均被拒绝；
- GST 默认关闭时三个套餐分别为 `1695/10995/19900`、GST 为零且 total 等于 subtotal；打开时按整单半入法得到 `1865/12095/21890`，并验证 `single_pack_price_cents` 分别为 `1695/1865`；
- 服务端向模拟 Stripe 客户端传入正确的 subtotal/GST/total 整数金额、AUD、积分 metadata、固定回跳地址、`payment_method_types=['card']`、30 分钟过期时间和固定 API 版本；
- `user_id` 和 `customer_email` 来自模拟登录会话；无 email 时不预填；
- 相同 `Idempotency-Key` 重试得到同一 Session，Stripe 成功但本地写入失败可安全重试；
- 同一幂等键改用不同套餐返回 `409` 且不调用 Stripe；
- 第 6 个不同 Session 创建请求在 10 分钟窗口内返回 `429`，重复幂等请求不占新额度；
- 模拟 Stripe 超时/错误不会生成已支付订单或积分，并释放本次限频名额；Stripe 已成功而本地落单失败时不释放。

### Webhook

- 原始正文的正确测试签名通过；缺失、错误、过期签名失败且零写入；
- `Content-Length` 已超限和无长度但流式读取超过 256 KiB 都返回 `413` 且零写入；
- `completed/paid` 和 `async_payment_succeeded` 调用 2a 包装函数；重复、乱序和两种成功事件组合只发一次积分；
- `completed/unpaid` 与 `async_payment_failed` 不发积分；只有 `pending` 订单可转 `failed`；
- `checkout.session.expired` 只把 `pending` 订单转为 `cancelled`，不得覆盖 `paid`；
- 强制 2a 处理异常后，业务写入回滚、`stripeevent.failed` 被保存、Webhook 返回 `500`，下一次投递可成功；
- 暂时性错误保存原因码并返回 `500`；确定性冲突保存原因码、出现在管理员列表并返回 `200`；
- `livemode` 与配置不一致时不发积分并进入确定性冲突；
- 退款和拒付事件为 `observed_pending`，不改变余额或批次；`charge.refunded` 设置检测标记，拒付更新 `dispute_status`；
- 未订阅事件返回 `200` 且不写支付表；
- metadata、订单、金额、币种或 PaymentIntent 冲突时不发积分。

### 管理员操作

- 非管理员不能读取失败列表、对账或调用补发；
- 对账端点零数据库写入，并能识别缺流水、缺成功事件、模式错配、过期 pending、退款及拒付标记；
- 按 Event ID 和 Session ID 补发都重新取 Stripe 测试对象并调用 2a 包装函数；Session 补发写 `admin_replay:<session_id>`/`admin.replay`，Event 已超出 Stripe 保留期时明确失败；重复补发不重复发积分；
- 退款/拒付目标不能通过补发入口发积分；
- 补发保存管理员审计，确定性冲突仍不发积分。

PostgreSQL 契约继续使用 GitHub Actions 的 `postgres:16` 服务容器。Stripe SDK 层通过依赖注入的模拟客户端测试。

人工联调只允许 Stripe 测试模式：使用开发者本人本地环境变量和 Stripe CLI 转发到本地 Webhook，CLI 与 Workbench Endpoint 均固定 `2026-09-30.endive`；只使用 Stripe 官方测试支付方式。不得把密钥写入仓库、聊天、测试 fixture、GitHub Actions 或日志，不进入 Render 生产服务，不使用真实卡，不触发真实退款。联调结束后撤销本地临时 secret，并确认所有订单和事件 `livemode=false`。

## 12. 数据与文件边界（供后续计划使用）

数据库需通过新 migration 增加：

- `purchase.livemode boolean not null`、`purchase.expires_at`、`purchase.refund_detected_at`、幂等键哈希及必要索引；
- `stripeevent.livemode boolean not null`、`failure_reason_code`、`stripe_object_id`，并把状态约束扩展为包含 `observed_pending`；
- 管理员补发审计表或等价的不可变审计记录；所有新增支付对象继续启用 RLS、撤销前端角色权限。

预计后续实施涉及：

- 新建 `backend/app/payments.py`：套餐解析、Checkout 创建、Webhook 验签与事件分类；
- 新建 `backend/app/payment_operations.py`：管理员失败列表、只读对账与幂等补发；
- 修改 `backend/app/main.py`：注册两个薄路由；
- 修改 `backend/app/config.py`：只声明配置字段；
- 修改 `backend/app/pack_credits.py` 及对应 migration：让 2a 包装函数保存真实 `stripe_event_type`，并增加记录业务观察事件的短事务；
- 新建 `backend/tests/test_stripe_payments.py`：模拟 Stripe 客户端、签名与事件；
- 新建 `backend/tests/test_payment_operations.py`：告警列表、只读对账、补发审计与权限；
- 延续 `.github/workflows/quality-gates.yml` 的 PostgreSQL 16 作业，不加入部署步骤或生产 secret。

Stripe SDK 依赖、具体版本和实施步骤应在本设计获批后另写实施计划决定。本提交不修改运行时代码、依赖、migration、测试或 CI。
