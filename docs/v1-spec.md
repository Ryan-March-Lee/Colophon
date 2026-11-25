# Colophon · v1 规格

> 标签优先的本地文献阅读工作台。单人、桌面、离线优先、自带模型 Key。
> 本文只定「最小功能集」和「数据模型」，不涉及具体技术选型与排期。

---

## 0. 已定决策

| # | 问题 | 决定 |
|---|---|---|
| 1 | 年份 / 作者 / 单位 | 结构化属性，自动填充；标签只放用户概念 |
| 2 | 标签层级 | 扁平 + 改名 / 合并，不做父子层级 |
| 3 | PDF 阅读器 | 内置只读渲染；v1 不做高亮 / 批注，留到以后 |
| 4 | AI 范围 | 默认当前筛选；可显式扩到全库并标明 |
| 5 | 模型接入 | 用户自带 OpenAI 兼容 Key；可选本地模型；v1 把 Key 存在本地 `library.db`，接口只返回「已保存」 |
| 6 | 外部文献管理器 | 不集成（包括 Zotero；只借鉴思路） |
| 7 | 浏览器插件 | v1 不做 |
| 8 | 保存筛选 | 进 v1，作为「智能视图」 |
| 9 | AI 对话 | 随「范围快照」保存 |
| 10 | 引用导出 | BibTeX / RIS + 复制（GB/T 7714） |

默认值（不再单独决策）：

- 标签字段：名称、颜色、一句话定义、自动计数。
- 标签输入：补全已有标签，但允许新建。
- 笔记：每篇一个 Markdown 笔记；v1 不做 PDF 高亮。
- 阅读状态：待读 / 在读 / 已读 + 星标。
- 未打标：一个虚拟视图，不是分类。
- AI 建议：按需生成，不自动跑；采纳才写入。
- 批量导入：先入库、后补全，只在重复 / 冲突 / 低置信度时打断。
- 打标提示：已读时从右侧侧滑，不遮挡、不阻断。

---

## 1. 最小功能集（8 项）

### 1. 导入与文件托管
- 拖入 PDF、批量导入一个文件夹、输入 DOI / arXiv ID。
- 计算文件哈希去重；复制进 `files/`。
- 元数据顺序：PDF 内嵌 DocInfo（标题 / 期刊 / DOI / 专利号）→ DOI / arXiv / 专利号回源（arXiv、Crossref、OpenAlex）→ 标题反查 Crossref（相似度足够才采纳）→ 模型读首页文本；扫描件渲染首页识图。模型只负责读，读到的编号回源核对。
- 字段齐 → 直接可用；模型补出的字段保留「待补全」直到手动确认；缺字段 / 抓不到 → 进「待补全」，可以单篇或批量补（批量只跑规则，不逐篇调模型）。
- 只在重复、冲突、低置信度时打断；原文件夹不动；单篇失败不阻塞整批。

### 2. 条目与元数据
- 列表、详情、编辑。
- 标题、摘要、年份、venue、DOI、arXiv ID、URL、语言。
- 作者与单位结构化保存（含顺序、角色）；v1 依赖元数据源的规范化，手动合并放 v1.1。

### 3. 标签
- 自由输入、补全、颜色、一句话定义、使用计数。
- 改名、合并、删除；合并前显示影响篇数。
- 读完时的打标面板从右侧侧滑，不遮挡原文，可随时收起。
- 「未打标」虚拟视图。

### 4. 检索、分面与智能视图
- 标签 × 年份 × 作者 × 单位 × 阅读状态组合筛选，实时剩余计数。
- 标题 / 摘要 / 全文 / 作者搜索。
- 把当前筛选保存为智能视图（名称 + 筛选快照 + 排序）。
- 内置「待补全」「未打标」两个虚拟视图。

### 5. 内置阅读器
- PDF.js 只读渲染、翻页、缩放、旋转；打开时记住上次页码。
- 导入后抽取全文，供搜索与 AI 使用。
- v1 不做高亮、批注、手写、图形、表单、OCR。

### 6. 阅读工作台
- 阅读状态 + 星标。
- 一句话记忆（用户手写）。
- 每篇一个 Markdown 笔记。

### 7. 范围锁定的 AI 阅读
- 默认范围 = 当前筛选 / 当前智能视图，界面显示「只读这 N 篇」。
- 可显式「扩到全库」，范围状态始终可见。
- 回答带 `[n]` 引用，可跳到具体文献的页码 / 段落。
- 标签建议按需生成，采纳才写入。
- 对话随范围快照保存，可复现「当时那 N 篇」。
- 自带 OpenAI 兼容 API Key；可选本地模型（Ollama 等）。

