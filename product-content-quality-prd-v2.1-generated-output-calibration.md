# 产品内容质量升级 PRD V2.1：真实生成样本校准

版本：2.1  
日期：2026-09-05  
状态：增量需求稿，可进入产品与工程评审  
父需求：`product-content-quality-prd-v2.md`

## 1. 背景与结论

用户使用旧申请重新生成了 Tailored CV v132 与 Cover Letter v133。人工检查认为 CV 相比旧版本明显改善，事实检查通过，内容已达到可投递水平；系统却将 Writing quality 判为 fail，并提示五段相关经历的源证据过薄。

本次样本暴露的核心问题不是生成失败，而是质量判断与成品质量不一致：CV 共约 451 词，刚好低于现有 `650 × 70% = 455` 词检查线；同时，导入流程将同一岗位的多条职责拆成独立证据，质量检查仍按每条证据是否达到 20 词判断，忽略完整岗位段落的合计信息量，最终产生阻断性误报。

本增量需求修正质量判断粒度，并补齐人工审阅发现的内容差异化、重复技能及跨文档一致性检查。V2 的事实边界、Relevance/Timeline 专项规则、生成状态和发布门槛继续有效。

### 1.1 Availability 现状核查

Applicant Profile 已存在 `availability_notice` 字段，当前允许值为 `not_specified / two_weeks / one_month / negotiable`；数据库、后端模型和前端表单默认值均为 `not_specified`，界面不会默认选择 one month。当前没有 `immediate` 选项。生成 prompt 会把 `one_month` 映射为 `Available following one month's notice`，生成后的确定性整理也使用固定句子 `I am available following one month's notice.`。

当前本地数据库没有该线上用户的 Profile 记录，因此无法从本地证明本次生成时的实际值。现有生成结果与固定 `one_month` 句子完全相同，但仍存在两种可能，必须从该次运行的 Profile 快照区分：

1. 快照为 `one_month`：内容来自已保存 Profile，不是模型编造；若事实已变化，属于用户资料陈旧和编辑入口可见性问题。
2. 快照为 `not_specified` 或缺失：属于 `unsupported_availability_claim`。当前确定性检查只拦截 immediate/prompt availability，不检查 notice-period 表述，因此该说法可能漏过审核。

不得根据输出句子反推 Profile 值并直接归因。运行 trace 必须保留非敏感枚举值 `availability_notice` 及 Profile 版本/更新时间，供用户和支持人员定位。

## 2. 目标与非目标

### 2.1 目标

1. 资料充足且事实审核通过的精炼 CV，不因接近固定目标词数而被误判为不可使用。
2. 源证据密度按用户实际提供的岗位上下文判断，不受内部拆分记录方式影响。
3. 区分“用户材料不足”“生成未充分利用”和“成品精炼但相关”三种情况，并给出正确责任归因。
4. 检出多段经历被写成相似通用职责、技能重复和 Resume/Cover Letter 可用时间冲突。
5. 正确区分招聘广告发布方、代表招聘关系与实际用工组织，并把最相关的近期经历纳入 Cover Letter 选材比较。
6. 将招聘页面明确提供的技能标签纳入需求建模，但不把标签直接冒充候选人能力。
7. 所有阻断问题必须引用具体成品段落和对应来源；辅助指标不能单独造成阻断。

### 2.2 非目标

- 不要求 CV 达到统一 650 词或固定两页。
- 不为了增加具体性而生成未经来源支持的数字、工具、成果或责任范围。
- 不重新设计已完成的 Cover Letter 雇主白名单与任期时态检查。
- 不改变 Relevance/Timeline 专项 PRD 的 12 个月阈值和四档展示规则。
- 不在本期引入新的评分模型或外部依赖。

## 3. 样本观察

