# mh3_director — MiniMax H3 导演包生成器

不依赖 ComfyUI，就能**离线创作**并导出可被 ComfyUI「MiniMax H3 Director」节点
**Import pack** 直接导入的 `*.mmxpack.zip`。适合：平时不在 ComfyUI 机器旁，也能记录/整理创作想法。

- 主程序：`mh3_director_pack.py`（**纯 Python 标准库，零依赖**，可拷到任意有 Python 的机器离线用）
- 工程目录：`projects/<工程名>/project.json` + `media/`
- 导出目录：`exports/<工程名>.mmxpack.zip`
- 接口：**CLI** 与 **本地 HTTP**（`serve`）

用 ComfyUI 自带 python 运行最省事：
`J:\ComfyUI_windows_portable_amd\ComfyUI_windows_portable\python_embeded\python.exe`

---

## 支持的三种任务模式

| task | 用途 | 每组含义 |
|---|---|---|
| `r2v` | 参考主体生视频 | 每组=一段，带提示词 + 参考图/视频/音频 + 时长（最通用） |
| `fl2v` | 首/尾帧生视频 | 每组=一段，带 start/end 图（任一可空）或纯文本 |
| `v2v` | 视频转视频 | 一个源视频切成多段，每段一个提示词 |
| （兼容 t2v/i2v/rv2v/mixed） | | |

---

## CLI 用法

```bat
:: 建工程（task 默认 r2v）
python mh3_director_pack.py new my_reel --task r2v
python mh3_director_pack.py use my_reel          :: 设为"活动工程"（之后可省 --project）
python mh3_director_pack.py active               :: 查看活动工程

:: 公共提示词 / 公共素材
python mh3_director_pack.py set-common --prompt "<Picture 1> ..." --enable on
python mh3_director_pack.py add-material --file 角色A.png --scope common --mname "角色A"

:: 分组（每段一组）+ 时长（秒，自动换算 17k+5 帧）
python mh3_director_pack.py add-group --gname "开场" --prompt "..." --duration 5
python mh3_director_pack.py set-group --group g458942 --duration 3 --from-prev on
python mh3_director_pack.py del-group --group g458942

:: 分段素材（参考图/视频/音频，或 fl2v 首尾帧）
python mh3_director_pack.py add-material --file 场景.png --scope group --group g458942 --mname "场景参考"
python mh3_director_pack.py add-material --file 首帧.png --scope group --group g458942 --role start
python mh3_director_pack.py add-material --file 尾帧.png --scope group --group g458942 --role end

:: 每段 LoRA（每段 ≤8；name=strength，可重复追加）
python mh3_director_pack.py add-group --gname "开场" --prompt "..." --lora "角色A.safetensors=0.8" --lora "motion.safetensors=1.0"
python mh3_director_pack.py set-group --group g458942 --lora "x.safetensors=0.6"    :: 追加
python mh3_director_pack.py set-group --group g458942 --lora-clear                    :: 清空该段

:: 输出设置（分辨率 / 连续性 / 音频 / 导出）
python mh3_director_pack.py set-output --width 1280 --height 720 --long-edge 1280
python mh3_director_pack.py set-output --continuity on --continuity-overlap 22 --continuity-mode guide --continuity-redraw 0.1
python mh3_director_pack.py set-output --audio-mode generate --ref-image-size match --export-mode all

:: v2v 源视频
python mh3_director_pack.py set-source --file clip.mp4

:: 查看 / 校验 / 报告 / 导出
python mh3_director_pack.py show                 :: 结构化列出（JSON）
python mh3_director_pack.py report               :: 人类可读摘要（纯文本，按分组）
python mh3_director_pack.py validate
python mh3_director_pack.py export --out my_reel.mmxpack.zip   :: 导出，并附带一份文本摘要

:: 导入已有导演包 → 变成可编辑工程
python mh3_director_pack.py import-pack old.mmxpack.zip --name old_reel

:: 提示词优化（草稿+指令 → 候选；--apply 才写回，默认只返回候选）
python mh3_director_pack.py enhance --project my_reel --group g458942 --instruction "加一个特写，压到6秒"
python mh3_director_pack.py enhance --project my_reel --common --instruction "更电影感" --apply
python mh3_director_pack.py enhance --draft "一只猫走过" --instruction "下雨" --backend vlm

:: 从素材反推提示词（本地 VLM；--apply 合并进公共/某段提示词）
python mh3_director_pack.py reverse --project my_reel --file 角色.png --scope common --target h3
python mh3_director_pack.py reverse --project my_reel --file 角色.png --scope common --apply

:: 配置（提示词优化后端：vlm / director / auto）
python mh3_director_pack.py config            :: 查看
python mh3_director_pack.py config --save     :: 生成/写出 config.json
```

> 所有命令输出 **JSON**，便于 agent 解析。未指定 `--project` 时用"活动工程"。

