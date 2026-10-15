# reverse_prompt — 本地 VLM 提示词反推工具

用**本地 Qwen3.8-27B（带视觉塔 mmproj）**把图片/视频反推成可直接使用的提示词，**不消耗云端 token**。

| 输入 | 目标 | 输出格式 |
|---|---|---|
| 图片 | **Krea2 / Qwen-Image** | JSON：`{"rewritten_prompt": "...", "wh_ratio": "2:3"}` |
| 图片 | **MiniMax H3**（I2VA，图=首帧） | 首帧指令行 + `integrated_multimodal_description` / `overall_soundscape` / `non_diegetic_music` |
| 视频 | **MiniMax H3** | 同上三段式（采样多帧，自动匹配真实时长/镜头/运镜/台词） |

模板**已打包进 `templates/`**（源：`comfyui-minimax-h3-prompt-enhancer-T8` 官方 Skill），工具**不依赖任何自定义节点，可完全本地独立运行**：

| 文件 | 用途 |
|---|---|
| `templates/qwen-image-2.1_SKILL.md` | 图像 → Krea2 / Qwen-Image 提示词 |
| `templates/h3_base-en.txt` | 视频 → MiniMax H3（T2VA/I2VA/FL2VA/L2VA） |
| `templates/h3_ref-en.txt` | 视频 → MiniMax H3（Ref2VA 全参考） |
| `templates/h3-prompt-writing_SKILL.md` | H3 总览 |

加载顺序：`templates/`（自带）→ `RP_SKILL_DIR` 指定的旧目录 → 内置简化版兜底。

> **本地独立 / 私密**：本工具全程只连本地 `llama-server`，结果写在 `<媒体名>.prompt.txt`，**不经过任何云端模型**。
> NSFW 工作流可完全本地闭环：`启动VLM.bat` + `反推提示词.bat`，产出你自己看即可，无需把内容交给云端。

---

## 1. 先启动本地 VLM（llama-server，带视觉塔）

```bat
J:\llama_cpp_120x\llama-server.exe ^
  -m "J:/models/ISTA-DASLabQwen3.8-27B-GSQ-RCO-GGUF/Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp.gguf" ^
  --mmproj "J:/models/ISTA-DASLabQwen3.8-27B-GSQ-RCO-GGUF/mmproj-Qwen3.8-27B-BF16.gguf" ^
  --gpu-layers 99 --ctx-size 16384 --port 8080 --host 127.0.0.1 ^
  --cache-type-k q8_0 --cache-type-v q8_0 --flash-attn on --jinja --parallel 1 --image-min-tokens 1024
```

（用完可关，ComfyUI 生成时会重新占用显存。）

## 2. 用法

```bat
:: 图片 -> Krea2 提示词
python reverse_prompt.py 4.png

:: 图片 -> MiniMax H3 (I2VA, 8 秒)
python reverse_prompt.py 4.png --target h3 --duration 8

:: 视频 -> MiniMax H3
python reverse_prompt.py clip.mp4 --target h3 --frames 6

:: 指定输出文件 / 允许深度思考 / 更多帧
python reverse_prompt.py clip.mp4 --target h3 --frames 10 --think --out clip.h3.txt
```

参数：

| 参数 | 说明 |
|---|---|
| `--target auto\|krea2\|h3` | auto：图片→krea2，视频→h3 |
| `--frames N` | 视频采样帧数（默认 6） |
| `--duration S` | H3 目标时长；图片默认 8s，视频默认读真实时长 |
| `--max-side N` | 送 VLM 的图片最长边（默认 1024；调小更省 token/更快） |
| `--think` | 允许模型思考（更慢更细） |
| `--out FILE` | 输出文件（默认 `<媒体名>.prompt.txt`） |

> 用 ComfyUI 自带的 python 运行最省事：
> `J:\ComfyUI_windows_portable_amd\ComfyUI_windows_portable\python_embeded\python.exe reverse_prompt.py ...`

## 3. 为什么省 token

图片/视频先由**本地 GPU 的 Qwen3.8-27B 视觉塔**读成文字，再交给云端大模型（我）——我读到的是
几百 token 的文字，而不是上千 token 的原图。代价：文字会丢细粒度信息。

## 4. 自定义 / 换后端（本地 vLLM 或云端 API）

本工具发的是标准 OpenAI 兼容请求（`<RP_SERVER>/v1/chat/completions`），所以既可用本地服务，也可用云端 API：

