# #163 的 40 项失败：夹具与真实业务写入分类

> 2026-10-25 更新：用户提供北京时间 17:34:13 的生产日志，确认 main.py:1199 save_profile 写 ApplicantProfile.updated_at 失败。该点已由“潜在”升级为用户日志已确认；本次未重新访问生产，#163 的 7/33 分类仍按当次日志第一处失败保留。后续修复设计见 `production-datetime-incident-design.md`。

日期：2026-10-25。独立分析分支：`codex/dependency-stability-analysis`，基于 `origin/main` 的 `dbce8ca`；未引入流量优化实现。本次仅分析既有日志和源码，未重跑应用、测试、数据库或生产请求。

## 1. 证据及分类口径

- GitHub Actions #163：提交 `e7bf7e20e630b136838e8453c7059132f71e82f1`，失败步骤 `Run full isolated SQLite regression suite`。
- 结果：40 failed、593 passed、2 skipped、197 subtests passed。PostgreSQL 16 窗口查询的旧代码 RED 和新代码 GREEN 两步并非这次 job 失败的步骤。
- 40 个失败块都包含 `Datetime values must have timezone information`。代表性的业务堆栈在 `sqlalchemy/sql/type_api.py` 调用 `sqlmodel/sql/sqltypes.py:34 process_bind_param` 后抛出 ValueError，再包装为 StatementError。
- 这是 Python/ORM 参数绑定层的校验，不是 SQLite SQL 语法错误。该位置支持 PostgreSQL 路径也有兼容风险的判断；本次没有执行 PostgreSQL 业务写入，不能把可能性写成生产实测。
- 每项按**日志中的第一处失败写入**分类：A=只在测试显式造数/夹具写入时失败，B=请求进入真实业务代码后写入失败。不是按测试名称分类，也不把一项测试的后续未执行功能全部判为故障。
- 安全的逐项原始元数据在 `ci-163-failure-evidence.json`：仅测试名、类别及日志中的文件/行号/函数。未保存原始日志、SQL 参数值、连接信息或用户资料。

| 类别 | 项数 | 能说明什么 |
|---|---:|---|
| A：测试夹具显式无时区日期 | 7 | 夹具不符合新类型要求，不能直接证明对应生产功能失败 |
| B：真实业务 DateTime 写入 | 33 | 同类业务写入存在兼容风险，不能仅靠修夹具消除 |
| 合计 | 40 | 不重复计数；本次没有“模型默认值无时区”这一额外类别 |

## 2. A 类：7 项的三个夹具来源

| 失败组 | 项数 | 来源（#163 提交行号） | 实际字段/原因 |
|---|---:|---|---|
| IdentityReadTests 全部三项 | 3 | `backend/tests/test_egress_reads.py:28`；`:31` setUp commit | `JobApplication.archived_at=datetime(2026, 1, 1)`。实际查询及探针断言尚未执行 |
| OutcomeEndpointTests 三项 | 3 | `backend/tests/test_outcome_learning.py:131–132` | 旧文档 `GeneratedDocument.created_at=datetime.utcnow()-timedelta(days=1)`，setUp commit 先失败，未执行 outcome endpoint |
| 永久删除已归档申请测试 | 1 | `backend/tests/test_submission.py:357–359` | 测试先写 `JobApplication.archived_at=datetime.utcnow()` 并 commit；尚未发送永久删除请求 |

这些夹具日后应改成带时区的合成值，但**本次不修改测试**。日志参数中同时出现的 created_at 并不等于该默认值也是 naive；源码中的相关模型默认值实际使用 UTC-aware 工厂。

## 3. B 类：33 项首先在哪条业务链路失败

下表的写入源行号均对应当前 main/生产源码 `dbce8ca`；日志 commit 的对应堆栈行号另在 JSON 中，避免把提交间行号偏移当作不同问题。

