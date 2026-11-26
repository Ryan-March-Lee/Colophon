# Colophon · 界面组件与状态清单

对应 `gewu-flows.html` 的 12 个屏。只列组件、状态和数据动作，不列视觉细节。

---

## 0. 全局框架

**AppShell**：TopBar + 左栏 + 列表 + 详情；Toast、Modal、Drawer 挂在最外层。

| 断点 | 布局 |
|---|---|
| ≥ 1180 | 三栏：左栏 246 / 列表自适应 / 详情 428 |
| ≤ 1080 | 两栏：左栏变抽屉，列表 + 详情 |
| ≤ 880 | 单栏：详情全屏覆盖，带返回 |

**全局状态（任何屏都可能遇到）**

- 首次启动：选择书库根目录。
- 空库：只显示导入引导。
- 索引中：可读、可打标，AI 入口显示「索引中」。
- 文件缺失：条目卡片标记，详情给「重新定位」。
- 无 AI Key：AI 面板给设置入口，不报错。
- 模型失败 / 超时：保留问题，可重试。
- 磁盘满 / 无权限：阻断写入，明确说明原因。
- 离线：本地功能全部可用，AI 标记不可用。

---

## 1. 共享组件

| 组件 | 用在哪 | 必须处理的状态 |
|---|---|---|
| `TopBar` | 全局 | 搜索聚焦 / 导入中 / 索引中 |
| `SearchInput` | 全局 | 空、输入中、无结果、快捷键 `/` |
| `FacetGroup` | 左栏 | 展开、折叠、已选 N、全部 0 |
| `FacetRow` | 左栏 | 未选、已选、计数 0 禁用、悬停 |
| `YearHistogram` | 左栏 | 未选、已选、0 篇、悬停计数 |
| `SelectedChips` | 列表顶 | 无筛选、有筛选、清除全部 |
| `PaperCard` | 列表 | 默认、选中、悬停、无标签、文件缺失 |
| `StatusPill` | 列表 / 详情 | 待读、在读、已读 |
| `TagChip` | 全局 | 默认、可删、可筛选、相似提示 |
| `PdfViewer` | 阅读 | 加载、渲染、无文本层、文件缺失、缩放、旋转 |
| `PageNav` | 阅读 | 首页 / 中间 / 末页、跳页、记住位置 |
| `MetaBar` | 详情 | 完整、待补全、抓取中、抓取失败、DOI 冲突 |
| `MetaEditor` | 补全弹层 | 空字段、保存中、保存失败、DOI 重复 |
| `SideSheet` | 打标 / 详情 | 打开、收起、窄屏全屏、不锁滚动 |
| `TagEditor` | 阅读（仅右侧栏） | 空、输入、补全、新建、重复、保存中 |
| `TagSuggestCard` | 阅读（仅右侧栏） | 生成中、待采纳、已采纳、已忽略、失败 |
| `ScopeBar` | AI | 筛选范围 N、全库、0 篇、已扩范围 |
| `ChatComposer` | AI | 可用、0 篇禁用、发送中、无 Key |
| `MessageList` | AI | 空、流式、完成、错误、无引用 |
| `CitationChip` | AI | 可跳转、未定位、页码缺失 |
| `EvidenceCard` | AI / 阅读 | 段落预览、跳转、定位失败 |
| `SavedViewMenu` | 左栏 / 列表 | 保存、重命名、删除、脏状态 |
| `ConfirmDialog` | 全局 | 合并、删除、扩范围、清空 |
| `Toast` | 全局 | 成功、警告、错误、可撤销 |
| `MissingFileBanner` | 详情 | 丢失、重定位中、已恢复 |

---

## 2. 逐屏清单

### 导入

| 屏 | 主要组件 | 必须状态 | 数据 / 动作 |
|---|---|---|---|
| 1 投放 | `DropZone`、`FilePicker`、`DoiInput`、`TypeHint` | 空闲、拖拽中、类型不支持、扫描版提示 | 读取类型；建导入任务；不做写入 |
| 2 批量识别 | `BatchSummary`、`BatchRow`、`StatusBadge`、`CancelButton` | 识别中、可入库 / 待补全 / 重复、部分失败 | 哈希去重；抓 arXiv / Crossref / OpenAlex；只列出，不写库 |
| 3 托管入库 | `ProgressBar`、`FilePathText`、`BackgroundTask` | 复制中、索引中、失败回滚、磁盘不足 | 复制、命名、写 `file` / `item` |
| 4 待补全 | `MetaBar`、`MetaEditor`、`FetchAll`、`MissingFields` | 全部完成、待补全 N、抓取中、抓取失败、手动保存失败 | 单篇 / 批量抓取；手动改元数据 |

