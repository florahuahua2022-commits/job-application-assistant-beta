# 产品内容质量升级 PRD V3：Resume、Cover Letter 与投递模板

版本：3.0  
日期：2026-09-05  
状态：可进入产品与工程联合评审  
适用产品：Job Application Assistant Beta

## 1. 文档定位

本 PRD 合并并取代以下整体质量文档：

- `product-content-quality-prd-v2.md`
- `product-content-quality-prd-v2.1-generated-output-calibration.md`

Relevance / Timeline Integrity 专项 PRD 继续并存。专项 PRD 中的 12 个月阈值、`full / condensed / timeline_only / hidden` 四档、连续低相关经历合并规则及 Test 1–15 均继续有效。本 PRD 不以版本号覆盖这些具体规则。

本 PRD 的目标是让用户基于真实简历和 JD 生成事实可靠、岗位相关、表达具体、视觉专业的 Resume 与 Cover Letter，完成后只需少量个人措辞调整即可投递。Career Ops 是经用户认可的成品参考，不是事实来源，也不是要求逐字或逐像素复制的产品依赖。

## 2. 背景

2026-09-05 的真实生成暴露了四类问题：

1. **质量判断误报**：约 451 词的 CV 因刚好低于 `650 × 70% = 455` 词，并且拆分后的短 evidence 超过半数，被判定 Writing quality fail；人工审阅认为成品已基本可投递。
2. **事实利用不足**：后续 v135 虽提升至约 509 词，但仍省略 2019–2026 的多段经历，并丢失 Dayforce、供应商数量、国际访问规模、社区项目范围、CRM 和证书等有辨识度的事实。
3. **规划与一致性问题**：旧版 Cover Letter 未选择最新 WA Government 经历；机构名曾显示为 `WA gov`；availability 可能来自过期 Profile，也可能是无来源生成，而现有检查不能完整区分。
4. **模板差距**：当前 Classic Calibri 模板结构可读，但与 Career Ops 参考成品相比，姓名区、章节层级、组织与日期布局、信息密度及 CV/Cover Letter 视觉一致性仍不足。

## 3. 对标基准

### 3.1 版本

- Career Ops 仓库：`https://github.com/career-ops-hq/career-ops`
- 本次对比固定 commit：`843d179624e09c5b9bc9ec372a2b21dd338ef94e`
- Career Ops 样本：两页 CV PDF（约 599 词）和一页 Cover Letter PDF（约 332 词）。
- 当前产品样本：Tailored CV v135 DOCX（约 509 词）和 Cover Letter v137 DOCX（约 283 词）。

### 3.2 基准限制

正式对标必须使用完全相同的 Master Resume、完整 JD、页面技能标签、Profile、补充回答和生成日期。本次已提供双方成品，但尚未固定全部共同输入，因此：

- 可以确认结构、信息保留和视觉差距。
- 不能仅凭 Career Ops 输出证明其中的具体数字、日期、资格或声明真实。
- Career Ops 成品中的 Dayforce、人数、供应商数量、社区规模、Police Clearance、White Card 和具体到岗日期，必须在共同输入中确认后才能进入黄金事实集。

## 4. 产品原则

1. **事实优先**：不得创造或升级数字、日期、工具、资格、雇主、责任、结果和当前状态。
2. **先保留再筛选**：解析阶段保留完整事实，规划阶段再按岗位相关性和信息价值分配篇幅。
3. **相关性不改变历史**：低相关经历可以压缩，但不能制造虚假连续任职或不合理空档。
4. **具体胜于通用**：优先使用有来源的行动、对象、工具、范围和背景；不靠同义词替换制造差异。
5. **辅助指标不独立定罪**：词数、关键词和动词频率只能触发检查，不能单独导致质量失败。
6. **两份文档分工明确**：Resume 展示职业证据全貌；Cover Letter 选择少量案例解释岗位适配。
7. **内容与模板解耦**：模板切换只重新导出，不调用模型改写正文。
8. **用户最终确认**：Ready 表示通过系统检查并达到可用门槛，不代表替用户完成最终事实确认。

## 5. 范围

### 5.1 P0

- 完整来源解析和事实追溯。
- Relevance / Timeline Integrity 回归。
- Resume 证据选择、具体性和跨经历重复检查。
- Cover Letter 招聘主体解析及案例选择。
- Availability 来源、生成和跨文档一致性。
- 质量责任归因和用户提示。
- Career Ops 同样本内容回归。

### 5.2 P1

