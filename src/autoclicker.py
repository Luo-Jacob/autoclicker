# -*- coding: utf-8 -*-
"""
AutoClicker —— 物理级鼠标 / 键盘连点器

由 Luo-Jacob 制作 · https://github.com/Luo-Jacob/autoclicker
MIT License · Copyright (c) 2026 Luo-Jacob

特性:
  1. 使用 Win32 SendInput 在当前光标位置模拟真实按下/释放(非窗口消息投递)
  2. 一个热键可挂载多个动作: 按下热键后这些动作同时开始, 各自并行重复
  3. 每个动作独立配置间隔、随机波动、按下保持、重复次数
  4. 全局热键基于 RegisterHotKey, 程序不在前台同样响应
  5. 多组绑定, 各自独立热键与动作配置, 互不干扰
  6. 配置持久化到程序目录下 AutoClicker/config.json, 启动加载 / 修改即存
  7. 纯 GUI + 系统托盘, 无控制台窗口; 提供全局急停热键 / 托盘菜单 / 界面按钮三重急停
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

APP_NAME = "AutoClicker"
APP_VERSION = "1.2.0"
MUTEX_NAME = "Global\\AutoClicker_SingleInstance"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# ---------------------------------------------------------------------------
# 无控制台环境兜底(noconsole 打包时 sys.stdout 可能为 None)
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# 路径与配置
# ---------------------------------------------------------------------------
def base_dir():
    """程序所在目录(打包后为 exe 所在目录)。"""
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
# 旧版本配置目录, 仅用于首次启动时迁移
LEGACY_CONFIG_FILES = [
    os.path.join(base_dir(), "AutoClicker", "config.json"),
]
PROJECT_URL = "https://github.com/Luo-Jacob/autoclicker"
AUTHOR = "Luo-Jacob"

MOD_CTRL, MOD_ALT, MOD_SHIFT, MOD_WIN = 0x0002, 0x0001, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
MODS_ORDER = ("ctrl", "alt", "shift", "win")


def new_id():
    return uuid.uuid4().hex[:10]


def default_action(index=1):
    """一个点击动作: 一次具体的点击 / 按键(多个动作之间并行执行)。"""
    return {
        "id": new_id(),
        "type": "mouse",             # mouse | keyboard
        "mouse_button": "left",      # left | middle | right
        "kbd": {"mods": [], "vk": 0x74},   # F5
        "interval_ms": 100,          # 本动作执行后的等待
        "jitter_ms": 20,
        "hold_ms": 0,
        "repeat": 0,                 # 本动作连续执行次数, 0 = 不限(一直执行本步)
    }


def default_binding(index=1):
    """一个绑定 = 一个热键 + 一组同时执行的点击动作。"""
    return {
        "id": new_id(),
        "name": "绑定 %d" % index,
        "enabled": True,
        "hotkey": {"mods": ["ctrl", "alt"], "vk": ord("X")},
        "actions": [default_action()],
        "repeat": 0,                 # 整组总动作次数上限, 0 = 无限
        "loop": True,                # 一轮跑完后是否循环
    }


def default_config():
    return {
        "version": 2,
        "settings": {
            "emergency_hotkey": {"mods": ["ctrl", "alt"], "vk": ord("K")},
            "panic_fallback": True,   # 始终启用 Ctrl+Alt+F12 备用急停
            "close_action": "tray",   # tray = 关窗最小化到托盘 | quit = 关窗直接退出程序
            "start_hidden": False,    # 启动后不显示主界面, 仅托盘
            "autostart": False,       # 开机自动启动(注册表 Run)
            "theme": "light",         # light | dark
            "background": "",         # 界面背景图文件名(位于 AClib/pictures), 空=无背景
        },
        "bindings": [],
    }


class Config(object):
    def __init__(self):
        self._lock = threading.RLock()
        self.data = default_config()
        self.load()

    # ---- 读写 ----
    def load(self):
        try:
            path = CONFIG_FILE
            if not os.path.isfile(path):
                # 迁移旧版本配置(AutoClicker/config.json)
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
        if isinstance(st, dict):
            s = cfg["settings"]
            for k in list(s.keys()):
                if k in st:
                    s[k] = st[k]
            s["emergency_hotkey"] = _sanitize_combo(
                st.get("emergency_hotkey"), s["emergency_hotkey"])
            # v1 -> v2: 旧的 close_to_tray 布尔换成 close_action
            if "close_action" not in st and "close_to_tray" in st:
                s["close_action"] = "tray" if st.get("close_to_tray") else "quit"
            if s.get("close_action") not in ("tray", "quit"):
                s["close_action"] = "tray"
            if s.get("theme") not in ("light", "dark"):
                s["theme"] = "light"
            s["background"] = str(s.get("background") or "")
            s["autostart"] = bool(s.get("autostart"))
            s["panic_fallback"] = bool(s.get("panic_fallback", True))
            s["start_hidden"] = bool(s.get("start_hidden", False))
        bl = raw.get("bindings")
        if isinstance(bl, list):
            for item in bl:
                if not isinstance(item, dict):
                    continue
                b = default_binding()
                if isinstance(item.get("id"), str) and item["id"]:
                    b["id"] = item["id"]
                if "name" in item:
                    b["name"] = item["name"]
                b["hotkey"] = _sanitize_combo(item.get("hotkey"), b["hotkey"])
                acts = item.get("actions")
                if isinstance(acts, list) and acts:
                    # 新格式: 动作序列
                    b["actions"] = [_sanitize_action(a) for a in acts]
                    if "repeat" in item:
                        try:
                            b["repeat"] = max(0, int(item["repeat"]))
                        except Exception:
                            b["repeat"] = 0
                else:
                    # 旧格式(单动作) -> 迁移成只有一步的序列
                    legacy = dict(item)
                    legacy.pop("id", None)
                    if "action" in legacy:
                        legacy["type"] = legacy["action"]
                    b["actions"] = [_sanitize_action(legacy)]
                    try:
                        b["repeat"] = max(0, int(item.get("repeat", 0)))
                    except Exception:
                        b["repeat"] = 0
                if "loop" in item:
                    b["loop"] = bool(item["loop"])
                b["enabled"] = bool(item.get("enabled", True))
                cfg["bindings"].append(b)
        return cfg

    @staticmethod
    def _sanitize_action(a):
        return _sanitize_action(a)

    # ---- 便捷访问 ----
    @property
    def settings(self):
        return self.data["settings"]

    @property
    def bindings(self):
        return self.data["bindings"]

    def binding_by_id(self, bid):
        for b in self.bindings:
            if b["id"] == bid:
                return b
        return None


def _sanitize_combo(combo, fallback):
    if not isinstance(combo, dict):
        return dict(fallback)
    mods = [m for m in combo.get("mods", []) if m in MODS_ORDER]
    try:
        vk = int(combo.get("vk"))
    except Exception:
        vk = int(fallback["vk"])
    if vk <= 0 or vk > 0xFE:
        vk = int(fallback["vk"])
    return {"mods": mods, "vk": vk}


def _sanitize_action(a, fallback=None):
    act = default_action() if fallback is None else dict(fallback)
    if not isinstance(a, dict):
        return act
    if isinstance(a.get("id"), str) and a["id"]:
        act["id"] = a["id"]
    if "type" in a:
        act["type"] = a["type"]
    if "mouse_button" in a:
        act["mouse_button"] = a["mouse_button"]
    for k in ("interval_ms", "jitter_ms", "hold_ms", "repeat"):
        if k in a:
            try:
                act[k] = max(0, int(a[k]))
            except Exception:
                act[k] = 0
    act["kbd"] = _sanitize_combo(a.get("kbd"), act.get("kbd") or {"mods": [], "vk": 0x74})
    if act.get("type") not in ("mouse", "keyboard"):
        act["type"] = "mouse"
    if act.get("mouse_button") not in ("left", "middle", "right"):
        act["mouse_button"] = "left"
    act["interval_ms"] = max(1, int(act.get("interval_ms", 100)))
    if not act.get("id"):
        act["id"] = new_id()
    return act


def action_label(act):
    """动作的一句话摘要。"""
    if act.get("type") == "keyboard":
        return "键盘 " + fmt_combo(act.get("kbd"))
    return {"left": "鼠标左键", "middle": "鼠标中键", "right": "鼠标右键"}.get(
        act.get("mouse_button", "left"), "鼠标左键")


def action_detail(act):
    """带节奏参数的动作摘要。"""
    n = int(act.get("repeat", 0))
    txt = action_label(act)
    if n:
        txt += " ×%d" % n
    jit = int(act.get("jitter_ms", 0))
    txt += " · %dms" % int(act.get("interval_ms", 100))
    if jit:
        txt += "±%d" % jit
    return txt


def binding_rhythm(binding, limit=2):
    """绑定的节奏摘要(各步间隔)。"""
    acts = binding.get("actions") or []
    if not acts:
        return "—"
    parts = []
    for a in acts[:limit]:
        jit = int(a.get("jitter_ms", 0))
        t = "%d" % int(a.get("interval_ms", 100))
        if jit:
            t += "±%d" % jit
        parts.append(t)
    txt = " / ".join(parts)
    if len(acts) > limit:
        txt += " …"
    return txt


def binding_summary(binding, limit=3):
    """绑定的动作摘要(多个动作是同时执行的, 用 + 连接)。"""
    acts = binding.get("actions") or []
    if not acts:
        return "（无动作）"
    parts = [action_label(a) for a in acts]
    if len(parts) <= limit:
        return " + ".join(parts)
    return " + ".join(parts[:limit]) + " + …共%d项" % len(parts)


# ---------------------------------------------------------------------------
# 虚拟键 -> 显示名
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


MOD_DISPLAY = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}


def combo_mods(combo):
    if not combo:
        return 0
    m = 0
    for name in combo.get("mods", []):
        if name == "ctrl":
            m |= MOD_CTRL
        elif name == "alt":
            m |= MOD_ALT
        elif name == "shift":
            m |= MOD_SHIFT
        elif name == "win":
            m |= MOD_WIN
    return m


def fmt_combo(combo):
    if not combo:
        return "未设置"
    parts = [MOD_DISPLAY[m] for m in MODS_ORDER if m in combo.get("mods", [])]
    parts.append(vk_name(combo.get("vk")))
    return "+".join(parts)


def combo_key(combo):
    """热键唯一标识, 用于查重。"""
    if not combo:
        return None
    return tuple(sorted(combo.get("mods", []))) + (int(combo.get("vk", 0)),)


# ---------------------------------------------------------------------------
# SendInput —— 真实物理输入模拟
# ---------------------------------------------------------------------------
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MAP = {
    "left": (0x0002, 0x0004),
    "middle": (0x0020, 0x0040),
    "right": (0x0008, 0x0010),
}

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001


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


def mouse_click(button="left", hold_ms=0):
    """在当前光标位置按下并释放(不移动光标)。"""
    down, up = MOUSEEVENTF_MAP.get(button, MOUSEEVENTF_MAP["left"])
    _send([_mouse_input(down)])
    if hold_ms and hold_ms > 0:
        time.sleep(hold_ms / 1000.0)
    _send([_mouse_input(up)])


def keyboard_tap(combo):
    """按下并释放一组按键(支持修饰键组合)。"""
    mods = [vk for vk in _mod_vks(combo.get("mods", []))]
    vk = int(combo.get("vk", 0))
    seq = [_key_input(m, False) for m in mods]
    seq.append(_key_input(vk, False))
    seq.append(_key_input(vk, True))
    seq.extend([_key_input(m, True) for m in reversed(mods)])
    _send(seq)


def _mod_vks(mods):
    out = []
    for m in mods:
        if m == "ctrl":
            out.append(0x11)
        elif m == "alt":
            out.append(0x12)
        elif m == "shift":
            out.append(0x10)
        elif m == "win":
            out.append(0x5B)
    return out


# ---------------------------------------------------------------------------
# 全局热键: RegisterHotKey + 独立消息循环线程
# ---------------------------------------------------------------------------
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_APP_REFRESH = 0x8001


class HotkeyManager(threading.Thread):
    """在后台线程中维护 RegisterHotKey 注册并分发回调。"""

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True
        self.name = "HotkeyThread"
        self._specs = []          # [(ref_key, mods, vk, callback)]
        self._active = {}         # hotkey_id -> callback
        self._by_ref = {}         # ref_key -> hotkey_id
        self._next_id = 1
        self._tid = None
        self._ready = threading.Event()
        self._lock = threading.RLock()
        self.errors = []
        self.start()
        self._ready.wait(5)

    # ---- 线程主循环 ----
    def run(self):
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # 创建消息队列
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

    # ---- 对外接口(可在任意线程调用) ----
    def update(self, specs):
        """specs: [(ref_key, combo_dict, callback), ...]"""
        with self._lock:
            self._specs = specs
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

    # ---- 注册逻辑(必须在热键线程内执行) ----
    def _unregister_all(self):
        for hid in list(self._active.keys()):
            try:
                user32.UnregisterHotKey(None, hid)
            except Exception:
                pass
        self._active.clear()
        self._by_ref.clear()

    def _apply(self):
        with self._lock:
            specs = list(self._specs)
        self._unregister_all()
        errs = []
        for ref, combo, cb in specs:
            if not combo:
                continue
            mods = combo_mods(combo)
            vk = int(combo.get("vk", 0))
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
                self._by_ref[ref] = hid
            else:
                errs.append(fmt_combo(combo))
        self.errors = errs


# ---------------------------------------------------------------------------
# 连点工作线程
# ---------------------------------------------------------------------------
class ActionRunner(threading.Thread):
    """绑定中的一个动作: 按自己的节奏独立重复。多个动作之间是同时开始、并行执行的。"""

    def __init__(self, act, index, worker):
        threading.Thread.__init__(self)
        self.daemon = True
        self.name = "ActionRunner-%d" % index
        self.act = act or {}
        self.worker = worker

    def run(self):
        w = self.worker
        act = self.act
        repeat = max(0, int(act.get("repeat", 0)))          # 0 = 一直重复到停止
        interval = max(1, int(act.get("interval_ms", 100)))
        jitter = max(0, int(act.get("jitter_ms", 0)))
        hold = max(0, int(act.get("hold_ms", 0)))
        done = 0
        while (repeat == 0 or done < repeat) and not w.stop_event.is_set():
            try:
                if act.get("type") == "keyboard":
                    keyboard_tap(act.get("kbd") or {})
                else:
                    mouse_click(act.get("mouse_button", "left"), hold)
            except Exception:
                pass
            done += 1
            if w.tick():          # 达到整组总次数上限
                break
            if repeat and done >= repeat:
                break
            delay = interval / 1000.0
            if jitter:
                delay += random.uniform(-jitter, jitter) / 1000.0
            if delay < 0.001:
                delay = 0.001
            if w.stop_event.wait(delay):
                break


class ClickWorker(threading.Thread):
    """一个绑定: 按下热键后, 该绑定下的所有动作同时开始、各自并行重复。"""

    def __init__(self, binding, on_stop=None):
        threading.Thread.__init__(self)
        self.daemon = True
        self.name = "ClickWorker-%s" % binding["id"]
        self.binding = binding
        self.on_stop = on_stop
        self.stop_event = threading.Event()
        self.count = 0
        self._count_lock = threading.Lock()
        self._total = max(0, int(binding.get("repeat", 0)))
        self._runners = []

    def tick(self):
        """计数 + 检查总次数上限; 返回 True 表示应停止。"""
        with self._count_lock:
            self.count += 1
            if self._total and self.count >= self._total:
                self.stop_event.set()
                return True
        return False

    def run(self):
        b = self.binding
        acts = b.get("actions") or []
        if not acts:
            self._fire_stop()
            return
        self._runners = [ActionRunner(a, i, self) for i, a in enumerate(acts)]
        for r in self._runners:
            r.start()
        for r in self._runners:
            try:
                r.join()
            except Exception:
                pass
        self._fire_stop()

    def _fire_stop(self):
        if self.on_stop:
            try:
                self.on_stop(self.binding["id"])
            except Exception:
                pass

    def stop(self):
        self.stop_event.set()


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
class Engine(object):
    def __init__(self, config):
        global ENGINE
        ENGINE = self
        self.config = config
        self.hotkeys = HotkeyManager()
        self._workers = {}
        self._last_count = {}          # 上一次运行的点击总数(停止后仍可显示)
        self._lock = threading.RLock()
        self.on_change = lambda: None      # 状态变化回调(UI 更新)
        self.on_error = lambda msg: None

    # ---- 状态 ----
    def is_running(self, bid):
        with self._lock:
            w = self._workers.get(bid)
            return bool(w and w.is_alive())

    def click_count(self, bid):
        with self._lock:
            w = self._workers.get(bid)
            if w is not None:
                return w.count
            return int(self._last_count.get(bid, 0))

    def running_ids(self):
        with self._lock:
            return [k for k, v in self._workers.items() if v.is_alive()]

    # ---- 控制 ----
    def start(self, bid):
        b = self.config.binding_by_id(bid)
        if not b:
            return False
        with self._lock:
            old = self._workers.get(bid)
            if old and old.is_alive():
                return True
            self._last_count.pop(bid, None)
            w = ClickWorker(b, on_stop=self._worker_finished)
            self._workers[bid] = w
            w.start()
        self.on_change()
        return True

    def stop(self, bid):
        with self._lock:
            w = self._workers.pop(bid, None)
            if w is not None:
                self._last_count[bid] = w.count
        if w:
            w.stop()
        self.on_change()

    def toggle(self, bid):
        if self.is_running(bid):
            self.stop(bid)
        else:
            self.start(bid)

    def stop_all(self):
        with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for w in workers:
            w.stop()
        self.on_change()

    def _worker_finished(self, bid):
        with self._lock:
            w = self._workers.get(bid)
            if w and w is threading.current_thread():
                self._workers.pop(bid, None)
                self._last_count[bid] = w.count
        self.on_change()

    # ---- 热键注册 ----
    def rebuild_hotkeys(self):
        specs = []
        cfg = self.config
        for b in cfg.bindings:
            if not b.get("enabled", True):
                continue
            combo = b.get("hotkey")
            if not combo:
                continue
            specs.append(("bind:" + b["id"], combo, self._mk_toggle(b["id"])))

        em = cfg.settings.get("emergency_hotkey")
        if em:
            specs.append(("emergency", em, self._mk_emergency()))
        if cfg.settings.get("panic_fallback", True):
            fallback = {"mods": ["ctrl", "alt"], "vk": 0x7B}  # F12
            if combo_key(em) != combo_key(fallback):
                specs.append(("panic_fallback", fallback, self._mk_emergency()))

        # 同一组合只保留第一个, 其余标记为冲突
        seen = set()
        unique = []
        for ref, combo, cb in specs:
            k = combo_key(combo)
            if k in seen:
                continue
            seen.add(k)
            unique.append((ref, combo, cb))
        self.hotkeys.update(unique)

    def _mk_toggle(self, bid):
        def _cb():
            self.toggle(bid)
        return _cb

    def _mk_emergency(self):
        def _cb():
            self.stop_all()
        return _cb

    def suspend(self):
        """录制按键期间临时注销全部热键。"""
        self.hotkeys.update([])

    def resume(self):
        self.rebuild_hotkeys()


# ---------------------------------------------------------------------------
# 键位录制(pynput 低层钩子, 仅录制期间启用)
# ---------------------------------------------------------------------------
from pynput import keyboard as _pkb  # noqa: E402

_PKB_MODS = {
    _pkb.Key.ctrl: "ctrl", _pkb.Key.ctrl_l: "ctrl", _pkb.Key.ctrl_r: "ctrl",
    _pkb.Key.alt: "alt", _pkb.Key.alt_l: "alt", _pkb.Key.alt_r: "alt",
    _pkb.Key.alt_gr: "alt",
    _pkb.Key.shift: "shift", _pkb.Key.shift_l: "shift", _pkb.Key.shift_r: "shift",
    _pkb.Key.cmd: "win", _pkb.Key.cmd_l: "win", _pkb.Key.cmd_r: "win",
}


def _pkb_vk(key):
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


ENGINE = None  # 全局引擎引用(录制期间用于暂停热键)


class Recorder(object):
    """录制一个按键组合。"""

    def __init__(self, on_update):
        self.on_update = on_update
        self.mods = set()
        self.combo = None
        self._listener = None

    def start(self):
        if ENGINE:
            try:
                ENGINE.suspend()
            except Exception:
                pass
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
            try:
                ENGINE.resume()
            except Exception:
                pass

    def clear(self):
        self.mods.clear()
        self.combo = None
        self.on_update(None)

    def _press(self, key):
        if key == _pkb.Key.alt_gr:
            self.mods.update(("ctrl", "alt"))
            self.on_update(self._build(None))
            return
        name = _PKB_MODS.get(key)
        if name:
            self.mods.add(name)
            self.on_update(self._build(None))
            return
        vk = _pkb_vk(key)
        if not vk:
            return
        self.combo = self._build(vk)
        self.on_update(self.combo)

    def _release(self, key):
        if key == _pkb.Key.alt_gr:
            self.mods.discard("ctrl")
            self.mods.discard("alt")
            return
        name = _PKB_MODS.get(key)
        if name and name in self.mods:
            self.mods.discard(name)

    def _build(self, vk):
        mods = [m for m in MODS_ORDER if m in self.mods]
        return {"mods": mods, "vk": int(vk) if vk else 0}


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
    # 鼠标外形
    d.ellipse([22, 12, 42, 50], fill=(255, 255, 255, 255))
    d.line([32, 13, 32, 27], fill=color, width=2)
    d.ellipse([30, 17, 34, 23], fill=color)
    # 点击波纹
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
    d.arc([48, 104, 96, 152], start=60, end=300, fill=(255, 255, 255, 255), width=10)
    d.arc([160, 104, 208, 152], start=240, end=120, fill=(255, 255, 255, 255), width=10)
    img.save(path, format="ICO", sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
import tkinter as tk  # noqa: E402
from tkinter import ttk, messagebox  # noqa: E402

# ---------------------------------------------------------------------------
# 主题配色(亮 / 暗)
# ---------------------------------------------------------------------------
THEMES = {
    "light": {
        "BG": "#f4f6f9", "CARD": "#ffffff", "TEXT": "#1f2937", "MUTED": "#6b7280",
        "ACCENT": "#2563eb", "DANGER": "#dc2626", "HEAD": "#e5e7eb", "SEL": "#dbeafe",
        "FIELD": "#ffffff", "BORDER": "#d1d5db",
    },
    "dark": {
        "BG": "#1b1e24", "CARD": "#252a32", "TEXT": "#e6e9ef", "MUTED": "#98a1b0",
        "ACCENT": "#5b93ff", "DANGER": "#ef4444", "HEAD": "#31363f", "SEL": "#33415c",
        "FIELD": "#2b3038", "BORDER": "#3c4453",
    },
}

# 当前生效的颜色(切换主题时会被整体替换)
_c = THEMES["light"]
BG = _c["BG"]
CARD = _c["CARD"]
TEXT = _c["TEXT"]
MUTED = _c["MUTED"]
ACCENT = _c["ACCENT"]
DANGER = _c["DANGER"]
HEAD = _c["HEAD"]
SEL = _c["SEL"]
FIELD = _c["FIELD"]
BORDER = _c["BORDER"]
CURRENT_THEME = "light"


def _hex_rgb(h):
    h = (h or "#000000").lstrip("#")
    if len(h) != 6:
        h = "000000"
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def _mix_hex(c1, c2, ratio=0.5):
    """把 c1 按 ratio 混入 c2(结果偏 c2)。"""
    a, b = _hex_rgb(c1), _hex_rgb(c2)
    return _rgb_hex(tuple(a[i] * ratio + b[i] * (1 - ratio) for i in range(3)))


def set_theme(name):
    """切换主题: 更新模块级颜色变量, 已创建的控件由 apply_theme() 刷新。"""
    global _c, BG, CARD, TEXT, MUTED, ACCENT, DANGER, HEAD, SEL, FIELD, BORDER, CURRENT_THEME
    _c = THEMES.get(name, THEMES["light"])
    BG = _c["BG"]
    CARD = _c["CARD"]
    TEXT = _c["TEXT"]
    MUTED = _c["MUTED"]
    ACCENT = _c["ACCENT"]
    DANGER = _c["DANGER"]
    HEAD = _c["HEAD"]
    SEL = _c["SEL"]
    FIELD = _c["FIELD"]
    BORDER = _c["BORDER"]
    CURRENT_THEME = name if name in THEMES else "light"
    return CURRENT_THEME


def apply_theme(root):
    """把当前主题应用到 ttk 样式与已存在的窗口。"""
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
    st.configure("TLabelframe", background=BG, bordercolor=BORDER)
    st.configure("TLabelframe.Label", background=BG, foreground=TEXT)
    st.configure("TButton", padding=(10, 6), background=CARD, foreground=TEXT,
                 bordercolor=BORDER, lightcolor=CARD, darkcolor=CARD)
    st.map("TButton", background=[("active", HEAD), ("disabled", BG)],
           foreground=[("disabled", MUTED)])
    st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                 borderwidth=0, padding=(12, 6))
    st.map("Accent.TButton", background=[("active", ACCENT)])
    st.configure("Danger.TButton", background=DANGER, foreground="#ffffff",
                 borderwidth=0, padding=(14, 8))
    st.map("Danger.TButton", background=[("active", DANGER)])
    st.configure("TCheckbutton", background=BG, foreground=TEXT, indicatorcolor=FIELD)
    st.map("TCheckbutton", background=[("active", BG)],
           indicatorcolor=[("selected", ACCENT)])
    st.configure("TRadiobutton", background=BG, foreground=TEXT, indicatorcolor=FIELD)
    st.map("TRadiobutton", background=[("active", BG)],
           indicatorcolor=[("selected", ACCENT)])
    st.configure("TEntry", fieldbackground=FIELD, foreground=TEXT,
                 bordercolor=BORDER, insertcolor=TEXT)
    st.configure("TSpinbox", fieldbackground=FIELD, foreground=TEXT,
                 bordercolor=BORDER, background=FIELD, arrowcolor=TEXT)
    st.configure("TCombobox", fieldbackground=FIELD, foreground=TEXT,
                 background=FIELD, bordercolor=BORDER, arrowcolor=TEXT)
    st.configure("TNotebook", background=BG, bordercolor=BORDER)
    st.configure("TNotebook.Tab", background=HEAD, foreground=TEXT, padding=(12, 6))
    st.map("TNotebook.Tab", background=[("selected", CARD)])
    st.configure("TSeparator", background=BORDER)
    st.configure("TScrollbar", background=BG, troughcolor=BG, bordercolor=BORDER,
                 arrowcolor=TEXT)
    st.configure("Treeview", background=CARD, fieldbackground=CARD,
                 foreground=TEXT, rowheight=28, borderwidth=0, bordercolor=BORDER)
    st.configure("Treeview.Heading", background=HEAD, foreground=TEXT,
                 font=("Microsoft YaHei UI", 9, "bold"), bordercolor=BORDER)
    st.map("Treeview", background=[("selected", SEL)], foreground=[("selected", TEXT)])

    # 下拉框弹出的列表(不属于 ttk 样式, 要用 option 数据库)
    try:
        root.option_add("*TCombobox*Listbox.background", FIELD)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", SEL)
        root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    except Exception:
        pass

    def paint(w):
        try:
            if isinstance(w, tk.Toplevel) or isinstance(w, tk.Tk):
                w.configure(bg=BG)
        except Exception:
            pass

    try:
        paint(root)
        for w in root.winfo_children():
            paint(w)
    except Exception:
        pass

UI_QUEUE = queue.Queue()


def ui_call(fn, *args, **kwargs):
    UI_QUEUE.put((fn, args, kwargs))


class RecordDialog(object):
    """录制按键组合的模态对话框。"""

    def __init__(self, parent, title, initial=None, allow_single=True):
        self.initial = initial
        self.result = initial
        self.allow_single = allow_single
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.configure(bg=BG)
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.grab_set()
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        frm = ttk.Frame(self.top, padding=18)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text=title, font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        ttk.Label(frm, text="请按下需要的组合键(例如 Ctrl+Alt+X), 松开后点击「确定」。",
                  foreground=MUTED).pack(anchor="w", pady=(4, 10))

        self.var = tk.StringVar(value=fmt_combo(initial))
        self.big = tk.Label(frm, textvariable=self.var, font=("Consolas", 16, "bold"),
                            bg=CARD, fg=ACCENT, relief="solid", bd=1,
                            width=24, height=2, anchor="center")
        self.big.pack(fill="x", pady=(0, 6))

        self.hint = tk.StringVar(value="等待按键…")
        ttk.Label(frm, textvariable=self.hint, foreground=MUTED).pack(anchor="w")

        if not allow_single:
            ttk.Label(frm, text="提示: 建议至少包含一个修饰键(Ctrl / Alt / Shift), 否则会独占该按键。",
                      foreground="#b45309").pack(anchor="w", pady=(6, 0))

        bar = ttk.Frame(frm)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text="清 除", command=self._clear).pack(side="left")
        ttk.Button(bar, text="取 消", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="确 定", command=self._ok, style="Accent.TButton").pack(side="right", padx=(8, 0))

        self.recorder = Recorder(self._on_update)
        self.recorder.start()

        self.top.update_idletasks()
        self._center(parent)
        parent.wait_window(self.top)

    def _center(self, parent):
        try:
            self.top.geometry(
                "+%d+%d" % (parent.winfo_rootx() + (parent.winfo_width() - self.top.winfo_width()) // 2,
                            parent.winfo_rooty() + (parent.winfo_height() - self.top.winfo_height()) // 2))
        except Exception:
            pass

    def _on_update(self, combo):
        if combo is None:
            self.result = None
            self.var.set("未设置")
            self.hint.set("等待按键…")
            return
        if combo.get("vk"):
            self.result = combo
            self.var.set(fmt_combo(combo))
            self.hint.set("已捕获: %s" % fmt_combo(combo))
        else:
            self.var.set("+".join([MOD_DISPLAY[m] for m in MODS_ORDER if m in combo.get("mods", [])]) + "+…"
                         if combo.get("mods") else "等待按键…")

    def _clear(self):
        self.recorder.clear()

    def _ok(self):
        self.recorder.stop()
        self.top.grab_release()
        self.top.destroy()

    def _cancel(self):
        self.recorder.stop()
        self.result = self.initial
        self.top.grab_release()
        self.top.destroy()


class ActionDialog(object):
    """编辑一个点击动作(一次点击 / 一次按键)。"""

    def __init__(self, parent, action, title="编辑动作"):
        self.action = json.loads(json.dumps(action)) if action else default_action()
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.configure(bg=BG)
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.grab_set()
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        frm = ttk.Frame(self.top, padding=16)
        frm.pack(fill="both", expand=True)

        self.type_var = tk.StringVar(
            value=("keyboard" if self.action.get("type") == "keyboard"
                   else self.action.get("mouse_button", "left")))
        self.kbd_var = tk.StringVar(value=fmt_combo(self.action.get("kbd")))
        self.interval_var = tk.StringVar(value=str(self.action.get("interval_ms", 100)))
        self.jitter_var = tk.StringVar(value=str(self.action.get("jitter_ms", 0)))
        self.hold_var = tk.StringVar(value=str(self.action.get("hold_ms", 0)))
        self.repeat_var = tk.StringVar(value=str(self.action.get("repeat", 0)))

        row = 0
        ttk.Label(frm, text="动作类型").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
        bar = ttk.Frame(frm)
        bar.grid(row=row, column=1, columnspan=2, sticky="w")
        for text, val in (("鼠标左键", "left"), ("鼠标中键", "middle"),
                          ("鼠标右键", "right"), ("键盘按键", "keyboard")):
            ttk.Radiobutton(bar, text=text, value=val, variable=self.type_var,
                            command=self._sync).pack(side="left", padx=(0 if val == "left" else 8, 0))
        row += 1

        self.kbd_label = ttk.Label(frm, text="按键")
        self.kbd_label.grid(row=row, column=0, sticky="w", pady=5)
        self.kbd_entry = ttk.Entry(frm, textvariable=self.kbd_var, width=22, state="readonly")
        self.kbd_entry.grid(row=row, column=1, sticky="ew", pady=5)
        self.kbd_btn = ttk.Button(frm, text="录制…", command=self._record_kbd, width=8)
        self.kbd_btn.grid(row=row, column=2, padx=6)
        row += 1

        for label, var in (("执行后等待(毫秒)", self.interval_var),
                           ("随机波动 ±(毫秒)", self.jitter_var),
                           ("按下保持(毫秒)", self.hold_var),
                           ("重复次数(0=不限)", self.repeat_var)):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Spinbox(frm, from_=0, to=86400000, textvariable=var, width=12).grid(
                row=row, column=1, sticky="w", pady=5)
            row += 1

        ttk.Label(frm, text="「重复次数」= 这个动作点几下；0 = 一直点到停止为止。",
                  foreground=MUTED).grid(row=row, column=0, columnspan=3, sticky="w", pady=(10, 2))
        row += 1

        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(btns, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="保存", command=self._save, style="Accent.TButton").pack(side="right", padx=(0, 8))

        frm.columnconfigure(1, weight=1)
        self._sync()
        self.top.update_idletasks()
        try:
            self.top.geometry("+%d+%d" % (parent.winfo_rootx() + 80, parent.winfo_rooty() + 60))
        except Exception:
            pass
        parent.wait_window(self.top)

    def _sync(self):
        kb = self.type_var.get() == "keyboard"
        self.kbd_btn.configure(state="normal" if kb else "disabled")
        self.kbd_label.configure(foreground=TEXT if kb else MUTED)

    def _record_kbd(self):
        dlg = RecordDialog(self.top, "录制键盘按键", self.action.get("kbd"), allow_single=True)
        if dlg.result:
            self.action["kbd"] = dlg.result
            self.kbd_var.set(fmt_combo(dlg.result))

    def _save(self):
        kind = self.type_var.get()
        if kind == "keyboard" and not (self.action.get("kbd") or {}).get("vk"):
            messagebox.showwarning("缺少按键", "请先录制要模拟的键盘按键。", parent=self.top)
            return
        nums = {}
        for key, var, minimum in (("interval_ms", self.interval_var, 1),
                                  ("jitter_ms", self.jitter_var, 0),
                                  ("hold_ms", self.hold_var, 0),
                                  ("repeat", self.repeat_var, 0)):
            try:
                v = int(float(var.get().strip() or 0))
            except Exception:
                messagebox.showwarning("数值无效", "请输入有效的整数数值。", parent=self.top)
                return
            nums[key] = max(minimum, v)
        if kind == "keyboard":
            self.action["type"] = "keyboard"
        else:
            self.action["type"] = "mouse"
            self.action["mouse_button"] = kind
        self.action.update(nums)
        self.result = self.action
        self.top.grab_release()
        self.top.destroy()

    def _cancel(self):
        self.result = None
        self.top.grab_release()
        self.top.destroy()


class BindingDialog(object):
    """新增 / 编辑绑定: 一个热键 + 一组同时启动的点击动作。"""

    def __init__(self, parent, binding, existing):
        self.binding = json.loads(json.dumps(binding))  # 副本
        if not self.binding.get("actions"):
            self.binding["actions"] = [default_action()]
        self.existing = existing
        self.ok = False
        self.top = tk.Toplevel(parent)
        self.top.title("编辑绑定" if binding.get("name") else "新增绑定")
        self.top.configure(bg=BG)
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.grab_set()
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)

        frm = ttk.Frame(self.top, padding=16)
        frm.pack(fill="both", expand=True)

        self.name_var = tk.StringVar(value=self.binding.get("name", ""))
        self.enabled_var = tk.BooleanVar(value=bool(self.binding.get("enabled", True)))
        self.repeat_var = tk.StringVar(value=str(self.binding.get("repeat", 0)))
        self.hk_var = tk.StringVar(value=fmt_combo(self.binding.get("hotkey")))

        row = 0

        ttk.Label(frm, text="名称").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
        ttk.Entry(frm, textvariable=self.name_var, width=30).grid(row=row, column=1, columnspan=3, sticky="ew", pady=5)
        row += 1

        ttk.Checkbutton(frm, text="启用该绑定", variable=self.enabled_var).grid(
            row=row, column=1, columnspan=3, sticky="w", pady=(2, 6))
        row += 1

        ttk.Label(frm, text="启动/停止热键").grid(row=row, column=0, sticky="w", pady=5)
        e = ttk.Entry(frm, textvariable=self.hk_var, width=22, state="readonly")
        e.grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(frm, text="录制…", command=self._record_hotkey, width=8).grid(row=row, column=2, padx=6)
        row += 1

        # ---- 动作列表 ----
        ttk.Separator(frm, orient="horizontal").grid(row=row, column=0, columnspan=4, sticky="ew", pady=(12, 8))
        row += 1
        ttk.Label(frm, text="点击动作（按下热键后这些动作同时开始，各自按自己的节奏重复）").grid(
            row=row, column=0, columnspan=4, sticky="w", pady=(0, 6))
        row += 1

        box = ttk.Frame(frm)
        box.grid(row=row, column=0, columnspan=4, sticky="ew")
        cols = ("no", "action", "rhythm", "times")
        self.act_tree = ttk.Treeview(box, columns=cols, show="headings", height=4, selectmode="browse")
        heads = {"no": "#", "action": "动作", "rhythm": "节奏", "times": "重复次数"}
        widths = {"no": 34, "action": 150, "rhythm": 130, "times": 80}
        for c in cols:
            self.act_tree.heading(c, text=heads[c])
            self.act_tree.column(c, width=widths[c], anchor="center" if c != "action" else "w")
        self.act_tree.pack(side="left", fill="x", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=self.act_tree.yview)
        sb.pack(side="right", fill="y")
        self.act_tree.configure(yscrollcommand=sb.set)
        self.act_tree.bind("<Double-1>", lambda ev: self._edit_action())
        row += 1

        bar = ttk.Frame(frm)
        bar.grid(row=row, column=0, columnspan=4, sticky="w", pady=(8, 0))
        for text, cmd in (("＋ 添加", self._add_action), ("编辑", self._edit_action),
                          ("复制", self._dup_action), ("删除", self._del_action),
                          ("↑ 上移", self._move_up), ("↓ 下移", self._move_down)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 6))
        row += 1

        ttk.Label(frm, text="总点击次数(0=无限)").grid(row=row, column=0, sticky="w", pady=(10, 5))
        ttk.Spinbox(frm, from_=0, to=86400000, textvariable=self.repeat_var, width=12).grid(
            row=row, column=1, sticky="w", pady=5)
        row += 1

        ttk.Label(frm, text="点击位置始终为鼠标光标当前所在位置(不移动、不锁定光标)。",
                  foreground=MUTED).grid(row=row, column=0, columnspan=4, sticky="w", pady=(10, 2))
        row += 1
        ttk.Label(frm, text="热键为「开关」: 按一次开始，再按一次停止；双击上表行可编辑该动作。",
                  foreground=MUTED).grid(row=row, column=0, columnspan=4, sticky="w")
        row += 1

        bar2 = ttk.Frame(frm)
        bar2.grid(row=row, column=0, columnspan=4, sticky="e", pady=(16, 0))
        ttk.Button(bar2, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(bar2, text="保存", command=self._save, style="Accent.TButton").pack(side="right", padx=(0, 8))

        frm.columnconfigure(1, weight=1)
        self._refresh_actions()
        self.top.update_idletasks()
        try:
            self.top.geometry("+%d+%d" % (parent.winfo_rootx() + 40, parent.winfo_rooty() + 20))
        except Exception:
            pass
        parent.wait_window(self.top)

    # ---- 动作列表 ----
    def _refresh_actions(self):
        t = self.act_tree
        for iid in list(t.get_children()):
            t.delete(iid)
        for i, act in enumerate(self.binding.get("actions") or [], 1):
            n = int(act.get("repeat", 0))
            jit = int(act.get("jitter_ms", 0))
            rhythm = "%dms" % int(act.get("interval_ms", 100))
            if jit:
                rhythm += " ±%d" % jit
            t.insert("", "end", iid=str(i - 1),
                     values=(i, action_label(act), rhythm, ("不限" if n == 0 else "×%d" % n)))

    def _index(self):
        sel = self.act_tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _add_action(self):
        dlg = ActionDialog(self.top, default_action(), "添加动作")
        if dlg.result:
            self.binding.setdefault("actions", []).append(dlg.result)
            self._refresh_actions()

    def _edit_action(self):
        i = self._index()
        if i is None:
            messagebox.showinfo("提示", "请先选中要编辑的步骤。", parent=self.top)
            return
        dlg = ActionDialog(self.top, self.binding["actions"][i], "编辑动作")
        if dlg.result:
            self.binding["actions"][i] = dlg.result
            self._refresh_actions()
            self.act_tree.selection_set(str(i))

    def _dup_action(self):
        i = self._index()
        if i is None:
            return
        copy = json.loads(json.dumps(self.binding["actions"][i]))
        copy["id"] = new_id()
        self.binding["actions"].insert(i + 1, copy)
        self._refresh_actions()
        self.act_tree.selection_set(str(i + 1))

    def _del_action(self):
        i = self._index()
        if i is None:
            return
        if len(self.binding["actions"]) <= 1:
            messagebox.showinfo("提示", "至少保留一个动作。", parent=self.top)
            return
        self.binding["actions"].pop(i)
        self._refresh_actions()

    def _move(self, delta):
        i = self._index()
        if i is None:
            return
        j = i + delta
        acts = self.binding["actions"]
        if j < 0 or j >= len(acts):
            return
        acts[i], acts[j] = acts[j], acts[i]
        self._refresh_actions()
        self.act_tree.selection_set(str(j))

    def _move_up(self):
        self._move(-1)

    def _move_down(self):
        self._move(1)

    def _record_hotkey(self):
        dlg = RecordDialog(self.top, "录制热键", self.binding.get("hotkey"), allow_single=False)
        if dlg.result:
            self.binding["hotkey"] = dlg.result
            self.hk_var.set(fmt_combo(dlg.result))

    def _save(self):
        name = self.name_var.get().strip() or "绑定"
        if not self.binding.get("hotkey") or not self.binding["hotkey"].get("vk"):
            messagebox.showwarning("缺少热键", "请先为该绑定录制一个启动/停止热键。", parent=self.top)
            return
        acts = self.binding.get("actions") or []
        if not acts:
            messagebox.showwarning("缺少动作", "请至少添加一个点击动作。", parent=self.top)
            return
        for act in acts:
            if act.get("type") == "keyboard" and not (act.get("kbd") or {}).get("vk"):
                messagebox.showwarning("缺少按键", "序列中有键盘动作未录制按键。", parent=self.top)
                return
        try:
            total = int(float(self.repeat_var.get().strip() or 0))
        except Exception:
            messagebox.showwarning("数值无效", "请输入有效的整数数值。", parent=self.top)
            return
        self.binding["name"] = name
        self.binding["enabled"] = bool(self.enabled_var.get())
        self.binding["loop"] = True
        self.binding["repeat"] = max(0, total)
        self.binding["actions"] = acts
        self.ok = True
        self.top.grab_release()
        self.top.destroy()

    def _cancel(self):
        self.ok = False
        self.top.grab_release()
        self.top.destroy()


def exe_command():
    """开机启动注册表里写的命令行。"""
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
    """读取开机启动项命令行, 未启用返回空串。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ)
        try:
            v, _ = winreg.QueryValueEx(k, APP_NAME)
        finally:
            winreg.CloseKey(k)
        return str(v)
    except Exception:
        return ""


