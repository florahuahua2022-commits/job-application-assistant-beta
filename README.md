# 求职助手（Job Application Assistant）

这是一个本地优先的全栈求职助手 MVP。它把一份真实的 Master Resume 与职位描述（JD）结合，生成岗位定制简历、求职信和 WA Government Selection Criteria 草稿；同时记录申请进度，并为最终由用户确认的浏览器投递预留接口。

## 当前可用范围

- Master Resume：直接上传 DOCX、PDF 或 TXT，也可粘贴编辑；固定个人资料中的联系方式优先于旧简历联系方式
- 固定个人资料库：在本地保存姓名、联系方式、工作权、可入职时间和最多两位推荐人，供不同类型岗位重复使用
- JD：粘贴招聘链接后自动读取公司、职位名称和 JD；网站限制读取时，可一次粘贴完整广告并自动拆分字段
- 混合广告检查：识别重复的 About the Role / Employer Questions，并提醒是否混入之前保存岗位的公司内容
- 申请记录：公司、岗位、链接、状态、截止日期、平台确认编号和提交时间
- 生成接口：为简历、Cover Letter 和 Selection Criteria 提供统一接口；另以确定性检查验证实际导出的 Resume 工件是否适合 ATS 读取
- 文档导出：单份 DOCX/PDF，以及完整申请材料包
- 上传前质量检查：自动检查材料完整性、职位名称、联系方式、占位符、未经确认的到岗表述和求职信长度风险；电话号码匹配忽略空格、括号、连字符及 `0`/`+61` 格式差异
- 编辑保存反馈：明确显示 Unsaved changes、Saving、Saved 或 Save failed
- 生成前确认：再次核对职位、组织、电话和邮箱后才能生成材料
- 公司关系检查：若 Cover Letter 未提及广告中的组织，提示核对招聘方、客户和实际雇主之间的关系
- 澳洲本地化质量：生成时要求自然的 Australian English，并在 Final Check 中提醒美式拼写和常见 AI 套话
- 语言变体预留：当前固定为 `Australian English`，通过 `TARGET_ENGLISH_VARIANT` 配置；未来可直接连接地区语言下拉菜单，无需重写 AI 生成逻辑
- 材料按需生成：普通岗位只生成 CV 与 Cover Letter；只有岗位明确提供 Selection Criteria 时才生成并要求该文件
- Selection Criteria 质量：要求每项使用真实、自然的 STAR 证据，并提醒结果或影响不清晰的案例
- 证据可追溯生成：Cover Letter 与 Selection Criteria 只使用按 JD 相关度筛选的简历证据，并在编辑界面显示实际使用的证据 ID
- Selection Criteria 免费额度：在线新用户默认可生成 2 次；每成功邀请 1 位新用户并由对方认领邀请码，邀请人增加 1 次；只有生成成功才扣除额度
- 提交记录：点击 **Review & Apply** 后自动进入 Ready；可复制链接到任意浏览器。完成外部申请后，点击一次 **Mark as Applied** 直接保存日期；确认编号只在岗位详情中选填，不再弹窗
- 申请记录管理：集中统计和筛选 Draft、Ready、Applied；所有保存及已投岗位都可在列表中查看
- 本地备份与恢复：备份个人资料、Master Resume、岗位记录、状态和生成材料；支持下载及确认后恢复
- 半自动投递接口：只准备浏览器投递任务；**不会绕过验证码或在没有你确认的情况下提交**

## 真实环境验证

项目已于 2026-08-02 在 WA Government Jobs（BigRedSky）完成一次真实端到端申请验证：从岗位 JD、DeepSeek 材料生成、人工校验、表单协助和附件上传，到用户本人最终提交并取得确认编号。

详细记录见 `docs/END_TO_END_VALIDATION_2026-08-02.md`。

## 实施路线图

1. **基础工程（已完成）**：Next.js 前端、FastAPI 后端、SQLite 数据模型、REST API 契约。
2. **材料管理（部分完成）**：Master Resume 文件解析与编辑、AI 草稿编辑、明确保存反馈、DOCX/PDF 导出；后续补充版本历史。
3. **AI 生成（已完成）**：支持 OpenAI、DeepSeek 和自动回退；以“只允许改写真实经历、不得编造”为系统约束。
4. **ATS 分析**：提取 JD 关键词，显示已覆盖、缺失、证据不足的关键词；不把“分数”伪装成招聘方真实 ATS 分数。
5. **投递助手（核心流程已验证）**：协助打开申请页、预填信息和上传材料；用户审阅后亲自点击最终提交。验证码、两步验证、敏感声明和最终提交由用户处理。
6. **质量与安全**：添加测试、文档渲染检查、加密/备份策略、删除与数据导出功能。

## 本地启动

### Windows 简单方式

首次使用：

1. 双击 `install-app.cmd`，等待依赖安装完成。
2. 安装结束后会打开 `backend/.env`，填入 OpenAI 或 DeepSeek API Key 并保存。
3. 双击 `start-app.cmd`，浏览器会打开 `http://localhost:3000`。

以后使用只需双击 `start-app.cmd`。如果启动失败，双击 `check-app.cmd` 查看缺少的环境、配置或服务。

`install-app.cmd` 不会把 API Key 写入代码；`.env` 已被 Git 忽略。

### 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

复制 `.env.example` 为 `.env`，然后配置 AI 提供商：

- OpenAI：设置 `AI_PROVIDER=openai` 和 `OPENAI_API_KEY`。
- DeepSeek：设置 `AI_PROVIDER=deepseek` 和 `DEEPSEEK_API_KEY`。
- 自动回退：保持 `AI_PROVIDER=openai`、设置 `AI_FALLBACK_TO_DEEPSEEK=true`，并同时填写两个 Key。当 OpenAI 调用失败时，后端会尝试 DeepSeek。

两个平台的 API 额度彼此独立。未配置可用的 API Key 时，应用仍可管理简历与职位记录，并会清楚提示未配置。

### 前端

```powershell
cd frontend
pnpm install
pnpm dev
```

打开 `http://localhost:3000`。后端 API 地址默认是 `http://localhost:8000`，可用 `NEXT_PUBLIC_API_BASE_URL` 修改。

## 项目结构

```text
backend/     FastAPI、SQLite、OpenAI 与 Playwright 集成点
frontend/    Next.js 用户界面
docs/        真实申请验证记录和产品文档
start-app.cmd Windows 一键启动入口
```

## 安全边界

- 不把 API Key 提交到代码仓库；仅放入 `backend/.env`。
- 上传的简历与个人信息仅存储在你选择的本地/部署环境中。
- 个人资料库不保存密码、OTP、验证码、犯罪记录、纪律处分、健康或多元化声明；这些问题每次申请时仍由用户本人确认。
- 备份文件不包含 `.env`、API Key 或密码。恢复前系统会自动为当前数据创建安全备份，并要求用户明确确认。
- 生成内容是草稿，必须由你核对真实性与准确性。
- 发布路径为：Diagnose → Generate → Review → Final Check → Pack Review → 验证所选 Resume 工件（DOCX/PDF + 模板）→ Ready to Apply → Applied。
- **Review & Apply** 使用统一 Release Checklist；任一阻断项未解决时不会打开申请页面。警告仍会显示，但不会被误当成阻断项。
- DOCX/PDF 草稿仍可下载用于人工检查；只有明确选择且通过 ATS 工件验证的 Resume 格式与模板组合才属于已验证提交工件。
- 自动化投递必须遵守招聘平台规则；该项目不规避 CAPTCHA、登录保护或最终确认步骤。
