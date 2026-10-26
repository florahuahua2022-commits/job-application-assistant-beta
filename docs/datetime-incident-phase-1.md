# 日期时区事故：阶段一 RED 记录

日期：2026-10-25（北京时间）。本阶段到此暂停，阶段二至四尚未实施。

## 隔离与范围

- 分支：`codex/production-datetime-incident`，起点 `origin/main` / `dbce8ca5f7c79fb60d78ba4bc2fecdcfe8e31c5b`。
- 没有改业务代码、生产配置、requirements 或 CI；没有访问生产数据库、日志或控制台；没有推送、合并、amend 或强推。
- 复用已有离线运行器：导入应用前清空继承凭据、禁用 dotenv，阻断外部连接；只允许内存 SQLite 或临时目录内的 SQLite 文件。新测试全部使用内存 SQLite、固定 UTC 合成资料和用户 ID，不执行应用启动或迁移。
- 从已批准的分析提交复制 AGENTS.md，保留生产访问、外部唯一权威日志及禁止 amend/强推的规则。

## 环境与复现

实际运行 Python **3.13.2**（64 位 Windows），临时独立虚拟环境。安装 main 的 `backend/requirements.txt`，同时明确指定：

| 依赖 | 实际安装版本 |
| --- | --- |
| SQLModel | 0.0.48 |
| SQLAlchemy | 2.1.6 |
| Pydantic | 2.14.1 |

这三项与用户提供的生产现用组一致；不声称其他传递依赖与生产完全一致。

以该环境的 Python 执行：

```text
python backend/tests/run_offline.py backend/tests/test_datetime_incident.py -q --tb=short
python backend/tests/run_offline.py backend/tests/test_outcome_learning.py backend/tests/test_submission.py -q --tb=short
```

## 新增断言：6 失败、3 通过

| 断言 | 当前结果 | 证据与用途 |
| --- | --- | --- |
| 已有资料更新成功、持久化并返回 aware 时间 | RED | main.py:1199 写 naive updated_at，1205 commit 触发 SQLModel 绑定错误 |
| naive submitted_at 返回 422、不改投递状态 | RED | 请求未被输入边界拒绝，进入业务后触发日期绑定错误 |
| 输入模型拒绝 naive submitted_at | RED | `Failed: DID NOT RAISE ValidationError`，独立证明缺少输入校验 |
| Z 日期投递成功、返回/读回保持同一时刻 | RED | application.updated_at 仍为 naive，提交失败 |
| offset 日期投递成功、返回/读回保持同一时刻 | RED | 同上 |
| 不传 submitted_at 的标记投递成功 | RED | 服务端默认 submitted_at / updated_at 为 naive |
| 输入模型接受 Z 日期 | PASS | 与 UTC 合成预期为同一时刻 |
| 输入模型接受 offset 日期 | PASS | 与 UTC 合成预期为同一时刻 |
| 前端 markApplied 请求只发送确认编号 | PASS | 固定当前无客户端时间戳的请求形状 |

资料保存故障的实际摘录（参数全部为合成资料）：

```text
sqlmodel/sql/sqltypes.py:34: in process_bind_param
ValueError: Datetime values must have timezone information.
[SQL: UPDATE applicantprofile SET first_name=?, updated_at=? WHERE applicantprofile.id = ?]
```

新增测试未标记 xfail、未吞掉异常、未修改实现来提前转绿。API 异常由 TestClient 向测试传播，确保 RED 保留真实 SQL 绑定堆栈。上述持久化及响应断言会在阶段二修复后继续执行。

## 夹具修订与既有测试

- main 的 `test_outcome_learning.py`：历史文档 created_at 改为固定 `2026-01-01T00:00:00Z`，涉及原清单的三个测试。
- main 的 `test_submission.py`：归档资料 archived_at 改为同一固定 UTC 合成日期，涉及原清单的一个测试。
- 两文件实跑：**13 failed, 71 passed, 12 subtests passed**。失败堆栈没有 setUp 帧，说明原夹具插入故障已消除；剩余业务失败保持 RED，等待阶段二修复。本结果不是完整回归通过声明。
- 提交前合并复跑上述三个测试文件：**19 failed, 74 passed, 12 subtests passed**（37.13 秒），与两次分开执行的结果一致。`git diff --check` 及业务/依赖/CI/前端相对 main 无差异检查通过。
- 原七项中的另三项属于流量优化分支 `test_egress_reads.py` 共用归档夹具。main 没有该文件；遵守本轮“不碰流量优化分支”，本阶段不复制或改动它。恢复该 PR 时再把相同的 aware 合成值同步过去，并重跑其测试。

## 前端检索

检索整个 frontend 中的 `submitted_at`：仅类型声明及投递日期显示使用该字段，没有发送该字段的代码。`markApplied` 只发送 `submission_reference`。本阶段没有修改前端；实际标记投递 API 已加入无客户端时间戳的 RED 回归断言。

## 后续阶段的边界

1. 阶段二：修复 19 处列写入、输入边界和读回兼容；Python 3.13 下通过生产组与旧组同一份完整回归，完成隔离 PostgreSQL 16 的业务与积分清单。此阶段尚未开始，不声称通过旧组或 PostgreSQL。
2. 阶段三：仅资料主表单/申请内编辑/识别联系信息保存，以及新建/编辑职位，复用一个小响应处理函数；错误持续显示在按钮旁，保留输入、可重试，不展示服务端细节。界面测试只覆盖资料保存和职位保存。其他保存入口不改。
3. 发布方式仅核对仓库配置与公开文档，不访问生产控制台；确认前端与新旧后端兼容后，前后端按同次发布处理，无需刻意分阶段部署。
4. 阶段四：生产依赖固定当前组，CI 用矩阵参数化同一份测试，删除独立 CI 约束。每阶段单独追加提交并暂停审阅。
5. 独立后续事项：为全部后端依赖设计完整锁定方案；本次不实施。

阶段一差异文件包含本阶段新增文件和测试夹具修订，不含差异文件自身及既有流量优化 patch。
