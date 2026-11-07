# -*- coding: utf-8 -*-
"""
AutoClicker v1.4 —— 物理级鼠标 / 键盘连点器

由 Luo-Jacob 制作 · https://github.com/Luo-Jacob/autoclicker
MIT License · Copyright (c) 2026 Luo-Jacob

功能总览:
  [基础点击] 一个热键挂载多个动作, 同时开始、各自独立节奏; 每个动作可设
             频率(n±m ms/次 或 n±m 次/s 或 单位时间随机 n~m 次)、坐标
             (跟随鼠标 / 固定坐标 / 编号坐标)、结束条件(秒 / 次数 / 再按热键)、
             单点时长(按住 ms)。
  [节点循环] 一个流程含 k 个节点, 顺序执行; 每节点独立设点击次数、频率、
             单点时长、循环间隔; 支持循环轮数、全局循环间隔、定时启动。
  [高级脚本] 类 C 伪代码 DSL, 无头文件; 内置 rand/left/right/middle/keyboard/
             pause/hotkey/mouse; 支持 int/for/while/if else 与基础运算符。
  [其它]     最多 5 键组合热键、定时启动、编号坐标库、方案预设快速切换、
             全局热键与三重急停、亮暗主题、背景图、系统托盘、开机启动。

配置: 程序同目录 AClib/config.json (schema v3, 自动从 v2 迁移)。
"""

import ctypes
import json
import os
import queue
import random
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from ctypes import wintypes

import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkfont

try:
    from pynput import keyboard as _pkb
except Exception:
    _pkb = None

APP_NAME = "AutoClicker"
APP_VERSION = "1.5.0"
MUTEX_NAME = "Global\\AutoClicker_SingleInstance"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class _NullStream(object):
    def write(self, *a, **k):
        return 0

    def flush(self, *a, **k):
        pass

    def isatty(self):
        return False


if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    d = os.path.dirname(os.path.abspath(__file__))
    if os.path.basename(d).lower() in ("src", "source", "app"):
        d = os.path.dirname(d)
    return d


CONFIG_DIR = os.path.join(base_dir(), "AClib")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PICTURES_DIR = os.path.join(CONFIG_DIR, "pictures")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")
LEGACY_CONFIG_FILES = [os.path.join(base_dir(), "AutoClicker", "config.json")]
PROJECT_URL = "https://github.com/Luo-Jacob/autoclicker"
LATEST_URL = PROJECT_URL + "/releases/latest"   # 最新版本页面
AUTHOR = "Luo-Jacob"

MOD_CTRL, MOD_ALT, MOD_SHIFT, MOD_WIN = 0x0002, 0x0001, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
MODS_ORDER = ("ctrl", "alt", "shift", "win")
MOD_DISPLAY = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}

# 自测时可用它替换真实输入, 避免 selftest 真的点击
CLICK_SINK = None


def new_id():
    return uuid.uuid4().hex[:10]


def _i(v, default=0, minimum=None):
    try:
        n = int(float(v))
    except Exception:
        n = int(default)
    if minimum is not None:
        n = max(minimum, n)
    return n


def _f(v, default=0.0, minimum=None):
    try:
        n = float(v)
    except Exception:
        n = float(default)
    if minimum is not None:
        n = max(minimum, n)
    return n


# ---------------------------------------------------------------------------
# 数据模型 (schema v3)
# ---------------------------------------------------------------------------
def default_schedule():
    """定时启动: countdown=倒计时 / clock=指定时刻(一次) / daily=每日时刻。"""
    return {"enabled": False, "mode": "countdown", "delay_s": 60,
            "at": "09:00", "daily": False}


def default_action():
    """一个点击动作。各处的 n / m / k 都是彼此独立的参数, 不共用变量。"""
    return {
        "id": new_id(),
        "type": "mouse",             # mouse | keyboard | script
        "mouse_button": "left",      # left | middle | right
        "kbd": {"mods": [], "vk": 0x74},
        "rate_mode": "ms",           # ms = n±m ms/次 | cps = n±m 次/s | window = 单位时间随机 n~m 次
        "interval_ms": 100,          # n (ms/次)
        "jitter_ms": 20,             # m (ms)
        "cps": 10,                   # n (次/s)
        "cps_jitter": 2,             # m (次/s)
        "window_ms": 1000,           # 单位时间
        "min_n": 3,                  # n 次/单位时间
        "max_n": 8,                  # m 次/单位时间
        "hold_ms": 0,                # 单点时长(按住)
        "pos_mode": "cursor",        # cursor=跟随鼠标 | fixed=固定坐标 | coord=编号坐标
        "pos": {"x": 0, "y": 0},
        "coord_no": 0,
        "stop_mode": "infinite",     # infinite=再按一次热键 | count | duration
        "stop_count": 0,
        "stop_duration_ms": 0,       # 内部以 ms 存, 界面以秒输入
        "script": "",                # type=script 时的伪代码
        "repeat": 0,                 # 兼容旧字段
    }


def default_binding(index=1):
    """基础点击分区: 一个热键 + 一组同时启动的独立动作。"""
    return {
        "id": new_id(),
        "no": index,
        "name": "绑定 %d" % index,
        "enabled": True,
        "hotkey": {"mods": ["ctrl", "alt"], "vk": ord("X")},
        "hotkey_type": "combo",      # combo | multi | none
        "hotkey_keys": [],            # 最多 5 个任意按键的 VK
        "actions": [default_action()],
        "repeat": 0,                 # 整组总点击次数上限, 0 = 无限
        "loop": True,
        "schedule": default_schedule(),
    }


def default_node(index=1):
    """节点循环里的一个动作节点。"""
    return {
        "id": new_id(),
        "no": index,
        "action": default_action(),
        "click_count": 1,            # 本节点点击次数 n
        "interval_ms": 100,          # 点击频率 n ms/次 (click_count > 1 时生效)
        "jitter_ms": 0,              # 点击频率 ±m ms/次
        "hold_ms": 0,                # 单机时长 n ms
        "round_duration_ms": 0,      # 单轮时长 n ms (0=不限, 到时结束本节点)
        "node_interval_ms": 100,     # 本节点执行完后的循环间隔 n ms
    }


def default_flow(index=1):
    """节点循环分区: k 个节点顺序循环 n 轮。"""
    return {
        "id": new_id(),
        "no": index,
        "name": "流程 %d" % index,
        "enabled": True,
        "hotkey": {"mods": ["ctrl", "alt"], "vk": ord("J")},
        "hotkey_type": "combo",
        "hotkey_keys": [],
        "rounds": 0,                 # 0 = 无限循环
        "round_interval_ms": 0,      # 全局循环间隔(每轮结束后)
        "stop_mode": "infinite",
        "stop_count": 0,
        "stop_duration_ms": 0,
        "nodes": [default_node()],
        "schedule": default_schedule(),
    }


def default_coord(no=1, name="", x=0, y=0):
    return {"no": no, "name": name or ("坐标 %d" % no), "x": int(x), "y": int(y)}


def default_preset(no=1, name="", bindings=None, flows=None, coords=None):
    return {"no": no, "name": name or ("方案 %d" % no),
            "bindings": json.loads(json.dumps(bindings or [])),
            "flows": json.loads(json.dumps(flows or [])),
            "coords": json.loads(json.dumps(coords or []))}


def default_config():
    return {
        "version": 3,
        "settings": {
            "emergency_hotkey": {"mods": ["ctrl", "alt"], "vk": ord("K")},
            "panic_fallback": True,
            "close_action": "tray",
            "start_hidden": False,
            "autostart": False,
            "theme": "light",
            "background": "",
            "background_opacity": 45,  # 启用背景图时内容面板的遮罩不透明度(0-100, 越小越透)
            # v1.5 个性化
            "accent": "blue",          # 强调色方案: blue | violet | teal | rose | amber
            "density": "comfortable",  # 界面密度: compact | comfortable
            "radius": 10,              # 圆角半径 0-18
            "animations": True,        # 主题 / 分区切换的过渡动画
            "badges": True,            # 状态徽标
            "script_enabled": False,   # 高级脚本默认关闭, 需显式开启
            "restore_cursor": True,    # 固定坐标点击后把光标移回原位
            "active_preset": 0,        # 0 = 当前工作集(不属于任何预设)
            "scratch_script": "",
        },
        "bindings": [],
        "flows": [],
        "coords": [],
        "presets": [],
    }


# ---------------------------------------------------------------------------
# 清洗 / 迁移
# ---------------------------------------------------------------------------
def _sanitize_combo(combo, fallback):
    if not isinstance(combo, dict):
        return dict(fallback)
    mods = [m for m in combo.get("mods", []) if m in MODS_ORDER]
    vk = _i(combo.get("vk"), fallback.get("vk", 0))
    if vk <= 0 or vk > 0xFE:
        vk = int(fallback["vk"])
    return {"mods": mods, "vk": vk}


def _sanitize_pos(p):
    if not isinstance(p, dict):
        return {"x": 0, "y": 0}
    return {"x": _i(p.get("x"), 0), "y": _i(p.get("y"), 0)}


def _sanitize_schedule(s):
    out = default_schedule()
    if not isinstance(s, dict):
        return out
    out["enabled"] = bool(s.get("enabled", False))
    mode = s.get("mode")
    out["mode"] = mode if mode in ("countdown", "clock", "daily") else "countdown"
    out["delay_s"] = _i(s.get("delay_s"), 60, 1)
    at = str(s.get("at") or "09:00")
    out["at"] = at if len(at) >= 4 else "09:00"
    out["daily"] = bool(s.get("daily", False))
    return out


def schedule_due(sched, now=None):
    """计算定时启动的到期时间戳; 未启用返回 None。模块级函数便于自测。"""
    now = now or time.time()
    s = _sanitize_schedule(sched)
    if not s["enabled"]:
        return None
    if s["mode"] == "countdown":
        return now + s["delay_s"]
    try:
        hh, mm = s["at"].split(":")[:2]
        lt = time.localtime(now)
        due = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                           int(hh), int(mm), 0, 0, 0, -1))
    except Exception:
        return None
    if due <= now:
        due += 86400
    return due


def _sanitize_action(a, fallback=None):
    act = default_action() if fallback is None else dict(fallback)
    if not isinstance(a, dict):
        return act
    if isinstance(a.get("id"), str) and a["id"]:
        act["id"] = a["id"]
    for k in ("type", "mouse_button", "rate_mode", "stop_mode", "pos_mode", "script"):
        if k in a:
            act[k] = a[k]
    for k in ("interval_ms", "jitter_ms", "cps", "cps_jitter", "window_ms",
              "min_n", "max_n", "hold_ms", "coord_no", "stop_count",
              "stop_duration_ms", "repeat"):
        if k in a:
            act[k] = _i(a[k], act.get(k, 0), 0)
    act["pos"] = _sanitize_pos(a.get("pos"))
    act["kbd"] = _sanitize_combo(a.get("kbd"), act.get("kbd") or {"mods": [], "vk": 0x74})
    if act["type"] not in ("mouse", "keyboard", "script"):
        act["type"] = "mouse"
    if act["mouse_button"] not in ("left", "middle", "right"):
        act["mouse_button"] = "left"
    if act["rate_mode"] not in ("ms", "cps", "window"):
        act["rate_mode"] = "ms"
    if act["pos_mode"] not in ("cursor", "fixed", "coord"):
        act["pos_mode"] = "cursor"
    if act["stop_mode"] not in ("infinite", "count", "duration"):
        act["stop_mode"] = "count" if act.get("repeat", 0) > 0 else "infinite"
    if act["stop_mode"] == "count":
        act["stop_count"] = max(1, act.get("stop_count", 0) or act.get("repeat", 0) or 1)
    if act["stop_mode"] == "duration":
        act["stop_duration_ms"] = max(1, act.get("stop_duration_ms", 0) or 1000)
    act["repeat"] = act["stop_count"] if act["stop_mode"] == "count" else 0
    act["interval_ms"] = max(0, act.get("interval_ms", 100))
    act["cps"] = max(1, act.get("cps", 10))
    act["window_ms"] = max(1, act.get("window_ms", 1000))
    act["min_n"] = max(0, act.get("min_n", 3))
    act["max_n"] = max(act["min_n"], act.get("max_n", 8))
    act["script"] = str(act.get("script") or "")
    if not act.get("id"):
        act["id"] = new_id()
    return act


def _sanitize_hotkey(item, fallback):
    ht = item.get("hotkey_type")
    if ht not in ("combo", "multi", "none"):
        ht = "combo" if item.get("hotkey") else "none"
    out = {"hotkey": _sanitize_combo(item.get("hotkey"), fallback),
           "hotkey_type": ht, "hotkey_keys": []}
    keys = item.get("hotkey_keys")
    if isinstance(keys, list):
        seen = []
        for v in keys:
            n = _i(v, 0)
            if 0 < n <= 0xFE and n not in seen and len(seen) < 5:
                seen.append(n)
        out["hotkey_keys"] = seen
    if ht == "multi" and not out["hotkey_keys"]:
        out["hotkey_type"] = "combo"
    return out


def _sanitize_binding(item, index):
    b = default_binding(index)
    if not isinstance(item, dict):
        return b
    if isinstance(item.get("id"), str) and item["id"]:
        b["id"] = item["id"]
    b["no"] = _i(item.get("no"), index, 1)
    if "name" in item:
        b["name"] = str(item["name"])
    b.update(_sanitize_hotkey(item, b["hotkey"]))
    b["enabled"] = bool(item.get("enabled", True))
    b["repeat"] = _i(item.get("repeat"), 0, 0)
    b["loop"] = bool(item.get("loop", True))
    b["schedule"] = _sanitize_schedule(item.get("schedule"))
    acts = item.get("actions")
    if isinstance(acts, list) and acts:
        b["actions"] = [_sanitize_action(a) for a in acts]
    else:
        legacy = dict(item)
        legacy.pop("id", None)
        if "action" in legacy:
            legacy["type"] = legacy["action"]
        b["actions"] = [_sanitize_action(legacy)]
    return b


def _sanitize_node(item, index):
    n = default_node(index)
    if not isinstance(item, dict):
        return n
    if isinstance(item.get("id"), str) and item["id"]:
        n["id"] = item["id"]
    n["no"] = _i(item.get("no"), index, 1)
    n["action"] = _sanitize_action(item.get("action") or item)
    for k in ("click_count", "interval_ms", "jitter_ms", "hold_ms",
              "round_duration_ms", "node_interval_ms"):
        n[k] = _i(item.get(k), n[k], 0)
    n["click_count"] = max(1, n["click_count"])
    return n


def _sanitize_flow(item, index):
    f = default_flow(index)
    if not isinstance(item, dict):
        return f
    if isinstance(item.get("id"), str) and item["id"]:
        f["id"] = item["id"]
    f["no"] = _i(item.get("no"), index, 1)
    if "name" in item:
        f["name"] = str(item["name"])
    f.update(_sanitize_hotkey(item, f["hotkey"]))
    f["enabled"] = bool(item.get("enabled", True))
    for k in ("rounds", "round_interval_ms", "stop_count", "stop_duration_ms"):
        f[k] = _i(item.get(k), f[k], 0)
    mode = item.get("stop_mode")
    f["stop_mode"] = mode if mode in ("infinite", "count", "duration") else "infinite"
    f["schedule"] = _sanitize_schedule(item.get("schedule"))
    nodes = item.get("nodes")
    if isinstance(nodes, list) and nodes:
        f["nodes"] = [_sanitize_node(x, i) for i, x in enumerate(nodes, 1)]
    return f


def _sanitize_coord(item, index):
    if not isinstance(item, dict):
        return default_coord(index)
    return {"no": _i(item.get("no"), index, 1),
            "name": str(item.get("name") or ("坐标 %d" % index)),
            "x": _i(item.get("x"), 0), "y": _i(item.get("y"), 0)}


def _sanitize_preset(item, index):
    if not isinstance(item, dict):
        return default_preset(index)
    p = default_preset(_i(item.get("no"), index, 1), str(item.get("name") or ""))
    p["bindings"] = [_sanitize_binding(x, i) for i, x in
                     enumerate(item.get("bindings") or [], 1)]
    p["flows"] = [_sanitize_flow(x, i) for i, x in
                  enumerate(item.get("flows") or [], 1)]
    p["coords"] = [_sanitize_coord(x, i) for i, x in
                   enumerate(item.get("coords") or [], 1)]
    return p


class Config(object):
    def __init__(self):
        self._lock = threading.RLock()
        self.data = default_config()
        self.load()

    def load(self):
        try:
            path = CONFIG_FILE
            if not os.path.isfile(path):
                for legacy in LEGACY_CONFIG_FILES:
                    if os.path.isfile(legacy):
                        path = legacy
                        break
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.data = self._sanitize(raw)
                if path != CONFIG_FILE:
                    self.save()
        except Exception:
            self.data = default_config()

    def save(self):
        with self._lock:
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                tmp = CONFIG_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                if os.path.exists(CONFIG_FILE):
                    os.remove(CONFIG_FILE)
                os.replace(tmp, CONFIG_FILE)
            except Exception:
                pass

    @staticmethod
    def _sanitize(raw):
        cfg = default_config()
        if not isinstance(raw, dict):
            return cfg
        st = raw.get("settings")
        s = cfg["settings"]
        if isinstance(st, dict):
            for k in list(s.keys()):
                if k in st:
                    s[k] = st[k]
            s["emergency_hotkey"] = _sanitize_combo(
                st.get("emergency_hotkey"), s["emergency_hotkey"])
            if "close_action" not in st and "close_to_tray" in st:
                s["close_action"] = "tray" if st.get("close_to_tray") else "quit"
            if s.get("close_action") not in ("tray", "quit"):
                s["close_action"] = "tray"
            if s.get("theme") not in ("light", "dark"):
                s["theme"] = "light"
            s["background"] = str(s.get("background") or "")
            s["background_opacity"] = min(90, max(10, _i(s.get("background_opacity"), 45)))
            if s.get("accent") not in ACCENTS:
                s["accent"] = "blue"
            if s.get("density") not in ("compact", "comfortable"):
                s["density"] = "comfortable"
            s["radius"] = min(18, max(0, _i(s.get("radius"), 10)))
            s["animations"] = bool(s.get("animations", True))
            s["badges"] = bool(s.get("badges", True))
            s["autostart"] = bool(s.get("autostart"))
            s["panic_fallback"] = bool(s.get("panic_fallback", True))
            s["start_hidden"] = bool(s.get("start_hidden", False))
            s["script_enabled"] = bool(s.get("script_enabled", False))
            s["restore_cursor"] = bool(s.get("restore_cursor", True))
            s["active_preset"] = _i(s.get("active_preset"), 0, 0)
            s["scratch_script"] = str(s.get("scratch_script") or "")

        bl = raw.get("bindings")
        if isinstance(bl, list):
            cfg["bindings"] = [_sanitize_binding(x, i) for i, x in enumerate(bl, 1)]
        fl = raw.get("flows")
        if isinstance(fl, list):
            cfg["flows"] = [_sanitize_flow(x, i) for i, x in enumerate(fl, 1)]
        cl = raw.get("coords")
        if isinstance(cl, list):
            cfg["coords"] = [_sanitize_coord(x, i) for i, x in enumerate(cl, 1)]
        pl = raw.get("presets")
        if isinstance(pl, list) and pl:
            cfg["presets"] = [_sanitize_preset(x, i) for i, x in enumerate(pl, 1)]
        elif _i(raw.get("version"), 2) < 3 and (cfg["bindings"] or cfg["flows"]):
            # v2 -> v3: 把当前工作集自动存成 #1 默认方案
            cfg["presets"] = [default_preset(1, "默认方案",
                                             cfg["bindings"], cfg["flows"], cfg["coords"])]
            s["active_preset"] = 1
        return cfg

    @property
    def settings(self):
        return self.data["settings"]

    @property
    def bindings(self):
        return self.data["bindings"]

    @bindings.setter
    def bindings(self, v):
        self.data["bindings"] = v

    @property
    def flows(self):
        return self.data["flows"]

    @flows.setter
    def flows(self, v):
        self.data["flows"] = v

    @property
    def coords(self):
        return self.data["coords"]

    @coords.setter
    def coords(self, v):
        self.data["coords"] = v

    @property
    def presets(self):
        return self.data["presets"]

    def items(self):
        return [("basic", b) for b in self.bindings] + [("flow", f) for f in self.flows]

    def item_by_id(self, iid):
        for kind, it in self.items():
            if it["id"] == iid:
                return kind, it
        return None, None

    def coord_by_no(self, no):
        for c in self.coords:
            if _i(c.get("no"), 0) == _i(no, -1):
                return c
        return None

    def next_coord_no(self):
        return max([_i(c.get("no"), 0) for c in self.coords] or [0]) + 1

    def next_preset_no(self):
        return max([_i(p.get("no"), 0) for p in self.presets] or [0]) + 1


# ---------------------------------------------------------------------------
# 虚拟键 / 显示
# ---------------------------------------------------------------------------
EXTENDED_VKS = set([0xA3, 0xA5, 0x2C, 0x2D, 0x2E, 0x21, 0x22, 0x23, 0x24,
                    0x25, 0x26, 0x27, 0x28, 0x5B, 0x5C, 0x6F, 0x13, 0x90])


def is_extended(vk):
    return vk in EXTENDED_VKS


def vk_name(vk):
    try:
        vk = int(vk)
    except Exception:
        return "?"
    scan = user32.MapVirtualKeyW(vk, 0)
    if scan:
        buf = ctypes.create_unicode_buffer(64)
        lparam = (scan << 16) | ((1 if is_extended(vk) else 0) << 24)
        if user32.GetKeyNameTextW(lparam, buf, 64):
            return buf.value
    if 0x30 <= vk <= 0x5A:
        return chr(vk)
    return "VK%02X" % vk


def combo_mods(combo):
    if not combo:
        return 0
    m = 0
    for name in combo.get("mods", []):
        m |= {"ctrl": MOD_CTRL, "alt": MOD_ALT,
              "shift": MOD_SHIFT, "win": MOD_WIN}.get(name, 0)
    return m


def fmt_combo(combo):
    if not combo:
        return "未设置"
    parts = [MOD_DISPLAY[m] for m in MODS_ORDER if m in combo.get("mods", [])]
    parts.append(vk_name(combo.get("vk")))
    return "+".join(parts)


def combo_key(combo):
    if not combo:
        return None
    return tuple(sorted(combo.get("mods", []))) + (int(combo.get("vk", 0)),)


def fmt_keys(keys):
    ks = [_i(k, 0) for k in (keys or []) if _i(k, 0) > 0]
    return "+".join(vk_name(k) for k in ks) if ks else "未设置"


def hotkey_text(item):
    ht = item.get("hotkey_type", "combo")
    if ht == "multi":
        return fmt_keys(item.get("hotkey_keys"))
    if ht == "none":
        return "手动/定时"
    return fmt_combo(item.get("hotkey"))


def hotkey_dedupe(item):
    ht = item.get("hotkey_type", "combo")
    if ht == "multi":
        return ("m", tuple(sorted(_i(k, 0) for k in item.get("hotkey_keys", []))))
    if ht == "none":
        return None
    return ("c", combo_key(item.get("hotkey")))


SPECIAL_VK = {
    "CTRL": 0x11, "ALT": 0x12, "SHIFT": 0x10, "WIN": 0x5B,
    "ENTER": 0x0D, "RETURN": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B,
    "SPACE": 0x20, "TAB": 0x09, "BACKSPACE": 0x08, "DELETE": 0x2E,
    "DEL": 0x2E, "INSERT": 0x2D, "INS": 0x2D, "HOME": 0x24, "END": 0x23,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22, "PGUP": 0x21, "PGDN": 0x22,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "CAPSLOCK": 0x14, "PRTSC": 0x2C, "NUMLOCK": 0x90,
}
for _fi in range(1, 13):
    SPECIAL_VK["F%d" % _fi] = 0x6F + _fi
for _ci, _ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    SPECIAL_VK[_ch] = 0x41 + _ci
for _d in "0123456789":
    SPECIAL_VK[_d] = ord(_d)


def vk_from_name(name):
    n = str(name or "").strip().upper()
    if not n:
        return 0
    if n in SPECIAL_VK:
        return SPECIAL_VK[n]
    if n.startswith("VK") and len(n) > 2:
        try:
            return int(n[2:], 16) & 0xFF
        except Exception:
            return 0
    if len(n) == 1:
        r = user32.VkKeyScanW(ord(n))
        if r != -1:
            return r & 0xFF
    return 0


def parse_key_spec(spec):
    """把 'Ctrl+Alt+X' / 'F5' / VK 整数 转成 combo dict。"""
    if isinstance(spec, (int, float)):
        return {"mods": [], "vk": int(spec) & 0xFF}
    parts = [p.strip().upper() for p in str(spec or "").split("+") if p.strip()]
    mods, vk = [], 0
    for p in parts:
        if p in ("CTRL", "CONTROL"):
            if "ctrl" not in mods:
                mods.append("ctrl")
        elif p in ("ALT", "MENU"):
            if "alt" not in mods:
                mods.append("alt")
        elif p == "SHIFT":
            if "shift" not in mods:
                mods.append("shift")
        elif p in ("WIN", "SUPER", "CMD", "META"):
            if "win" not in mods:
                mods.append("win")
        else:
            vk = vk_from_name(p)
    return {"mods": [m for m in MODS_ORDER if m in mods], "vk": vk}


# ---------------------------------------------------------------------------
# Win32 SendInput —— 真实物理输入模拟
# ---------------------------------------------------------------------------
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MAP = {
    "left": (0x0002, 0x0004), "middle": (0x0020, 0x0040), "right": (0x0008, 0x0010),
}
KEYEVENTF_KEYUP, KEYEVENTF_EXTENDEDKEY = 0x0002, 0x0001


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    _anonymous_ = ("_i",)
    _fields_ = [("type", wintypes.DWORD), ("_i", _I)]


def _send(inputs):
    if not inputs:
        return 0
    arr = (INPUT * len(inputs))(*inputs)
    return user32.SendInput(len(inputs), ctypes.byref(arr), ctypes.sizeof(INPUT))


def _mouse_input(flags):
    e = INPUT()
    e.type = INPUT_MOUSE
    e.mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)
    return e


def _key_input(vk, up):
    e = INPUT()
    e.type = INPUT_KEYBOARD
    flags = KEYEVENTF_KEYUP if up else 0
    if is_extended(vk):
        flags |= KEYEVENTF_EXTENDEDKEY
    scan = user32.MapVirtualKeyW(vk, 0)
    e.ki = KEYBDINPUT(vk, scan & 0xFF, flags, 0, 0)
    return e


