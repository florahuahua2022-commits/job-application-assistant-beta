# 日期时区事故：阶段二

阶段二基线为 `8d78b6d`，分支 `codex/production-datetime-incident`。只处理批准的日期写入、输入和读回兼容，以及相应验收基础设施；阶段三、四未开始。

## 进入前的三项核对

1. `test_datetime_incident.py` 不再要求数据库读回对象必须保留 tzinfo。测试把已知 UTC 的历史数据库值统一为 UTC，再与响应中的同一时刻比较；Z/offset 输入也比较同一 UTC 时刻。旧 SQLModel/SQLite 会丢失 tzinfo，这是驱动行为差异，不能误判为业务失败。这个兼容规则不用于客户端输入。
2. `rg -n timedelta backend/tests/test_outcome_learning.py`：无输出、退出码 1。当前导入为 `from datetime import datetime, timezone`，没有残留使用。
3. `run_offline.py` 与流量优化分支内容一致（忽略 Git 换行转换）；本阶段没有改它。事故分支 AGENTS.md 比流量优化分支多出已批准的禁止 amend 已推送提交、禁止强推规则，本阶段同样未改。维护责任由事故分支承担，批准合并后以 main 为准；恢复流量优化 PR 时同步 main 的这两个文件，不把旧规则覆盖回来。本轮没有修改流量优化分支。

## 实现边界

- main.py 的 18 处及 source_acquisition.py 的 1 处列赋值，全部从 `datetime.utcnow()` 改为现有 `utc_now()`。逐项原写法 → 新写法及旧行号见 [19 处对照清单](datetime-incident-19-writes.md)。AST 核对数量为 19，无遗漏。
- 不改用户过滤、权限、事务边界、发布门禁、质量检查、积分算法或状态流转。
- `JobApplicationSubmissionUpdate.submitted_at` 使用 `AwareDatetime | None`：拒绝无时区日期，接受 Z/offset；校验后先转成 UTC 再写入。省略字段或 null 仍由服务端生成投递时间。
- 旧组回归曾证明 offset 输入直接写入 SQLite 会保留墙钟钟点、丢掉 offset，造成时刻偏移；UTC 归一化修复了这个真实的 RED 失败，不是放宽测试。
- `as_utc` 仅用于两处已知 UTC 数据库日期的排序边界：结果快照选取和文档列表。历史 naive 值按旧 UTC 约定解释，aware 值转换为 UTC；不删除已有时区，不将任意客户端 naive 值当作 UTC。
- 另外七处 `datetime.utcnow()` 属于 JSON、导出或辅助日期，不属于本次列赋值范围，保持原样。main.py 的新增行保持 CRLF。
- requirements.txt、前端和生产配置未改；阶段三错误反馈、阶段四生产依赖固定仍待单独批准执行。

## RED → GREEN

补齐读回断言后，在未改实现的生产现用组上：**8 failed, 3 passed**，包含新增的两处混合 naive/aware 排序失败。

修复后新增断言 **11 passed**。旧组完整回归随后暴露 offset 输入丢失时刻的问题（1 failed），增加输入 UTC 归一化后同一断言转绿；没有 xfail 或按版本跳过断言。

## Python 3.13 双依赖组完整回归

本地实际 Python **3.13.2**，两个独立临时虚拟环境；仅三项依赖版本不同，其余均从同一份 backend/requirements.txt 安装。

| 依赖组 | SQLModel / SQLAlchemy / Pydantic | 完整离线回归 |
| --- | --- | --- |
| 生产现用组 | 0.0.48 / 2.1.6 / 2.14.1 | 630 passed，12 skipped，182 subtests passed |
| 旧组 | 0.0.39 / 2.0.52 / 2.13.4 | 630 passed，12 skipped，182 subtests passed |

12 项跳过不是未验收：10 项是新 PG 专属清单，2 项是原有真实 PG 合约；它们在下面的隔离 PG16 运行中全部执行且无跳过。离线运行器继续禁用 dotenv、清空继承凭据、阻断外部连接。

## 隔离 PostgreSQL 16 清单

