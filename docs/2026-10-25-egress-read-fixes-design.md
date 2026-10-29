# 三项数据库读取优化：设计稿，不实施

日期：2026-10-25。分支：`codex/egress-read-design`。

## 范围与基线

来源调查按用户决定结束：接受“经连接池反复读取大字段”的机制结论，操作者无法确认；本次不继续归因或调查历史访问。

只设计此前报告 #1、#4 中用户指定的跨申请检查/parse-ad、#5。没有业务代码、迁移、测试代码或依赖改动；不启动应用、不执行应用测试、不连接生产、不合并 main。失败测试在下文定义，**尚未编写或运行，不声称已得到 RED/GREEN**。

本次只向 GitHub fetch main，确认远端最新 `origin/main` 为 `dbce8ca5f7c79fb60d78ba4bc2fecdcfe8e31c5b`（2026-10-06 18:06:21 +08:00），从该提交创建独立工作树和分支。机器上的本地 `main` 另停在 `379482d`，不是这次 fetch 得到的远端 main；未移动、合并或重置该本地分支。本稿“main”均指已核对的远端 main 快照；不承诺它等于生产部署版本。

此前报告的基线是支付分支 `21ebc79`；此次不 cherry-pick、不合并支付分支，不修改它的文件。其文档提交 `e2a2c37` 不属于本次分支。

## 1. main 是否同样存在，行号差异

文件均为 `backend/app/main.py`；比较的是旧报告代码基线 `21ebc79` 与本次 main `dbce8ca`，不是文档自身行号。

| 位置 / 问题 | 旧报告基线行号 | main 行号 | 差异 / 结论 |
|---|---:|---:|---|
| `latest_application_documents()` | 192–201 | 114–123 | −78；仍 `.all()` 读取全部历史，Python 按类型挑最新 |
| `current_required_documents()` | 303–322 | 225–244 | −78；仍先 latest，再按 required/fingerprint 筛选 |
| `quality_check()` | 2181–2646 | 2103–2568 | −78；仍有独立全历史查询，随后再调用 current helper |
| `pack_review()` | 2735–2803 | 2657–2725 | −78；质量检查后仍独立读取全部历史 |
| `release_checklist()` | 2670–2731 | 2592–2653 | −78；current helper + quality_check 的嵌套关系相同 |
| parse-ad 的 `previous_companies` 查询 | 1613 | 1535 | −78；仍读全部 JobApplication，实际只取 company |
| 质量检查的 `other_applications` 查询 | 2402–2404 | 2324–2326 | −78；仍读完整其他申请，循环只使用 company/title |
| `generation_request_status()` | 3231–3243 | 3153–3165 | −78；仍经完整实体查申请，再提取 release state |
| `get_for_user()` | 887–891 | 809–813 | −78；`session.get(model,id)` 完整实体读取后在 Python 检查 user_id |
| `complete_generation_pack_if_ready()` | 3171–3192 | 3093–3114 | −78；仍经 current helper 校验所需文档，影响 #1 语义 |

三项在 main 均存在。frontend 的 `generationRequest.ts` 仍为每轮请求后等待 5 秒、每文档最多 120 轮，完成后接收 document；本次 #5 只优化后端状态查询，不改间隔、响应协议或 Retry。

## 2. 方案选择与共同测试方法

本稿推荐 SQL 层窄投影/限行，沿用现有 SQLModel、SQLite 测试和请求协议；不引入缓存、状态新表、后台队列或迁移。只改 API 返回 schema 不能减少已经读出的数据库大字段，因此不作为方案。查询形状的选择是本稿建议，等待用户审阅，尚未作为已批准实现。

### 离线、合成、可证明的失败测试

未来实现时采用现有 `unittest` 和 `StaticPool` 的本地内存 SQLite 模式。测试启动器须在**导入任何 app 模块之前**显式设置 `DATABASE_URL=sqlite://`、离线 deployment mode、移除可选 PostgreSQL 测试 URL，并禁止外部 socket 连接；不读取 `.env`。检查配置最终解析到内存库，拦截非 SQLite engine/外部连接，随后才导入 app。不要用 TestClient startup 不经控制地触发 `create_db_and_tables()`。依赖覆盖 `get_session/get_current_user`，模型/AI 只做内存 stub；完整回归也只能在同样隔离条件下进行。

合成字段采用合法 JSON，例如 `json.dumps({"padding": "x" * 65536})`，而非真实简历。每份文档 trace 放 64 KiB padding、正文约 8 KiB；每份申请 snapshot/matches/job_model 等分别放 64 KiB padding。为需要计算指纹的场景保留合法且一致的领域字段，padding 只加到允许的冗余位置。数据在内存中创建，不提交大夹具文件。默认每类型 30 个历史版本（3 类型共 90 行），再扩成每类型 100 行做敏感性断言，无需线上压力测试。