def _mod_vks(mods):
    out = []
    for m in mods:
        out.append({"ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B}.get(m, 0))
    return [v for v in out if v]


def get_cursor_pos():
    try:
        p = POINT()
        if user32.GetCursorPos(ctypes.byref(p)):
            return int(p.x), int(p.y)
    except Exception:
        pass
    return 0, 0


def set_cursor_pos(x, y):
    try:
        return bool(user32.SetCursorPos(int(x), int(y)))
    except Exception:
        return False


CURSOR_LOCK = threading.RLock()


def interruptible_wait(seconds, stop_event=None):
    """可急停的等待; 返回 True 表示被中止。"""
    seconds = max(0.0, float(seconds))
    if stop_event is not None:
        return stop_event.wait(seconds)
    if seconds > 0:
        time.sleep(seconds)
    return False


def mouse_click(button="left", hold_ms=0, pos=None, restore=True, stop_event=None):
    """在指定坐标(或当前光标位置)按下并释放。固定坐标时可恢复原光标。"""
    down, up = MOUSEEVENTF_MAP.get(button, MOUSEEVENTF_MAP["left"])
    if pos is None:
        _send([_mouse_input(down)])
        if hold_ms > 0:
            interruptible_wait(hold_ms / 1000.0, stop_event)
        _send([_mouse_input(up)])
        return
    with CURSOR_LOCK:
        ox, oy = get_cursor_pos()
        set_cursor_pos(pos[0], pos[1])
        try:
            _send([_mouse_input(down)])
            if hold_ms > 0:
                interruptible_wait(hold_ms / 1000.0, stop_event)
            _send([_mouse_input(up)])
        finally:
            if restore:
                set_cursor_pos(ox, oy)


def keyboard_tap(combo):
    mods = _mod_vks(combo.get("mods", []))
    vk = _i(combo.get("vk"), 0)
    if not vk:
        return
    seq = [_key_input(m, False) for m in mods]
    seq.append(_key_input(vk, False))
    seq.append(_key_input(vk, True))
    seq.extend([_key_input(m, True) for m in reversed(mods)])
    _send(seq)


# ---------------------------------------------------------------------------
# 全局热键 (RegisterHotKey) + 多键组合监听 (低层键盘钩子)
# ---------------------------------------------------------------------------
WM_HOTKEY, WM_QUIT, WM_APP_REFRESH = 0x0312, 0x0012, 0x8001


class HotkeyManager(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True
        self.name = "HotkeyThread"
        self._specs = []
        self._active = {}
        self._next_id = 1
        self._tid = None
        self._ready = threading.Event()
        self._lock = threading.RLock()
        self.errors = []
        self.start()
        self._ready.wait(5)

    def run(self):
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._tid = kernel32.GetCurrentThreadId()
        self._ready.set()
        self._apply()
        while True:
            res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if res <= 0:
                break
            if msg.message == WM_HOTKEY:
                cb = self._active.get(int(msg.wParam))
                if cb:
                    try:
                        cb()
                    except Exception:
                        pass
            elif msg.message == WM_APP_REFRESH:
                self._apply()
        self._unregister_all()

    def update(self, specs):
        with self._lock:
            self._specs = list(specs)
        self._post(WM_APP_REFRESH)

    def stop(self):
        self._post(WM_QUIT)

    def _post(self, msg):
        if not self._tid:
            return
        for _ in range(3):
            if user32.PostThreadMessageW(self._tid, msg, 0, 0):
                return
            time.sleep(0.05)

    def _unregister_all(self):
        for hid in list(self._active.keys()):
            try:
                user32.UnregisterHotKey(None, hid)
            except Exception:
                pass
        self._active.clear()

    def _apply(self):
        with self._lock:
            specs = list(self._specs)
        self._unregister_all()
        errs = []
        for _ref, combo, cb in specs:
            if not combo:
                continue
            mods, vk = combo_mods(combo), _i(combo.get("vk"), 0)
            if not vk:
                continue
            if self._next_id > 0xBFFF:
                self._next_id = 1
            hid = self._next_id
            self._next_id += 1
            ok = False
            for _ in range(3):
                if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                    ok = True
                    break
                if user32.RegisterHotKey(None, hid, mods, vk):
                    ok = True
                    break
                time.sleep(0.02)
            if ok:
                self._active[hid] = cb
            else:
                errs.append(fmt_combo(combo))
        self.errors = errs


def _pkb_vk(key):
    if _pkb is None:
        return None
    if isinstance(key, _pkb.Key):
        try:
            return int(key.value.vk)
        except Exception:
            return None
    if isinstance(key, _pkb.KeyCode):
        if key.vk:
            return int(key.vk)
        if key.char:
            res = user32.VkKeyScanW(ord(key.char))
            if res != -1:
                return res & 0xFF
    return None


class KeyWatcher(object):
    """最多 5 个任意按键同时按下的全局监听(RegisterHotKey 做不到任意多键)。"""

    def __init__(self):
        self._specs = {}          # ref -> (frozenset(vk), callback)
        self._pressed = set()
        self._fired = set()
        self._enabled = True
        self._lock = threading.RLock()
        self._listener = None
        self.errors = []

    def start(self):
        if _pkb is None:
            self.errors = ["缺少 pynput, 多键热键不可用"]
            return
        try:
            self._listener = _pkb.Listener(on_press=self._press, on_release=self._release)
            self._listener.daemon = True
            self._listener.start()
        except Exception as e:
            self.errors = ["多键监听启动失败: %s" % e]

    def update(self, specs):
        with self._lock:
            self._specs = {ref: (frozenset(keys), cb) for ref, keys, cb in specs}
            self._fired.clear()

    def set_enabled(self, on):
        with self._lock:
            self._enabled = bool(on)
            self._pressed.clear()
            self._fired.clear()

    def _press(self, key):
        vk = _pkb_vk(key)
        if not vk:
            return
        with self._lock:
            self._pressed.add(vk)
            if not self._enabled:
                return
            fire = []
            for ref, (target, cb) in self._specs.items():
                if target and target.issubset(self._pressed) and ref not in self._fired:
                    self._fired.add(ref)
                    fire.append(cb)
        for cb in fire:
            try:
                cb()
            except Exception:
                pass

    def _release(self, key):
        vk = _pkb_vk(key)
        if not vk:
            return
        with self._lock:
            self._pressed.discard(vk)
            for ref, (target, _cb) in self._specs.items():
                if not target.issubset(self._pressed):
                    self._fired.discard(ref)

    def stop(self):
        try:
            if self._listener:
                self._listener.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 高级: 类 C 伪代码 DSL (无头文件, 自带解释器)
#   支持 int / for / while / if else / break / continue / 基础运算与逻辑运算
#   内置 rand / left / right / middle / keyboard / pause / hotkey / mouse / point
# ---------------------------------------------------------------------------
class ScriptError(Exception):
    def __init__(self, message, line=0):
        Exception.__init__(self, message)
        self.message = message
        self.line = line

    def __str__(self):
        return ("第 %d 行: " % self.line if self.line else "") + self.message


class ScriptStopped(Exception):
    pass


class Point(object):
    def __init__(self, x=0, y=0):
        self.x, self.y = int(x), int(y)

    def __repr__(self):
        return "(%d, %d)" % (self.x, self.y)

    def __eq__(self, other):
        return isinstance(other, Point) and self.x == other.x and self.y == other.y

    def __ne__(self, other):
        return not self.__eq__(other)


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


_SCRIPT_KEYWORDS = {"int", "if", "else", "while", "for", "break", "continue"}
_SCRIPT_OPS2 = ("==", "!=", "<=", ">=", "&&", "||", "++", "--", "+=", "-=", "*=", "/=")
_SCRIPT_OPS1 = "+-*/%<>=!(){};,."


def script_tokenize(src):
    toks = []
    i, line, col, n = 0, 1, 1, len(src)
    while i < n:
        c = src[i]
        if c in " \t\r":
            i += 1
            col += 1
            continue
        if c == "\n":
            i += 1
            line += 1
            col = 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                if src[i] == "\n":
                    line += 1
                i += 1
            i = min(n, i + 2)
            continue
        if c == '"':
            i += 1
            buf = []
            while i < n and src[i] != '"':
                if src[i] == "\\" and i + 1 < n:
                    nxt = src[i + 1]
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                    i += 2
                else:
                    buf.append(src[i])
                    i += 1
            if i >= n:
                raise ScriptError("字符串未闭合", line)
            i += 1
            toks.append(("STR", "".join(buf), line, col))
            continue
        if c.isdigit():
            j = i
            while j < n and src[j].isdigit():
                j += 1
            toks.append(("INT", int(src[i:j]), line, col))
            col += j - i
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            toks.append(("KW" if word in _SCRIPT_KEYWORDS else "ID", word, line, col))
            col += j - i
            i = j
            continue
        two = src[i:i + 2]
        if two in _SCRIPT_OPS2:
            toks.append(("OP", two, line, col))
            i += 2
            col += 2
            continue
        if c in _SCRIPT_OPS1:
            toks.append(("OP", c, line, col))
            i += 1
            col += 1
            continue
        raise ScriptError("无法识别的字符 %r" % c, line)
    toks.append(("EOF", None, line, col))
    return toks


class ScriptParser(object):
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self, off=0):
        return self.t[min(self.i + off, len(self.t) - 1)]

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def is_op(self, v):
        return self.peek()[0] == "OP" and self.peek()[1] == v

    def is_kw(self, v):
        return self.peek()[0] == "KW" and self.peek()[1] == v

    def expect_op(self, v):
        if not self.is_op(v):
            raise ScriptError("缺少 '%s'" % v, self.peek()[2])
        return self.take()

    def expect_id(self):
        if self.peek()[0] != "ID":
            raise ScriptError("需要标识符", self.peek()[2])
        return self.take()[1]

    def parse_program(self):
        out = []
        while self.peek()[0] != "EOF":
            out.append(self.parse_stmt())
        return out

    def parse_block(self):
        self.expect_op("{")
        out = []
        while not self.is_op("}") and self.peek()[0] != "EOF":
            out.append(self.parse_stmt())
        self.expect_op("}")
        return ("block", out)

    def parse_stmt(self):
        ln = self.peek()[2]
        if self.is_op("{"):
            return self.parse_block()
        if self.is_op(";"):
            self.take()
            return ("empty",)
        if self.is_kw("int"):
            return self.parse_intdecl()
        if self.is_kw("if"):
            self.take()
            self.expect_op("(")
            cond = self.parse_expr()
            self.expect_op(")")
            then = self.parse_stmt()
            other = None
            if self.is_kw("else"):
                self.take()
                other = self.parse_stmt()
            return ("if", cond, then, other)
        if self.is_kw("while"):
            self.take()
            self.expect_op("(")
            cond = self.parse_expr()
            self.expect_op(")")
            return ("while", cond, self.parse_stmt())
        if self.is_kw("for"):
            self.take()
            self.expect_op("(")
            if self.is_kw("int"):
                init = self.parse_intdecl()
            elif self.is_op(";"):
                self.take()
                init = ("empty",)
            else:
                init = self.parse_simple_stmt()
            cond = ("num", 1) if self.is_op(";") else self.parse_expr()
            self.expect_op(";")
            step = ("empty",) if self.is_op(")") else self.parse_simple_stmt(no_semi=True)
            self.expect_op(")")
            return ("for", init, cond, step, self.parse_stmt())
        if self.is_kw("break"):
            self.take()
            self.expect_op(";")
            return ("break",)
        if self.is_kw("continue"):
            self.take()
            self.expect_op(";")
            return ("continue",)
        return self.parse_simple_stmt()

    def parse_intdecl(self):
        self.take()  # int
        decls = []
        while True:
            name = self.expect_id()
            init = None
            if self.is_op("="):
                self.take()
                init = self.parse_expr()
            decls.append((name, init))
            if self.is_op(","):
                self.take()
                continue
            break
        self.expect_op(";")
        return ("int", decls)

    def parse_simple_stmt(self, no_semi=False):
        if self.peek()[0] != "ID":
            raise ScriptError("无法识别的语句", self.peek()[2])
        name = self.take()[1]
        if self.is_op("("):
            self.take()
            args = []
            if not self.is_op(")"):
                args.append(self.parse_expr())
                while self.is_op(","):
                    self.take()
                    args.append(self.parse_expr())
            self.expect_op(")")
            if not no_semi:
                self.expect_op(";")
            return ("expr", ("call", name, args))
        ops = ("=", "+=", "-=", "*=", "/=", "++", "--")
        op = None
        for candidate in ops:
            if self.is_op(candidate):
                op = candidate
                break
        if op is None:
            raise ScriptError("变量 '%s' 后面不是有效的运算符" % name, self.peek()[2])
        self.take()
        if op in ("++", "--"):
            if not no_semi:
                self.expect_op(";")
            return ("assign", name, op, None)
        rhs = self.parse_expr()
        if not no_semi:
            self.expect_op(";")
        return ("assign", name, op, rhs)

    # --- 表达式: 递归下降, 优先级从低到高 ---
    def parse_expr(self):
        return self.parse_or()

    def _bin(self, sub, ops):
        node = sub()
        while self.peek()[0] == "OP" and self.peek()[1] in ops:
            op = self.take()[1]
            node = ("bin", op, node, sub())
        return node

    def parse_or(self):
        return self._bin(self.parse_and, ("||",))

    def parse_and(self):
        return self._bin(self.parse_eq, ("&&",))

    def parse_eq(self):
        return self._bin(self.parse_rel, ("==", "!="))

    def parse_rel(self):
        return self._bin(self.parse_add, ("<", ">", "<=", ">="))

    def parse_add(self):
        return self._bin(self.parse_mul, ("+", "-"))

    def parse_mul(self):
        return self._bin(self.parse_unary, ("*", "/", "%"))

    def parse_unary(self):
        if self.peek()[0] == "OP" and self.peek()[1] in ("!", "-", "+"):
            op = self.take()[1]
            return ("un", op, self.parse_unary())
        return self.parse_postfix()

    def parse_postfix(self):
        node = self.parse_primary()
        while self.is_op("."):
            self.take()
            name = self.expect_id()
            node = ("attr", node, name)
        return node

    def parse_primary(self):
        tok = self.peek()
        if tok[0] == "INT":
            self.take()
            return ("num", tok[1])
        if tok[0] == "STR":
            self.take()
            return ("str", tok[1])
        if tok[0] == "ID":
            name = self.take()[1]
            if self.is_op("("):
                self.take()
                args = []
                if not self.is_op(")"):
                    args.append(self.parse_expr())
                    while self.is_op(","):
                        self.take()
                        args.append(self.parse_expr())
                self.expect_op(")")
                return ("call", name, args)
            return ("var", name)
        if tok[0] == "OP" and tok[1] == "(":
            self.take()
            e = self.parse_expr()
            self.expect_op(")")
            return e
        raise ScriptError("表达式不完整", tok[2])


def compile_script(src):
    """仅做语法检查, 返回 AST。"""
    return ScriptParser(script_tokenize(str(src or ""))).parse_program()


def extract_hotkey(program):
    """脚本第一行 hotkey("...") 视为声明该脚本的启动热键。"""
    if not program:
        return None
    st = program[0]
    if st[0] == "expr" and st[1][0] == "call" and st[1][1] == "hotkey":
        args = st[1][2]
        if args and args[0][0] == "str":
            return args[0][1]
    return None


class ScriptInterpreter(object):
    """解释执行 DSL。所有副作用都经 api 白名单, 无文件/网络/导入能力。"""

    def __init__(self, api, should_stop=None, max_steps=1000000):
        self.api = api
        self.env = {}
        self.should_stop = should_stop or (lambda: False)
        self.max_steps = max_steps
        self.steps = 0

    def _step(self):
        self.steps += 1
        if self.steps > self.max_steps:
            raise ScriptError("执行步数超过上限 %d, 已强制中止" % self.max_steps)
        if self.should_stop():
            raise ScriptStopped()

    def run(self, program):
        for st in program:
            self.exec_stmt(st)

    def exec_stmt(self, st):
        self._step()
        kind = st[0]
        if kind == "empty":
            return
        if kind == "block":
            for s in st[1]:
                self.exec_stmt(s)
            return
        if kind == "expr":
            self.eval(st[1])
            return
        if kind == "int":
            for name, init in st[1]:
                self.env[name] = int(self._num(self.eval(init))) if init is not None else 0
            return
        if kind == "assign":
            name, op = st[1], st[2]
            cur = self.env.get(name, 0)
            if op in ("++", "--"):
                val = cur + (1 if op == "++" else -1)
            else:
                rhs = self._num(self.eval(st[3])) if st[3] is not None else 0
                if op == "=":
                    val = rhs
                elif op == "+=":
                    val = cur + rhs
                elif op == "-=":
                    val = cur - rhs
                elif op == "*=":
                    val = cur * rhs
                elif op == "/=":
                    val = self._div(cur, rhs)
                else:
                    val = rhs
            self.env[name] = int(val)
            return
        if kind == "if":
            if self._truth(self.eval(st[1])):
                self.exec_stmt(st[2])
            elif st[3] is not None:
                self.exec_stmt(st[3])
            return
        if kind == "while":
            while self._truth(self.eval(st[1])):
                self._step()
                try:
                    self.exec_stmt(st[2])
                except _Break:
                    break
                except _Continue:
                    continue
            return
        if kind == "for":
            self.exec_stmt(st[1])
            while self._truth(self.eval(st[2])):
                self._step()
                try:
                    self.exec_stmt(st[4])
                except _Break:
                    break
                except _Continue:
                    pass
                self.exec_stmt(st[3])
            return
        if kind == "break":
            raise _Break()
        if kind == "continue":
            raise _Continue()
        raise ScriptError("未知语句 %s" % kind)

    @staticmethod
    def _truth(v):
        if isinstance(v, str):
            return len(v) > 0
        try:
            return float(v) != 0
        except Exception:
            return bool(v)

    @staticmethod
    def _num(v):
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            try:
                return float(v)
            except Exception:
                return 0
        return 0

    @staticmethod
    def _div(a, b):
        if b == 0:
            raise ScriptError("除数不能为 0")
        q = abs(a) / abs(b)
        q = int(q)
        return -q if (a < 0) != (b < 0) else q

    def eval(self, e):
        self._step()
        k = e[0]
        if k == "num" or k == "str":
            return e[1]
        if k == "var":
            if e[1] not in self.env:
                raise ScriptError("未定义的变量 '%s'" % e[1])
            return self.env[e[1]]
        if k == "attr":
            obj = self.eval(e[1])
            name = e[2]
            if isinstance(obj, Point) and name in ("x", "y"):
                return getattr(obj, name)
            raise ScriptError("不支持的属性 '%s'" % name)
        if k == "un":
            v = self._num(self.eval(e[2]))
            return {"!": lambda: 0 if v else 1, "-": lambda: -v, "+": lambda: v}[e[1]]()
        if k == "bin":
            op = e[1]
            if op == "&&":
                if not self._truth(self.eval(e[2])):
                    return 0
                return 1 if self._truth(self.eval(e[3])) else 0
            if op == "||":
                if self._truth(self.eval(e[2])):
                    return 1
                return 1 if self._truth(self.eval(e[3])) else 0
            a, b = self.eval(e[2]), self.eval(e[3])
            if op in ("==", "!="):
                eq = (a == b) if not (isinstance(a, (int, float)) and isinstance(b, (int, float))) \
                    else self._num(a) == self._num(b)
                return (1 if eq else 0) if op == "==" else (0 if eq else 1)
            a, b = self._num(a), self._num(b)
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                return self._div(a, b)
            if op == "%":
                if b == 0:
                    raise ScriptError("除数不能为 0")
                return a - self._div(a, b) * b
            return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}.get(op, 0) and 1 or 0
        if k == "call":
            name, args = e[1], [self.eval(a) for a in e[2]]
            return self._call(name, args)
        raise ScriptError("未知表达式 %s" % k)

    def _call(self, name, args):
        if name == "rand":
            rng = getattr(self, "_rng", None)
            if rng is None:
                rng = self._rng = random.Random()
            if len(args) >= 2:
                a, b = int(self._num(args[0])), int(self._num(args[1]))
                if a > b:
                    a, b = b, a
                return rng.randint(a, b)
            if len(args) == 1:
                rng.seed(int(self._num(args[0])))
            return rng.randint(10000, 100000)
        if name == "point":
            if len(args) < 2:
                raise ScriptError("point(x, y) 需要两个参数")
            return Point(self._num(args[0]), self._num(args[1]))
        if name == "mouse":
            return self.api.mouse()
        if name in ("left", "right", "middle"):
            self.api.click(name, *args)
            return 0
        if name in ("keyboard", "hotkey"):
            if not args:
                raise ScriptError("%s(*按键) 缺少必填参数" % name)
            self.api.key(args[0])
            return 0
        if name == "pause":
            if not args:
                raise ScriptError("pause(*ms) 缺少必填参数")
            self.api.pause(self._num(args[0]))
            return 0
        raise ScriptError("未知函数 '%s'" % name)


def run_script(program, api, should_stop=None, max_steps=1000000):
    return ScriptInterpreter(api, should_stop, max_steps).run(program)


SCRIPT_TEMPLATE = """// AutoClicker 伪代码示例 —— 无头文件, 直接编写
// int / for / while / if else / break / continue
// 运算符: + - * / % == != < <= > >= && || ! ++ -- += -=
// 内置: rand(seed) left(坐标) right(坐标) middle(坐标)
//       keyboard(*按键) pause(*ms) hotkey(*按键) mouse() point(x,y)
// 带 * 的参数必填; 坐标可写 point(x,y)、mouse()、或直接 left(x, y)

hotkey("Ctrl+Alt+X");          // 顶部这一行可声明脚本的启动热键
int i;
for (i = 0; i < 10; i++) {
    left(mouse());             // 在当前鼠标位置左键点击
    pause(100);                // 间隔 100 ms
}
"""

SCRIPT_GUIDE = """语法（类 C 伪代码，无需 #include / 头文件）:
  int a, b = 10;              声明整数变量
  for (i = 0; i < 10; i++) { ... }
  while (1) { ... }
  if (a > b) { ... } else { ... }
  break;  continue;
  a++; a--; a += 2; a *= 3;
  运算符: + - * / %  == != < <= > >=  && || !  ( ) .

内置函数（* 为必填参数）:
  rand(seed)          返回 10^4 ~ 10^5 的随机数; rand(a,b) 返回 a~b
  left(坐标)          左键点击; 不填=跟随鼠标
  right(坐标)         右键点击
  middle(坐标)        中键点击
  keyboard(*按键)     按下按键, 如 keyboard("A") / keyboard("Ctrl+Shift+F5")
  pause(*ms)          等待 n 毫秒(可被急停立即打断)
  hotkey(*按键)       模拟按下组合键; 写在第一行时用作"声明启动热键"
  mouse()             返回当前鼠标坐标, 可用 mouse().x / mouse().y
  point(x, y)         构造一个坐标

坐标写法:  left() / left(mouse()) / left(point(320, 240)) / left(320, 240)

安全: 解释器只能调用上表函数, 没有文件 / 网络 / 模块导入能力;
      有执行步数上限, 且随时可被急停热键中断。
"""

# ---------------------------------------------------------------------------
# 摘要文案
# ---------------------------------------------------------------------------
def action_label(act):
    t = (act or {}).get("type")
    if t == "keyboard":
        return "键盘 " + fmt_combo(act.get("kbd"))
    if t == "script":
        return "脚本"
    return {"left": "鼠标左键", "middle": "鼠标中键", "right": "鼠标右键"}.get(
        act.get("mouse_button", "left"), "鼠标左键")


def action_rate_label(act):
    mode = (act or {}).get("rate_mode", "ms")
    if mode == "cps":
        n, m = _f(act.get("cps"), 10), _f(act.get("cps_jitter"), 0)
        return ("%g±%g 次/s" % (n, m)) if m else ("%g 次/s" % n)
    if mode == "window":
        return "每 %g s 随机 %d~%d 次" % (_f(act.get("window_ms"), 1000) / 1000.0,
                                          _i(act.get("min_n"), 3), _i(act.get("max_n"), 8))
    n, m = _i(act.get("interval_ms"), 100), _i(act.get("jitter_ms"), 0)
    return ("%d±%d ms/次" % (n, m)) if m else ("%d ms/次" % n)


def action_pos_label(act, cfg=None):
    mode = (act or {}).get("pos_mode", "cursor")
    if mode == "fixed":
        p = act.get("pos") or {}
        return "固定 (%d, %d)" % (_i(p.get("x"), 0), _i(p.get("y"), 0))
    if mode == "coord":
        no = _i(act.get("coord_no"), 0)
        c = cfg.coord_by_no(no) if cfg else None
        return ("#%d %s" % (no, c.get("name", ""))) if c else ("#%d (缺失)" % no)
    return "跟随鼠标"


def action_stop_label(act):
    mode = (act or {}).get("stop_mode") or "infinite"
    if mode == "count":
        return "点满 %d 次" % _i(act.get("stop_count"), 0)
    if mode == "duration":
        return "持续 %g 秒" % (_i(act.get("stop_duration_ms"), 0) / 1000.0)
    return "再按热键 / 急停"


def action_detail(act):
    txt = action_label(act)
    txt += " · " + action_rate_label(act)
    if _i(act.get("hold_ms"), 0):
        txt += " · 按住 %dms" % _i(act.get("hold_ms"), 0)
    return txt


def binding_rhythm(binding, limit=2):
    acts = binding.get("actions") or []
    if not acts:
        return "—"
    parts = [action_rate_label(a) for a in acts[:limit]]
    txt = " / ".join(parts)
    if len(acts) > limit:
        txt += " …"
    return txt


def binding_summary(binding, limit=3):
    acts = binding.get("actions") or []
    if not acts:
        return "（无动作）"
    parts = [action_label(a) for a in acts]
    if len(parts) <= limit:
        return " + ".join(parts)
    return " + ".join(parts[:limit]) + " + …共%d项" % len(parts)


def flow_summary(flow, limit=3):
    nodes = flow.get("nodes") or []
    if not nodes:
        return "（无节点）"
    parts = [action_label((n.get("action") or {})) for n in nodes[:limit]]
    txt = " → ".join(parts)
    if len(nodes) > limit:
        txt += " → …共%d节点" % len(nodes)
    return txt


def node_label(node):
    act = node.get("action") or {}
    rd = _i(node.get("round_duration_ms"), 0)
    return "%s ×%d · %d±%dms · 单轮%s · 间隔%dms" % (
        action_label(act), _i(node.get("click_count"), 1),
        _i(node.get("interval_ms"), 100), _i(node.get("jitter_ms"), 0),
        ("%dms" % rd) if rd else "不限",
        _i(node.get("node_interval_ms"), 0))


# ---------------------------------------------------------------------------
# 节奏与坐标
# ---------------------------------------------------------------------------
def resolve_pos(act, cfg):
    """返回 (x, y) 或 None(跟随鼠标)。"""
    mode = act.get("pos_mode", "cursor")
    if mode == "fixed":
        p = act.get("pos") or {}
        return _i(p.get("x"), 0), _i(p.get("y"), 0)
    if mode == "coord":
        c = cfg.coord_by_no(act.get("coord_no")) if cfg else None
        if c:
            return _i(c.get("x"), 0), _i(c.get("y"), 0)
    return None


def action_delay(act):
    """一次点击后应等待的秒数。"""
    mode = act.get("rate_mode", "ms")
    if mode == "cps":
        n = _f(act.get("cps"), 10, 0.05)
        m = _f(act.get("cps_jitter"), 0, 0)
        rate = max(0.05, n + random.uniform(-m, m))
        return max(0.001, 1.0 / rate)
    n = _f(act.get("interval_ms"), 100, 0)
    m = _f(act.get("jitter_ms"), 0, 0)
    return max(0.001, (n + random.uniform(-m, m)) / 1000.0)


def node_delay(node):
    n = _f(node.get("interval_ms"), 100, 0)
    m = _f(node.get("jitter_ms"), 0, 0)
    return max(0.001, (n + random.uniform(-m, m)) / 1000.0)


def perform_action(act, cfg, worker, hold_override=None):
    """执行一次点击(鼠标 / 键盘 / 脚本)。返回是否成功计入一次点击。"""
    hold = _i(act.get("hold_ms"), 0) if hold_override is None else _i(hold_override, 0)
    t = act.get("type", "mouse")
    if CLICK_SINK is not None:
        CLICK_SINK(("action", t, act.get("mouse_button"), act.get("kbd"),
                    resolve_pos(act, cfg), hold))
        return
    if t == "keyboard":
        keyboard_tap(act.get("kbd") or {})
    elif t == "script":
        run_script_action(act, cfg, worker)
    else:
        pos = resolve_pos(act, cfg)
        restore = bool(cfg.settings.get("restore_cursor", True)) if cfg else True
        mouse_click(act.get("mouse_button", "left"), hold, pos=pos,
                    restore=restore, stop_event=worker.stop_event)


# ---------------------------------------------------------------------------
# 高级脚本 API (把 DSL 内置函数接到真实输入)
# ---------------------------------------------------------------------------
class ScriptAPI(object):
    def __init__(self, worker, cfg, act=None):
        self.worker = worker
        self.cfg = cfg
        self.act = act or {}
        self.deadline = None
        self.limit = 0
        mode = self.act.get("stop_mode", "infinite")
        if mode == "duration":
            self.deadline = time.time() + max(1, _i(self.act.get("stop_duration_ms"), 1000)) / 1000.0
        elif mode == "count":
            self.limit = max(1, _i(self.act.get("stop_count"), 1))

    def _guard(self):
        if self.worker.stop_event.is_set():
            raise ScriptStopped()
        if self.deadline and time.time() >= self.deadline:
            self.worker.stop_all()
            raise ScriptStopped()
        if self.limit and self.worker.count >= self.limit:
            self.worker.stop_all()
            raise ScriptStopped()

    def mouse(self):
        self._guard()
        if CLICK_SINK is not None:
            return Point(0, 0)
        return Point(*get_cursor_pos())

    def click(self, button, *args):
        self._guard()
        pos = None
        if len(args) >= 2:
            pos = (int(args[0]), int(args[1]))
        elif len(args) == 1 and isinstance(args[0], Point):
            pos = (args[0].x, args[0].y)
        if CLICK_SINK is not None:
            CLICK_SINK(("script_click", button, pos))
        else:
            restore = bool(self.cfg.settings.get("restore_cursor", True)) if self.cfg else True
            mouse_click(button, 0, pos=pos, restore=restore,
                        stop_event=self.worker.stop_event)
        self.worker.tick()

    def key(self, spec):
        self._guard()
        combo = parse_key_spec(spec)
        if CLICK_SINK is not None:
            CLICK_SINK(("script_key", combo))
        else:
            keyboard_tap(combo)
        self.worker.tick()

    def pause(self, ms):
        self._guard()
        if interruptible_wait(max(0.0, float(ms)) / 1000.0, self.worker.stop_event):
            raise ScriptStopped()
        self._guard()


def run_script_action(act, cfg, worker):
    """把一段伪代码跑起来; 语法错误抛给上层记录。"""
    src = str(act.get("script") or "")
    if not src.strip():
        return
    if cfg is not None and not cfg.settings.get("script_enabled", False):
        raise ScriptError("高级脚本未启用, 请在「设置 → 高级」中开启")
    program = compile_script(src)
    api = ScriptAPI(worker, cfg, act)
    run_script(program, api, should_stop=lambda: worker.stop_event.is_set())


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------
class ActionRunner(threading.Thread):
    """基础点击: 一个动作一条线程, 同刻启动, 各自独立节奏 / 坐标 / 结束条件。"""

    def __init__(self, act, index, worker, cfg):
        threading.Thread.__init__(self)
        self.daemon = True
        self.name = "ActionRunner-%d" % index
        self.act = act or {}
        self.worker = worker
        self.cfg = cfg

    def run(self):
        w, act = self.worker, self.act
        mode = act.get("stop_mode") or "infinite"
        if mode not in ("infinite", "count", "duration"):
            mode = "infinite"
        limit_n = _i(act.get("stop_count"), 0)
        limit_ms = _i(act.get("stop_duration_ms"), 0)
        t0 = time.time()
        done = 0
        try:
            while not w.stop_event.is_set():
                if mode == "duration" and (time.time() - t0) * 1000.0 >= limit_ms:
                    w.stop_all()
                    break
                perform_action(act, self.cfg, w)
                done += 1
                if w.tick():
                    break
                if mode == "count" and done >= limit_n:
                    w.stop_all()
                    break
                if act.get("rate_mode") == "window":
                    # 单位时间内随机 n~m 次: 把 k 次点击散布到窗口内的随机时刻
                    win = max(1, _i(act.get("window_ms"), 1000)) / 1000.0
                    lo = _i(act.get("min_n"), 3)
                    hi = max(lo, _i(act.get("max_n"), 8))
                    k = random.randint(lo, hi)
                    stamps = sorted(random.random() * win for _ in range(max(0, k - 1)))
                    prev = 0.0
                    for ts in stamps:
                        if w.stop_event.wait(max(0.001, ts - prev)):
                            return
                        prev = ts
                        if mode == "duration" and (time.time() - t0) * 1000.0 >= limit_ms:
                            w.stop_all()
                            return
                        perform_action(act, self.cfg, w)
                        done += 1
                        if w.tick():
                            return
                        if mode == "count" and done >= limit_n:
                            w.stop_all()
                            return
                    if w.stop_event.wait(max(0.001, win - prev)):
                        return
                else:
                    if w.stop_event.wait(action_delay(act)):
                        break
        except ScriptError as e:
            w.report_error("脚本错误: %s" % e)
            w.stop_all()
        except ScriptStopped:
            pass
        except Exception:
            pass


class FlowRunner(object):
    """节点循环: k 个节点顺序执行 n 轮, 每节点有自己的点击次数/频率/间隔。"""

    def __init__(self, worker, cfg):
        self.worker = worker
        self.cfg = cfg

    def run(self):
        w = self.worker
        flow = w.binding
        nodes = flow.get("nodes") or []
        rounds = _i(flow.get("rounds"), 0)
        round_gap = _i(flow.get("round_interval_ms"), 0) / 1000.0
        mode = flow.get("stop_mode") or "infinite"
        if mode not in ("infinite", "count", "duration"):
            mode = "infinite"
        limit_n = _i(flow.get("stop_count"), 0)
        limit_ms = _i(flow.get("stop_duration_ms"), 0)
        t0 = time.time()
        r = 0
        try:
            while not w.stop_event.is_set() and (rounds == 0 or r < rounds):
                for node in nodes:
                    if w.stop_event.is_set():
                        return
                    if mode == "duration" and (time.time() - t0) * 1000.0 >= limit_ms:
                        w.stop_all()
                        return
                    act = node.get("action") or default_action()
                    cnt = max(1, _i(node.get("click_count"), 1))
                    round_ms = _i(node.get("round_duration_ms"), 0)
                    node_t0 = time.time()
                    for i in range(cnt):
                        if w.stop_event.is_set():
                            return
                        if round_ms and (time.time() - node_t0) * 1000.0 >= round_ms:
                            break                      # 单轮时长到, 结束本节点进入下一个
                        perform_action(act, self.cfg, w, hold_override=node.get("hold_ms"))
                        if w.tick() or (mode == "count" and w.count >= limit_n):
                            w.stop_all()
                            return
                        if round_ms and (time.time() - node_t0) * 1000.0 >= round_ms:
                            break
                        if i < cnt - 1 and w.stop_event.wait(node_delay(node)):
                            return
                    gap = _i(node.get("node_interval_ms"), 0) / 1000.0
                    if gap > 0 and w.stop_event.wait(gap):
                        return
                r += 1
                if mode == "duration" and (time.time() - t0) * 1000.0 >= limit_ms:
                    w.stop_all()
                    return
                if round_gap > 0 and w.stop_event.wait(round_gap):
                    return
        except ScriptError as e:
            w.report_error("脚本错误: %s" % e)
            w.stop_all()
        except ScriptStopped:
            pass
        except Exception:
            pass


class ClickWorker(threading.Thread):
    """一个启动单元: 基础点击(并行动作) 或 节点循环(顺序状态机)。"""

    def __init__(self, kind, item, cfg, on_stop=None, on_error=None):
        threading.Thread.__init__(self)
        self.daemon = True
        self.name = "ClickWorker-%s" % item["id"]
        self.kind = kind
        self.binding = item
        self.cfg = cfg
        self.on_stop = on_stop
        self.on_error = on_error
        self.stop_event = threading.Event()
        self.count = 0
        self._count_lock = threading.Lock()
        if kind == "flow":
            self._total = _i(item.get("stop_count"), 0) if item.get("stop_mode") == "count" else 0
        else:
            self._total = _i(item.get("repeat"), 0)
        self._runners = []

    def tick(self):
        with self._count_lock:
            self.count += 1
            if self._total and self.count >= self._total:
                self.stop_event.set()
                return True
        return False

    def stop_all(self):
        self.stop_event.set()

    def stop(self):
        self.stop_event.set()

    def report_error(self, msg):
        if self.on_error:
            try:
                self.on_error(msg)
            except Exception:
                pass

    def run(self):
        try:
            if self.kind == "flow":
                FlowRunner(self, self.cfg).run()
            else:
                acts = self.binding.get("actions") or []
                if not acts:
                    self._fire_stop()
                    return
                self._runners = [ActionRunner(a, i, self, self.cfg)
                                 for i, a in enumerate(acts)]
                for r in self._runners:
                    r.start()
                for r in self._runners:
                    r.join()
        finally:
            self._fire_stop()

    def _fire_stop(self):
        if self.on_stop:
            try:
                self.on_stop(self, self.binding["id"])
            except Exception:
                pass


class Engine(object):
    def __init__(self, config):
        global ENGINE
        ENGINE = self
        self.config = config
        self.hotkeys = HotkeyManager()
        self.watcher = KeyWatcher()
        self.watcher.start()
        self._workers = {}
        self._last_count = {}
        self._lock = threading.RLock()
        self.on_change = lambda: None
        self.on_error = lambda msg: None
        self.errors = []

    def is_running(self, iid):
        with self._lock:
            w = self._workers.get(iid)
            return bool(w and w.is_alive())

    def click_count(self, iid):
        with self._lock:
            w = self._workers.get(iid)
            if w is not None:
                return w.count
            return int(self._last_count.get(iid, 0))

    def total_clicks(self):
        with self._lock:
            return sum(w.count for w in self._workers.values()) + \
                   sum(v for k, v in self._last_count.items() if k not in self._workers)

    def running_ids(self):
        with self._lock:
            return [k for k, v in self._workers.items() if v.is_alive()]

    def start(self, iid):
        kind, item = self.config.item_by_id(iid)
        if not item:
            return False
        with self._lock:
            old = self._workers.get(iid)
            if old and old.is_alive():
                return True
            self._last_count.pop(iid, None)
            w = ClickWorker(kind, item, self.config,
                            on_stop=self._worker_finished, on_error=self._on_error)
            self._workers[iid] = w
            w.start()
        self.on_change()
        return True

    def stop(self, iid):
        with self._lock:
            w = self._workers.pop(iid, None)
            if w is not None:
                self._last_count[iid] = w.count
        if w:
            w.stop()
        self.on_change()

    def toggle(self, iid):
        if self.is_running(iid):
            self.stop(iid)
        else:
            self.start(iid)

    def stop_all(self):
        with self._lock:
            workers = list(self._workers.items())
            for iid, w in workers:
                self._last_count[iid] = w.count
            self._workers.clear()
        for _iid, w in workers:
            w.stop()
        self.on_change()

    def _worker_finished(self, w, iid):
        with self._lock:
            if self._workers.get(iid) is w:
                self._workers.pop(iid, None)
            self._last_count[iid] = w.count
        self.on_change()

    def _on_error(self, msg):
        self.errors.append(str(msg))
        try:
            self.on_error(msg)
        except Exception:
            pass

    def _mk_toggle(self, iid):
        def _cb():
            self.toggle(iid)
        return _cb

    def _mk_emergency(self):
        def _cb():
            self.stop_all()
        return _cb

    def rebuild_hotkeys(self):
        combo_specs, multi_specs = [], []
        for _kind, item in self.config.items():
            if not item.get("enabled", True):
                continue
            ht = item.get("hotkey_type", "combo")
            cb = self._mk_toggle(item["id"])
            if ht == "combo":
                combo = item.get("hotkey")
                if combo and _i(combo.get("vk"), 0):
                    combo_specs.append(("bind:" + item["id"], combo, cb))
            elif ht == "multi":
                keys = [_i(k, 0) for k in item.get("hotkey_keys", []) if _i(k, 0) > 0]
                if keys:
                    multi_specs.append(("bind:" + item["id"], keys, cb))

        em = self.config.settings.get("emergency_hotkey")
        stop_cb = self._mk_emergency()
        if em and _i(em.get("vk"), 0):
            combo_specs.append(("emergency", em, stop_cb))
        if self.config.settings.get("panic_fallback", True):
            fb = {"mods": ["ctrl", "alt"], "vk": 0x7B}
            if combo_key(em) != combo_key(fb):
                combo_specs.append(("panic_fallback", fb, stop_cb))
            mfb = [0x11, 0x12, 0x7B]
            if not any(set(k) == set(mfb) for _r, k, _c in multi_specs):
                multi_specs.append(("panic_fallback_multi", mfb, stop_cb))

        seen, unique = set(), []
        for ref, combo, cb in combo_specs:
            k = combo_key(combo)
            if k in seen:
                continue
            seen.add(k)
            unique.append((ref, combo, cb))
        self.hotkeys.update(unique)
        self.watcher.update(multi_specs)

    def suspend(self):
        self.hotkeys.update([])
        self.watcher.set_enabled(False)

    def resume(self):
        self.watcher.set_enabled(True)
        self.rebuild_hotkeys()

    def shutdown(self):
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        try:
            self.watcher.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 键位录制
# ---------------------------------------------------------------------------
_PKB_MODS = {}
if _pkb is not None:
    _PKB_MODS = {
        _pkb.Key.ctrl: "ctrl", _pkb.Key.ctrl_l: "ctrl", _pkb.Key.ctrl_r: "ctrl",
        _pkb.Key.alt: "alt", _pkb.Key.alt_l: "alt", _pkb.Key.alt_r: "alt",
        _pkb.Key.alt_gr: "alt", _pkb.Key.shift: "shift",
        _pkb.Key.shift_l: "shift", _pkb.Key.shift_r: "shift",
        _pkb.Key.cmd: "win", _pkb.Key.cmd_l: "win", _pkb.Key.cmd_r: "win",
    }

ENGINE = None


class Recorder(object):
    """录制一个经典组合键(修饰键 + 主键)。"""

    def __init__(self, on_update):
        self.on_update = on_update
        self.mods = set()
        self.combo = None
        self._listener = None

    def start(self):
        if ENGINE:
            ENGINE.suspend()
        if _pkb is None:
            return
        self._listener = _pkb.Listener(on_press=self._press, on_release=self._release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        if ENGINE:
            ENGINE.resume()

    def clear(self):
        self.mods.clear()
        self.combo = None
        self.on_update(None)

    def _press(self, key):
        if _pkb is not None and key == _pkb.Key.alt_gr:
            self.mods.update(("ctrl", "alt"))
            self.on_update(self._build(None))
            return
        name = _PKB_MODS.get(key)
        if name:
            self.mods.add(name)
            self.on_update(self._build(None))
            return
        vk = _pkb_vk(key)
        if vk:
            self.combo = self._build(vk)
            self.on_update(self.combo)

    def _release(self, key):
        if _pkb is not None and key == _pkb.Key.alt_gr:
            self.mods.discard("ctrl")
            self.mods.discard("alt")
            return
        name = _PKB_MODS.get(key)
        if name:
            self.mods.discard(name)

    def _build(self, vk):
        return {"mods": [m for m in MODS_ORDER if m in self.mods],
                "vk": int(vk) if vk else 0}


class MultiKeyRecorder(object):
    """录制最多 5 个任意按键(同时按下, 用低层钩子看 VK 集合)。"""

    def __init__(self, on_update, limit=5):
        self.on_update = on_update
        self.limit = limit
        self.keys = []
        self.pressed = set()
        self._listener = None

    def start(self):
        if ENGINE:
            ENGINE.suspend()
        if _pkb is None:
            return
        self._listener = _pkb.Listener(on_press=self._press, on_release=self._release)
        self._listener.daemon = True
        self._listener.start()

    def stop(self):
        if self._listener:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        if ENGINE:
            ENGINE.resume()

    def clear(self):
        self.keys = []
        self.on_update([])

    def _press(self, key):
        vk = _pkb_vk(key)
        if not vk:
            return
        self.pressed.add(vk)
        if vk not in self.keys and len(self.keys) < self.limit:
            self.keys.append(vk)
            self.on_update(list(self.keys))

    def _release(self, key):
        vk = _pkb_vk(key)
        if vk:
            self.pressed.discard(vk)


# ---------------------------------------------------------------------------
# 托盘图标
# ---------------------------------------------------------------------------
def make_tray_image(state="idle"):
    from PIL import Image, ImageDraw
    color = {"idle": (37, 99, 235, 255), "run": (22, 163, 74, 255)}.get(state, (37, 99, 235, 255))
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=14, fill=color)
    d.ellipse([22, 12, 42, 50], fill=(255, 255, 255, 255))
    d.line([32, 13, 32, 27], fill=color, width=2)
    d.ellipse([30, 17, 34, 23], fill=color)
    d.arc([12, 26, 24, 38], start=60, end=300, fill=(255, 255, 255, 255), width=3)
    d.arc([40, 26, 52, 38], start=240, end=120, fill=(255, 255, 255, 255), width=3)
    return img


def make_ico(path):
    from PIL import Image, ImageDraw
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 6, size - 7, size - 7], radius=54, fill=(37, 99, 235, 255))
    d.ellipse([88, 48, 168, 200], fill=(255, 255, 255, 255))
    d.line([128, 52, 128, 108], fill=(37, 99, 235, 255), width=8)
    d.ellipse([120, 68, 136, 92], fill=(37, 99, 235, 255))
    d.arc([48, 104, 96, 152], start=60, end=300, fill=(37, 99, 235, 255), width=10)
    d.arc([160, 104, 208, 152], start=240, end=120, fill=(37, 99, 235, 255), width=10)
    img.save(path, format="ICO", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])