- `career_modern` CV 与 Cover Letter 模板。
- DOCX/PDF 同源导出和视觉验收。
- 招聘页面技能标签建模及 Key Skills 映射。
- 技能去重、References 默认策略及称呼优化。

### 5.3 非目标

- 不保证获得面试或工作。
- 不要求所有 CV 达到 650 词或固定两页。
- 不把缺少数字结果本身视为质量失败。
- 不自动研究并写入未经验证的公司信息。
- 不重新设计已完成的 Cover Letter 雇主白名单和任期时态检查，只做回归与补漏。
- 不复制 Career Ops 的产品品牌、完整工作流或无来源内容。

## 6. 核心流程

1. 用户选择或上传 Master Resume，并确认当前申请使用的快照。
2. 用户导入完整 JD；系统解析职位、招聘主体、要求、限制和页面技能标签。
3. 系统建立可追溯的 Career Knowledge Base，不因换行、日期格式或复合段落丢失事实。
4. 系统将岗位要求与事实匹配，形成 Application Decision。
5. Resume Plan 按相关性、事实价值和时间线完整性分配内容。
6. Cover Letter Plan 独立选择 2–3 个互补案例。
7. 系统生成正文，执行事实审核、内容审核和跨文档一致性检查。
8. 明确问题最多自动修复两轮；保留每轮版本和问题记录。
9. 用户查看、编辑和选择正文版本。
10. 用户选择格式与模板，从同一正文导出 DOCX/PDF。
11. 导出完成 ATS 与视觉检查后，文档才可显示为可使用。

## 7. 数据与来源要求

### Q1：完整事实结构

每条经历至少保留：

- 原始岗位段落或来源组 ID。
- 雇主、职位、地点及可靠任期。
- 独立行动、对象、工具、范围、背景和结果。
- 原文片段和来源位置。
- 事实确认状态及解析置信度。

拆分职责用于匹配时，每条 evidence 必须保留 `source_paragraph` 或可稳定还原的来源组。不能把一条职责记录机械等同于一个完整岗位，也不能因内部拆分改变源材料密度判断。

### Q2：来源密度

工作经历的来源密度按同一雇主、职位和任期对应的完整原始岗位段落计算：

- 标题、雇主和日期只排除一次。
- 合并该岗位所有独立职责计算有效详情。
- 单条职责少于 20 词不能独立证明材料不足。
- 无法还原完整段落时，标记解析不确定；不得直接归因用户资料不足。
- 不相关的岗位、项目或教育记录不得跨组拼接凑密度。

## 8. Resume 规划与生成

### Q3：Relevance 与时间线

- 使用专项 PRD 的 `full / condensed / timeline_only / hidden` 四档。
- 只有隐藏内容造成的连续空档大于 12 个月时触发时间线兜底；恰好 12 个月不触发。
- 连续低相关经历之间真实间隔不超过两个月时，才可进入同一简短占位组。
- 当前任职覆盖至生成日期；真实空档和部分未覆盖区间保持原样。
- 只修复由隐藏造成的 artificial gap，不把真实空档改写成连续就业。
- 2019–2026 等多段真实经历不得整体消失并形成误导性长期空档。

### Q4：内容选择

每个重点要求对应的最佳事实必须被使用，或记录以下之一作为省略理由：

- 重复。
- 低相关性。
- 事实冲突或未确认。
- 用户排除。
- 雇主明确篇幅限制。
- 已被更强且互补的事实覆盖。

固定 evidence 数量和 bullet 上限不得优先于独特事实。相关岗位默认可使用 4–6 条有内容的 bullet；压缩岗位使用 0–2 条。数量是篇幅指导，不是机械生成目标。

### Q5：Resume 写作

- Summary 说明职业定位、年限和 1–2 项有来源的优势。
- 重点 bullet 尽量包含行动及至少一个对象、工具、范围或背景。
- 保留 supported/assisted/contributed 等真实责任边界。
- 不把政府雇主自动升级为政策、治理或法规经验。
- 不把供应商协调自动升级为 Contract Management。
- 不把 JD 标签直接写成候选人技能。
- Availability 默认不放入 Summary；只有用户明确选择时才能出现。
- `References available upon request` 默认省略，除非雇主要求或用户选择。

### Q6：跨经历重复

系统按“行动 + 对象/背景”检查语义重复：

- 三段及以上经历使用近似通用模板，并且来源存在未使用的差异化事实时，判 `generation_under_utilized`。
- 仅首个动词相同，但对象、范围和事实明显不同，不得失败。
- 修复必须换入来源中的独特事实；不能只轮换 Provided、Supported、Collated、Maintained 或 Liaised。
- 同一事实不得改写后跨多个岗位重复使用。

