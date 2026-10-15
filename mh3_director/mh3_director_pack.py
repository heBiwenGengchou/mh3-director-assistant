#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
mh3_director_pack — MiniMax H3 导演包生成器（离线/Agent 友好）

目标：不依赖 ComfyUI，就能创建/编辑工程，并导出可被 ComfyUI 的
「MiniMax H3 Director」节点 **Import pack** 直接导入的 `*.mmxpack.zip`。

输出包结构（ASCII 路径）：
  pack.json
  shared_params/shared_params.json   + Picture{n} / Video{n} / Audio{n}
  asset_groups/NN/group.json         + Picture{n} / Video{n} / Audio{n} / start.* / end.*
  source_video/clip_1.*              (v2v / rv2v)

工程结构（本工具的编辑对象，人可读）：
  <root>/<name>/project.json
  <root>/<name>/media/...

用法（CLI，输出 JSON）：
  python mh3_director_pack.py new <name> [--task r2v|v2v|fl2v|t2v|i2v]
  python mh3_director_pack.py list
  python mh3_director_pack.py show <name> [--group <id>]
  python mh3_director_pack.py set-common <name> [--prompt ...] [--enable on|off]
  python mh3_director_pack.py add-group <name> [--gname ...] [--prompt ...] [--duration 5]
  python mh3_director_pack.py set-group <name> <gid> [--prompt ...] [--duration ...] [--gname ...] [--from-prev on|off]
  python mh3_director_pack.py del-group <name> <gid>
  python mh3_director_pack.py add-material <name> --file <path> [--scope common|group] [--group <gid>] [--kind image|video|audio] [--mname ...]
  python mh3_director_pack.py set-source <name> --file <video>
  python mh3_director_pack.py export <name> [--out <zip>]
  python mh3_director_pack.py import-pack <zip> [--name <name>]
  python mh3_director_pack.py validate <name>
  python mh3_director_pack.py serve [--port 8765]

HTTP（与 CLI 同参数，POST JSON；GET /help 列出）：
  POST /new /add-group /set-group /del-group /add-material /set-common /set-source
       /export /import-pack /list /show /validate   body: {"args": {...}}
"""
import argparse, json, os, re, shutil, sys, tempfile, zipfile, uuid, time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ------------------------------------------------------------------ 常量
__version__ = "0.7.0"
PACK_FORMAT = "minimax-h3-director-pack"
PACK_VERSION = 1
DEFAULT_TASK = "r2v"
TASK_KEYS = ("t2v", "i2v", "fl2v", "r2v", "v2v", "rv2v", "mixed")
TASK_LABELS = {
    "t2v": "文生视频(Text to Video)",
    "i2v": "图生视频(Image to Video)",
    "fl2v": "首尾帧生视频(First-Last Frame)",
    "r2v": "参考主体生视频(Reference to Video)",
    "v2v": "视频转视频(Video to Video)",
    "rv2v": "参考素材改视频(Reference Video Edit)",
    "mixed": "混合模式(Mixed Segments)",
}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VID_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".mpg", ".mpeg", ".mts", ".ts"}
AUD_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".wma"}
MAX_IMG = 9   # Picture1..9
MAX_VID = 3   # Video1..3
MAX_AUD = 3   # Audio1..3
MAX_LORAS = 8 # 每段 LoRA 上限
WIDGET_KEYS = ("steps", "sampler", "scheduler", "cfg", "shift_video", "shift_audio", "seed")
DEFAULT_WIDGETS = {"steps": 25, "sampler": "res_multistep", "scheduler": "simple",
                   "cfg": 1.0, "shift_video": 12.0, "shift_audio": 3.0, "seed": 0}
DEFAULT_ROOT = Path(__file__).resolve().parent / "projects"
DEFAULT_EXPORT_DIR = Path(__file__).resolve().parent / "exports"

def _base_dir():
    """可写文件目录：打包成 exe 时用 exe 所在目录，否则用脚本所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

APP_NAME = "导演包生成小助手"
APP_DIR = _base_dir()   # projects / exports / config.json / _uploads / .vlm.pid 都放这里
DEFAULT_ROOT = APP_DIR / "projects"
DEFAULT_EXPORT_DIR = APP_DIR / "exports"

# 无控制台(pythonw / --noconsole)运行时 stdout/stderr 为 None，重定向到日志文件，避免 print 崩溃
if sys.stdout is None or sys.stderr is None:
    try:
        _logf = open(APP_DIR / "导演包生成小助手.log", "a", encoding="utf-8", buffering=1)
    except Exception:
        import io
        _logf = io.StringIO()
    if sys.stdout is None:
        sys.stdout = _logf
    if sys.stderr is None:
        sys.stderr = _logf

# 可配置项清单（分档：概览=类别+项名；--full/--section=含取值/默认/示例）
CAPABILITIES = [
    {"key": "mode", "title": "任务模式", "summary": "决定分组语义（建工程时 --task）", "items": [
        {"name": "--task r2v", "desc": "参考主体生视频：每组=一段，带参考图/视频/音频", "default": "r2v", "example": "new my_reel --task r2v"},
        {"name": "--task fl2v", "desc": "首/尾帧生视频：每组带 start/end 图", "example": "new x --task fl2v"},
        {"name": "--task v2v", "desc": "源视频切段编辑（需 set-source）", "example": "new x --task v2v"},
        {"name": "t2v/i2v/rv2v/mixed", "desc": "兼容别名", "example": ""},
    ]},
    {"key": "shared", "title": "公共(shared)", "summary": "公共提示词与公共素材（拼接进每组）", "items": [
        {"name": "set-common --prompt --enable on|off", "desc": "公共提示词", "default": "空", "example": "set-common --prompt '<Picture 1> ...' --enable on"},
        {"name": "add-material --scope common", "desc": "公共素材(图/视频/音频)", "values": "--kind image|video|audio --mname --decided-by", "example": "add-material --file a.png --scope common --mname 角色A"},
    ]},
    {"key": "group", "title": "分段(groups)", "summary": "每段=一组：提示词/时长/素材/LoRA/连续性", "items": [
        {"name": "add-group", "desc": "新建分段", "values": "--gname --prompt --duration(秒) --task --lora --decided-by", "example": "add-group --gname 开场 --prompt '...' --duration 5"},
        {"name": "set-group", "desc": "修改分段", "values": "--prompt --negative --duration --gname --from-prev on|off --lora --lora-clear", "example": "set-group --group gXXXX --duration 4 --from-prev on"},
        {"name": "del-group", "desc": "删除分段", "values": "--group", "example": "del-group --group gXXXX"},
        {"name": "add-material --scope group", "desc": "分段素材 / fl2v 首尾帧", "values": "--group --role ref|start|end --mname --decided-by", "example": "add-material --file s.png --scope group --group gXXXX --role start"},
        {"name": "时长", "desc": "秒 → 帧数(24fps, 吸附 17k+5)", "values": "5s→124, 3s→73, 4s→107"},
        {"name": "LoRA", "desc": "每段 LoRA 栈", "values": "name=strength；≤8；强度[-10,10]", "example": "--lora 'x.safetensors=0.8'"},
        {"name": "角色替换(向导)", "desc": "本地 VLM 检测公共角色(目标)与分段角色(被替换)，逐个把分段角色替换为公共角色，可跳过", "values": "主菜单 → 分段 → 3", "example": ""},
    ]},
    {"key": "output", "title": "输出(output)", "summary": "分辨率/连续性/音频/参考图/导出（set-output）", "items": [
        {"name": "--width --height", "desc": "输出分辨率", "default": "864x480(16:9)", "example": "set-output --width 1280 --height 720"},
        {"name": "--long-edge", "desc": "参考图长边", "default": "参考图最大边"},
        {"name": "--continuity on|off", "desc": "段间连续性", "default": "off", "example": "set-output --continuity on"},
        {"name": "--continuity-overlap N", "desc": "上下文帧", "default": "22", "values": "5/22/39/56"},
        {"name": "--continuity-mode guide|continue", "desc": "引导 / 引导+重绘", "default": "guide"},
        {"name": "--continuity-redraw F", "desc": "重绘幅度(continue 时)", "default": "0.10"},
        {"name": "--audio-mode generate|source|mute", "desc": "音频", "default": "generate"},
        {"name": "--ref-image-size match|max", "desc": "参考图适配", "default": "match"},
        {"name": "--export-mode all|select", "desc": "导出范围", "default": "all"},
        {"name": "--max-export-frames N", "desc": "导出帧上限", "default": "0(全部)"},
    ]},
    {"key": "source", "title": "源视频", "summary": "v2v/rv2v 的源素材", "items": [
        {"name": "set-source --file", "desc": "设置源视频", "example": "set-source --file clip.mp4"},
    ]},
    {"key": "io", "title": "导入/导出/查看", "summary": "生成与读取导演包", "items": [
        {"name": "export [--out]", "desc": "导出 .mmxpack.zip", "example": "export"},
        {"name": "import-pack <zip> [--name]", "desc": "读取旧包 → 工程", "example": "import-pack old.zip --name old"},
        {"name": "validate", "desc": "校验上限/缺失/强度", "example": "validate"},
        {"name": "show [--group gid]", "desc": "查看内容(按分组)", "example": "show"},
    ]},
    {"key": "pack", "title": "导演包结构", "summary": ".mmxpack.zip 内部（ASCII 路径）", "items": [
        {"name": "pack.json", "desc": "format / formatVersion / taskType / widgets / output"},
        {"name": "shared_params/", "desc": "shared_params.json + PictureN / VideoN / AudioN"},
        {"name": "asset_groups/NN/", "desc": "group.json + PictureN / VideoN / AudioN / start.* / end.*"},
        {"name": "source_video/", "desc": "clip_1.*（v2v/rv2v）"},
    ]},
]

def capabilities(section=None, full=False):
    cats = CAPABILITIES
    if section:
        cats = [c for c in cats if c["key"] == section]
    if full or section:
        out = cats
    else:
        out = [{"key": c["key"], "title": c["title"], "summary": c["summary"],
                "items": [i["name"] for i in c["items"]]} for c in cats]
    return {"ok": True, "categories": out}

# ------------------------------------------------------------------ 工具函数
def frames_from_sec(seconds, fps=24.0):
    """官方公式: max(5, round(a*fps)) 吸附到 17k+5。"""
    a = max(0.1, float(seconds or 0.1))
    rate = max(1.0, float(fps or 24.0))
    n = max(5, int(round(a * rate)))
    return n + ((5 - (n % 17)) % 17)

def _ext(path, allowed, fallback):
    e = Path(str(path)).suffix.lower()
    if e == ".jpeg":
        e = ".jpg"
    return e if e in allowed else fallback

def _jprint(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))

def _safe_stem(s):
    s = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]+", "_", str(s or "")).strip("_")
    return s or "media"

def _project_dir(root, name):
    return Path(root) / name

def _load(root, name):
    p = _project_dir(root, name) / "project.json"
    if not p.is_file():
        raise SystemExit(f"[错误] 找不到工程: {name} ({p})")
    return json.loads(p.read_text(encoding="utf-8"))