本地实际 PostgreSQL **16.15**，全新临时集群，仅监听本机独立端口，专用测试库。PG runner 不读取 dotenv 或生产配置，只接受固定本机测试目标；原有 PG 合约通过不含连接信息的 opaque 标记接入，不能传入任意连接字符串。

临时库执行仓库迁移及明确绑定到临时引擎的建表/升级函数，补齐仓库启动升级所提供的 completed_at 前置列；不执行应用 lifespan。生产数据库、控制台、服务配置均未访问。

| 依赖组 | 会话时区 | 结果 |
| --- | --- | --- |
| 生产现用组 | UTC | 24 passed，0 skipped |
| 生产现用组 | Australia/Perth | 24 passed，0 skipped |
| 旧组 | UTC | 24 passed，0 skipped |
| 旧组 | Australia/Perth | 24 passed，0 skipped |

验收包括：

- 更新已有资料，读回同一时刻；新建/编辑职位、上传职位附件、更新来源模型及替换已有简历。
- naive 投递输入 422 且不改状态，Z/offset 输入保持时刻，不传 submitted_at 的标记投递仍有效；状态变更、归档、恢复。
- 结果事件新增、纠正、排除、删除；SC 文档编辑和真实确认存储。
- timestamptz 读回及已知 UTC 的历史 timestamp without time zone 读回；混合历史日期的两处排序。
- 真实 `public.reserve_pack_credits` / `complete_pack_credits` / `release_pack_credits`：预留、重复预留、完结、重复完结、失败释放、重复释放、已关闭 pack 重试幂等；检查余额、usage、ledger 和月度计数。
- 复用现有真实生成积分编排断言，确认完整 CV + Cover Letter 才完结、失败释放、关闭 pack 不可重新扣费。仅合成草稿生成被替换，编排、数据库访问及服务端积分函数均真实执行。
- 原有 PG 自定义充值幂等合约及并发竞争最后一个积分/全局名额合约，均实际执行。

同一批命令由两个依赖环境分别运行：

```text
python backend/tests/run_offline.py -q --tb=short
python backend/tests/run_pg16.py --incident-pg16-port 55439 --incident-timezone UTC backend/tests/test_datetime_incident.py backend/tests/test_datetime_pg16.py backend/tests/test_pack_credits.py::PostgreSQLPackCreditContractTests -q --tb=short
python backend/tests/run_pg16.py --incident-pg16-port 55439 --incident-timezone Australia/Perth backend/tests/test_datetime_incident.py backend/tests/test_datetime_pg16.py backend/tests/test_pack_credits.py::PostgreSQLPackCreditContractTests -q --tb=short
```

## CI 与交付

- 既有 backend job 改为生产/旧组矩阵，运行同一份测试；Python 由 3.12 改为 3.13，与用户提供的生产 Python 次版本一致，不声称补丁版本完全相同。
- 每个矩阵实例使用独立 PG16 服务，两种会话时区都执行；保留并上传离线及 PG16 证据。触发块逐项核对不变：pull_request，push 仅 main。创建草稿 PR 通过既有 pull_request 事件触发 CI。
- backend job 显式使用 bash，确保 GitHub Actions 的 `-o pipefail` 生效，测试输出经 tee 保存时不会吞掉失败退出码。这是验收门禁的一部分，不改变触发条件。
- 依赖固定仍留在阶段四；本阶段未新增 CI 专用约束文件。CI 结果以草稿 PR 的本次提交检查为准，本地结果如上。
- 已完成只读整体审阅和 PG 合约入口增量复核，未发现阻断问题。CRLF 感知差异检查及 YAML 解析通过。
- 差异文件 `datetime-incident-phase-2.patch` 以阶段一为基线，包含本阶段代码、测试、CI、说明及19处对照，不含它自身、阶段一 patch 或既有流量优化 patch。
- 本阶段后暂停；不合并 main，不部署，不改生产配置，不动流量优化和支付分支，不 amend/强推。

后续独立事项仍为：全部后端依赖的完整锁定方案；本次不实施。
