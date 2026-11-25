# Colophon

标签优先的本地文献阅读工作台。单人、离线优先、自带模型 Key。

当前是**可运行骨架**：书库、分面筛选、标签 / 状态 / 笔记写回 SQLite，内置 PDF 阅读器，AI 问答与标签建议已接真实模型。v1 不做 PDF 高亮与批注。

## 运行

环境用 conda，名字和项目一致，叫 `colophon`。第一次先建环境装依赖：

```powershell
cd D:\Python_project\colophon
conda create -n colophon python=3.11 -y
conda activate colophon
python -m pip install -r requirements.txt
```

之后每次运行，两种起法，默认和 exe 完全一致：

```powershell
conda activate colophon

# 桌面窗口（日常用这个，双击 exe 走的也是这一条）
python run.py

# 浏览器（开发时方便开 DevTools，加 --reload 还能热重载）
python run.py --browser
```

桌面窗口是 WebView2（Edge 内核）套在 FastAPI 外面，不经过浏览器标签页；`COLOPHON_DEBUG=1` 可以开 DevTools。

PowerShell 如果没执行过 `conda init`，`conda activate` 会报错，这时直接用环境的绝对路径：

```powershell
D:\Anaconda\envs\colophon\python.exe run.py
```

## 分发给同事（Windows 绿色包）

同事不需要装 Python，也不需要拉仓库。打一个 zip 就行：

```powershell
.\build.ps1
```

产物是 `dist\Colophon-windows-x64.zip`（约 40MB）。解压后双击 `Colophon.exe` 直接开窗口，不经过浏览器；关窗即退出。exe 的入口就是 `run.py`，和 `python run.py` 是同一条代码路径。

- 数据落在 exe 同级的 `library/`，整个解压目录可以搬走；`COLOPHON_LIBRARY` 仍然优先级最高。
- 再次双击不会开第二个副本，会把已开着的窗口提到前台。
- 8765 端口被占用时自动顺延到 8766、8767……普通用户只会看到窗口，端口只是内部细节。
- 首次运行 Windows 会提示「已保护你的电脑」，点「更多信息 → 仍要运行」；未签名程序的常规提示，要消掉得买代码签名证书。
- 起不来时看 `library/colophon-error.log`，运行日志在同目录 `colophon.log`。
- 每个同事填自己的模型 Key，Key 只写进他本地的 `library/library.db`。

发版：`git tag v0.1.0; git push origin v0.1.0`。GitHub Actions 会在 windows-latest 上自动打包并把 zip 挂到 Release（`.github/workflows/release.yml`），同事直接从 Releases 页下载。当前只覆盖 Windows；要支持 macOS 需要另加 macos runner（PyInstaller 不能交叉编译）。

打包相关文件：`Colophon.spec`（onedir 配置，`web/`、`docs/`、`app/schema.sql`、PyMuPDF 原生库和 pywebview 的 WebView2 组件都已收集）、`build.ps1`（本地构建 + 压缩）、`packaging/HOW-TO-USE.txt`（随 zip 发给同事的说明）、`packaging/make_icon.py`（生成 `web/favicon.ico`，窗口 / 任务栏 / exe 图标共用）。

## 现在能用

- 空库启动：首次启动只建库，不写任何演示数据；书库为空时列表中间直接给「导入 PDF」入口。
- 导入 PDF 后即可筛选、打标、阅读；元数据抓取是单独动作，不阻塞导入。
- 左栏分面筛选：标签 / 年份 / 作者 / 单位，实时计数。
- 全局搜索、排序、深浅色。
- 标签增删、标签合并（标签库里手动填旧写法 → 保留写法）、阅读状态、笔记、一句话记忆 → 写回 SQLite。
- 导入 PDF：复制进 `library/files/`、按 SHA-256 去重、标记「待补全」；非 PDF 直接拒绝。
- 列表顶部有「待补全 N」筛选，只看等待补元数据的条目。
- 内置 PDF 阅读器（PDF.js，本地文件，无需联网）：连续滚动、翻页、缩放、适应宽度、旋转，记住上次页码。
- 条目没有 PDF 时，阅读器里可以直接「选择 PDF 文件」补上；原文件不会被移动。
- 打开 PDF 会自动把「待读」改成「在读」。
- 元数据抓取：先读 PDF 内嵌信息（标题 / 期刊 / DOI / 专利号），再从前两页文本和文件名识别 DOI、arXiv、WO/EP/US/CN 专利号，走 arXiv、Crossref、OpenAlex 补全；完全没有编号时按标题反查 Crossref（相似度不够就放弃，宁缺勿错）。
- 扫描件兜底：没有文本层或抽不出字的 PDF，单篇「抓取元数据」会把首页渲染成图片交给已配置的模型识图；有文本层则只发文本。模型只负责读，读到的 DOI / arXiv / 专利号会回源核对；模型补出来的条目保留「待补全」，手动确认后才清掉。
- 「待补全」里可以单篇「抓取元数据」、手动补全（标题 / 作者 / 年份 / 期刊 / DOI / arXiv / 摘要），或「补全全部」批量跑（批量只跑规则，不逐篇等模型；扫描件请单篇抓取）。
- AI 范围问答：默认只读当前筛选，可显式扩到全库；检索摘要与 PDF 页后调用你配置的 OpenAI 兼容模型，回答用 `[n]` 标出处，点引用直接打开原文/页码。
- 模型设置：点右上角**「设置」**按钮（齿轮图标），填接口地址、模型名、API Key；Key 只写本地 `library/library.db`。也支持指向 Ollama / LM Studio 的 `/v1`。
- 对话按「当时的 item_ids」存库；刷新页面会在范围一致时恢复最近一段对话。
- AI 标签建议：在「标签」页点「用模型提候选」，模型读标题 / 摘要 / 已有标签，给出 3–4 个带理由的候选；采纳才写入，忽略不落库。