# ---------------------------------------------------------------------------
# 主题
# ---------------------------------------------------------------------------
ACCENTS = {
    "blue": ("#2563eb", "经典蓝"),
    "violet": ("#7c3aed", "紫罗兰"),
    "teal": ("#0d9488", "青绿"),
    "rose": ("#e11d48", "玫红"),
    "amber": ("#d97706", "琥珀"),
}

THEMES = {
    "light": {
        "BG": "#f4f6f9", "CARD": "#ffffff", "CARD2": "#f8fafc",
        "TEXT": "#1f2937", "MUTED": "#6b7280",
        "ACCENT": "#2563eb", "DANGER": "#dc2626", "HEAD": "#eef2f7", "SEL": "#dbeafe",
        "FIELD": "#ffffff", "BORDER": "#e2e8f0", "RING": "#cbd5e1",
        "OK": "#16a34a", "WARN": "#d97706", "GHOST": "#eef4ff",
    },
    "dark": {
        # v1.5 重做: 更深的中性底 + 分层卡片, 不再是亮色的简单反色
        "BG": "#11141a", "CARD": "#1b2029", "CARD2": "#232a35",
        "TEXT": "#e8edf6", "MUTED": "#94a1b5",
        "ACCENT": "#5b93ff", "DANGER": "#f87171", "HEAD": "#262d39", "SEL": "#2c3a55",
        "FIELD": "#1d232d", "BORDER": "#323b49", "RING": "#3b4657",
        "OK": "#4ade80", "WARN": "#fbbf24", "GHOST": "#263146",
    },
}
_c = THEMES["light"]
BG, CARD, CARD2, TEXT, MUTED = _c["BG"], _c["CARD"], _c["CARD2"], _c["TEXT"], _c["MUTED"]
ACCENT, DANGER, HEAD, SEL = _c["ACCENT"], _c["DANGER"], _c["HEAD"], _c["SEL"]
FIELD, BORDER, RING = _c["FIELD"], _c["BORDER"], _c["RING"]
OK, WARN, GHOST = _c["OK"], _c["WARN"], _c["GHOST"]
CURRENT_THEME = "light"
CURRENT_ACCENT = "blue"
CURRENT_DENSITY = "comfortable"
CURRENT_RADIUS = 10
ANIMATIONS = True
BADGES = True


def apply_personal(settings):
    """把个性化设置同步到模块级状态与自绘表格的行高。"""
    global CURRENT_DENSITY, CURRENT_RADIUS, ANIMATIONS, BADGES
    CURRENT_DENSITY = settings.get("density") if settings.get("density") in ("compact", "comfortable") else "comfortable"
    CURRENT_RADIUS = min(18, max(0, _i(settings.get("radius"), 10)))
    ANIMATIONS = bool(settings.get("animations", True))
    BADGES = bool(settings.get("badges", True))
    compact = (CURRENT_DENSITY == "compact")
    GlassTable.ROW_H = 24 if compact else 30
    GlassTable.HEAD_H = 26 if compact else 32


def _hex_rgb(h):
    h = (h or "#000000").lstrip("#")
    if len(h) != 6:
        h = "000000"
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def _mix_hex(c1, c2, ratio=0.5):
    a, b = _hex_rgb(c1), _hex_rgb(c2)
    return _rgb_hex(tuple(a[i] * ratio + b[i] * (1 - ratio) for i in range(3)))


def set_theme(name, accent=None):
    """应用主题 + 个性化强调色; accent=None 时沿用当前选择。"""
    global _c, BG, CARD, CARD2, TEXT, MUTED, ACCENT, DANGER, HEAD, SEL
    global FIELD, BORDER, RING, OK, WARN, GHOST, CURRENT_THEME, CURRENT_ACCENT
    CURRENT_THEME = name if name in THEMES else "light"
    if accent in ACCENTS:
        CURRENT_ACCENT = accent
    _c = THEMES[CURRENT_THEME]
    BG, CARD, CARD2 = _c["BG"], _c["CARD"], _c["CARD2"]
    TEXT, MUTED = _c["TEXT"], _c["MUTED"]
    DANGER, HEAD = _c["DANGER"], _c["HEAD"]
    FIELD, BORDER, RING = _c["FIELD"], _c["BORDER"], _c["RING"]
    OK, WARN = _c["OK"], _c["WARN"]
    base = ACCENTS[CURRENT_ACCENT][0]
    # 暗色下把强调色提亮, 保证在深色卡片上的对比度
    ACCENT = _mix_hex(base, "#ffffff", 0.75) if CURRENT_THEME == "dark" else base
    SEL = _mix_hex(ACCENT, CARD, 0.18)
    GHOST = _mix_hex(ACCENT, CARD, 0.07)
    return CURRENT_THEME


def apply_theme(root):
    st = ttk.Style()
    try:
        st.theme_use("clam")
    except Exception:
        pass
    st.configure(".", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 9),
                 fieldbackground=FIELD, troughcolor=BG, bordercolor=BORDER)
    st.configure("TFrame", background=BG)
    st.configure("TLabel", background=BG, foreground=TEXT)
    st.configure("Card.TFrame", background=CARD, relief="flat")
    st.configure("Card2.TFrame", background=CARD2, relief="flat")
    st.configure("TLabelframe", background=BG, bordercolor=RING)
    st.configure("TLabelframe.Label", background=BG, foreground=TEXT)
    # v1.5: 字号层级 / 提示文字 / 徽标样式
    st.configure("Title.TLabel", background=BG, foreground=TEXT,
                 font=("Microsoft YaHei UI", 16, "bold"))
    st.configure("Section.TLabel", background=BG, foreground=TEXT,
                 font=("Microsoft YaHei UI", 10, "bold"))
    st.configure("Hint.TLabel", background=BG, foreground=MUTED,
                 font=("Microsoft YaHei UI", 9))
    st.configure("Badge.TLabel", background=CARD2, foreground=TEXT,
                 font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 3))
    compact = (CURRENT_DENSITY == "compact")
    st.configure("TButton", padding=(8, 4) if compact else (11, 7),
                 background=CARD, foreground=TEXT,
                 bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD)
    st.map("TButton", background=[("active", GHOST), ("disabled", BG)],
           foreground=[("disabled", MUTED)])
    st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                 borderwidth=0, padding=(12, 5) if compact else (14, 8))
    st.map("Accent.TButton", background=[("active", _mix_hex(ACCENT, "#ffffff", 0.15))])
    st.configure("Danger.TButton", background=DANGER, foreground="#ffffff",
                 borderwidth=0, padding=(12, 5) if compact else (14, 8))
    st.map("Danger.TButton", background=[("active", DANGER)])
    st.configure("TCheckbutton", background=BG, foreground=TEXT, indicatorcolor=FIELD)
    st.map("TCheckbutton", background=[("active", BG)], indicatorcolor=[("selected", ACCENT)])
    st.configure("TRadiobutton", background=BG, foreground=TEXT, indicatorcolor=FIELD)
    st.map("TRadiobutton", background=[("active", BG)], indicatorcolor=[("selected", ACCENT)])
    st.configure("TEntry", fieldbackground=FIELD, foreground=TEXT,
                 bordercolor=RING, lightcolor=RING, darkcolor=RING,
                 insertcolor=ACCENT, padding=(6, 3))
    st.configure("TSpinbox", fieldbackground=FIELD, foreground=TEXT,
                 bordercolor=RING, background=FIELD, arrowcolor=TEXT,
                 lightcolor=RING, darkcolor=RING)
    st.configure("TCombobox", fieldbackground=FIELD, foreground=TEXT,
                 background=FIELD, bordercolor=RING, arrowcolor=TEXT,
                 lightcolor=RING, darkcolor=RING, padding=(6, 3))
    st.configure("TNotebook", background=BG, bordercolor=BORDER)
    st.configure("TNotebook.Tab", background=HEAD, foreground=TEXT,
                 padding=(12, 5) if compact else (14, 7))
    st.map("TNotebook.Tab", background=[("selected", CARD)])
    st.configure("TSeparator", background=BORDER)
    st.configure("TScrollbar", background=CARD2, troughcolor=BG, bordercolor=BORDER,
                 arrowcolor=MUTED)
    st.configure("Treeview", background=CARD, fieldbackground=CARD,
                 foreground=TEXT, rowheight=24 if compact else 28,
                 borderwidth=0, bordercolor=BORDER)
    st.configure("Treeview.Heading", background=HEAD, foreground=TEXT,
                 font=("Microsoft YaHei UI", 9, "bold"), bordercolor=BORDER)
    st.map("Treeview", background=[("selected", SEL)], foreground=[("selected", TEXT)])
    st.configure("Link.TLabel", background=BG, foreground=ACCENT,
                 font=("Microsoft YaHei UI", 9, "underline"))
    try:
        root.option_add("*TCombobox*Listbox.background", FIELD)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", SEL)
        root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    except Exception:
        pass
    try:
        root.configure(bg=BG)
        for w in root.winfo_children():
            if isinstance(w, (tk.Toplevel, tk.Tk)):
                w.configure(bg=BG)
    except Exception:
        pass


def _rgba(hex_color, alpha):
    """把 #rrggbb 转成 PIL 用的 (r,g,b,a)。"""
    return _hex_rgb(hex_color) + (max(0, min(255, int(alpha))),)


def _round_rect(draw, box, radius, fill):
    """圆角矩形; 兼容没有 rounded_rectangle 的老 Pillow。"""
    if radius <= 0:
        draw.rectangle(box, fill=fill)
        return
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill)
        return
    except (TypeError, AttributeError):
        pass
    x0, y0, x1, y1 = box
    r = max(0, min(int(radius), (x1 - x0) // 2, (y1 - y0) // 2))
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=fill)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=fill)
    draw.ellipse([x0, y0, x0 + 2 * r, y0 + 2 * r], fill=fill)
    draw.ellipse([x1 - 2 * r, y0, x1, y0 + 2 * r], fill=fill)
    draw.ellipse([x0, y1 - 2 * r, x0 + 2 * r, y1], fill=fill)
    draw.ellipse([x1 - 2 * r, y1 - 2 * r, x1, y1], fill=fill)


def _round_pill(cv, x0, y0, x1, y1, r, fill):
    """tk.Canvas 上画纯色圆角胶囊(4 个圆 + 2 个矩形)。"""
    r = max(0, min(int(r), (x1 - x0) // 2, (y1 - y0) // 2))
    if r <= 0:
        cv.create_rectangle(x0, y0, x1, y1, fill=fill, outline=fill)
        return
    cv.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline=fill)
    cv.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, outline=fill)
    for ax, ay in ((x0, y0), (x1 - 2 * r, y0),
                   (x0, y1 - 2 * r), (x1 - 2 * r, y1 - 2 * r)):
        cv.create_oval(ax, ay, ax + 2 * r, ay + 2 * r, fill=fill, outline=fill)