| 首次失败业务链路 | 项数 | 主要写入源 | #163 显示的实际影响 |
|---|---:|---|---|
| 新建申请 → source-aware 模型重建 → commit | 15 | `main.py:476`，`JobApplication.updated_at` | 初始 insert 可先成功，后续重建写 updated_at 时失败；测试未继续到 JDF、上传、生成等目标步骤 |
| 编辑职位/申请 | 6 | `main.py:1841`，`JobApplication.updated_at`；`:1839` 同时可能写 JobSource | 保存职位资料失败 |
| 上传并替换已有 Master Resume | 2 | `main.py:1280`，`Resume.updated_at` | 替换/重新上传路径失败；不能泛化成所有首次上传都失败 |
| 上传来源附件 → source-aware 重建 | 2 | `main.py:476` 的申请 updated_at；`:1646` 的 JobSource.updated_at 也为 naive | 该次日志先报 UPDATE jobapplication，不能把第二个潜在源当作已执行到的第二次失败 |
| 记录已投递/提交信息 | 2 | `main.py:1865–1866`，`submitted_at/updated_at` | 查询触发 autoflush 时即可失败，并非一定等到函数末尾 commit |
| 更新申请状态 | 2 | `main.py:1899–1900`，条件写 submitted_at、总是写 updated_at | 1 项 applied 路径、1 项 ready/release 流程；不等于 ATS 算法本身失败 |
| 归档/恢复申请 | 3 | `main.py:1919–1920`，`archived_at/updated_at` | 归档写两字段；恢复虽清空 archived_at，仍会写 naive updated_at |
| 保存编辑后的生成文档/SC 内容 | 1 | `main.py:2759`，`JobApplication.updated_at` | 为 SC 更新确认状态时写申请日期失败，不是模型生成 API 调用失败 |

33 项业务失败均可追到上述显式赋值，不能靠只改 7 项夹具解决。

## 4. 全量静态写入清单：19 处 DateTime 列赋值

行号为 `dbce8ca`。表中“已见”指 40 项日志里观察到了相关链路；“潜在”是源码发现，不声称已经在生产或该次 CI 执行到。

| 文件:行号 | 函数 | DateTime 列 | 用户功能 / 证据级别 |
|---|---|---|---|
| `backend/app/main.py:476` | rebuild_source_aware_models | JobApplication.updated_at | 新建职位、来源重建；已见 |
| `backend/app/main.py:1199` | save_profile | ApplicantProfile.updated_at | 保存已有个人资料；潜在。首次创建走 aware 默认值 |
| `backend/app/main.py:1280` | upload_resume | Resume.updated_at | 替换已有简历；已见 |
| `backend/app/main.py:1646` | upload_application_source | JobSource.updated_at | 手工补充附件；潜在第二写入源，该次先被申请 updated_at 阻断 |
| `backend/app/main.py:1839` | update_application | JobSource.updated_at | 更新主招聘来源；潜在第二写入源 |
| `backend/app/main.py:1841` | update_application | JobApplication.updated_at | 保存职位资料；已见 |
| `backend/app/main.py:1865` | record_application_submission | JobApplication.submitted_at | 未提供 submitted_at 时默认 utcnow；已见。若客户端提供 naive datetime，也需另做输入边界评估 |
| `backend/app/main.py:1866` | record_application_submission | JobApplication.updated_at | 保存投递信息；已见 |
| `backend/app/main.py:1899` | update_application_status | JobApplication.submitted_at | 首次标为 applied；已见 |
| `backend/app/main.py:1900` | update_application_status | JobApplication.updated_at | 状态更新；已见 |
| `backend/app/main.py:1919` | update_application_archive | JobApplication.archived_at | 归档；已见 |
| `backend/app/main.py:1920` | update_application_archive | JobApplication.updated_at | 归档/恢复；已见 |
| `backend/app/main.py:1991` | add_application_outcome_event | JobApplication.updated_at | 添加申请结果/面试进展；潜在，相关 CI 测试先被夹具阻断 |
| `backend/app/main.py:2012` | correct_application_outcome_event | JobApplication.updated_at | 更正进展；潜在 |
| `backend/app/main.py:2031` | remove_application_outcome_event | JobApplication.updated_at | 删除进展；潜在 |
| `backend/app/main.py:2050` | set_application_outcome_exclusion | JobApplication.updated_at | 排除学习信号；潜在 |
| `backend/app/main.py:2759` | update_generated_document | JobApplication.updated_at | SC 编辑导致确认失效；已见 |
| `backend/app/main.py:3569` | save_selection_confirmations | JobApplication.updated_at | 保存 SC 确认；潜在 |
| `backend/app/source_acquisition.py:136` | acquire_sources | JobSource.updated_at | 自动获取来源的 finally 分支；潜在，创建申请可能先失败 |

