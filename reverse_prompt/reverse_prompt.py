#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
reverse_prompt.py  —  本地 VLM 提示词反推工具
================================================
用本地 Qwen3.8-27B(带视觉塔, llama-server) 把【图片】或【视频】反推成可直接使用的提示词：

  * 图片 -> Krea2 / Qwen-Image 提示词  (官方 qwen-image-2.1 Skill 契约, 输出 JSON{rewritten_prompt,wh_ratio})
  * 图片 -> MiniMax H3 视频提示词       (I2VA: 首帧来自该图)
  * 视频 -> MiniMax H3 视频提示词       (采样多帧, 输出 integrated_multimodal_description / overall_soundscape / non_diegetic_music)

依赖:
  - 本地 llama-server 已启动并带 --mmproj (默认 http://127.0.0.1:8080)
  - opencv(cv2) / Pillow / numpy  (ComfyUI 自带 python 已具备)

示例:
  python reverse_prompt.py H:/comfyui/output/krea2_character_sheet_final.png
  python reverse_prompt.py 4.png --target krea2 --out 4.krea2.json
  python reverse_prompt.py 4.png --target h3 --duration 8        # I2VA
  python reverse_prompt.py clip.mp4 --target h3 --frames 6
  python reverse_prompt.py clip.mp4 --target h3 --think          # 允许模型思考(更慢更细)
"""
import argparse, base64, json, os, sys, time, urllib.request, urllib.error, tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ------------------------------------------------------------------ 配置
SERVER     = os.environ.get("RP_SERVER", "http://127.0.0.1:8080")
MODEL      = os.environ.get("RP_MODEL", "qwen")
API_KEY    = os.environ.get("RP_API_KEY", "")
# 有些云端 OpenAI 兼容接口不接受 llama-server 专用的 chat_template_kwargs：设 RP_NO_TEMPLATE_KWARGS=1 关闭
NO_TMPL_KW = os.environ.get("RP_NO_TEMPLATE_KWARGS", "").strip().lower() in ("1", "true", "on", "yes")

# 模板优先用工具自带 templates/ (完全独立, 不依赖任何自定义节点)
TOOL_DIR   = os.path.dirname(os.path.abspath(__file__))
TPL_DIR    = os.path.join(TOOL_DIR, "templates")
SKILL_DIR  = os.environ.get(
    "RP_SKILL_DIR",
    r"J:\ComfyUI_windows_portable_amd\ComfyUI_windows_portable\ComfyUI\custom_nodes"
    r"\comfyui-minimax-h3-prompt-enhancer-T8\official_skills",
)

def _first(*paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return paths[-1] if paths else None

QWEN_SKILL = _first(os.path.join(TPL_DIR, "qwen-image-2.1_SKILL.md"),
                    os.path.join(SKILL_DIR, "qwen-image-2.1", "SKILL.md"))
H3_BASE    = _first(os.path.join(TPL_DIR, "h3_base-en.txt"),
                    os.path.join(SKILL_DIR, "h3-prompt-writing", "references", "base-en.txt"))
H3_REF     = _first(os.path.join(TPL_DIR, "h3_ref-en.txt"),
                    os.path.join(SKILL_DIR, "h3-prompt-writing", "references", "ref-en.txt"))

IMG_EXT   = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VID_EXT   = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv"}

# ------------------------------------------------------------------ 模板加载
_FALLBACK_QWEN = (
    "You turn an image request into ONE long English paragraph describing the finished image "
    "as if you were looking at it (medium/style/subject/background, region-by-region walk, "
    "explicit lighting sentence, closing composition sentence), plus the aspect ratio. "
    "No quality boosters, no 'you', no instructions. Output ONLY JSON: "
    '{"rewritten_prompt": "...", "wh_ratio": "3:2"}'
)
_FALLBACK_H3 = (
    "Write a MiniMax H3 video prompt. Output exactly three fields:\n"
    "integrated_multimodal_description: [Shot 1] <style>, <composition>... (shots, camera motion, "
    "speakers with (S1), dialogue in <d>[English] ...</d>, on-screen text in quotes)\n"
    "overall_soundscape: <1-4 sentences>\n"
    "non_diegetic_music: <1-3 sentences or N/A>"
)

def _load(path, fallback):
    try:
        with open(path, encoding="utf-8") as f:
            t = f.read()
        if t.lstrip().startswith("---"):          # 去掉 YAML front-matter
            end = t.find("\n---", 3)
            if end != -1:
                t = t[end + 4:].lstrip()
        return t
    except Exception as e:
        print(f"[warn] 读不到模板 {path} -> 用内置简化版 ({e})", file=sys.stderr)
        return fallback

# ------------------------------------------------------------------ 多模态调用
def _data_url(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext or 'png'}"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()

def _downscale(path, max_side, out_dir, tag=""):
    """把图片缩到最长边 max_side 并转 jpg, 控制 token。返回新路径。"""
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        w, h = im.size
        if max(w, h) > max_side:
            s = max_side / max(w, h)
            im = im.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
        dst = os.path.join(out_dir, f"rp_{tag}{int(time.time()*1000)%100000}.jpg")
        im.save(dst, "JPEG", quality=82)
        return dst
    except Exception:
        return path

def vlm(messages, max_tokens=1400, temperature=0.2, think=False, timeout=600):
    payload = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if not NO_TMPL_KW:
        payload["chat_template_kwargs"] = {"enable_thinking": bool(think)}
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = "Bearer " + API_KEY
    req = urllib.request.Request(
        SERVER.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.load(r)
    except urllib.error.URLError as e:
        raise SystemExit(f"[错误] 连不上 LLM 服务 ({SERVER})：{e}\n"
                         f"· 本地: 启动带视觉塔的 llama-server/vLLM，并把 RP_SERVER 指到它；\n"
                         f"· 云端/API: 把 RP_SERVER 设为 OpenAI 兼容地址(如 https://.../v1)，并设 RP_API_KEY、RP_MODEL。")
    return resp["choices"][0]["message"].get("content", "").strip(), resp.get("usage", {})

# ------------------------------------------------------------------ 视频抽帧
def extract_frames(video, n, max_side, out_dir):
    import cv2
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"[错误] 打不开视频: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = total / fps if fps else 0.0
    n = max(1, min(n, max(1, total)))
    # 在 (0, dur) 上均匀取 n 帧, 避免首尾黑帧
    idxs = [int(round((i + 0.5) * total / n)) for i in range(n)]
    frames = []
    for i, fi in enumerate(idxs):
        fi = min(max(fi, 0), max(total - 1, 0))
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = cap.read()
        if not ok:
            continue
        t = fi / fps if fps else 0.0
        tmp = os.path.join(out_dir, f"rp_frame_{i}.png")
        cv2.imwrite(tmp, frame)
        frames.append((t, _downscale(tmp, max_side, out_dir, tag=f"f{i}_")))
    cap.release()
    return frames, dur

# ------------------------------------------------------------------ 目标：图片 -> Krea2
def gen_krea2(img_path, out_dir, max_side, think, extra=""):
    skill = _load(QWEN_SKILL, _FALLBACK_QWEN)
    sys_msg = (
        "You are an image prompt rewriting expert (contract below). You are given an ACTUAL image to "
        "observe. Follow the contract strictly and output ONLY the final JSON object on one line:\n\n"
        + skill
    )
    utext = "Observe the image below and output the JSON exactly as the contract requires."
    if extra:
        utext += "\nAdditional instruction: " + extra
    small = _downscale(img_path, max_side, out_dir, tag="img_")
    msgs = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": [
            {"type": "text", "text": utext},
            {"type": "image_url", "image_url": {"url": _data_url(small)}},
        ]},
    ]
    txt, usage = vlm(msgs, max_tokens=1600, think=think)
    # 尝试解析 JSON
    obj = None
    try:
        s = txt[txt.find("{"): txt.rfind("}") + 1]
        obj = json.loads(s)
    except Exception:
        obj = None
    return {"target": "krea2", "raw": txt, "json": obj, "usage": usage}

# ------------------------------------------------------------------ 目标：图片 -> H3 (I2VA)
def gen_h3_from_image(img_path, out_dir, max_side, duration, think, extra=""):
    base = _load(H3_BASE, _FALLBACK_H3)
    sys_msg = (
        "You are a MiniMax H3 video prompt writer. The guide below is the frozen source contract -- "
        "follow its field names, section order, shot/camera/dialogue notation EXACTLY.\n\n"
        "TASK: The user gives ONE image which is the FIRST FRAME of a target video (I2VA). "
        f"Target duration = {duration:.2f} seconds. Reverse-engineer a plausible, self-consistent H3 prompt. "
        "Output the first-frame instruction line, then the three core fields. "
        "No Markdown fences, no explanation.\n\n" + base
    )
    small = _downscale(img_path, max_side, out_dir, tag="h3img_")
    utext = f"<Picture 1> (first frame). Target duration {duration:.2f}s. Write the H3 I2VA prompt."
    if extra:
        utext += "\nAdditional instruction: " + extra
    msgs = [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": [
            {"type": "text", "text": utext},
            {"type": "image_url", "image_url": {"url": _data_url(small)}},
        ]},
    ]
    txt, usage = vlm(msgs, max_tokens=1600, think=think)
    return {"target": "h3-image", "raw": txt, "usage": usage}

# ------------------------------------------------------------------ 目标：视频 -> H3
def gen_h3_from_video(video, out_dir, frames_n, max_side, duration_override, think, extra=""):
    base = _load(H3_BASE, _FALLBACK_H3)
    frames, dur = extract_frames(video, frames_n, max_side, out_dir)
    dur_txt = duration_override if duration_override else dur
    sys_msg = (
        "You are a MiniMax H3 video prompt writer. The guide below is the frozen source contract -- "
        "follow its field names, section order, shot/camera/dialogue notation EXACTLY.\n\n"
        "TASK: The user gives sampled frames of a REAL video (timestamps given). Reverse-engineer the H3 "
        f"prompt that could regenerate it. The real total duration is {dur_txt:.2f} seconds; make the "
        "timeline match it. Output the three core fields (integrated_multimodal_description, "
        "overall_soundscape, non_diegetic_music). No Markdown fences, no explanation.\n\n" + base
    )
    user_content = [{"type": "text",
                     "text": f"Video duration: {dur_txt:.2f}s. {len(frames)} sampled frames (order = time):"}]
    for t, fp in frames:
        user_content.append({"type": "text", "text": f"Frame at t={t:.2f}s:"})
        user_content.append({"type": "image_url", "image_url": {"url": _data_url(fp)}})
    tail = "Now output the H3 prompt (three core fields). Match the observed content and duration."
    if extra:
        tail += "\nAdditional instruction: " + extra
    user_content.append({"type": "text", "text": tail})
    msgs = [{"role": "system", "content": sys_msg}, {"role": "user", "content": user_content}]
    txt, usage = vlm(msgs, max_tokens=2200, think=think)
    return {"target": "h3-video", "duration": dur_txt, "frames": len(frames), "raw": txt, "usage": usage}

# ------------------------------------------------------------------ 改写(revise): 用本地模型按指令修改草稿提示词
def gen_revise(draft, instruction, target, media_path, max_side, think, extra=""):
    if target == "h3":
        tpl = _load(H3_BASE, _FALLBACK_H3)
        sys_msg = ("You are a MiniMax H3 prompt editor. Below is the frozen prompt-writing contract. "
                   "Rewrite the user's DRAFT prompt according to their INSTRUCTION; keep H3 field names/order. "
                   "Output ONLY the final prompt text, no explanation, no Markdown fence.\n\n" + tpl)
    else:
        tpl = _load(QWEN_SKILL, _FALLBACK_QWEN)
        sys_msg = ("You are an image prompt editor. Below is the frozen contract. Rewrite the user's DRAFT "
                   "prompt according to their INSTRUCTION into one final English paragraph + ratio. "
                   'Output ONLY valid JSON {"rewritten_prompt": "...", "wh_ratio": "..."}.\n\n' + tpl)
    utext = f"DRAFT:\n{draft}\n\nINSTRUCTION:\n{instruction}"
    if extra:
        utext += "\n\nADDITIONAL: " + extra
    content = [{"type": "text", "text": utext}]
    if media_path and os.path.isfile(media_path):
        small = _downscale(media_path, max_side, tempfile.mkdtemp(prefix="rp_rev_"), tag="rev_")
        content.append({"type": "image_url", "image_url": {"url": _data_url(small)}})
    msgs = [{"role": "system", "content": sys_msg}, {"role": "user", "content": content}]
    txt, usage = vlm(msgs, max_tokens=1600, think=think)
    return {"target": ("h3" if target == "h3" else "krea2"), "raw": txt, "usage": usage}

# ------------------------------------------------------------------ 单文件处理 (供 CLI / 节点复用)
def kind_of(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in VID_EXT: return "video"
    if ext in IMG_EXT: return "image"
    return None

def process_file(media, target, opts, out_dir):
    """opts: 有 .max_side/.frames/.duration/.think 的对象(或 namespace)。返回 (target, res, body) 或 None。"""
    kind = kind_of(media)
    if kind is None:
        return None
    t = target if target != "auto" else ("h3" if kind == "video" else "krea2")
    if t == "krea2":
        if kind != "image":
            return None
        res = gen_krea2(media, out_dir, opts.max_side, opts.think)
        body = json.dumps(res["json"], ensure_ascii=False, indent=2) if res.get("json") else res.get("raw", "")
    else:
        if kind == "video":
            res = gen_h3_from_video(media, out_dir, opts.frames, opts.max_side, opts.duration, opts.think)
        else:
            dur = opts.duration if opts.duration else 8.0
            res = gen_h3_from_image(media, out_dir, opts.max_side, dur, opts.think)
        body = res.get("raw", "")
    return t, res, body

def _write_out(path, body, res):
    with open(path, "w", encoding="utf-8") as f:
        f.write(body + "\n")
        f.write("\n# meta: " + json.dumps({"target": res.get("target"), "usage": res.get("usage")},
                                          ensure_ascii=False) + "\n")

# ------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(description="本地 VLM 提示词反推 (Krea2 / MiniMax H3)")
    ap.add_argument("media", nargs="?", help="图片/视频路径，或一个文件夹(批量)；用 --revise 可省略")
    ap.add_argument("--target", choices=["auto", "krea2", "h3"], default="auto",
                    help="auto: 图片->krea2, 视频->h3")
    ap.add_argument("--frames", type=int, default=6, help="视频采样帧数 (默认 6)")
    ap.add_argument("--duration", type=float, default=None, help="H3 目标时长秒; 图片默认 8, 视频默认读实际时长")
    ap.add_argument("--max-side", type=int, default=1024, help="送入 VLM 的图片最长边 (默认 1024, 省 token 可调小)")
    ap.add_argument("--think", action="store_true", help="允许模型思考 (更慢更细)")
    ap.add_argument("--out", default=None, help="输出文件; 默认写入 <media>.prompt.txt")
    ap.add_argument("--recursive", action="store_true", help="批量模式: 递归子目录")
    ap.add_argument("--skip-existing", action="store_true", help="批量模式: 已有 .prompt.txt 就跳过")
    ap.add_argument("--summary", default=None, help="批量汇总文件; 默认 <目录>/_prompts_summary.txt")
    ap.add_argument("--revise", default=None, help="改写模式: 传入草稿提示词文本(或 @文件路径)")
    ap.add_argument("--instruction", default=None, help="改写模式: 如何修改(自然语言)")
    args = ap.parse_args()

    # ---------------- 改写模式 ----------------
    if args.revise is not None:
        draft = args.revise
        if draft.startswith("@"):
            draft = open(draft[1:], encoding="utf-8").read()
        target = "h3" if args.target == "h3" else "krea2"
        res = gen_revise(draft, args.instruction or "", target, args.media, args.max_side, args.think)
        body = res["raw"]
        out = args.out or "revised.prompt.txt"
        with open(out, "w", encoding="utf-8") as f:
            f.write(body + "\n")
        print(body)
        print(f"\n[i] 已保存: {out}")
        return

    media = args.media
    if not media or not os.path.exists(media):
        raise SystemExit(f"[错误] 路径不存在或未指定: {media}")
    out_dir = tempfile.mkdtemp(prefix="rp_")

    # ---------------- 批量文件夹 ----------------
    if os.path.isdir(media):
        exts = IMG_EXT | VID_EXT
        files = []
        if args.recursive:
            for root, _dirs, names in os.walk(media):
                files += [os.path.join(root, n) for n in names if os.path.splitext(n)[1].lower() in exts]
        else:
            files = [os.path.join(media, n) for n in os.listdir(media)
                     if os.path.splitext(n)[1].lower() in exts]
        files.sort()
        if not files:
            raise SystemExit("[错误] 目录里没有支持的图片/视频")
        print(f"[i] 批量: {len(files)} 个文件, target={args.target}, server={SERVER}")
        rows = []
        for i, fp in enumerate(files, 1):
            outp = os.path.splitext(fp)[0] + ".prompt.txt"
            if args.skip_existing and os.path.exists(outp):
                print(f"[{i}/{len(files)}] 跳过(已存在): {os.path.basename(fp)}")
                rows.append((os.path.basename(fp), "SKIP", "")); continue
            try:
                r = process_file(fp, args.target, args, out_dir)
                if r is None:
                    print(f"[{i}/{len(files)}] 跳过(不支持): {os.path.basename(fp)}"); continue
                t, res, body = r
                _write_out(outp, body, res)
                first = (body.strip().splitlines() or [""])[0]
                print(f"[{i}/{len(files)}] OK ({t}): {os.path.basename(fp)}")
                rows.append((os.path.basename(fp), t, first[:160]))
            except Exception as e:
                print(f"[{i}/{len(files)}] 失败: {os.path.basename(fp)} -> {e}")
                rows.append((os.path.basename(fp), "ERR", str(e)[:160]))
        summ = args.summary or os.path.join(media, "_prompts_summary.txt")
        with open(summ, "w", encoding="utf-8") as f:
            for name, t, first in rows:
                f.write(f"{name}\t{t}\t{first}\n")
        print(f"\n[i] 完成, 汇总: {summ}")
        return

    # ---------------- 单文件 ----------------
    if not os.path.isfile(media):
        raise SystemExit(f"[错误] 不是有效文件: {media}")
    r = process_file(media, args.target, args, out_dir)
    if r is None:
        raise SystemExit("[错误] 不支持的文件类型")
    target, res, body = r
    print(f"[i] 输入: {media}  target={target}  server={SERVER}")
    out = args.out or (os.path.splitext(media)[0] + ".prompt.txt")
    _write_out(out, body, res)
    print("\n================= 反推结果 =================")
    print(body)
    print("\n-------------------------------------------")
    u = res.get("usage", {})
    print(f"[i] 本地 VLM token: prompt={u.get('prompt_tokens')} completion={u.get('completion_tokens')}")
    print(f"[i] 已保存: {out}")

if __name__ == "__main__":
    main()