| 观察 | 当前结果 | 产品判断 |
|---|---|---|
| CV 约 451 词，目标 650 词 | 低于 455 词即进入短文档检查 | 词数只能触发诊断，不能证明内容不足 |
| 同一岗位职责被拆成多条短 evidence | 超过一半单条少于 20 词，判 source thin | 应按原岗位段落或等价完整来源聚合判断 |
| 五段经历均保留且时间线清晰 | 提示用户为五段经历补资料 | 责任归因错误；不应要求用户重复提供已有内容 |
| 多段经历反复出现 Provided、Supported、Collated、Maintained、Liaised | 可能只产生轻量动词提示 | 应检查行动、对象和语义差异，而非只数首词 |
| Key Skills 与 Technical Skills 重复 | 未检出 | 应给出非阻断去重建议或在生成阶段合并 |
| CV 最近任期已结束，Cover Letter 声称需一个月通知期 | 未检出 | 需核对用户确认的 availability；无来源时属于未经支持的承诺 |
| Cover Letter 使用泛化称呼和低质量雇主标签 | 允许通过 | 有可靠招聘方名称时应优先使用；否则仅建议，不编造 |
| JD 发布方为招聘中介、客户为政府部门，抬头显示 `WA gov` | 单一 Organisation 字段被原样用于生成 | 需要区分广告发布方、招聘关系和用工组织；保留解析原文及选择理由 |
| Cover Letter 未使用最新 WA 政府经历，重点使用较早项目经历 | 最多选择两个 primary evidence，当前排序未明确考虑时间、目标环境和排除理由 | 最新不等于必选，但直接相关近期经历必须进入显式比较并留下未选理由 |
| 招聘页面列出 Project Delivery、Project Management、Contract Management、Data Analysis 标签 | 标签未形成独立结构化输入 | 标签属于广告需求线索；只有证据支持的部分才能进入候选人 Key Skills |

## 4. P0 需求

### Q1：按完整来源聚合证据密度

源证据密度的最小判断单元为“同一事实上下文”，对工作经历默认是同一雇主、职位和任期对应的原始岗位段落。

- 内部为匹配和生成而拆分职责时，每条证据必须保留原岗位段落引用或可稳定还原的来源组 ID。
- 同一来源组中的标题、雇主和日期只排除一次；所有独立职责合并计算有效详情。
- 同一短职责可以用于证据匹配，但不能因为内部拆分后少于 20 词而独立证明用户材料不足。
- 无法还原原岗位段落时，可按同一 `source_section + authoritative period` 聚合；若归属仍不确定，标记解析不确定，不向用户断言资料不足。
- 单条独立项目、志愿经历或教育事实继续按其真实来源范围判断，不得跨无关经历合并凑密度。

实现基线：本地已将存在 `source_paragraph` 的拆分经历优先按该段落计算密度，并新增回归检查；该变更尚不等于完成本 PRD 的全部验收。

### Q2：词数仅作诊断触发器

- `target_words` 是写作规划值，不是发布最低线。
- CV 低于目标词数 70% 时，系统可以启动内容利用检查，但不能仅凭词数将 Writing quality 设为 fail。
- 451 词与 455 词不得产生“可使用/不可使用”的跳变；边界前后只有诊断是否运行的差别。
- 存在雇主明确最小字数时，按雇主要求独立校验，并引用该要求。
- 材料较少但所有相关事实已合理使用时，状态为 `concise_but_relevant`，最多显示 advisory。

### Q3：正确区分三类内容不足

| 类型 | 判定条件 | 产品行为 | 是否阻断 Ready |
|---|---|---|---|
| `insufficient_source_detail` | 完整来源组确实缺少可写的行动、对象、工具、范围或背景，且成品无法形成有用描述 | 指向具体经历，请用户补充已知事实 | 是，但必须有来源级证据 |
| `generation_under_utilized` | 完整来源存在相关具体事实，但成品遗漏或泛化 | 系统重新规划或局部重写，不要求用户补资料 | 是 |
| `concise_but_relevant` | 成品较短，但相关事实已覆盖、无明显重复或关键遗漏 | 说明精炼，可继续使用 | 否 |

不得用 `evidence_thin` 比例单独决定 `insufficient_source_detail`。阻断判定至少同时满足：完整来源组不足、相关岗位表达因此无实际信息、没有被解析或规划遗漏所解释。