def _save(root, proj):
    d = _project_dir(root, proj["name"])
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(proj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(d / "project.json")

def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")

# ---- 活动工程指针（agent 流程用：不指定名时用当前活动工程）----
def _active_path():
    return Path(__file__).resolve().parent / "active.json"

def set_active(name):
    _active_path().write_text(json.dumps({"name": name, "since": _now()}, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "active": name}

def get_active():
    p = _active_path()
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("name")
        except Exception:
            return None
    return None

def active_project(root):
    a = get_active()
    return {"ok": bool(a), "active": a, "exists": bool(a and (_project_dir(root, a) / "project.json").is_file())}

def resolve_name(root, name):
    if name:
        return name
    a = get_active()
    if not a:
        raise SystemExit("[错误] 未指定工程，且没有活动工程。先 `new <名>` 或 `use <名>`。")
    return a

# ------------------------------------------------------------------ 工程模型
def new_project(root, name, task=DEFAULT_TASK):
    task = task if task in TASK_KEYS else DEFAULT_TASK
    d = _project_dir(root, name)
    (d / "media").mkdir(parents=True, exist_ok=True)
    proj = {
        "schema": "mh3-director-project",
        "schemaVersion": 1,
        "name": name,
        "task_type": task,
        "created": _now(),
        "updated": _now(),
        "widgets": dict(DEFAULT_WIDGETS),
        "output": {},
        "shared": {"common_enabled": True, "prompt": "", "images": [], "videos": [], "audios": []},
        "groups": [],
        "source_video": None,
    }
    path = _save(root, proj)
    return {"ok": True, "project": path, "name": name, "task_type": task}

def _normalize_user_path(p):
    if p is None:
        return p
    s = str(p).strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        s = s[1:-1].strip()
    return os.path.expandvars(os.path.expanduser(s))

def _copy_media(root, proj, src, kind):
    src = Path(_normalize_user_path(src))
    if not src.is_file():
        raise SystemExit(f"[错误] 素材不存在: {src}（粘贴路径别带引号；向导里也可用 L 选最近文件）")
    allowed = {"image": IMG_EXT, "video": VID_EXT, "audio": AUD_EXT}[kind]
    d = _project_dir(root, proj["name"]) / "media"
    d.mkdir(parents=True, exist_ok=True)
    base = _safe_stem(src.stem) + src.suffix.lower()
    dst = d / base
    n = 1
    while dst.exists() and dst.resolve() != src.resolve():
        dst = d / f"{_safe_stem(src.stem)}_{n}{src.suffix.lower()}"; n += 1
    if dst.resolve() != src.resolve():
        shutil.copy2(src, dst)
    return f"media/{dst.name}"

def add_material(root, name, file, scope="common", group=None, kind=None, mname="", role="ref", decided_by="agent"):
    proj = _load(root, name)
    kind = (kind or "").lower()
    if not kind:
        e = Path(file).suffix.lower()
        kind = "image" if e in IMG_EXT else ("video" if e in VID_EXT else "audio")
    if kind not in ("image", "video", "audio"):
        raise SystemExit("[错误] kind 须为 image/video/audio")
    rel = _copy_media(root, proj, file, kind)
    entry = {"name": mname or Path(file).stem, "file": rel,
             "decided_by": (decided_by if decided_by in ("user", "agent") else "agent"), "at": _now()}
    key = {"image": "images", "video": "videos", "audio": "audios"}[kind]
    if scope == "common":
        proj["shared"][key].append(entry)
        where = "shared"
    else:
        gid = group
        g = next((g for g in proj["groups"] if g["id"] == gid), None)
        if g is None:
            raise SystemExit(f"[错误] 找不到分组: {gid}（先用 add-group 创建）")
        if role in ("start", "end"):
            if kind != "image":
                raise SystemExit("[错误] start/end 只能是图片")
            g["start_image" if role == "start" else "end_image"] = entry
            where = f"{gid}:{role}"
        else:
            g[key].append(entry)
            where = gid
    proj["updated"] = _now()
    _save(root, proj)
    return {"ok": True, "scope": where, "kind": kind, "role": role, "name": entry["name"], "file": rel}

def _parse_lora(spec):
    """'name=strength' 或 'name:strength' 或 dict -> {name,strength,active}"""
    if isinstance(spec, dict):
        name = str(spec.get("name") or "").strip()
        strength = spec.get("strength", 1.0)
        active = spec.get("active", True)
    else:
        s = str(spec)
        name, sep, st = s.partition("=")
        if not sep:
            name, sep, st = s.partition(":")
        try:
            strength = float(st) if sep and st.strip() else 1.0
        except ValueError:
            strength = 1.0
        active = True
    return {"name": name.strip(), "strength": float(strength), "active": bool(active)}

def _norm_loras(specs):
    return [r for r in (_parse_lora(s) for s in (specs or [])) if r["name"]][:MAX_LORAS]

def add_group(root, name, gname="", prompt="", duration=5.0, task_type="", loras=None, decided_by="agent"):
    proj = _load(root, name)
    gid = "g" + uuid.uuid4().hex[:6]
    fc = frames_from_sec(duration)
    start = 0
    for g in proj["groups"]:
        start += int(g.get("frame_count") or 124)
    proj["groups"].append({
        "id": gid, "name": gname or "", "prompt": prompt, "negative": "",
        "duration_sec": float(duration), "frame_count": fc, "start": start,
        "task_type": task_type, "images": [], "videos": [], "audios": [],
        "continuity_from_prev": False, "start_image": None, "end_image": None,
        "loras": _norm_loras(loras),
        "decided_by": (decided_by if decided_by in ("user", "agent") else "agent"), "at": _now(),
    })
    proj["updated"] = _now(); _save(root, proj)
    return {"ok": True, "group": gid, "frame_count": fc, "duration_sec": float(duration),
            "loras": [r["name"] for r in _norm_loras(loras)]}

def _reflow_starts(proj):
    cur = 0
    for g in proj["groups"]:
        g["start"] = cur
        cur += int(g.get("frame_count") or 124)

def set_group(root, name, gid, prompt=None, duration=None, gname=None, from_prev=None, negative=None,
              lora=None, lora_clear=False):
    proj = _load(root, name)
    g = next((g for g in proj["groups"] if g["id"] == gid), None)
    if g is None:
        raise SystemExit(f"[错误] 找不到分组: {gid}")
    if prompt is not None: g["prompt"] = prompt
    if negative is not None: g["negative"] = negative
    if gname is not None: g["name"] = gname
    if duration is not None:
        g["duration_sec"] = float(duration); g["frame_count"] = frames_from_sec(duration)
    if from_prev is not None:
        g["continuity_from_prev"] = str(from_prev).lower() in ("1", "true", "on", "yes")
    if lora_clear:
        g["loras"] = []
    if lora:
        g["loras"] = ((g.get("loras") or []) + _norm_loras(lora))[:MAX_LORAS]
    _reflow_starts(proj)
    proj["updated"] = _now(); _save(root, proj)
    return {"ok": True, "group": gid, "frame_count": g.get("frame_count"),
            "duration_sec": g.get("duration_sec"),
            "loras": [r["name"] for r in (g.get("loras") or [])]}

def del_group(root, name, gid):
    proj = _load(root, name)
    before = len(proj["groups"])
    proj["groups"] = [g for g in proj["groups"] if g["id"] != gid]
    if len(proj["groups"]) == before:
        raise SystemExit(f"[错误] 找不到分组: {gid}")
    _reflow_starts(proj); proj["updated"] = _now(); _save(root, proj)
    return {"ok": True, "removed": gid}

def set_common(root, name, prompt=None, enable=None):
    proj = _load(root, name)
    if prompt is not None: proj["shared"]["prompt"] = prompt
    if enable is not None:
        proj["shared"]["common_enabled"] = str(enable).lower() in ("1", "true", "on", "yes")
    proj["updated"] = _now(); _save(root, proj)
    return {"ok": True, "common_enabled": proj["shared"]["common_enabled"]}

def set_source(root, name, file):
    proj = _load(root, name)
    rel = _copy_media(root, proj, file, "video")
    proj["source_video"] = {"file": rel, "name": Path(file).stem}
    proj["updated"] = _now(); _save(root, proj)
    return {"ok": True, "source_video": rel}

def set_output(root, name, fields):
    """设置输出参数(分辨率/连续性/导出等); 只覆盖传入的键, 其余保留。"""
    proj = _load(root, name)
    out = dict(proj.get("output") or {})
    for k, v in (fields or {}).items():
        if v is None:
            continue
        out[k] = v
    proj["output"] = out
    proj["updated"] = _now(); _save(root, proj)
    return {"ok": True, "output": out}

def list_projects(root):
    root = Path(root)
    items = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            pj = d / "project.json"
            if pj.is_file():
                p = json.loads(pj.read_text(encoding="utf-8"))
                items.append({"name": p.get("name"), "task_type": p.get("task_type"),
                              "groups": len(p.get("groups") or []),
                              "shared_images": len((p.get("shared") or {}).get("images") or []),
                              "path": str(pj)})
    return {"ok": True, "projects": items}

def _mat_brief(m):
    return {"name": m.get("name"), "decided_by": m.get("decided_by", "unknown")}

def show_project(root, name, group=None):
    proj = _load(root, name)
    sh = proj.get("shared") or {}
    summary = {
        "ok": True, "name": proj["name"], "task_type": proj["task_type"],
        "shared": {"common_enabled": sh.get("common_enabled", True), "prompt": sh.get("prompt", ""),
                   "images": [_mat_brief(m) for m in sh.get("images", [])],
                   "videos": [_mat_brief(m) for m in sh.get("videos", [])],
                   "audios": [_mat_brief(m) for m in sh.get("audios", [])]},
        "source_video": proj.get("source_video"),
        "output": proj.get("output") or {},
        "groups": [{
            "id": g["id"], "name": g.get("name", ""), "prompt": g.get("prompt", ""),
            "duration_sec": g.get("duration_sec"), "frame_count": g.get("frame_count"),
            "start": g.get("start"), "continuity_from_prev": g.get("continuity_from_prev", False),
            "decided_by": g.get("decided_by", "unknown"),
            "images": [_mat_brief(m) for m in g.get("images", [])],
            "videos": [_mat_brief(m) for m in g.get("videos", [])],
            "audios": [_mat_brief(m) for m in g.get("audios", [])],
            "loras": [r.get("name") for r in g.get("loras", [])],
            "start_image": (g.get("start_image") or {}).get("name") if isinstance(g.get("start_image"), dict) else None,
            "end_image": (g.get("end_image") or {}).get("name") if isinstance(g.get("end_image"), dict) else None,
        } for g in proj.get("groups", [])],
    }
    if group:
        summary["groups"] = [g for g in summary["groups"] if g["id"] == group] or summary["groups"]
    return summary

def report_text(root, name):
    """人类可读的导演包内容摘要（纯文本，按分组展示）。"""
    s = show_project(root, name)
    L = []
    L.append(f"=== 导演包内容：{s['name']}  [{s['task_type']}] ===")
    sh = s["shared"]
    L.append(f"公共提示词：{sh['prompt'] or '（空）'}")
    common = []
    for kind, key in (("图", "images"), ("视频", "videos"), ("音频", "audios")):
        for m in sh[key]:
            common.append(f"[{kind}]{m['name']}（来源:{m['decided_by']}）")
    L.append(f"公共素材：{'  '.join(common) if common else '（无）'}")
    out = s.get("output") or {}
    if out:
        L.append("输出：%sx%s  连续性=%s(overlap=%s)  音频=%s  参考图=%s  导出=%s" % (
            out.get("width", "?"), out.get("height", "?"),
            "on" if out.get("continuityEnabled") else "off", out.get("continuityOverlapFrames", 22),
            out.get("audioMode", "generate"), out.get("refImageSize", "match"), out.get("exportMode", "all")))
    L.append("")
    L.append(f"分段（共 {len(s['groups'])} 段）：")
    for i, g in enumerate(s["groups"], 1):
        cont = "   ← 接上一段" if g.get("continuity_from_prev") else ""
        L.append(f"{i}. {g.get('name') or g['id']}  [{g['id']}]  时长 {g.get('duration_sec')}s / {g.get('frame_count')}帧"
                 f"  来源:{g.get('decided_by','unknown')}{cont}")
        L.append(f"   提示词：{g.get('prompt') or '（空）'}")
        mats = []
        for kind, key in (("图", "images"), ("视频", "videos"), ("音频", "audios")):
            for m in g[key]:
                mats.append(f"[{kind}]{m['name']}（{m['decided_by']}）")
        if g.get("start_image"):
            mats.append(f"[首帧]{g['start_image']}")
        if g.get("end_image"):
            mats.append(f"[尾帧]{g['end_image']}")
        L.append(f"   素材：{'  '.join(mats) if mats else '（无）'}")
        if g.get("loras"):
            L.append(f"   LoRA：{', '.join(g['loras'])}")
    return "\n".join(L)

# ------------------------------------------------------------------ 导出 .mmxpack.zip
def export_pack(root, name, out=None):
    proj = _load(root, name)
    pdir = _project_dir(root, name)
    task = proj.get("task_type", DEFAULT_TASK)
    staging = Path(tempfile.mkdtemp(prefix="mh3pack_"))
    shared_json = {"commonEnabled": bool((proj.get("shared") or {}).get("common_enabled", True)),
                   "commonCollapsed": False,
                   "prompt": (proj.get("shared") or {}).get("prompt", ""),
                   "refs": [], "refAudios": [], "refVideos": []}
    img_idx = 0
    def cp(rel, dstrel):
        src = pdir / rel
        if not src.is_file():
            raise SystemExit(f"[错误] 工程内素材缺失: {rel}")
        (staging / dstrel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, staging / dstrel)
    # shared
    (staging / "shared_params").mkdir(parents=True, exist_ok=True)
    (staging / "asset_groups").mkdir(parents=True, exist_ok=True)
    for m in (proj.get("shared") or {}).get("images", []):
        if img_idx >= MAX_IMG: raise SystemExit(f"[错误] 图片总数超过 {MAX_IMG}（H3 上限）")
        ext = _ext(m["file"], IMG_EXT, ".png"); dst = f"shared_params/Picture{img_idx+1}{ext}"
        cp(m["file"], dst)
        shared_json["refs"].append({"index": img_idx, "imageFile": dst, "fileName": Path(dst).name,
                                    "type": "input", "subfolder": "shared_params"})
        img_idx += 1
    for i, m in enumerate((proj.get("shared") or {}).get("videos", [])[:MAX_VID]):
        ext = _ext(m["file"], VID_EXT, ".mp4"); dst = f"shared_params/Video{i+1}{ext}"; cp(m["file"], dst)
        shared_json["refVideos"].append({"index": i, "videoFile": dst, "fileName": Path(dst).name,
                                         "type": "input", "subfolder": "shared_params"})
    for i, m in enumerate((proj.get("shared") or {}).get("audios", [])[:MAX_AUD]):
        ext = _ext(m["file"], AUD_EXT, ".wav"); dst = f"shared_params/Audio{i+1}{ext}"; cp(m["file"], dst)
        shared_json["refAudios"].append({"index": i, "audioFile": dst, "fileName": Path(dst).name,
                                         "type": "input", "subfolder": "shared_params"})
    (staging / "shared_params" / "shared_params.json").write_text(
        json.dumps(shared_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # groups
    for gi, g in enumerate(proj.get("groups", [])):
        folder = f"asset_groups/{gi+1:02d}"
        gj = {"id": g.get("id") or f"g{gi}", "prompt": g.get("prompt", ""), "negativePrompt": g.get("negative", ""),
              "durationSec": g.get("duration_sec"), "frameCount": g.get("frame_count"),
              "length": g.get("frame_count"), "start": g.get("start"), "taskType": g.get("task_type", ""),
              "refs": [], "refAudios": [], "refVideos": [],
              "continuityFromPrev": bool(g.get("continuity_from_prev", False)),
              "refImageSize": None, "loras": g.get("loras", [])}
        for m in g.get("images", []):
            if img_idx >= MAX_IMG: raise SystemExit(f"[错误] 图片总数超过 {MAX_IMG}（H3 上限）")
            ext = _ext(m["file"], IMG_EXT, ".png"); dst = f"{folder}/Picture{img_idx+1}{ext}"; cp(m["file"], dst)
            gj["refs"].append({"index": img_idx, "imageFile": dst, "fileName": Path(dst).name,
                               "type": "input", "subfolder": folder})
            img_idx += 1
        for i, m in enumerate(g.get("videos", [])[:MAX_VID]):
            ext = _ext(m["file"], VID_EXT, ".mp4"); dst = f"{folder}/Video{i+1}{ext}"; cp(m["file"], dst)
            gj["refVideos"].append({"index": i, "videoFile": dst, "fileName": Path(dst).name,
                                    "type": "input", "subfolder": folder})
        for i, m in enumerate(g.get("audios", [])[:MAX_AUD]):
            ext = _ext(m["file"], AUD_EXT, ".wav"); dst = f"{folder}/Audio{i+1}{ext}"; cp(m["file"], dst)
            gj["refAudios"].append({"index": i, "audioFile": dst, "fileName": Path(dst).name,
                                    "type": "input", "subfolder": folder})
        for key, stem in (("start_image", "start"), ("end_image", "end")):
            v = g.get(key)
            if isinstance(v, dict) and v.get("file"):
                ext = _ext(v["file"], IMG_EXT, ".jpg"); dst = f"{folder}/{stem}{ext}"; cp(v["file"], dst)
                rec = {"imageFile": dst, "fileName": Path(dst).name, "type": "input", "subfolder": folder,
                       "width": 0, "height": 0}
                gj[key] = rec
                if stem == "start" and not (gj.get("genImage") or {}).get("imageFile"):
                    gj["genImage"] = {"imageFile": dst, "fileName": Path(dst).name}
        (staging / folder).mkdir(parents=True, exist_ok=True)
        (staging / folder / "group.json").write_text(json.dumps(gj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # source video (v2v)
    if isinstance(proj.get("source_video"), dict) and proj["source_video"].get("file"):
        ext = _ext(proj["source_video"]["file"], VID_EXT, ".mp4"); dst = f"source_video/clip_1{ext}"
        cp(proj["source_video"]["file"], dst)
    # pack.json
    pack_meta = {"format": PACK_FORMAT, "formatVersion": PACK_VERSION, "taskType": task,
                 "widgets": {k: proj.get("widgets", {}).get(k, DEFAULT_WIDGETS[k]) for k in WIDGET_KEYS},
                 "output": proj.get("output") or {}}
    (staging / "pack.json").write_text(json.dumps(pack_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not out:
        DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = str(DEFAULT_EXPORT_DIR / f"{name}.mmxpack.zip")
    out = Path(out); out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(staging.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(staging)).replace("\\", "/")
                zf.write(p, rel)
    shutil.rmtree(staging, ignore_errors=True)
    size = out.stat().st_size
    return {"ok": True, "zip": str(out), "bytes": size, "task_type": task,
            "images": img_idx, "groups": len(proj.get("groups", []))}

def validate_pack(root, name):
    proj = _load(root, name)
    warnings, errors = [], []
    img = len((proj.get("shared") or {}).get("images", [])) + sum(len(g.get("images", [])) for g in proj.get("groups", []))
    if img > MAX_IMG: errors.append(f"图片总数 {img} > 上限 {MAX_IMG}")
    for g in proj.get("groups", []):
        if len(g.get("videos", [])) > MAX_VID: errors.append(f"组 {g['id']} 视频 > {MAX_VID}")
        if len(g.get("audios", [])) > MAX_AUD: errors.append(f"组 {g['id']} 音频 > {MAX_AUD}")
        if len(g.get("loras", [])) > MAX_LORAS: errors.append(f"组 {g['id']} LoRA 数 > {MAX_LORAS}")
        for r in g.get("loras", []):
            try:
                s = float(r.get("strength", 1.0))
                if not (-10.0 <= s <= 10.0): warnings.append(f"组 {g['id']} LoRA {r.get('name')} 强度 {s} 超出 [-10,10]")
            except (TypeError, ValueError):
                warnings.append(f"组 {g['id']} LoRA {r.get('name')} 强度非法")
        if not (g.get("prompt") or "").strip(): warnings.append(f"组 {g['id']} 没有提示词")
    if not proj.get("groups"): warnings.append("没有任何分组")
    if proj.get("task_type") in ("v2v", "rv2v") and not proj.get("source_video"):
        errors.append("v2v/rv2v 需要 source_video")
    return {"ok": not errors, "errors": errors, "warnings": warnings}

# ------------------------------------------------------------------ 导入 .mmxpack.zip -> 工程
def import_pack(root, zip_path, name=None):
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise SystemExit(f"[错误] 找不到 zip: {zip_path}")
    name = name or re.sub(r"\.mmxpack\.zip$", "", zip_path.name, flags=re.I) or ("pack_" + uuid.uuid4().hex[:6])
    ext_dir = Path(tempfile.mkdtemp(prefix="mh3imp_"))
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(ext_dir)
    meta = {}
    if (ext_dir / "pack.json").is_file():
        meta = json.loads((ext_dir / "pack.json").read_text(encoding="utf-8"))
    task = str(meta.get("taskType") or "r2v").split(" — ")[0].split(" - ")[0].strip() or "r2v"
    d = _project_dir(root, name); (d / "media").mkdir(parents=True, exist_ok=True)
    proj = {"schema": "mh3-director-project", "schemaVersion": 1, "name": name, "task_type": task,
            "created": _now(), "updated": _now(),
            "widgets": {**DEFAULT_WIDGETS, **(meta.get("widgets") or {})}, "output": meta.get("output") or {},
            "shared": {"common_enabled": True, "prompt": "", "images": [], "videos": [], "audios": []},
            "groups": [], "source_video": None}
    media_dir = d / "media"
    def take(src_rel, kind, mname=""):
        src = ext_dir / src_rel
        if not src.is_file(): return None
        base = _safe_stem(Path(src_rel).name) + src.suffix.lower(); dst = media_dir / base; n = 1
        while dst.exists():
            dst = media_dir / f"{_safe_stem(Path(src_rel).stem)}_{n}{src.suffix.lower()}"; n += 1
        shutil.copy2(src, dst)
        return {"name": mname or Path(src_rel).stem, "file": f"media/{dst.name}"}
    def read_json(p):
        try: return json.loads((ext_dir / p).read_text(encoding="utf-8"))
        except Exception: return {}
    sh = read_json("shared_params/shared_params.json")
    proj["shared"]["common_enabled"] = bool(sh.get("commonEnabled", True))
    proj["shared"]["prompt"] = sh.get("prompt", "")
    for r in sh.get("refs", []):
        e = take(r.get("imageFile", ""), "image"); 
        if e: proj["shared"]["images"].append(e)
    for r in sh.get("refVideos", []):
        e = take(r.get("videoFile", ""), "video")
        if e: proj["shared"]["videos"].append(e)
    for r in sh.get("refAudios", []):
        e = take(r.get("audioFile", ""), "audio")
        if e: proj["shared"]["audios"].append(e)
    grp_root = ext_dir / "asset_groups"
    if grp_root.is_dir():
        for gdir in sorted([p for p in grp_root.iterdir() if p.is_dir()], key=lambda p: p.name):
            gj = read_json(f"asset_groups/{gdir.name}/group.json")
            g = {"id": gj.get("id") or gdir.name, "name": "", "prompt": gj.get("prompt", ""),
                 "negative": gj.get("negativePrompt", ""), "duration_sec": gj.get("durationSec"),
                 "frame_count": gj.get("frameCount") or gj.get("length") or 124, "start": gj.get("start") or 0,
                 "task_type": gj.get("taskType", ""), "images": [], "videos": [], "audios": [],
                 "continuity_from_prev": bool(gj.get("continuityFromPrev", False)),
                 "loras": gj.get("loras", []), "start_image": None, "end_image": None}
            for r in gj.get("refs", []):
                e = take(r.get("imageFile", ""), "image")
                if e: g["images"].append(e)
            for r in gj.get("refVideos", []):
                e = take(r.get("videoFile", ""), "video")
                if e: g["videos"].append(e)
            for r in gj.get("refAudios", []):
                e = take(r.get("audioFile", ""), "audio")
                if e: g["audios"].append(e)
            for key, fld in (("startImage", "start_image"), ("endImage", "end_image")):
                v = gj.get(key)
                if isinstance(v, dict) and v.get("imageFile"):
                    e = take(v["imageFile"], "image", mname=fld)
                    if e: g[fld] = e
            proj["groups"].append(g)
    sv = ext_dir / "source_video"
    if sv.is_dir():
        vids = sorted([p for p in sv.iterdir() if p.is_file() and p.suffix.lower() in VID_EXT])
        if vids:
            e = take(f"source_video/{vids[0].name}", "video", mname="source")
            if e: proj["source_video"] = e
    _reflow_starts(proj)
    _save(root, proj)
    shutil.rmtree(ext_dir, ignore_errors=True)
    return {"ok": True, "name": name, "task_type": task, "groups": len(proj["groups"]),
            "shared_images": len(proj["shared"]["images"]), "project": str(_project_dir(root, name) / "project.json")}

# ------------------------------------------------------------------ 配置 & 提示词优化(可插拔)
DEFAULT_CONFIG_PATH = APP_DIR / "config.json"

def _default_config():
    here = APP_DIR
    return {
        "enhance": {
            "backend": "auto",
            "target": "h3",
            "media_dirs": [],
            "vlm": {
                "python": "",
                "script": str((here / ".." / "reverse_prompt" / "reverse_prompt.py").resolve()),
                "server": "http://127.0.0.1:8080",
                "model": "",
                "api_key": "",
                "auto_start": True,
                "auto_stop": True,
                "launch": {
                    "exe": "",
                    "cwd": "",
                    "startup_timeout": 480,
                    "args": [],
                },
            },
            "api": {
                "base_url": "https://api.openai.com/v1",
                "api_key": "",
                "model": "",
                "no_template_kwargs": True,
            },
            "director": {
                "url": "http://127.0.0.1:8188",
                "llm_url": "",
                "model": "",
                "api_format": "openai_compat",
                "api_key": "",
                "output_language": "en",
            },
        }
    }

def _deep_merge(base, extra):
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out

def load_config(path=None):
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    data = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    return _deep_merge(_default_config(), data)

def save_config(cfg, path=None):
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(p)

def update_config(patch, path=None):
    """把 patch 深度合并进 config.json 并保存。返回 {ok, path, config}。"""
    if isinstance(patch, str):
        try:
            patch = json.loads(patch or "{}")
        except Exception as e:
            return {"ok": False, "error": f"patch JSON 解析失败: {e}"}
    cfg = load_config(path)
    cfg = _deep_merge(cfg, patch or {})
    save_config(cfg, path)
    return {"ok": True, "path": str(Path(path) if path else DEFAULT_CONFIG_PATH), "config": cfg}

def _api_request(base_url, api_key, model, messages, max_tokens=8, timeout=30):
    import urllib.request, urllib.error
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "未填 base_url（如 https://api.openai.com/v1）"}
    url = base + "/chat/completions"
    payload = {"model": model or "", "messages": messages, "max_tokens": max_tokens}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + str(api_key)
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"ok": True, "data": json.loads(r.read().decode("utf-8", "replace"))}
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            msg = ""
        return {"ok": False, "error": f"HTTP {e.code}: {msg}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

def test_connection(kind="vlm", patch=None):
    """测试本地 VLM 或在线 API 是否可用。patch 可选，用于测试未保存的表单值。"""
    cfg = load_config()
    if patch:
        if isinstance(patch, str):
            try:
                patch = json.loads(patch or "{}")
            except Exception:
                patch = {}
        cfg = _deep_merge(cfg, patch)
    if str(kind).lower() in ("api", "remote"):
        a = cfg["enhance"].get("api") or {}
        r = _api_request(a.get("base_url"), a.get("api_key"), a.get("model"),
                         [{"role": "user", "content": "ping"}], max_tokens=4, timeout=30)
        reply = None
        if r.get("ok"):
            try:
                reply = r["data"]["choices"][0]["message"].get("content")
            except Exception:
                reply = ""
        return {"ok": bool(r.get("ok")), "kind": "api", "base_url": a.get("base_url"),
                "model": a.get("model"), "reply": reply, "error": r.get("error")}
    s = (cfg["enhance"]["vlm"].get("server") or "").rstrip("/")
    ok = _vlm_health(s)
    return {"ok": ok, "kind": "vlm", "server": s,
            "error": None if ok else f"本地 VLM 不可达（{s or '未填 server'}）"}

def _http_post_json(url, payload, timeout=300):
    import urllib.request
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def _http_ok(url, timeout=3):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= int(getattr(r, "status", 200)) < 500
    except Exception:
        return False

def _pick_backend(cfg, hint=None):
    hint = str(hint or cfg["enhance"].get("backend") or "auto").lower()
    if hint in ("vlm", "local"):
        return "vlm"
    if hint in ("api", "remote"):
        return "api"
    if hint == "director":
        return "director"
    vlm_server = (cfg["enhance"]["vlm"].get("server") or "").rstrip("/")
    if vlm_server and _http_ok(vlm_server + "/health"):
        return "vlm"
    if (cfg["enhance"].get("api") or {}).get("api_key"):
        return "api"
    d = cfg["enhance"]["director"]
    if d.get("url") and _http_ok(d["url"].rstrip("/") + "/system_stats"):
        return "director"
    return "vlm"

def _local_profile(cfg):
    v = cfg["enhance"]["vlm"]
    return {"server": v.get("server"), "model": v.get("model"), "api_key": v.get("api_key")}

def _api_profile(cfg):
    a = cfg["enhance"].get("api") or {}
    return {"server": a.get("base_url"), "model": a.get("model"), "api_key": a.get("api_key"),
            "no_template_kwargs": bool(a.get("no_template_kwargs", True))}

def _rp_env(profile):
    env = dict(os.environ)
    p = profile or {}
    server = p.get("server") or p.get("base_url") or ""
    if server:
        env["RP_SERVER"] = str(server)
    if p.get("model"):
        env["RP_MODEL"] = str(p["model"])
    if p.get("api_key"):
        env["RP_API_KEY"] = str(p["api_key"])
    if p.get("no_template_kwargs"):
        env["RP_NO_TEMPLATE_KWARGS"] = "1"
    return env

_VLM_PROC = None
VLM_PID_FILE = APP_DIR / ".vlm.pid"

def _write_vlm_pid(pid):
    try:
        VLM_PID_FILE.write_text(json.dumps({"pid": int(pid), "at": _now()}), encoding="utf-8")
    except Exception:
        pass

def _read_vlm_pid():
    try:
        return int(json.loads(VLM_PID_FILE.read_text(encoding="utf-8")).get("pid"))
    except Exception:
        return None

def _clear_vlm_pid():
    try:
        VLM_PID_FILE.unlink()
    except Exception:
        pass

def _vlm_health(server, timeout=3):
    server = (server or "").rstrip("/")
    return bool(server) and _http_ok(server + "/health", timeout)

def ensure_vlm(cfg, log=None):
    """确保 VLM 可用：已在跑则直接用；否则按 config 自动拉起。返回 {ok, started, error}。"""
    global _VLM_PROC
    v = cfg["enhance"]["vlm"]
    server = (v.get("server") or "").rstrip("/")
    if _vlm_health(server):
        return {"ok": True, "started": False}
    if not v.get("auto_start"):
        return {"ok": False, "started": False,
                "error": f"本地 VLM 未启动（{server}）；可手动运行 启动VLM.bat，或在 config.json 打开 enhance.vlm.auto_start"}
    launch = v.get("launch") or {}
    exe = _normalize_user_path(launch.get("exe") or "")
    if not exe or not Path(exe).is_file():
        return {"ok": False, "started": False,
                "error": f"未配置 VLM 启动程序（config.json 的 enhance.vlm.launch.exe），当前: {exe or '(空)'}"}
    import subprocess
    args = [exe] + [str(x) for x in (launch.get("args") or [])]
    if log:
        log("  正在启动本地 VLM（首次加载模型约数分钟，请稍候）...")
    try:
        _VLM_PROC = subprocess.Popen(args, cwd=launch.get("cwd") or str(Path(exe).parent))
        _write_vlm_pid(_VLM_PROC.pid)
    except Exception as e:
        return {"ok": False, "started": False, "error": f"启动 VLM 失败: {type(e).__name__}: {e}"}
    deadline = time.time() + float(launch.get("startup_timeout") or 480)
    while time.time() < deadline:
        if _vlm_health(server):
            if log:
                log("  VLM 已就绪。")
            return {"ok": True, "started": True}
        if _VLM_PROC.poll() is not None:
            return {"ok": False, "started": True, "error": f"VLM 进程退出（code {_VLM_PROC.returncode}）"}
        time.sleep(5)
    return {"ok": False, "started": True, "error": "VLM 启动超时"}

def stop_vlm(cfg=None):
    """只停止由本工具启动的 VLM（本进程句柄或 .vlm.pid）；用户手动启动的不动。"""
    global _VLM_PROC
    import os as _os, signal as _signal
    if _VLM_PROC is not None:
        proc = _VLM_PROC
        _VLM_PROC = None
        try:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except Exception:
                proc.kill()
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        _clear_vlm_pid()
        return {"ok": True, "stopped": True}
    pid = _read_vlm_pid()
    if not pid:
        return {"ok": True, "stopped": False}
    try:
        _os.kill(pid, _signal.SIGTERM)
    except Exception as e:
        _clear_vlm_pid()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    _clear_vlm_pid()
    return {"ok": True, "stopped": True}

def _enhance_vlm(cfg, draft, instruction, target, log=None, profile=None, ensure=True):
    import subprocess, tempfile
    if log is None:
        log = lambda s: print(s, file=sys.stderr)
    if ensure:
        ens = ensure_vlm(cfg, log=log)
        if not ens.get("ok"):
            return {"ok": False, "error": ens.get("error")}
    v = cfg["enhance"]["vlm"]
    script = v.get("script") or ""
    if not Path(script).is_file():
        return {"ok": False, "error": f"找不到 reverse_prompt.py：{script}（改 config.json 的 enhance.vlm.script）"}
    py = v.get("python") or sys.executable
    tmpdir = tempfile.mkdtemp(prefix="mh3enh_")
    draft_f = Path(tmpdir) / "draft.txt"; out_f = Path(tmpdir) / "revised.txt"
    draft_f.write_text(draft or "", encoding="utf-8")
    env = _rp_env(profile or _local_profile(cfg))
    cmd = [py, script, "--target", str(target or "h3"), "--revise", "@" + str(draft_f),
           "--instruction", str(instruction or ""), "--out", str(out_f)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900, env=env)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    text = out_f.read_text(encoding="utf-8").strip() if out_f.is_file() else ""
    if not text:
        text = (r.stdout or "").strip()
    if not text:
        return {"ok": False, "error": (r.stderr or r.stdout or "本地 VLM 未返回结果").strip()[:400]}
    return {"ok": True, "text": text, "backend": "vlm"}

def _enhance_director(cfg, draft, instruction, target):
    d = cfg["enhance"]["director"]
    if not d.get("url"):
        return {"ok": False, "error": "未配置导演台 URL"}
    if not d.get("model"):
        return {"ok": False, "error": "未配置 LLM 模型（改 config.json 的 enhance.director.model）"}
    prompt = draft or ""
    if instruction:
        prompt = prompt + "\n\n[修改要求]\n" + str(instruction)
    payload = {"prompt": prompt, "model": d.get("model"), "llm_url": d.get("llm_url") or "",
               "api_format": d.get("api_format") or "openai_compat", "api_key": d.get("api_key") or "",
               "task_type": str(target or "r2v"), "output_language": d.get("output_language") or "en"}
    try:
        res = _http_post_json(d["url"].rstrip("/") + "/minimax/director/enhance", payload, timeout=600)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if isinstance(res, dict) and res.get("response"):
        return {"ok": True, "text": res["response"], "backend": "director"}
    return {"ok": False, "error": (res or {}).get("error") or "导演台未返回结果"}

def enhance_prompt(root, name=None, scope="group", group=None, draft=None, instruction="",
                   target=None, backend=None, apply=False, config_path=None):
    """优化提示词：草稿+指令 -> 候选。apply=True 时才写回工程。绝不静默覆盖。"""
    cfg = load_config(config_path)
    backend = _pick_backend(cfg, backend)
    target = str(target or cfg["enhance"].get("target") or "h3")
    cur = draft
    proj = None
    if name:
        proj = _load(root, name)
    if cur is None and proj is not None:
        if scope == "common":
            cur = (proj.get("shared") or {}).get("prompt", "")
        else:
            g = next((g for g in proj.get("groups", []) if g["id"] == group), None)
            if g is None:
                return {"ok": False, "error": f"找不到分组: {group}"}
            cur = g.get("prompt", "")
    if not (cur or "").strip():
        return {"ok": False, "error": "草稿提示词为空（先写内容或用 --draft）"}
    if backend == "director":
        r = _enhance_director(cfg, cur, instruction, target)
    elif backend == "api":
        r = _enhance_vlm(cfg, cur, instruction, target, profile=_api_profile(cfg), ensure=False)
    else:
        r = _enhance_vlm(cfg, cur, instruction, target)
    r["backend"] = backend
    r["draft"] = cur
    if r.get("ok") and apply and proj is not None:
        if scope == "common":
            set_common(root, name, prompt=r["text"], enable="on")
        else:
            set_group(root, name, group, prompt=r["text"])
        r["applied"] = True
    return r

def _strip_reverse_meta(text):
    lines = [ln for ln in (text or "").splitlines() if not ln.lstrip().startswith("# meta:")]
    return "\n".join(lines).strip()

def _extract_reverse_text(text):
    t = (text or "").strip()
    if t.startswith("{"):
        try:
            d = json.loads(t)
            for k in ("rewritten_prompt", "prompt", "integrated_multimodal_description"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return _strip_reverse_meta(v.strip())
        except Exception:
            pass
    return _strip_reverse_meta(text)

def _reverse_vlm(cfg, file, target, log=None, profile=None, ensure=True):
    import subprocess, tempfile
    if log is None:
        log = lambda s: print(s, file=sys.stderr)
    if ensure:
        ens = ensure_vlm(cfg, log=log)
        if not ens.get("ok"):
            return {"ok": False, "error": ens.get("error")}
    v = cfg["enhance"]["vlm"]
    script = v.get("script") or ""
    if not Path(script).is_file():
        return {"ok": False, "error": f"找不到 reverse_prompt.py：{script}（改 config.json 的 enhance.vlm.script）"}
    f = _normalize_user_path(file)
    if not Path(f).is_file():
        return {"ok": False, "error": f"素材不存在: {f}（粘贴路径别带引号）"}
    py = v.get("python") or sys.executable
    tmpdir = tempfile.mkdtemp(prefix="mh3rev_")
    out_f = Path(tmpdir) / "reverse.txt"
    env = _rp_env(profile or _local_profile(cfg))
    cmd = [py, script, f, "--target", str(target or "h3"), "--out", str(out_f)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900, env=env)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    text = out_f.read_text(encoding="utf-8").strip() if out_f.is_file() else ""
    if not text:
        text = (r.stdout or "").strip()
    if not text:
        return {"ok": False, "error": (r.stderr or r.stdout or "本地 VLM 未返回结果").strip()[:400]}
    return {"ok": True, "text": _extract_reverse_text(text), "backend": "vlm"}

def reverse_from_media(root, name=None, file=None, scope="common", group=None, target=None,
                       combine="append", backend=None, apply=False, config_path=None):
    """从素材(图/视频)反推提示词；apply=True 时按 combine(append/replace) 合入公共或某段提示词。"""
    if not file:
        return {"ok": False, "error": "需要 --file 指定素材文件"}
    cfg = load_config(config_path)
    backend = _pick_backend(cfg, backend)
    target = str(target or cfg["enhance"].get("target") or "h3")
    if backend == "director":
        return {"ok": False, "backend": backend,
                "error": "反推素材需要本地/云端 LLM（vlm 或 api 后端）；导演台后端暂不支持图片反推"}
    if backend == "api":
        r = _reverse_vlm(cfg, file, target, profile=_api_profile(cfg), ensure=False)
    else:
        r = _reverse_vlm(cfg, file, target)
    r["backend"] = backend
    r["source"] = _normalize_user_path(file)
    if not r.get("ok") or not apply:
        return r
    text = r["text"]
    proj = _load(root, name)
    if scope == "common":
        cur = (proj.get("shared") or {}).get("prompt", "") or ""
        new = text if combine == "replace" or not cur.strip() else (cur.rstrip() + "\n\n" + text).strip()
        set_common(root, name, prompt=new, enable="on")
    else:
        g = next((g for g in proj.get("groups", []) if g["id"] == group), None)
        if g is None:
            return {"ok": False, "error": f"找不到分组: {group}"}
        cur = g.get("prompt", "") or ""
        new = text if combine == "replace" or not cur.strip() else (cur.rstrip() + "\n\n" + text).strip()
        set_group(root, name, group, prompt=new)
    r["applied"] = True
    r["combined"] = combine
    return r

# ------------------------------------------------------------------ 角色检测 & 替换 (本地 VLM)
def _vlm_chat(cfg, messages, max_tokens=1024, temperature=0.1, timeout=300, ensure=True, log=None):
    """直接调本地 VLM 的 OpenAI 兼容接口，返回 {ok, text} / {ok:False, error}。"""
    import urllib.request, urllib.error
    if ensure:
        ens = ensure_vlm(cfg, log=log)
        if not ens.get("ok"):
            return {"ok": False, "error": ens.get("error")}
    v = cfg["enhance"]["vlm"]
    server = (v.get("server") or "").rstrip("/")
    if not server:
        return {"ok": False, "error": "未配置本地 VLM server（config.json 的 enhance.vlm.server）"}
    payload = {"model": v.get("model") or "qwen", "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature,
               "chat_template_kwargs": {"enable_thinking": False}}
    headers = {"Content-Type": "application/json"}
    if v.get("api_key"):
        headers["Authorization"] = "Bearer " + str(v["api_key"])
    req = urllib.request.Request(server + "/v1/chat/completions",
                                 data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"连不上本地 VLM（{server}）：{e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        content = resp["choices"][0]["message"].get("content", "")
    except Exception:
        return {"ok": False, "error": f"VLM 返回格式异常: {str(resp)[:200]}"}
    return {"ok": True, "text": (content or "").strip()}

def _dedup_names(items):
    out, seen = [], set()
    for x in items or []:
        s = str(x).strip().strip('"\'“”').strip()
        if s and s.lower() not in seen:
            seen.add(s.lower()); out.append(s)
    return out

def _parse_characters(text):
    t = (text or "").strip()
    if "{" in t and "}" in t:
        try:
            d = json.loads(t[t.find("{"): t.rfind("}") + 1])
            arr = d.get("characters") or d.get("chars") or d.get("names") or d.get("角色")
            if isinstance(arr, list):
                return _dedup_names(arr)
        except Exception:
            pass
    out = []
    for ln in t.splitlines():
        s = ln.strip().lstrip("-*•").strip()
        s = re.sub(r"^[0-9]+[).、）]\s*", "", s).strip().strip('",').strip()
        if s and not s.startswith("{") and len(s) <= 40:
            out.append(s)
    return _dedup_names(out)

def detect_characters(cfg, text, log=None):
    """用本地 VLM 从一段提示词里检测角色名。返回 {ok, characters, error?}。"""
    text = (text or "").strip()
    if not text:
        return {"ok": True, "characters": []}
    sys_msg = "You are a precise text-analysis assistant. Output only JSON."
    usr = ("Extract the names of all distinct CHARACTERS / PERSONS (proper nouns) that appear in the "
           "prompt text below. Exclude clothing, props, locations, brands, and generic nouns. "
           'Return ONLY JSON: {"characters": ["Name1", "Name2"]}. If none, return {"characters": []}.\n\n'
           "TEXT:\n" + text)
    r = _vlm_chat(cfg, [{"role": "system", "content": sys_msg},
                        {"role": "user", "content": usr}],
                  max_tokens=512, temperature=0.0, log=log)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error")}
    return {"ok": True, "characters": _parse_characters(r.get("text")), "raw": r.get("text")}

def _replace_name(text, src, dst):
    """把 text 中的 src 替换成 dst；ASCII 名按词边界，CJK 直接替换。返回 (new, count)。"""
    if not text or not src:
        return text, 0
    if re.fullmatch(r"[A-Za-z0-9 _\-]+", src):
        pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(src) + r"(?![A-Za-z0-9_])")
        return pat.subn(dst, text)
    return text.replace(src, dst), text.count(src)

def replace_characters(root, name, src, dst, scope="all"):
    """把某分段角色名替换成公共角色名：默认替换到所有含提示词的分段。返回 {ok, changes}。"""
    proj = _load(root, name)
    changes = []
    for g in proj.get("groups", []):
        txt = g.get("prompt") or ""
        new, k = _replace_name(txt, src, dst)
        if k:
            set_group(root, name, g["id"], prompt=new)
            changes.append({"group": g["id"], "count": k})
    return {"ok": True, "src": src, "dst": dst, "changes": changes}

def detect_chars(root, name=None):
    """检测当前工程：公共提示词里的角色(替换目标) 与 各分段提示词里的角色(被替换)。"""
    nm = resolve_name(root, name) if (name or get_active()) else None
    if not nm:
        return {"ok": False, "error": "没有活动工程"}
    cfg = load_config()
    proj = _load(root, nm)
    common = ((proj.get("shared") or {}).get("prompt") or "").strip()
    pub = []
    if common:
        r = detect_characters(cfg, common)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error")}
        pub = r["characters"]
    segs = []
    for g in proj.get("groups", []):
        txt = (g.get("prompt") or "").strip()
        chars = []
        if txt:
            r = detect_characters(cfg, txt)
            if not r.get("ok"):
                return {"ok": False, "error": f"检测分段 {g['id']} 失败：{r.get('error')}"}
            chars = r["characters"]
        segs.append({"id": g["id"], "name": g.get("name") or "", "characters": chars})
    union, gids = [], {}
    for s in segs:
        for c in s["characters"]:
            gids.setdefault(c, [])
            if s["id"] not in gids[c]:
                gids[c].append(s["id"])
            if c not in union:
                union.append(c)
    return {"ok": True, "project": nm, "public": pub, "segments": segs,
            "segment_characters": [{"name": c, "groups": gids[c]} for c in union]}

def replace_char(root, name=None, src=None, dst=None):
    nm = resolve_name(root, name) if (name or get_active()) else None
    if not nm:
        return {"ok": False, "error": "没有活动工程"}
    if not src or not dst:
        return {"ok": False, "error": "需要 src（被替换）与 dst（替换成）"}
    return replace_characters(root, nm, src, dst)

def list_media(root=None, limit=60):
    try:
        items = [str(p) for p in _list_recent_media(int(limit or 60))]
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "files": items}

# ------------------------------------------------------------------ CLI
# ------------------------------------------------------------------ 交互向导
def _ask(prompt, default=""):
    try:
        s = input(prompt).strip()
    except EOFError:
        raise SystemExit("[wizard] 无交互输入(stdin 结束)。请在终端里直接运行 `wizard`；agent 请改用普通子命令。")
    return s or default

def _stdin_has_pending(grace=0.12):
    """标准输入缓冲区里是否还有未读内容（用于识别“粘贴进来的多段文本”）。非终端时返回 False。"""
    try:
        if not sys.stdin.isatty():
            return False
    except Exception:
        return False
    try:
        import msvcrt
    except Exception:
        return False
    try:
        if msvcrt.kbhit():
            return True
        end = time.time() + grace
        while time.time() < end:
            if msvcrt.kbhit():
                return True
            time.sleep(0.01)
    except Exception:
        return False
    return False

def _read_prompt_file(path):
    p = _normalize_user_path(path)
    last = None
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(p, "r", encoding=enc) as f:
                return f.read().strip()
        except UnicodeDecodeError as e:
            last = e
            continue
        except Exception as e:
            print(f"  [导入失败] {e}")
            return None
    print(f"  [导入失败] 无法解码文件: {p}（{last}）")
    return None

def _import_prompt_file(default=""):
    """从文本文件读取提示词（多行原文照读）。"""
    path = _ask("  提示词文件路径（.txt/.md 等文本，可直接粘贴，可带引号）> ", "")
    if not path:
        return default
    if path.strip().startswith("@"):
        path = path.strip()[1:].strip()
    txt = _read_prompt_file(path)
    if txt is None:
        return default
    print(f"  [已从文件导入 {len(txt)} 字符]")
    return txt

def _ask_prompt(label, default=""):
    """提示词输入：可直接粘贴多段文本(自动合并)；输入 @文件路径 从文件导入；回车=默认/取消。"""
    try:
        first = input(label)
    except EOFError:
        raise SystemExit("[wizard] 无交互输入(stdin 结束)。请在终端里直接运行 `wizard`；agent 请改用普通子命令。")
    s = first.strip()
    if s.startswith("@"):
        path = s[1:].strip()
        if not path:
            return _import_prompt_file(default)
        txt = _read_prompt_file(path)
        if txt is None:
            return default
        print(f"  [已从文件导入 {len(txt)} 字符]")
        return txt
    if not s:
        return default
    lines = [first.rstrip("\r\n")]
    while _stdin_has_pending():
        try:
            ln = input()
        except EOFError:
            break
        lines.append(ln.rstrip("\r\n"))
    txt = "\n".join(lines).strip("\n")
    if len(lines) > 1:
        print(f"  [已合并粘贴的 {len(lines)} 行]")
    return txt or default

def _list_recent_media(limit=30):
    items = []
    dirs = load_config()["enhance"].get("media_dirs") or []
    for root in [Path(d) for d in dirs]:
        if root.is_dir():
            for p in root.rglob("*"):
                try:
                    if p.is_file() and p.suffix.lower() in (IMG_EXT | VID_EXT | AUD_EXT):
                        items.append(p)
                except OSError:
                    pass
    items.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return items[:limit]

def _pick_media():
    print("  素材文件：直接输入/粘贴路径；或输入 L 列出最近文件按序号选。")
    s = _ask("  路径 / L> ")
    if s.upper() == "L" or s.isdigit():
        items = _list_recent_media()
        if not items:
            print("  没找到最近文件。"); return None
        for i, p in enumerate(items, 1):
            print(f"    [{i}] {p}")
        idx = s if s.isdigit() else _ask("  选择序号> ")
        try:
            return str(items[int(idx) - 1])
        except (ValueError, IndexError):
            print("  无效序号。"); return None
    return _normalize_user_path(s)

def _hr():
    print("-" * 46)

def _menu(title, options):
    _hr(); print(title)
    for key, label in options:
        print(f"  {key}) {label}")
    _hr()

def _ask_float(prompt, default):
    s = _ask(prompt, str(default))
    try:
        return float(s)
    except (TypeError, ValueError):
        print(f"  （无效数字，用默认 {default}）")
        return float(default)

def _ask_int(prompt, default):
    s = _ask(prompt, str(default))
    try:
        return int(float(s))
    except (TypeError, ValueError):
        print(f"  （无效整数，用默认 {default}）")
        return int(default)

def _mats_line(obj):
    parts = []
    for kind, key in (("图", "images"), ("视频", "videos"), ("音频", "audios")):
        for m in obj.get(key, []) or []:
            parts.append(f"[{kind}]{m.get('name')}（{m.get('decided_by', '?')}）")
    if obj.get("start_image"):
        parts.append(f"[首帧]{obj['start_image'].get('name')}")
    if obj.get("end_image"):
        parts.append(f"[尾帧]{obj['end_image'].get('name')}")
    return "  ".join(parts) or "（无）"

def _pick_group(R, name):
    groups = _load(R, name)["groups"]
    if not groups:
        print("  还没有分段，请先新建分段。")
        return None
    for i, g in enumerate(groups, 1):
        print(f"    [{i}] {g['id']}  {g.get('name') or ''}")
    s = _ask("  选分段序号（回车取消）> ", "")
    if not s:
        return None
    try:
        return groups[int(s) - 1]["id"]
    except (ValueError, IndexError):
        print("  无效序号。"); return None

def _add_group_material(R, name, gid):
    role = _ask("  角色 ref/start/end（默认 ref）> ", "ref")
    f = _pick_media()
    if not f:
        return
    try:
        _jprint(add_material(R, name, f, scope="group", group=gid,
                             mname=_ask("  素材名> ", Path(f).stem), role=role, decided_by="user"))
    except SystemExit as e:
        print(" ", e)

def _edit_loras(R, name, gid):
    g = next((g for g in _load(R, name)["groups"] if g["id"] == gid), None)
    if g is None:
        return
    print(f"  当前 LoRA：{[r.get('name') for r in g.get('loras', [])] or '（无）'}")
    s = _ask("  追加 name=strength（逗号分隔；输入 clear 清空；回车取消）> ", "")
    if not s:
        return
    if s.strip().lower() == "clear":
        set_group(R, name, gid, lora_clear=True); print("  已清空。"); return
    set_group(R, name, gid, lora=[x.strip() for x in s.split(",") if x.strip()]); print("  已追加。")

def _opt_enhance(R, name, scope, gid=None):
    print("  提示词优化（后端见 config.json：vlm / director / auto）")
    instr = _ask("  要怎么改（如：加一个特写，压到6秒；回车=泛化润色）> ", "")
    r = enhance_prompt(R, name, scope=scope, group=gid, instruction=instr)
    if not r.get("ok"):
        print(f"  优化失败（{r.get('backend')}）: {r.get('error')}")
        return
    print(f"\n  —— 候选（{r.get('backend')}）——\n{r.get('text')}\n")
    if _ask("  采用并写回？ y/N> ", "n").lower() in ("y", "yes"):
        enhance_prompt(R, name, scope=scope, group=gid, instruction=instr, apply=True)
        print("  已写回。")

def _opt_reverse(R, name, scope, gid=None):
    print("  从素材反推提示词（本地 VLM / reverse_prompt）")
    f = _pick_media()
    if not f:
        return
    tgt = _ask("  目标 h3/krea2（默认 h3）> ", "h3")
    r = reverse_from_media(R, name, file=f, scope=scope, group=gid, target=tgt, combine="append", apply=False)
    if not r.get("ok"):
        print(f"  反推失败: {r.get('error')}")
        return
    print(f"\n  —— 反推结果 ——\n{r.get('text')}\n")
    if _ask("  追加到提示词末尾？ y/N> ", "n").lower() in ("y", "yes"):
        reverse_from_media(R, name, file=f, scope=scope, group=gid, target=tgt, combine="append", apply=True)
        print("  已追加。")

def _replace_chars_flow(R, name):
    """主菜单-分段-角色替换：VLM 检测【分段角色=被替换】与【公共角色=目标】，逐个交互替换。"""
    cfg = load_config()
    proj = _load(R, name)
    common = ((proj.get("shared") or {}).get("prompt") or "").strip()
    groups = [g for g in proj.get("groups", []) if (g.get("prompt") or "").strip()]
    if not common and not groups:
        print("  工程里还没有提示词，先在「公共参数 / 分段」里写好提示词。")
        return
    print("  正在用本地 VLM 检测角色（首次会拉起模型，可能较慢，请稍候）...")
    pub = []
    if common:
        r = detect_characters(cfg, common)
        if not r.get("ok"):
            print(f"  检测公共提示词失败：{r.get('error')}")
            return
        pub = r["characters"]
    seg_chars, char_gids = [], {}
    for g in groups:
        r = detect_characters(cfg, g.get("prompt"))
        if not r.get("ok"):
            print(f"  检测分段「{g.get('name') or g['id']}」失败：{r.get('error')}")
            return
        for c in r["characters"]:
            char_gids.setdefault(c, [])
            if g["id"] not in char_gids[c]:
                char_gids[c].append(g["id"])
            if c not in seg_chars:
                seg_chars.append(c)
    _hr()
    print("检测结果：")
    print("  公共角色（来自公共提示词）  ：%s" % ("、".join(pub) if pub else "（未检测到）"))
    print("  分段角色（来自各分段提示词）：%s" % ("、".join(seg_chars) if seg_chars else "（未检测到）"))
    _hr()
    if not seg_chars:
        print("  没有检测到分段角色，结束。")
        return
    if not pub:
        print("  公共提示词里没有检测到角色，无法选择替换目标；请先把目标角色写进「公共参数」。")
        return
    remaining = list(seg_chars)
    done = []
    while remaining:
        _hr()
        print("要替换哪个分段角色？")
        for i, sc in enumerate(remaining, 1):
            print("    %d) 「%s」  出现于：%s" % (i, sc, "、".join(char_gids.get(sc, []))))
        print("    0) 结束")
        c = _ask("  选择> ", "")
        if not c.isdigit():
            print("  无效输入，结束。"); break
        i = int(c)
        if i == 0:
            break
        if i < 1 or i > len(remaining):
            print("  无效选项。"); continue
        sc = remaining.pop(i - 1)
        _hr()
        print("把分段角色「%s」替换成哪个公共角色？" % sc)
        for j, pc in enumerate(pub, 1):
            print("    %d) 「%s」" % (j, pc))
        print("    0) 不替换（跳过）")
        t = _ask("  选择> ", "0")
        if not t.isdigit() or int(t) == 0:
            print("  已跳过。"); continue
        j = int(t)
        if j < 1 or j > len(pub):
            print("  无效选项，跳过。"); continue
        dst = pub[j - 1]
        if dst == sc:
            print("  目标与原名相同，跳过。"); continue
        r = replace_characters(R, name, sc, dst)
        total = sum(x["count"] for x in r["changes"])
        if total:
            print("  已替换 %d 处：%s → %s" % (total, sc, dst))
            done.append((sc, dst, total))
        else:
            print("  未找到可替换的文本。")
    _hr()
    if done:
        print("完成，本次替换：")
        for sc, dst, n in done:
            print("  %s → %s（%d 处）" % (sc, dst, n))
    else:
        print("未做任何替换。")

def _edit_group(R, name, gid):
    while True:
        g = next((g for g in _load(R, name)["groups"] if g["id"] == gid), None)
        if g is None:
            print("  分段已不存在。"); return
        _hr()
        print(f"分段 {g['id']} 「{g.get('name') or ''}」  时长 {g.get('duration_sec')}s/{g.get('frame_count')}帧  接上段={g.get('continuity_from_prev')}")
        print(f"  提示词：{g.get('prompt') or '（空）'}")
        print(f"  素材：{_mats_line(g)}")
        if g.get("loras"):
            print("  LoRA：%s" % ", ".join(r.get('name', '') for r in g['loras']))
        _menu("编辑分段", [("1", "改提示词"), ("i", "从文件导入提示词"), ("2", "优化提示词"), ("3", "改时长"), ("4", "改名"),
                           ("5", "切换 接上一段"), ("6", "LoRA 追加/清空"), ("7", "加素材"),
                           ("8", "删除该分段"), ("9", "从素材反推并合入"), ("0", "返回")])
        c = _ask("选择> ", "0")
        if c == "1":
            set_group(R, name, gid, prompt=_ask_prompt("  提示词（可粘贴多段；@文件 导入）> ", "")); print("  已更新。")
        elif c.lower() == "i":
            t = _import_prompt_file(g.get("prompt") or "")
            if t != (g.get("prompt") or ""):
                set_group(R, name, gid, prompt=t); print("  已更新。")
        elif c == "2":
            _opt_enhance(R, name, "group", gid)
        elif c == "3":
            set_group(R, name, gid, duration=_ask_float("  时长秒> ", g.get("duration_sec") or 5)); print("  已更新。")
        elif c == "4":
            set_group(R, name, gid, gname=_ask("  段名> ", g.get("name") or "")); print("  已更新。")
        elif c == "5":
            cur = "on" if g.get("continuity_from_prev") else "off"
            set_group(R, name, gid, from_prev=_ask("  接上一段 on/off（当前 %s）> " % cur, "off" if cur == "on" else "on"))
            print("  已更新。")
        elif c == "6":
            _edit_loras(R, name, gid)
        elif c == "7":
            _add_group_material(R, name, gid)
        elif c == "8":
            if _ask("  确认删除该分段？ y/N> ", "n").lower() in ("y", "yes"):
                del_group(R, name, gid); print("  已删除。"); return
        elif c == "9":
            _opt_reverse(R, name, "group", gid)
        elif c in ("0", ""):
            return

def _groups_menu(R, name):
    while True:
        groups = _load(R, name)["groups"]
        _hr(); print(f"分段（共 {len(groups)}；一次只建空段，随后再逐项填）")
        for i, g in enumerate(groups, 1):
            cont = "  ←接上段" if g.get("continuity_from_prev") else ""
            print(f"  [{i}] {g['id']} 「{g.get('name') or ''}」 {g.get('duration_sec')}s{cont}")
        _menu("分段", [("1", "新建空段"), ("2", "编辑某段"), ("3", "角色替换(VLM检测)"), ("0", "返回")])
        c = _ask("选择> ", "0")
        if c == "1":
            gname = _ask("  段名（可空）> ", "")
            dur = _ask_float("  时长秒> ", 5)
            r = add_group(R, name, gname, "", dur, "", None, decided_by="user")
            print(f"  已建空段 {r['group']}（{r['frame_count']}帧）。接着用「编辑某段」填提示词/素材。")
        elif c == "2":
            gid = _pick_group(R, name)
            if gid:
                _edit_group(R, name, gid)
        elif c == "3":
            _replace_chars_flow(R, name)
        elif c in ("0", ""):
            return

def _common_menu(R, name):
    while True:
        sh = _load(R, name).get("shared") or {}
        _hr(); print("公共参数")
        print(f"  公共提示词：{sh.get('prompt') or '（空）'}  启用={sh.get('common_enabled', True)}")
        print(f"  公共素材：{_mats_line(sh)}")
        _menu("公共参数", [("1", "设置/修改公共提示词"), ("i", "从文件导入提示词"), ("2", "优化公共提示词"), ("3", "加公共素材"),
                           ("4", "开关公共参数"), ("5", "从素材反推并合入公共提示词"), ("0", "返回")])
        c = _ask("选择> ", "0")
        if c == "1":
            set_common(R, name, prompt=_ask_prompt("  公共提示词（可粘贴多段；@文件 导入）> ", ""), enable="on"); print("  已设置。")
        elif c.lower() == "i":
            t = _import_prompt_file(sh.get("prompt") or "")
            if t != (sh.get("prompt") or ""):
                set_common(R, name, prompt=t, enable="on"); print("  已设置。")
        elif c == "2":
            _opt_enhance(R, name, "common")
        elif c == "3":
            f = _pick_media()
            if f:
                try:
                    _jprint(add_material(R, name, f, scope="common",
                                         mname=_ask("  素材名> ", Path(f).stem), decided_by="user"))
                except SystemExit as e:
                    print(" ", e)
        elif c == "4":
            set_common(R, name, enable="off" if bool(sh.get("common_enabled", True)) else "on"); print("  已切换。")
        elif c == "5":
            _opt_reverse(R, name, "common")
        elif c in ("0", ""):
            return

def _output_menu(R, name):
    while True:
        out = _load(R, name).get("output") or {}
        _hr(); print("输出设置（回车=保持不变）")
        print("  当前：%s" % (json.dumps(out, ensure_ascii=False) if out else "（默认）"))
        _menu("输出设置", [("1", "宽/高"), ("2", "连续性 on/off"), ("3", "上下文帧 5/22/39/56"),
                           ("4", "连续性模式 guide/continue"), ("5", "重绘幅度"), ("6", "音频模式"),
                           ("7", "参考图适配"), ("8", "导出范围"), ("9", "帧上限"), ("0", "返回")])
        c = _ask("选择> ", "0")
        if c == "1":
            w = _ask_int("  宽> ", int(out.get("width") or 864)); h = _ask_int("  高> ", int(out.get("height") or 480))
            set_output(R, name, {"width": w, "height": h, "longEdge": w})
        elif c == "2":
            on = _ask("  连续性 on/off> ", "on" if out.get("continuityEnabled") else "off")
            set_output(R, name, {"continuityEnabled": str(on).lower() in ("on", "1", "true", "yes")})
        elif c == "3":
            set_output(R, name, {"continuityOverlapFrames": _ask_int("  上下文帧> ", int(out.get("continuityOverlapFrames") or 22))})
        elif c == "4":
            set_output(R, name, {"continuityMode": _ask("  guide/continue> ", out.get("continuityMode") or "guide")})
        elif c == "5":
            set_output(R, name, {"continuityRedraw": _ask_float("  重绘幅度> ", float(out.get("continuityRedraw") or 0.1))})
        elif c == "6":
            set_output(R, name, {"audioMode": _ask("  generate/source/mute> ", out.get("audioMode") or "generate")})
        elif c == "7":
            set_output(R, name, {"refImageSize": _ask("  match/max> ", out.get("refImageSize") or "match")})
        elif c == "8":
            set_output(R, name, {"exportMode": _ask("  all/select> ", out.get("exportMode") or "all")})
        elif c == "9":
            set_output(R, name, {"maxExportFrames": _ask_int("  帧上限(0=全部)> ", int(out.get("maxExportFrames") or 0))})
        elif c in ("0", ""):
            return
        else:
            print("  无效选项。")

def _wizard_main(root):
    R = root
    print(f"=== {APP_NAME} · 命令行向导 ===")
    print("（分步输入：一次只问一项；回车用默认或取消）")
    print("（提示词处可直接粘贴多段文本，会自动合并；也可输入 @文件路径 从文本文件导入）")
    a = get_active()
    name = ""
    if a and (_project_dir(R, a) / "project.json").is_file():
        ans = _ask(f"继续活动工程「{a}」？回车=是 / n=选其它 / 或直接输入工程名> ", "Y")
        low = ans.strip().lower()
        if low in ("y", "yes", ""):
            name = a
        elif low not in ("n", "list", "l"):
            name = ans.strip()
    if not name:
        print("现有工程：")
        items = list_projects(R)["projects"]
        if not items:
            print("  （无）")
        for p in items:
            print(f"  - {p['name']}  [{p['task_type']}]  {p['groups']}段")
        name = _ask("工程名（不存在则新建）> ", "")
    if not name:
        raise SystemExit("[wizard] 未提供工程名。")
    if not (_project_dir(R, name) / "project.json").is_file():
        task = _ask("任务模式 r2v/fl2v/v2v (默认 r2v)> ", "r2v")
        new_project(R, name, task)
        print(f"[已建工程] {name}（{task}）")
    set_active(name)
    print(f"[活动工程] {name}\n")
    while True:
        proj = _load(R, name)
        _hr()
        print(f"工程：{name}  [{proj.get('task_type')}]  分段 {len(proj.get('groups', []))}")
        _menu("主菜单", [("1", "公共参数"), ("2", "分段"), ("3", "输出设置"), ("4", "查看内容"),
                         ("5", "校验"), ("6", "导出"), ("7", "可配置项"), ("8", "切换工程"), ("0", "退出")])
        c = _ask("选择> ", "0")
        try:
            if c == "1":
                _common_menu(R, name)
            elif c == "2":
                _groups_menu(R, name)
            elif c == "3":
                _output_menu(R, name)
            elif c == "4":
                print(report_text(R, name))
            elif c == "5":
                _jprint(validate_pack(R, name))
            elif c == "6":
                _jprint(export_pack(R, name))
                print("\n" + report_text(R, name))
                print("\n→ 到 ComfyUI 的 Director 节点点 **Import pack** 选这个 zip 导入。")
            elif c == "7":
                _jprint(capabilities())
            elif c == "8":
                nm = _ask("  新工程名（不存在则新建）> ", "")
                if nm:
                    if not (_project_dir(R, nm) / "project.json").is_file():
                        task = _ask("  任务模式 r2v/fl2v/v2v (默认 r2v)> ", "r2v")
                        new_project(R, nm, task)
                    set_active(nm); name = nm; print(f"  已切换到 {nm}")
            elif c in ("0", "q", "quit", "exit", ""):
                print("再见。"); break
            else:
                print("  无效选项。")
        except SystemExit as e:
            print(" ", e)
        except Exception as e:
            print(f"  出错: {type(e).__name__}: {e}")
    return {"ok": True, "wizard": "done", "project": name}

def run_wizard(root):
    """交互向导会话：会话内按需自动拉起 VLM；退出时若为本工具启动则关闭以释放显存。"""
    cfg = load_config()
    try:
        return _wizard_main(root)
    finally:
        if cfg["enhance"]["vlm"].get("auto_stop"):
            r = stop_vlm(cfg)
            if r.get("stopped"):
                print("[已关闭本工具启动的本地 VLM，释放显存]")

def build_parser():
    ap = argparse.ArgumentParser(description=f"{APP_NAME}（MiniMax H3 导演包生成器 · CLI/HTTP）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="工程根目录")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new"); p.add_argument("name"); p.add_argument("--task", default=DEFAULT_TASK)
    sub.add_parser("list")
    sub.add_parser("active")
    sub.add_parser("version")
    sub.add_parser("wizard")
    p = sub.add_parser("capabilities"); p.add_argument("--section", default=None); p.add_argument("--full", action="store_true")
    p = sub.add_parser("report"); p.add_argument("--project", default=None)
    p = sub.add_parser("use"); p.add_argument("name")
    p = sub.add_parser("show"); p.add_argument("--project", default=None); p.add_argument("--group", default=None)
    p = sub.add_parser("set-common"); p.add_argument("--project", default=None); p.add_argument("--prompt"); p.add_argument("--enable")
    p = sub.add_parser("add-group"); p.add_argument("--project", default=None); p.add_argument("--gname", default="")
    p.add_argument("--prompt", default=""); p.add_argument("--duration", type=float, default=5.0)
    p.add_argument("--task", default=""); p.add_argument("--lora", action="append", help="name=strength，可重复")
    p.add_argument("--decided-by", dest="decided_by", choices=["user", "agent"], default="agent")
    p = sub.add_parser("set-group"); p.add_argument("--project", default=None); p.add_argument("--group", required=True)
    p.add_argument("--prompt"); p.add_argument("--negative"); p.add_argument("--duration", type=float)
    p.add_argument("--gname"); p.add_argument("--from-prev")
    p.add_argument("--lora", action="append", help="追加 name=strength，可重复")
    p.add_argument("--lora-clear", action="store_true", help="清空该段 LoRA")
    p = sub.add_parser("del-group"); p.add_argument("--project", default=None); p.add_argument("--group", required=True)
    p = sub.add_parser("add-material"); p.add_argument("--project", default=None); p.add_argument("--file", required=True)
    p.add_argument("--scope", default="common", choices=["common", "group"]); p.add_argument("--group", default=None)
    p.add_argument("--kind", default=None); p.add_argument("--mname", default="")
    p.add_argument("--role", default="ref", choices=["ref", "start", "end"])
    p.add_argument("--decided-by", dest="decided_by", choices=["user", "agent"], default="agent")
    p = sub.add_parser("set-source"); p.add_argument("--project", default=None); p.add_argument("--file", required=True)
    p = sub.add_parser("export"); p.add_argument("--project", default=None); p.add_argument("--out", default=None)
    p = sub.add_parser("import-pack"); p.add_argument("zip"); p.add_argument("--name", default=None)
    p = sub.add_parser("validate"); p.add_argument("--project", default=None)
    p = sub.add_parser("config"); p.add_argument("--save", action="store_true")
    p = sub.add_parser("enhance"); p.add_argument("--project", default=None)
    p.add_argument("--group", default=None); p.add_argument("--common", action="store_true")
    p.add_argument("--draft", default=None); p.add_argument("--instruction", default="")
    p.add_argument("--target", choices=["h3", "krea2"], default=None)
    p.add_argument("--backend", choices=["auto", "vlm", "api", "director"], default=None)
    p.add_argument("--apply", action="store_true"); p.add_argument("--config", default=None)
    p = sub.add_parser("reverse"); p.add_argument("--project", default=None)
    p.add_argument("--file", required=True); p.add_argument("--scope", default="common", choices=["common", "group"])
    p.add_argument("--group", default=None); p.add_argument("--target", choices=["h3", "krea2"], default=None)
    p.add_argument("--combine", choices=["append", "replace"], default="append")
    p.add_argument("--backend", choices=["auto", "vlm", "api", "director"], default=None)
    p.add_argument("--apply", action="store_true"); p.add_argument("--config", default=None)
    p = sub.add_parser("vlm"); p.add_argument("action", nargs="?", choices=["status", "start", "stop"], default="status")
    p.add_argument("--config", default=None)
    p = sub.add_parser("set-output"); p.add_argument("--project", default=None)
    p.add_argument("--mode"); p.add_argument("--width", type=int); p.add_argument("--height", type=int)
    p.add_argument("--long-edge", type=int); p.add_argument("--max-export-frames", type=int)
    p.add_argument("--export-mode", choices=["all", "select"]); p.add_argument("--audio-mode", choices=["generate", "source", "mute"])
    p.add_argument("--ref-image-size", choices=["match", "max"])
    p.add_argument("--continuity", choices=["on", "off"]); p.add_argument("--continuity-overlap", type=int)
    p.add_argument("--continuity-mode", choices=["guide", "continue"]); p.add_argument("--continuity-redraw", type=float)
    p = sub.add_parser("detect-chars"); p.add_argument("--project", default=None)
    p = sub.add_parser("replace-char"); p.add_argument("--project", default=None)
    p.add_argument("--src", required=True); p.add_argument("--dst", required=True)
    p = sub.add_parser("media"); p.add_argument("--limit", type=int, default=60)
    p = sub.add_parser("set-config"); p.add_argument("--patch", default=None)
    p = sub.add_parser("test-connection"); p.add_argument("--kind", default="vlm"); p.add_argument("--patch", default=None)
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=8765); p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    return ap

def run_cli(a):
    R = a.root; c = a.cmd
    if c == "new": return new_project(R, a.name, a.task)
    if c == "list": return list_projects(R)
    if c == "active": return active_project(R)
    if c == "version": return {"ok": True, "version": __version__, "packFormat": PACK_FORMAT, "packVersion": PACK_VERSION}
    if c == "capabilities": return capabilities(a.section, a.full)
    if c == "config":
        cfg = load_config()
        if a.save: save_config(cfg)
        return {"ok": True, "path": str(DEFAULT_CONFIG_PATH), "saved": bool(a.save), "config": cfg}
    if c == "report": return {"ok": True, "report": report_text(R, resolve_name(R, a.project))}
    if c == "use":
        if not (_project_dir(R, a.name) / "project.json").is_file():
            raise SystemExit(f"[错误] 工程不存在: {a.name}")
        return set_active(a.name)
    if c == "show": return show_project(R, resolve_name(R, a.project), a.group)
    if c == "set-common": return set_common(R, resolve_name(R, a.project), a.prompt, a.enable)
    if c == "wizard": return run_wizard(R)
    if c == "add-group": return add_group(R, resolve_name(R, a.project), a.gname, a.prompt, a.duration, a.task, a.lora, a.decided_by)
    if c == "set-group": return set_group(R, resolve_name(R, a.project), a.group, a.prompt, a.duration, a.gname,
                                          a.from_prev, a.negative, a.lora, a.lora_clear)
    if c == "del-group": return del_group(R, resolve_name(R, a.project), a.group)
    if c == "add-material": return add_material(R, resolve_name(R, a.project), a.file, a.scope, a.group, a.kind, a.mname, a.role, a.decided_by)
    if c == "set-source": return set_source(R, resolve_name(R, a.project), a.file)
    if c == "set-output":
        def _b(x): return None if x is None else (str(x).lower() in ("1", "true", "on", "yes"))
        fields = {"mode": a.mode, "width": a.width, "height": a.height, "longEdge": a.long_edge,
                  "maxExportFrames": a.max_export_frames, "exportMode": a.export_mode,
                  "audioMode": a.audio_mode, "refImageSize": a.ref_image_size,
                  "continuityEnabled": _b(a.continuity), "continuityOverlapFrames": a.continuity_overlap,
                  "continuityMode": a.continuity_mode, "continuityRedraw": a.continuity_redraw}
        return set_output(R, resolve_name(R, a.project), {k: v for k, v in fields.items() if v is not None})
    if c == "export": return export_pack(R, resolve_name(R, a.project), a.out)
    if c == "import-pack": return import_pack(R, a.zip, a.name)
    if c == "validate": return validate_pack(R, resolve_name(R, a.project))
    if c == "enhance":
        nm = resolve_name(R, a.project) if (a.project or get_active()) else None
        return enhance_prompt(R, nm, scope=("common" if a.common else "group"), group=a.group,
                              draft=a.draft, instruction=a.instruction or "", target=a.target,
                              backend=a.backend, apply=a.apply, config_path=a.config)
    if c == "reverse":
        nm = resolve_name(R, a.project) if (a.project or get_active()) else None
        return reverse_from_media(R, nm, file=a.file, scope=a.scope, group=a.group, target=a.target,
                                  combine=a.combine, backend=a.backend, apply=a.apply, config_path=a.config)
    if c == "vlm":
        cfg = load_config(getattr(a, "config", None))
        server = cfg["enhance"]["vlm"].get("server")
        act = getattr(a, "action", "status") or "status"
        if act == "status":
            return {"ok": True, "server": server, "health": _vlm_health(server),
                    "managed": (_VLM_PROC is not None) or VLM_PID_FILE.is_file()}
        if act == "start":
            return ensure_vlm(cfg, log=lambda s: print(s, file=sys.stderr))
        return stop_vlm(cfg)
    if c == "detect-chars": return detect_chars(R, a.project)
    if c == "replace-char": return replace_char(R, a.project, a.src, a.dst)
    if c == "media": return list_media(R, getattr(a, "limit", 60))
    if c == "set-config": return update_config(getattr(a, "patch", None))
    if c == "test-connection": return test_connection(getattr(a, "kind", "vlm") or "vlm", getattr(a, "patch", None))
    raise SystemExit("unknown cmd")

def cmd_from_args(cmd, kw):
    ns = argparse.Namespace(cmd=cmd, root=kw.get("root", str(DEFAULT_ROOT)))
    for k in ("name", "project", "group", "file", "zip", "scope", "kind", "mname", "role",
              "prompt", "negative", "duration", "gname", "from_prev", "enable", "task", "out",
              "lora", "lora_clear", "mode", "width", "height", "long_edge", "max_export_frames",
              "export_mode", "audio_mode", "ref_image_size", "continuity", "continuity_overlap",
              "continuity_mode", "continuity_redraw", "decided_by", "section", "full",
              "common", "draft", "instruction", "target", "backend", "apply", "config", "save", "combine", "action",
              "src", "dst", "limit", "patch"):
        setattr(ns, k, kw.get(k))
    return run_cli(ns)

# ------------------------------------------------------------------ HTTP + 网页界面
UPLOAD_DIR = APP_DIR / "_uploads"

def _save_upload(filename, data):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(Path(filename or "upload").stem) or "upload"
    ext = Path(filename or "").suffix or ".bin"
    dst = UPLOAD_DIR / (stem + ext)
    n = 1
    while dst.exists():
        dst = UPLOAD_DIR / f"{stem}_{n}{ext}"; n += 1
    dst.write_bytes(data)
    return dst

WEB_UI_HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>导演包生成小助手</title>
<style>
:root{--bg:#0f1216;--panel:#171c22;--panel2:#1e252d;--fg:#e6edf3;--mut:#8b98a5;--acc:#4a9eff;--ok:#3fb950;--err:#f85149;--bd:#2b333c}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}
header{display:flex;gap:10px;align-items:center;padding:10px 14px;background:var(--panel);border-bottom:1px solid var(--bd);position:sticky;top:0;z-index:5;flex-wrap:wrap}
h1{font-size:15px;margin:0 8px 0 0;font-weight:600}
select,input,textarea,button{font:inherit;color:var(--fg);background:var(--panel2);border:1px solid var(--bd);border-radius:6px;padding:6px 8px}
textarea{width:100%;min-height:96px;resize:vertical}
button{cursor:pointer}
button:hover{border-color:var(--acc)}
.btn{background:var(--acc);border-color:var(--acc);color:#06121f;font-weight:600}
.btn.ghost{background:transparent;color:var(--fg);border-color:var(--bd);font-weight:400}
.btn.sm{padding:3px 9px;font-size:12px}
.tabs{display:flex;gap:6px;padding:10px 14px 0;flex-wrap:wrap}
.tabs button{border-radius:6px 6px 0 0}
.tabs button.active{background:var(--acc);color:#06121f;font-weight:600;border-color:var(--acc)}
main{padding:14px}
section{display:none}
section.on{display:block}
.card{background:var(--panel);border:1px solid var(--bd);border-radius:8px;padding:10px;margin:10px 0}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0}
.muted{color:var(--mut);font-size:12px}
.chk{display:flex;gap:5px;align-items:center}
.chip{display:inline-block;background:var(--panel2);border:1px solid var(--bd);border-radius:999px;padding:2px 10px;margin:2px}
.drop{outline:2px dashed var(--acc)!important;outline-offset:2px;background:#1b2530!important}
.warn{color:#d29922}.err{color:var(--err)}.ok{color:var(--ok)}
pre{white-space:pre-wrap;word-break:break-word;background:var(--panel2);border:1px solid var(--bd);border-radius:6px;padding:10px;max-height:50vh;overflow:auto}
#toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);background:#000c;border:1px solid var(--bd);padding:8px 16px;border-radius:8px;opacity:0;transition:.2s;pointer-events:none;z-index:50}
#toast.show{opacity:1}
#toast.err{border-color:var(--err);color:#ffb4ae}
.overlay{position:fixed;inset:0;background:#000a;display:none;align-items:center;justify-content:center;z-index:40}
.overlay.on{display:flex}
.modal{background:var(--panel);border:1px solid var(--bd);border-radius:10px;padding:16px;max-width:780px;width:92%;max-height:85vh;overflow:auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px}
label.f{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--mut)}
</style></head><body>
<header>
  <h1>导演包生成小助手</h1>
  <select id="projectSel"></select>
  <button class="btn ghost" id="refreshBtn">刷新</button>
  <button class="btn ghost" id="newBtn">新建工程</button>
  <span id="vlmBadge" class="muted">VLM: ?</span>
  <button class="btn ghost sm" id="vlmStart">启动本地VLM</button>
  <button class="btn ghost sm" id="shutdownBtn">停止服务</button>
</header>
<div class="tabs">
  <button data-tab="common" class="active">公共参数</button>
  <button data-tab="groups">分段</button>
  <button data-tab="chars">角色替换</button>
  <button data-tab="output">输出</button>
  <button data-tab="io">校验/导出</button>
  <button data-tab="settings">设置</button>
</div>
<main>
  <section id="tab-common" class="on">
    <div class="card">
      <div class="row"><b>公共提示词</b><span class="muted">（会拼进每个分段）</span></div>
      <textarea id="commonPrompt" placeholder="公共提示词，可多行（可直接把 .txt/.md 文件拖到这里导入）"></textarea>
      <div class="row">
        <button class="btn ghost sm" id="commonImport">导入文件</button>
        <label class="chk"><input type="checkbox" id="commonEnabled"> 启用公共参数</label>
        <button class="btn sm" id="commonSave">保存</button>
        <button class="btn ghost sm" id="commonEnhance">优化</button>
      </div>
    </div>
    <div class="card">
      <div class="row"><b>公共素材</b><span class="muted" id="commonMats">（无）</span></div>
      <div class="row">
        <label class="f">角色<select id="commonRole"><option value="ref">ref</option><option value="start">start</option><option value="end">end</option></select></label>
        <label class="f">素材名<input id="commonMatName" placeholder="可空"></label>
        <button class="btn sm" id="commonMatAdd">添加素材（上传文件）</button>
      </div>
    </div>
  </section>
  <section id="tab-groups">
    <div class="card">
      <div class="row"><b>新建分段</b>
        <input id="newGname" placeholder="段名（可空）">
        <label>时长 <input id="newGdur" type="number" step="0.5" min="0.5" value="5" style="width:80px"></label>
        <button class="btn sm" id="addGroupBtn">新建空段</button>
        <button class="btn sm" id="saveAllBtn">保存全部分段</button>
        <span class="muted">（改完直接一次保存所有卡片）</span>
      </div>
    </div>
    <div id="groups"></div>
  </section>
  <section id="tab-chars">
    <div class="card">
      <div class="row"><b>角色替换</b>
        <span class="muted">本地 VLM 从公共/分段提示词检测角色，再把分段角色替换成公共角色</span>
        <button class="btn sm" id="detectBtn">检测角色</button></div>
      <div id="chars"></div>
    </div>
  </section>
  <section id="tab-output">
    <div class="card">
      <div class="row"><b>输出设置</b><button class="btn sm" id="outputSave">保存</button></div>
      <div class="grid" id="outputGrid"></div>
    </div>
  </section>
  <section id="tab-io">
    <div class="card">
      <div class="row">
        <button class="btn sm" id="validateBtn">校验</button>
        <button class="btn sm" id="exportBtn">导出 .mmxpack.zip</button>
        <button class="btn ghost sm" id="importBtn">导入包（上传zip）</button>
        <button class="btn ghost sm" id="reportBtn">查看内容</button>
      </div>
      <pre id="report">（点上方按钮显示结果）</pre>
    </div>
  </section>
  <section id="tab-settings">
    <div class="card">
      <div class="row"><b>后端选择</b><span class="muted">决定“优化 / 反推 / 角色检测”走哪里</span></div>
      <div class="row">
        <label class="f">使用后端
          <select id="s_backend">
            <option value="auto">自动（本地优先，其次在线API）</option>
            <option value="vlm">本地 LLM/VLM（llama-server / vLLM）</option>
            <option value="api">在线 API（OpenAI 兼容）</option>
            <option value="director">导演台</option>
          </select>
        </label>
        <button class="btn sm" id="settingsSave">保存设置</button>
        <button class="btn ghost sm" id="settingsReload">重新加载</button>
      </div>
    </div>
    <div class="card">
      <div class="row"><b>本地 LLM / VLM</b>
        <button class="btn ghost sm" id="testVlm">测试本地VLM</button>
        <button class="btn ghost sm" id="startVlm2">启动本地VLM</button>
        <button class="btn ghost sm" id="stopVlm2">停止本地VLM</button>
      </div>
      <div class="grid">
        <label class="f">服务地址 server<input id="s_vlm_server" placeholder="http://127.0.0.1:8080"></label>
        <label class="f">模型名 model（可空）<input id="s_vlm_model"></label>
        <label class="f">API Key（可空）<input id="s_vlm_key"></label>
        <label class="f">Python 解释器 python（可空=自动）<input id="s_vlm_python"></label>
        <label class="f">reverse_prompt.py 路径 script<input id="s_vlm_script"></label>
        <label class="f">启动程序 exe<input id="s_vlm_exe"></label>
        <label class="f">工作目录 cwd<input id="s_vlm_cwd"></label>
        <label class="f">启动超时(秒)<input id="s_vlm_timeout" type="number" value="480"></label>
      </div>
      <div class="row"><label class="chk"><input type="checkbox" id="s_vlm_autostart"> 需要时自动启动</label>
        <label class="chk"><input type="checkbox" id="s_vlm_autostop"> 退出向导时自动关闭</label></div>
      <label class="f">启动参数 args（每行一个）<textarea id="s_vlm_args" placeholder="每行一个参数"></textarea></label>
    </div>
    <div class="card">
      <div class="row"><b>在线 API</b><button class="btn ghost sm" id="testApi">测试在线API</button></div>
      <div class="grid">
        <label class="f">Base URL<input id="s_api_base" placeholder="https://api.deepseek.com/v1"></label>
        <label class="f">API Key<input id="s_api_key"></label>
        <label class="f">模型名 model<input id="s_api_model" placeholder="deepseek-chat"></label>
      </div>
      <div class="row"><label class="chk"><input type="checkbox" id="s_api_notmpl"> 不发送 chat_template_kwargs（部分云端需要）</label></div>
    </div>
    <div class="card">
      <div class="row"><b>素材目录 media_dirs</b><span class="muted">用于“最近文件”列表</span></div>
      <label class="f">每行一个目录<textarea id="s_media_dirs" placeholder="H:\comfyui\output"></textarea></label>
    </div>
    <div class="card">
      <div class="row"><b>导演台（可选）</b></div>
      <div class="grid">
        <label class="f">URL<input id="s_dir_url"></label>
        <label class="f">LLM URL<input id="s_dir_llm"></label>
        <label class="f">模型<input id="s_dir_model"></label>
        <label class="f">格式<input id="s_dir_format"></label>
        <label class="f">API Key<input id="s_dir_key"></label>
        <label class="f">输出语言<input id="s_dir_lang"></label>
      </div>
    </div>
    <pre id="settingsMsg"></pre>
  </section>
</main>
<div id="toast"></div>
<div class="overlay" id="overlay"><div class="modal"><div id="modalBody"></div><div class="row" id="modalBtns"></div></div></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>(s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const api=async(cmd,args)=>{const r=await fetch('/'+cmd,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({args:args||{}})});return await r.json();};
let S={project:null,show:null,chars:null};
let toastT;function toast(m,err){const t=$('#toast');t.textContent=m;t.className='show'+(err?' err':'');clearTimeout(toastT);toastT=setTimeout(()=>t.className='',2600);}
function modal(title,bodyHtml,buttons){return new Promise(res=>{const ov=$('#overlay');$('#modalBody').innerHTML='<h3 style="margin:0 0 10px">'+esc(title)+'</h3>'+bodyHtml;const bb=$('#modalBtns');bb.innerHTML='';buttons.forEach(b=>{const el=document.createElement('button');el.textContent=b;el.className=(b==='取消'?'btn ghost':'btn');el.onclick=()=>{ov.className='overlay';res(b);};bb.appendChild(el);});ov.className='overlay on';});}
function uploadFile(file){return fetch('/upload',{method:'POST',headers:{'X-Filename':encodeURIComponent(file.name)},body:file}).then(r=>r.json());}
function pickFile(accept,cb){const i=document.createElement('input');i.type='file';if(accept)i.accept=accept;i.onchange=()=>{const f=i.files[0];if(f)cb(f);};i.click();}
function makeDrop(el,onFile){if(!el)return;el.addEventListener('dragover',e=>{e.preventDefault();el.classList.add('drop');});el.addEventListener('dragleave',()=>el.classList.remove('drop'));el.addEventListener('drop',e=>{e.preventDefault();el.classList.remove('drop');const f=e.dataTransfer&&e.dataTransfer.files&&e.dataTransfer.files[0];if(f)onFile(f);});}
async function loadTextTo(ta,f){ta.value=await f.text();toast('已导入文件内容到文本框（记得保存）');}
async function addMaterialFile(scope,gid,f,role,mname){toast('上传中…');const up=await uploadFile(f);if(!up.ok){toast(up.error,true);return;}const a={project:S.project,file:up.path,scope:scope,mname:(mname||f.name),role:role};if(scope==='group')a.group=gid;const r=await api('add-material',a);if(r.ok){toast('已加素材');refreshShow();}else toast(r.error,true);}
function badge(st){const el=$('#vlmBadge');if(st&&st.health){el.textContent='VLM: 在线';el.className='ok';}else{el.textContent='VLM: 离线';el.className='warn';}}
async function reloadProjects(){const r=await api('list');const sel=$('#projectSel');const cur=S.project||sel.value;sel.innerHTML='<option value="">（选择工程）</option>'+(r.projects||[]).map(p=>'<option value="'+esc(p.name)+'">'+esc(p.name)+' ['+esc(p.task_type)+'] '+p.groups+'段</option>').join('');if(cur)sel.value=cur;}
async function openProject(name){if(!name){S.project=null;S.show=null;$('#groups').innerHTML='';return;}S.project=name;await refreshShow();}
async function refreshShow(){if(!S.project)return;const r=await api('show',{project:S.project});if(r.ok){S.show=r;renderCommon();renderGroups();renderOutput();}else toast(r.error,true);}
function renderCommon(){const sh=(S.show&&S.show.shared)||{};$('#commonPrompt').value=sh.prompt||'';$('#commonEnabled').checked=sh.common_enabled!==false;const a=[];(sh.images||[]).forEach(m=>a.push('图 '+m.name));(sh.videos||[]).forEach(m=>a.push('视频 '+m.name));(sh.audios||[]).forEach(m=>a.push('音频 '+m.name));$('#commonMats').textContent=a.join('，')||'（无）';}
async function saveCommon(){const r=await api('set-common',{project:S.project,prompt:$('#commonPrompt').value,enable:$('#commonEnabled').checked?'on':'off'});if(r.ok){toast('公共参数已保存');refreshShow();}else toast(r.error,true);}
function matsLine(g){const a=[];(g.images||[]).forEach(m=>a.push('图 '+m.name));(g.videos||[]).forEach(m=>a.push('视频 '+m.name));(g.audios||[]).forEach(m=>a.push('音频 '+m.name));if(g.start_image)a.push('首帧 '+g.start_image);if(g.end_image)a.push('尾帧 '+g.end_image);return a.join('，')||'（无）';}
function renderGroups(){const box=$('#groups');if(!S.show){box.innerHTML='';return;}box.innerHTML='';(S.show.groups||[]).forEach((g,idx)=>{const gid=g.id;const card=document.createElement('div');card.className='card';card.dataset.gid=gid;
card.innerHTML='<div class="row"><b>'+(idx+1)+'.</b><input class="gname" placeholder="段名" value="'+esc(g.name)+'"><label>时长 <input class="gdur" type="number" step="0.5" min="0.5" style="width:80px" value="'+(g.duration_sec||5)+'"></label><span class="muted">'+g.frame_count+'帧 · '+gid+'</span><label class="chk"><input class="gprev" type="checkbox" '+(g.continuity_from_prev?'checked':'')+'> 接上一段</label></div>'
+'<textarea class="gprompt" placeholder="分段提示词（可多行；可直接拖入 .txt 文件到此处）">'+esc(g.prompt)+'</textarea>'
+'<div class="row"><button class="btn ghost sm imp">导入文件</button><button class="btn sm save">保存本段</button><button class="btn ghost sm opt">优化</button><button class="btn ghost sm rev">反推</button><button class="btn ghost sm del">删除</button></div>'
+'<div class="row"><select class="mrole"><option value="ref">ref</option><option value="start">start</option><option value="end">end</option></select><input class="mname" placeholder="素材名（可空）"><button class="btn ghost sm mat">加素材(上传)</button><span class="muted">'+esc(matsLine(g))+'</span></div>'
+'<div class="row"><input class="lora" placeholder="LoRA name=strength"><button class="btn ghost sm loraAdd">追加LoRA</button><button class="btn ghost sm loraClr">清空LoRA</button><span class="muted">'+esc((g.loras||[]).join(', ')||'（无LoRA）')+'</span></div>';
card.querySelector('.imp').onclick=()=>pickFile('.txt,.md,text/plain',f=>loadTextTo(card.querySelector('.gprompt'),f));
makeDrop(card.querySelector('.gprompt'),f=>loadTextTo(card.querySelector('.gprompt'),f));
makeDrop(card.querySelector('.imp'),f=>loadTextTo(card.querySelector('.gprompt'),f));
card.querySelector('.save').onclick=()=>saveGroup(card,gid);
card.querySelector('.opt').onclick=()=>enhance('group',gid);
card.querySelector('.rev').onclick=()=>reverse('group',gid);
makeDrop(card.querySelector('.rev'),f=>reverseFromFile('group',gid,f));
card.querySelector('.del').onclick=async()=>{if(!confirm('删除该分段？'))return;const r=await api('del-group',{project:S.project,group:gid});if(r.ok){toast('已删除');refreshShow();}else toast(r.error,true);};
card.querySelector('.mat').onclick=()=>pickFile('image/*,video/*,audio/*',f=>addMaterialFile('group',gid,f,card.querySelector('.mrole').value,card.querySelector('.mname').value));
makeDrop(card.querySelector('.mat'),f=>addMaterialFile('group',gid,f,card.querySelector('.mrole').value,card.querySelector('.mname').value));
card.querySelector('.loraAdd').onclick=async()=>{const v=card.querySelector('.lora').value.trim();if(!v)return;const r=await api('set-group',{project:S.project,group:gid,lora:[v]});if(r.ok){toast('已追加LoRA');refreshShow();}else toast(r.error,true);};
card.querySelector('.loraClr').onclick=async()=>{const r=await api('set-group',{project:S.project,group:gid,lora_clear:true});if(r.ok){toast('已清空LoRA');refreshShow();}else toast(r.error,true);};
box.appendChild(card);});}
async function saveGroup(card,gid){const r=await api('set-group',groupArgs(card,gid));if(r.ok){toast('已保存');refreshShow();}else toast(r.error,true);}
function groupArgs(card,gid){const dur=parseFloat(card.querySelector('.gdur').value);return {project:S.project,group:gid,gname:card.querySelector('.gname').value,prompt:card.querySelector('.gprompt').value,duration:(isNaN(dur)?undefined:dur),from_prev:card.querySelector('.gprev').checked?'on':'off'};}
async function saveAllGroups(){const cards=[...document.querySelectorAll('#groups .card')];let ok=0,fail=0;for(const card of cards){const gid=card.dataset.gid;const r=await api('set-group',groupArgs(card,gid));if(r.ok)ok++;else{fail++;toast('保存失败 '+gid+': '+r.error,true);}}toast('已保存 '+ok+' 段'+(fail?('，失败 '+fail):''));if(ok)refreshShow();}
async function addGroup(){const gn=$('#newGname').value;const dur=parseFloat($('#newGdur').value)||5;const r=await api('add-group',{project:S.project,gname:gn,duration:dur});if(r.ok){toast('已新建分段');$('#newGname').value='';refreshShow();}else toast(r.error,true);}
const OUTDEF=[['width','宽','number',864],['height','高','number',480],['continuity','连续性',['on','off'],'on'],['overlap','上下文帧','number',22],['mode','连续性模式',['guide','continue'],'guide'],['redraw','重绘幅度','number',0.1],['audio','音频',['generate','source','mute'],'generate'],['ref','参考图适配',['match','max'],'match'],['export','导出范围',['all','select'],'all'],['max','帧上限(0=全部)','number',0]];
function renderOutput(){const g=$('#outputGrid');if(!S.show)return;const o=S.show.output||{};let html='';html+='<label class="f">宽<input id="o_width" type="number" value="'+(o.width||864)+'"></label>';
html+='<label class="f">高<input id="o_height" type="number" value="'+(o.height||480)+'"></label>';
html+='<label class="f">连续性<select id="o_continuity"><option value="on">on</option><option value="off">off</option></select></label>';
html+='<label class="f">上下文帧<input id="o_overlap" type="number" value="'+(o.continuityOverlapFrames||22)+'"></label>';
html+='<label class="f">连续性模式<select id="o_mode"><option>guide</option><option>continue</option></select></label>';
html+='<label class="f">重绘幅度<input id="o_redraw" type="number" step="0.05" value="'+(o.continuityRedraw!=null?o.continuityRedraw:0.1)+'"></label>';
html+='<label class="f">音频<select id="o_audio"><option>generate</option><option>source</option><option>mute</option></select></label>';
html+='<label class="f">参考图适配<select id="o_ref"><option>match</option><option>max</option></select></label>';
html+='<label class="f">导出范围<select id="o_export"><option>all</option><option>select</option></select></label>';
html+='<label class="f">帧上限<input id="o_max" type="number" value="'+(o.maxExportFrames||0)+'"></label>';
g.innerHTML=html;
const set=(id,v)=>{const e=$(id);if(e)e.value=v;};
set('#o_continuity',o.continuityEnabled?'on':'off');set('#o_mode',o.continuityMode||'guide');set('#o_audio',o.audioMode||'generate');set('#o_ref',o.refImageSize||'match');set('#o_export',o.exportMode||'all');}
async function saveOutput(){const r=await api('set-output',{project:S.project,width:parseInt($('#o_width').value),height:parseInt($('#o_height').value),continuity:$('#o_continuity').value,continuity_overlap:parseInt($('#o_overlap').value),continuity_mode:$('#o_mode').value,continuity_redraw:parseFloat($('#o_redraw').value),audio_mode:$('#o_audio').value,ref_image_size:$('#o_ref').value,export_mode:$('#o_export').value,max_export_frames:parseInt($('#o_max').value||'0')||0});if(r.ok){toast('输出设置已保存');refreshShow();}else toast(r.error,true);}
async function detectChars(){if(!S.project){toast('先选工程',true);return;}toast('检测中…（首次会拉起本地VLM，可能较慢）');const r=await api('detect-chars',{project:S.project});if(!r.ok){toast(r.error,true);return;}S.chars=r;renderChars();toast('检测完成');}
function renderChars(){const box=$('#chars');if(!S.chars){box.innerHTML='';return;}const pub=S.chars.public||[];const seg=S.chars.segment_characters||[];let html='<div class="row"><b>公共角色（替换目标）</b>'+(pub.length?pub.map(c=>'<span class="chip">'+esc(c)+'</span>').join(''):'<span class="muted">（未检测到）</span>')+'</div>';
html+='<div class="row"><b>分段角色（被替换）</b>'+(seg.length?'':'<span class="muted">（未检测到）</span>')+'</div>';
if(!pub.length)html+='<div class="warn">公共提示词没检测到角色，无法替换；请先写好公共提示词再检测。</div>';
if(seg.length){html+='<table style="width:100%">';seg.forEach(sc=>{html+='<tr><td style="padding:4px 8px">'+esc(sc.name)+' <span class="muted">('+esc((sc.groups||[]).join(','))+')</span></td><td>→</td><td><select class="repSel" data-src="'+esc(sc.name)+'" '+(pub.length?'':'disabled')+'><option value="">不替换</option>'+pub.map(p=>'<option value="'+esc(p)+'">'+esc(p)+'</option>').join('')+'</select></td></tr>';});html+='</table>';
html+='<div class="row"><button class="btn sm" id="applyChars">执行替换</button><span class="muted">选“不替换”的会跳过</span></div>';}
box.innerHTML=html;const b=$('#applyChars');if(b)b.onclick=applyChars;}
async function applyChars(){const sels=[...document.querySelectorAll('.repSel')];let total=0;const list=[];for(const s of sels){const dst=s.value;if(!dst)continue;const src=s.dataset.src;const r=await api('replace-char',{project:S.project,src:src,dst:dst});if(r.ok){const n=(r.changes||[]).reduce((a,c)=>a+c.count,0);total+=n;list.push(src+'→'+dst+'('+n+')');}else toast(r.error,true);}toast('已替换 '+total+' 处 '+(list.join(' ')));if(total){await refreshShow();await detectChars();}}
async function enhance(scope,gid){const instr=prompt('优化要求（可空=泛化润色）','');if(instr===null)return;toast('优化中…（本地VLM可能较慢）');const r=await api('enhance',{project:S.project,common:scope==='common',group:gid,instruction:instr,apply:false});if(!r.ok){toast(r.error,true);return;}const ch=await modal('优化候选 ['+(r.backend||'')+']','<pre>'+esc(r.text)+'</pre>',['采用','取消']);if(ch==='采用'){const r2=await api('enhance',{project:S.project,common:scope==='common',group:gid,instruction:instr,apply:true});if(r2.ok){toast('已写回');refreshShow();}else toast(r2.error,true);}}
async function reverseFromFile(scope,gid,f){toast('上传中…');const up=await uploadFile(f);if(!up.ok){toast(up.error,true);return;}const tgt=prompt('目标 krea2 / h3 ?','h3');if(tgt===null)return;toast('反推中…（本地VLM可能较慢）');const base={project:S.project,file:up.path,scope:(scope==='common'?'common':'group'),group:gid,target:(tgt.trim()||'h3')};const r=await api('reverse',Object.assign({},base,{combine:'append',apply:false}));if(!r.ok){toast(r.error,true);return;}const ch=await modal('反推结果','<pre>'+esc(r.text)+'</pre>',['追加','替换','取消']);if(ch==='取消')return;const r2=await api('reverse',Object.assign({},base,{combine:(ch==='替换'?'replace':'append'),apply:true}));if(r2.ok){toast('已合入');refreshShow();}else toast(r2.error,true);}
function reverse(scope,gid){pickFile('image/*,video/*',f=>reverseFromFile(scope,gid,f));}
async function showReport(){const r=await api('report',{project:S.project});$('#report').textContent=r.report||JSON.stringify(r,null,2);}
async function doValidate(){const r=await api('validate',{project:S.project});$('#report').textContent=JSON.stringify(r,null,2);}
async function doExport(){toast('导出中…');const r=await api('export',{project:S.project});$('#report').textContent=r.ok?('导出成功: '+r.zip+'\n大小: '+r.bytes+' 字节\n分组: '+r.groups+'  图片: '+r.images):JSON.stringify(r,null,2);toast(r.ok?'已导出':'导出失败',!r.ok);}
async function importPackFile(f){toast('上传中…');const up=await uploadFile(f);if(!up.ok){toast(up.error,true);return;}const nm=prompt('新工程名（可空）','');const r=await api('import-pack',{zip:up.path,name:((nm&&nm.trim())||null)});if(r.ok){toast('已导入');await reloadProjects();openProject(r.name);}else toast(r.error,true);}
function doImport(){pickFile('.zip',importPackFile);}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('active',x===b));document.querySelectorAll('main section').forEach(s=>s.classList.toggle('on',s.id==='tab-'+b.dataset.tab));});
$('#projectSel').onchange=e=>openProject(e.target.value);
$('#refreshBtn').onclick=async()=>{await reloadProjects();await refreshShow();toast('已刷新');};
$('#newBtn').onclick=async()=>{const n=prompt('新工程名','');if(!n)return;const t=prompt('任务模式 r2v/fl2v/v2v','r2v')||'r2v';const r=await api('new',{name:n,task:t});if(r.ok){toast('已新建');await reloadProjects();openProject(n);}else toast(r.error,true);};
$('#vlmStart').onclick=async()=>{toast('启动VLM中…（首次加载模型较慢）');const r=await api('vlm',{action:'start'});toast(r.ok?'VLM已就绪':(r.error||'启动失败'),!r.ok);badge(await api('vlm',{action:'status'}));};
$('#commonSave').onclick=saveCommon;
$('#commonImport').onclick=()=>pickFile('.txt,.md,text/plain',f=>loadTextTo($('#commonPrompt'),f));
makeDrop($('#commonPrompt'),f=>loadTextTo($('#commonPrompt'),f));
makeDrop($('#commonImport'),f=>loadTextTo($('#commonPrompt'),f));
$('#commonEnhance').onclick=()=>enhance('common',null);
$('#commonMatAdd').onclick=()=>pickFile('image/*,video/*,audio/*',f=>addMaterialFile('common',null,f,$('#commonRole').value,$('#commonMatName').value));
makeDrop($('#commonMatAdd'),f=>addMaterialFile('common',null,f,$('#commonRole').value,$('#commonMatName').value));
$('#addGroupBtn').onclick=addGroup;
$('#saveAllBtn').onclick=saveAllGroups;
$('#detectBtn').onclick=detectChars;
$('#outputSave').onclick=saveOutput;
$('#validateBtn').onclick=doValidate;
$('#exportBtn').onclick=doExport;
$('#importBtn').onclick=doImport;
makeDrop($('#importBtn'),importPackFile);
$('#reportBtn').onclick=showReport;
$('#shutdownBtn').onclick=async()=>{if(!confirm('停止本地服务？（停止后本页无法再操作，可关闭此页）'))return;try{await api('shutdown');}catch(e){}document.body.innerHTML='<div style="padding:50px;font-size:18px">本地服务已停止，可以关闭此页面了。</div>';};
function linesVal(s){return (s||'').split(/\r?\n/).map(x=>x.trim()).filter(Boolean);}
async function loadSettings(){try{const r=await api('config',{});const c=r.config||{};const e=c.enhance||{};const v=e.vlm||{};const a=e.api||{};const d=e.director||{};const L=v.launch||{};
$('#s_backend').value=e.backend||'auto';
$('#s_vlm_server').value=v.server||'';$('#s_vlm_model').value=v.model||'';$('#s_vlm_key').value=v.api_key||'';
$('#s_vlm_python').value=v.python||'';$('#s_vlm_script').value=v.script||'';
$('#s_vlm_exe').value=L.exe||'';$('#s_vlm_cwd').value=L.cwd||'';$('#s_vlm_timeout').value=L.startup_timeout||480;
$('#s_vlm_args').value=(L.args||[]).join('\n');
$('#s_vlm_autostart').checked=!!v.auto_start;$('#s_vlm_autostop').checked=!!v.auto_stop;
$('#s_api_base').value=a.base_url||'';$('#s_api_key').value=a.api_key||'';$('#s_api_model').value=a.model||'';$('#s_api_notmpl').checked=a.no_template_kwargs!==false;
$('#s_media_dirs').value=(e.media_dirs||[]).join('\n');
$('#s_dir_url').value=d.url||'';$('#s_dir_llm').value=d.llm_url||'';$('#s_dir_model').value=d.model||'';$('#s_dir_format').value=d.api_format||'openai_compat';$('#s_dir_key').value=d.api_key||'';$('#s_dir_lang').value=d.output_language||'en';
$('#settingsMsg').textContent='已加载: '+(r.path||'');}catch(err){toast('加载设置失败: '+err,true);}}
function settingsPatch(){return {enhance:{backend:$('#s_backend').value,media_dirs:linesVal($('#s_media_dirs').value),vlm:{server:$('#s_vlm_server').value,model:$('#s_vlm_model').value,api_key:$('#s_vlm_key').value,python:$('#s_vlm_python').value,script:$('#s_vlm_script').value,auto_start:$('#s_vlm_autostart').checked,auto_stop:$('#s_vlm_autostop').checked,launch:{exe:$('#s_vlm_exe').value,cwd:$('#s_vlm_cwd').value,startup_timeout:(parseInt($('#s_vlm_timeout').value)||480),args:linesVal($('#s_vlm_args').value)}},api:{base_url:$('#s_api_base').value,api_key:$('#s_api_key').value,model:$('#s_api_model').value,no_template_kwargs:$('#s_api_notmpl').checked},director:{url:$('#s_dir_url').value,llm_url:$('#s_dir_llm').value,model:$('#s_dir_model').value,api_format:$('#s_dir_format').value,api_key:$('#s_dir_key').value,output_language:$('#s_dir_lang').value}}};}
async function saveSettings(){const r=await api('set-config',{patch:settingsPatch()});if(r.ok){toast('设置已保存');$('#settingsMsg').textContent='已保存: '+r.path;badge(await api('vlm',{action:'status'}));}else toast(r.error||'保存失败',true);}
async function testVlm(){toast('测试中…');const r=await api('test-connection',{kind:'vlm',patch:settingsPatch()});toast(r.ok?('本地VLM可达: '+(r.server||'')):(r.error||'不可达'),!r.ok);badge(await api('vlm',{action:'status'}));}
async function testApi(){toast('测试中…');const r=await api('test-connection',{kind:'api',patch:settingsPatch()});toast(r.ok?('在线API可用: '+((r.reply||'').slice(0,40))):('在线API失败: '+(r.error||'')),!r.ok);}
$('#settingsSave').onclick=saveSettings;
$('#settingsReload').onclick=loadSettings;
$('#testVlm').onclick=testVlm;
$('#testApi').onclick=testApi;
$('#startVlm2').onclick=async()=>{toast('启动VLM中…（首次加载模型较慢）');const r=await api('vlm',{action:'start'});toast(r.ok?'VLM已就绪':(r.error||'启动失败'),!r.ok);badge(await api('vlm',{action:'status'}));};
$('#stopVlm2').onclick=async()=>{const r=await api('vlm',{action:'stop'});toast(r.ok?(r.stopped?'已关闭本工具启动的VLM':'没有由本工具启动的VLM'):(r.error||'停止失败'),!r.ok);badge(await api('vlm',{action:'status'}));};
(async()=>{try{const st=await api('vlm',{action:'status'});badge(st);}catch(e){}await reloadProjects();try{await loadSettings();}catch(e){}})();
</script></body></html>'''


def serve(host, port, open_browser=False):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import urllib.parse, threading
    srv = None
    HELP = {"endpoints": ["new", "list", "active", "use", "show", "report", "capabilities",
                          "set-common", "add-group", "set-group", "del-group", "add-material",
                          "set-source", "set-output", "export", "import-pack", "validate", "config",
                          "enhance", "reverse", "vlm", "detect-chars", "replace-char", "media", "upload",
                          "set-config", "test-connection", "shutdown"],
            "ui": f"http://{host}:{port}/",
            "note": "GET / 打开网页界面；POST /<endpoint> body: {\"args\": {...}}；POST /upload 上传；POST /shutdown 停止服务"}
    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def _send_html(self, html, code=200):
            b = html.encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/ui", "/index.html"): self._send_html(WEB_UI_HTML)
            elif path in ("/help", "/api"): self._send(HELP)
            elif path == "/favicon.ico": self._send({}, 204)
            else: self._send({"ok": False, "error": "not found"}, 404)
        def do_POST(self):
            try:
                ln = int(self.headers.get("Content-Length") or 0)
                cmd = self.path.strip("/").split("?")[0]
                if cmd == "upload":
                    data = self.rfile.read(ln) if ln else b""
                    fname = urllib.parse.unquote(self.headers.get("X-Filename") or "upload.bin")
                    dst = _save_upload(fname, data)
                    self._send({"ok": True, "path": str(dst), "name": dst.name}); return
                if cmd in ("shutdown", "exit", "quit"):
                    self._send({"ok": True, "shutdown": True})
                    threading.Thread(target=(lambda: srv and srv.shutdown()), daemon=True).start()
                    return
                body = json.loads(self.rfile.read(ln) or b"{}")
                args = body.get("args", body) or {}
                self._send(cmd_from_args(cmd, args))
            except SystemExit as e:
                self._send({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                self._send({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        def log_message(self, *a): pass
    url = f"http://{host}:{port}/"
    print(f"[{APP_NAME}] 网页界面: {url}   (API: POST /<cmd>)   root={DEFAULT_ROOT}")
    try:
        srv = ThreadingHTTPServer((host, port), H)
    except OSError as e:
        print(f"[{APP_NAME}] 端口 {port} 已被占用：{e}")
        print(f"[{APP_NAME}] 可能已有一个实例在运行，直接打开浏览器即可：{url}")
        if open_browser:
            import webbrowser
            webbrowser.open(url)
        return
    if open_browser:
        import threading, webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"[{APP_NAME}] 关闭此窗口即停止服务。")
    srv.serve_forever()

def main():
    argv = sys.argv[1:]
    if not argv:
        argv = ["serve", "--open"]
    ap = build_parser(); a = ap.parse_args(argv)
    if a.cmd == "serve":
        return serve(a.host, a.port, getattr(a, "open", False))
    if a.cmd == "report":
        print(report_text(a.root, resolve_name(a.root, a.project)))
        return
    if a.cmd == "export":
        r = export_pack(a.root, resolve_name(a.root, a.project), a.out)
        _jprint(r)
        print("\n" + report_text(a.root, resolve_name(a.root, a.project)))
        return
    _jprint(run_cli(a))

if __name__ == "__main__":
    main()