## HTTP 用法（供 agent/远程）

```bat
python mh3_director_pack.py serve --port 8765
```
- `GET /help` 列出端点
- `POST /<cmd>` body：`{"args": { ...CLI 参数... }}`，如
  `POST /add-material  {"args":{"file":"a.png","scope":"common","mname":"角色A"}}`
- 端点：`new/list/active/use/version/capabilities/config/report/show/set-common/add-group/set-group/del-group/add-material/set-source/set-output/export/import-pack/validate/enhance/reverse`

## 交互式向导（wizard）——供**用户直接使用**

```bat
python mh3_director_pack.py wizard
```
**分步问答**（一次只问一项；回车=默认/取消）。主菜单：`1`公共参数 `2`分段 `3`输出设置 `4`查看内容 `5`校验 `6`导出 `7`可配置项 `8`切换工程 `0`退出。
- **分段**：先「新建空段」（只问 **段名 + 时长**），再用「编辑某段」**逐项**填 提示词 / 时长 / 段名 / 连续性 / LoRA / 素材。
- **提示词**处有内置「优化」入口（见下节）。
- **输出设置**子菜单覆盖：宽/高、连续性、上下文帧、模式、重绘、音频模式、参考图适配、导出范围、帧上限。
- 选素材时：**直接输入/粘贴路径**，或输入 **`L`** 从 `config.json` 的 `enhance.media_dirs`（默认 ComfyUI `input/output`）列出最近文件按序号选。
- 向导内加入的素材/分组一律标注 **`decided_by: user`**。
- 导出后自动打印内容摘要，并提示去 Director 点 **Import pack**。

## 提示词优化（enhance）

把**草稿提示词 + 修改指令**交给优化器，返回**候选**；**默认只返回候选，不写回**；加 `--apply`（向导里会问"采用?"）才写回。**绝不静默覆盖**。

后端**可切换**（`config.json` → `enhance.backend`）：
| 后端 | 说明 | 依赖 |
|---|---|---|
| `vlm` | 调用离线 `reverse_prompt.py`（本地 llama-server / vLLM） | `enhance.vlm.server`（默认 8080）|
| `api` | 调用**任意 OpenAI 兼容接口**（云端或自建 vLLM） | `enhance.api.base_url` + `api_key` + `model` |
| `director` | 调用 ComfyUI 导演台 `POST /minimax/director/enhance` | ComfyUI（8188）+ 已配 LLM |
| `auto` | 按 本地健康 → 有 api_key → 导演台 顺序自动选；都不可用则**优雅报错** | — |

> `api` 配置示例（`mh3_director\config.json`）：
> ```json
> "api": { "base_url": "https://api.deepseek.com/v1", "api_key": "sk-...", "model": "deepseek-chat", "no_template_kwargs": true }
> ```
> 本地 vLLM 也可当 `api` 用（`base_url` 指向它、`api_key` 随便填非空）。

- `target`：`h3`（视频段，默认）/ `krea2`（图片提示词），模板不同。
- 直传草稿：`enhance --draft "..." --instruction "..."`（无需工程）。
- 生成本机配置：`config --save` → `mh3_director\config.json`（可改 VLM 路径/端口、导演台 LLM URL/模型、素材目录）。

### VLM 生命周期（自动起 / 退出关）

默认 `backend=vlm`（纯本地）。`enhance/reverse` 需要时会**自动启动**本地 VLM（`enhance.vlm.auto_start`，首次加载约数分钟）；
**向导会话退出时自动关闭**（`enhance.vlm.auto_stop`）以释放显存——但**只关本工具启动的**，你手动开的不会被关。

```bat
python mh3_director_pack.py vlm status   :: 看是否在跑 / 是否本工具管理
python mh3_director_pack.py vlm start    :: 手动起（工具会记住 pid）
python mh3_director_pack.py vlm stop     :: 关掉本工具管理的 VLM
```
> VLM 和 ComfyUI 抢显存：**优化/反推时开 VLM，生成时关 VLM**。用向导（`启动向导.bat`）时会自动起、退出自动关。
> 不想自动起/关，把 `config.json` 的 `enhance.vlm.auto_start` / `auto_stop` 设为 `false`。

## 从素材反推提示词（reverse）

用本地 VLM 把**图片/视频**反推成提示词，并可合并进工程的公共/某段提示词：
```bat
python mh3_director_pack.py reverse --project my_reel --file 角色.png --scope common --target h3
python mh3_director_pack.py reverse --project my_reel --file 角色.png --scope common --apply   :: 追加到公共提示词
python mh3_director_pack.py reverse --project my_reel --file 镜头.mp4 --scope group --group g458942 --apply
```
- 仅支持**本地 VLM**（`enhance.backend = vlm`）；`--target h3|krea2`；`--combine append|replace`。
- 默认只返回反推结果；`--apply` 才写入。向导里在「公共参数 / 编辑分段」菜单的「从素材反推并合入」。
- 路径粘贴**带引号也没关系**（会自动去掉首尾引号）。