### Q4：跨经历语义差异检查

质量检查按工作经历 bullet 的“行动 + 对象/背景”比较，而不是只统计首个动词。

- 如果三段及以上经历主要由相同的通用语义模板构成，例如“提供行政支持”“整理信息”“维护记录”，且来源中存在未使用的差异化事实，判定 `generation_under_utilized`。
- 如果来源本身只有这些通用职责，判定材料不足或 advisory，不得自动创造差异。
- 单纯重复 Provided/Supported 等首词可作为提示信号；只要对象、范围和事实明显不同，不得因此失败。
- 修复优先替换为来源中已有的独特行动、对象、工具或业务背景，不以同义词轮换冒充改善。

### Q5：Resume 与 Cover Letter 可用时间一致性

建立共享的 availability 事实，而不是从任期日期自动推断通知期。

- 用户明确提供 `available immediately`、通知期或可入职日期时，两份文档如提及该事实，必须使用同一已确认值。
- 文档中的 availability 表述没有任何用户确认来源时，判定 `unsupported_availability_claim`；它不能仅由 CV 任期状态推导。
- 文档表述与用户已确认的 availability 不一致时，判定 `availability_conflict`。
- 该问题阻断 Ready，但允许保留和编辑草稿。
- 自动修复只能删除无来源通知期或替换为已确认值，不能从结束日期推算“立即到岗”。
- 未确认 availability 时默认省略，不把缺失状态写成承诺。
- 审核需覆盖 immediate、promptly、具体日期、two weeks、one month、notice period 和 negotiable 等完整 availability 表述，不能只枚举 immediate/promptly 两个短语。
- 生成后的确定性整理按 Profile 枚举统一处理所有 availability 表述：`not_specified` 时删除；其他值替换为对应的已确认标准句。不得只替换 `available to commence...` 句式。
- 生成 trace 保存本次使用的 Profile 版本、更新时间和 `availability_notice` 枚举，不保存不必要的联系方式副本。
- 用户在 Profile 页面必须能看见当前 Availability 值；`one_month` 等非默认值在生成前摘要中可查看和修改。
- 增加 `immediate` 枚举和 “Available immediately” 选项；已有记录不迁移为 immediate，默认值继续为 `not_specified`。只有用户主动保存后才能在文档中使用。

处理矩阵：

| 生成时 Profile 值 | 文档表述 | 结果 | 修复责任 |
|---|---|---|---|
| `one_month` | one month's notice | 通过事实检查 | 若事实已变化，由用户更新 Profile；新生成使用新版本 |
| `immediate` | available immediately | 通过事实检查 | 使用用户主动保存的值 |
| `not_specified` 或 Profile 缺失 | one month's notice | `unsupported_availability_claim`，阻断 Ready | 系统删除表述或请用户确认 |
| `two_weeks` | one month's notice | `availability_conflict`，阻断 Ready | 系统替换为 two weeks 的确认句 |
| `negotiable` | one month's notice | `availability_conflict`，阻断 Ready | 系统替换为 negotiable 的确认句 |
| 任意值 | 根据 CV 结束日期推导立即到岗或通知期 | 不允许 | 删除推导，只使用 Profile |

### Q6：招聘主体解析与抬头使用

Job identity 需要区分以下角色，不再由单一 Organisation 字符串同时承担：

| 字段语义 | 示例 | 使用位置 |
|---|---|---|
| `advertiser` | Randstad Professionals | 有来源时用于招聘方称呼、抬头或正文中的招聘关系 |
| `hiring_organisation` | 未指明的 WA State Government department(s) | 只按广告原文描述，不擅自缩成具体部门 |
| `recruitment_relationship` | advertiser recruiting on behalf of government departments | 仅在 JD 明确说明时使用 |
| `organisation_display_name` | 经确认用于文档抬头的名称 | Cover Letter 抬头的唯一直接输入 |