上述赋值均调用 `datetime.utcnow()`，产生无 tzinfo 的 datetime。模型默认值则不同：`models.py:15–16` 的 `utc_now()` 返回 `datetime.now(timezone.utc)`；Resume、ApplicantProfile、JobApplication、JobSource、GeneratedDocument、GenerationUsage、PackCreditAccount/Ledger、GlobalMonthlyUsage 等默认时间均引用该 aware 工厂。**未发现这些模型默认工厂本身写 naive 日期。**

另有 7 处 `utcnow` 调用不直接绑定 DateTime 列：main.py 的 `:554` diagnosed_at 字符串、`:573/:578` 提交快照/事件辅助时间、`:901` 导出元数据、`:1160` export JSON 字符串、`:1782` answered_at 字符串、`:3178` running started_at 字符串。不能仅按关键词扫描把它们全部算入同一 ORM 绑定异常。

## 5. 对生产功能的判断

先前经批准读取的当前 Live 构建记录为 2026-10-22 的 `dbce8ca`，安装 SQLModel 0.0.48 / SQLAlchemy 2.1.6 / Pydantic 2.14.1，与 #163 失败组合相同。

- **保存已有个人资料、替换简历、创建/编辑职位、补来源、标记投递、归档/恢复、结果记录、部分 SC 编辑/确认：现在就有可能触发兼容失败。** 有的链路有 CI 直接证据，有的只有源码风险；不能宣称生产已经逐条复现。
- **生成：** `main.py:3206` 的 generation_started_at 明确为 aware，`:3421` 用它写 GeneratedDocument.created_at；不是全部生成时间都错误。新建职位、来源重建或资料修改失败会阻断前置流程，SC 确认/后续编辑也可失败；#163 不证明 CV/CL 模型请求本身失败。
- **积分预留/完结/释放：** `pack_credits.py:97,136,160,186,235` 使用 aware 时间，模型默认也 aware；PostgreSQL 路径通过服务端 reserve/complete/release 函数处理。未找到与这 40 项相同的直接 naive 写入证据。主流程可在预留之前失败；若预留后生成抛错，`finish_generation_request()` 代码会 rollback 并尝试释放，是否完整恢复不能靠静态分析保证。不能把间接未完结、异常释放风险写成“积分函数已确认坏了”。
- 原有两项可选 PostgreSQL 积分集成测试当时跳过；窗口查询的六项 PG16 测试不等于积分端到端验证。后续依赖固定的验收应补隔离 PG16 的真实积分路径，不访问生产。

当前结论是兼容风险，不是生产故障统计；本次未调用任何业务接口或数据库。

## 6. 更早 Render 成功构建对比

本次授权事件 `render-build-versions-20261025-02` 已先记入外部权威日志，读取结束后追加结果并释放锁。只选了一次早于 10 月 22 日的成功构建：`1dc9dcb`，部署 `dep-db0gorc9v7es73bcfgs0`，北京时间 **2026-10-03 22:08:45**。

Build logs 显示 `No logs to show` 和 `The time range is outside your log retention period.`。因此三包当时的版本均**无法核实**；不根据历史日期、requirements 范围或当前版本补猜。最终完整 AX 视图实际返回 136 行、7,177 UTF-8 字节，属于界面文本计量，不是数据库行数/网络字节。数据库访问为 0。未读取其他旧构建、未升级套餐或改日志流。

