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
- `total_cents`；
- `currency = AUD`；
- `single_pack_price_cents`；
- `catalog_version`。

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

1. 通过 `get_current_user` 从登录会话取得 `user_id`；未登录返回 `401`。请求体即使出现 `user_id`、金额、币种、积分数或 Price ID，也因严格请求模型而返回 `422`。
2. 用 `package_code` 查询服务端目录；未知套餐和 `custom` 返回 `400`。
3. success/cancel URL 使用服务端配置的固定前端来源和固定路径，不接受任意回跳 URL。
4. 调用 Stripe Checkout Session Create，固定 `mode=payment`，使用服务端 `price_data`。Stripe metadata 写入 `user_id`、`package_code`、`credits`、`catalog_version`；`client_reference_id` 同样使用 `user_id`，但 Webhook 不仅凭 metadata 授权。
5. Stripe 请求的幂等键为服务端命名空间、登录用户和请求 `Idempotency-Key` 的哈希；日志只记录哈希前缀，不记录原值。
6. Stripe 返回后，以 Session ID 创建或核对 `purchase` 的 `pending` 订单快照。相同幂等请求返回同一 Session；相同 Session 的事实不一致时返回冲突并报警。
7. 返回 Session ID 和 Stripe 托管的 Checkout URL。Secret、PaymentIntent client secret 和完整 Stripe 对象不得返回。

Stripe 创建成功但本地订单写入失败时返回 `500`。相同幂等键重试会从 Stripe 获得同一个 Session，再次尝试本地落单，不会创建第二笔付款会话。

## 5. Webhook 接口

### 5.1 HTTP 合约与签名

`POST /payments/stripe/webhook`

该接口不使用登录会话。它必须：

1. 读取未经 JSON 解析或重编码的原始请求体；
2. 读取 `Stripe-Signature`；
3. 使用 Stripe 官方签名构造函数和服务端 webhook secret 验签，默认时间容差 300 秒；
4. 缺少签名、签名错误、时间戳过期或正文损坏时返回 `400`，且不得写数据库；
5. 只有验签通过后才解析和分类事件。

服务端未配置 webhook secret 时启动校验失败；不能以“跳过验签”方式继续运行。

### 5.2 现在处理的事件

| Stripe 事件 | 条件 | 当前动作 |
|---|---|---|
| `checkout.session.completed` | `mode=payment` 且 `payment_status=paid` | 校验事实后调用 2a 成功处理包装函数并发积分 |
| `checkout.session.completed` | `payment_status=unpaid` | 记录为已观察事件，订单保持 `pending`，不发积分，等待异步结果 |
| `checkout.session.async_payment_succeeded` | `mode=payment` 且金额/币种/订单事实匹配 | 调用同一个 2a 成功处理包装函数并发积分 |
| `checkout.session.async_payment_failed` | 订单事实匹配 | 记录事件，订单置现有约束允许的 `failed`，不发积分 |

即时成功和异步成功可能对应不同 Stripe Event ID，但共享同一 Checkout Session。积分流水幂等键继续使用 Checkout Session ID，所以乱序或重复投递最多发放一次。

为了保留真实事件类型，实施时给 2a 的 Python/SQL 包装函数增加必填 `stripe_event_type` 参数；除此之外不改变它的事务语义。成功事件仍由 `process_stripe_purchase_event` 在一个事务中写事件、订单和积分。若该事务失败，Python 包装函数必须先回滚，再调用 `record_stripe_event_failure` 保存 `failed` 和错误摘要，最后重新抛出；Webhook 返回 `500` 让 Stripe 重试。只有 `processed` 才视为重复成功，`failed` 可重处理。

异步支付失败属于支付业务结果，不是处理异常，因此不能调用 `record_stripe_event_failure`。它通过一个受信后端专用的“记录已观察事件并更新订单状态”短事务写为 `processed`，避免 Stripe 对同一失败通知无限重试。

### 5.3 现在只记录的事件

以下事件验签后只写 `stripeevent` 的事件 ID、真实事件类型、对象标识和事实指纹，状态记为 `processed`；不计算退款、不改积分、不自动冻结账号：

- 退款：`refund.created`、`refund.updated`、`charge.refunded`；
- 拒付：`charge.dispute.created`、`charge.dispute.updated`、`charge.dispute.closed`。