- 解析必须保存候选名称、原始文本片段、来源位置及选择结果。生成 trace 能回答 `WA gov` 来自哪段原文、经过了什么规范化，以及为何被选为 display name。
- 优先使用明确标注的公司/招聘方、页面发布方或正文中清晰自我标识；不能从泛化描述截取临时短标签作为正式名称。
- 保留合法品牌大小写；未知品牌不得随意 title-case。`WA gov`、`wa GOV` 等非原文正式名称不得作为最终抬头。
- 当中介代表未指定的多个政府部门招聘时，可显示招聘中介名称，并在正文准确说明其代表关系；不得虚构具体政府部门。
- 自动解析不确定或多个候选冲突时，保存为待确认并允许生成草稿；未确认名称可以从抬头省略，不能输出低置信度猜测。
- 用户保存申请前仍可修改 display name；修改后重建 Job Model 并使旧生成结果标记为基于旧输入。

### Q7：Cover Letter 必须比较近期直接相关经历

Cover Letter Plan 仍以岗位要求和证据强度为主，但候选 evidence 需要增加以下可解释比较维度：

1. 对 Cover Letter 前三项重点要求的直接支持程度。
2. 与目标工作环境的明确相似性，例如来源已确认的 WA State Government 经历。
3. 时间新近程度及当前/最近工作状态。
4. 事实具体程度，以及是否能形成有内容的案例。
5. 与 Resume 或 Selection Criteria 的重复成本。

规则：

- 最新经历不自动获胜，政府经历也不因雇主身份自动证明某项能力。
- 如果最新经历同时直接支持 Cover Letter 的重点要求，并提供目标环境或近期能力证据，它必须进入最终候选比较；未选时记录机器可读的具体理由，例如事实过薄、只属 adjacent、与更强案例重复或不支持前三优先要求。
- 最多两个案例的默认限制继续有效，但不能因 evidence 原始顺序或同分时先出现而静默排除更相关的近期经历。
- 对同分候选，按“重点要求覆盖增量 → 目标环境相关性 → 新近程度 → 事实具体度”比较；历史 outcome 只作最后一级 tie-break。
- Plan trace 至少记录候选 ID、支持的重点要求、直接/相邻分类、环境关联依据、任期、具体度、最终选择及排除理由。
- Reviewer 检查存在更强且互补的未选案例时，报告 `cover_evidence_under_utilized`；修复只重新选材和重写受影响段落。

## 5. P1 需求

### Q8：技能去重、页面标签与信息预算

- Key Skills 和 Technical Skills 中完全相同或规范化后等价的条目只保留一次。
- Key Skills 优先表达岗位能力；Technical Skills 只保留已确认的软件、系统和技术工具。
- 不将“records management”能力与具体 Records Management System 自动视为同一事实；系统名称未确认时不得升级为具体产品。
- `References available upon request` 默认省略，只有地区惯例、雇主要求或用户明确选择时保留。
- 招聘页面明确提供的技能标签作为 `advertised_skill_tags` 保存，保留原文、页面区域和来源类型，不与正文明确要求混为一类。
- 每个标签分别匹配候选人证据，标记为 `direct / adjacent / gap`。只有 direct 标签可以按原意进入 Key Skills；adjacent 标签必须使用候选人实际做过的较窄表达，不能原样升级；gap 不进入 Key Skills。
- 页面标签与正文要求冲突时，以正文明确要求为准，并在 Job Model 中保留冲突说明。
- Project Delivery、Project Management、Contract Management、Data Analysis 必须逐项处理，不能因为它们位于页面元数据区域而整体遗漏，也不能为了覆盖标签把供应商协调自动升级为 Contract Management。

### Q9：Cover Letter 收件人与雇主标签

- 有可靠招聘机构或联系人名称时，优先生成针对性称呼及正确组织名称。
- 只有笼统雇主类别时，可使用中性称呼；不得把内部短标签或低质量提取值直接写入抬头。
- `Dear Sir/Madam`、通用组织标签和职位复数形式属于 advisory，除非与 JD 明确身份冲突。