> **谁决定参考**：所有参考素材（图/视频/音频）、其归属与命名，**由用户决定**；除非用户明确授权由 agent 代为选择。
> 每条素材与每个分组都记录 `decided_by`（`user`/`agent`）与时间：**非交互 CLI 默认记 `agent`**（保守），向导记 `user`；
> agent 若要表示"这是用户选的"，调用时传 `--decided-by user`。

## 可配置项（capabilities）——用户能决定什么

```bat
python mh3_director_pack.py capabilities                 :: 概览（分类 + 项名）
python mh3_director_pack.py capabilities --section output :: 某类展开（含取值/默认/示例）
python mh3_director_pack.py capabilities --full           :: 全量展开
```
向导里也有（菜单 `9`）。

**概览**（`--full`/`--section` 看细节）：

| 类别 | 你能决定 |
|---|---|
| **任务模式** | `r2v`（参考组）/ `fl2v`（首尾帧）/ `v2v`（源视频切段） |
| **公共 shared** | 公共提示词、公共素材（图/视频/音频） |
| **分段 groups** | 每段：名称、提示词、时长(秒)、素材(含 fl2v 首尾帧)、**LoRA 栈**、连续性(from-prev) |
| **输出 output** | 分辨率(width/height/longEdge)、段间连续性(开关/overlap 22/模式/重绘)、音频模式、参考图适配、导出范围/帧上限 |
| **源视频** | `set-source`（v2v/rv2v） |
| **导入/导出/查看** | `export` / `import-pack` / `validate` / `show` |
| **导演包结构** | `pack.json` + `shared_params/` + `asset_groups/NN/` (+ `source_video/`) |

> 不知道能改什么时，先看 `capabilities`；要精确取值再 `--section <key>`。

## 导入到 ComfyUI

在「MiniMax H3 Director」节点工具栏点 **Import pack**，选 `exports\<名>.mmxpack.zip` 即可
（会替换当前时间轴，需确认）。导出包**不含**模型。

## 关键规则（与导演台一致，已实测）

- 包内路径**只能 ASCII**；结构：`pack.json` + `shared_params/` + `asset_groups/NN/` (+ `source_video/`)。
- 图片槽 **全局连续**：公共 `Picture1..`，分组接着数（`<Picture N>` 引用全局编号）。
- 视频/音频：每文件夹 `Video1–3` / `Audio1–3`（组内同槽位覆盖公共）。
- 帧数：`max(5,round(秒×24))` 吸附 `17k+5`；默认 `864×480 16:9 / 24fps`。
- **素材"名字"只存在工程里**（包里只有槽位号，导演台不认名字）。
- 图片总数上限 9（H3 限制）。

## 换电脑 / 可移植

**核心（做/改/导出导演包）开箱即用**：把整个 `mh3_director` 目录拷到别处，装 Python 3.8+ 即可，纯标准库、可离线。
`.bat` 会自动找 `python_embeded` 或系统 Python（也可直接 `python mh3_director_pack.py ...`）。

**提示词优化/反推**是"调外部服务"，换机二选一配置（改 `mh3_director\config.json`）：
- 走**云端/API**：设 `enhance.backend="api"`，填 `enhance.api.base_url/api_key/model`（见上）；无需本地模型。
- 走**本地 vLLM/llama-server**：设 `enhance.backend="vlm"`，填 `enhance.vlm.server/model`；若要工具**自动起服务**，再填 `enhance.vlm.launch.exe/args`。
- 也要把 `reverse_prompt\` 目录一并拷过去（`enhance.vlm.script` 默认指向它的隔壁路径），或把 `enhance.vlm.script` 指到你的路径。
- `enhance.media_dirs` 默认空（向导「L 选最近文件」用）；填上你的 ComfyUI `input/output` 即可。

> 代码默认**不含任何本机盘符**（`media_dirs`、`vlm.launch` 默认为空）。你当前机器的 `config.json` 里保存的是本机值。

## 目录结构

```
mh3_director/
├── mh3_director_pack.py       主程序
├── active.json                活动工程指针（use 生成）
├── config.json                本机配置（VLM/API/导演台/素材目录；config --save 生成）
├── .vlm.pid                   本工具启动的 VLM 的 pid（自动关时用）
├── 启动向导.bat / 启动HTTP服务.bat
├── projects/<名>/project.json + media/
├── exports/<名>.mmxpack.zip
├── README.md
├── AGENT_PLAYBOOK.md          agent 操作流程
├── AGENT_HANDOFF.md           交接文档（环境/格式/验证）
└── CHANGELOG.md               版本/变更记录（`version` 命令可查当前版本）
```