class GlassTable(tk.Frame):
    """Canvas 自绘表格 —— 替掉 ttk.Treeview, 让表格区也能透出背景图。

    ttk.Treeview 的 field / heading 由 clam 引擎强制自绘不透明底色, 样式、
    色键透明、st.layout() 都无法改变。这里改为: 用 PIL 把「背景图裁片 +
    半透明表头/行带/选中高亮」合成为一张底图贴在 Canvas 上, 文字用
    create_text 浮在图上 —— 背景纹理完整可见, 同时保证文字可读。

    对外保持 ttk.Treeview 的常用 API(heading/column/insert/item/delete/
    exists/get_children/selection/selection_set/tag_configure/yview/bind),
    因此调用方几乎无需改动。
    """

    HEAD_H = 30
    ROW_H = 28
    _MOUSE_SEQ = ("<Button", "<Double", "<Triple", "<Motion", "<Enter",
                  "<Leave", "<MouseWheel", "<B1", "<B2", "<B3")

    def __init__(self, parent, columns=(), show="headings", selectmode="browse",
                 height=6, style=None, glass_provider=None, **kw):
        kw.pop("style", None)
        tk.Frame.__init__(self, parent, bd=0, highlightthickness=0, bg=BG, **kw)
        self._app = glass_provider
        self._cols = list(columns)
        self._heads, self._widths, self._anchors = {}, {}, {}
        self._rows, self._index, self._tags = [], {}, {}
        self._auto = 0
        self._sel = None
        self._offset = 0.0
        self._yscroll_cb = None
        self._pending = False
        self._bg_photo = None
        self._hover = -1
        self._font = tkfont.Font(family="Microsoft YaHei UI", size=9)
        self._head_font = tkfont.Font(family="Microsoft YaHei UI", size=9, weight="bold")
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=CARD)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Configure>", lambda e: self._schedule())

    # ---- ttk.Treeview 兼容 API ----
    def configure(self, cnf=None, **kw):
        if "yscrollcommand" in kw:
            self._yscroll_cb = kw.pop("yscrollcommand")
        if cnf is None:
            return tk.Frame.configure(self, **kw)
        return tk.Frame.configure(self, cnf, **kw)

    config = configure

    def heading(self, col, text=None, **kw):
        if text is not None:
            self._heads[col] = text
        self._schedule()
        return ""

    def column(self, col, width=None, anchor=None, **kw):
        if width is not None:
            self._widths[col] = max(24, int(width))
        if anchor is not None:
            self._anchors[col] = anchor
        self._schedule()
        return ""

    def tag_configure(self, tag, foreground=None, **kw):
        if foreground is not None:
            self._tags.setdefault(tag, {})["foreground"] = foreground
        self._schedule()
        return ""

    def insert(self, parent="", index="end", iid=None, values=(), tags=()):
        if iid is None:
            self._auto += 1
            iid = "I%d" % self._auto
        iid = str(iid)
        if isinstance(tags, str):
            tags = (tags,)
        row = {"iid": iid, "values": tuple(values), "tags": tuple(tags)}
        if iid in self._index:
            self._rows[self._index[iid]] = row
        else:
            self._index[iid] = len(self._rows)
            self._rows.append(row)
        self._schedule()
        return iid

    def delete(self, *iids):
        gone = {str(i) for i in iids}
        self._rows = [r for r in self._rows if r["iid"] not in gone]
        self._reindex()
        if self._sel in gone:
            self._sel = None
        self._schedule()

    def item(self, iid, values=None, tags=None, **kw):
        iid = str(iid)
        idx = self._index.get(iid)
        if idx is None:
            return {}
        row = self._rows[idx]
        if isinstance(tags, str):
            tags = (tags,)
        new_tags = tuple(tags) if tags is not None else row["tags"]
        new_vals = tuple(values) if values is not None else row["values"]
        if new_vals == row["values"] and new_tags == row["tags"]:
            return {}          # 250ms 轮询刷新时, 值没变就不重绘
        row["values"], row["tags"] = new_vals, new_tags
        self._schedule()
        return {}

    def exists(self, iid):
        return str(iid) in self._index

    def get_children(self, parent=""):
        return [r["iid"] for r in self._rows]

    def selection(self):
        return (self._sel,) if self._sel else ()

    def selection_set(self, *iids):
        self._sel = str(iids[0]) if iids else None
        self._schedule()

    def bind(self, sequence=None, func=None, add=None):
        if sequence and any(sequence.startswith(p) for p in self._MOUSE_SEQ):
            return self.canvas.bind(sequence, func, add)
        return tk.Frame.bind(self, sequence, func, add)

    def yview(self, *args):
        view = max(1, self.canvas.winfo_height() - self.HEAD_H)
        total = len(self._rows) * self.ROW_H
        maxoff = max(0, total - view)
        if args:
            if args[0] == "moveto":
                self._offset = float(args[1]) * maxoff
            elif args[0] == "scroll":
                n = int(float(args[1]))
                unit = args[2] if len(args) > 2 else "units"
                step = self.ROW_H if unit == "units" else view
                self._offset += n * step
            self._offset = max(0.0, min(float(maxoff), self._offset))
            self._update_scrollbar()
            self._redraw()
            return None
        if total <= view:
            return (0.0, 1.0)
        return (self._offset / float(total), min(1.0, (self._offset + view) / float(total)))

    def redraw_now(self):
        """外部(背景/主题/尺寸变化)强制重绘。"""
        self._pending = False
        try:
            self._redraw()
        except Exception:
            pass

    # ---- 内部 ----
    def _reindex(self):
        self._index = {r["iid"]: i for i, r in enumerate(self._rows)}

    def _schedule(self):
        if self._pending:
            return
        self._pending = True
        try:
            self.after_idle(self._flush)
        except Exception:
            self._flush()

    def _flush(self):
        self._pending = False
        try:
            self._redraw()
        except Exception:
            pass

    def _update_scrollbar(self):
        if self._yscroll_cb is None:
            return
        try:
            a, b = self.yview()
            self._yscroll_cb(a, b)
        except Exception:
            pass

    def _on_click(self, event):
        if event.y < self.HEAD_H:
            self._sel = None
        else:
            idx = int((event.y - self.HEAD_H + self._offset) // self.ROW_H)
            self._sel = self._rows[idx]["iid"] if 0 <= idx < len(self._rows) else None
        self._schedule()

    def _on_wheel(self, event):
        n = -int(event.delta / 120.0) if abs(event.delta) >= 120 else (-1 if event.delta > 0 else 1)
        self.yview("scroll", n * 3, "units")
        return "break"

    def _on_motion(self, event):
        if event.y < self.HEAD_H:
            idx = -1
        else:
            idx = int((event.y - self.HEAD_H + self._offset) // self.ROW_H)
            if not (0 <= idx < len(self._rows)):
                idx = -1
        if idx != self._hover:
            self._hover = idx
            self._schedule()

    def _on_leave(self, event):
        if self._hover != -1:
            self._hover = -1
            self._schedule()

    def _glass_base(self, w, h):
        """返回 (是否半透明模式, RGB 底图)。半透明时从整窗磨砂图裁出本控件对应区域。"""
        from PIL import Image
        app = self._app
        if app is not None and getattr(app, "_glass_active", False) \
                and getattr(app, "_frosted_image", None) is not None:
            try:
                rx, ry = app.root.winfo_rootx(), app.root.winfo_rooty()
                x = self.canvas.winfo_rootx() - rx
                y = self.canvas.winfo_rooty() - ry
                img = app._frosted_image
                base = Image.new("RGB", (w, h), _hex_rgb(CARD))
                ix0, iy0 = max(0, x), max(0, y)
                ix1, iy1 = min(img.width, x + w), min(img.height, y + h)
                if ix1 > ix0 and iy1 > iy0:
                    base.paste(img.crop((ix0, iy0, ix1, iy1)), (ix0 - x, iy0 - y))
                return True, base
            except Exception:
                pass
        return False, Image.new("RGB", (w, h), _hex_rgb(CARD))

    def _clip(self, text, width):
        if width <= 10:
            return ""
        if self._font.measure(text) <= width:
            return text
        while text and self._font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def _redraw(self):
        from PIL import Image, ImageDraw, ImageTk
        cv = self.canvas
        w, h = max(1, cv.winfo_width()), max(1, cv.winfo_height())
        cv.delete("all")
        if w <= 2 or h <= 2:
            return
        cols = self._cols
        if not cols:
            return
        minw = [self._widths.get(c, 80) for c in cols]
        total = sum(minw) or 1
        if w > total:
            extra = w - total
            widths = [mw + int(extra * mw / float(total)) for mw in minw]
            widths[-1] += w - sum(widths)
        else:
            widths = minw[:]
        glass, base = self._glass_base(w, h)

        ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        rad = CURRENT_RADIUS
        # 表头: 圆角胶囊, 半透明模式只叠薄薄一层, 背景纹理仍可见
        _round_rect(d, [3, 2, w - 3, self.HEAD_H - 3],
                    min(rad, self.HEAD_H // 2),
                    _rgba(HEAD, 170 if glass else 255))
        x = 0
        for ci in range(len(cols) - 1):
            x += widths[ci]
            d.line([(x, 9), (x, self.HEAD_H - 9)],
                   fill=_rgba(RING, 90 if glass else 170), width=1)
        # 行: 独立圆角条(轻微内缩), 悬浮 / 选中 / 斑马纹
        first = max(0, int(self._offset // self.ROW_H))
        for i in range(first, len(self._rows)):
            yy = int(self.HEAD_H + i * self.ROW_H - self._offset)
            if yy > h:
                break
            if yy + self.ROW_H < self.HEAD_H:
                continue
            box = [4, yy + 1, w - 4, yy + self.ROW_H - 2]
            r = min(rad, 9, max(0, (box[3] - box[1]) // 2))
            selected = self._rows[i]["iid"] == self._sel
            if selected:
                _round_rect(d, box, r,
                            _rgba(SEL if not glass else ACCENT, 115 if glass else 255))
                _round_rect(d, [6, yy + 4, 9, yy + self.ROW_H - 5], 2,
                            _rgba(ACCENT, 255))
            elif i == self._hover:
                _round_rect(d, box, r, _rgba(GHOST, 165 if glass else 255))
            elif i % 2:
                _round_rect(d, box, r, _rgba(CARD2 if glass else HEAD, 70 if glass else 170))
            elif glass:
                _round_rect(d, box, r, _rgba(CARD, 38))
        comp = Image.alpha_composite(base.convert("RGBA"), ov).convert("RGB")
        self._bg_photo = ImageTk.PhotoImage(comp)
        cv.create_image(0, 0, anchor="nw", image=self._bg_photo, tags="__glassbg__")

        # 文字层
        for ci, c in enumerate(cols):
            x0 = sum(widths[:ci])
            anchor = self._anchors.get(c, "w")
            tx = x0 + widths[ci] / 2.0 if anchor == "center" else x0 + 10
            cv.create_text(tx, self.HEAD_H / 2.0, text=self._heads.get(c, c),
                           anchor=anchor, font=self._head_font, fill=TEXT)
        for i in range(first, len(self._rows)):
            yy = int(self.HEAD_H + i * self.ROW_H - self._offset)
            if yy > h:
                break
            if yy + self.ROW_H < self.HEAD_H:
                continue
            row = self._rows[i]
            tag = row["tags"][0] if row["tags"] else ""
            color = self._tags.get(tag, {}).get("foreground", TEXT)
            if row["iid"] == self._sel and color == MUTED:
                color = TEXT          # 灰色状态文字在高亮行上提亮, 保证可读
            font = self._head_font if row["iid"] == self._sel else self._font
            cy = yy + self.ROW_H / 2.0
            for ci, c in enumerate(cols):
                val = str(row["values"][ci]) if ci < len(row["values"]) else ""
                x0 = sum(widths[:ci])
                anchor = self._anchors.get(c, "w")
                tx = x0 + widths[ci] / 2.0 if anchor == "center" else x0 + 10
                if BADGES and ci == 0 and tag:
                    dot = OK if tag == "running" else (WARN if tag == "waiting" else MUTED)
                    cv.create_oval(x0 + 8, cy - 4, x0 + 16, cy + 4, fill=dot, outline="")
                cv.create_text(tx, cy, text=self._clip(val, widths[ci] - 18),
                               anchor=anchor, font=font, fill=color)
        self._update_scrollbar()


UI_QUEUE = queue.Queue()


def ui_call(fn, *args, **kwargs):
    UI_QUEUE.put((fn, args, kwargs))


def exe_command():
    try:
        if getattr(sys, "frozen", False):
            return '"%s"' % os.path.abspath(sys.executable)
        py = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "pythonw.exe")
        if not os.path.isfile(py):
            py = os.path.abspath(sys.executable)
        return '"%s" "%s"' % (py, os.path.abspath(__file__))
    except Exception:
        return ""


def autostart_get():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ)
        try:
            v, _t = winreg.QueryValueEx(k, APP_NAME)
        finally:
            winreg.CloseKey(k)
        return str(v)
    except Exception:
        return ""


def autostart_set(on, hidden=False):
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            if on:
                cmd = exe_command() + (" --hidden" if hidden else "")
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except Exception:
                    pass
        finally:
            winreg.CloseKey(k)
        return True
    except Exception:
        return False


def list_pictures():
    try:
        os.makedirs(PICTURES_DIR, exist_ok=True)
        names = [n for n in os.listdir(PICTURES_DIR)
                 if n.lower().endswith(IMAGE_EXTS) and os.path.isfile(os.path.join(PICTURES_DIR, n))]
        return sorted(names, key=lambda s: s.lower())
    except Exception:
        return []


def import_picture(src):
    try:
        os.makedirs(PICTURES_DIR, exist_ok=True)
        name = os.path.basename(src)
        dst = os.path.join(PICTURES_DIR, name)
        if os.path.abspath(src) != os.path.abspath(dst):
            base, ext = os.path.splitext(name)
            i = 1
            while os.path.exists(dst):
                dst = os.path.join(PICTURES_DIR, "%s_%d%s" % (base, i, ext))
                i += 1
            shutil.copy2(src, dst)
        return os.path.basename(dst)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 对话框公共件
# ---------------------------------------------------------------------------
def make_dialog(parent, title, resizable=False, padding=16):
    top = tk.Toplevel(parent)
    top.title(title)
    top.configure(bg=BG)
    top.transient(parent)
    top.resizable(resizable, resizable)
    top.grab_set()
    frm = ttk.Frame(top, padding=padding)
    frm.pack(fill="both", expand=True)
    return top, frm


def center_dialog(top, parent, dx=60, dy=40):
    top.update_idletasks()
    try:
        top.geometry("+%d+%d" % (max(0, parent.winfo_rootx() + dx),
                                 max(0, parent.winfo_rooty() + dy)))
    except Exception:
        pass


def close_dialog(top):
    try:
        top.grab_release()
    except Exception:
        pass
    top.destroy()


def spin(parent, var, lo=0, hi=86400000, width=11):
    return ttk.Spinbox(parent, from_=lo, to=hi, textvariable=var, width=width)


def ask_num(var, label, parent, minimum=0):
    try:
        v = int(float(var.get().strip() or 0))
    except Exception:
        messagebox.showwarning("数值无效", "%s 请输入有效数字。" % label, parent=parent)
        return None
    return max(minimum, v)


def _row(frm, row, label, widget, note=None):
    ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
    widget.grid(row=row, column=1, sticky="w", pady=4)
    if note:
        ttk.Label(frm, text=note, foreground=MUTED).grid(row=row, column=2, sticky="w", padx=(6, 0))


class RecordDialog(object):
    """录制经典组合键(修饰键 + 主键)。"""

    def __init__(self, parent, title, initial=None, allow_single=True):
        self.initial, self.result = initial, initial
        self.top, frm = make_dialog(parent, title)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        ttk.Label(frm, text=title, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Label(frm, text="按下需要的组合键(例如 Ctrl+Alt+X), 松开后点「确定」。",
                  foreground=MUTED).pack(anchor="w", pady=(4, 10))
        self.var = tk.StringVar(value=fmt_combo(initial))
        tk.Label(frm, textvariable=self.var, font=("Consolas", 16, "bold"),
                 bg=CARD, fg=ACCENT, relief="solid", bd=1, width=24, height=2).pack(fill="x")
        self.hint = tk.StringVar(value="等待按键…")
        ttk.Label(frm, textvariable=self.hint, foreground=MUTED).pack(anchor="w", pady=(6, 0))
        if not allow_single:
            ttk.Label(frm, text="建议带一个修饰键, 否则会全局独占该按键。",
                      foreground="#b45309").pack(anchor="w")
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text="清 除", command=self._clear).pack(side="left")
        ttk.Button(bar, text="取 消", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="确 定", command=self._ok, style="Accent.TButton").pack(side="right", padx=(8, 0))
        self.recorder = Recorder(self._on_update)
        self.recorder.start()
        center_dialog(self.top, parent)
        parent.wait_window(self.top)

    def _on_update(self, combo):
        if combo is None:
            self.result = None
            self.var.set("未设置")
            self.hint.set("等待按键…")
            return
        if combo.get("vk"):
            self.result = combo
            self.var.set(fmt_combo(combo))
            self.hint.set("已捕获: " + fmt_combo(combo))
        else:
            self.var.set("+".join(MOD_DISPLAY[m] for m in MODS_ORDER
                                  if m in combo.get("mods", [])) + "+…")

    def _clear(self):
        self.recorder.clear()

    def _ok(self):
        self.recorder.stop()
        close_dialog(self.top)

    def _cancel(self):
        self.recorder.stop()
        self.result = self.initial
        close_dialog(self.top)


class RecordKeysDialog(object):
    """录制最多 5 个任意按键的组合(同时按下)。"""

    def __init__(self, parent, initial=None):
        self.initial, self.result = list(initial or []), list(initial or [])
        self.top, frm = make_dialog(parent, "录制多键组合(最多 5 键)")
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        ttk.Label(frm, text="同时按下想要的按键(最多 5 个)",
                  font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Label(frm, text="例如 A + S + D 同时按下; 与普通热键不同, 不要求修饰键。",
                  foreground=MUTED, wraplength=380, justify="left").pack(anchor="w", pady=(4, 10))
        self.var = tk.StringVar(value=fmt_keys(self.result))
        tk.Label(frm, textvariable=self.var, font=("Consolas", 15, "bold"),
                 bg=CARD, fg=ACCENT, relief="solid", bd=1, width=26, height=2).pack(fill="x")
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text="清 除", command=self._clear).pack(side="left")
        ttk.Button(bar, text="取 消", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="确 定", command=self._ok, style="Accent.TButton").pack(side="right", padx=(8, 0))
        self.recorder = MultiKeyRecorder(self._on_update)
        self.recorder.start()
        center_dialog(self.top, parent)
        parent.wait_window(self.top)

    def _on_update(self, keys):
        self.result = list(keys)
        self.var.set(fmt_keys(keys) if keys else "等待按键…")

    def _clear(self):
        self.recorder.clear()

    def _ok(self):
        self.recorder.stop()
        close_dialog(self.top)

    def _cancel(self):
        self.recorder.stop()
        self.result = self.initial
        close_dialog(self.top)


class CoordPickerDialog(object):
    """实时显示鼠标坐标并拾取。"""

    def __init__(self, parent, cfg, initial=None):
        self.cfg = cfg
        self.result = None
        p = initial or {}
        self.top, frm = make_dialog(parent, "拾取点击坐标")
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        ttk.Label(frm, text="移动鼠标到目标位置, 然后点「拾取当前位置」。",
                  foreground=MUTED, wraplength=400, justify="left").pack(anchor="w")
        self.live = tk.StringVar(value="实时坐标: …")
        tk.Label(frm, textvariable=self.live, font=("Consolas", 14, "bold"),
                 bg=CARD, fg=ACCENT, relief="solid", bd=1, height=2).pack(fill="x", pady=(10, 8))
        ttk.Button(frm, text="拾取当前位置", command=self._grab).pack(anchor="w")
        self.x_var = tk.StringVar(value=str(_i(p.get("x"), 0)))
        self.y_var = tk.StringVar(value=str(_i(p.get("y"), 0)))
        box = ttk.Frame(frm)
        box.pack(fill="x", pady=(12, 0))
        ttk.Label(box, text="X").pack(side="left")
        ttk.Entry(box, textvariable=self.x_var, width=9).pack(side="left", padx=(4, 14))
        ttk.Label(box, text="Y").pack(side="left")
        ttk.Entry(box, textvariable=self.y_var, width=9).pack(side="left", padx=(4, 0))
        self.save_var = tk.BooleanVar(value=False)
        self.name_var = tk.StringVar(value="")
        ttk.Checkbutton(frm, text="同时保存到编号坐标库", variable=self.save_var,
                        command=self._sync).pack(anchor="w", pady=(12, 0))
        self.name_entry = ttk.Entry(frm, textvariable=self.name_var, width=24, state="disabled")
        self.name_entry.pack(anchor="w", pady=(2, 0))
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="确定", command=self._ok, style="Accent.TButton").pack(side="right", padx=(8, 0))
        self._tick()
        center_dialog(self.top, parent)
        parent.wait_window(self.top)

    def _tick(self):
        try:
            if not self.top.winfo_exists():
                return
            x, y = get_cursor_pos()
            self.live.set("实时坐标:  %d , %d" % (x, y))
            self.top.after(80, self._tick)
        except Exception:
            pass

    def _sync(self):
        self.name_entry.configure(state="normal" if self.save_var.get() else "disabled")

    def _grab(self):
        x, y = get_cursor_pos()
        self.x_var.set(str(x))
        self.y_var.set(str(y))

    def _ok(self):
        x = ask_num(self.x_var, "X 坐标", self.top)
        y = ask_num(self.y_var, "Y 坐标", self.top)
        if x is None or y is None:
            return
        coord_no = 0
        if self.save_var.get():
            no = self.cfg.next_coord_no()
            name = self.name_var.get().strip() or ("坐标 %d" % no)
            self.cfg.coords.append(default_coord(no, name, x, y))
            coord_no = no
        self.result = {"x": x, "y": y, "coord_no": coord_no}
        close_dialog(self.top)

    def _cancel(self):
        self.result = None
        close_dialog(self.top)


class ScheduleDialog(object):
    """定时启动配置。"""

    def __init__(self, parent, sched):
        self.result = None
        s = _sanitize_schedule(sched)
        self.top, frm = make_dialog(parent, "定时启动")
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.en_var = tk.BooleanVar(value=s["enabled"])
        self.mode_var = tk.StringVar(value=s["mode"])
        self.delay_var = tk.StringVar(value=str(s["delay_s"]))
        self.at_var = tk.StringVar(value=s["at"])
        ttk.Checkbutton(frm, text="启用定时启动", variable=self.en_var,
                        command=self._sync).pack(anchor="w")
        box = ttk.Frame(frm)
        box.pack(fill="x", pady=(10, 4))
        for text, val in (("倒计时(秒)", "countdown"), ("指定时刻", "clock"), ("每日时刻", "daily")):
            ttk.Radiobutton(box, text=text, value=val, variable=self.mode_var,
                            command=self._sync).pack(side="left", padx=(0, 12))
        self.delay_row = ttk.Frame(frm)
        self.delay_row.pack(fill="x", pady=4)
        ttk.Label(self.delay_row, text="启动倒计时").pack(side="left")
        spin(self.delay_row, self.delay_var, 1, 86400).pack(side="left", padx=(8, 4))
        ttk.Label(self.delay_row, text="秒后自动启动").pack(side="left")
        self.at_row = ttk.Frame(frm)
        self.at_row.pack(fill="x", pady=4)
        ttk.Label(self.at_row, text="时刻 (HH:MM)").pack(side="left")
        ttk.Entry(self.at_row, textvariable=self.at_var, width=10).pack(side="left", padx=(8, 0))
        ttk.Label(frm, text="倒计时从「保存配置 / 程序启动」时开始计时；指定时刻按本机时间触发。",
                  foreground=MUTED, wraplength=400, justify="left").pack(anchor="w", pady=(12, 0))
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="确定", command=self._ok, style="Accent.TButton").pack(side="right", padx=(8, 0))
        self._sync()
        center_dialog(self.top, parent)
        parent.wait_window(self.top)

    def _sync(self):
        on = self.en_var.get()
        mode = self.mode_var.get()
        for w in (self.delay_row, self.at_row):
            for c in w.winfo_children():
                try:
                    c.configure(state="normal" if on else "disabled")
                except Exception:
                    pass
        for c in self.delay_row.winfo_children():
            try:
                c.configure(state="normal" if (on and mode == "countdown") else "disabled")
            except Exception:
                pass
        for c in self.at_row.winfo_children():
            try:
                c.configure(state="normal" if (on and mode in ("clock", "daily")) else "disabled")
            except Exception:
                pass

    def _ok(self):
        delay = ask_num(self.delay_var, "倒计时", self.top, 1) or 1
        at = self.at_var.get().strip() or "09:00"
        self.result = {"enabled": bool(self.en_var.get()), "mode": self.mode_var.get(),
                       "delay_s": delay, "at": at, "daily": self.mode_var.get() == "daily"}
        close_dialog(self.top)

    def _cancel(self):
        self.result = None
        close_dialog(self.top)


class ScriptDialog(object):
    """内置类 C 伪代码编辑器。"""

    def __init__(self, parent, code="", title="脚本编辑器", cfg=None):
        self.cfg = cfg
        self.result = None
        self.top, frm = make_dialog(parent, title, resizable=True)
        self.top.geometry("760x580")
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        head = ttk.Frame(frm)
        head.pack(fill="x")
        ttk.Label(head, text="类 C 伪代码 · 无头文件", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        self.hotkey_hint = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.hotkey_hint, foreground=MUTED).pack(side="left", padx=(12, 0))
        wrap = ttk.Frame(frm)
        wrap.pack(fill="both", expand=True, pady=(8, 4))
        self.text = tk.Text(wrap, wrap="none", font=("Consolas", 10), bg=FIELD,
                            fg=TEXT, insertbackground=TEXT, relief="solid", bd=1,
                            undo=True, width=80)
        self.text.insert("1.0", code or SCRIPT_TEMPLATE)
        sy = ttk.Scrollbar(wrap, orient="vertical", command=self.text.yview)
        sx = ttk.Scrollbar(wrap, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self.status, foreground=MUTED,
                  wraplength=700, justify="left").pack(fill="x")
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="插入模板", command=self._template).pack(side="left")
        ttk.Button(bar, text="语法检查", command=self._check).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="语法速查", command=self._guide).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="保存", command=self._ok, style="Accent.TButton").pack(side="right", padx=(8, 0))
        frm.columnconfigure(0, weight=1)
        center_dialog(self.top, parent, 30, 20)
        parent.wait_window(self.top)

    def _code(self):
        return self.text.get("1.0", "end-1c")

    def _check(self):
        try:
            program = compile_script(self._code())
            hk = extract_hotkey(program)
            c = parse_key_spec(hk) if hk else None
            self.hotkey_hint.set(("声明启动热键: " + fmt_combo(c)) if c and c.get("vk") else "")
            self.status.set("语法检查通过。运行时可被热键 / 急停立即中断。")
            return True
        except ScriptError as e:
            self.status.set("语法错误 —— %s" % e)
            messagebox.showwarning("语法错误", str(e), parent=self.top)
            return False

    def _template(self):
        self.text.insert("end", "\n" + SCRIPT_TEMPLATE)

    def _guide(self):
        # 不用 grab_set, 避免与父对话框的模态 grab 嵌套冲突
        top = tk.Toplevel(self.top)
        top.title("脚本语法速查")
        top.configure(bg=BG)
        top.transient(self.top)
        top.resizable(True, True)
        top.geometry("640x520")
        frm = ttk.Frame(top, padding=12)
        frm.pack(fill="both", expand=True)
        t = tk.Text(frm, wrap="word", font=("Consolas", 10), bg=FIELD, fg=TEXT,
                    relief="solid", bd=1)
        t.insert("1.0", SCRIPT_GUIDE)
        t.configure(state="disabled")
        t.pack(fill="both", expand=True)
        ttk.Button(frm, text="关闭", command=lambda: top.destroy()).pack(pady=(8, 0))
        center_dialog(top, self.top)

    def _ok(self):
        if not self._check():
            return
        self.result = self._code()
        close_dialog(self.top)

    def _cancel(self):
        self.result = None
        close_dialog(self.top)