## 6. 用户提示与状态

用户可见提示先说明结论和影响，再给操作：

- `concise_but_relevant`：**“这份简历较精炼，相关经历已覆盖，可继续检查或导出。”**
- `generation_under_utilized`：**“原始简历中还有可用于该岗位的具体信息，当前草稿未充分使用。重新优化草稿。”**
- `insufficient_source_detail`：**“以下经历缺少可确认的工作对象、工具或范围。补充你实际做过的信息可以提高针对性。”**
- `unsupported_availability_claim`：**“求职信包含尚未确认的到岗时间或通知期。请确认该信息或删除表述。”**
- `availability_conflict`：**“文档中的到岗时间与已确认信息不一致。请使用已确认值或删除该表述。”**

提示中不得向用户展示 `resume_quality [MAJOR]`、内部 evidence ID、比例或调试字段。技术详情可折叠查看。

## 7. 验收用例

| ID | 给定与操作 | 预期结果 |
|---|---|---|
| V21-01 | 一个岗位原段落含 5 条各少于 20 词的职责，合计有充分行动和对象；导入后被拆分 | 按完整岗位段落判定不薄；不得要求用户补资料 |
| V21-02 | 一个岗位只有“负责日常行政工作” | 判 `insufficient_source_detail`，指出该岗位可补充的事实类型 |
| V21-03 | 451 词、事实覆盖充分、无重大重复的 CV，目标 650 词 | Writing quality 不因少于 455 词失败；可为 `concise_but_relevant` |
| V21-04 | 与 V21-03 相同内容增加 4 个无信息词 | 质量状态不得因跨过 455 词发生改善 |
| V21-05 | 来源有具体对象和工具，成品五段经历均写成通用行政支持 | 判 `generation_under_utilized`，建议系统重写，不提示用户补资料 |
| V21-06 | 多段 bullet 首词相同，但行动对象和背景不同 | 可提示措辞优化，不阻断 Ready |
| V21-07 | Key Skills 与 Technical Skills 重复 Excel、Word、Outlook | 输出只保留一处，或产生明确非阻断去重建议 |
| V21-08 | 无 availability 来源；Cover Letter 写 one month's notice | 判 `unsupported_availability_claim`，阻断 Ready；不得根据 CV 任期猜测正确值 |
| V21-09 | 用户明确确认仍需一个月通知期 | 文档使用或合理省略该值，不因 CV 任期表面结束误报 |
| V21-09A | 用户确认可立即到岗；Cover Letter 写 one month's notice | 判 `availability_conflict`，引用两个冲突值 |
| V21-09B | Profile 为 `not_specified`，模型写 `I am available following one month's notice.` | 确定性整理删除该句；审核仍能检出未删除的同义表达 |
| V21-09C | Profile 为 `one_month`，文档使用固定 one-month 句子 | 事实检查通过；trace 显示该枚举、Profile 版本和更新时间 |
| V21-09D | Profile 从 `one_month` 更新为 `not_specified` | 既有文档保留并标记基于旧 Profile；新生成不再写通知期 |
| V21-10 | 用户未提供 availability | 两份文档均可省略，不自动写 immediately available |
| V21-11 | JD 有 Randstad 等可靠招聘方名称 | Cover Letter 不输出内部短标签作为组织名称 |
| V21-12 | 完整来源无法还原，但存在拆分职责 | 标记解析不确定；不得直接归因用户资料不足 |
| V21-13 | 页面发布方为 Randstad Professionals，正文说明代表多个 WA State Government departments 招聘 | Job identity 分别保存 advertiser、hiring organisation 和关系；抬头使用可靠 display name，不输出 `WA gov` |
| V21-14 | 解析得到 `WA gov` 与 `Randstad Professionals` 两个名称候选 | trace 保留两处原文、置信度和选择理由；低置信度短标签不进入最终抬头 |
| V21-15 | Finance Administration Officer 是最新经历，直接支持至少一个 Cover Letter 首要要求且有政府环境依据 | 进入最终候选比较；选择或排除均有具体可读理由 |
| V21-16 | 较早 Chevron 案例比最新经历更具体且覆盖不同首要要求 | 允许选择 Chevron，但 Plan 同时说明 Finance 经历是否作为第二案例及未选原因 |
| V21-17 | 三个岗位分别生成近似相同的 `Provided administrative and project support` bullet | Reviewer 检出跨 entry 语义重复；来源有差异化事实时判 `generation_under_utilized` |
| V21-18 | 多个 bullet 使用相同常见动词，但对象、范围和工作内容不同 | 不因首词重复失败 |
| V21-19 | 页面技能标签包含 Project Delivery、Project Management、Contract Management、Data Analysis | 四项均进入 Job Model 并分别完成 direct/adjacent/gap 分类 |
| V21-20 | 候选人只有供应商协调和审批支持，无合同管理职责 | Contract Management 不得原样写入 Key Skills；可使用有来源的较窄表达或标记 gap |