### 读完打标

标签只有一个写入口：阅读器右侧栏。详情页的标签页只读，未打标工作台只做队列。

| 屏 | 主要组件 | 必须状态 | 数据 / 动作 |
|---|---|---|---|
| 1 阅读中 | `PdfViewer`、`PageNav`、`ZoomControl`、`ReaderSidePanel` | 加载中、渲染失败、无文本层、文件缺失、无托管 PDF（只凭标题打标）、保存失败 | 记录页码；侧栏打标 |
| 2 侧栏打标 | `TagEditor`、`MetadataChips`、`QuickTags`、`GroupPicker` | 空标签、相似标签、新建组（组名空要拦）、保存中 | 写 `item_tag`，组归属随标签走 |
| 3 一句话记忆 | `MemoryInput` | 空、输入中、保存失败 | 写 `memory`（防抖 600ms） |
| 4 AI 建议 | `TagSuggestCard` | 无 Key、生成中、待采纳、已采纳、已忽略、失败 | 采纳写 `item_tag` 且落未分组 |
| 5 未打标队列 | `QueueCard`、`MetaBar`、`NextButton`、`SkipButton` | 队列空、元数据缺失、无 PDF | 只路由：进阅读器打标 / 跳过 / 退出 |
| 6 回流 | `FacetCounts`、`Toast`、`ViewLink` | 已保存、计数更新、保存失败 | 刷新左栏与智能视图 |

### 范围问答

| 屏 | 主要组件 | 必须状态 | 数据 / 动作 |
|---|---|---|---|
| 1 锁范围 | `ScopeBar`、`FilterChips`、`ChatComposer` | N > 0、N = 0、无 Key | 解析 item_ids；0 篇禁用输入 |
| 2 扩范围 | `ConfirmBar`、`CancelButton` | 待确认、已扩、已缩回 | 写范围快照；不静默扩 |
| 3 回答 | `MessageList`、`CitationChip`、`EvidenceCard` | 流式、完成、无证据、引用未定位、错误 | 检索 → 生成；挂 chunk / page |
| 4 复现 | `ConversationCard`、`RerunNotice` | 新鲜、过期、重跑中、只读 | 读 `scope_json`；重跑生成新版本 |

---

## 3. 交互约定

- `/` 聚焦搜索；`Esc` 收起侧滑 / 关闭弹层；`Cmd/Ctrl + Enter` 发送。
- 侧滑不锁滚动；只有破坏性动作（合并、删除、扩范围）才用确认弹层。
- 所有异步动作都有「进行中 / 失败 / 重试」三种可见状态。
- 所有空状态都带一个下一步动作，不只说「没有数据」。
- 引用点击 = 打开原文献并定位；定位失败只标「未定位」，不猜。
- 任何自动行为（AI 建议、自动合并、扩范围）都必须可撤销或需确认。
- 标签只有阅读器右侧栏一个写入口；详情页标签页与未打标队列都不写 `item_tag`。

---

## 4. 开骨架的 P0 组件

先做这 10 个，其它都可以后补：

1. `AppShell` + `TopBar` + 三栏布局
2. `FacetGroup` / `FacetRow` / `SelectedChips`
3. `PaperCard` + 列表虚拟滚动
4. `DetailPane` + `ViewTabs`
5. `PdfViewer` + `PageNav` + `ZoomControl` + `ReaderSidePanel`
6. `TagEditor` + `TagChip` + `QuickTags`（只在阅读器侧栏）
7. `ImportDialog` + `BatchSummary` + `QueueList`
8. `ScopeBar` + `MessageList` + `CitationChip`
9. `Toast` + `ConfirmDialog`
10. `Settings`（AI Key、根目录、备份）

P1：年份柱状图、智能视图、标签库合并、批量补全、索引进度。
P2：快捷键、主题、拖拽排序、导出。
