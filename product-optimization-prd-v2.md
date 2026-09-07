# 产品优化 PRD（第二版）

## 1. 背景与目标

内测反馈显示，用户无法稳定完成「录入职位 → 生成申请材料 → 保存并再次处理」的核心路径。首要目标是让用户在自动提取失败时仍可继续，并让每份申请在重新打开后保持其当时确认的材料与进度。

## 2. 范围

### 本期包含

- 职位文本提取、链接导入的独立失败处理与手动兜底。
- Application 的保存、恢复、简历快照与归档。
- Generate 的唯一硬门槛。
- 申请列表、申请详情、Profile 三个工作区。
- 文档选择、诊断提示与删除/归档交互简化。

### 本期不包含

- 对任意招聘网站的链接抓取承诺；仅支持可公开访问且已验证的来源。
- 自动决定是否生成 Selection Criteria。
- 重新设计生成引擎、PDF 内容质量或申请自动投递。

## 3. 核心对象与状态

| 对象 | 用户可见含义 | 规则 |
|---|---|---|
| Master Resume | Profile 中当前默认简历 | 仅影响以后新建的申请 |
| Application Resume Snapshot | 某份申请使用的简历版本 | 创建申请时生成；更新 Master Resume 不得覆盖 |
| Job Description Snapshot | 某份申请确认的职位描述 | 自动导入、粘贴或手动编辑后保存 |
| Generated Document | 基于某次 Resume/JD Snapshot 的生成结果 | 显示生成时间；材料变更后标为“需要重新生成” |
| Application Status | Draft / Ready / Applied | 表示申请进度 |
| Archive Flag | 已归档或未归档 | 与 Application Status 独立；恢复后保留原进度 |

### 可恢复范围

重新打开 Application 时，必须恢复已保存的职位信息、文档选择、Resume/JD Snapshot、生成文档、申请进度与归档状态。Diagnosis 结果可恢复并显示生成时间；Resume 或 JD 变更后须标为“需重新检查”。进行中的导入或生成任务不恢复为进行中，应显示为未完成并提供重试入口。

## 4. 用户流程

### 4.1 新建并生成申请

1. 用户从 **My Applications** 点击“新建申请”。
2. 用户粘贴 JD、导入受支持链接，或直接手动填写职位信息。
3. 系统尝试提取信息；失败时保留原始输入，说明失败原因，并引导用户手动补充。
4. 用户保存申请；系统从当前 Master Resume 创建 Application Resume Snapshot。
5. 申请详情页展示 Documents 区域、非阻塞 Diagnosis 提示和 Generate。
6. 用户按需选择是否生成 Cover Letter 和 Selection Criteria，点击 Generate。
7. 用户检查、编辑、下载文档，并可将申请标为 Applied。

### 4.2 Generate 门槛

```text
Generate enabled = 已确认可用的职位描述 AND 可用的 Application Resume Snapshot
```

“已确认可用的职位描述”可来自粘贴文本、成功导入或手动填写；自动提取成功、职位名称、Diagnosis 警告、解析警告、Cover Letter、Selection Criteria 与其他可选元数据均不得作为门槛。

| 情况 | 是否阻塞 Generate | 页面行为 |
|---|---:|---|
| 无可用简历 | 是 | 显示上传/选择简历入口 |
| 无可用职位描述 | 是 | 显示补充 JD 入口 |
| 自动提取失败 | 否 | 保留输入，提示手动填写 |
| Diagnosis 发现匹配度低 | 否 | 展示建议卡片 |
| 未选择 Cover Letter 或 Selection Criteria | 否 | 按当前选择生成 |

## 5. 需求清单

### P0-1 职位信息导入可靠性

文本提取与链接导入必须按两条独立路径验证和修复。

- 粘贴 JD 后的提取失败：保留原文本与已填写字段，明确指出未识别的字段，允许手动编辑、保存和 Generate。
- 链接导入失败：保留链接，说明该链接无法导入，并提供“改为粘贴 JD 文本”的下一步；不得静默失败。
- 链接导入仅对公开、可访问且经验证的来源提供支持；不支持时应直接降级为手动流程。
- 所有自动填充字段均允许手动覆盖。