| 环境变量 | 说明 |
|---|---|
| `RP_SERVER` | 服务基地址。本地 llama-server/vLLM 或云端，如 `http://127.0.0.1:8080`、`https://api.deepseek.com/v1` |
| `RP_MODEL` | 模型名（云端必填，如 `deepseek-chat`；本地可留默认 `qwen`）|
| `RP_API_KEY` | 需要鉴权时填，会加 `Authorization: Bearer <key>` |
| `RP_NO_TEMPLATE_KWARGS` | 设为 `1` 时**不发送** llama-server 专用的 `chat_template_kwargs`（云端/部分接口不接受未知字段时用）|
| `RP_SKILL_DIR` | 换模板目录（默认用工具自带 `templates/`）|

示例（走某云端 API 反推图片 → Krea2）：
```bat
set RP_SERVER=https://api.deepseek.com/v1
set RP_MODEL=deepseek-chat
set RP_API_KEY=sk-xxxx
set RP_NO_TEMPLATE_KWARGS=1
python reverse_prompt.py 图.png --target krea2
```
> 注意：**图片/视频反推需要视觉模型**（能看图的）；纯文本模型只能做 `--revise` 改写。
> 找不到模板时脚本会用内置简化版兜底。

> 与导演包工具 `mh3_director` 的对接：在 `mh3_director\config.json` 里用 `enhance.backend=api` 填 `base_url/api_key/model` 即可，或 `enhance.vlm.*` 走本地。

## 5. 批量反推整个文件夹

把 `media` 参数换成一个**目录**即可批量：

```bat
python reverse_prompt.py D:\my_images\                       :: 图片全部 -> Krea2
python reverse_prompt.py D:\my_videos\ --target h3            :: 视频全部 -> H3
python reverse_prompt.py D:\mix\ --recursive --skip-existing  :: 递归 + 跳过已生成的
```

- 每个文件生成 `<文件名>.prompt.txt`；目录下再写一份 `_prompts_summary.txt`（文件→首行）。
- 参数：`--recursive`（递归子目录）、`--skip-existing`（已有就跳过）、`--summary FILE`。

## 6. ComfyUI 节点

已做成节点包，放在 `ComfyUI\custom_nodes\reverse_prompt_local\`，重启 ComfyUI 后在 **`reverse_prompt`** 分类下可见：

| 节点 | 输入 | 输出 |
|---|---|---|
| **Reverse Prompt (Local VLM)** | `image`(IMAGE) 或 `video_path`(STRING) + target/server/model/max_side/duration/frames/think/extra | `prompt`(STRING), `raw`(STRING) |
| **Reverse Prompt Batch (Folder)** | `folder`(STRING) + target/recursive/skip_existing/... | `summary`(STRING), `count`(INT) |

用法：
- 图片：`LoadImage → Reverse Prompt (Local VLM) [target=krea2] → prompt` 接到 `CLIPTextEncode` 或你的提示词节点。
- 视频：`Reverse Prompt (Local VLM) [target=h3]`，在 `video_path` 填视频绝对路径。
- `extra`：附加中文/英文要求（如“重点描述服装材质”“不要出现露骨词”）。
- 节点复用同一套模板与本地 VLM；需先启动 `启动VLM.bat`。
- 若移动了工具目录，设环境变量 `RP_TOOL_DIR` 指向 `reverse_prompt` 目录。

## 7. 改写草稿提示词（`--revise`，配合"修改→确认"工序）

反推结果只是**草稿**。可让本地模型按指令改写（沿用同一套官方 Skill，离线）：

```bat
python reverse_prompt.py 图片.png --target krea2 --revise "@草稿.txt" --instruction "改成白天暖色；去掉武器；强调服装材质" --out revised.txt
python reverse_prompt.py --target h3 --revise "@草稿.txt" --instruction "时长压到6秒，增加一个特写镜头" --out revised_h3.txt
```

- `--revise` 传草稿文本（`@文件路径` 表示读文件）；`--instruction` 传怎么改。
- 可选在最前面给一张图作为 grounding（`图片.png`），改写会更贴合素材。
- 输出 `--out` 指定的文件（默认 `revised.prompt.txt`）。
- 与导演包工具的协作流程见 `custom_nodes\reverse_prompt_local\SEE_ALSO.md`。
