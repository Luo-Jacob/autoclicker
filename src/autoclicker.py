# -*- coding: utf-8 -*-
"""
AutoClicker —— 物理级鼠标 / 键盘连点器

特性:
  1. 使用 Win32 SendInput 在当前光标位置模拟真实按下/释放(非窗口消息投递)
  2. 可配置点击间隔、随机波动、按键(左/中/右键 / 键盘按键或组合)
  3. 全局热键基于 RegisterHotKey, 程序不在前台同样响应
  4. 多组绑定, 各自独立热键与点击配置
  5. 配置持久化到程序目录下 AutoClicker/config.json, 启动加载 / 修改即存
  6. 纯 GUI + 系统托盘, 无控制台窗口; 提供全局急停热键 / 托盘菜单 / 界面按钮三重急停
"""

import ctypes
import json
import os
import queue
import random
import sys
import threading
import time
import uuid
from ctypes import wintypes

APP_NAME = "AutoClicker"
APP_VERSION = "1.0.0"
MUTEX_NAME = "Global\\AutoClicker_SingleInstance"

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


CONFIG_DIR = os.path.join(base_dir(), "AutoClicker")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

MOD_CTRL, MOD_ALT, MOD_SHIFT, MOD_WIN = 0x0002, 0x0001, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
MODS_ORDER = ("ctrl", "alt", "shift", "win")


def new_id():
    return uuid.uuid4().hex[:10]


def default_binding(index=1):
    return {
        "id": new_id(),
        "name": "绑定 %d" % index,
        "enabled": True,
        "hotkey": {"mods": ["ctrl", "alt"], "vk": ord("X")},
        "action": "mouse",           # mouse | keyboard
        "mouse_button": "left",      # left | middle | right
        "kbd": {"mods": [], "vk": 0x74},   # F5
        "interval_ms": 100,
        "jitter_ms": 20,
        "hold_ms": 0,
        "repeat": 0,                 # 0 = 无限
    }


