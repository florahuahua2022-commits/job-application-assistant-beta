# 读取优化执行与验收记录

基线：已获取的远端 main `dbce8ca`。只实施获批 #4、#5；#1 未获本次实施授权。全部数据库测试为离线合成夹具，不使用生产凭据或连接。

## #4：公司/职位窄列查询

- 旧 main 的业务文件保持原样时执行新增测试：修正一次过短广告夹具后，得到 **2 failed, 1 passed**。两个失败均为真实 SQLite 结果列仍有完整 23 列，而非预期 company 或 company/position_title；计数器自身通过。
- 实现仅修改 parse-ad 和 quality-check 的 other-applications 查询，SQL 层投影，保留用户范围、当前申请排除、归档记录、重复公司名与检测规则。
- 新测试在每条申请的三个 JSON 各填充 64 KiB/256 KiB 两种大小：parse-ad 实际返回 21 行、1 列；跨申请查询返回 20 行、2 列，增大无关字段后结果字节数不变。其他 owner 不返回。
- 定向回归：**96 passed, 2 skipped, 13 subtests passed**（submission、pack credits、generation requests 和新增读取测试）。
- 完整后端回归：**622 passed, 2 skipped, 182 subtests passed**。跳过项是原有可选 PostgreSQL 测试，不是本次失败；已有弃用警告未在本次修改。
- 运行入口 `python backend/tests/run_offline.py`：导入应用前清除继承凭据、禁用 dotenv、使用内存数据库并拦截外部连接。现有并发夹具仅允许操作临时目录里的 SQLite；可选 PostgreSQL 测试 URL 不继承。Windows asyncio 内部 socketpair 是唯一的连接调用例外，限定调用者为标准库 socketpair 实现，不允许普通 loopback 数据库或外部连接。
- 最初广回归遇到测试设施问题：Windows socketpair 被网络拦截器误挡、受限进程无法清理临时目录；修正为仅允许标准库内部 socketpair，并在具备本地临时文件权限的进程内重跑上述隔离入口后通过。没有放开数据库/外部网络限制。

## #5：生成状态窄列查询

- 测试前以 AST 比较，确认状态 handler 与 `dbce8ca` 旧 main 完全一致。新增状态测试得到 **13 failed, 2 passed**（pytest 将失败子测试单列）：失败点为整实体列仍被读取，以及非 owner 查询仍实际返回一行。没有把别的错误当作 RED。
- 实现只修改 GET status：SQL 只投影 `release_state_json`，WHERE 限定申请与在线 owner；通过结果行存在性区分未找到和空状态。保留任意已有 document_id 的返回契约，包括 failed/running 状态的已保存草稿；不改提交、CAS、worker、轮询间隔或积分。
- 64/256 KiB 两种 padding 下，连续十次新 Session 的 running poll 实际返回 10 行、每行 1 列，字节数不随无关 JSON 增长；非 owner 返回 0 行且不读文档。completed/failed saved draft、无草稿、未知请求、坏 JSON、本地无 user 过滤均有覆盖。只读和不调用积分操作的断言通过。
- 定向回归：**101 passed, 2 skipped, 23 subtests passed**。
- 完整后端回归：**627 passed, 2 skipped, 192 subtests passed**，原有弃用警告 127 条；可选 PostgreSQL 两项仍跳过，没有运行任何 PostgreSQL/生产连接。
- 独立静态复核：未发现 #4/#5 可操作问题；复核者没有执行应用、测试或网络访问。
- 执行完成后停止业务实现。#1 与 PostgreSQL 16 CI 尚未实现/执行；用户补充的要求及支付分支同步说明见 `2026-10-25-egress-read-handoff.md`。