## 7. 逐项索引

下表由安全证据 JSON 生成；类别以第一处失败写入为准。

| # | 类别 | 测试（#163 原名） | 第一处失败 |
|---:|:---:|---|---|
| 1 | B | `backend/tests/test_application_decision_api.py::ApplicationDecisionApiTests::test_job_change_invalidates_decision` | `backend/app/main.py:1851 update_application` |
| 2 | B | `backend/tests/test_application_decision_api.py::ApplicationDecisionApiTests::test_missing_jdf_keeps_diagnosis_incomplete_without_gating_generation` | `backend/app/main.py:1467 create_application` |
| 3 | A | `backend/tests/test_egress_reads.py::IdentityReadTests::test_cross_application_reads_only_identity_preserving_leak_detection` | `backend/tests/test_egress_reads.py:31 setUp` |
| 4 | A | `backend/tests/test_egress_reads.py::IdentityReadTests::test_parse_ad_reads_only_company_preserving_scope_duplicates_and_archive` | `backend/tests/test_egress_reads.py:31 setUp` |
| 5 | A | `backend/tests/test_egress_reads.py::IdentityReadTests::test_probe_counts_actual_fetches` | `backend/tests/test_egress_reads.py:31 setUp` |
| 6 | B | `backend/tests/test_job_sources.py::JobSourceTests::test_acquisition_endpoint_persists_validated_source_state` | `backend/app/main.py:1467 create_application` |
| 7 | B | `backend/tests/test_job_sources.py::JobSourceTests::test_attached_jdf_creates_unresolved_source_without_fabricated_text` | `backend/app/main.py:1467 create_application` |
| 8 | B | `backend/tests/test_job_sources.py::JobSourceTests::test_duplicate_fragments_are_deduplicated_and_unresolved_jdf_is_reconciled` | `backend/app/main.py:1467 create_application` |
| 9 | B | `backend/tests/test_job_sources.py::JobSourceTests::test_unrelated_application_update_preserves_sources` | `backend/app/main.py:1467 create_application` |
| 10 | B | `backend/tests/test_manual_source_upload.py::ManualSourceUploadTests::test_backend_rejects_invalid_type_and_mismatched_target` | `backend/app/main.py:1467 create_application` |
| 11 | B | `backend/tests/test_manual_source_upload.py::ManualSourceUploadTests::test_duplicate_sha_is_persisted_without_duplicate_extracted_text` | `backend/app/main.py:1659 upload_application_source` |
| 12 | B | `backend/tests/test_manual_source_upload.py::ManualSourceUploadTests::test_manual_docx_and_txt_uploads_create_extracted_sources` | `backend/app/main.py:1659 upload_application_source` |
| 13 | B | `backend/tests/test_manual_source_upload.py::ManualSourceUploadTests::test_manual_pdf_fulfils_unresolved_jdf_without_losing_provenance_or_binary` | `backend/app/main.py:1467 create_application` |
| 14 | B | `backend/tests/test_manual_source_upload.py::ManualSourceUploadTests::test_manual_upload_enforces_ownership` | `backend/app/main.py:1467 create_application` |
| 15 | B | `backend/tests/test_manual_source_upload.py::ManualSourceUploadTests::test_oversized_mismatched_and_corrupt_uploads_are_rejected` | `backend/app/main.py:1467 create_application` |
| 16 | B | `backend/tests/test_manual_source_upload.py::ManualSourceUploadTests::test_partial_upload_is_truthfully_persisted` | `backend/app/main.py:1467 create_application` |
| 17 | A | `backend/tests/test_outcome_learning.py::OutcomeEndpointTests::test_cross_user_access_is_rejected` | `backend/tests/test_outcome_learning.py:132 setUp` |
| 18 | A | `backend/tests/test_outcome_learning.py::OutcomeEndpointTests::test_record_correct_remove_unknown_and_exclude_without_ai` | `backend/tests/test_outcome_learning.py:132 setUp` |
| 19 | A | `backend/tests/test_outcome_learning.py::OutcomeEndpointTests::test_submission_snapshot_is_immutable_and_later_document_gets_no_credit` | `backend/tests/test_outcome_learning.py:132 setUp` |
| 20 | B | `backend/tests/test_real_user_regression.py::RealUserRegressionTests::test_job_edit_updates_primary_source_and_makes_old_documents_historical` | `backend/app/main.py:1851 update_application` |
| 21 | B | `backend/tests/test_real_user_regression.py::RealUserRegressionTests::test_production_shaped_historical_resume_requires_complete_reupload` | `backend/app/main.py:1298 upload_resume` |
| 22 | B | `backend/tests/test_real_user_regression.py::RealUserRegressionTests::test_upload_unresolved_then_exclude_allows_update_and_generation` | `backend/app/main.py:1298 upload_resume` |
| 23 | B | `backend/tests/test_release_gate.py::ReleaseGateTests::test_failed_ats_blocks_and_clean_release_can_be_marked_ready` | `backend/app/main.py:1912 update_application_status` |
| 24 | B | `backend/tests/test_source_aware_parsing.py::SourceAwareIntegrationTests::test_automatic_acquisition_rebuilds_from_extracted_source` | `backend/app/main.py:1467 create_application` |
| 25 | B | `backend/tests/test_source_aware_parsing.py::SourceAwareIntegrationTests::test_creation_persists_unresolved_attached_jdf_warning` | `backend/app/main.py:1467 create_application` |
| 26 | B | `backend/tests/test_source_aware_parsing.py::SourceAwareIntegrationTests::test_manual_upload_rebuilds_models_and_invalidates_stale_derivatives` | `backend/app/main.py:1467 create_application` |
| 27 | B | `backend/tests/test_source_aware_parsing.py::SourceAwareIntegrationTests::test_replacing_source_with_changed_text_rebuilds_and_preserves_source_id` | `backend/app/main.py:1467 create_application` |
| 28 | B | `backend/tests/test_source_aware_parsing.py::SourceAwareIntegrationTests::test_same_extracted_text_does_not_invalidate_derivatives` | `backend/app/main.py:1467 create_application` |
| 29 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_accepts_blank_optional_confirmation_reference` | `backend/app/main.py:1876 record_application_submission` |
| 30 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_applied_status_records_date_without_confirmation_reference` | `backend/app/main.py:1910 update_application_status` |
| 31 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_archive_preserves_workflow_status` | `backend/app/main.py:1930 update_application_archive` |
| 32 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_archives_own_application` | `backend/app/main.py:1930 update_application_archive` |
| 33 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_confirmed_requirements_survive_unrelated_and_full_form_unchanged_updates` | `backend/app/main.py:1851 update_application` |
| 34 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_editing_selection_criteria_clears_saved_confirmations_and_blocks_submission` | `backend/app/main.py:2756 update_generated_document` |
| 35 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_material_job_description_or_selection_criteria_change_requires_confirmation` | `backend/app/main.py:1851 update_application` |
| 36 | A | `backend/tests/test_submission.py::SubmissionRecordTests::test_permanently_deletes_archived_application_after_title_confirmation` | `backend/tests/test_submission.py:359 test_permanently_deletes_archived_application_after_title_confirmation` |
| 37 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_records_confirmation_and_marks_application_applied` | `backend/app/main.py:1876 record_application_submission` |
| 38 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_restores_own_application` | `backend/app/main.py:1930 update_application_archive` |
| 39 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_updates_saved_job_details_without_losing_jd` | `backend/app/main.py:1851 update_application` |
| 40 | B | `backend/tests/test_submission.py::SubmissionRecordTests::test_user_overridden_requirements_survive_unrelated_update` | `backend/app/main.py:1851 update_application` |
