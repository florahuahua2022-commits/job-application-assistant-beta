# 19 处日期列赋值对照

基线：阶段一提交 `8d78b6d`。AST 扫描确认 main.py 18 处、source_acquisition.py 1 处，无遗漏。只替换时钟来源，不改授权、状态判断、提交顺序或事务。

| # | 文件:旧行号 / 函数 | 原写法 → 新写法 |
| --- | --- | --- |
| 1 | `backend/app/main.py:476` / `rebuild_source_aware_models` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 2 | `backend/app/main.py:1199` / `save_profile` | `profile.updated_at = datetime.utcnow()` → `profile.updated_at = utc_now()` |
| 3 | `backend/app/main.py:1280` / `upload_resume` | `current.updated_at = datetime.utcnow()` → `current.updated_at = utc_now()` |
| 4 | `backend/app/main.py:1646` / `upload_application_source` | `target.updated_at = datetime.utcnow()` → `target.updated_at = utc_now()` |
| 5 | `backend/app/main.py:1839` / `update_application` | `primary.updated_at = datetime.utcnow()` → `primary.updated_at = utc_now()` |
| 6 | `backend/app/main.py:1841` / `update_application` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 7 | `backend/app/main.py:1865` / `record_application_submission` | `application.submitted_at = payload.submitted_at or datetime.utcnow()` → `application.submitted_at = payload.submitted_at or utc_now()` |
| 8 | `backend/app/main.py:1866` / `record_application_submission` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 9 | `backend/app/main.py:1899` / `update_application_status` | `application.submitted_at = datetime.utcnow()` → `application.submitted_at = utc_now()` |
| 10 | `backend/app/main.py:1900` / `update_application_status` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 11 | `backend/app/main.py:1919` / `update_application_archive` | `application.archived_at = datetime.utcnow() if payload.action == "archive" else None` → `application.archived_at = utc_now() if payload.action == "archive" else None` |
| 12 | `backend/app/main.py:1920` / `update_application_archive` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 13 | `backend/app/main.py:1991` / `add_application_outcome_event` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 14 | `backend/app/main.py:2012` / `correct_application_outcome_event` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 15 | `backend/app/main.py:2031` / `remove_application_outcome_event` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 16 | `backend/app/main.py:2050` / `set_application_outcome_exclusion` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 17 | `backend/app/main.py:2759` / `update_generated_document` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 18 | `backend/app/main.py:3569` / `save_selection_confirmations` | `application.updated_at = datetime.utcnow()` → `application.updated_at = utc_now()` |
| 19 | `backend/app/source_acquisition.py:136` / `acquire_sources` | `source.updated_at = datetime.utcnow()` → `source.updated_at = utc_now()` |

另外的七处 datetime.utcnow() 用于 JSON、导出或辅助日期，不属于本次列赋值范围，保持原样。
