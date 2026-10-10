# 2b 后积分路径生产兼容性审计

本审计只基于仓库代码和 migration，不访问生产数据库。生产对象清单仍须由负责人运行统一只读预检确认。

## SQL 函数与触发器

| 对象 | 读写对象 | 2b 后结论 |
|---|---|---|
| `reserve_pack_credits` | 账号、流水、批次、分摊、使用记录、月度计数 | 正确：账号级 advisory lock；先按 FIFO 扣批次并保留账号账面余额；可用余额由批次预留扣除。 |
| `complete_pack_credits` | 分摊、账号、使用记录、月度计数 | 正确：预留转完成后才扣账号账面余额，满足“账号余额 = 批次剩余 + 进行中预留”。 |
| `release_pack_credits` | 批次、分摊、使用记录、流水、月度计数 | 正确：只释放 `reserved`；已退款批次会拒绝释放，避免把积分还回已退款批次；重复调用返回 `false`。 |
| `get_available_pack_credits` | 账号、进行中分摊 | 正确：返回账号余额减进行中预留，旧应用在 migration 与新代码短暂共存时不会多读可用余额。 |
| `grant_pack_credits`、`grant_manual_pack_topup` | 账号、流水 | 正确：沿用账号锁和幂等键；流水触发器同步创建批次。 |
| `process_stripe_purchase_event` | 订单、事件、充值函数 | 正确：只从 `pending` 转 `paid`，充值与事件成功同事务。 |
| `create_pack_credit_lot_from_grant` 触发器 | 流水 → 批次 | 正确：仅三类正向 grant 建批次，`source_ledger_id` 唯一保证幂等。 |

较早 migration 中同名函数会被 `2026100902_credit_lots_refunds.sql` 的 `create or replace` 覆盖；生产必须先完成 0902，再启动包含新应用代码的版本。

## “线上旧代码 + 新数据库”部署窗口

部署顺序固定为先 migration、后推送 `main`。两者之间仍运行旧后端，兼容性如下：

- 旧 PostgreSQL 后端查询余额时已经调用同名 `get_available_pack_credits`；migration 用 `create or replace` 将其切换为“账号余额减进行中批次预留”，所以旧代码不会把已预留积分再次提供给生成。
- 旧后端的预留、完成、释放仍调用同名数据库函数。0902 原位替换函数签名，不要求旧进程重启；新的批次、分摊、月度计数语义立即生效。
- 旧人工充值和 2a Stripe 发放仍写 `packcreditledger`；0902 的流水触发器为迁移后新增 grant 自动建批次，迁移事务中的历史回填覆盖迁移前 grant，因此窗口内不会漏批次。
- 旧 ORM 对新增列不赋值时由数据库默认值处理；新增表不会被生产 `create_all` 创建或覆盖。旧代码继续读取 `packcreditaccount.balance` 时，该值仍是账面余额；只有可用余额入口必须走上述函数。
- 风险边界：migration 成功后不得回滚到不调用这些数据库函数的更老应用版本；若必须回滚应用，只能回滚到本审计确认的 2a 兼容版本。窗口期间保持 `MONTHLY_PACK_LIMIT_GLOBAL=0`，直到迁移后预检、权限自检和恒等式检查通过。

## Python 读写路径

| 路径 | 位置 | 2b 后结论 |
|---|---|---|
| 查询余额 | `pack_credits.pack_credit_balance` | PostgreSQL 调用 `get_available_pack_credits`，不会直接把进行中预留当可用余额。 |
| 预留/完成/释放 | `reserve_pack_credits`、`complete_pack_credits`、`release_pack_credits` | PostgreSQL 全部调用数据库函数；SQLite 分支仅供既有本地单元测试。 |
| 过期预留 | `expire_pack_reservations` | 找出到期 `reserved` 使用记录，逐笔调用数据库 `release_pack_credits`；账号锁、状态条件和流水幂等键使崩溃后重跑安全。 |
| 生成入口异常释放 | `main.py` 生成流程 | 生成开始前先清理过期预留；生成异常时调用同一释放函数，不存在第二套余额算法。 |
| 支付/人工充值 | `process_stripe_purchase_event`、`grant_manual_topup` | PostgreSQL 统一落流水；2b 触发器把 2a 期间及之后的 grant 建为批次。 |
| 账号导出/删除 | `main.py` | 导出只读账号与流水；有订单、退款或管理员支付审计时，限制型外键阻止直接删除。保留并脱敏方案待会计确认期限后实施。 |
| 启动兼容 DDL | `database.create_db_and_tables` | 2b 后所需列已存在，因此不改余额/批次语义；上线顺序仍必须是 migration 在前、main 在后。运行时 DDL 的移除按既定要求另做提交。 |

## PostgreSQL 16 证据

`PostgreSQLCreditLotTests` 覆盖：FIFO 扣减与精确释放、活动预留的可用余额、模拟进程崩溃后由新连接释放过期预留，以及每次操作后的恒等式：

`packcreditaccount.balance = SUM(packcreditlot.remaining_credits) + SUM(reserved allocations.credits)`

这些测试只在 GitHub Actions 的 PostgreSQL 16 服务容器运行，不以 SQLite 替代。