回归要求：同时通过 V2 A01–A24、Relevance/Timeline Test 1–15、Cover Letter 雇主白名单与任期时态检查。V21-03、V21-04 必须成对执行，防止重新引入硬词数边界。V21-13 至 V21-20 使用本次 JD 和履历的授权脱敏 fixture，不能只用抽象模拟字段。

## 8. Career Ops 同样本基准

### 8.1 基准版本与材料

- Career Ops 仓库：`career-ops-hq/career-ops`
- 对比时 `main` HEAD：`843d179624e09c5b9bc9ec372a2b21dd338ef94e`
- Career Ops 成品：两页 CV PDF（约 599 词）与一页 Cover Letter PDF（约 332 词）。
- 当前产品成品：Tailored CV v135 DOCX（约 509 词）与 Cover Letter v137 DOCX（约 283 词）。
- Career Ops PDF 已完成逐页视觉检查；当前产品 DOCX 已完成正文、样式和页面设置检查，但本机缺少可用的 Word/LibreOffice 渲染器，尚未完成逐页视觉对照。

限制：本轮没有收到双方实际使用的 Master Resume、完整 JD、Profile 快照和 Career Ops 交互答案，不能证明所有输入完全一致。Career Ops 输出中的具体日期、Police Clearance、White Card、Dayforce、人数和规模只能作为待核对的成品事实；进入正式黄金样本前必须回到共同输入确认来源。

### 8.2 内容差距

| 维度 | Career Ops 样本 | 当前产品 v135/v137 | 需求结论 |
|---|---|---|---|
| 时间线 | 展示 Finance 后 2019–2026 的 Mable、My Support、Sodex/Woolworths/Puma、Core Color 与 Amazon 经历 | Finance 后直接跳至 2017–2019 Avaintec，形成约七年可见空档 | 必须执行 Relevance/Timeline 专项规则；低相关经历可压缩，不能整体消失造成假空档 |
| 具体事实 | 保留 Dayforce、3–5 家供应商、约 10 人/三个月国际访问、超过 10,000 居民、CRM 订单及证书 | 多数被通用行政、记录和协调语句替代或遗漏 | 规划先保留独特事实，再压缩通用职责；遗漏需判 `generation_under_utilized` |
| 最新政府经历 | CV 和 Cover Letter 均优先使用 Department of Communities | v135 CV 和 v137 Cover Letter 已使用 Finance Administration Officer | 该项较上一版已改善，纳入回归防止退化 |
| 案例广度 | Cover Letter 使用政府支持、会议协调、基础设施项目和多项目供应商四个扫描友好的案例 | v137 使用政府支持和 CCCC 两个较长案例 | 默认 2–3 个案例；案例数服务于岗位重点，不能机械复制 Career Ops 的四条结构 |
| 重复度 | 仍有 Provided/Maintained，但由人数、系统、对象和规模形成差异 | 多个岗位重复通用句式，差异主要靠雇主名称 | 执行 Q4 的语义差异检查，不以首词去重代替事实差异 |
| Availability | Career Ops 写明具体可工作日期；来源待核对 | Profile `one_month` 固定句进入 Summary 和 Cover Letter | 两者均必须回到确认来源；CV Summary 默认不放 availability，除非用户明确选择 |
| 招聘身份 | Career Ops 使用中性的 Government Recruitment Team | v137 使用 Talent，较旧版 `WA gov` 有改善 | Q6 仍需保留 advertiser/client/display name 的来源和选择 trace |
| 求职信语气 | 具体、易扫描，但 White Card/Police Clearance 段落较长 | 更传统、简洁，但个体差异较弱 | 借鉴结构清晰度；不复制无来源承诺或把求职信写成要求清单 |