class ActionDialog(object):
    """编辑一个动作: 频率 / 坐标 / 结束条件 / 单点时长 / 脚本。"""

    def __init__(self, parent, action, cfg, title="编辑动作", compact=False):
        self.cfg = cfg
        self.action = json.loads(json.dumps(action)) if action else default_action()
        self.compact = compact
        self.result = None
        allow_script = bool(cfg.settings.get("script_enabled", False))
        self.top, frm = make_dialog(parent, title)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        a = self.action
        init_type = ("keyboard" if a.get("type") == "keyboard" else
                     "script" if a.get("type") == "script" else a.get("mouse_button", "left"))
        self.type_var = tk.StringVar(value=init_type)
        self.kbd_var = tk.StringVar(value=fmt_combo(a.get("kbd")))
        self.rate_var = tk.StringVar(value=a.get("rate_mode", "ms"))
        self.pos_var = tk.StringVar(value=a.get("pos_mode", "cursor"))
        self.stop_var = tk.StringVar(value=a.get("stop_mode", "infinite"))
        self.script_var = tk.StringVar(value="")
        self._set_script_hint()

        self.interval_var = tk.StringVar(value=str(_i(a.get("interval_ms"), 100)))
        self.jitter_var = tk.StringVar(value=str(_i(a.get("jitter_ms"), 0)))
        self.cps_var = tk.StringVar(value=str(_i(a.get("cps"), 10)))
        self.cps_jit_var = tk.StringVar(value=str(_i(a.get("cps_jitter"), 0)))
        self.window_var = tk.StringVar(value=str(_i(a.get("window_ms"), 1000)))
        self.min_var = tk.StringVar(value=str(_i(a.get("min_n"), 3)))
        self.max_var = tk.StringVar(value=str(_i(a.get("max_n"), 8)))
        self.hold_var = tk.StringVar(value=str(_i(a.get("hold_ms"), 0)))
        self.x_var = tk.StringVar(value=str(_i((a.get("pos") or {}).get("x"), 0)))
        self.y_var = tk.StringVar(value=str(_i((a.get("pos") or {}).get("y"), 0)))
        self.count_var = tk.StringVar(value=str(_i(a.get("stop_count"), 0) or 10))
        self.dur_var = tk.StringVar(value=str(_i(a.get("stop_duration_ms"), 0) / 1000.0 or 3))

        row = 0
        ttk.Label(frm, text="动作类型").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
        tbar = ttk.Frame(frm)
        tbar.grid(row=row, column=1, columnspan=2, sticky="w")
        types = [("左键", "left"), ("中键", "middle"), ("右键", "right"), ("键盘", "keyboard")]
        if allow_script:
            types.append(("脚本", "script"))
        for text, val in types:
            ttk.Radiobutton(tbar, text=text, value=val, variable=self.type_var,
                            command=self._sync).pack(side="left", padx=(0, 7))
        row += 1

        self.kbd_label = ttk.Label(frm, text="按键")
        self.kbd_label.grid(row=row, column=0, sticky="w", pady=4)
        self.kbd_entry = ttk.Entry(frm, textvariable=self.kbd_var, width=20, state="readonly")
        self.kbd_entry.grid(row=row, column=1, sticky="w", pady=4)
        self.kbd_btn = ttk.Button(frm, text="录制…", command=self._record_kbd, width=8)
        self.kbd_btn.grid(row=row, column=2, sticky="w", padx=6)
        row += 1

        self.script_row = ttk.Frame(frm)
        self.script_row.grid(row=row, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(self.script_row, text="伪代码").pack(side="left")
        ttk.Label(self.script_row, textvariable=self.script_var, foreground=MUTED).pack(side="left", padx=(8, 0))
        self.script_btn = ttk.Button(self.script_row, text="编写脚本…", command=self._edit_script)
        self.script_btn.pack(side="right")
        row += 1

        ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 6))
        row += 1
        ttk.Label(frm, text="点击频率").grid(row=row, column=0, sticky="w", padx=(0, 10))
        rbar = ttk.Frame(frm)
        rbar.grid(row=row, column=1, columnspan=2, sticky="w")
        for text, val in (("n±m ms/次", "ms"), ("n±m 次/s", "cps"), ("单位时间随机 n~m 次", "window")):
            ttk.Radiobutton(rbar, text=text, value=val, variable=self.rate_var,
                            command=self._sync).pack(side="left", padx=(0, 10))
        row += 1

        # 频率参数: 三组, 按模式启用
        self.rate_widgets = {}
        groups = [
            ("ms", "基准 n (ms)", self.interval_var, "波动 ±m (ms)", self.jitter_var),
            ("cps", "基准 n (次/s)", self.cps_var, "波动 ±m (次/s)", self.cps_jit_var),
        ]
        for mode, l1, v1, l2, v2 in groups:
            line = ttk.Frame(frm)
            line.grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
            ttk.Label(line, text=l1, width=15).pack(side="left")
            s1 = spin(line, v1, 0, 86400000, 9)
            s1.pack(side="left")
            ttk.Label(line, text=l2).pack(side="left", padx=(12, 0))
            s2 = spin(line, v2, 0, 86400000, 9)
            s2.pack(side="left")
            self.rate_widgets[mode] = (line, (s1, s2))
            row += 1
        wline = ttk.Frame(frm)
        wline.grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(wline, text="单位时间(ms)", width=15).pack(side="left")
        sw = spin(wline, self.window_var, 1, 86400000, 9)
        sw.pack(side="left")
        ttk.Label(wline, text="次数 n~m").pack(side="left", padx=(12, 0))
        smin = spin(wline, self.min_var, 0, 100000, 7)
        smin.pack(side="left")
        smax = spin(wline, self.max_var, 0, 100000, 7)
        smax.pack(side="left", padx=(4, 0))
        self.rate_widgets["window"] = (wline, (sw, smin, smax))
        row += 1

        hline = ttk.Frame(frm)
        hline.grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 2))
        ttk.Label(hline, text="单点时长", width=15).pack(side="left")
        spin(hline, self.hold_var, 0, 86400000, 9).pack(side="left")
        ttk.Label(hline, text="ms (按下保持)", foreground=MUTED).pack(side="left", padx=(8, 0))
        row += 1

        ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 6))
        row += 1
        ttk.Label(frm, text="点击坐标").grid(row=row, column=0, sticky="w", padx=(0, 10))
        pbar = ttk.Frame(frm)
        pbar.grid(row=row, column=1, columnspan=2, sticky="w")
        for text, val in (("跟随鼠标", "cursor"), ("固定坐标", "fixed"), ("编号坐标", "coord")):
            ttk.Radiobutton(pbar, text=text, value=val, variable=self.pos_var,
                            command=self._sync).pack(side="left", padx=(0, 10))
        row += 1
        pline = ttk.Frame(frm)
        pline.grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(pline, text="X", width=2).pack(side="left")
        self.x_spin = spin(pline, self.x_var, -100000, 100000, 8)
        self.x_spin.pack(side="left")
        ttk.Label(pline, text="Y").pack(side="left", padx=(10, 0))
        self.y_spin = spin(pline, self.y_var, -100000, 100000, 8)
        self.y_spin.pack(side="left", padx=(4, 0))
        self.pick_btn = ttk.Button(pline, text="拾取…", command=self._pick)
        self.pick_btn.pack(side="left", padx=(10, 0))
        self.coord_combo = ttk.Combobox(pline, state="readonly", width=18)
        self.coord_combo.pack(side="left", padx=(10, 0))
        self._fill_coords()
        row += 1

        if not compact:
            ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 6))
            row += 1
            ttk.Label(frm, text="结束条件").grid(row=row, column=0, sticky="w", padx=(0, 10))
            sbar = ttk.Frame(frm)
            sbar.grid(row=row, column=1, columnspan=2, sticky="w")
            for text, val in (("再按一次热键", "infinite"), ("点击次数", "count"), ("持续时间", "duration")):
                ttk.Radiobutton(sbar, text=text, value=val, variable=self.stop_var,
                                command=self._sync).pack(side="left", padx=(0, 10))
            row += 1
            sline = ttk.Frame(frm)
            sline.grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
            ttk.Label(sline, text="次数", width=6).pack(side="left")
            self.count_spin = spin(sline, self.count_var, 1, 1000000, 9)
            self.count_spin.pack(side="left")
            ttk.Label(sline, text="次", foreground=MUTED).pack(side="left", padx=(2, 12))
            ttk.Label(sline, text="时长").pack(side="left")
            self.dur_spin = spin(sline, self.dur_var, 0.1, 86400, 9)
            self.dur_spin.pack(side="left", padx=(4, 0))
            ttk.Label(sline, text="秒", foreground=MUTED).pack(side="left", padx=(2, 0))
            row += 1

        ttk.Label(frm, text=("节点模式下由节点参数控制频率 / 次数 / 时长；此处只设置动作类型与坐标。"
                             if compact else
                             "各参数彼此独立；任一动作到达结束条件, 该热键下所有点击一起停止。"),
                  foreground=MUTED, wraplength=470, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(10, 2))
        row += 1
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="保存", command=self._save, style="Accent.TButton").pack(side="right", padx=(0, 8))
        frm.columnconfigure(1, weight=1)
        self._sync()
        center_dialog(self.top, parent, 40, 20)
        parent.wait_window(self.top)

    def _fill_coords(self):
        vals = ["#%d %s (%d,%d)" % (_i(c.get("no"), 0), c.get("name", ""),
                                     _i(c.get("x"), 0), _i(c.get("y"), 0))
                for c in self.cfg.coords]
        self.coord_combo["values"] = vals
        cur = _i(self.action.get("coord_no"), 0)
        for idx, c in enumerate(self.cfg.coords):
            if _i(c.get("no"), 0) == cur:
                self.coord_combo.current(idx)
                return
        if vals:
            self.coord_combo.current(0)

    def _set_script_hint(self):
        src = str(self.action.get("script") or "")
        self.script_var.set(("%d 字符" % len(src)) if src.strip() else "未编写")

    def _sync(self):
        t = self.type_var.get()
        kb = t == "keyboard"
        sc = t == "script"
        self.kbd_btn.configure(state="normal" if kb else "disabled")
        self.kbd_label.configure(foreground=TEXT if kb else MUTED)
        self.script_btn.configure(state="normal" if sc else "disabled")
        for mode, (_line, widgets) in self.rate_widgets.items():
            for w in widgets:
                w.configure(state="normal" if mode == self.rate_var.get() else "disabled")
        pm = self.pos_var.get()
        for w in (self.x_spin, self.y_spin, self.pick_btn):
            w.configure(state="normal" if pm == "fixed" else "disabled")
        self.coord_combo.configure(state="readonly" if pm == "coord" else "disabled")
        if not self.compact:
            self.count_spin.configure(state="normal" if self.stop_var.get() == "count" else "disabled")
            self.dur_spin.configure(state="normal" if self.stop_var.get() == "duration" else "disabled")

    def _record_kbd(self):
        dlg = RecordDialog(self.top, "录制键盘按键", self.action.get("kbd"), allow_single=True)
        if dlg.result:
            self.action["kbd"] = dlg.result
            self.kbd_var.set(fmt_combo(dlg.result))

    def _edit_script(self):
        dlg = ScriptDialog(self.top, self.action.get("script", ""), cfg=self.cfg)
        if dlg.result is not None:
            self.action["script"] = dlg.result
            self._set_script_hint()

    def _pick(self):
        dlg = CoordPickerDialog(self.top, self.cfg,
                                {"x": _i(self.x_var.get(), 0), "y": _i(self.y_var.get(), 0)})
        if dlg.result:
            self.x_var.set(str(dlg.result["x"]))
            self.y_var.set(str(dlg.result["y"]))
            if dlg.result.get("coord_no"):
                self.action["coord_no"] = dlg.result["coord_no"]
                self.pos_var.set("coord")
                self._fill_coords()
                self._sync()

    def _save(self):
        t = self.type_var.get()
        if t == "keyboard" and not _i((self.action.get("kbd") or {}).get("vk"), 0):
            messagebox.showwarning("缺少按键", "请先录制要模拟的键盘按键。", parent=self.top)
            return
        if t == "script":
            try:
                compile_script(self.action.get("script", ""))
            except ScriptError as e:
                messagebox.showwarning("脚本错误", str(e), parent=self.top)
                return
        nums = {}
        pairs = (("interval_ms", self.interval_var), ("jitter_ms", self.jitter_var),
                 ("cps", self.cps_var), ("cps_jitter", self.cps_jit_var),
                 ("window_ms", self.window_var), ("min_n", self.min_var),
                 ("max_n", self.max_var), ("hold_ms", self.hold_var))
        for key, var in pairs:
            v = ask_num(var, key, self.top)
            if v is None:
                return
            nums[key] = v
        nums["cps"] = max(1, nums["cps"])
        nums["window_ms"] = max(1, nums["window_ms"])
        nums["max_n"] = max(nums["min_n"], nums["max_n"])
        nums["rate_mode"] = self.rate_var.get()
        x = ask_num(self.x_var, "X", self.top)
        y = ask_num(self.y_var, "Y", self.top)
        if x is None or y is None:
            return
        nums["pos"] = {"x": x, "y": y}
        nums["pos_mode"] = self.pos_var.get()
        if nums["pos_mode"] == "coord":
            sel = self.coord_combo.get()
            no = 0
            for c in self.cfg.coords:
                if sel.startswith("#%d " % _i(c.get("no"), 0)):
                    no = _i(c.get("no"), 0)
                    break
            if not no:
                messagebox.showwarning("缺少坐标", "请先在「坐标库」里添加编号坐标。", parent=self.top)
                return
            nums["coord_no"] = no
        if self.compact:
            nums["stop_mode"] = "infinite"
        else:
            nums["stop_mode"] = self.stop_var.get()
            if nums["stop_mode"] == "count":
                n = ask_num(self.count_var, "次数", self.top, 1)
                if n is None:
                    return
                nums["stop_count"] = n
                nums["repeat"] = n
            else:
                nums["stop_count"] = 0
                nums["repeat"] = 0
            if nums["stop_mode"] == "duration":
                secs = _f(self.dur_var.get(), 3, 0.1)
                nums["stop_duration_ms"] = int(secs * 1000)
            else:
                nums["stop_duration_ms"] = 0
        self.action["type"] = "script" if t == "script" else ("keyboard" if t == "keyboard" else "mouse")
        if t not in ("keyboard", "script"):
            self.action["mouse_button"] = t
        self.action.update(nums)
        self.result = self.action
        close_dialog(self.top)

    def _cancel(self):
        self.result = None
        close_dialog(self.top)


class HotkeyRow(object):
    """绑定 / 流程共用的热键编辑行。"""

    def __init__(self, parent, frm, row, item):
        self.parent, self.item = parent, item
        self.type_var = tk.StringVar(value=item.get("hotkey_type", "combo"))
        self.text_var = tk.StringVar(value=hotkey_text(item))
        ttk.Label(frm, text="启动热键").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
        box = ttk.Frame(frm)
        box.grid(row=row, column=1, columnspan=2, sticky="w")
        self.combo_rb = ttk.Radiobutton(box, text="组合键", value="combo",
                                        variable=self.type_var, command=self.sync)
        self.combo_rb.pack(side="left")
        self.multi_rb = ttk.Radiobutton(box, text="多键(≤5)", value="multi",
                                        variable=self.type_var, command=self.sync)
        self.multi_rb.pack(side="left", padx=(10, 0))
        self.none_rb = ttk.Radiobutton(box, text="不绑定", value="none",
                                       variable=self.type_var, command=self.sync)
        self.none_rb.pack(side="left", padx=(10, 0))
        self.row2 = row + 1
        line = ttk.Frame(frm)
        line.grid(row=self.row2, column=1, columnspan=2, sticky="w", pady=(0, 4))
        self.entry = ttk.Entry(line, textvariable=self.text_var, width=22, state="readonly")
        self.entry.pack(side="left")
        self.btn = ttk.Button(line, text="录制…", width=8, command=self.record)
        self.btn.pack(side="left", padx=(6, 0))
        ttk.Label(line, text="再按一次即停止", foreground=MUTED).pack(side="left", padx=(8, 0))
        self.sync()

    def sync(self):
        t = self.type_var.get()
        self.btn.configure(state="normal" if t in ("combo", "multi") else "disabled")
        self.text_var.set(hotkey_text({"hotkey": self.item.get("hotkey"),
                                       "hotkey_type": t,
                                       "hotkey_keys": self.item.get("hotkey_keys")}))

    def record(self):
        if self.type_var.get() == "multi":
            dlg = RecordKeysDialog(self.parent, self.item.get("hotkey_keys"))
            if dlg.result is not None:
                self.item["hotkey_keys"] = dlg.result
        else:
            dlg = RecordDialog(self.parent, "录制启动热键", self.item.get("hotkey"), allow_single=True)
            if dlg.result:
                self.item["hotkey"] = dlg.result
        self.sync()

    def apply(self):
        self.item["hotkey_type"] = self.type_var.get()


class BindingDialog(object):
    """基础点击: 一个热键 + 一组同时启动的独立动作。"""

    def __init__(self, parent, binding, cfg):
        self.cfg = cfg
        self.binding = json.loads(json.dumps(binding))
        if not self.binding.get("actions"):
            self.binding["actions"] = [default_action()]
        self.ok = False
        self.top, frm = make_dialog(parent, "新增绑定" if not binding.get("name") else "编辑绑定",
                                    resizable=True)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.name_var = tk.StringVar(value=self.binding.get("name", ""))
        self.enabled_var = tk.BooleanVar(value=bool(self.binding.get("enabled", True)))
        self.repeat_var = tk.StringVar(value=str(_i(self.binding.get("repeat"), 0)))
        self.sched = _sanitize_schedule(self.binding.get("schedule"))
        self.sched_var = tk.StringVar(value="")

        row = 0
        ttk.Label(frm, text="名称").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
        ttk.Entry(frm, textvariable=self.name_var, width=34).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=5)
        row += 1
        ttk.Checkbutton(frm, text="启用该绑定", variable=self.enabled_var).grid(
            row=row, column=1, columnspan=2, sticky="w", pady=(0, 4))
        row += 1
        self.hk = HotkeyRow(self.top, frm, row, self.binding)
        row += 2

        ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(6, 6))
        row += 1
        ttk.Label(frm, text="点击动作（同时开始, 各自按自己的频率 / 坐标 / 结束条件独立运行）",
                  font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w")
        row += 1
        box = ttk.Frame(frm)
        box.grid(row=row, column=0, columnspan=3, sticky="nsew")
        cols = ("no", "action", "rate", "pos", "stop")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=6, selectmode="browse")
        heads = {"no": "#", "action": "动作", "rate": "点击频率", "pos": "坐标", "stop": "结束条件"}
        widths = {"no": 34, "action": 120, "rate": 150, "pos": 120, "stop": 120}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="center" if c == "no" else "w")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self._edit_action())
        frm.rowconfigure(row, weight=1)
        row += 1
        bar = ttk.Frame(frm)
        bar.grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for text, cmd in (("＋ 添加", self._add_action), ("编辑", self._edit_action),
                          ("复制", self._dup_action), ("删除", self._del_action),
                          ("↑", self._move_up), ("↓", self._move_down)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 5))
        row += 1
        ttk.Label(frm, text="整组总次数上限 (0=无限)").grid(row=row, column=0, sticky="w", pady=(10, 4))
        spin(frm, self.repeat_var, 0, 86400000).grid(row=row, column=1, sticky="w", pady=(10, 4))
        row += 1
        sbox = ttk.Frame(frm)
        sbox.grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(sbox, text="定时启动…", command=self._schedule).pack(side="left")
        ttk.Label(sbox, textvariable=self.sched_var, foreground=MUTED).pack(side="left", padx=(8, 0))
        self._sync_sched()
        row += 1
        ttk.Label(frm, text="热键为开关: 按一次开始, 再按一次停止；双击行可编辑动作。\n"
                            "任一动作到达自己的结束条件时, 本组所有点击会一起停止。",
                  foreground=MUTED, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        row += 1
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="保存", command=self._save, style="Accent.TButton").pack(side="right", padx=(0, 8))
        frm.columnconfigure(1, weight=1)
        self._refresh()
        center_dialog(self.top, parent, 30, 15)
        parent.wait_window(self.top)

    def _sync_sched(self):
        s = self.sched
        if not s["enabled"]:
            self.sched_var.set("未启用")
        elif s["mode"] == "countdown":
            self.sched_var.set("倒计时 %d 秒" % s["delay_s"])
        else:
            self.sched_var.set("%s %s" % ("每日" if s["daily"] else "指定", s["at"]))

    def _schedule(self):
        dlg = ScheduleDialog(self.top, self.sched)
        if dlg.result:
            self.sched = dlg.result
            self._sync_sched()

    def _refresh(self):
        t = self.tree
        for iid in list(t.get_children()):
            t.delete(iid)
        for i, a in enumerate(self.binding.get("actions") or [], 1):
            t.insert("", "end", iid=str(i - 1),
                     values=(i, action_label(a), action_rate_label(a),
                             action_pos_label(a, self.cfg), action_stop_label(a)))

    def _index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _add_action(self):
        dlg = ActionDialog(self.top, default_action(), self.cfg, "添加动作")
        if dlg.result:
            self.binding.setdefault("actions", []).append(dlg.result)
            self._refresh()

    def _edit_action(self):
        i = self._index()
        if i is None:
            messagebox.showinfo("提示", "请先选中一个动作。", parent=self.top)
            return
        dlg = ActionDialog(self.top, self.binding["actions"][i], self.cfg)
        if dlg.result:
            self.binding["actions"][i] = dlg.result
            self._refresh()
            self.tree.selection_set(str(i))

    def _dup_action(self):
        i = self._index()
        if i is None:
            return
        cp = json.loads(json.dumps(self.binding["actions"][i]))
        cp["id"] = new_id()
        self.binding["actions"].insert(i + 1, cp)
        self._refresh()
        self.tree.selection_set(str(i + 1))

    def _del_action(self):
        i = self._index()
        if i is None:
            return
        if len(self.binding["actions"]) <= 1:
            messagebox.showinfo("提示", "至少保留一个动作。", parent=self.top)
            return
        self.binding["actions"].pop(i)
        self._refresh()

    def _move(self, d):
        i = self._index()
        if i is None:
            return
        j = i + d
        acts = self.binding["actions"]
        if j < 0 or j >= len(acts):
            return
        acts[i], acts[j] = acts[j], acts[i]
        self._refresh()
        self.tree.selection_set(str(j))

    def _move_up(self):
        self._move(-1)

    def _move_down(self):
        self._move(1)

    def _save(self):
        total = ask_num(self.repeat_var, "总次数", self.top)
        if total is None:
            return
        acts = self.binding.get("actions") or []
        if not acts:
            messagebox.showwarning("缺少动作", "请至少添加一个动作。", parent=self.top)
            return
        for a in acts:
            if a.get("type") == "keyboard" and not _i((a.get("kbd") or {}).get("vk"), 0):
                messagebox.showwarning("缺少按键", "有键盘动作未录制按键。", parent=self.top)
                return
        self.hk.apply()
        self.binding["name"] = self.name_var.get().strip() or "绑定"
        self.binding["enabled"] = bool(self.enabled_var.get())
        self.binding["repeat"] = total
        self.binding["schedule"] = self.sched
        self.ok = True
        close_dialog(self.top)

    def _cancel(self):
        self.ok = False
        close_dialog(self.top)


class NodeDialog(object):
    """节点循环里的一个动作节点。"""

    def __init__(self, parent, node, cfg):
        self.cfg = cfg
        self.node = json.loads(json.dumps(node)) if node else default_node()
        self.result = None
        self.top, frm = make_dialog(parent, "编辑节点")
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        n = self.node
        self.act_var = tk.StringVar(value="")
        self.count_var = tk.StringVar(value=str(_i(n.get("click_count"), 1)))
        self.interval_var = tk.StringVar(value=str(_i(n.get("interval_ms"), 100)))
        self.jitter_var = tk.StringVar(value=str(_i(n.get("jitter_ms"), 0)))
        self.hold_var = tk.StringVar(value=str(_i(n.get("hold_ms"), 0)))
        self.round_var = tk.StringVar(value=str(_i(n.get("round_duration_ms"), 0)))
        self.gap_var = tk.StringVar(value=str(_i(n.get("node_interval_ms"), 100)))
        self._sync_act()

        row = 0
        ttk.Label(frm, text="节点动作").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
        ttk.Label(frm, textvariable=self.act_var, foreground=ACCENT).grid(
            row=row, column=1, sticky="w")
        ttk.Button(frm, text="编辑动作…", command=self._edit_action).grid(row=row, column=2, padx=6)
        row += 1
        ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 6))
        row += 1
        fields = [
            ("点击次数 n", self.count_var, "次"),
            ("点击频率 n", self.interval_var, "ms/次 (次数>1 时生效)"),
            ("波动 ±m", self.jitter_var, "ms"),
            ("单机时长 n", self.hold_var, "ms (按下保持)"),
            ("单轮时长 n", self.round_var, "ms (0=不限, 到时进下一节点)"),
            ("循环间隔 n", self.gap_var, "ms (本节点执行完后等待)"),
        ]
        for label, var, note in fields:
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 10))
            spin(frm, var, 0, 86400000).grid(row=row, column=1, sticky="w", pady=3)
            ttk.Label(frm, text=note, foreground=MUTED).grid(row=row, column=2, sticky="w", padx=(8, 0))
            row += 1
        ttk.Label(frm, text="循环顺序: 动作1 + 其循环间隔 + 动作2 + 其循环间隔 + … + 下一轮",
                  foreground=MUTED, wraplength=430, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(12, 0))
        row += 1
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="保存", command=self._save, style="Accent.TButton").pack(side="right", padx=(0, 8))
        frm.columnconfigure(1, weight=1)
        center_dialog(self.top, parent, 40, 25)
        parent.wait_window(self.top)

    def _sync_act(self):
        a = self.node.get("action") or {}
        self.act_var.set("%s · %s" % (action_label(a), action_pos_label(a, self.cfg)))

    def _edit_action(self):
        dlg = ActionDialog(self.top, self.node.get("action"), self.cfg,
                           "编辑节点动作", compact=True)
        if dlg.result:
            self.node["action"] = dlg.result
            self._sync_act()

    def _save(self):
        vals = {}
        for key, var, minimum in (("click_count", self.count_var, 1),
                                  ("interval_ms", self.interval_var, 0),
                                  ("jitter_ms", self.jitter_var, 0),
                                  ("hold_ms", self.hold_var, 0),
                                  ("round_duration_ms", self.round_var, 0),
                                  ("node_interval_ms", self.gap_var, 0)):
            v = ask_num(var, key, self.top, minimum)
            if v is None:
                return
            vals[key] = v
        self.node.update(vals)
        self.result = self.node
        close_dialog(self.top)

    def _cancel(self):
        self.result = None
        close_dialog(self.top)