## 9. Cover Letter 规划与生成

### Q7：招聘主体

Job identity 分开保存：

| 字段 | 含义 |
|---|---|
| `advertiser` | 招聘广告发布方或中介 |
| `hiring_organisation` | 实际用工组织；未知时保持泛化 |
| `recruitment_relationship` | JD 明确说明的代表招聘关系 |
| `organisation_display_name` | 用户确认用于文档的展示名称 |

解析必须保存所有名称候选、原文片段、来源位置、置信度和选择理由。`WA gov` 等非原文正式名称不得进入最终抬头。中介代表多个未指定政府部门招聘时，可以使用可靠中介名称，并按原文说明关系；不得虚构具体部门。

### Q8：Cover Letter 选材

默认选择 2–3 个互补案例，依次比较：

1. 对前三项岗位重点的直接支持程度。
2. 新增的重点要求覆盖。
3. 与目标环境的明确关联。
4. 时间新近程度。
5. 事实具体程度。
6. 与其他申请文档的重复成本。

最新经历不自动入选，政府雇主也不自动证明政府能力；但最新且直接相关的经历必须进入候选比较。未选择时，Plan 记录事实过薄、仅 adjacent、与更强案例重复或不支持首要要求等具体理由。

### Q9：Cover Letter 写作

- 开头直接说明岗位及真实适配背景，不写无来源热情或价值观。
- 每个案例说明做了什么、涉及什么对象/背景，以及为何与岗位相关。
- 默认 250–400 词、一页；材料较少时允许更短。
- 不逐条复制 Resume，也不写成第二份 Selection Criteria。
- 公司、团队、项目和招聘关系只能来自 JD 或可靠来源。
- Work rights、Police Clearance、White Card、availability 等声明只使用 Profile 或用户确认值。
- 有明确招聘方时使用其可靠名称；未知时使用中性称呼，不输出低质量内部短标签。

## 10. Availability

### Q10：字段与选项

Applicant Profile 使用 `availability_notice`，允许：

- `not_specified`：不在文档中说明。
- `immediate`：Available immediately。
- `two_weeks`：Available following two weeks' notice。
- `one_month`：Available following one month's notice。
- `negotiable`：Start date negotiable。

默认值保持 `not_specified`。已有记录不得自动迁移为 `immediate`。用户必须主动保存非默认值。

### Q11：生成与审核

| Profile 值 | 文档表述 | 结果 |
|---|---|---|
| 与已确认值一致 | 对应标准句或合理省略 | 通过 |
| `not_specified` 或 Profile 缺失 | 出现通知期、日期或立即到岗承诺 | `unsupported_availability_claim`，阻断 Ready |
| 已确认值与文档不同 | 任意冲突值 | `availability_conflict`，阻断 Ready |
| 任意值 | 根据 CV 任期推算 availability | 不允许 |

检查覆盖 immediate、promptly、具体日期、two weeks、one month、notice period、negotiable 及同义表达。确定性整理在 `not_specified` 时删除所有 availability 句，其他值统一为已确认标准句。

生成 trace 保存 Profile 版本、更新时间和 `availability_notice` 枚举。Profile 页面和生成前摘要显示当前值并允许修改。

## 11. 招聘页面技能标签

### Q12：标签建模

页面明确提供的 Project Delivery、Project Management、Contract Management、Data Analysis 等标签保存为 `advertised_skill_tags`，记录原文、页面区域和来源类型。

每个标签分别匹配候选人证据：

- `direct`：可按原意进入 Key Skills。
- `adjacent`：只能使用候选人实际做过的较窄表达。
- `gap`：不进入 Key Skills。

页面标签是需求线索，不等于候选人事实。标签与正文冲突时，以正文明确要求为准，并保存冲突说明。

### Q13：技能去重

- Key Skills 表达岗位能力。
- Technical Skills 只保留已确认的软件、系统和技术工具。
- 两节中规范化后完全相同的条目只保留一次。
- 能力与具体系统不自动视为同一事实，例如 records management 与某一 Records Management System 可以分别存在。

## 12. 质量审核与状态

### Q14：三类内容不足