### 8.3 本样本必须保留的回归事实

以下内容只有在共同输入确认后才进入黄金事实清单；确认前不得作为生成系统的事实来源：

- Finance Administration Officer：WA Government、Dayforce、journals/reconciliations、报告与记录。
- Avaintec：约三个月、约 10 人的国际访问，以及政府、医院和供应商协调。
- CCCC Kenya：约三家供应商，水、电和土地申请流程。
- Chevron CDB：影响超过 10,000 名社区居民的社会经济调研背景。
- Pratt & Whitney：同时协调三至五家供应商并跟踪项目计划。
- 2019–2026 连续经历、Certificate III、CRM supplier-order processing。

同一事实经确认后，当前产品必须保留、合理压缩或给出可审计省略理由；不得只因为 evidence 数量、固定 bullet 上限或通用职责占满篇幅而删除。

### 8.4 Career Ops 对标模板规范

本期新增一个面向行政、政府和项目支持岗位的 `career_modern` 模板。目标是达到参考样本的信息层级、专业度和可扫描性，不复制 Career Ops 的品牌名称。Career Ops 仓库采用 MIT License；如直接复用其模板代码，工程必须保留许可证和归属说明。

#### CV 模板

- A4、单栏、ATS 阅读顺序从上至下；本样本目标两页。
- 第一页顶部使用突出姓名、单行联系方式和细分隔线；不显示内部状态或无来源标签。
- 一级章节使用统一大写标题、细分隔线和克制的强调色。
- Core Competencies 使用紧凑标签视觉，但底层文本顺序连续可提取；窄屏或 DOCX 不稳定时可降级为同行短语列表。
- 工作经历中组织名称与职位形成两级层次，日期右对齐；跨页不重复姓名大标题，不把单个岗位标题留在页底。
- 重点岗位保留 4–6 条具体 bullet，压缩经历保留 0–1 条；总篇幅由事实价值和两页预算决定。
- Education、Certifications、Skills 分区清楚；空章节完整移除。
- 不使用多栏正文、图标字体、技能进度条、头像或可能破坏 ATS 提取顺序的浮动元素。

#### Cover Letter 模板

- A4 单页，使用与 CV 相同的姓名、联系方式、字体体系和基础间距。
- 视觉更克制：黑色正文、清晰标题、招聘方/地点/日期元数据行和顶部细分隔线。
- 正文保持左对齐；允许 2–4 个带粗体能力标签的短 bullet，但默认仍可使用自然段结构，由内容规划选择。
- 称呼、招聘方、职位、日期和落款不得使用占位符或低置信度组织简称。
- 一页容纳不了时先删除重复和低价值说明，再调整间距；正文不得小于可读下限。

#### 设计 token 与跨格式一致性

工程实现前冻结一份可版本化 token 表，至少包含：A4 尺寸、页边距、姓名/章节/岗位/正文/辅助文字字号、字体回退、行距、段前后距、bullet 缩进、分隔线、主文字色、章节强调色、岗位强调色和标签样式。

- 参考方向：深色正文，青色章节标题，紫色组织名称；颜色只用于层级，不承载唯一语义。
- PDF 与 DOCX 使用同一内容版本和模板 token；允许渲染能力造成的小差异，但章节顺序、文本、日期、分页目标和视觉层级一致。
- 字体不可用时使用定义好的回退字体，不能静默改变字号造成溢出。
- 模板选择属于导出设置，只重新导出所选正文，不重新调用模型。