class FlowDialog(object):
    """节点循环: 轮数 / 全局间隔 / 热键 / 结束条件 / 节点表。"""

    def __init__(self, parent, flow, cfg):
        self.cfg = cfg
        self.flow = json.loads(json.dumps(flow))
        if not self.flow.get("nodes"):
            self.flow["nodes"] = [default_node()]
        self.ok = False
        self.top, frm = make_dialog(parent,
                                    "新增流程" if not flow.get("name") else "编辑流程",
                                    resizable=True)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        f = self.flow
        self.name_var = tk.StringVar(value=f.get("name", ""))
        self.enabled_var = tk.BooleanVar(value=bool(f.get("enabled", True)))
        self.rounds_var = tk.StringVar(value=str(_i(f.get("rounds"), 0)))
        self.gap_var = tk.StringVar(value=str(_i(f.get("round_interval_ms"), 0)))
        self.stop_var = tk.StringVar(value=f.get("stop_mode", "infinite"))
        self.count_var = tk.StringVar(value=str(_i(f.get("stop_count"), 0) or 100))
        self.dur_var = tk.StringVar(value=str(_i(f.get("stop_duration_ms"), 0) / 1000.0 or 10))
        self.sched = _sanitize_schedule(f.get("schedule"))
        self.sched_var = tk.StringVar(value="")

        row = 0
        ttk.Label(frm, text="名称").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
        ttk.Entry(frm, textvariable=self.name_var, width=34).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=5)
        row += 1
        ttk.Checkbutton(frm, text="启用该流程", variable=self.enabled_var).grid(
            row=row, column=1, columnspan=2, sticky="w", pady=(0, 4))
        row += 1
        self.hk = HotkeyRow(self.top, frm, row, self.flow)
        row += 2

        ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(6, 6))
        row += 1
        l1 = ttk.Frame(frm)
        l1.grid(row=row, column=0, columnspan=3, sticky="w")
        ttk.Label(l1, text="循环轮数 n").pack(side="left")
        spin(l1, self.rounds_var, 0, 1000000, 9).pack(side="left", padx=(8, 0))
        ttk.Label(l1, text="(0=无限)", foreground=MUTED).pack(side="left", padx=(4, 16))
        ttk.Label(l1, text="全局循环间隔").pack(side="left")
        spin(l1, self.gap_var, 0, 86400000, 9).pack(side="left", padx=(8, 0))
        ttk.Label(l1, text="ms", foreground=MUTED).pack(side="left", padx=(4, 0))
        row += 1
        l2 = ttk.Frame(frm)
        l2.grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(l2, text="结束条件").pack(side="left")
        for text, val in (("手动/热键", "infinite"), ("总次数", "count"), ("总时长", "duration")):
            ttk.Radiobutton(l2, text=text, value=val, variable=self.stop_var,
                            command=self._sync).pack(side="left", padx=(6, 0))
        self.count_spin = spin(l2, self.count_var, 1, 1000000, 8)
        self.count_spin.pack(side="left", padx=(8, 0))
        self.dur_spin = spin(l2, self.dur_var, 0.1, 86400, 8)
        self.dur_spin.pack(side="left", padx=(8, 0))
        ttk.Label(l2, text="次 / 秒", foreground=MUTED).pack(side="left", padx=(4, 0))
        row += 1
        sbox = ttk.Frame(frm)
        sbox.grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(sbox, text="定时启动…", command=self._schedule).pack(side="left")
        ttk.Label(sbox, textvariable=self.sched_var, foreground=MUTED).pack(side="left", padx=(8, 0))
        self._sync_sched()
        row += 1

        ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky="ew", pady=(8, 6))
        row += 1
        ttk.Label(frm, text="节点（按顺序循环执行；双击行编辑）",
                  font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w")
        row += 1
        box = ttk.Frame(frm)
        box.grid(row=row, column=0, columnspan=3, sticky="nsew")
        cols = ("no", "action", "count", "rate", "hold", "round", "gap", "pos")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", height=6, selectmode="browse")
        heads = {"no": "#", "action": "动作", "count": "次数", "rate": "频率(±ms)",
                 "hold": "单机时长", "round": "单轮时长", "gap": "循环间隔", "pos": "坐标"}
        widths = {"no": 32, "action": 96, "count": 50, "rate": 100,
                  "hold": 76, "round": 76, "gap": 76, "pos": 104}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="center" if c in ("no", "count") else "w")
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self._edit_node())
        frm.rowconfigure(row, weight=1)
        row += 1
        bar = ttk.Frame(frm)
        bar.grid(row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))
        for text, cmd in (("＋ 添加", self._add_node), ("编辑", self._edit_node),
                          ("复制", self._dup_node), ("删除", self._del_node),
                          ("↑", self._move_up), ("↓", self._move_down)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 5))
        row += 1
        ttk.Label(frm, text="每个节点可独立设置点击次数、频率、单机时长与循环间隔；"
                            "点击坐标在「编辑动作」里设置。",
                  foreground=MUTED, wraplength=470, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        row += 1
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="保存", command=self._save, style="Accent.TButton").pack(side="right", padx=(0, 8))
        frm.columnconfigure(1, weight=1)
        self._sync()
        self._refresh()
        center_dialog(self.top, parent, 25, 12)
        parent.wait_window(self.top)

    def _sync(self):
        m = self.stop_var.get()
        self.count_spin.configure(state="normal" if m == "count" else "disabled")
        self.dur_spin.configure(state="normal" if m == "duration" else "disabled")

    def _sync_sched(self):
        s = self.sched
        self.sched_var.set("未启用" if not s["enabled"] else
                           ("倒计时 %d 秒" % s["delay_s"] if s["mode"] == "countdown"
                            else "%s %s" % ("每日" if s["daily"] else "指定", s["at"])))

    def _schedule(self):
        dlg = ScheduleDialog(self.top, self.sched)
        if dlg.result:
            self.sched = dlg.result
            self._sync_sched()

    def _refresh(self):
        t = self.tree
        for iid in list(t.get_children()):
            t.delete(iid)
        for i, n in enumerate(self.flow.get("nodes") or [], 1):
            a = n.get("action") or {}
            rate = "%d±%d" % (_i(n.get("interval_ms"), 0), _i(n.get("jitter_ms"), 0))
            rd = _i(n.get("round_duration_ms"), 0)
            t.insert("", "end", iid=str(i - 1),
                     values=(i, action_label(a), _i(n.get("click_count"), 1), rate,
                             "%dms" % _i(n.get("hold_ms"), 0),
                             ("%dms" % rd) if rd else "不限",
                             "%dms" % _i(n.get("node_interval_ms"), 0),
                             action_pos_label(a, self.cfg)))

    def _index(self):
        sel = self.tree.selection()
        try:
            return int(sel[0]) if sel else None
        except Exception:
            return None

    def _add_node(self):
        dlg = NodeDialog(self.top, default_node(len(self.flow["nodes"]) + 1), self.cfg)
        if dlg.result:
            self.flow["nodes"].append(dlg.result)
            self._refresh()

    def _edit_node(self):
        i = self._index()
        if i is None:
            messagebox.showinfo("提示", "请先选中一个节点。", parent=self.top)
            return
        dlg = NodeDialog(self.top, self.flow["nodes"][i], self.cfg)
        if dlg.result:
            self.flow["nodes"][i] = dlg.result
            self._refresh()
            self.tree.selection_set(str(i))

    def _dup_node(self):
        i = self._index()
        if i is None:
            return
        cp = json.loads(json.dumps(self.flow["nodes"][i]))
        cp["id"] = new_id()
        self.flow["nodes"].insert(i + 1, cp)
        self._refresh()
        self.tree.selection_set(str(i + 1))

    def _del_node(self):
        i = self._index()
        if i is None:
            return
        if len(self.flow["nodes"]) <= 1:
            messagebox.showinfo("提示", "至少保留一个节点。", parent=self.top)
            return
        self.flow["nodes"].pop(i)
        self._refresh()

    def _move(self, d):
        i = self._index()
        if i is None:
            return
        j = i + d
        ns = self.flow["nodes"]
        if j < 0 or j >= len(ns):
            return
        ns[i], ns[j] = ns[j], ns[i]
        self._refresh()
        self.tree.selection_set(str(j))

    def _move_up(self):
        self._move(-1)

    def _move_down(self):
        self._move(1)

    def _save(self):
        rounds = ask_num(self.rounds_var, "循环轮数", self.top)
        gap = ask_num(self.gap_var, "全局间隔", self.top)
        if rounds is None or gap is None:
            return
        if not self.flow.get("nodes"):
            messagebox.showwarning("缺少节点", "请至少添加一个节点。", parent=self.top)
            return
        mode = self.stop_var.get()
        if mode == "count":
            n = ask_num(self.count_var, "总次数", self.top, 1)
            if n is None:
                return
            self.flow["stop_count"] = n
        else:
            self.flow["stop_count"] = 0
        self.flow["stop_duration_ms"] = int(_f(self.dur_var.get(), 10, 0.1) * 1000) \
            if mode == "duration" else 0
        self.hk.apply()
        self.flow["name"] = self.name_var.get().strip() or "流程"
        self.flow["enabled"] = bool(self.enabled_var.get())
        self.flow["rounds"] = rounds
        self.flow["round_interval_ms"] = gap
        self.flow["stop_mode"] = mode
        self.flow["schedule"] = self.sched
        self.ok = True
        close_dialog(self.top)

    def _cancel(self):
        self.ok = False
        close_dialog(self.top)


class LibraryDialog(object):
    """编号坐标库 + 方案预设。"""

    def __init__(self, parent, app):
        self.app = app
        self.cfg = app.config
        self.top, frm = make_dialog(parent, "坐标库 / 方案管理", resizable=True)
        self.top.geometry("680x480")
        nb = ttk.Notebook(frm)
        nb.pack(fill="both", expand=True)
        p1 = ttk.Frame(nb, padding=12)
        p2 = ttk.Frame(nb, padding=12)
        nb.add(p1, text="编号坐标")
        nb.add(p2, text="方案预设")

        # --- 坐标 ---
        cbox = ttk.Frame(p1)
        cbox.pack(fill="both", expand=True)
        cols = ("no", "name", "x", "y")
        self.ctree = ttk.Treeview(cbox, columns=cols, show="headings", height=10)
        for c, h, w in zip(cols, ("#", "名称", "X", "Y"), (50, 200, 90, 90)):
            self.ctree.heading(c, text=h)
            self.ctree.column(c, width=w)
        self.ctree.pack(side="left", fill="both", expand=True)
        csb = ttk.Scrollbar(cbox, orient="vertical", command=self.ctree.yview)
        csb.pack(side="right", fill="y")
        self.ctree.configure(yscrollcommand=csb.set)
        cbar = ttk.Frame(p1)
        cbar.pack(fill="x", pady=(8, 0))
        ttk.Button(cbar, text="＋ 拾取新坐标", command=self._add_coord).pack(side="left")
        ttk.Button(cbar, text="重命名", command=self._rename_coord).pack(side="left", padx=(6, 0))
        ttk.Button(cbar, text="删除", command=self._del_coord).pack(side="left", padx=(6, 0))
        ttk.Label(p1, text="动作里选「编号坐标」即可按编号引用, 修改一处全局生效。",
                  foreground=MUTED).pack(anchor="w", pady=(8, 0))

        # --- 方案 ---
        pbox = ttk.Frame(p2)
        pbox.pack(fill="both", expand=True)
        cols = ("no", "name", "b", "f", "c")
        self.ptree = ttk.Treeview(pbox, columns=cols, show="headings", height=10)
        for c, h, w in zip(cols, ("#", "名称", "绑定", "流程", "坐标"), (50, 220, 70, 70, 70)):
            self.ptree.heading(c, text=h)
            self.ptree.column(c, width=w)
        self.ptree.pack(side="left", fill="both", expand=True)
        psb = ttk.Scrollbar(pbox, orient="vertical", command=self.ptree.yview)
        psb.pack(side="right", fill="y")
        self.ptree.configure(yscrollcommand=psb.set)
        pbar = ttk.Frame(p2)
        pbar.pack(fill="x", pady=(8, 0))
        ttk.Button(pbar, text="另存当前为方案", command=self._save_preset).pack(side="left")
        ttk.Button(pbar, text="切换到该方案", command=self._switch_preset).pack(side="left", padx=(6, 0))
        ttk.Button(pbar, text="重命名", command=self._rename_preset).pack(side="left", padx=(6, 0))
        ttk.Button(pbar, text="删除", command=self._del_preset).pack(side="left", padx=(6, 0))
        ttk.Label(p2, text="切换方案会替换当前工作集的绑定 / 流程 / 坐标, 并重建全部热键。",
                  foreground=MUTED).pack(anchor="w", pady=(8, 0))
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(10, 0))
        ttk.Button(bar, text="关闭", command=lambda: close_dialog(self.top)).pack(side="right")
        self._refresh()
        center_dialog(self.top, parent, 30, 20)
        parent.wait_window(self.top)

    def _refresh(self):
        for iid in list(self.ctree.get_children()):
            self.ctree.delete(iid)
        for c in self.cfg.coords:
            self.ctree.insert("", "end", iid=str(_i(c.get("no"), 0)),
                              values=(_i(c.get("no"), 0), c.get("name", ""),
                                      _i(c.get("x"), 0), _i(c.get("y"), 0)))
        for iid in list(self.ptree.get_children()):
            self.ptree.delete(iid)
        for p in self.cfg.presets:
            self.ptree.insert("", "end", iid=str(_i(p.get("no"), 0)),
                              values=(_i(p.get("no"), 0), p.get("name", ""),
                                      len(p.get("bindings") or []),
                                      len(p.get("flows") or []),
                                      len(p.get("coords") or [])))

    def _sel(self, tree):
        s = tree.selection()
        try:
            return _i(s[0], 0) if s else 0
        except Exception:
            return 0

    def _add_coord(self):
        dlg = CoordPickerDialog(self.top, self.cfg)
        if dlg.result and not dlg.result.get("coord_no"):
            no = self.cfg.next_coord_no()
            self.cfg.coords.append(default_coord(no, "坐标 %d" % no,
                                                 dlg.result["x"], dlg.result["y"]))
        self.cfg.save()
        self._refresh()
        self.app._refresh_tree()

    def _rename_coord(self):
        no = self._sel(self.ctree)
        if not no:
            return
        c = self.cfg.coord_by_no(no)
        if not c:
            return
        d = _TextDialog(self.top, "重命名坐标", "名称", c.get("name", ""))
        if d.result is not None:
            c["name"] = d.result
            self.cfg.save()
            self._refresh()

    def _del_coord(self):
        no = self._sel(self.ctree)
        if not no:
            return
        if messagebox.askyesno("删除坐标", "确认删除 #%d？引用它的动作会退回跟随鼠标。" % no,
                               parent=self.top):
            self.cfg.coords = [c for c in self.cfg.coords if _i(c.get("no"), 0) != no]
            self.cfg.save()
            self._refresh()

    def _save_preset(self):
        d = _TextDialog(self.top, "另存为方案", "方案名称",
                        "方案 %d" % self.cfg.next_preset_no())
        if d.result is None:
            return
        no = self.cfg.next_preset_no()
        self.cfg.presets.append(default_preset(
            no, d.result, self.cfg.bindings, self.cfg.flows, self.cfg.coords))
        self.cfg.settings["active_preset"] = no
        self.cfg.save()
        self._refresh()
        self.app._refresh_presets()

    def _switch_preset(self):
        no = self._sel(self.ptree)
        if no:
            self.app.switch_preset(no)
            self._refresh()

    def _rename_preset(self):
        no = self._sel(self.ptree)
        p = next((x for x in self.cfg.presets if _i(x.get("no"), 0) == no), None)
        if not p:
            return
        d = _TextDialog(self.top, "重命名方案", "方案名称", p.get("name", ""))
        if d.result is not None:
            p["name"] = d.result
            self.cfg.save()
            self._refresh()
            self.app._refresh_presets()

    def _del_preset(self):
        no = self._sel(self.ptree)
        if not no:
            return
        if messagebox.askyesno("删除方案", "确认删除方案 #%d？" % no, parent=self.top):
            self.cfg.presets = [p for p in self.cfg.presets if _i(p.get("no"), 0) != no]
            if _i(self.cfg.settings.get("active_preset"), 0) == no:
                self.cfg.settings["active_preset"] = 0
            self.cfg.save()
            self._refresh()
            self.app._refresh_presets()


class _TextDialog(object):
    def __init__(self, parent, title, label, initial=""):
        self.result = None
        self.top, frm = make_dialog(parent, title)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.var = tk.StringVar(value=initial)
        ttk.Label(frm, text=label).pack(anchor="w")
        e = ttk.Entry(frm, textvariable=self.var, width=30)
        e.pack(fill="x", pady=(4, 0))
        e.focus_set()
        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Button(bar, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="确定", command=self._ok, style="Accent.TButton").pack(side="right", padx=(8, 0))
        center_dialog(self.top, parent)
        parent.wait_window(self.top)

    def _ok(self):
        self.result = self.var.get().strip()
        close_dialog(self.top)

    def _cancel(self):
        close_dialog(self.top)


class AboutDialog(object):
    def __init__(self, parent):
        self.top, frm = make_dialog(parent, "关于 %s" % APP_NAME, padding=20)
        ttk.Label(frm, text="%s  v%s" % (APP_NAME, APP_VERSION),
                  font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        byline = tk.Label(frm, text="由 %s 制作   ·   © %s  ↗" % (AUTHOR, time.strftime("%Y")),
                          fg=ACCENT, bg=BG, cursor="hand2",
                          font=("Microsoft YaHei UI", 9, "underline"))
        byline.pack(anchor="w", pady=(4, 12))
        byline.bind("<Button-1>", lambda e: self._open_latest())
        ttk.Separator(frm).pack(fill="x", pady=(0, 12))
        ttk.Label(frm, text="开源地址（点击打开）").pack(anchor="w")
        link = tk.Label(frm, text=PROJECT_URL, fg=ACCENT, bg=BG, cursor="hand2",
                        font=("Microsoft YaHei UI", 9, "underline"))
        link.pack(anchor="w", pady=(2, 10))
        link.bind("<Button-1>", lambda e: self._open())
        ttk.Label(frm, text="许可协议: MIT License（可自由使用、修改、分发, 保留版权声明）",
                  foreground=MUTED, wraplength=380, justify="left").pack(anchor="w")
        ttk.Label(frm, text="Python + Win32 SendInput · 基础点击 / 节点循环 / 类 C 伪代码脚本",
                  foreground=MUTED, wraplength=380, justify="left").pack(anchor="w", pady=(6, 12))
        bar = ttk.Frame(frm)
        bar.pack(fill="x")
        ttk.Button(bar, text="打开项目主页", command=self._open).pack(side="left")
        ttk.Button(bar, text="最新版本", command=self._open_latest).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="复制网址", command=self._copy).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="关闭", command=lambda: close_dialog(self.top)).pack(side="right")
        center_dialog(self.top, parent, 120, 80)
        parent.wait_window(self.top)

    def _open(self):
        try:
            webbrowser.open(PROJECT_URL)
        except Exception:
            pass

    def _open_latest(self):
        try:
            webbrowser.open(LATEST_URL)
        except Exception:
            pass

    def _copy(self):
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(PROJECT_URL)
        except Exception:
            pass