| 类型 | 条件 | 责任与动作 | 阻断 Ready |
|---|---|---|---|
| `insufficient_source_detail` | 完整来源组确实缺少有用的行动、对象、工具、范围或背景 | 指向具体经历，请用户补充真实信息 | 是 |
| `generation_under_utilized` | 来源有具体相关事实，成品遗漏或泛化 | 系统重新规划或局部重写 | 是 |
| `concise_but_relevant` | 成品较短，但相关事实覆盖充分 | 保持精炼，可继续使用 | 否 |

`evidence_thin` 比例、词数、关键词或动词频率不得单独产生阻断。451 词和 455 词不能形成可使用/不可使用的质量跳变。

### Q15：审核输出

每个重大问题必须包含：

- 成品中的具体位置和原句。
- 对应来源事实或明确缺口。
- 问题对投递的影响。
- 由用户补资料、系统重写、更新 Profile 或确认事实中的哪一方处理。

用户界面显示自然语言结论，不直接展示 `resume_quality [MAJOR]`、evidence ID、比例或调试字段。技术详情可折叠。

### Q16：状态

| 状态 | 条件 | 允许动作 |
|---|---|---|
| 待检查 | 有正文但审核未完成，或用户已编辑 | 查看、编辑、重试检查、下载标记草稿 |
| 待完善 | 有明确事实或重大内容问题 | 补充、局部修复、草稿下载 |
| 可使用 | 当前版本事实、内容与导出检查通过 | 正常导出，仍提示用户最终确认 |
| 基于旧材料 | Resume、JD 或 Profile 已更新 | 查看历史、使用新输入重新生成 |

生成任务状态与文档质量状态分开。审核失败或超时保留正文，不伪装为通过；两轮修复仍失败时保留较好版本并显示剩余问题。

## 13. Career Modern 模板

### Q17：通用要求

- CV 与 Cover Letter 均使用 A4、单栏和从上到下的 ATS 文本顺序。
- 不使用多栏正文、头像、技能进度条、图标字体或浮动文本框。
- 颜色只用于视觉层级，不承载唯一信息。
- 空章节连同标题和间距完整移除。
- 模板使用版本化设计 token，不依赖应用默认样式。

### Q18：CV 模板

- 本样本目标两页；页数根据事实量和雇主限制调整。
- 顶部使用突出姓名、单行联系方式和细分隔线。
- 章节标题使用统一大写、强调色和细分隔线。
- Core Competencies 可使用紧凑标签；底层文本必须连续可提取。
- 组织名称、职位和右对齐日期形成稳定层级。
- 不把岗位标题孤立在页底；同一岗位标题与第一条 bullet 保持在同页。
- Education、Certifications 与 Skills 使用紧凑、清晰的独立区域。

### Q19：Cover Letter 模板

- 单页，使用与 CV 相同的姓名、联系方式、字体体系和基础间距。
- 使用更克制的黑色正文、清晰标题、招聘方/地点/日期元数据行和顶部细线。
- 正文左对齐。
- 允许自然段或 2–4 个带粗体能力标签的短 bullet，由内容规划选择。
- 一页空间不足时先删除重复和低价值内容，再调整间距；不能压缩到不可读字号。

### Q20：设计 token

工程实现前冻结 token 表，至少包含：

- A4 尺寸与页边距。
- 姓名、章节、组织、职位、正文和辅助文字字号。
- 字体及回退字体。
- 行距、段前后距和 bullet 缩进。
- 分隔线宽度和颜色。
- 主文字色、章节强调色、组织强调色和 competency 标签样式。

参考方向为深色正文、青色章节标题和紫色组织名称。若直接复用 Career Ops MIT 模板代码，必须保留许可证及归属说明。

### Q21：跨格式一致性

- PDF 与 DOCX 从同一正文版本和同一模板 token 导出。
- 章节、事实、日期、条目和 ATS 顺序完全一致。
- 渲染能力导致的合理差异可以接受，但不能出现内容丢失、重排、截断或重写。
- 字体不可用时使用显式回退，不能静默改变字号造成溢出。
- 模板切换只生成新导出文件，保留原正文与历史文件。

## 14. 追踪与可解释性

每次生成记录：

- 实际 provider/model 及回退情况。
- Resume、JD、Job Model 和 Profile 的版本及指纹。
- 招聘主体名称候选、原文和最终选择。
- 完整事实 → evidence → document plan 的映射。
- 每项重点要求的候选事实、评分维度、选择和排除理由。
- 初稿、审核问题、修复稿和最终选择版本。
- 模板、格式、设计 token 版本和导出检查。

不得只记录配置中的首选模型，也不得在生成中混合新旧输入版本。

## 15. 验收用例

### 15.1 内容与事实