记录这些事件是为后续退款/拒付状态机提供可追踪输入，不代表业务已经完成。拒付事件的订单待处理标记和退款事件对 `paymentrefund` 的推进留到后续明确批准的实现。

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

任何事实冲突都不发积分，并进入 2a 的失败记录路径后返回 `500` 和产生安全告警。除签名/正文无效外不使用 `400`，避免绕过统一失败审计。

## 7. 状态与响应规则

- 验签失败：`400`，无数据库写入；
- 支持事件处理成功或已 `processed`：`200`；
- 已验签但不在订阅范围：`200`；
- 可重试的数据库/内部错误：2a 包装记录 `failed` 后返回 `500`；
- Stripe SDK 暂时不可用只影响 Checkout 创建接口，返回 `502/503`，不能创建本地“已支付”记录；
- 日志不得包含签名、Bearer Token、Webhook secret、完整原始正文或 Checkout URL。

## 8. 配置与权限

后续实施只增加以下服务端配置名，不在仓库保存值：

- `STRIPE_SECRET_KEY`；
- `STRIPE_WEBHOOK_SECRET`；
- `STRIPE_MODE=test|live`，默认和非生产环境必须为 `test`；
- 固定 Checkout 成功/取消路径可由现有 `FRONTEND_ORIGIN` 拼接。

生产启动必须拒绝测试/生产模式与密钥前缀不一致的组合。前端永远不能读取 secret。数据库仍只允许已确认的生产后端角色执行 2a 支付函数。

## 9. 测试设计

所有自动化测试均使用本地构造的 Stripe 对象、模拟客户端和测试用虚构签名 secret；不访问 Stripe 网络、不触发付款或退款、不需要用户提供任何密钥。

### Checkout Session

- 未登录、非法套餐、`custom`、额外的 `user_id`/金额/Price ID 均被拒绝；
- 服务端向模拟 Stripe 客户端传入正确的整数金额、AUD、积分 metadata 和固定回跳地址；
- `user_id` 来自模拟登录会话；
- 相同 `Idempotency-Key` 重试得到同一 Session，Stripe 成功但本地写入失败可安全重试；
- 模拟 Stripe 超时/错误不会生成已支付订单或积分。

### Webhook

- 原始正文的正确测试签名通过；缺失、错误、过期签名失败且零写入；
- `completed/paid` 和 `async_payment_succeeded` 调用 2a 包装函数；重复、乱序和两种成功事件组合只发一次积分；
- `completed/unpaid` 与 `async_payment_failed` 不发积分；
- 强制 2a 处理异常后，业务写入回滚、`stripeevent.failed` 被保存、Webhook 返回 `500`，下一次投递可成功；
- 退款和拒付事件只记录，不改变余额、批次、订单退款状态或 `paymentrefund`；
- 未订阅事件返回 `200` 且不写支付表；
- metadata、订单、金额、币种或 PaymentIntent 冲突时不发积分。

PostgreSQL 契约继续使用 GitHub Actions 的 `postgres:16` 服务容器。Stripe SDK 层通过依赖注入的模拟客户端测试；如做人工联调，只允许 Stripe 测试模式与 Stripe CLI 生成的测试事件，不进入生产、不使用真实卡、不提交密钥。

## 10. 文件边界（供后续计划使用）

预计后续实施涉及：

- 新建 `backend/app/payments.py`：套餐解析、Checkout 创建、Webhook 验签与事件分类；
- 修改 `backend/app/main.py`：注册两个薄路由；
- 修改 `backend/app/config.py`：只声明配置字段；
- 修改 `backend/app/pack_credits.py` 及对应 migration：让 2a 包装函数保存真实 `stripe_event_type`，并增加记录业务观察事件的短事务；
- 新建 `backend/tests/test_stripe_payments.py`：模拟 Stripe 客户端、签名与事件；
- 延续 `.github/workflows/quality-gates.yml` 的 PostgreSQL 16 作业，不加入部署步骤或生产 secret。

Stripe SDK 依赖、具体版本和实施步骤应在本设计获批后另写实施计划决定。本提交不修改运行时代码、依赖、migration、测试或 CI。
