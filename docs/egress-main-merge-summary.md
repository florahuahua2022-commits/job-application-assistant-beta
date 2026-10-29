# 流量优化分支合入 main：本地验证摘要

合并目标：codex/egress-read-design，原 HEAD 518d21a；合入 origin/main e785b8e（PR #39 合并提交）。使用 merge，不 rebase、不 amend、不强推。未推送、未开 PR、未部署、未访问生产。

## 解决方式

- AGENTS.md 完整采用 main，保留未经批准禁止 amend/force push 和外部唯一权威日志规则。不访问或修改外部日志。
- run_offline.py 双方内容相同；前端、正式 requirements、source_acquisition.py 与 main 一致。
- main.py 自动合并后仅五个查询函数与 main 不同，保留 #1/#4/#5 查询优化及全部日期修复。
- 工作流以 main 为基础，保留 Python 3.13 双依赖、PG16 事故验收、安装版本与 freeze 证据、前端浏览器测试；保留独立最新文档 PG16 RED/GREEN 任务，使用 Python 3.13 和正式 requirements。
- 删除旧 ci-constraints.txt 及安装引用；test_egress_reads.py 的 archived_at 改为 UTC 合成值。
- 触发及 concurrency 前缀逐字与 main 一致：push 只匹配 main，pull_request 保持原样。因此推送本分支本身不会触发该工作流；没有现存 PR 时，需要新建草稿 PR 才会通过 pull_request 触发。本次没有推送或开 PR。

## 实际验证

本地 Python 3.13.2，PostgreSQL 16.15；临时新集群只监听 127.0.0.1:55443。测试启动器不读取 dotenv、清除继承配置并限制数据库目标；PG16 验收执行的迁移仅作用于本次临时库。

| 类别 | 生产现用依赖组 | 旧兼容依赖组 |
| --- | --- | --- |
| 核心依赖 | 0.0.48 / 2.1.6 / 2.14.1 | 0.0.39 / 2.0.52 / 2.13.4 |
| 完整离线后端 | 644 passed，15 PG专属 skipped，198 subtests passed | 644 passed，15 PG专属 skipped，198 subtests passed |
| 其中流量测试 | 14项（部分列8、最新文档6），包含于完整回归 | 同左 |
| 最新文档真实 PG16 | 6 passed | 6 passed |
| 最新文档旧基线 RED | 6项执行、7个预期失败断言、0 errors；脚本按预期成功 | 同左 |
| 事故/积分真实 PG16，UTC | 27 passed，0 skipped | 27 passed，0 skipped |
| 事故/积分真实 PG16，Australia/Perth | 27 passed，0 skipped | 27 passed，0 skipped |

前端 Node 24.22.1：68项单元测试、2个Playwright界面测试均通过，生产构建通过；8秒提示、60秒慢成功、90秒总期限均用假时钟。前端文件未改。

PG16事故清单沿用 main：资料更新、职位新建/编辑、简历替换、投递及响应时区、状态/归档/恢复、结果记录、SC确认、真实服务端积分预留/完结/失败释放/重试幂等。

工作流 YAML 解析通过，两个 Python 安装段 AST 解析通过；无冲突标记；独立只读审查未发现缺陷。本次是本地验证结果，没有声称 GitHub CI 已运行。

## 部分列查询结果使用检查

AST 加逐段消费者检查，3/3通过：parse-ad 的 previous_companies 传入解析器；quality_check 的公司/职位元组用于字符串比较；generation_request_status 的 row 用于解码状态。无结果属性/下标赋值，无将这些部分列结果传入 session.add/add_all/merge/delete。状态接口测试同时断言无INSERT/UPDATE/DELETE。latest_application_documents 的内部排名只用于 SQL JOIN，外层仍返回完整 GeneratedDocument，不把投影行当ORM实体写回。

## 原19处日期列 AST 对照

按阶段一 8d78b6d 的函数和目标列映射核对，不仅统计调用次数：main.py 18/18，source_acquisition.py 1/1。所有19处保留utc_now；两个文件均没有datetime.utcnow写入这些日期列；source_acquisition.py 没有其他datetime用法。

| 合并后位置 | 函数 | 目标列 | 当前表达式 |
| --- | --- | --- | --- |
| backend/app/main.py:481 | rebuild_source_aware_models | `application.updated_at` | `utc_now()` |
| backend/app/main.py:1204 | save_profile | `profile.updated_at` | `utc_now()` |
| backend/app/main.py:1285 | upload_resume | `current.updated_at` | `utc_now()` |
| backend/app/main.py:1654 | upload_application_source | `target.updated_at` | `utc_now()` |
| backend/app/main.py:1849 | update_application | `application.updated_at` | `utc_now()` |
| backend/app/main.py:1847 | update_application | `primary.updated_at` | `utc_now()` |
| backend/app/main.py:1873 | record_application_submission | `application.submitted_at` | `payload.submitted_at or utc_now()` |
| backend/app/main.py:1874 | record_application_submission | `application.updated_at` | `utc_now()` |
| backend/app/main.py:1908 | update_application_status | `application.updated_at` | `utc_now()` |
| backend/app/main.py:1907 | update_application_status | `application.submitted_at` | `utc_now()` |
| backend/app/main.py:1927 | update_application_archive | `application.archived_at` | `utc_now() if payload.action == 'archive' else None` |
| backend/app/main.py:1928 | update_application_archive | `application.updated_at` | `utc_now()` |
| backend/app/main.py:1999 | add_application_outcome_event | `application.updated_at` | `utc_now()` |
| backend/app/main.py:2020 | correct_application_outcome_event | `application.updated_at` | `utc_now()` |
| backend/app/main.py:2039 | remove_application_outcome_event | `application.updated_at` | `utc_now()` |
| backend/app/main.py:2058 | set_application_outcome_exclusion | `application.updated_at` | `utc_now()` |
| backend/app/main.py:2754 | update_generated_document | `application.updated_at` | `utc_now()` |
| backend/app/main.py:3567 | save_selection_confirmations | `application.updated_at` | `utc_now()` |
| backend/app/source_acquisition.py:136 | acquire_sources | `source.updated_at` | `utc_now()` |

## 交付差异

`docs/egress-main-merge-increment.patch` 以 origin/main e785b8e 为基线，排除全部 docs/，不包含旧阶段说明和旧patch。包含6个实际代码/测试/CI文件。历史 docs 保留在分支历史，未覆盖或删除。相对main，AGENTS、日期修复、requirements和前端没有倒退。