**“读取行数”定义：本次被测 SELECT 实际返回到驱动/应用侧的行数，不是 API 返回条数，不是数据库内部扫描行数。** 不用 SELECT 的 `cursor.rowcount`（通常为 −1），不用 ORM load 事件（identity map 会掩盖重复查询），不用 HTTP JSON 长度代替数据库结果大小。

测试侧在 SQLite DBAPI cursor 外包一层透明计数器，记录 `fetchone/fetchmany/fetchall` 实际交付行数、`cursor.description` 列名和已返回字段的 UTF-8 字节总和；保持原调用/返回语义，不额外 fetch、不重跑查询、不加载业务关系属性。结合 SQLAlchemy 执行时 compiled statement/result metadata 检查顶层投影，并检查子查询投影，不能简单搜索 SQL 字符串里的列名（WHERE 合法使用 user_id，不等于 SELECT 输出 user_id）。计数器只在合成数据写完、commit 完、关闭 seed Session 后启用；被测调用使用新 Session，避免缓存/夹具写入污染。

现有测试不含这种结果边界计数；实现时最多新增一个小型测试工具，放测试目录，仅供这些测试用。测试启动器允许自己导入测试依赖，但不在本次交付编写它。计数器先用一个已知 3 行/2 列查询自检，确认分批 fetch 不重计。SQLite SQL 选择语义实测，PostgreSQL 仅离线 compile 检查方言兼容；不声称 SQLite 验证了生产执行计划或实际 Supabase 流量。

## 3. #1 每种文档类型只取最新一份

### 推荐改动

保持 `latest_application_documents()` 返回 `dict[str, GeneratedDocument]`。用**窄字段窗口子查询**在用户/申请范围内选 `id, document_type, row_number()`，按 document_type 分组，按 `created_at DESC, id DESC` 排序；外层只连接 rank=1 的 id，获取完整最新文档。内层不 SELECT trace/content/plan/reviewer，外层最多返回 K 行，K 是该申请实际存在的类型数。这样保留 helper 对类型的通用性，避免硬编码三类后悄悄丢弃其他既有类型。

同时把 quality-check 和 pack-review 的独立 `.all() + setdefault` 改为调用同一 helper；否则只改共享 helper，会遗漏两个仍在读全历史的调用点。current helper、ATS、导出、submission snapshot、编辑后 review、积分完结原有调用沿用返回契约。history 接口继续完整历史语义，不借机改分页或 UI。

替代方案：每种类型一次 `LIMIT 1`，SQL 更直观，但固定三类会缩小旧 helper 契约，先查询类型又增加往返；PostgreSQL `DISTINCT ON` 简短但与当前 SQLite 测试不通用。因此推荐上述一个窗口查询；不增加依赖或索引迁移。窗口仍可能扫描索引/元数据，但不会将旧文档大字段全部传到客户端；内部扫描数不承诺 O(K)。

同时间戳目前没有稳定排序。本稿建议用最大 id 作为决胜，是**显式补充规则**，并非保证旧实现每次都选同一条；须在审阅中认可这一规则，避免把它藏在“等价优化”里。

### 要设计成失败的测试

| 测试 | 合成场景与断言 | 旧 main 的预计失败点 |
|---|---|---|
| `latest_query_fetches_one_row_per_type` | owner 的同申请每类型 30/100 份，加另一用户/申请和一个未知类型；返回各类型正确最新 id。单次 helper 的数据库结果文档行数恰好 K，扩大 H 不改变 K；内层只选 id/type/rank，外层完整行只限 K 条 | 结果虽只有 K 个对象，驱动实际读取 H 行；不是靠断言 dict 长度就算通过 |
| `latest_query_preserves_owner_and_application_scope` | 外用户和其他申请具有更新时间/更大 id；不得参与排名、返回或大字段读取。缺失类型不合成占位；同时间戳按最大 id | 旧范围断言可能已通过；限行/确定性排序是新增验收点 |
| `quality_and_pack_review_never_fetch_full_history` | 用可通过既有检查的两文档 fixture 加历史 padding；观测各文档 SELECT。quality-check 仍有两次 latest 调用时总返回最多 2K；release-checklist 最多 3K；pack-review 可进入审阅时最多 3K。每次都只返回最新集，禁止任一 H 行查询 | quality-check/pack-review 自己的全历史查询仍返回 H 行 |
| `latest_is_checked_before_current_or_review_status` | 老版本 current/pass、新版本 stale 或 fail/provider_failed；验证不得为过门禁回退老版本。更新 trace 指纹、改 profile/requirements 后 current 判断仍正确 | 此项是语义保护，旧实现应通过；用于防止新 SQL 先过滤 pass/current 再取最新 |