class SettingsDialog(object):
    """设置: 常规 / 外观 / 高级 / 关于。"""

    def __init__(self, parent, config, app=None):
        self.config, self.app, self.ok = config, app, False
        self._old_theme = config.settings.get("theme", "light")
        self._old_opacity = _i(config.settings.get("background_opacity"), 45)
        self._old_background = config.settings.get("background", "")
        self._old_personal = {
            "accent": config.settings.get("accent", "blue"),
            "density": config.settings.get("density", "comfortable"),
            "radius": _i(config.settings.get("radius"), 10),
            "animations": bool(config.settings.get("animations", True)),
            "badges": bool(config.settings.get("badges", True)),
        }
        self.top = tk.Toplevel(parent)
        self.top.title("设置")
        self.top.configure(bg=BG)
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.grab_set()
        self.top.protocol("WM_DELETE_WINDOW", self._close)
        st = config.settings
        self.em_var = tk.StringVar(value=fmt_combo(st.get("emergency_hotkey")))
        self.fallback_var = tk.BooleanVar(value=bool(st.get("panic_fallback", True)))
        self.close_var = tk.StringVar(value=st.get("close_action", "tray"))
        self.auto_var = tk.BooleanVar(value=bool(st.get("autostart", False)))
        self.auto_hidden_var = tk.BooleanVar(value=bool(st.get("start_hidden", False)))
        self.theme_var = tk.StringVar(value=st.get("theme", "light"))
        self.bg_var = tk.StringVar(value=(st.get("background") or ""))
        self.script_var = tk.BooleanVar(value=bool(st.get("script_enabled", False)))
        self.restore_var = tk.BooleanVar(value=bool(st.get("restore_cursor", True)))
        self.new_em = st.get("emergency_hotkey")
        # v1.5 个性化
        self._accent_keys = list(ACCENTS.keys())
        self.accent_var = tk.StringVar(value=self._old_personal["accent"])
        self.density_var = tk.StringVar(value=self._old_personal["density"])
        self.radius_var = tk.IntVar(value=self._old_personal["radius"])
        self.anim_var = tk.BooleanVar(value=self._old_personal["animations"])
        self.badge_var = tk.BooleanVar(value=self._old_personal["badges"])
        self._radius_label = tk.StringVar(value="%d px" % self._old_personal["radius"])

        nb = ttk.Notebook(self.top)
        nb.pack(fill="both", expand=True, padx=14, pady=14)
        f1, f2, f5, f3, f4 = (ttk.Frame(nb, padding=16) for _ in range(5))
        nb.add(f1, text="常规")
        nb.add(f2, text="外观")
        nb.add(f5, text="个性化")
        nb.add(f3, text="高级")
        nb.add(f4, text="关于")

        row = 0
        ttk.Label(f1, text="紧急停止热键").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(f1, textvariable=self.em_var, width=22, state="readonly").grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(f1, text="录制…", width=8, command=self._record).grid(row=row, column=2, padx=6)
        row += 1
        ttk.Checkbutton(f1, text="始终保留 Ctrl+Alt+F12 备用急停",
                        variable=self.fallback_var).grid(row=row, column=0, columnspan=3, sticky="w", pady=4)
        row += 1
        ttk.Separator(f1).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1
        ttk.Label(f1, text="关闭窗口时").grid(row=row, column=0, sticky="w", pady=4)
        box = ttk.Frame(f1)
        box.grid(row=row, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(box, text="最小化到系统托盘", value="tray",
                        variable=self.close_var).pack(anchor="w")
        ttk.Radiobutton(box, text="直接退出程序", value="quit",
                        variable=self.close_var).pack(anchor="w", pady=(2, 0))
        row += 1
        ttk.Separator(f1).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1
        self.auto_state = tk.StringVar(value="开机启动: " + ("已启用" if autostart_get() else "未启用"))
        ttk.Checkbutton(f1, text="开机自动启动", variable=self.auto_var).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=4)
        row += 1
        ttk.Checkbutton(f1, text="开机启动后不显示主界面(仅托盘)",
                        variable=self.auto_hidden_var).grid(
            row=row, column=1, columnspan=2, sticky="w", pady=2)
        row += 1
        ttk.Label(f1, textvariable=self.auto_state, foreground=MUTED).grid(
            row=row, column=1, columnspan=2, sticky="w", pady=2)
        row += 1
        ttk.Label(f1, text="配置文件: %s" % CONFIG_FILE, foreground=MUTED,
                  wraplength=380, justify="left").grid(row=row, column=0, columnspan=3, sticky="w", pady=(12, 2))
        row += 1
        ttk.Button(f1, text="打开配置目录", command=self._open_dir).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(2, 4))
        f1.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(f2, text="界面主题").grid(row=row, column=0, sticky="w", pady=(0, 6))
        box2 = ttk.Frame(f2)
        box2.grid(row=row, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(box2, text="亮色", value="light", variable=self.theme_var,
                        command=self._preview_theme).pack(side="left")
        ttk.Radiobutton(box2, text="暗色", value="dark", variable=self.theme_var,
                        command=self._preview_theme).pack(side="left", padx=(16, 0))
        row += 1
        ttk.Separator(f2).grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)
        row += 1
        ttk.Label(f2, text="界面背景图").grid(row=row, column=0, sticky="w", pady=6)
        self.bg_combo = ttk.Combobox(f2, textvariable=self.bg_var, width=26, state="readonly")
        self.bg_combo.grid(row=row, column=1, sticky="ew", pady=6)
        self.bg_combo.bind("<<ComboboxSelected>>", lambda e: self._preview_bg())
        ttk.Button(f2, text="浏览…", width=8, command=self._browse_bg).grid(row=row, column=2, padx=6)
        row += 1
        self._fill_pictures()
        ttk.Button(f2, text="不使用背景图", command=self._clear_bg).grid(
            row=row, column=1, sticky="w", pady=(2, 8))
        row += 1
        ttk.Separator(f2).grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1
        ttk.Label(f2, text="面板不透明度").grid(row=row, column=0, sticky="w", pady=4)
        self.op_var = tk.IntVar(value=_i(st.get("background_opacity"), 45))
        self.op_label = tk.StringVar(value="%d%%" % self.op_var.get())
        obox = ttk.Frame(f2)
        obox.grid(row=row, column=1, columnspan=2, sticky="ew")
        ttk.Scale(obox, from_=10, to=90, orient="horizontal", variable=self.op_var,
                  command=self._preview_opacity).pack(side="left", fill="x", expand=True)
        ttk.Label(obox, textvariable=self.op_label, width=5).pack(side="left", padx=(8, 0))
        row += 1
        ttk.Label(f2, text="启用背景图时内容面板会半透明透出图片；数值越小越透、越大越清晰。",
                  foreground=MUTED, wraplength=400, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(2, 4))
        row += 1
        ttk.Label(f2, text="图片放在 %s\n选「浏览…」会自动复制进去。" % PICTURES_DIR,
                  foreground=MUTED, wraplength=400, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w")
        f2.columnconfigure(1, weight=1)

        # ---- 个性化 (v1.5) ----
        row = 0
        ttk.Label(f5, text="强调色", font=("Microsoft YaHei UI", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 4))
        row += 1
        abox = ttk.Frame(f5)
        abox.grid(row=row, column=0, columnspan=3, sticky="w")
        self.accent_combo = ttk.Combobox(abox, state="readonly", width=13,
                                         values=[ACCENTS[k][1] for k in self._accent_keys])
        try:
            self.accent_combo.current(self._accent_keys.index(self.accent_var.get()))
        except Exception:
            self.accent_combo.current(0)
        self.accent_combo.pack(side="left")
        self.accent_combo.bind("<<ComboboxSelected>>", lambda e: self._preview_personal())
        self._accent_swatch = tk.Canvas(abox, width=30, height=22,
                                        highlightthickness=0, bd=0, bg=BG)
        self._accent_swatch.pack(side="left", padx=(10, 0))
        ttk.Label(abox, text="按钮 / 链接 / 选中态 / 表格强调条",
                  foreground=MUTED).pack(side="left", padx=(10, 0))
        row += 1
        ttk.Separator(f5).grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)
        row += 1
        ttk.Label(f5, text="界面密度").grid(row=row, column=0, sticky="w", pady=6)
        dbox = ttk.Frame(f5)
        dbox.grid(row=row, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(dbox, text="舒适", value="comfortable", variable=self.density_var,
                        command=self._preview_personal).pack(side="left")
        ttk.Radiobutton(dbox, text="紧凑（行高更小、留白更少）", value="compact",
                        variable=self.density_var,
                        command=self._preview_personal).pack(side="left", padx=(12, 0))
        row += 1
        ttk.Label(f5, text="圆角半径").grid(row=row, column=0, sticky="w", pady=6)
        rbox = ttk.Frame(f5)
        rbox.grid(row=row, column=1, columnspan=2, sticky="ew")
        ttk.Scale(rbox, from_=0, to=18, orient="horizontal", variable=self.radius_var,
                  command=lambda v: self._preview_personal()).pack(side="left", fill="x", expand=True)
        ttk.Label(rbox, textvariable=self._radius_label, width=6).pack(side="left", padx=(8, 0))
        row += 1
        ttk.Separator(f5).grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)
        row += 1
        ttk.Checkbutton(f5, text="动态切换（主题渐变、分区指示条滑动）",
                        variable=self.anim_var, command=self._preview_personal).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=3)
        row += 1
        ttk.Checkbutton(f5, text="显示状态徽标与表格状态色点",
                        variable=self.badge_var, command=self._preview_personal).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=3)
        row += 1
        ttk.Label(f5, text="所有改动即时预览；取消设置会还原，点「保存」才写入配置。",
                  foreground=MUTED, wraplength=420, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(12, 0))
        f5.columnconfigure(1, weight=1)
        self._preview_personal()

        row = 0
        ttk.Checkbutton(f3, text="启用高级脚本（类 C 伪代码）", variable=self.script_var).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(0, 2))
        row += 1
        ttk.Label(f3, text="关闭时不能添加/运行脚本动作。脚本只能调用内置点击函数, "
                            "无文件 / 网络 / 模块导入能力, 且有步数上限。",
                  foreground=MUTED, wraplength=400, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w")
        row += 1
        ttk.Separator(f3).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1
        ttk.Checkbutton(f3, text="固定坐标点击后把鼠标移回原位置",
                        variable=self.restore_var).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1
        ttk.Separator(f3).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1
        b3 = ttk.Frame(f3)
        b3.grid(row=row, column=0, columnspan=3, sticky="w")
        ttk.Button(b3, text="坐标库 / 方案管理", command=self._library).pack(side="left")
        ttk.Button(b3, text="脚本编辑器", command=self._script).pack(side="left", padx=(8, 0))
        ttk.Button(b3, text="C++ 编译路径说明", command=self._cpp).pack(side="left", padx=(8, 0))
        row += 1
        ttk.Label(f3, text="C++ 改版已是独立产品线：仓库 cpp/ 下提供代码工作台"
                            "（真 C++ 编写 + 一键真编译）与完整源码；docs/CPP_PATH.md "
                            "记录编译路径与数据包级点击的规划。",
                  foreground=MUTED, wraplength=400, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(10, 0))
        f3.columnconfigure(1, weight=1)

        ttk.Label(f4, text="%s  v%s" % (APP_NAME, APP_VERSION),
                  font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        byline = tk.Label(f4, text="由 %s 制作   ·   © %s  ↗" % (AUTHOR, time.strftime("%Y")),
                          fg=ACCENT, bg=BG, cursor="hand2",
                          font=("Microsoft YaHei UI", 9, "underline"))
        byline.pack(anchor="w", pady=(2, 10))
        byline.bind("<Button-1>", lambda e: self._open_latest_url())
        link = tk.Label(f4, text=PROJECT_URL, fg=ACCENT, bg=BG, cursor="hand2",
                        font=("Microsoft YaHei UI", 9, "underline"))
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: self._open_url())
        ttk.Label(f4, text="许可协议: MIT License", foreground=MUTED).pack(anchor="w", pady=(6, 10))
        b4 = ttk.Frame(f4)
        b4.pack(fill="x")
        ttk.Button(b4, text="打开项目主页", command=self._open_url).pack(side="left")
        ttk.Button(b4, text="复制网址", command=self._copy_url).pack(side="left", padx=(8, 0))
        ttk.Button(b4, text="关于窗口", command=lambda: AboutDialog(self.top)).pack(side="left", padx=(8, 0))

        bar = ttk.Frame(self.top)
        bar.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(bar, text="取消", command=self._close).pack(side="right")
        ttk.Button(bar, text="保存", command=self._save, style="Accent.TButton").pack(side="right", padx=(0, 8))
        center_dialog(self.top, parent, 60, 40)
        parent.wait_window(self.top)

    def _fill_pictures(self):
        names = [""] + list_pictures()
        self.bg_combo["values"] = names
        if (self.bg_var.get() or "") not in names:
            self.bg_var.set("")

    def _preview_theme(self):
        if self.app:
            self.app.preview_theme(self.theme_var.get())

    def _preview_bg(self):
        if self.app:
            self.app.preview_background(self.bg_var.get())

    def _preview_opacity(self, value=None):
        v = min(90, max(10, _i(self.op_var.get(), 45)))
        self.op_label.set("%d%%" % v)
        if self.app:
            self.app.preview_opacity(v)

    def _preview_personal(self, *_):
        """个性化即时预览: 强调色 / 密度 / 圆角 / 动画 / 徽标。"""
        try:
            i = self.accent_combo.current()
            if 0 <= i < len(self._accent_keys):
                self.accent_var.set(self._accent_keys[i])
        except Exception:
            pass
        radius = min(18, max(0, _i(self.radius_var.get(), 10)))
        try:
            self._radius_label.set("%d px" % radius)
            sw = self._accent_swatch
            sw.configure(bg=BG)
            sw.delete("all")
            col = ACCENTS.get(self.accent_var.get(), ACCENTS["blue"])[0]
            _round_pill(sw, 1, 1, 29, 21, min(radius, 8), col)
        except Exception:
            pass
        if self.app:
            self.app.preview_personal({
                "accent": self.accent_var.get(),
                "density": self.density_var.get(),
                "radius": radius,
                "animations": bool(self.anim_var.get()),
                "badges": bool(self.badge_var.get()),
            })

    def _browse_bg(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.top, title="选择背景图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif"), ("所有文件", "*.*")])
        if not path:
            return
        name = import_picture(path)
        if not name:
            messagebox.showwarning("导入失败", "无法复制图片到 pictures 目录。", parent=self.top)
            return
        self._fill_pictures()
        self.bg_var.set(name)
        self._preview_bg()

    def _clear_bg(self):
        self.bg_var.set("")
        self._preview_bg()

    def _library(self):
        if self.app:
            LibraryDialog(self.top, self.app)

    def _script(self):
        dlg = ScriptDialog(self.top, self.config.settings.get("scratch_script", ""),
                           "脚本编辑器（草稿）", self.config)
        if dlg.result is not None:
            self.config.settings["scratch_script"] = dlg.result
            self.config.save()

    def _cpp(self):
        # 打包态 base_dir() 是 exe 目录, 源码态是仓库根; 两处都找一遍
        roots = [base_dir(),
                 os.path.join(base_dir(), "autoclicker"),
                 os.path.dirname(os.path.abspath(__file__)),
                 os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
        for root in roots:
            path = os.path.join(root, "docs", "CPP_PATH.md")
            if os.path.isfile(path):
                try:
                    os.startfile(path)
                    return
                except Exception:
                    break
        try:
            webbrowser.open(PROJECT_URL + "/tree/main/docs")
        except Exception:
            pass

    def _record(self):
        dlg = RecordDialog(self.top, "录制紧急停止热键", self.new_em, allow_single=False)
        if dlg.result:
            self.new_em = dlg.result
            self.em_var.set(fmt_combo(dlg.result))

    def _open_dir(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            os.startfile(CONFIG_DIR)
        except Exception:
            pass

    def _open_url(self):
        try:
            webbrowser.open(PROJECT_URL)
        except Exception:
            pass

    def _open_latest_url(self):
        try:
            webbrowser.open(LATEST_URL)
        except Exception:
            pass

    def _copy_url(self):
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(PROJECT_URL)
        except Exception:
            pass

    def _save(self):
        s = self.config.settings
        s["emergency_hotkey"] = self.new_em
        s["panic_fallback"] = bool(self.fallback_var.get())
        s["close_action"] = self.close_var.get()
        s["start_hidden"] = bool(self.auto_hidden_var.get())
        s["theme"] = self.theme_var.get()
        s["background"] = (self.bg_var.get() or "").strip()
        s["autostart"] = bool(self.auto_var.get())
        s["script_enabled"] = bool(self.script_var.get())
        s["restore_cursor"] = bool(self.restore_var.get())
        s["background_opacity"] = min(90, max(10, _i(self.op_var.get(), 45)))
        s["accent"] = self.accent_var.get()
        s["density"] = self.density_var.get()
        s["radius"] = min(18, max(0, _i(self.radius_var.get(), 10)))
        s["animations"] = bool(self.anim_var.get())
        s["badges"] = bool(self.badge_var.get())
        autostart_set(s["autostart"], hidden=s["start_hidden"])
        self.config.save()
        self.ok = True
        if self.app:
            self.app.apply_settings()
            self.app.rebuild_after_settings()
        self._close()

    def _close(self):
        if not self.ok and self.app:
            try:
                self.app.preview_personal(self._old_personal)
                self.app.preview_theme(self._old_theme)
                self.app.config.settings["background_opacity"] = self._old_opacity
                self.app.preview_background(self._old_background)
            except Exception:
                pass
        close_dialog(self.top)


class App(object):
    def __init__(self, root, config, engine):
        self.root, self.config, self.engine = root, config, engine
        self.icon = None
        self._tray_state = None
        self._menu_sig = None
        self._was_running = False
        self._closing = False
        self._sched_due = {}
        self._glass_tables = []       # 主界面 Canvas 自绘表格
        self._glass_active = False
        self._frosted_image = None
        self._frosted_photo = None
        self._panel_color = BG

        self.root.title("%s  v%s" % (APP_NAME, APP_VERSION))
        self.root.geometry("1000x620")
        self.root.minsize(860, 500)
        self.root.configure(bg=BG)
        apply_personal(config.settings)
        set_theme(config.settings.get("theme", "light"),
                  config.settings.get("accent"))
        try:
            os.makedirs(PICTURES_DIR, exist_ok=True)
        except Exception:
            pass
        self._style()
        self._setup_background()
        self._build()
        self._refresh_tree()
        engine.on_change = self.request_refresh
        engine.on_error = self._on_engine_error
        engine.rebuild_hotkeys()
        self._setup_tray()
        self._reschedule()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(400, self._refresh_background)
        self._poll()
        self._sched_tick()

    # ---- 样式与背景 ----
    def _style(self):
        apply_theme(self.root)

    def _setup_background(self):
        self._bg_photo, self._bg_item, self._bg_margin = None, None, 0
        self._frosted_image, self._frosted_photo = None, None
        self.bg_canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, bg=BG)
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        try:
            self.bg_canvas.lower()
        except Exception:
            pass
        self._resize_job = None
        self.root.bind("<Configure>", self._on_resize)
        self._refresh_background()

    def _layout_outer(self):
        try:
            w, h = self.root.winfo_width(), self.root.winfo_height()
            if w <= 1 or h <= 1:
                return
            m = getattr(self, "_bg_margin", 0)
            self.outer.place(x=m, y=m, width=max(40, w - 2 * m), height=max(40, h - 2 * m))
        except Exception:
            pass

    def _on_resize(self, event=None):
        self._layout_outer()
        if self._resize_job:
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(150, self._refresh_background)

    def _glass_off(self):
        """关闭背景图: 移除所有面板底图 Canvas, 恢复不透明主题色。"""
        try:
            self.root.attributes("-alpha", 1.0)
            self.root.configure(bg=BG)
            self.bg_canvas.configure(bg=BG)
        except Exception:
            pass
        self._remove_glass_canvases()
        self._glass_active = False
        self._panel_color = BG
        self._frosted_image, self._frosted_photo = None, None
        self._reset_glass()
        self._paint_tabs()
        self._refresh_badges()
        self._redraw_glass_tables()

    def _redraw_glass_tables(self):
        for t in getattr(self, "_glass_tables", ()):
            try:
                t.redraw_now()
            except Exception:
                pass

    def _iter_panel_frames(self, widget=None):
        """遍历主界面里所有作为布局容器的 Frame(底图 Canvas 不是 Frame, 自动排除)。"""
        w = widget if widget is not None else self.root
        for child in w.winfo_children():
            if isinstance(child, GlassTable):
                continue          # 自绘表格自己画背景, 不需要再铺一层
            if isinstance(child, (tk.Frame, ttk.Frame)):
                yield child
                for sub in self._iter_panel_frames(child):
                    yield sub

    def _remove_glass_canvases(self):
        for frame in list(self._iter_panel_frames()):
            cv = getattr(frame, "_glass_canvas", None)
            if cv is not None:
                try:
                    cv.destroy()
                except Exception:
                    pass
                frame._glass_canvas = None

    def _reset_glass(self):
        """不透明模式: 容器走 clam 默认背景, 文字带底色。"""
        st = ttk.Style()
        for style in ("Glass.TFrame", "Glass.TLabel", "Link.TLabel", "Outer.TFrame"):
            st.layout(style, st.layout(style.split(".", 1)[-1]))
        st.configure("Outer.TFrame", background=BG, bordercolor=BG,
                     lightcolor=BG, darkcolor=BG)
        st.configure("Glass.TFrame", background=BG, bordercolor=BG,
                     lightcolor=BG, darkcolor=BG)
        st.configure("Glass.TLabel", background=BG, foreground=TEXT,
                     bordercolor=BG, lightcolor=BG, darkcolor=BG)
        st.configure("Link.TLabel", background=BG, foreground=ACCENT,
                     bordercolor=BG, lightcolor=BG, darkcolor=BG,
                     font=("Microsoft YaHei UI", 9, "underline"))
        # 主表格为 GlassTable 自绘, 不需要 Glass.Treeview 样式;
        # 对话框里的 ttk 表格仍走 apply_theme 里的基础 Treeview 样式。

    def _refresh_background(self):
        """背景图: 窗口底层画清晰原图; 每个面板容器内部铺一层底图 Canvas,
        以负坐标偏移绘制同一张半透明磨砂图 —— 面板整体半透明且与背景像素对齐,
        既不会挡住背景图, 也不会透出桌面。"""
        name = (self.config.settings.get("background") or "").strip()
        try:
            self.bg_canvas.delete("all")
        except Exception:
            return
        path = os.path.join(PICTURES_DIR, name) if name else ""
        if not path or not os.path.isfile(path):
            self.bg_canvas.configure(bg=BG)
            self._bg_photo = None
            self._glass_off()
            self._bg_margin = 0
            self._layout_outer()
            return
        try:
            from PIL import Image, ImageTk
            w, h = max(1, self.root.winfo_width()), max(1, self.root.winfo_height())
            img = Image.open(path).convert("RGB")
            rw, rh = img.size
            scale = max(w / float(rw), h / float(rh))
            img = img.resize((max(1, int(rw * scale)), max(1, int(rh * scale))), Image.LANCZOS)
            left, top = max(0, (img.size[0] - w) // 2), max(0, (img.size[1] - h) // 2)
            img = img.crop((left, top, left + w, top + h))

            op = min(90, max(10, _i(self.config.settings.get("background_opacity"), 45))) / 100.0
            m = 18
            self._bg_photo = ImageTk.PhotoImage(img)
            self.bg_canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)

            # 整窗半透明磨砂图: 面板容器与自绘表格共用, 按各自绝对位置裁片
            frosted = Image.blend(img, Image.new("RGB", (w, h), _hex_rgb(CARD)), op)
            self._frosted_image = frosted
            self._frosted_photo = ImageTk.PhotoImage(frosted)
            avg = frosted.resize((1, 1)).getpixel((0, 0))
            panel = _rgb_hex(avg)
            self._panel_color = panel

            st = ttk.Style()
            # 文字去掉 border 元素, 只保留 label -> 不画色块, 直接浮在底图上
            label_layout = [("Label.padding", {
                "sticky": "nswe", "children": [("Label.label", {"sticky": "nswe"})]})]
            st.layout("Glass.TLabel", label_layout)
            st.layout("Link.TLabel", label_layout)
            st.configure("Glass.TLabel", foreground=TEXT)
            st.configure("Link.TLabel", foreground=ACCENT,
                         font=("Microsoft YaHei UI", 9, "underline"))
            # 主表格已换 GlassTable 自绘, 底图直接取 frosted 裁片,
            # 不再依赖 ttk.Treeview 的 field/heading 样式。

            self._glass_active = True
            self._bg_margin = m
            self._paint_tabs()
            self._layout_outer()
            self._paint_glass_canvases()
        except Exception:
            self.bg_canvas.configure(bg=BG)
            self._bg_photo = None
            self._glass_off()

    def _paint_glass_canvases(self):
        """给每个面板容器铺底图; 按容器在窗口中的绝对位置负偏移, 拼接后严丝合缝。"""
        if not getattr(self, "_glass_active", False):
            return
        try:
            self.root.update_idletasks()   # 结算 pack/place, 保证容器坐标已稳定
        except Exception:
            pass
        rx0, ry0 = self.root.winfo_rootx(), self.root.winfo_rooty()
        for frame in self._iter_panel_frames():
            try:
                cv = getattr(frame, "_glass_canvas", None)
                if cv is None:
                    cv = tk.Canvas(frame, highlightthickness=0, bd=0, bg=BG)
                    cv.place(x=0, y=0, relwidth=1, relheight=1)
                    tk.Misc.lower(cv)
                    frame._glass_canvas = cv
                if not cv.winfo_exists():
                    continue
                x = frame.winfo_rootx() - rx0
                y = frame.winfo_rooty() - ry0
                cv.delete("all")
                cv.create_image(-x, -y, anchor="nw", image=self._frosted_photo)
            except Exception:
                pass
        self._redraw_glass_tables()

    def apply_settings(self):
        apply_personal(self.config.settings)
        set_theme(self.config.settings.get("theme", "light"),
                  self.config.settings.get("accent"))
        self._restyle()

    # ---- 界面 ----
    def _build(self):
        self.outer = ttk.Frame(self.root, style="Outer.TFrame", padding=0)
        self._layout_outer()
        panel = self.outer

        head = ttk.Frame(panel, padding=(18, 14, 18, 8), style="Glass.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=APP_NAME, font=("Microsoft YaHei UI", 17, "bold"),
                  foreground=TEXT, style="Glass.TLabel").pack(side="left")
        ttk.Label(head, text="· 连点工作台", foreground=MUTED,
                  style="Glass.TLabel").pack(side="left", padx=(8, 0))
        # 圆角状态徽标
        self._badge_state = tk.Canvas(head, width=100, height=24,
                                      highlightthickness=0, bd=0, bg=BG)
        self._badge_state._ac_text = "就绪"
        self._badge_state.pack(side="left", padx=(14, 0))
        self._badge_total = tk.Canvas(head, width=108, height=24,
                                      highlightthickness=0, bd=0, bg=BG)
        self._badge_total._ac_text = "总点击 0"
        self._badge_total.pack(side="left", padx=(8, 0))
        for text, cmd, w, sty in (("关于", self.open_about, 6, "TButton"),
                                  ("设置", self.open_settings, 7, "Accent.TButton"),
                                  ("库", self.open_library, 5, "TButton"),
                                  ("脚本动作", self.add_script_binding, 10, "TButton"),
                                  ("隐藏", self.hide_window, 6, "TButton")):
            ttk.Button(head, text=text, command=cmd, width=w,
                       style=sty).pack(side="right", padx=(0, 6))

        preset_bar = ttk.Frame(panel, padding=(18, 4), style="Glass.TFrame")
        preset_bar.pack(fill="x")
        ttk.Label(preset_bar, text="方案", font=("Microsoft YaHei UI", 9, "bold"),
                  foreground=MUTED, style="Glass.TLabel").pack(side="left", padx=(0, 4))
        self.preset_var = tk.StringVar()
        self.preset_combo = ttk.Combobox(preset_bar, textvariable=self.preset_var,
                                         state="readonly", width=24)
        self.preset_combo.pack(side="left", padx=(8, 0))
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        ttk.Button(preset_bar, text="另存为…", command=self.save_preset).pack(side="left", padx=(8, 0))
        ttk.Button(preset_bar, text="保存当前", command=self.update_preset).pack(side="left", padx=(6, 0))
        ttk.Button(preset_bar, text="紧急停止(全部)", command=self.emergency_stop,
                   style="Danger.TButton").pack(side="right")
        self._refresh_presets()

        # 圆角胶囊分区标签(单 Canvas 自绘): ttk.Notebook 的 client 自绘不透明,
        # 会整块挡住背景图; Canvas 胶囊既能透明配合背景, 又能做滑动指示条。
        tabbar = ttk.Frame(panel, style="Glass.TFrame")
        tabbar.pack(fill="x", padx=(18, 18), pady=(10, 0))
        self._tab_canvas = tk.Canvas(tabbar, height=36, highlightthickness=0, bd=0, bg=BG)
        self._tab_canvas.pack(fill="x")
        self._tab_canvas.bind("<Button-1>", self._on_tab_click)
        self._tab_canvas.bind("<Configure>", lambda e: self._paint_tabs())
        self._tab_boxes = {}
        self._indicator_x = 4.0

        host = ttk.Frame(panel, style="Glass.TFrame")
        host.pack(fill="both", expand=True, padx=(18, 18), pady=(0, 4))
        tab1 = ttk.Frame(host, padding=10, style="Glass.TFrame")
        tab2 = ttk.Frame(host, padding=10, style="Glass.TFrame")
        self._tab_frames = {"basic": tab1, "flow": tab2}
        self._build_table(tab1, "basic")
        self._build_table(tab2, "flow")
        self._active_tab = "basic"
        self._switch_tab("basic")

        foot = ttk.Frame(panel, padding=(16, 4, 16, 10), style="Glass.TFrame")
        foot.pack(fill="x")
        self.tip = tk.StringVar(value="双击行可编辑；热键为开关模式, 全局生效。")
        ttk.Label(foot, textvariable=self.tip, foreground=MUTED,
                  style="Glass.TLabel").pack(side="left")
        self.foot_right = ttk.Label(foot, foreground=MUTED, style="Glass.TLabel")
        self.foot_right.pack(side="right")
        self.author_link = ttk.Label(foot, text="由 %s 制作" % AUTHOR,
                                     style="Link.TLabel", cursor="hand2")
        self.author_link.pack(side="right", padx=(0, 20))
        self.author_link.bind("<Button-1>", lambda e: self._open_latest())
        self.author_link.bind("<Enter>",
                              lambda e: self.tip.set("点击查看 GitHub 最新版本页面: %s" % LATEST_URL))
        self.author_link.bind("<Leave>", lambda e: self._restore_tip())
        self._update_foot()

    def _open_latest(self):
        """署名链接: 打开项目最新版本页面(始终指向最新, 不会因版本更新而失效)。"""
        try:
            webbrowser.open(LATEST_URL)
        except Exception:
            pass

    def _restore_tip(self):
        try:
            self._refresh_tree()
        except Exception:
            pass

    def _switch_tab(self, kind):
        self._active_tab = kind
        for k, frame in self._tab_frames.items():
            if k == kind:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        self._paint_tabs()
        box = self._tab_boxes.get(kind)
        if box:
            self._animate_indicator(box[0])
        if getattr(self, "_glass_active", False):
            try:
                self.root.after(60, self._paint_glass_canvases)
            except Exception:
                pass

    def _on_tab_click(self, event):
        for k, (x0, y0, x1, y1) in self._tab_boxes.items():
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self._switch_tab(k)
                return

    def _animate_indicator(self, target):
        """分区指示条滑动过渡; 关闭动画时直接落位。"""
        if not ANIMATIONS:
            self._indicator_x = float(target)
            self._paint_tabs()
            return
        start = float(self._indicator_x)
        if abs(target - start) < 0.5:
            return

        def step(i):
            if i > 6:
                return
            self._indicator_x = start + (target - start) * (i / 6.0)
            self._paint_tabs()
            try:
                self.root.after(18, lambda: step(i + 1))
            except Exception:
                pass
        step(1)

    def _paint_tabs(self):
        """圆角胶囊分区标签 + 滑动指示条, 配色随背景图 / 主题刷新。"""
        cv = getattr(self, "_tab_canvas", None)
        if cv is None:            # _setup_background() 早于 _build(), 此时还没创建
            return
        glass = getattr(self, "_glass_active", False)
        base = getattr(self, "_panel_color", BG) if glass else BG
        try:
            cv.configure(bg=base)
        except Exception:
            pass
        cv.delete("all")
        h = max(30, cv.winfo_height())
        tw = 116
        for i, (key, text) in enumerate((("basic", "  基础点击  "),
                                         ("flow", "  节点循环  "))):
            x0 = 4 + i * (tw + 6)
            box = (x0, 2, x0 + tw, h - 7)
            self._tab_boxes[key] = box
            active = (key == getattr(self, "_active_tab", "basic"))
            r = min(CURRENT_RADIUS, 12)
            if active:
                _round_pill(cv, box[0], box[1], box[2], box[3], r, ACCENT)
            else:
                _round_pill(cv, box[0], box[1], box[2], box[3], r, RING)
                _round_pill(cv, box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1,
                            max(0, r - 1), CARD)
            cv.create_text((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0,
                           text=text, fill="#ffffff" if active else TEXT,
                           font=("Microsoft YaHei UI", 10, "bold" if active else "normal"))
        if BADGES:
            x = float(getattr(self, "_indicator_x", 4.0))
            _round_pill(cv, x + 8, h - 5, x + tw - 8, h - 3, 1, ACCENT)

    def _build_table(self, parent, kind):
        bar = ttk.Frame(parent, style="Glass.TFrame")
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text="新增", style="Accent.TButton",
                   command=(self.add_binding if kind == "basic" else self.add_flow)).pack(side="left")
        ttk.Button(bar, text="编辑", width=8,
                   command=(self.edit_binding if kind == "basic" else self.edit_flow)).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="删除", width=8,
                   command=(self.delete_binding if kind == "basic" else self.delete_flow)).pack(side="left", padx=(6, 0))
        ttk.Button(bar, text="启动 / 停止", width=12,
                   command=self.toggle_selected).pack(side="left", padx=(12, 0))
        ttk.Label(bar, text=("并行独立节奏" if kind == "basic" else "顺序循环流程"),
                  foreground=MUTED, style="Glass.TLabel").pack(side="right")
        wrap = ttk.Frame(parent, style="Glass.TFrame")
        wrap.pack(fill="both", expand=True)
        cols = ("status", "no", "name", "hotkey", "action", "rate", "mode", "count")
        # 主表格用 Canvas 自绘: ttk.Treeview 的 field 由 clam 强制画不透明底色,
        # 无法透出背景图; GlassTable 保持相同 API, 底图直接取背景图裁片。
        tree = GlassTable(wrap, columns=cols, show="headings",
                          selectmode="browse", glass_provider=self)
        self._glass_tables.append(tree)
        heads = {"status": "状态", "no": "#", "name": "名称", "hotkey": "启动热键",
                 "action": "动作 / 节点", "rate": "频率", "mode": "结束 / 轮数", "count": "已点击"}
        widths = {"status": 100, "no": 34, "name": 110, "hotkey": 120,
                  "action": 190, "rate": 140, "mode": 120, "count": 72}
        for c in cols:
            tree.heading(c, text=heads[c])
            tree.column(c, width=widths[c], anchor="center" if c in ("no", "count", "status") else "w")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vs.set)
        tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        tree.tag_configure("running", foreground=OK)
        tree.tag_configure("stopped", foreground=MUTED)
        tree.tag_configure("waiting", foreground=WARN)
        tree.bind("<Double-1>", lambda e: (self.edit_binding() if kind == "basic" else self.edit_flow()))
        if kind == "basic":
            self.tree = tree
        else:
            self.flow_tree = tree

    def _update_foot(self):
        try:
            short = os.path.join(os.path.basename(os.path.dirname(CONFIG_FILE)),
                                 os.path.basename(CONFIG_FILE))
            self.foot_right.configure(text="配置: %s" % short)
        except Exception:
            pass

    # ---- 数据同步 ----
    def _active_kind(self):
        return getattr(self, "_active_tab", "basic")

    def _status_text(self, item):
        due = self._sched_due.get(item["id"])
        if due:
            left = max(0, int(due - time.time()))
            return "◷ 等待定时 %ds" % left
        return "—"

    def _refresh_table(self, kind, tree, items):
        keep = {it["id"]: it for it in items}
        for iid in list(tree.get_children()):
            if iid not in keep:
                tree.delete(iid)
        for it in items:
            running = self.engine.is_running(it["id"])
            if not it.get("enabled", True):
                status = "已禁用"
            elif running:
                status = "● 运行中"
            elif it["id"] in self._sched_due:
                status = self._status_text(it)
            else:
                status = "○ 已停止"
            if kind == "basic":
                action = binding_summary(it)
                rate = binding_rhythm(it)
                mode = action_stop_label((it.get("actions") or [{}])[0])
            else:
                action = flow_summary(it)
                rate = "%d 节点" % len(it.get("nodes") or [])
                mode = ("%d 轮" % _i(it.get("rounds"), 0)) if _i(it.get("rounds"), 0) else "无限轮"
            values = (status, _i(it.get("no"), 0), it.get("name", ""), hotkey_text(it),
                      action, rate, mode, str(self.engine.click_count(it["id"])))
            tag = "running" if running else ("waiting" if it["id"] in self._sched_due else "stopped")
            if tree.exists(it["id"]):
                tree.item(it["id"], values=values, tags=(tag,))
            else:
                tree.insert("", "end", iid=it["id"], values=values, tags=(tag,))

    def _refresh_tree(self):
        self._refresh_table("basic", self.tree, self.config.bindings)
        self._refresh_table("flow", self.flow_tree, self.config.flows)
        n = len(self.engine.running_ids())
        changed = False
        for key, txt in (("state", ("运行中 %d 组" % n) if n else "就绪"),
                         ("total", "总点击 %d" % self.engine.total_clicks())):
            b = getattr(self, "_badge_" + key, None)
            if b is not None and getattr(b, "_ac_text", None) != txt:
                b._ac_text = txt
                changed = True
        if changed:
            self._refresh_badges()
        errs = list(self.engine.hotkeys.errors) + list(self.engine.watcher.errors)
        if errs:
            self.tip.set("⚠ 热键注册失败: " + "、".join(errs))
        else:
            self.tip.set("双击行编辑 · 热键按一次开始、再按一次停止")
        self._sync_tray()

    def _on_engine_error(self, msg):
        ui_call(lambda m=msg: (self.tip.set("脚本错误: " + m), self._notify(m, "脚本错误")))

    # ---- 选中 / 增删改 ----
    def selected_id(self):
        tree = self.flow_tree if self._active_kind() == "flow" else self.tree
        sel = tree.selection()
        return sel[0] if sel else None

    def add_binding(self):
        b = default_binding(len(self.config.bindings) + 1)
        dlg = BindingDialog(self.root, b, self.config)
        if dlg.ok:
            self.config.bindings.append(dlg.binding)
            self.save_and_rebuild()
            self.tree.selection_set(dlg.binding["id"])

    def edit_binding(self):
        iid = self.selected_id()
        if not iid:
            messagebox.showinfo("提示", "请先选择一条绑定。", parent=self.root)
            return
        kind, b = self.config.item_by_id(iid)
        if kind != "basic":
            return
        dlg = BindingDialog(self.root, b, self.config)
        if dlg.ok:
            self.config.bindings[self.config.bindings.index(b)] = dlg.binding
            self.engine.stop(iid)
            self.save_and_rebuild()

    def delete_binding(self):
        iid = self.selected_id()
        if not iid:
            messagebox.showinfo("提示", "请先选择一条绑定。", parent=self.root)
            return
        _kind, b = self.config.item_by_id(iid)
        if not b:
            return
        if messagebox.askyesno("删除绑定", "确认删除「%s」？" % b.get("name", ""), parent=self.root):
            self.engine.stop(iid)
            self.config.bindings = [x for x in self.config.bindings if x["id"] != iid]
            self.save_and_rebuild()

    def add_flow(self):
        f = default_flow(len(self.config.flows) + 1)
        dlg = FlowDialog(self.root, f, self.config)
        if dlg.ok:
            self.config.flows.append(dlg.flow)
            self.save_and_rebuild()
            self.flow_tree.selection_set(dlg.flow["id"])

    def edit_flow(self):
        iid = self.selected_id()
        if not iid:
            messagebox.showinfo("提示", "请先选择一条流程。", parent=self.root)
            return
        kind, f = self.config.item_by_id(iid)
        if kind != "flow":
            return
        dlg = FlowDialog(self.root, f, self.config)
        if dlg.ok:
            self.config.flows[self.config.flows.index(f)] = dlg.flow
            self.engine.stop(iid)
            self.save_and_rebuild()

    def delete_flow(self):
        iid = self.selected_id()
        if not iid:
            messagebox.showinfo("提示", "请先选择一条流程。", parent=self.root)
            return
        _kind, f = self.config.item_by_id(iid)
        if not f:
            return
        if messagebox.askyesno("删除流程", "确认删除「%s」？" % f.get("name", ""), parent=self.root):
            self.engine.stop(iid)
            self.config.flows = [x for x in self.config.flows if x["id"] != iid]
            self.save_and_rebuild()

    def toggle_selected(self):
        iid = self.selected_id()
        if not iid:
            messagebox.showinfo("提示", "请先选择一条记录。", parent=self.root)
            return
        self.engine.toggle(iid)
        self._refresh_tree()

    def emergency_stop(self):
        self.engine.stop_all()
        self._refresh_tree()
        self._notify("已停止全部连点", "紧急停止已执行")

    def add_script_binding(self):
        if not self.config.settings.get("script_enabled", False):
            messagebox.showinfo("高级脚本未启用",
                                "请先在「设置 → 高级」中启用高级脚本。", parent=self.root)
            return
        dlg = ScriptDialog(self.root, SCRIPT_TEMPLATE, "新建脚本动作", self.config)
        if dlg.result is None:
            return
        b = default_binding(len(self.config.bindings) + 1)
        b["name"] = "脚本 %d" % b["no"]
        a = default_action()
        a["type"] = "script"
        a["script"] = dlg.result
        b["actions"] = [a]
        try:
            hk = extract_hotkey(compile_script(dlg.result))
            c = parse_key_spec(hk) if hk else None
            if c and c.get("vk"):
                b["hotkey"] = c
        except ScriptError:
            pass
        self.config.bindings.append(b)
        self.save_and_rebuild()
        self._switch_tab("basic")
        self.tree.selection_set(b["id"])

    # ---- 方案 ----
    def _preset_label(self, p):
        return "#%d %s" % (_i(p.get("no"), 0), p.get("name", ""))

    def _refresh_presets(self):
        vals = ["（当前工作集）"] + [self._preset_label(p) for p in self.config.presets]
        self.preset_combo["values"] = vals
        cur = _i(self.config.settings.get("active_preset"), 0)
        idx = 0
        for i, p in enumerate(self.config.presets, 1):
            if _i(p.get("no"), 0) == cur:
                idx = i
                break
        try:
            self.preset_combo.current(idx)
        except Exception:
            pass

    def _on_preset_selected(self, _e=None):
        idx = self.preset_combo.current()
        if idx <= 0:
            return
        p = self.config.presets[idx - 1]
        self.switch_preset(_i(p.get("no"), 0))

    def save_preset(self):
        d = _TextDialog(self.root, "另存为方案", "方案名称",
                        "方案 %d" % self.config.next_preset_no())
        if d.result is None:
            return
        no = self.config.next_preset_no()
        self.config.presets.append(default_preset(
            no, d.result, self.config.bindings, self.config.flows, self.config.coords))
        self.config.settings["active_preset"] = no
        self.config.save()
        self._refresh_presets()

    def update_preset(self):
        no = _i(self.config.settings.get("active_preset"), 0)
        p = next((x for x in self.config.presets if _i(x.get("no"), 0) == no), None)
        if not p:
            messagebox.showinfo("提示", "当前不属于任何方案, 请用「另存为…」。", parent=self.root)
            return
        p["bindings"] = json.loads(json.dumps(self.config.bindings))
        p["flows"] = json.loads(json.dumps(self.config.flows))
        p["coords"] = json.loads(json.dumps(self.config.coords))
        self.config.save()
        self._notify("方案 #%d 已更新" % no, APP_NAME)

    def switch_preset(self, no):
        p = next((x for x in self.config.presets if _i(x.get("no"), 0) == no), None)
        if not p:
            return
        if not messagebox.askyesno("切换方案",
                                   "切换到「%s」？当前工作集会被该方案内容替换（未保存的修改会丢失）。" % p.get("name", ""),
                                   parent=self.root):
            self._refresh_presets()
            return
        self.engine.stop_all()
        self.config.bindings = json.loads(json.dumps(p.get("bindings") or []))
        self.config.flows = json.loads(json.dumps(p.get("flows") or []))
        self.config.coords = json.loads(json.dumps(p.get("coords") or []))
        self.config.settings["active_preset"] = no
        self.save_and_rebuild()
        self._refresh_presets()

    # ---- 定时启动 ----
    def _compute_due(self, sched, now=None):
        return schedule_due(sched, now)

    def _reschedule(self):
        self._sched_due = {}
        for _kind, it in self.config.items():
            if not it.get("enabled", True):
                continue
            due = self._compute_due(it.get("schedule"))
            if due:
                self._sched_due[it["id"]] = due

    def _sched_tick(self):
        if self._closing:
            return
        try:
            now = time.time()
            for _kind, it in self.config.items():
                iid = it["id"]
                due = self._sched_due.get(iid)
                if due and now >= due:
                    if not self.engine.is_running(iid):
                        self.engine.start(iid)
                        self._notify("定时启动: %s" % it.get("name", ""), APP_NAME)
                    s = _sanitize_schedule(it.get("schedule"))
                    if s["mode"] == "daily":
                        self._sched_due[iid] = due + 86400
                        while self._sched_due[iid] <= now:
                            self._sched_due[iid] += 86400
                    else:
                        self._sched_due.pop(iid, None)
        except Exception:
            pass
        self.root.after(500, self._sched_tick)

    # ---- 设置 / 关于 ----
    def open_settings(self):
        dlg = SettingsDialog(self.root, self.config, app=self)
        if dlg.ok:
            self.engine.rebuild_hotkeys()
            self._refresh_tree()
            self._update_foot()
            self._notify("设置已保存", APP_NAME)

    def open_about(self):
        AboutDialog(self.root)

    def open_library(self):
        LibraryDialog(self.root, self)
        self._refresh_tree()
        self._refresh_presets()

    def _restyle(self):
        self._style()
        self._refresh_background()
        for tree in (self.tree, self.flow_tree):
            try:
                tree.tag_configure("running", foreground=OK)
                tree.tag_configure("stopped", foreground=MUTED)
                tree.tag_configure("waiting", foreground=WARN)
            except Exception:
                pass
        self._paint_tabs()
        self._refresh_badges()
        self._redraw_glass_tables()

    def preview_theme(self, name):
        """主题切换: 开启动画时做几帧颜色插值, 关闭时直接切换。"""
        if not ANIMATIONS:
            set_theme(name)
            self._restyle()
            return
        keys = ("BG", "CARD", "CARD2", "TEXT", "MUTED", "HEAD", "SEL",
                "FIELD", "BORDER", "RING", "OK", "WARN", "GHOST",
                "ACCENT", "DANGER")
        g = globals()
        start = {k: g[k] for k in keys}
        try:
            set_theme(name)
            target = {k: g[k] for k in keys}
            for i in range(1, 7):
                t = i / 6.0
                for k in keys:
                    g[k] = _mix_hex(target[k], start[k], t)
                self._style()
                self._refresh_background()
                self._redraw_glass_tables()
                self._paint_tabs()
                self._refresh_badges()
                try:
                    self.root.update()
                except Exception:
                    pass
        finally:
            set_theme(name)
            self._restyle()

    def preview_personal(self, vals):
        """个性化即时预览(不写配置, 取消时由设置窗口传回旧值还原)。"""
        merged = dict(self.config.settings)
        merged.update(vals or {})
        apply_personal(merged)
        set_theme(merged.get("theme", "light"), merged.get("accent"))
        self._restyle()

    def _refresh_badges(self):
        """状态徽标配色随主题 / 强调色 / 运行状态刷新。"""
        glass = getattr(self, "_glass_active", False)
        canvas_bg = getattr(self, "_panel_color", BG) if glass else BG
        running = "运行中" in getattr(getattr(self, "_badge_state", None), "_ac_text", "")
        for key in ("state", "total"):
            b = getattr(self, "_badge_" + key, None)
            if b is None:
                continue
            try:
                b.configure(bg=canvas_bg)
                b.delete("all")
                w = max(40, int(b.cget("width")) or 90)
                h = max(20, int(b.cget("height")) or 24)
                if key == "state":
                    fill = _mix_hex(OK, BG, 0.20) if running else _mix_hex(ACCENT, BG, 0.10)
                    fg = OK if running else TEXT
                else:
                    fill, fg = _mix_hex(ACCENT, BG, 0.14), ACCENT
                r = min(CURRENT_RADIUS, h // 2)
                _round_pill(b, 0, 0, w, h, r, RING)          # 1px 描边
                _round_pill(b, 1, 1, w - 1, h - 1, max(0, r - 1), fill)
                b.create_text(w / 2.0, h / 2.0, text=getattr(b, "_ac_text", ""),
                              fill=fg, font=("Microsoft YaHei UI", 9, "bold"))
            except Exception:
                pass

    def preview_background(self, name):
        self.config.settings["background"] = name or ""
        self._refresh_background()

    def preview_opacity(self, value):
        """设置里拖动不透明度滑块时即时预览(不写盘, 保存时才落配置)。"""
        self.config.settings["background_opacity"] = value
        self._refresh_background()

    def rebuild_after_settings(self):
        self.engine.rebuild_hotkeys()
        self._refresh_tree()
        self._update_foot()

    def save_and_rebuild(self):
        self.config.save()
        self.engine.rebuild_hotkeys()
        self._reschedule()
        self._refresh_tree()
        self._update_foot()

    # ---- 窗口 / 托盘 ----
    def hide_window(self):
        self.root.withdraw()
        self._notify("已最小化到系统托盘", "双击托盘图标可重新打开界面")

    def show_window(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    def on_close(self):
        if self.config.settings.get("close_action", "tray") == "quit":
            self.quit_app()
        else:
            self.hide_window()

    def _setup_tray(self):
        try:
            import pystray
            from pystray import MenuItem as Item, Menu
        except Exception:
            self.icon = None
            return
        self.pystray, self.MenuItem, self.Menu = pystray, Item, Menu
        self.icon = pystray.Icon("AutoClicker", make_tray_image("idle"), APP_NAME, self._build_menu())
        try:
            self.icon.run_detached()
        except Exception:
            self.icon = None

    def _build_menu(self):
        Menu, Item = self.Menu, self.MenuItem
        items = [Item("显示主界面", self._tray_show, default=True),
                 Item("紧急停止(全部)", self._tray_stop_all), Menu.SEPARATOR]
        for b in self.config.bindings:
            items.append(Item(self._menu_text_factory("基础", b),
                              self._menu_action_factory(b["id"])))
        for f in self.config.flows:
            items.append(Item(self._menu_text_factory("节点", f),
                              self._menu_action_factory(f["id"])))
        items.append(Menu.SEPARATOR)
        items.append(Item("设置…", self._tray_settings))
        items.append(Item("关于", self._tray_about))
        items.append(Item("退出", self._tray_quit))
        return Menu(*items)

    def _menu_text_factory(self, prefix, b):
        iid = b["id"]

        def _text(_item):
            mark = "■ " if self.engine.is_running(iid) else "▶ "
            return "%s[%s] %s  [%s]" % (mark, prefix, b.get("name", ""), hotkey_text(b))
        return _text

    def _menu_action_factory(self, iid):
        def _action(_icon=None, _item=None):
            ui_call(self.engine.toggle, iid)
        return _action

    def _tray_show(self, _i=None, _it=None):
        ui_call(self.show_window)

    def _tray_stop_all(self, _i=None, _it=None):
        ui_call(self.emergency_stop)

    def _tray_quit(self, _i=None, _it=None):
        ui_call(self.quit_app)

    def _tray_settings(self, _i=None, _it=None):
        ui_call(self.show_window)
        ui_call(self.open_settings)

    def _tray_about(self, _i=None, _it=None):
        ui_call(self.show_window)
        ui_call(self.open_about)

    def _sync_tray(self):
        if not self.icon:
            return
        running = len(self.engine.running_ids())
        state = "run" if running else "idle"
        if state != self._tray_state:
            self._tray_state = state
            try:
                self.icon.icon = make_tray_image(state)
            except Exception:
                pass
        try:
            self.icon.title = APP_NAME + (("  ·  运行中 %d 组" % running) if running else "  ·  就绪")
        except Exception:
            pass
        sig = tuple((it["id"], it.get("name"), hotkey_text(it), self.engine.is_running(it["id"]))
                    for _k, it in self.config.items())
        if sig != self._menu_sig:
            self._menu_sig = sig
            try:
                self.icon.menu = self._build_menu()
                self.icon.update_menu()
            except Exception:
                pass

    def _notify(self, msg, title=APP_NAME):
        if self.icon:
            try:
                self.icon.notify(msg, title)
            except Exception:
                pass

    # ---- 主循环 ----
    def request_refresh(self):
        ui_call(self._refresh_tree)

    def _poll(self):
        if self._closing:
            return
        dirty = False
        try:
            while True:
                fn, args, kwargs = UI_QUEUE.get_nowait()
                try:
                    fn(*args, **kwargs)
                    dirty = True
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:
            any_running = bool(self.engine.running_ids())
            if dirty or any_running or self._was_running or self._sched_due:
                self._refresh_tree()
            self._was_running = any_running
        except Exception:
            pass
        self.root.after(250, self._poll)

    def quit_app(self):
        if self._closing:
            return
        self._closing = True
        self.engine.stop_all()
        self.engine.shutdown()
        self.config.save()
        if self.icon:
            try:
                self.icon.visible = False
                self.icon.stop()
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        os._exit(0)


# ---------------------------------------------------------------------------
# 无界面自测
# ---------------------------------------------------------------------------
def run_selftest():
    global CLICK_SINK
    lines = []

    def ck(name, cond, extra=""):
        lines.append(("%s  %s%s" % ("PASS" if cond else "FAIL", name,
                                    ("  -> " + str(extra)) if extra else "")))

    # 1. v2 -> v3 迁移
    v2 = {"version": 2,
          "settings": {"close_to_tray": False, "theme": "dark"},
          "bindings": [{"id": "old1", "name": "旧绑定",
                        "hotkey": {"mods": ["ctrl"], "vk": ord("X")},
                        "actions": [{"type": "mouse", "mouse_button": "right",
                                     "interval_ms": 23, "jitter_ms": 3,
                                     "stop_mode": "infinite"}]}]}
    cfg3 = Config._sanitize(v2)
    ck("v2->v3 版本", cfg3["version"] == 3)
    ck("v2 旧绑定保留", len(cfg3["bindings"]) == 1 and cfg3["bindings"][0]["id"] == "old1")
    ck("v2 close_to_tray 迁移", cfg3["settings"]["close_action"] == "quit")
    ck("v2 自动生成 #1 方案", len(cfg3["presets"]) == 1 and cfg3["settings"]["active_preset"] == 1)
    a = cfg3["bindings"][0]["actions"][0]
    ck("新字段默认值齐全", all(k in a for k in ("rate_mode", "pos_mode", "coord_no", "script", "cps")))

    class FakeCfg(object):
        settings = {"restore_cursor": True, "script_enabled": True}
        coords = [{"no": 1, "name": "P1", "x": 111, "y": 222}]

        def coord_by_no(self, no):
            for c in self.coords:
                if c["no"] == _i(no, 0):
                    return c
            return None

    fcfg = FakeCfg()

    # 2. 频率
    act_ms = default_action()
    act_ms["interval_ms"], act_ms["jitter_ms"] = 100, 20
    ds = [action_delay(act_ms) for _ in range(200)]
    ck("ms 频率 n±m 范围", all(0.079 <= d <= 0.121 for d in ds),
       "min=%.3f max=%.3f" % (min(ds), max(ds)))
    act_cps = default_action()
    act_cps["rate_mode"], act_cps["cps"], act_cps["cps_jitter"] = "cps", 10, 2
    ds = [action_delay(act_cps) for _ in range(200)]
    ck("cps 频率 n±m 范围", all(0.082 <= d <= 0.126 for d in ds),
       "min=%.3f max=%.3f" % (min(ds), max(ds)))

    # 3. 坐标解析
    ck("跟随鼠标返回 None", resolve_pos(default_action(), fcfg) is None)
    fixed = default_action()
    fixed["pos_mode"], fixed["pos"] = "fixed", {"x": 5, "y": 6}
    ck("固定坐标", resolve_pos(fixed, fcfg) == (5, 6))
    ref = default_action()
    ref["pos_mode"], ref["coord_no"] = "coord", 1
    ck("编号坐标引用", resolve_pos(ref, fcfg) == (111, 222))
    ck("按键名解析", parse_key_spec("Ctrl+Shift+F5")["vk"] == 0x74 and
       parse_key_spec("Ctrl+Shift+F5")["mods"] == ["ctrl", "shift"])

    # 4. DSL 语法与执行
    class Api(object):
        def __init__(self):
            self.log = []

        def mouse(self):
            return Point(7, 9)

        def click(self, button, *args):
            self.log.append(("click", button, args))

        def key(self, spec):
            self.log.append(("key", spec))

        def pause(self, ms):
            self.log.append(("pause", ms))

    src = """
    int s = 0, i;
    for (i = 0; i < 5; i++) { s += i; }
    if (s == 10) { left(mouse()); } else { right(); }
    int x = mouse().x;
    int y = point(3, 4).y;
    keyboard("A");
    pause(10);
    """
    api = Api()
    try:
        prog = compile_script(src)
        run_script(prog, api)
        ok = api.log[0] == ("click", "left", (Point(7, 9),))
        ok = ok and api.log[-2:] == [("key", "A"), ("pause", 10)]
        ck("DSL for/if else/属性/内置函数", ok, api.log)
    except Exception as e:
        ck("DSL for/if else/属性/内置函数", False, e)
    ck("DSL 声明热键提取", extract_hotkey(compile_script('hotkey("Ctrl+Alt+J"); left();')) == "Ctrl+Alt+J")
    try:
        compile_script("for (i = 0; i < 3; i++) { left() }")
        ck("DSL 语法错误可捕获", False)
    except ScriptError:
        ck("DSL 语法错误可捕获", True)
    try:
        run_script(compile_script("while (1) { left(); }"), Api(), max_steps=2000)
        ck("DSL 步数上限", False)
    except ScriptError:
        ck("DSL 步数上限", True)

    # 5. 节点顺序循环 (不产生真实点击)
    events = []
    CLICK_SINK = lambda e: events.append(e)
    flow = default_flow()
    flow["rounds"] = 2
    flow["round_interval_ms"] = 0
    flow["nodes"] = []
    for idx, btn in enumerate(("left", "right"), 1):
        n = default_node(idx)
        n["action"]["mouse_button"] = btn
        n["click_count"] = 2
        n["interval_ms"] = 1
        n["node_interval_ms"] = 1
        flow["nodes"].append(n)
    w = ClickWorker("flow", flow, fcfg)
    FlowRunner(w, fcfg).run()
    seq = [e[2] for e in events if e[0] == "action"]
    ck("节点顺序循环 2 轮 × 2 节点 × 2 次", seq == ["left", "left", "right", "right"] * 2, seq)
    ck("节点计数", w.count == 8, w.count)

    # 6. 基础并行: 定量结束
    events = []
    b = default_binding()
    act = default_action()
    act["stop_mode"], act["stop_count"], act["interval_ms"] = "count", 3, 1
    b["actions"] = [act]
    b["repeat"] = 0
    w = ClickWorker("basic", b, fcfg)
    ActionRunner(act, 0, w, fcfg).run()
    ck("基础定量点击 3 次", w.count == 3 and len(events) == 3, w.count)

    # 7. 随机次数窗口
    events = []
    act = default_action()
    act["rate_mode"] = "window"
    act["stop_mode"], act["stop_count"] = "count", 6
    act["window_ms"], act["min_n"], act["max_n"] = 20, 2, 4
    b["actions"] = [act]
    w = ClickWorker("basic", b, fcfg)
    ActionRunner(act, 0, w, fcfg).run()
    ck("随机次数窗口按总次数结束", w.count == 6 and len(events) == 6, w.count)

    # 8. 热键类型 / 去重 / 多键显示
    multi = default_binding()
    multi["hotkey_type"] = "multi"
    multi["hotkey_keys"] = [0x41, 0x42, 0x43, 0x44, 0x45]
    ck("最多 5 键组合显示", hotkey_text(multi).count("+") == 4, hotkey_text(multi))
    ck("多键去重键", hotkey_dedupe(multi) == ("m", (0x41, 0x42, 0x43, 0x44, 0x45)))
    sanitized = _sanitize_hotkey({"hotkey_type": "multi",
                                  "hotkey_keys": [0x41] * 3 + [0x42, 0x43, 0x44, 0x45, 0x46]},
                                 b["hotkey"])
    ck("多键录制上限 5 且去重", len(sanitized["hotkey_keys"]) == 5, sanitized["hotkey_keys"])
    ck("不绑定热键", hotkey_dedupe({"hotkey_type": "none"}) is None)

    # 9. 定时启动
    now = time.time()
    ck("定时未启用返回 None", schedule_due(default_schedule(), now) is None)
    d = schedule_due({"enabled": True, "mode": "countdown", "delay_s": 30}, now)
    ck("倒计时到期时间", abs(d - (now + 30)) < 0.01, d - now)
    d2 = schedule_due({"enabled": True, "mode": "daily", "at": "00:00"}, now)
    ck("每日时刻顺延到次日", d2 > now and d2 <= now + 86400, d2 - now)

    # 10. 方案快照独立性
    b["name"] = "改名之前"
    p = default_preset(1, "快照", [b], [default_flow()], [default_coord(1, "P", 1, 2)])
    b["name"] = "改名之后"
    ck("方案是深拷贝快照", p["bindings"][0]["name"] == "改名之前")
    ck("方案含绑定/流程/坐标", len(p["bindings"]) == 1 and len(p["flows"]) == 1 and len(p["coords"]) == 1)

    # 11. 脚本安全开关
    class NoScriptCfg(FakeCfg):
        settings = {"restore_cursor": True, "script_enabled": False}

    sa = default_action()
    sa["type"] = "script"
    sa["script"] = "left();"
    w = ClickWorker("basic", b, NoScriptCfg())
    try:
        run_script_action(sa, NoScriptCfg(), w)
        ck("脚本关闭时拒绝运行", False)
    except ScriptError:
        ck("脚本关闭时拒绝运行", True)

    # 12. DSL while / break / continue / 逻辑运算 / rand 范围
    api2 = Api()
    src2 = """
    int i = 0, t = 0;
    while (1) {
        i++;
        if (i > 20) { break; }
        if (i % 2 == 0) { continue; }
        t += i;
    }
    int r = rand(7);
    if (r >= 10000 && r <= 100000 && t == 100) { middle(); }
    """
    try:
        run_script(compile_script(src2), api2)
        ck("DSL while/break/continue/逻辑/rand",
           any(e[0] == "click" and e[1] == "middle" for e in api2.log), api2.log)
    except Exception as e:
        ck("DSL while/break/continue/逻辑/rand", False, e)

    # 13. 节点循环的整组结束条件(总次数)
    events = []
    flow = default_flow()
    flow["rounds"] = 0
    flow["stop_mode"] = "count"
    flow["stop_count"] = 3
    flow["nodes"] = [default_node()]
    flow["nodes"][0]["click_count"] = 1
    flow["nodes"][0]["node_interval_ms"] = 0
    w = ClickWorker("flow", flow, fcfg)
    FlowRunner(w, fcfg).run()
    ck("流程按总次数结束", w.count == 3, w.count)

    # 14. 单轮时长到时结束本节点
    events = []
    flow = default_flow()
    flow["rounds"] = 1
    flow["stop_mode"] = "infinite"
    n1 = default_node()
    n1["click_count"] = 1000
    n1["interval_ms"] = 1
    n1["round_duration_ms"] = 30
    n1["node_interval_ms"] = 0
    n1["action"]["mouse_button"] = "left"
    n2 = default_node(2)
    n2["click_count"] = 2
    n2["interval_ms"] = 1
    n2["node_interval_ms"] = 0
    n2["action"]["mouse_button"] = "right"
    flow["nodes"] = [n1, n2]
    w = ClickWorker("flow", flow, fcfg)
    FlowRunner(w, fcfg).run()
    seq = [e[2] for e in events if e[0] == "action"]
    ck("单轮时长截断节点并进入下一节点",
       seq and seq[-2:] == ["right", "right"] and seq.count("left") < 1000,
       "left=%d right=%d" % (seq.count("left"), seq.count("right")))

    # 15. v1.4.5: 背景不透明度配置与最新版本链接
    c4 = Config._sanitize({"version": 3, "settings": {"background_opacity": 500}})
    ck("背景不透明度上限钳制", c4["settings"]["background_opacity"] == 90,
       c4["settings"]["background_opacity"])
    c5 = Config._sanitize({"version": 3, "settings": {"background_opacity": 0}})
    ck("背景不透明度下限钳制", c5["settings"]["background_opacity"] == 10,
       c5["settings"]["background_opacity"])
    ck("默认背景不透明度", default_config()["settings"]["background_opacity"] == 45)
    ck("最新版本链接指向 releases/latest",
       LATEST_URL == PROJECT_URL + "/releases/latest", LATEST_URL)

    # 16. 引擎 stop_all 保留计数
    cfg = Config()
    eng = Engine(cfg)
    cfg.bindings = [b]
    eng.start(b["id"])
    time.sleep(0.05)
    before = eng.click_count(b["id"])
    eng.stop_all()
    ck("stop_all 后仍显示上次计数", eng.click_count(b["id"]) >= before and before >= 0,
       "%d -> %d" % (before, eng.click_count(b["id"])))
    eng.shutdown()
    CLICK_SINK = None

    # 17. v1.4.6: 主表格为 Canvas 自绘, 且保持 Treeview 兼容 API
    # 注意: 不能用 inspect.getsource —— PyInstaller 打包后源文件不存在,
    #       会抛 OSError 让 --selftest 崩溃。改用字节码 co_names。
    _apis = ("heading", "column", "insert", "item", "delete", "exists",
             "get_children", "selection", "selection_set", "tag_configure",
             "yview", "bind", "configure", "redraw_now")
    ck("GlassTable 具备 Treeview 兼容 API",
       all(hasattr(GlassTable, a) for a in _apis),
       [a for a in _apis if not hasattr(GlassTable, a)])
    _names = GlassTable._redraw.__code__.co_names
    ck("GlassTable 底图与文字均由 Canvas 绘制",
       "create_image" in _names and "create_text" in _names)
    ck("GlassTable 行带为半透明叠加而非纯色",
       "_rgba" in _names and "alpha_composite" in _names)

    # 18. v1.5.0: 个性化配置 / 强调色 / 动态切换状态
    _pers = ("accent", "density", "radius", "animations", "badges")
    ck("默认个性化字段齐全",
       all(k in default_config()["settings"] for k in _pers))
    c6 = Config._sanitize({"version": 3, "settings": {
        "accent": "not-a-color", "density": "huge", "radius": 999}})
    ck("非法强调色回落 blue", c6["settings"]["accent"] == "blue")
    ck("非法密度回落 comfortable", c6["settings"]["density"] == "comfortable")
    ck("圆角上限钳制 18", c6["settings"]["radius"] == 18)
    ck("强调色方案齐全",
       all(k in ACCENTS for k in ("blue", "violet", "teal", "rose", "amber")))
    t1 = set_theme("dark", "teal")
    dark_accent = ACCENT
    set_theme("light", "teal")
    ck("暗色强调色被提亮", t1 == "dark" and dark_accent != ACCENTS["teal"][0])
    ck("亮色强调色取原值", ACCENT == ACCENTS["teal"][0])
    apply_personal({"density": "compact", "radius": 6,
                    "animations": False, "badges": False})
    ck("紧凑密度改变表格行高", GlassTable.ROW_H == 24, GlassTable.ROW_H)
    ck("动画/徽标开关写入全局", ANIMATIONS is False and BADGES is False)
    apply_personal({"density": "comfortable", "radius": 10,
                    "animations": True, "badges": True})
    set_theme("light", "blue")

    failed = [x for x in lines if x.startswith("FAIL")]
    text = "AutoClicker v%s selftest  %s\n%s\n\n%s\n" % (
        APP_VERSION, time.strftime("%Y-%m-%d %H:%M:%S"),
        "-" * 60, "\n".join(lines))
    text += "\n%d passed, %d failed\n" % (len(lines) - len(failed), len(failed))
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "selftest.txt"), "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    try:
        sys.stdout.write(text)
    except Exception:
        pass
    return 0 if not failed else 1


# ---------------------------------------------------------------------------
def main():
    if "--selftest" in sys.argv[1:]:
        sys.exit(run_selftest())

    kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if kernel32.GetLastError() == 183:
        user32.MessageBoxW(None, "AutoClicker 已经在运行了，请查看系统托盘。",
                           APP_NAME, 0x00000040)
        return

    config = Config()
    config.save()
    try:
        os.makedirs(PICTURES_DIR, exist_ok=True)
    except Exception:
        pass
    try:
        want = bool(config.settings.get("autostart", False))
        if want != bool(autostart_get()):
            autostart_set(want, hidden=bool(config.settings.get("start_hidden", False)))
    except Exception:
        pass
    engine = Engine(config)

    root = tk.Tk()
    try:
        from PIL import ImageTk
        _img = ImageTk.PhotoImage(make_tray_image("idle"))
        root._ac_icon = _img
        root.iconphoto(True, _img)
    except Exception:
        pass
    app = App(root, config, engine)
    if bool(config.settings.get("start_hidden", False)) or ("--hidden" in sys.argv[1:]):
        app.hide_window()
    root.mainloop()


if __name__ == "__main__":
    main()