### P0-2 保存、回显与主动清空

- Application 必须恢复第 3 节定义的可恢复状态。
- 编辑既有记录时，用户主动清空非硬必填字段必须可保存。
- 校验按动作与业务规则执行：新建时缺少硬必填信息可阻止保存；编辑时主动清空非硬必填信息不得被当作“未填写”拦截。

### P1-1 简历资料库与快照

- 用户上传或更新 Master Resume 后，新建申请默认使用它。
- 新建申请时创建独立 Application Resume Snapshot。
- Job A 使用 V1 后，更新 Master Resume 为 V2 不得改变 Job A；新建 Job B 使用 V2。
- 已生成文档必须关联其使用的 Resume/JD Snapshot；任一材料变更后标记为“需要重新生成”，不得伪称已更新。

### P1-2 文档选择简化

- Resume 是唯一必需材料，且必须已有可用快照。
- Cover Letter 默认开启生成；用户可关闭。它是“是否生成”的选择，不是 Generate 门槛。
- **Selection Criteria 默认关闭，不自动判断、不自动生成。** 用户可在 Documents 区域手动开启“生成 Selection Criteria”。
- 选择开启后，Selection Criteria 纳入本次生成；关闭时不展示其额外配置。
- 如果 JD 明确提到 Selection Criteria，页面可显示轻提示“该职位可能要求 Selection Criteria”，但不得自动开启，也不得阻塞 Generate。
- **Format（Standalone / Combined）保留为用户手动选择，不做自动判断。** 它与“是否生成”是两个独立决定：仅在某文档已选择生成后展示其 Format；未选择生成时隐藏对应 Format，避免与选择开关并列造成认知负担。
- Format 的选择不得改变 Generate 门槛。若现有格式组合存在适用限制，应在选择时说明并由产品/工程沿用既有业务规则，不得静默改选。

### P1-3 Diagnosis 从关卡改为提示

Diagnosis 保留“材料完整性检查”和“岗位匹配建议”两项能力，但不再要求用户先点击 Diagnose 才能继续。

- 硬缺失（无可用简历或无可用职位描述）由 Generate 门槛处理。
- 匹配度低、经验建议、解析警告等展示为可关闭/可再次查看的提示卡。
- 材料变更后，旧 Diagnosis 标记为“基于旧材料”；用户可重新检查，但不被迫先检查。

### P1-4 信息架构

| 工作区 | 目的 | 核心内容 |
|---|---|---|
| My Applications | 默认首页与恢复入口 | All / Draft / Ready / Applied / Archived 筛选、列表、新建申请 |
| Application Detail | 处理单份申请 | 职位信息、Documents、Diagnosis、Generate、Review、Download、Apply |
| Profile | 全局资料与恢复 | Contact、Master Resume、Backup & Restore |

从列表进入详情、编辑后返回列表时，应保留用户原来的筛选与浏览位置。Application Detail 不是常驻导航 Tab。

### P1-5 创建、编辑、归档与删除

- 新建职位和编辑已保存职位使用同一个表单；编辑时预填已保存数据。
- **Draft 记录提供 Delete 操作**：用于用户录入错误、修改成本过高、决定重新粘贴或新建 JD 的场景。Delete 可在 Application Detail 编辑表单中使用；确认后删除该 Draft 及其关联临时数据，并返回 My Applications，让用户立即“新建申请”。
- Ready / Applied 记录的主操作为 Archive：归档记录从默认活跃列表隐藏，出现在 Archived 筛选中。Archive 必须同时提供于 Application Detail 的编辑表单和 My Applications 列表的每条记录快捷操作；两个入口调用同一归档逻辑。
- Restore 仅移除归档标记，必须恢复原 Application Status。
- Ready / Applied 记录的永久删除（Delete permanently）仅在 Archived 记录中提供；需二次确认，并要求输入职位名称。确认前必须说明将删除关联文档和申请历史；确认后该 Application 及其关联 Snapshot、生成文档和申请历史均不可恢复。