def default_config():
    return {
        "version": 1,
        "settings": {
            "emergency_hotkey": {"mods": ["ctrl", "alt"], "vk": ord("K")},
            "panic_fallback": True,   # 始终启用 Ctrl+Alt+F12 备用急停
            "close_to_tray": True,
            "start_hidden": False,
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
            if os.path.isfile(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.data = self._sanitize(raw)
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
            for k, v in cfg["settings"].items():
                if k in st:
                    cfg["settings"][k] = st[k]
            cfg["settings"]["emergency_hotkey"] = _sanitize_combo(
                st.get("emergency_hotkey"), cfg["settings"]["emergency_hotkey"])
        bl = raw.get("bindings")
        if isinstance(bl, list):
            for item in bl:
                if not isinstance(item, dict):
                    continue
                b = default_binding()
                for k, v in b.items():
                    if k in item:
                        b[k] = item[k]
                b["hotkey"] = _sanitize_combo(item.get("hotkey"), b["hotkey"])
                b["kbd"] = _sanitize_combo(item.get("kbd"), b["kbd"])
                if b["action"] not in ("mouse", "keyboard"):
                    b["action"] = "mouse"
                if b["mouse_button"] not in ("left", "middle", "right"):
                    b["mouse_button"] = "left"
                for k in ("interval_ms", "jitter_ms", "hold_ms", "repeat"):
                    try:
                        b[k] = max(0, int(b[k]))
                    except Exception:
                        b[k] = 0
                b["interval_ms"] = max(1, b["interval_ms"])
                b["enabled"] = bool(b["enabled"])
                if not b.get("id"):
                    b["id"] = new_id()
                cfg["bindings"].append(b)
        return cfg

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
class ClickWorker(threading.Thread):
    def __init__(self, binding, on_stop=None):
        threading.Thread.__init__(self)
        self.daemon = True
        self.name = "ClickWorker-%s" % binding["id"]
        self.binding = binding
        self.on_stop = on_stop
        self.stop_event = threading.Event()
        self.count = 0

    def run(self):
        b = self.binding
        interval = max(1, int(b.get("interval_ms", 100)))
        jitter = max(0, int(b.get("jitter_ms", 0)))
        hold = max(0, int(b.get("hold_ms", 0)))
        repeat = max(0, int(b.get("repeat", 0)))
        action = b.get("action", "mouse")
        button = b.get("mouse_button", "left")
        while not self.stop_event.is_set():
            try:
                if action == "keyboard":
                    keyboard_tap(b.get("kbd") or {})
                else:
                    mouse_click(button, hold)
            except Exception:
                pass
            self.count += 1
            if repeat and self.count >= repeat:
                break
            delay = interval / 1000.0
            if jitter:
                delay += random.uniform(-jitter, jitter) / 1000.0
            if delay < 0.001:
                delay = 0.001
            if self.stop_event.wait(delay):
                break
        if self.on_stop:
            try:
                self.on_stop(b["id"])
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
            return w.count if w else 0

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
            w = ClickWorker(b, on_stop=self._worker_finished)
            self._workers[bid] = w
            w.start()
        self.on_change()
        return True

    def stop(self, bid):
        with self._lock:
            w = self._workers.pop(bid, None)
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

BG = "#f4f6f9"
CARD = "#ffffff"
TEXT = "#1f2937"
MUTED = "#6b7280"
ACCENT = "#2563eb"

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


class BindingDialog(object):
    """新增 / 编辑绑定。"""

    def __init__(self, parent, binding, existing):
        self.binding = json.loads(json.dumps(binding))  # 副本
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
        self.action_var = tk.StringVar(
            value=("keyboard" if self.binding.get("action") == "keyboard"
                   else self.binding.get("mouse_button", "left")))
        self.interval_var = tk.StringVar(value=str(self.binding.get("interval_ms", 100)))
        self.jitter_var = tk.StringVar(value=str(self.binding.get("jitter_ms", 0)))
        self.hold_var = tk.StringVar(value=str(self.binding.get("hold_ms", 0)))
        self.repeat_var = tk.StringVar(value=str(self.binding.get("repeat", 0)))
        self.hk_var = tk.StringVar(value=fmt_combo(self.binding.get("hotkey")))
        self.kbd_var = tk.StringVar(value=fmt_combo(self.binding.get("kbd")))

        row = 0

        def add(label, widget):
            nonlocal row
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 10))
            widget.grid(row=row, column=1, columnspan=3, sticky="ew", pady=5)
            row += 1

        ttk.Label(frm, text="名称").grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(frm, textvariable=self.name_var, width=30).grid(row=row, column=1, columnspan=3, sticky="ew", pady=5)
        row += 1

        ttk.Checkbutton(frm, text="启用该绑定", variable=self.enabled_var).grid(
            row=row, column=1, columnspan=3, sticky="w", pady=(2, 6))
        row += 1

        # 热键
        ttk.Label(frm, text="启动/停止热键").grid(row=row, column=0, sticky="w", pady=5)
        e = ttk.Entry(frm, textvariable=self.hk_var, width=22, state="readonly")
        e.grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(frm, text="录制…", command=self._record_hotkey, width=8).grid(row=row, column=2, padx=6)
        row += 1

        # 动作
        ttk.Label(frm, text="点击动作").grid(row=row, column=0, sticky="w", pady=5)
        act = ttk.Frame(frm)
        act.grid(row=row, column=1, columnspan=3, sticky="w")
        for text, val in (("鼠标左键", "left"), ("鼠标中键", "middle"),
                          ("鼠标右键", "right"), ("键盘按键", "keyboard")):
            ttk.Radiobutton(act, text=text, value=val, variable=self.action_var,
                            command=self._sync).pack(side="left", padx=(0 if val == "left" else 8, 0))
        row += 1

        ttk.Label(frm, text="键盘按键").grid(row=row, column=0, sticky="w", pady=5)
        self.kbd_entry = ttk.Entry(frm, textvariable=self.kbd_var, width=22, state="readonly")
        self.kbd_entry.grid(row=row, column=1, sticky="ew", pady=5)
        self.kbd_btn = ttk.Button(frm, text="录制…", command=self._record_kbd, width=8)
        self.kbd_btn.grid(row=row, column=2, padx=6)
        row += 1

        for label, var in (("间隔(毫秒)", self.interval_var),
                           ("随机波动 ±(毫秒)", self.jitter_var),
                           ("按下保持(毫秒)", self.hold_var),
                           ("重复次数(0=无限)", self.repeat_var)):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Spinbox(frm, from_=0, to=86400000, textvariable=var, width=12).grid(
                row=row, column=1, sticky="w", pady=5)
            row += 1

        ttk.Label(frm, text="点击位置: 始终为鼠标光标当前所在位置(不移动、不锁定光标)。",
                  foreground=MUTED).grid(row=row, column=0, columnspan=4, sticky="w", pady=(10, 2))
        row += 1
        ttk.Label(frm, text="热键为「开关」: 按一次开始连点, 再按一次停止。",
                  foreground=MUTED).grid(row=row, column=0, columnspan=4, sticky="w")
        row += 1

        bar = ttk.Frame(frm)
        bar.grid(row=row, column=0, columnspan=4, sticky="e", pady=(16, 0))
        ttk.Button(bar, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(bar, text="保存", command=self._save, style="Accent.TButton").pack(side="right", padx=(0, 8))

        frm.columnconfigure(1, weight=1)
        self._sync()
        self.top.update_idletasks()
        try:
            self.top.geometry("+%d+%d" % (parent.winfo_rootx() + 60, parent.winfo_rooty() + 40))
        except Exception:
            pass
        parent.wait_window(self.top)

    def _sync(self):
        kb = self.action_var.get() == "keyboard"
        self.kbd_entry.configure(state="readonly")
        self.kbd_btn.configure(state="normal" if kb else "disabled")

    def _record_hotkey(self):
        dlg = RecordDialog(self.top, "录制热键", self.binding.get("hotkey"), allow_single=False)
        if dlg.result:
            self.binding["hotkey"] = dlg.result
            self.hk_var.set(fmt_combo(dlg.result))

    def _record_kbd(self):
        dlg = RecordDialog(self.top, "录制键盘按键", self.binding.get("kbd"), allow_single=True)
        if dlg.result:
            self.binding["kbd"] = dlg.result
            self.kbd_var.set(fmt_combo(dlg.result))

    def _save(self):
        name = self.name_var.get().strip() or "绑定"
        if not self.binding.get("hotkey") or not self.binding["hotkey"].get("vk"):
            messagebox.showwarning("缺少热键", "请先为该绑定录制一个启动/停止热键。", parent=self.top)
            return
        act = self.action_var.get()
        if act == "keyboard" and not self.binding.get("kbd", {}).get("vk"):
            messagebox.showwarning("缺少按键", "请选择要模拟的键盘按键。", parent=self.top)
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
        self.binding["name"] = name
        self.binding["enabled"] = bool(self.enabled_var.get())
        if act == "keyboard":
            self.binding["action"] = "keyboard"
        else:
            self.binding["action"] = "mouse"
            self.binding["mouse_button"] = act
        self.binding.update(nums)
        self.ok = True
        self.top.grab_release()
        self.top.destroy()

    def _cancel(self):
        self.ok = False
        self.top.grab_release()
        self.top.destroy()


class SettingsDialog(object):
    def __init__(self, parent, config):
        self.config = config
        self.ok = False
        self.top = tk.Toplevel(parent)
        self.top.title("设置")
        self.top.configure(bg=BG)
        self.top.transient(parent)
        self.top.resizable(False, False)
        self.top.grab_set()

        frm = ttk.Frame(self.top, padding=16)
        frm.pack(fill="both", expand=True)

        st = config.settings
        self.em_var = tk.StringVar(value=fmt_combo(st.get("emergency_hotkey")))
        self.fallback_var = tk.BooleanVar(value=bool(st.get("panic_fallback", True)))
        self.tray_var = tk.BooleanVar(value=bool(st.get("close_to_tray", True)))
        self.hidden_var = tk.BooleanVar(value=bool(st.get("start_hidden", False)))

        ttk.Label(frm, text="全局设置", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(frm, text="紧急停止热键").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=self.em_var, width=22, state="readonly").grid(row=1, column=1, sticky="ew", pady=6)
        ttk.Button(frm, text="录制…", width=8, command=self._record).grid(row=1, column=2, padx=6)

        self.new_em = st.get("emergency_hotkey")

        ttk.Checkbutton(frm, text="始终保留 Ctrl+Alt+F12 作为备用急停热键",
                        variable=self.fallback_var).grid(row=2, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Checkbutton(frm, text="关闭窗口时最小化到系统托盘(不退出程序)",
                        variable=self.tray_var).grid(row=3, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Checkbutton(frm, text="程序启动时隐藏主界面(仅托盘)",
                        variable=self.hidden_var).grid(row=4, column=0, columnspan=3, sticky="w", pady=4)

        ttk.Label(frm, text="配置文件: %s" % CONFIG_FILE, foreground=MUTED).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(14, 2))
        ttk.Button(frm, text="打开配置目录", command=self._open_dir).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(2, 10))

        bar = ttk.Frame(frm)
        bar.grid(row=7, column=0, columnspan=3, sticky="e")
        ttk.Button(bar, text="取消", command=self._close).pack(side="right")
        ttk.Button(bar, text="保存", command=self._save, style="Accent.TButton").pack(side="right", padx=(0, 8))

        frm.columnconfigure(1, weight=1)
        self.top.update_idletasks()
        try:
            self.top.geometry("+%d+%d" % (parent.winfo_rootx() + 80, parent.winfo_rooty() + 60))
        except Exception:
            pass
        parent.wait_window(self.top)

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
        self.config.settings["emergency_hotkey"] = self.new_em
        self.config.settings["panic_fallback"] = bool(self.fallback_var.get())
        self.config.settings["close_to_tray"] = bool(self.tray_var.get())
        self.config.settings["start_hidden"] = bool(self.hidden_var.get())
        self.config.save()
        self.ok = True
        self._close()

    def _close(self):
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
        self.root.geometry("780x520")
        self.root.minsize(680, 440)
        self.root.configure(bg=BG)

        self._style()
        self._build()
        self._refresh_tree()

        # 工作线程 -> 主线程: 一律经队列转发, 绝不在子线程触碰任何 tk 控件
        engine.on_change = self.request_refresh
        engine.rebuild_hotkeys()
        self._setup_tray()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll()

    # ---- 样式 ----
    def _style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 9))
        st.configure("TFrame", background=BG)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Card.TFrame", background=CARD, relief="flat")
        st.configure("TButton", padding=(10, 6))
        st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                     borderwidth=0, padding=(12, 6))
        st.map("Accent.TButton", background=[("active", "#1d4ed8")])
        st.configure("Danger.TButton", background="#dc2626", foreground="#ffffff",
                     borderwidth=0, padding=(14, 8))
        st.map("Danger.TButton", background=[("active", "#b91c1c")])
        st.configure("Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, rowheight=28, borderwidth=0)
        st.configure("Treeview.Heading", background="#e5e7eb", foreground=TEXT,
                     font=("Microsoft YaHei UI", 9, "bold"))
        st.map("Treeview", background=[("selected", "#dbeafe")],
               foreground=[("selected", TEXT)])

    # ---- 界面 ----
    def _build(self):
        head = ttk.Frame(self.root, padding=(16, 14, 16, 6))
        head.pack(fill="x")
        ttk.Label(head, text=APP_NAME, font=("Microsoft YaHei UI", 15, "bold"),
                  foreground=TEXT).pack(side="left")
        self.state_label = ttk.Label(head, text="就绪", foreground=MUTED)
        self.state_label.pack(side="left", padx=(14, 0))
        ttk.Button(head, text="设置", command=self.open_settings, width=8).pack(side="right")
        ttk.Button(head, text="隐藏到托盘", command=self.hide_window, width=11).pack(side="right", padx=(0, 8))

        bar = ttk.Frame(self.root, padding=(16, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="＋ 新增", command=self.add_binding, style="Accent.TButton").pack(side="left")
        ttk.Button(bar, text="编辑", command=self.edit_binding, width=8).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="删除", command=self.delete_binding, width=8).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="启动 / 停止", command=self.toggle_selected, width=12).pack(side="left", padx=(16, 0))
        ttk.Button(bar, text="⏹ 紧急停止(全部)", command=self.emergency_stop,
                   style="Danger.TButton").pack(side="right")

        wrap = ttk.Frame(self.root, padding=(16, 8))
        wrap.pack(fill="both", expand=True)
        cols = ("status", "name", "hotkey", "action", "interval", "count")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="browse")
        heads = {"status": "状态", "name": "名称", "hotkey": "热键", "action": "点击动作",
                 "interval": "间隔(ms)", "count": "已点击"}
        widths = {"status": 110, "name": 130, "hotkey": 150, "action": 150,
                  "interval": 110, "count": 90}
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

        foot = ttk.Frame(self.root, padding=(16, 6, 16, 12))
        foot.pack(fill="x")
        self.tip = tk.StringVar(value="双击行可编辑；热键为开关模式(按一次开始，再按一次停止)，全局生效。")
        ttk.Label(foot, textvariable=self.tip, foreground=MUTED).pack(side="left")
        ttk.Label(foot, text="配置: %s" % CONFIG_FILE, foreground=MUTED).pack(side="right")

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
            if b["action"] == "keyboard":
                action = "键盘 " + fmt_combo(b.get("kbd"))
            else:
                action = {"left": "鼠标左键", "middle": "鼠标中键", "right": "鼠标右键"}[b.get("mouse_button", "left")]
            jit = int(b.get("jitter_ms", 0))
            interval = "%d" % int(b.get("interval_ms", 100))
            if jit:
                interval += " ±%d" % jit
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
        dlg = SettingsDialog(self.root, self.config)
        if dlg.ok:
            self.engine.rebuild_hotkeys()
            self._refresh_tree()
            self._notify("设置已保存", APP_NAME)

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
        if self.config.settings.get("close_to_tray", True):
            self.hide_window()
        else:
            self.quit_app()

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
    if config.settings.get("start_hidden", False):
        app.hide_window()
    root.mainloop()


if __name__ == "__main__":
    main()