### 8.5 模板与同样本验收

| ID | 给定与操作 | 预期结果 |
|---|---|---|
| CO-01 | 用确认后的共同输入重新生成本样本 CV | 所有黄金事实被使用或有可接受省略理由；2019–2026 不出现人为七年空档 |
| CO-02 | 使用 `career_modern` 导出 CV PDF | A4 两页、单栏、无截断重叠；姓名、章节、组织、职位和日期层级与参考样本同等清楚 |
| CO-03 | 同一正文导出 DOCX 与 PDF | 文本与事实一致，ATS 提取顺序一致；模板差异不触发内容重写 |
| CO-04 | 导出 Cover Letter | A4 一页、正文左对齐、招聘身份正确、最新政府案例出现或有明确排除理由 |
| CO-05 | 删除 Certifications 或 Skills 数据后导出 | 整个空章节及其间距被移除，无孤立标题 |
| CO-06 | 字体不可用或长组织名换行 | 使用预设回退，日期和正文不重叠，组织与职位关系仍清楚 |
| CO-07 | 对 PDF 和 DOCX 执行 ATS 提取 | 姓名、联系方式、章节、岗位、日期和 bullet 顺序与视觉阅读顺序一致 |
| CO-08 | 两位评审盲看 Career Ops 与当前产品成品 | 当前产品在内容完整性、岗位针对性、具体性、可读性和视觉专业度五项均不低于参考样本，且无需实质修改即可投递 |
| CO-09 | Career Ops 输出含共同输入未确认的具体日期或资格 | 不复制到当前产品；标记待确认或省略，事实审核通过后才能 Ready |

本样本通过不是全产品发布结论。完成后仍按 V2 第 10 节至少六组材料运行盲评，并分别报告内容与模板得分。

## 9. 指标与发布门槛

用至少 20 份经人工标注的真实或授权脱敏 CV 运行本检查，必须包含职责分行导入、材料充足但精炼、真实材料稀疏和跨文档 availability 四类样本。

- `insufficient_source_detail` 阻断误报率 ≤ 5%。
- 人工标注为“材料充足但生成未利用”的样本，责任归因准确率 ≥ 90%。
- availability 明确冲突检出率 100%，无来源推断到岗承诺为 0。
- 因单纯词数跨过阈值造成的质量状态翻转为 0。
- 事实错误和责任升级继续执行 V2 的零容忍门槛。

## 10. 实施顺序

1. **P0.1**：修正来源段落聚合和词数阻断逻辑，回归当前真实样本。
2. **P0.2**：增加三类责任归因及对应用户提示。
3. **P0.3**：拆分招聘主体身份，修复 display name 和 trace；对本次 JD 重新解析并记录 `WA gov` 的原始来源。
4. **P0.4**：修正 Cover Letter evidence 比较与排除理由，确认 Finance Administration Officer 的实际选择链路。
5. **P0.5**：增加跨文档 availability 一致性检查。
6. **P0.6**：用真实生成结果验证语义差异检查，再决定启发式或现有 reviewer 实现。
7. **P1.1**：页面技能标签、技能去重、References 默认策略和称呼优化。
8. **P1.2**：实现 `career_modern` CV/Cover Letter 模板及跨格式视觉、ATS 验收。

每阶段保留修改前后的生成正文、质量检查结果和来源引用。不得以让失败消失为目标删掉检查；必须证明误报下降且真实不足仍被检出。

## 11. 自审结论

本 PRD 是对 V2 的小范围质量校准，问题、责任边界和验收输入均来自一次真实生成结果，范围可直接拆分实现。P0 无需新增服务或依赖，优先复用现有 CKB、Resume Plan、reviewer 与跨文档事实检查链路。

进入开发前唯一需要冻结的是 availability 的权威来源字段及其用户编辑入口；在该字段完成前，可以先实现“无来源通知期不得写入”和当前样本回归，其余 P0 不受阻塞。
