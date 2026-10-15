# 导演包生成小助手 (mh3-director-assistant)

制作 **MiniMax H3 导演包**（`*.mmxpack.zip`）的本地工具。提供**网页点选界面**和**命令行向导**，
支持本地 LLM/VLM（llama-server / vLLM）与在线 OpenAI 兼容 API，可一键导出到 ComfyUI 的
`MiniMax H3 Director` 节点。

> 单文件 exe，双击即用；全程本地离线，不消耗云端 token；纯 Python 标准库实现，无第三方依赖。

---

## 功能特性

- **两种界面**：网页点选界面（浏览器）/ 命令行向导。
- **工程管理**：新建/切换工程，任务模式 r2v / fl2v / v2v / rv2v 等。
- **公共参数**：公共提示词（拼进每个分段）、公共素材（图/视频/音频）。
- **分段编辑**：每段独立设置提示词、时长、是否“接上一段”、素材、LoRA。
- **提示词导入**：按钮导入，或把 `.txt/.md` 直接**拖到文本框**；支持**一键保存全部分段**。
- **角色替换**：用**本地 VLM** 自动检测“公共角色（替换目标）”与“分段角色（被替换）”，逐个一键替换。
- **提示词优化 / 反推**：让模型按指令改写，或从图片/视频反推提示词。
- **本地 / 在线后端切换**：设置页一键选择本地 VLM 或在线 API，可分别配置路径、地址、密钥。
- **输出与导出**：分辨率/连续性/音频等设置，校验后导出 `.mmxpack.zip`，导入 ComfyUI 开跑。
- **便携**：可打包为单文件 exe（含图标），双击打开网页界面；无控制台版本带“停止服务”按钮。

---

## 快速开始

### 方式 A：下载可执行文件（推荐）
到本仓库的 **Releases** 页面下载：

- `导演包生成小助手.exe` —— 无控制台，双击即打开网页界面。
- `导演包生成小助手(带控制台).exe` —— 保留控制台窗口，便于查看日志。

> 首次双击会解压到临时目录，启动慢几秒属正常。用完在网页右上角点「停止服务」。

### 方式 B：从源码运行
需要 Python 3.8+（纯标准库，无需额外依赖）。

```bat
python mh3_director\mh3_director_pack.py serve --open      :: 网页界面
python mh3_director\mh3_director_pack.py wizard            :: 命令行向导
```

---

## 依赖

- Windows + Python 3.8+（打包版无需另装 Python）。
- 使用「本地 VLM」时：
  - 一个带视觉塔的本地服务（推荐 `llama-server`，Qwen + `--mmproj`），默认 `http://127.0.0.1:8080`；
  - 本仓库已附带 `reverse_prompt/`（反推/优化会调用 `reverse_prompt/reverse_prompt.py`）。
- 使用「在线 API」时：能联网，填好 `Base URL / API Key / model` 即可。

> 只做**工程编辑与导出**（不点优化/反推/角色检测）时，**不需要任何 LLM**。

---

## 目录结构

```
mh3-director-assistant/
├─ README.md
├─ LICENSE
├─ mh3_director/
│  ├─ mh3_director_pack.py        # 主程序（网页界面 + 命令行向导 + HTTP API）
│  ├─ config.example.json         # 配置模板（复制为 config.json 后按需修改）
│  ├─ 导演包生成小助手.ico         # 程序图标
│  └─ README.tech.md              # 详细技术文档（原工具 README）
└─ reverse_prompt/                # 提示词反推依赖（本地 VLM）
   ├─ reverse_prompt.py
   ├─ README.md
   └─ templates/
```

运行后会在工具目录生成：`projects/`（工程）、`exports/`（导出的 zip）、
`_uploads/`（网页上传临时文件）、`config.json`、`导演包生成小助手.log`。

---

## 配置

工具无需配置即可使用默认值。想自定义时，把 `mh3_director/config.example.json` 复制为
`mh3_director/config.json` 再改；或在**网页界面「设置」页**里直接改（会写回 `config.json`）：

- **后端**：`自动` / `本地 VLM` / `在线 API` / `导演台`
- **本地 VLM**：server、model、api_key、python、script（`reverse_prompt.py`）、
  启动程序 exe/cwd/args、自动启动、启动超时
- **在线 API**：Base URL、API Key、model、`chat_template_kwargs` 开关
- **素材目录 media_dirs**：用于“最近文件”列表

对应环境变量（`reverse_prompt.py`）：`RP_SERVER`、`RP_MODEL`、`RP_API_KEY`、`RP_NO_TEMPLATE_KWARGS`。

---

## 从源码打包 exe

```bat
pip install pyinstaller
pyinstaller --onefile --noconsole --name director_assistant ^
  --icon mh3_director\导演包生成小助手.ico mh3_director\mh3_director_pack.py
```

（`--console` 可生成带控制台的调试版本。）

---

## 常见问题

- **浏览器没自动打开**：手动访问 `http://127.0.0.1:8765/`。
- **端口被占用**：说明已有一个实例在跑，直接打开浏览器即可；换端口用 `... serve --port 8899`。
- **无控制台版怎么停服务**：网页右上角「停止服务」，或任务管理器结束进程。
- **本地 VLM 很慢/没反应**：首次要加载模型（可能数分钟），确认 `server` 地址和显存。
- **把 exe 单独拷走**：会在新位置新建 `config.json`/`projects`，且找不到 `reverse_prompt.py`
  （本地反推/优化不可用）。建议连同整个文件夹一起使用。

---

## 许可

MIT License，见 [LICENSE](LICENSE)。