### P1-6 Backup & Restore

Backup & Restore 移至 Profile。功能不变；恢复完成后应明确说明恢复了哪些 Application 与资料库数据。

### P3-1 可理解的校验状态

| 字段状态 | 视觉与文案 |
|---|---|
| 空且硬必填，在提交/失焦后校验 | 红色错误，说明如何修正 |
| 已输入但尚未校验 | 中性普通状态 |
| 已通过对应校验 | 绿色成功状态 |

不得将“非空”直接显示为“已正确”。

### P3-2 Format 与技术信息的渐进展示

- 不再将 Requirement 与 Format 两组下拉并排展示；用户先决定生成某文档，再看到该文档的 Format 选择。
- 默认不展示 parser warnings、内部检测过程和技术调试说明。面向用户的提示只说明结论、影响和下一步；必要时提供“查看详情”链接。Selection Criteria 的说明不得暗示系统已替用户做出决定。

### P3-3 Generate 位置与文档样式

- Generate 按钮置于 Documents 区块末尾，紧跟用户的文档选择与提示；本期不引入动态悬浮逻辑。
- Cover Letter 生成模板的正文采用左对齐。

## 6. 验收与回归测试

| # | 场景 | 预期结果 |
|---|---|---|
| 1 | 粘贴 JD → Extract → Save → 重开 | 已保存字段完整恢复 |
| 2 | 解析失败后手动补充 JD | 可保存且可 Generate |
| 3 | 无法导入的链接 | 明确提示并可改用粘贴 JD，流程不阻断 |
| 4 | Master Resume V1 → Job A → 更新 V2 → Job B | Job A 保留 V1，Job B 使用 V2 |
| 5 | 修改 Resume/JD 后重开申请 | 旧 Diagnosis 与旧文档标记为基于旧材料 |
| 6 | 普通岗位新建申请 | Selection Criteria 默认关闭且不生成 |
| 7 | 手动开启 Selection Criteria 后 Generate | Selection Criteria 被纳入本次生成，不改变按钮门槛 |
| 8 | 开启任一文档生成 | 仅该文档展示可手动选择的 Standalone / Combined Format；关闭后隐藏 |
| 9 | 无简历或无 JD | Generate 不可用，并显示可执行修复入口 |
| 10 | 有简历和 JD，但 Diagnosis 有警告或未选 Cover Letter/SC | Generate 可用 |
| 11 | Draft 录入错误后点击 Delete | 二次确认后删除该 Draft，返回列表并可立即新建申请 |
| 12 | 从详情页和列表页分别 Archive 同一类型记录 | 两个入口结果一致，记录从活跃列表移至 Archived |
| 13 | Applied 记录归档再恢复 | 恢复后仍为 Applied，数据不丢失 |
| 14 | 永久删除确认页取消 | 记录及所有关联数据不变 |
| 15 | 输入正确职位名称后永久删除 | Archived 记录及关联 Snapshot、生成文档、申请历史均不可恢复 |
| 16 | 编辑清空非硬必填字段 | 可保存，重开后仍为空 |

## 7. 执行顺序

1. **数据基线**：确定对象、Snapshot、归档标记与可恢复范围；修复保存、回显、主动清空。
2. **核心可完成性**：修复两条导入路径与 Generate 唯一门槛。
3. **资料版本稳定性**：实现 Master Resume 与 Application Snapshot。
4. **页面结构**：实现列表、详情、Profile 三个工作区。
5. **流程简化**：实现 Documents 选择、非阻塞 Diagnosis、归档与恢复。
6. **打磨**：校验视觉、技术信息折叠、按钮位置、Cover Letter 左对齐。

每阶段完成后运行对应回归用例；不得用临时字段或隐藏条件绕过第 4.2 节的 Generate 规则。