def autostart_set(on, hidden=False):
    """开关开机启动; 返回是否成功。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            if on:
                cmd = exe_command()
                if hidden:
                    cmd += " --hidden"
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
    """AClib/pictures 下的图片文件名(按名称排序)。"""
    try:
        os.makedirs(PICTURES_DIR, exist_ok=True)
        names = [n for n in os.listdir(PICTURES_DIR)
                 if n.lower().endswith(IMAGE_EXTS) and os.path.isfile(os.path.join(PICTURES_DIR, n))]
        return sorted(names, key=lambda s: s.lower())
    except Exception:
        return []


def import_picture(src):
    """把外部图片复制进 AClib/pictures, 返回文件名。"""
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


class AboutDialog(object):
    """关于: 版权 / 作者 / 开源地址。"""

    def __init__(self, parent):
        self.top = tk.Toplevel(parent)
        self.top.title("关于 %s" % APP_NAME)
        self.top.configure(bg=BG)
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.grab_set()

        frm = ttk.Frame(self.top, padding=20)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="%s  v%s" % (APP_NAME, APP_VERSION),
                  font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        ttk.Label(frm, text="由 %s 制作   ·   © %s" % (AUTHOR, time.strftime("%Y")),
                  foreground=MUTED).pack(anchor="w", pady=(4, 12))

        ttk.Separator(frm, orient="horizontal").pack(fill="x", pady=(0, 12))

        ttk.Label(frm, text="开源地址（点击在浏览器中打开）").pack(anchor="w")
        link = tk.Label(frm, text=PROJECT_URL, fg=ACCENT, bg=BG, cursor="hand2",
                        font=("Microsoft YaHei UI", 9, "underline"))
        link.pack(anchor="w", pady=(2, 10))
        link.bind("<Button-1>", lambda e: self._open())

        ttk.Label(frm, text="许可协议: MIT License（可自由使用、修改、分发，需保留版权声明）",
                  foreground=MUTED, wraplength=380, justify="left").pack(anchor="w")
        ttk.Label(frm, text="本程序使用 Python + Win32 SendInput 编写，源码完全公开。",
                  foreground=MUTED, wraplength=380, justify="left").pack(anchor="w", pady=(6, 12))

        bar = ttk.Frame(frm)
        bar.pack(fill="x")
        ttk.Button(bar, text="打开项目主页", command=self._open).pack(side="left")
        ttk.Button(bar, text="复制网址", command=self._copy).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="关闭", command=self._close).pack(side="right")

        self.top.update_idletasks()
        try:
            self.top.geometry("+%d+%d" % (parent.winfo_rootx() + 120, parent.winfo_rooty() + 80))
        except Exception:
            pass
        parent.wait_window(self.top)

    def _open(self):
        try:
            webbrowser.open(PROJECT_URL)
        except Exception:
            pass

    def _copy(self):
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(PROJECT_URL)
        except Exception:
            pass

    def _close(self):
        self.top.grab_release()
        self.top.destroy()


class SettingsDialog(object):
    """设置: 常规 / 外观 / 关于。"""

    def __init__(self, parent, config, app=None):
        self.config = config
        self.app = app
        self.ok = False
        self._old_theme = config.settings.get("theme", "light")
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
        self.new_em = st.get("emergency_hotkey")

        nb = ttk.Notebook(self.top)
        nb.pack(fill="both", expand=True, padx=14, pady=14)

        f1 = ttk.Frame(nb, padding=16)
        f2 = ttk.Frame(nb, padding=16)
        f3 = ttk.Frame(nb, padding=16)
        nb.add(f1, text="常规")
        nb.add(f2, text="外观")
        nb.add(f3, text="关于")

        # ---------------- 常规 ----------------
        row = 0
        ttk.Label(f1, text="紧急停止热键").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(f1, textvariable=self.em_var, width=22, state="readonly").grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Button(f1, text="录制…", width=8, command=self._record).grid(row=row, column=2, padx=6)
        row += 1

        ttk.Checkbutton(f1, text="始终保留 Ctrl+Alt+F12 作为备用急停热键",
                        variable=self.fallback_var).grid(row=row, column=0, columnspan=3, sticky="w", pady=4)
        row += 1

        ttk.Separator(f1, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1

        ttk.Label(f1, text="关闭窗口时").grid(row=row, column=0, sticky="w", pady=4)
        box = ttk.Frame(f1)
        box.grid(row=row, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(box, text="最小化到系统托盘(继续后台响应热键)", value="tray",
                        variable=self.close_var).pack(anchor="w")
        ttk.Radiobutton(box, text="直接退出程序", value="quit",
                        variable=self.close_var).pack(anchor="w", pady=(2, 0))
        row += 1

        ttk.Separator(f1, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1

        cur = autostart_get()
        self.auto_state = tk.StringVar(
            value=("开机启动: 已启用" if cur else "开机启动: 未启用"))
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
        row += 1
        f1.columnconfigure(1, weight=1)

        # ---------------- 外观 ----------------
        row = 0
        ttk.Label(f2, text="界面主题").grid(row=row, column=0, sticky="w", pady=(0, 6))
        box2 = ttk.Frame(f2)
        box2.grid(row=row, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(box2, text="亮色", value="light", variable=self.theme_var,
                        command=self._preview_theme).pack(side="left")
        ttk.Radiobutton(box2, text="暗色", value="dark", variable=self.theme_var,
                        command=self._preview_theme).pack(side="left", padx=(16, 0))
        row += 1

        ttk.Separator(f2, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)
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

        ttk.Label(f2, text="图片请放在配置目录下的 pictures 文件夹：\n%s" % PICTURES_DIR,
                  foreground=MUTED, wraplength=400, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w")
        row += 1
        ttk.Label(f2, text="选「浏览…」会自动把图片复制进该目录。图片按比例铺满窗口。",
                  foreground=MUTED, wraplength=400, justify="left").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(4, 0))
        row += 1
        f2.columnconfigure(1, weight=1)

        # ---------------- 关于 ----------------
        ttk.Label(f3, text="%s  v%s" % (APP_NAME, APP_VERSION),
                  font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(f3, text="由 %s 制作   ·   © %s" % (AUTHOR, time.strftime("%Y")),
                  foreground=MUTED).pack(anchor="w", pady=(2, 10))
        link = tk.Label(f3, text=PROJECT_URL, fg=ACCENT, bg=BG, cursor="hand2",
                        font=("Microsoft YaHei UI", 9, "underline"))
        link.pack(anchor="w")
        link.bind("<Button-1>", lambda e: self._open_url())
        ttk.Label(f3, text="许可协议: MIT License", foreground=MUTED).pack(anchor="w", pady=(6, 10))
        bar3 = ttk.Frame(f3)
        bar3.pack(fill="x")
        ttk.Button(bar3, text="打开项目主页", command=self._open_url).pack(side="left")
        ttk.Button(bar3, text="复制网址", command=self._copy_url).pack(side="left", padx=(8, 0))
        ttk.Button(bar3, text="关于窗口", command=self._open_about).pack(side="left", padx=(8, 0))

        # ---------------- 底部 ----------------
        bar = ttk.Frame(self.top)
        bar.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(bar, text="取消", command=self._close).pack(side="right")
        ttk.Button(bar, text="保存", command=self._save, style="Accent.TButton").pack(side="right", padx=(0, 8))

        self.top.update_idletasks()
        try:
            self.top.geometry("+%d+%d" % (parent.winfo_rootx() + 60, parent.winfo_rooty() + 40))
        except Exception:
            pass
        parent.wait_window(self.top)

    # ---- 外观 ----
    def _fill_pictures(self):
        names = [""] + list_pictures()
        self.bg_combo["values"] = names
        cur = (self.bg_var.get() or "").strip()
        if cur not in names:
            cur = ""
        self.bg_var.set(cur)

    def _preview_theme(self):
        if self.app:
            try:
                self.app.preview_theme(self.theme_var.get())
            except Exception:
                pass

    def _preview_bg(self):
        if self.app:
            try:
                self.app.preview_background(self.bg_var.get())
            except Exception:
                pass

    def _browse_bg(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self.top, title="选择背景图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif"), ("所有文件", "*.*")])
        if not path:
            return
        name = import_picture(path)
        if not name:
            messagebox.showwarning("导入失败", "无法把图片复制到 pictures 目录。", parent=self.top)
            return
        self._fill_pictures()
        self.bg_var.set(name)
        self._preview_bg()

    def _clear_bg(self):
        self.bg_var.set("")
        self._preview_bg()

    def _open_about(self):
        AboutDialog(self.top)

    def _open_url(self):
        try:
            webbrowser.open(PROJECT_URL)
        except Exception:
            pass

    def _copy_url(self):
        try:
            self.top.clipboard_clear()
            self.top.clipboard_append(PROJECT_URL)
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

    def _save(self):
        s = self.config.settings
        s["emergency_hotkey"] = self.new_em
        s["panic_fallback"] = bool(self.fallback_var.get())
        s["close_action"] = self.close_var.get()
        s["start_hidden"] = bool(self.auto_hidden_var.get())
        s["theme"] = self.theme_var.get()
        s["background"] = (self.bg_var.get() or "").strip()
        s["autostart"] = bool(self.auto_var.get())
        autostart_set(s["autostart"], hidden=s["start_hidden"])
        self.config.save()
        self.ok = True
        if self.app:
            try:
                self.app.apply_settings()
                self.app.rebuild_after_settings()
            except Exception:
                pass
        self._close()

    def _close(self):
        if not self.ok and self.app:
            # 取消: 还原预览过的主题/背景
            try:
                self.app.preview_theme(self._old_theme)
                self.app.preview_background(self.config.settings.get("background", ""))
            except Exception:
                pass
        self.top.grab_release()
        self.top.destroy()


class App(object):
    def __init__(self, root, config, engine):
        self.root = root
        self.config = config
        self.engine = engine
        self.icon = None
        self._tray_state = None
        self._menu_sig = None
        self._was_running = False
        self._closing = False

        self.root.title("%s  v%s" % (APP_NAME, APP_VERSION))
        self.root.geometry("860x540")
        self.root.minsize(760, 460)
        self.root.configure(bg=BG)

        set_theme(config.settings.get("theme", "light"))
        try:
            os.makedirs(PICTURES_DIR, exist_ok=True)
        except Exception:
            pass
        self._style()
        self._setup_background()
        self._build()
        self._refresh_tree()

        # 工作线程 -> 主线程: 一律经队列转发, 绝不在子线程触碰任何 tk 控件
        engine.on_change = self.request_refresh
        engine.rebuild_hotkeys()
        self._setup_tray()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(400, self._refresh_background)
        self._poll()

    # ---- 样式 ----
    def _style(self):
        apply_theme(self.root)

    # ---- 界面背景图 ----
    def _setup_background(self):
        """底层 Canvas 铺背景图(图片来自 AClib/pictures)。"""
        self._bg_photo = None
        self._bg_item = None
        self._bg_margin = 0
        self.bg_canvas = tk.Canvas(self.root, highlightthickness=0, bd=0,
                                   bg=BG)
        self.bg_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        try:
            self.bg_canvas.lower()
        except Exception:
            pass
        self._resize_job = None
        self.root.bind("<Configure>", self._on_resize)
        self._refresh_background()

    def _layout_outer(self):
        """内容面板四周留 18px, 背景图才看得见。"""
        try:
            w = self.root.winfo_width()
            h = self.root.winfo_height()
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

    def _reset_glass(self):
        """无背景图时, 玻璃面板回到普通主题色。"""
        st = ttk.Style()
        st.configure("Glass.TFrame", background=BG)
        st.configure("Glass.TLabel", background=BG, foreground=TEXT)
        st.configure("Glass.Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, rowheight=28, borderwidth=0, bordercolor=BORDER)
        st.configure("Glass.Treeview.Heading", background=HEAD, foreground=TEXT,
                     font=("Microsoft YaHei UI", 9, "bold"), bordercolor=BORDER)
        st.map("Glass.Treeview", background=[("selected", SEL)],
               foreground=[("selected", TEXT)])

    def _refresh_background(self):
        name = (self.config.settings.get("background") or "").strip()
        try:
            self.bg_canvas.delete("all")
        except Exception:
            return
        path = os.path.join(PICTURES_DIR, name) if name else ""
        if not path or not os.path.isfile(path):
            self.bg_canvas.configure(bg=BG)
            self._bg_photo = None
            self._reset_glass()
            self._bg_margin = 0
            self._layout_outer()
            return
        try:
            from PIL import Image, ImageTk
            w = max(1, self.root.winfo_width())
            h = max(1, self.root.winfo_height())
            img = Image.open(path)
            img = img.convert("RGB")
            # cover: 按比例填满窗口
            rw, rh = img.size
            scale = max(w / float(rw), h / float(rh))
            img = img.resize((max(1, int(rw * scale)), max(1, int(rh * scale))),
                             Image.LANCZOS)
            left = max(0, (img.size[0] - w) // 2)
            top = max(0, (img.size[1] - h) // 2)
            img = img.crop((left, top, left + w, top + h))
            # 面板色 = 图片平均色与主题色混合, 让内容区也带上图片色调
            avg = img.resize((1, 1)).getpixel((0, 0))
            panel = _mix_hex(_rgb_hex(avg), CARD, 0.18)
            head_col = _mix_hex(_rgb_hex(avg), HEAD, 0.18)
            st = ttk.Style()
            st.configure("Glass.TFrame", background=panel)
            st.configure("Glass.TLabel", background=panel, foreground=TEXT)
            st.configure("Glass.Treeview", background=panel, fieldbackground=panel,
                         foreground=TEXT, rowheight=28, borderwidth=0, bordercolor=BORDER)
            st.configure("Glass.Treeview.Heading", background=head_col, foreground=TEXT,
                         font=("Microsoft YaHei UI", 9, "bold"), bordercolor=BORDER)
            st.map("Glass.Treeview", background=[("selected", SEL)],
                   foreground=[("selected", TEXT)])
            self._bg_photo = ImageTk.PhotoImage(img)
            self.bg_canvas.configure(bg=BG)
            self.bg_canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)
            self._bg_margin = 18
            self._layout_outer()
        except Exception:
            self.bg_canvas.configure(bg=BG)
            self._bg_photo = None
            self._reset_glass()

    def apply_settings(self):
        """设置保存后: 主题 / 背景 / 列表配色立即生效。"""
        set_theme(self.config.settings.get("theme", "light"))
        self._restyle()

    # ---- 界面 ----
    def _build(self):
        # 内容面板(四周留边, 让背景图在窗口边缘露出来)
        self.outer = ttk.Frame(self.root, style="Glass.TFrame", padding=0)
        self._layout_outer()
        panel = self.outer

        head = ttk.Frame(panel, padding=(16, 14, 16, 6), style="Glass.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=APP_NAME, font=("Microsoft YaHei UI", 15, "bold"),
                  foreground=TEXT, style="Glass.TLabel").pack(side="left")
        self.state_label = ttk.Label(head, text="就绪", foreground=MUTED,
                                     style="Glass.TLabel")
        self.state_label.pack(side="left", padx=(14, 0))
        ttk.Button(head, text="关于", command=self.open_about, width=6).pack(side="right")
        ttk.Button(head, text="设置", command=self.open_settings, width=8).pack(side="right", padx=(8, 0))
        ttk.Button(head, text="隐藏到托盘", command=self.hide_window, width=11).pack(side="right", padx=(0, 8))

        bar = ttk.Frame(panel, padding=(16, 4), style="Glass.TFrame")
        bar.pack(fill="x")
        ttk.Button(bar, text="＋ 新增", command=self.add_binding, style="Accent.TButton").pack(side="left")
        ttk.Button(bar, text="编辑", command=self.edit_binding, width=8).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="删除", command=self.delete_binding, width=8).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="启动 / 停止", command=self.toggle_selected, width=12).pack(side="left", padx=(16, 0))
        ttk.Button(bar, text="⏹ 紧急停止(全部)", command=self.emergency_stop,
                   style="Danger.TButton").pack(side="right")

        wrap = ttk.Frame(panel, padding=(16, 8), style="Glass.TFrame")
        wrap.pack(fill="both", expand=True)
        cols = ("status", "name", "hotkey", "action", "interval", "count")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse",
                                 style="Glass.Treeview")
        heads = {"status": "状态", "name": "名称", "hotkey": "热键", "action": "点击动作",
                 "interval": "间隔(ms)", "count": "已点击"}
        widths = {"status": 110, "name": 120, "hotkey": 130, "action": 200,
                  "interval": 110, "count": 80}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor="center" if c != "name" and c != "action" else "w")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.tag_configure("running", foreground="#15803d")
        self.tree.tag_configure("stopped", foreground=MUTED)
        self.tree.bind("<Double-1>", lambda e: self.edit_binding())

        foot = ttk.Frame(panel, padding=(16, 6, 16, 12), style="Glass.TFrame")
        foot.pack(fill="x")
        self.tip = tk.StringVar(value="双击行可编辑；热键为开关模式(按一次开始，再按一次停止)，全局生效。")
        ttk.Label(foot, textvariable=self.tip, foreground=MUTED,
                  style="Glass.TLabel").pack(side="left")
        self.foot_right = ttk.Label(foot, foreground=MUTED, style="Glass.TLabel")
        self.foot_right.pack(side="right")
        self._update_foot()

    def _update_foot(self):
        try:
            self.foot_right.configure(
                text="配置: %s  ·  %s 制作" % (CONFIG_FILE, AUTHOR))
        except Exception:
            pass

    # ---- 数据同步 ----
    def _refresh_tree(self):
        tree = self.tree
        keep = {}
        for b in self.config.bindings:
            keep[b["id"]] = b
        for iid in list(tree.get_children()):
            if iid not in keep:
                tree.delete(iid)
        for b in self.config.bindings:
            running = self.engine.is_running(b["id"])
            if not b.get("enabled", True):
                status = "已禁用"
            elif running:
                status = "● 运行中"
            else:
                status = "○ 已停止"
            action = binding_summary(b)
            interval = binding_rhythm(b)
            values = (status, b.get("name", ""), fmt_combo(b.get("hotkey")),
                      action, interval, str(self.engine.click_count(b["id"])))
            if tree.exists(b["id"]):
                tree.item(b["id"], values=values,
                          tags=("running",) if running else ("stopped",))
            else:
                tree.insert("", "end", iid=b["id"], values=values,
                            tags=("running",) if running else ("stopped",))

        n = len(self.engine.running_ids())
        self.state_label.configure(
            text=("运行中: %d 组" % n) if n else "就绪",
            foreground="#15803d" if n else MUTED)
        errs = self.engine.hotkeys.errors
        if errs:
            self.tip.set("⚠ 以下热键注册失败(可能被其它程序占用): " + "、".join(errs))
        else:
            self.tip.set("双击行可编辑；热键为开关模式(按一次开始，再按一次停止)，全局生效。")
        self._sync_tray()

    # ---- 操作 ----
    def selected_id(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return sel[0]

    def add_binding(self):
        b = default_binding(len(self.config.bindings) + 1)
        dlg = BindingDialog(self.root, b, self.config.bindings)
        if dlg.ok:
            self.config.bindings.append(dlg.binding)
            self.save_and_rebuild()
            self.tree.selection_set(dlg.binding["id"])

    def edit_binding(self):
        bid = self.selected_id()
        if not bid:
            messagebox.showinfo("提示", "请先选择一条绑定。", parent=self.root)
            return
        b = self.config.binding_by_id(bid)
        if not b:
            return
        dlg = BindingDialog(self.root, b, self.config.bindings)
        if dlg.ok:
            idx = self.config.bindings.index(b)
            self.config.bindings[idx] = dlg.binding
            self.engine.stop(bid)
            self.save_and_rebuild()

    def delete_binding(self):
        bid = self.selected_id()
        if not bid:
            messagebox.showinfo("提示", "请先选择一条绑定。", parent=self.root)
            return
        b = self.config.binding_by_id(bid)
        if not b:
            return
        if not messagebox.askyesno("删除绑定", "确认删除「%s」？" % b.get("name", ""), parent=self.root):
            return
        self.engine.stop(bid)
        self.config.bindings = [x for x in self.config.bindings if x["id"] != bid]
        self.save_and_rebuild()

    def toggle_selected(self):
        bid = self.selected_id()
        if not bid:
            messagebox.showinfo("提示", "请先选择一条绑定。", parent=self.root)
            return
        self.engine.toggle(bid)
        self._refresh_tree()

    def emergency_stop(self):
        self.engine.stop_all()
        self._refresh_tree()
        self._notify("已停止全部连点", "紧急停止已执行")

    def open_settings(self):
        dlg = SettingsDialog(self.root, self.config, app=self)
        if dlg.ok:
            self.engine.rebuild_hotkeys()
            self._refresh_tree()
            self._update_foot()
            self._notify("设置已保存", APP_NAME)

    def open_about(self):
        AboutDialog(self.root)

    def _restyle(self):
        """主题/背景变化后的统一刷新(样式 + 背景 + 列表标签色)。"""
        self._style()
        self._refresh_background()
        try:
            self.tree.tag_configure("running", foreground="#15803d")
            self.tree.tag_configure("stopped", foreground=MUTED)
        except Exception:
            pass

    def preview_theme(self, name):
        """设置里切换主题时的即时预览。"""
        set_theme(name)
        self._restyle()

    def preview_background(self, name):
        """设置里切换背景图时的即时预览。"""
        try:
            self.config.settings["background"] = name or ""
        except Exception:
            pass
        self._refresh_background()

    def rebuild_after_settings(self):
        """设置保存后需要重建的东西(热键等)。"""
        try:
            self.engine.rebuild_hotkeys()
            self._refresh_tree()
            self._update_foot()
        except Exception:
            pass

    def save_and_rebuild(self):
        self.config.save()
        self.engine.rebuild_hotkeys()
        self._refresh_tree()

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

        self.pystray = pystray
        self.MenuItem = Item
        self.Menu = Menu
        self.icon = pystray.Icon(
            "AutoClicker",
            make_tray_image("idle"),
            APP_NAME,
            self._build_menu())
        try:
            self.icon.run_detached()
        except Exception:
            self.icon = None

    def _build_menu(self):
        Menu, Item = self.Menu, self.MenuItem
        items = [Item("显示主界面", self._tray_show, default=True),
                 Item("紧急停止(全部)", self._tray_stop_all),
                 Menu.SEPARATOR]
        for b in list(self.config.bindings):
            items.append(Item(self._menu_text_factory(b), self._menu_action_factory(b["id"])))
        items.append(Menu.SEPARATOR)
        items.append(Item("设置…", self._tray_settings))
        items.append(Item("关于", self._tray_about))
        items.append(Item("退出", self._tray_quit))
        return Menu(*items)

    def _menu_text_factory(self, b):
        bid = b["id"]

        def _text(item):
            mark = "■ " if self.engine.is_running(bid) else "▶ "
            return "%s%s  [%s]" % (mark, b.get("name", ""), fmt_combo(b.get("hotkey")))
        return _text

    def _menu_action_factory(self, bid):
        def _action(icon=None, item=None):
            ui_call(self.engine.toggle, bid)
        return _action

    def _tray_show(self, icon=None, item=None):
        ui_call(self.show_window)

    def _tray_stop_all(self, icon=None, item=None):
        ui_call(self.emergency_stop)

    def _tray_quit(self, icon=None, item=None):
        ui_call(self.quit_app)

    def _tray_settings(self, icon=None, item=None):
        ui_call(self.show_window)
        ui_call(self.open_settings)

    def _tray_about(self, icon=None, item=None):
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
        title = APP_NAME + (("  ·  运行中 %d 组" % running) if running else "  ·  就绪")
        try:
            self.icon.title = title
        except Exception:
            pass
        sig = tuple((b["id"], b.get("name"), fmt_combo(b.get("hotkey")),
                     self.engine.is_running(b["id"])) for b in self.config.bindings)
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
        """可在任意线程调用(仅入队, 不直接操作界面)。"""
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
            if dirty or any_running or self._was_running:
                self._refresh_tree()
            self._was_running = any_running
        except Exception:
            pass
        self.root.after(250, self._poll)

    # ---- 退出 ----
    def quit_app(self):
        if self._closing:
            return
        self._closing = True
        self.engine.stop_all()
        try:
            self.engine.hotkeys.stop()
        except Exception:
            pass
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
def main():
    # 单实例保护
    kernel32.CreateMutexW(None, True, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        user32.MessageBoxW(None, "AutoClicker 已经在运行了，请查看系统托盘。",
                           APP_NAME, 0x00000040)
        return

    config = Config()
    config.save()  # 确保配置目录 / 文件存在
    try:
        os.makedirs(PICTURES_DIR, exist_ok=True)
    except Exception:
        pass
    # 让注册表开机启动项与配置保持一致(例如 exe 位置变过)
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
    hidden = bool(config.settings.get("start_hidden", False)) or ("--hidden" in sys.argv[1:])
    if hidden:
        app.hide_window()
    root.mainloop()


if __name__ == "__main__":
    main()