| ID | 场景 | 必须结果 |
|---|---|---|
| A01 | 一个岗位原段落含多个独立行动 | 全部事实可追溯，不因一个记录只生成一句通用职责 |
| A02 | 拆分后每条职责少于 20 词，但岗位段落整体充分 | 不判用户材料不足 |
| A03 | 岗位只有“负责日常行政工作” | 判 `insufficient_source_detail`，指出具体岗位 |
| A04 | 451 词且事实覆盖充分的 CV | 不因低于 455 词失败 |
| A05 | 在 A04 增加四个无信息词 | 质量状态不发生改善 |
| A06 | 来源有工具和范围，五段经历均生成通用支持句 | 判 `generation_under_utilized` |
| A07 | 多段首词相同但事实不同 | 不因首词重复失败 |
| A08 | 三段生成近似相同的行政支持句 | 检出跨 entry 语义重复 |
| A09 | 原文无结果或数字 | 不补造结果或数字 |
| A10 | 用户只有相邻工具经验 | 不声称掌握 JD 指定工具 |

### 15.2 Timeline

| ID | 场景 | 必须结果 |
|---|---|---|
| T01 | 隐藏内容产生恰好 12 个月空档 | 不触发兜底 |
| T02 | 隐藏内容产生超过 12 个月空档 | 按专项规则显示 timeline continuity |
| T03 | 连续低相关经历间隔不超过两个月 | 可按专项规则合并 |
| T04 | 低相关经历之间存在更长真实空档 | 不合并成连续就业 |
| T05 | 2019–2026 有多段真实经历 | 不得全部省略形成七年可见空档 |

专项 Test 1–15 全部继续执行，本表不替代其完整输入和期望。

### 15.3 Cover Letter 与 Profile

| ID | 场景 | 必须结果 |
|---|---|---|
| C01 | 最新政府经历直接支持首要要求 | 进入候选比较，选择或排除均有理由 |
| C02 | 较早案例更具体且覆盖互补要求 | 可以选择较早案例，并解释取舍 |
| C03 | Randstad 等中介代表多个政府部门 | 正确区分 advertiser、client 和关系，不输出 `WA gov` |
| C04 | 多个名称候选冲突 | trace 保留原文和选择理由；低置信度名称不进抬头 |
| C05 | Profile=`not_specified`，模型写 one month's notice | 删除并判 `unsupported_availability_claim` |
| C06 | Profile=`one_month`，文档使用对应标准句 | 通过；trace 记录 Profile 版本和值 |
| C07 | Profile 从 one_month 改为 not_specified | 旧稿标为基于旧 Profile；新稿不写通知期 |
| C08 | Profile 与文档 availability 不同 | 判 `availability_conflict` |
| C09 | 无 Police Clearance 或 White Card 来源 | 不写已持有或满足相关要求 |

### 15.4 标签与模板

| ID | 场景 | 必须结果 |
|---|---|---|
| M01 | 页面有四个技能标签 | 全部进入 Job Model 并分别标记 direct/adjacent/gap |
| M02 | 只有供应商协调，无合同职责 | Contract Management 不进入 Key Skills |
| M03 | Key Skills 与 Technical Skills 重复 | 等价条目只保留一次 |
| M04 | 使用 `career_modern` 导出本样本 CV PDF | A4 两页、单栏、无截断重叠，层级与参考样本同等清楚 |
| M05 | 同一正文导出 DOCX 与 PDF | 文本、事实、顺序和视觉层级一致 |
| M06 | 导出 Cover Letter | A4 一页、正文左对齐、招聘身份和事实正确 |
| M07 | 空 Certifications 或 Skills | 整个空章节及间距移除 |
| M08 | 字体缺失或长组织名换行 | 使用回退字体，无重叠，日期仍可读 |
| M09 | ATS 提取 PDF 与 DOCX | 姓名、联系方式、章节、岗位、日期和 bullet 顺序正确 |
| M10 | 两位评审盲看 Career Ops 与产品成品 | 产品六项质量均不低于参考，且无需实质修改即可投递 |

### 15.5 流程回归

同时回归：

- V2 A01–A24。
- Relevance / Timeline Test 1–15。
- Cover Letter 雇主白名单与任期时态检查。
- 旧申请重新解析、材料更新、人工编辑保护和重复点击。
- Resume 成功而 Cover Letter 失败时仅重试失败文档。
- DOCX/PDF 导出失败时保留正文和已成功格式。

## 16. 质量评分与发布门槛

### 16.1 样本