补充验证：history endpoint 仍能取到旧版；CV/CL/SC 可选/必需组合和 legacy 无 fingerprint 的 require_contract 真/假行为与现有 `release_state.document_is_current()` 一致。改变 padding 或 H 不改变业务检查结果。

### 质量、发布、积分风险

- **质量检查**必须继续检查最新坏稿；不能只选通过审阅的记录。最新文档仍需完整 content/reviewer/plan/trace 供现有校验，本项限行，不删这些最新行的列。
- **发布门禁**仍由 current 指纹、required 集合、Reviewer/Pack Review/ATS/导出指纹共同决定。优化不能“从历史找一份能过的”。同时间戳 tie-break 需单列验收。
- **积分完结** main `3093–3114` 只要求本 Pack 各 required 请求完成，并且 current_required_documents 覆盖 required；它并不额外要求 Reviewer pass，也没有把文档 id 强制绑定当前 pack。保持现有条件，不能借优化收紧或放松。加离线测试：CV 完成 CL 未完成不完结；SC required 缺失不完结；新版本 stale 不完结；全部 current 完成后只完结一次；重复调用不重复计账；请求失败的释放路径沿用既有测试。

拟触及文件：`backend/app/main.py`；测试 `test_submission.py`、`test_pack_credits.py`，必要时一个共享离线查询计数工具。不改 models、数据库表、前端、credits 引擎。

## 4. #4 跨申请检查和 parse-ad 的窄列投影

### 推荐改动

- quality-check 的 other_applications SELECT **只选 company、position_title**，WHERE 保留 user_id 和 `id != current_application_id`，在循环解包两列；保持弱公司名/等价公司、职位截断、当前 JD/申请人证据例外以及仅 CL/SC 检查的全部逻辑。
- parse-ad SELECT **只选 company**，不额外取职位，因为现有 parser 只接收公司名列表；仍传当前用户全部公司名，保留重复值和既有 parser 行为，不借此 DISTINCT/过滤归档/分页。
- 不修改共享 `select_for_user()` 的实体返回行为，防止影响其他调用；直接在两个局部查询做列投影与用户过滤。`user_id=None` 的本地模式保留既有不加用户过滤语义，在线路径必须明确过滤。

这里减少的是每行字节数，不是需要比较的其他申请数量。不应为了让“读取行数更小”漏查申请。此前报告 #4 还提到 outcome learning；用户此次指定的三项中将该项限定为跨申请检查和 parse-ad，因此不改 outcome learning，也不改 `/applications` 列表。

### 要设计成失败的测试

| 测试 | 合成场景与断言 | 旧 main 的预计失败点 |
|---|---|---|
| `cross_application_query_selects_identity_only` | 当前 owner 共 N 条申请，另一个 owner 若干；snapshot/matches/JD 各 64 KiB。被测 other-applications SELECT 输出恰好 `(company, position_title)`，实际读取 N−1 行；不存在后续为补属性触发的完整 JobApplication SELECT | 旧 SELECT 输出全部映射列，padding 随行传出 |
| `parse_ad_query_selects_company_only` | 当前 owner N 条公司名含重复和已归档申请，别的 owner 有醒目标记。查询仅 company、实际 N 行；捕获 parser 输入确认内容/重复值不变，不含其他 owner 数据 | 旧查询读取 N 条完整实体；业务结果可能完全相同，但列/字节断言失败 |
| `identity_projection_bytes_ignore_large_padding` | 使用相同公司/职位，将冗余 JSON padding 从 64 KiB 增到 256 KiB；上述专属查询的实际结果列、行数和字段字节数完全不变 | 旧查询字节数随 padding 增长 |

语义保护复用并扩展现有 `test_submission.py:1068–1183` 的跨申请检测用例：公司+职位共同出现仍阻断；仅出现公司不阻断；当前 JD 或 Master Resume 支持的组合不阻断；等价/弱公司名不误报；CV 不触发该检查。主质量检查还会读取**当前**申请完整实体，不能把全接口“任何 JobApplication 查询都不得读大列”作为错误断言；计数只针对明确的 other-applications/parse-ad 查询。

质量风险主要是 tuple 解包错误或范围漏查；发布门禁间接依赖 quality-check 的泄漏错误，必须保持 ready/issues 语义；积分完结不直接调用这两段查询，不能因此更改 required、状态或计账条件。拟触及 `main.py` 两个局部查询和 `test_submission.py`，无 schema/API 协议变化。

## 5. #5 生成状态轮询只读状态字段

### 推荐改动

`generation_request_status()` 的申请查询改为顶层只 SELECT **JobApplication.release_state_json**；在 SQL WHERE 同时限定 application id 和在线 user_id，不为授权检查先调用完整实体 `get_for_user()`。使用有无“结果行”判断存在性，不能用 JSON 字符串真假判断；空值/坏 JSON 仍走 `load_release_state()` 的兼容逻辑。保留本地 `user_id=None` 的现有行为。