### 8. 引用导出
- 单篇 / 批量导出 BibTeX、RIS。
- 一键复制 GB/T 7714 格式。
- 其他 CSL 样式放 v1.1。

---

## 2. 数据模型

### 核心表

| 表 | 作用 | 关键字段 |
|---|---|---|
| `item` | 文献条目 | `id`, `type`, `title`, `abstract`, `year`, `venue`, `doi`, `arxiv_id`, `url`, `language`, `status`, `starred`, `memory`, `note_md`, `added_at`, `updated_at`, `deleted_at` |
| `author` | 作者 | `id`, `display_name`, `sort_name`, `orcid` |
| `item_author` | 条目—作者 | `item_id`, `author_id`, `position`, `role` |
| `org` | 单位 | `id`, `name`, `normalized_name` |
| `item_org` | 条目—单位 | `item_id`, `org_id`, `position` |
| `tag` | 标签 | `id`, `name`, `color`, `definition`, `created_at`, `deleted_at` |
| `tag_alias` | 旧名 / 别名 | `alias`, `tag_id` |
| `item_tag` | 条目—标签 | `item_id`, `tag_id`, `source`（user / ai）, `created_at` |
| `file` | 附件 | `id`, `item_id`, `rel_path`, `sha256`, `size`, `mime`, `original_name`, `added_at` |
| `annotation` | 高亮 | `id`, `file_id`, `page`, `rects_json`, `text`, `note_md`, `color`, `created_at` |
| `view` | 智能视图 | `id`, `name`, `filter_json`, `sort`, `created_at` |
| `conversation` | AI 对话 | `id`, `title`, `scope_json`, `created_at` |
| `message` | 对话消息 | `id`, `conversation_id`, `role`, `content`, `cites_json`, `model`, `created_at` |
| `chunk` | 全文切片（派生） | `id`, `file_id`, `page_from`, `page_to`, `text`, `char_start`, `char_end` |
| `embedding` | 向量（派生） | `chunk_id`, `vector`, `model` |
| `setting` | 应用配置 | `key`, `value`（API Key 不在此表） |

### 关键 JSON 字段

```jsonc
// view.filter_json
{ "tags": ["扩散模型", "RLHF"], "years": [2023, 2024], "authors": [], "orgs": [], "status": ["todo"], "q": "" }

// conversation.scope_json
{ "type": "filter", "item_ids": ["itm_01", "itm_02"], "count": 2, "filters": { ... } }
// 或 { "type": "library", "count": 15 }

// message.cites_json
[ { "item_id": "itm_01", "file_id": "fil_01", "page": 3, "chunk_id": "chk_09" } ]
```

### 约束与不变量

1. 所有文件路径存**相对根目录**的路径。
2. `file.sha256` 唯一；重复导入命中已有文件。
3. `item.doi`、`item.arxiv_id` 在非空时唯一。
4. 标签名做规范化（去首尾空格、大小写折叠）后唯一。
5. 合并标签 = 改写 `item_tag` + 写入 `tag_alias`，旧名不会再冒出来。
6. 条目与文件软删除；文件移入 `.trash/`，30 天后清理。
7. `chunk`、`embedding`、全文索引都标记为可重建，丢了不影响书库。
8. API Key 存系统钥匙串，不进数据库、不进备份。

---

## 3. 根目录

```text
<root>/
  library.db        # 唯一书库：条目、标签、笔记、批注、视图、对话、派生数据
  files/            # 所有 PDF，扁平；<id8>_<slug>.pdf
  .gewu/            # 缩略图、日志、临时导出（可删）
  .trash/           # 软删除文件，30 天后清理
  backups/
```

整个 `<root>` 可整体搬迁；备份 = 拷贝根目录（数据库用 SQLite 备份接口，避免热拷贝）。

---

## 4. v1 明确不做

- 同步、多设备、协作、权限。
- 浏览器插件、移动端。
- 任何外部文献管理器集成。
- 扫描版 OCR、知网 / 万方。
- 手写 / 图形批注、表单。
- 标签层级、笔记双向链接。
- 其他引用样式（APA / IEEE 等）。
- 联网检索、网页搜索。
- 内置模型计费。

---

## 5. 下一步

1. 确认本规格与流程板。
2. 按流程板核对组件与状态清单（`gewu-ui.md`）。
3. 定技术栈后开工。