至少六组候选人材料 × JD，覆盖：

- 充分相关且事实丰富。
- 多段相邻经验。
- 材料稀疏。
- 无量化结果。
- 严格篇幅限制。
- 政府与企业表达差异。

每组以相同输入分别生成 Resume 和 Cover Letter，各重复三次；保留初稿与修复稿，不能只挑最好一次。

### 16.2 人工评审

两位评审隐藏生成来源并随机排序，至少一位具备招聘或简历审阅经验。分别按 1–5 分评价：

- 事实与案例利用。
- 岗位针对性。
- 表达具体程度。
- 结构与可读性。
- 个人特色与文档分工。
- 视觉专业度。

### 16.3 门槛

1. 未经支持的申请人事实、责任升级和虚假时间线为零。
2. 人工标注的必保事实全部保留或有评审认可的省略理由。
3. `insufficient_source_detail` 阻断误报率不高于 5%。
4. “来源充分但生成未利用”的责任归因准确率至少 90%。
5. Availability 明确冲突检出率 100%，无来源推断承诺为零。
6. 单纯跨过词数阈值造成的质量状态翻转为零。
7. 至少 90% 成品各项平均分不低于 4，单项不低于 3。
8. 至少 80% 的逐份对照不低于 Career Ops 对应成品；Resume、Cover Letter 和模板分别达标，不能互相平均抵消。
9. 本次认可样本在内容和 `career_modern` 模板上通过 M04–M10。
10. 生产模型、回退模型、模板和运行配置与通过验收的配置一致。

同时报告成本、完成时长、补问次数、重试率和首次可使用率。不得为降低成本静默使用未通过质量验证的模型。

## 17. 实施顺序

| 阶段 | 交付 | 完成条件 |
|---|---|---|
| G0 基准冻结 | 共同输入、Career Ops commit、黄金事实、Profile 与模板样本 | 产品确认事实来源和对标范围 |
| G1 事实与时间线 | 来源聚合、完整解析、四档相关性及 Timeline | A01–A10、T01–T05、专项 Test 1–15 通过 |
| G2 规划与写作 | Resume 差异化、Cover Letter 身份与选材、标签映射 | C01–C09、M01–M03 通过 |
| G3 审核与状态 | 三类责任归因、availability、有限修复、版本追踪 | 重大误报和漏报场景通过 |
| G4 模板与导出 | `career_modern`、DOCX/PDF 同源及 ATS/视觉检查 | M04–M10 通过 |
| G5 小范围发布 | 六组盲评、成本和时延记录 | 第 16 节所有门槛满足 |

## 18. 当前实现基线

截至本稿：

- 本地已实现 Relevance/Timeline 基础链路、Resume/Cover Letter 独立计划、事实审核、雇主白名单、任期时态检查和 DOCX/PDF 导出。
- 本地已将存在 `source_paragraph` 的拆分经历优先按完整段落计算密度，并通过相关自动化检查；尚未完成本 PRD 全部验收。
- `availability_notice` 已存在，当前默认 `not_specified`，但没有 `immediate` 选项；现有检查只覆盖 immediate/prompt availability 的部分句式。
- Cover Letter v137 已使用最新 Finance Administration Officer，并修复旧版 `WA gov` 的表面结果；招聘主体结构和 trace 尚未完成。
- 当前有 Classic、Modern、Traditional 三种导出主题；尚未实现本 PRD 定义的 `career_modern` 及同样本视觉验收。

## 19. 开放决策

进入开发前需冻结：

1. 本次双方生成实际使用的共同 Master Resume、JD、Profile 和补充回答。
2. Career Ops 输出中具体日期、资格、人数、系统和规模的事实来源。
3. `career_modern` 的最终 token 表及用户是否可设为默认模板。
4. Organisation display name 的用户确认位置。
5. Availability 是否在生成前摘要中默认展示，建议展示非默认值。

这些决定不阻塞已确认的来源聚合、词数误报和跨经历重复修复。

## 20. 自审结论

本 PRD 已连接输入来源、事实结构、相关性与时间线、两类文档规划、质量审核、版本状态、模板导出和发布验收。P0 可复用现有 CKB、Plan、Reviewer 和 trace 链路；P1 在现有 exporter 上增加模板能力，具体渲染技术由工程设计决定。

完成编码不等于达到 Career Ops 水平。只有在共同输入、固定版本和人工盲评下通过第 16 节门槛，才可宣称达到本 PRD 定义的“用户生成后无需实质修改即可投递”。