从状态 JSON 中提取相同 pack_id/document_type key，返回结构保持一致：running 不读取 GeneratedDocument；任何状态只要有 document_id，就沿用原有文档所属用户检查和完整 document 返回（包括保留失败草稿的情况），不能擅自改成“只有 completed 才返回文档”。不改前端、不改 CAS 状态写入、reserve/complete/release 或后台 worker。本项只针对高频 GET，`save_generation_request()` 的整行读取和 release_state 历史 key 增长另行处理。

替代方案是新建状态小表或 PostgreSQL JSON key 投影，可进一步减小 growing release state，但引入迁移/方言差异；先不做。只投影 release_state_json 不等于读取字节归零，所有旧 request key 仍在该 JSON 内。

### 要设计成失败的测试

| 测试 | 合成场景与断言 | 旧 main 的预计失败点 |
|---|---|---|
| `running_poll_selects_release_state_only` | 单申请其他 JSON 各 64 KiB、状态小且 running；新 Session 调用 GET。申请 SELECT 恰好一列 release_state_json、实际 1 行、0 条文档查询；请求重复 10 次使用新 Session，10 行而不是缓存后假装 1 行 | 旧 get_for_user 每次选择完整实体，所选列和字节断言失败 |
| `poll_bytes_independent_of_unrelated_json` | 状态保持逐字一致，其他 JSON padding 64→256 KiB；每次状态读取的行数/列数/实际结果字节数相同 | 旧整实体读取随 padding 增长 |
| `completed_and_failed_draft_poll_preserve_document_contract` | completed+document_id、failed+document_id、failed 无草稿、running、未知 key/空坏 JSON 分别覆盖；有 document_id 时最多取 1 行相同完整文档，无时 0 行；HTTP 状态/detail/document 字段与旧契约一致 | 业务行为旧版应通过；状态查询列断言失败，防止新实现删除失败草稿响应 |
| `poll_authorization_is_enforced_in_sql` | 非 owner、缺失 application、未知 pack/type：保持现有 404；非 owner 的申请 SELECT 返回 0 行，不读对方完整 release state，不查文档。不把 SQL WHERE 的 user_id 误判为被输出的列 | 旧代码先读取外用户完整申请再判断，非 owner 实际返回 1 行，且全列读取 |

每种状态测试同时断言没有 UPDATE/INSERT/DELETE、没有 reserve/complete/release 调用。轮询优化不能触发 generation、质量检查或积分状态变更。响应已存在的文档可被前端正常恢复，后端文档授权逻辑不绕过。对跨申请 document_id 的额外一致性收紧如需修复，另列任务，不混入本次等价优化。

拟触及 `main.py` 状态 GET 及 `test_generation_requests.py`，不改共享 get_for_user、models、迁移、generationRequest.ts 或积分路径。

## 6. 验收与实施顺序（未实施）

建议未来分别独立提交：#4 窄投影 → #5 状态窄投影 → #1 最新文档限行。前两项改动小，#1 触达质量/发布/积分共享语义，需要更完整的保护；这是实现顺序建议，不改变原报告的预期收益排名。

每项必须先在上述隔离环境验证新查询断言在旧 main 上失败，再改实现，最后同一组断言转绿。语义保护测试原本就应通过，不声称每条新增断言都应 RED。不能用 mock 返回 K 行来证明 SQL 没有读 H 行，不能只断言 API 响应字段。新增测试必须观察真实内存数据库结果与选列，同时核对相同输入下质量 issues、发布 ready 和积分次数。以后扩大历史 H，只允许元数据内部扫描量变化，不允许最新文档输出行数随 H 增长。

本次仅做源码静态定位、Git 基线核对和文档审阅，未运行这些测试、未实现查询。下一步需要用户批准设计后再实施；此次“设计三项修复”不被当作实现授权。

## 7. 生产访问记录的持久规则

已建立 `docs/prod-access-log.md`，访问条目为空，不补写过去。新增根 `AGENTS.md` 仅将用户要求的生产访问规则持久化，指向该日志，便于后续会话在执行前读取；不是业务代码或自动联网功能。

事先取得本次目的/范围的明确批准 → 先落“已授权，尚未执行”事实及同意原话 → 已准备好计量才允许连接 → 执行后立即追加实际表、实际返回行数、基于本次结果的大致字节和北京时间。失败/重试逐次记录；无法记录或无法计量时停止，不以“只读”越过授权。意外中断导致缺失只如实记未记录并披露，不事后额外读库补数字。

这只是当前独立分支内的持久规则，没有合并或复制到其他分支，也不是已经上线的强制技术拦截。在本聊天中同样立即遵守；未来若切到缺少该规则/日志的工作树，必须先带入当前有效记录并核对，不能把文件缺失当作访问豁免。本次没有生产访问，所以不追加任何授权或执行事实。