## 已验证

- 空库启动：`/api/items` 返回 0 条，界面显示「书库还是空的」与导入入口。
- 点标签筛选 → 列表与计数即时更新；清除后恢复全库。
- 阅读状态切换、笔记 / 一句话记忆（600ms 防抖）写回 SQLite；刷新后仍在。
- 标签加 / 删 / 合并写回 SQLite；合并后旧名记为别名。
- 导入：正常 PDF 入库并进「待补全」队列；同文件第二次被识别为重复；非 PDF 返回错误。
- 阅读器：真实论文滚动渲染、翻页、缩放（相对适应宽度）、旋转、关闭后重开回到上次页码。
- 元数据：真实 arXiv 论文 → 抓取到作者、年份、arXiv 出处、摘要与机构（arXiv + OpenAlex）；无编号的 PDF 现在会先尝试标题反查和模型读首页，仍不行才提示手动补；DOI 冲突时不覆盖已有条目。
- 元数据三级流水线：内嵌信息直接命中 IEEE 论文（2005 / DOI）与 WO 专利（2009）；无文本层的专利首页能渲染成 PNG；用本地假模型验证了「识图补标题作者 → 保留待补全 → 手动确认清除」「文本模式请求体」「模型读到的 DOI 回源核对」和「标题反查相似度阈值」。
- 手动补全：PATCH 保存后按缺失字段自动更新「待补全」标记。
- AI 问答：用本地假模型验证了检索 → 调用 → 引用重编号 → 对话存库 → 刷新恢复的整条链路；无 Key 时禁止提问并指到设置。
- AI 标签建议：验证了生成 → 采纳写库 → 候选消失；与已有标签重复的会被服务端过滤；无 Key 返回 400 并提示配置。
- 绿色包：`dist\Colophon-windows-x64.zip` 解压后双击 exe 直接开窗口（标题 Colophon、带应用图标），页面、PDF.js 与 `/api/items` 都正常加载，书库建在 exe 旁；导入 PDF 成功，元数据抓取读到 PDF 内嵌的 WO 专利信息（说明打包后 PyMuPDF 原生库可用）；8765 被占用时自动换到 8766；关窗后进程退出、不留残余；重复双击只保留一个窗口和一个服务。
- 桌面窗口里的 PDF.js：探针实测 pdf.js 3.11 + WebWorker 在 WebView2 中打开 22 页论文，`getPage` 1ms、整页渲染 312ms，画布有真实像素；`localStorage`（上次页码、最近对话）在 `private_mode=False` 下落盘可复用。注意窗口被隐藏时 Chromium 会暂停 rAF，渲染要等窗口显示。 

## 还是占位

- 文本层与文本选择（因此暂时不能复制原文、不能高亮；高亮按你的决定留到以后）。
- 对话历史列表与「范围已变，重跑」提示；目前只恢复最近一段对话。
- 标签建议没有缓存，重复点会重新请求模型。
- 元数据抓取的缓存与限速；目前每次都直连源站。模型读首页也没有缓存，失败后要手动再点一次。

## 结构

```
app/        FastAPI + SQLite + 元数据抓取（app/metadata.py）+ 范围问答与标签建议（app/ai.py）
web/        前端单页 + PDF.js（web/vendor/pdfjs）+ 图标 web/favicon.ico
docs/       v1 规格、三条流程、组件清单
library/    运行时书库（自动创建，已 gitignore）
run.py      唯一入口：默认桌面窗口，--browser 走浏览器
Colophon.spec  PyInstaller 打包配置（onedir 绿色包，默认窗口版）
build.ps1   本地打包脚本 → dist\Colophon-windows-x64.zip
packaging/  随包说明 + 图标生成脚本
```

- 规格：`docs/v1-spec.md`
- 流程板：`docs/flows.html`（运行后访问 <http://127.0.0.1:8765/design/flows.html>）
- 组件与状态：`docs/ui-inventory.md`

## 数据目录

默认根目录是 `library/`，可以用环境变量 `COLOPHON_LIBRARY` 指向别处：

```powershell
$env:COLOPHON_LIBRARY = "D:\Papers"
```

整个目录可以搬走；备份就是拷贝它。

## 下一步

1. 对话历史列表 + 范围变化时的「重跑」提示。
2. 标签建议加缓存与「一次生成全部未打标」。
3. 接文本层：文本选择与复制（为以后的高亮留接口，但 v1 不上高亮）。
4. 元数据抓取加缓存 / 离线标记，避免重复请求源站。
5. 桌面窗口收尾：窗口尺寸/位置记忆、右键菜单与下载行为确认、安装器（Inno Setup）与代码签名。
