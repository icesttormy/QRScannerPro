"""
截屏识别 URL / 二维码 工具 —— PySide6 版（后台常驻 + 可配置版）
--------------------------------------------------------------
在原版基础上新增：
    1. 启动后常驻系统托盘，不再一打开就强制截图
    2. 托盘菜单：立即截图 / 设置 / 开机自启动 / 退出
    3. 全局快捷键（默认 Ctrl+Alt+Q），在任意界面按下即可截图识别
    4. 设置界面：
        - 选择跳转链接使用的浏览器（留空 = 系统默认浏览器）
        - 自定义全局快捷键
        - 开机自启动开关
    5. 配置保存在 %APPDATA%/ScreenQRScanner/config.json（Windows）
       或 ~/.config/ScreenQRScanner/config.json（其他系统）

识别逻辑（框选/二维码/OCR）与原版完全一致，未做改动。

依赖:
    pip install pyside6 opencv-python numpy pytesseract pyzbar pyinstaller

    - 全局快捷键在 Windows 上使用系统 RegisterHotKey 实现，无需额外依赖。
    - 开机自启动 / 全局快捷键均为 Windows 特性，其他系统会自动跳过。
    - 文字网址识别需要额外安装 Tesseract-OCR 引擎本体（不装也不影响二维码识别）:
      https://github.com/UB-Mannheim/tesseract/wiki
      装好后把下面 TESSERACT_CMD 改成实际安装路径。
"""

import sys
import os
import re
import json
import tempfile
import shutil
import subprocess
import webbrowser
import traceback
import ctypes
import threading
from urllib.parse import urlsplit, urlunsplit

# ---------- 无控制台窗口下的输出安全处理 ----------
# 用 --windowed/--noconsole 打包后，sys.stdout / sys.stderr 会是 None。
# 任何 print()，或任何未捕获异常想打印堆栈，都会因为往 None 写东西而直接崩掉整个程序，
# 而且崩溃时没有控制台看不到任何提示，表现就是"点了没反应"。
# 这里统一把输出重定向到日志文件：既不会再崩，出问题时也能翻日志看原因。
if sys.stdout is None or sys.stderr is None:
    try:
        _log_base = (os.environ.get("APPDATA", os.path.expanduser("~"))
                     if sys.platform == "win32" else os.path.expanduser("~/.config"))
        _log_dir = os.path.join(_log_base, "ScreenQRScanner")
        os.makedirs(_log_dir, exist_ok=True)
        _log_file = open(os.path.join(_log_dir, "run.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = _log_file
        sys.stderr = _log_file
    except Exception:
        import io
        sys.stdout = sys.stderr = io.StringIO()


def _log_exceptions(exc_type, exc_value, exc_tb):
    traceback.print_exception(exc_type, exc_value, exc_tb)


sys.excepthook = _log_exceptions

import numpy as np
import cv2

from PySide6.QtCore import (
    Qt, QObject, QRect, QRectF, Signal, QPropertyAnimation, QEasingCurve,
    QAbstractNativeEventFilter, QTimer, QPoint,
)
from PySide6.QtGui import (
    QPainter, QPainterPath, QColor, QPen, QFont, QGuiApplication, QImage,
    QIcon, QKeySequence, QAction, QCursor,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout,
    QPushButton, QGraphicsDropShadowEffect, QFrame, QDialog, QLineEdit,
    QCheckBox, QFileDialog, QKeySequenceEdit, QMessageBox, QSystemTrayIcon, QMenu,
    QScrollArea,
)

# ---------- Windows 全局快捷键(RegisterHotKey) ----------
# 不再使用 keyboard 库的低级键盘钩子：那种钩子在长时间运行、锁屏/睡眠后
# 很容易被 Windows 静默移除，表现为"用了很久之后快捷键突然失效"。
# RegisterHotKey 是系统提供的稳定接口，消息会走 Qt 主线程事件循环。
if sys.platform == "win32":
    WM_HOTKEY = 0x0312
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_WIN = 0x0008
    MOD_NOREPEAT = 0x4000

    _MODIFIER_MAP = {
        "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
        "alt": MOD_ALT,
        "shift": MOD_SHIFT,
        "win": MOD_WIN, "windows": MOD_WIN, "meta": MOD_WIN,
    }

    _SPECIAL_VK = {
        "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
        "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E,
        "del": 0x2E, "insert": 0x2D, "ins": 0x2D, "home": 0x24, "end": 0x23,
        "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    }

    def _parse_hotkey(hotkey: str):
        """把 'ctrl+alt+q' 解析成 (modifiers, virtual_key)，不支持则返回 None。"""
        mods = 0
        key = None
        for part in (hotkey or "").lower().split("+"):
            part = part.strip()
            if part in _MODIFIER_MAP:
                mods |= _MODIFIER_MAP[part]
            else:
                key = part
        if not key:
            return None
        if len(key) == 1:
            if "a" <= key <= "z":
                vk = ord(key.upper())
            elif "0" <= key <= "9":
                vk = ord(key)
            else:
                return None
        elif key in _SPECIAL_VK:
            vk = _SPECIAL_VK[key]
        elif key.startswith("f") and key[1:].isdigit():
            n = int(key[1:])
            if not 1 <= n <= 24:
                return None
            vk = 0x70 + n - 1
        else:
            return None
        return mods, vk

    class _W32MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("message", ctypes.c_uint),
            ("wParam", ctypes.c_ulonglong),
            ("lParam", ctypes.c_longlong),
            ("time", ctypes.c_uint),
            ("pt_x", ctypes.c_long),
            ("pt_y", ctypes.c_long),
        ]

    class WindowsHotkeyFilter(QAbstractNativeEventFilter):
        def __init__(self, on_hotkey):
            super().__init__()
            self.on_hotkey = on_hotkey

        def nativeEventFilter(self, eventType, message):
            try:
                if eventType == "windows_generic_MSG":
                    msg = _W32MSG.from_address(int(message))
                    if msg.message == WM_HOTKEY:
                        self.on_hotkey()
                        return True, 0
            except Exception:
                traceback.print_exc()
            return False, 0

    def _win_register_hotkey(hotkey: str):
        parsed = _parse_hotkey(hotkey)
        if parsed is None:
            return False, None
        mods, vk = parsed
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
        user32.RegisterHotKey.restype = ctypes.c_int
        if not user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
            print(f"RegisterHotKey 失败，错误码 {ctypes.get_last_error()}: {hotkey}")
            return False, None
        return True, 1

    def _win_unregister_hotkey(hotkey_id):
        if not hotkey_id:
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.UnregisterHotKey.restype = ctypes.c_int
        user32.UnregisterHotKey(None, hotkey_id)


# ---------- OCR 依赖(可选) ----------
# 优先用打包进 exe 里的 tesseract_bin（相对路径，任何电脑都能用）；
# 找不到打包版时（比如开发环境直接跑 main.py），退回本机安装路径。
def _resolve_tesseract_cmd() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    bundled = os.path.join(base, "tesseract_bin", "tesseract.exe")
    candidates = [
        bundled,
        os.environ.get("TESSERACT_CMD", ""),
        shutil.which("tesseract") or "",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            if os.path.abspath(candidate) == os.path.abspath(bundled):
                os.environ["TESSDATA_PREFIX"] = os.path.join(
                    base, "tesseract_bin", "tessdata"
                )
            return candidate
    return ""


TESSERACT_CMD = _resolve_tesseract_cmd()
try:
    import pytesseract
    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    OCR_AVAILABLE = bool(TESSERACT_CMD)
except ImportError:
    OCR_AVAILABLE = False

# ---------- 二维码依赖(可选，但强烈建议安装) ----------
try:
    from pyzbar.pyzbar import decode as zbar_decode
    ZBAR_AVAILABLE = True
except Exception:
    ZBAR_AVAILABLE = False

# ---------- 全局快捷键 ----------
# Windows 首选系统 RegisterHotKey；若组合键被其他程序占用，则退回 keyboard
# 库的低级钩子作为备用，保证快捷键仍可用。非 Windows 平台只用 keyboard。
try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except Exception:
    keyboard = None
    KEYBOARD_AVAILABLE = False

HOTKEY_AVAILABLE = True if sys.platform == "win32" else KEYBOARD_AVAILABLE


_SINGLE_INSTANCE_HANDLE = None


def acquire_single_instance() -> bool:
    """Windows 下用命名互斥量保证只运行一个实例；已存在实例则返回 False。"""
    global _SINGLE_INSTANCE_HANDLE
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.CreateMutexW(None, False, "ScreenQRScanner_SingleInstance_Mutex")
    if not handle:
        return True
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _SINGLE_INSTANCE_HANDLE = handle
    return True


# ============ 资源路径（兼容 PyInstaller 打包） ============

def resource_path(rel_path: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)


# ============ 配置管理 ============

APP_NAME = "ScreenQRScanner"

DEFAULT_CONFIG = {
    "browser_path": "",        # 空 = 使用系统默认浏览器
    "hotkey": "ctrl+alt+q",    # 小写 + 号连接
    "autostart": False,
    "confirm_open": True,      # 打开单个链接前先展示结果
}


def get_config_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.expanduser("~/.config")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


CONFIG_PATH = os.path.join(get_config_dir(), "config.json")


def _sanitize_config(raw) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        return cfg

    if isinstance(raw.get("browser_path"), str):
        cfg["browser_path"] = raw["browser_path"].strip()
    if isinstance(raw.get("hotkey"), str) and raw["hotkey"].strip():
        cfg["hotkey"] = raw["hotkey"].strip()
    if isinstance(raw.get("autostart"), bool):
        cfg["autostart"] = raw["autostart"]
    if isinstance(raw.get("confirm_open"), bool):
        cfg["confirm_open"] = raw["confirm_open"]
    return cfg


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return _sanitize_config(json.load(f))
    except Exception as e:
        print(f"读取配置失败，使用默认配置: {e}")
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> bool:
    """原子写入配置，避免进程中断留下半截 JSON。"""
    tmp_path = None
    try:
        config_dir = get_config_dir()
        fd, tmp_path = tempfile.mkstemp(
            prefix="config.", suffix=".tmp", dir=config_dir
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(_sanitize_config(cfg), f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_PATH)
        tmp_path = None
        return True
    except Exception as e:
        print(f"保存配置失败: {e}")
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def set_autostart(enable: bool):
    """写入 / 移除 Windows 开机自启项，返回 (是否成功, 错误信息)。"""
    if sys.platform != "win32":
        return False, "当前系统不支持开机自启动"
    try:
        import winreg
    except ImportError:
        return False, "当前 Python 环境缺少 winreg"

    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE
        )
    except Exception as e:
        print(f"打开注册表失败: {e}")
        return False, str(e)

    try:
        if enable:
            if getattr(sys, "frozen", False):
                # PyInstaller 打包后，sys.executable 就是 exe 本身
                exe_cmd = f'"{sys.executable}"'
            else:
                exe_cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_cmd)
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        return True, ""
    except Exception as e:
        print(f"写入开机自启项失败: {e}")
        return False, str(e)
    finally:
        winreg.CloseKey(key)


def open_url(url: str, config: dict) -> bool:
    """只打开校验通过的 http/https 链接。"""
    safe_url = parse_openable_url(url)
    if safe_url is None:
        print(f"拒绝打开非 http/https 内容: {url!r}")
        return False

    browser_path = (config or {}).get("browser_path", "").strip()
    if browser_path:
        try:
            popen_kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                             "stderr": subprocess.DEVNULL}
            if sys.platform == "win32":
                # --windowed 打包后没有控制台，继承标准句柄可能报错，这里显式规避
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            subprocess.Popen([browser_path, safe_url], **popen_kwargs)
            return True
        except Exception:
            print("使用自定义浏览器打开失败，已回退到系统默认浏览器:\n" + traceback.format_exc())
    try:
        return bool(webbrowser.open(safe_url))
    except Exception:
        print("系统默认浏览器打开失败:\n" + traceback.format_exc())
        return False


# ============ 网址匹配 ============

_KNOWN_GTLDS = {
    "com", "net", "org", "io", "co", "me", "cc", "top", "xyz", "info",
    "biz", "gov", "edu", "tv", "app", "dev", "site", "online", "store",
    "tech", "cloud", "pro", "mobi", "name", "club", "vip", "win", "link",
    "live", "life", "world", "today", "news", "blog", "wiki", "page",
    "space", "website", "fun", "icu", "ink", "art", "design", "email",
    "group", "network", "solutions", "services", "company", "business",
    "shop", "work", "zone", "city", "plus", "chat", "social", "media",
    "digital", "studio", "agency", "center", "global", "one", "run", "fit",
    "fan", "games", "game", "video", "photo", "music", "audio", "film",
    "money", "finance", "bank", "law", "doctor", "health", "care", "host",
    "web", "support", "systems", "tools", "software", "codes", "asia", "eu",
}

_BARE_DOMAIN_RE = re.compile(
    r'(?<![\w@.-])'
    r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+'
    r'[a-zA-Z]{2,24}'
    r'(?::[0-9]{1,5})?'
    r'(?:/[^\s\u4e00-\u9fff<>"\']*)?',
    re.IGNORECASE,
)

URL_PATTERN = re.compile(
    r'https?://[^\s<>"\']+'
    r'|'
    r'(?<![\w@.-])(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+'
    r'[a-zA-Z]{2,24}(?::[0-9]{1,5})?(?:/[^\s\u4e00-\u9fff<>"\']*)?',
    re.IGNORECASE,
)

_URL_TRAILING_CHARS = '.,;:!?)]}>"\''


def _trim_url_candidate(text: str) -> str:
    return (text or "").strip().rstrip(_URL_TRAILING_CHARS)


def _valid_hostname(host: str, require_domain: bool = False) -> bool:
    host = (host or "").rstrip(".").lower()
    if not host or len(host) > 253 or any(ch.isspace() for ch in host):
        return False

    try:
        import ipaddress
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass

    labels = host.split(".")
    if any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        return False

    if not require_domain:
        return True
    if len(labels) < 2:
        return False

    tld = labels[-1]
    return len(tld) == 2 or tld in _KNOWN_GTLDS


def parse_openable_url(text: str):
    """Return a normalized http/https URL, or None for non-openable payloads."""
    candidate = _trim_url_candidate(text)
    if not candidate or any(ch.isspace() for ch in candidate):
        return None

    scheme_match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", candidate)
    if scheme_match:
        if scheme_match.group(1).lower() not in {"http", "https"}:
            return None
        normalized = candidate
    else:
        if not _BARE_DOMAIN_RE.fullmatch(candidate):
            return None
        normalized = "http://" + candidate

    try:
        parts = urlsplit(normalized)
    except ValueError:
        return None

    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    if not _valid_hostname(parts.hostname, require_domain=not scheme_match):
        return None
    return urlunsplit((
        parts.scheme.lower(), parts.netloc, parts.path or "",
        parts.query, parts.fragment,
    ))


def normalize_url(url: str) -> str:
    normalized = parse_openable_url(url)
    return normalized if normalized is not None else _trim_url_candidate(url)


def as_open_url(text: str) -> str:
    """二维码内容如果本身就是网址就规范化，否则原样返回（比如纯文本二维码）"""
    normalized = parse_openable_url(text)
    return normalized if normalized is not None else (text or "").strip()


# ============ 图像识别 ============

def _build_qr_variants(gray):
    """生成一批预处理变体(原图/二值化/闭运算/缩放)，pyzbar 和 OpenCV 兜底共用，
    确保不管走哪条识别路径，识别率都一样强"""
    variants = [(gray, 1.0)]

    # Otsu 二值化 + 形态学闭运算：把"美化二维码"里的圆点糊合成实心方块
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append((otsu, 1.0))
    kernel = np.ones((3, 3), np.uint8)
    closed = cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, kernel)
    variants.append((closed, 1.0))

    # 缩小图像：用区域平均插值，让圆点自然融合成实心块，往往比放大更有效
    for down_scale in (0.5, 0.3):
        small = cv2.resize(gray, None, fx=down_scale, fy=down_scale,
                            interpolation=cv2.INTER_AREA)
        variants.append((small, down_scale))
        _, small_otsu = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append((small_otsu, down_scale))

    # 放大
    for scale in (1.5, 2.0, 3.0):
        big = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        variants.append((big, scale))

    # 自适应二值化，应对反光/低对比度的截图
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY, 35, 5)
    variants.append((thresh, 1.0))
    return variants


def detect_qrcodes(cv_img):
    """多尺度 + 多种预处理重试，尽量提高大范围截图下的二维码识别率。
    pyzbar 在某些电脑上可能因为缺少运行库而在调用时抛异常(不只是导入失败)，
    这里额外用 try/except 兜住，避免直接影响整体识别流程。"""
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    variants = _build_qr_variants(gray)

    if ZBAR_AVAILABLE:
        boxes = []
        try:
            for img_variant, scale in variants:
                results = zbar_decode(img_variant)
                if not results:
                    continue
                for r in results:
                    text = r.data.decode('utf-8', errors='ignore')
                    if not text:
                        continue
                    x, y, w, h = r.rect
                    normalized = parse_openable_url(text)
                    boxes.append({
                        "x1": int(x / scale), "y1": int(y / scale),
                        "x2": int((x + w) / scale), "y2": int((y + h) / scale),
                        "url": normalized if normalized is not None else text.strip(),
                        "openable": normalized is not None,
                        "kind": "qrcode",
                    })
                if boxes:
                    return boxes
        except Exception:
            pass  # zbar 运行时异常(如动态库加载失败)，退回 OpenCV 方案

    return _detect_qrcodes_opencv_fallback(cv_img, variants)


def _detect_qrcodes_opencv_fallback(cv_img, variants=None):
    """pyzbar 不可用/识别失败时，用 OpenCV 自带检测器兜底，
    同样跑一遍多种预处理变体，而不是只试原图"""
    detector = cv2.QRCodeDetector()

    def _try(img):
        found = []
        try:
            ok, decoded_info, points, _ = detector.detectAndDecodeMulti(img)
        except cv2.error:
            return found
        if not ok:
            return found
        for text, pts in zip(decoded_info, points):
            if not text:
                continue
            xs, ys = pts[:, 0], pts[:, 1]
            found.append((text, xs.min(), ys.min(), xs.max(), ys.max()))
        return found

    # 先试原始彩色图
    results = _try(cv_img)
    if results:
        return [{
            "x1": int(x1), "y1": int(y1), "x2": int(x2), "y2": int(y2),
            "url": as_open_url(text),
            "openable": parse_openable_url(text) is not None,
            "kind": "qrcode",
        } for text, x1, y1, x2, y2 in results]

    if variants is None:
        gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
        variants = _build_qr_variants(gray)

    for img_variant, scale in variants:
        results = _try(img_variant)
        if not results:
            continue
        boxes = [{
            "x1": int(x1 / scale), "y1": int(y1 / scale),
            "x2": int(x2 / scale), "y2": int(y2 / scale),
            "url": as_open_url(text),
            "openable": parse_openable_url(text) is not None,
            "kind": "qrcode",
        } for text, x1, y1, x2, y2 in results]
        if boxes:
            return boxes
    return []


def detect_urls_by_ocr(cv_img):
    boxes = []
    if not OCR_AVAILABLE:
        return boxes
    rgb = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
    # 加 timeout 防止 tesseract 偶尔卡死时把整个识别流程拖住。
    data = pytesseract.image_to_data(
        rgb, lang='eng', output_type=pytesseract.Output.DICT, timeout=15,
    )

    for i in range(len(data['text'])):
        word = data['text'][i].strip()
        if not word:
            continue
        for m in URL_PATTERN.finditer(word):
            url = parse_openable_url(m.group(0))
            if url is None:
                continue
            x, y, w, h = data['left'][i], data['top'][i], data['width'][i], data['height'][i]
            boxes.append({"x1": x, "y1": y, "x2": x + w, "y2": y + h,
                          "url": url, "openable": True, "kind": "text"})
    return boxes


def dedupe_boxes(boxes):
    """
    很多截图里二维码旁边就印着同一个网址的文字（或者网页上二维码下面写着链接），
    这会导致 zbar 识别到一次、OCR 又识别到一次，同一个链接出现两条结果，
    从而触发"多个结果"的列表窗口，而不是本该有的自动跳转。
    这里按规范化后的 URL 去重，同一个链接只保留一条（优先保留二维码识别的那条）。
    """
    def _key(value):
        value = (value or "").strip()
        try:
            parts = urlsplit(value)
            if parts.scheme and parts.netloc:
                return urlunsplit((
                    parts.scheme.lower(), parts.netloc.lower(),
                    parts.path, parts.query, parts.fragment,
                )).rstrip("/")
        except ValueError:
            pass
        return value.rstrip("/")

    seen = {}
    result = []
    for b in boxes:
        key = _key(b["url"])
        if key in seen:
            idx = seen[key]
            if result[idx]["kind"] != "qrcode" and b["kind"] == "qrcode":
                result[idx] = b
            continue
        seen[key] = len(result)
        result.append(b)
    return result


def run_detection(cv_img):
    # 二维码识别是纯内存操作，基本不会失败；OCR 会调用外部 tesseract，
    # 单独兜住异常，避免 OCR 失败时把已经识别出的二维码结果一起丢掉。
    boxes = detect_qrcodes(cv_img)
    try:
        boxes += detect_urls_by_ocr(cv_img)
    except Exception:
        print("文字网址识别失败:\n" + traceback.format_exc())
    return dedupe_boxes(boxes)


# ============ 深色扁平风格公共样式 ============

DARK_BG = "#1c1e22"
CARD_BG = "#24262b"
ACCENT_OK = "#00c896"
ACCENT_ERR = "#ff5252"
TEXT_MAIN = "#e8e8ea"
TEXT_SUB = "#9a9ea6"


def color_with_alpha(hex_color: str, alpha: int) -> str:
    color = QColor(hex_color)
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def drop_shadow(blur=32, y=8, alpha=170):
    eff = QGraphicsDropShadowEffect()
    eff.setBlurRadius(blur)
    eff.setOffset(0, y)
    eff.setColor(QColor(0, 0, 0, alpha))
    return eff


# ============ 1. 框选遮罩层 ============

class OverlaySelector(QWidget):
    selection_made = Signal(QRect)
    canceled = Signal()

    def __init__(self, background_pixmap, screen_geo):
        super().__init__()
        self.bg = background_pixmap
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setGeometry(screen_geo)
        self.setCursor(Qt.CrossCursor)
        self.start = None
        self.end = None
        self.selecting = False
        self._finished = False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.bg)

        overlay_color = QColor(10, 12, 16, 130)
        if self.start and self.end:
            rect = QRect(self.start, self.end).normalized()
            path = QPainterPath()
            path.addRect(QRectF(self.rect()))
            hole = QPainterPath()
            hole.addRect(QRectF(rect))
            path = path.subtracted(hole)
            painter.fillPath(path, overlay_color)

            pen = QPen(QColor(ACCENT_OK), 2)
            painter.setPen(pen)
            painter.drawRect(rect)

            size_text = f"{rect.width()} × {rect.height()}"
            painter.setFont(QFont("Microsoft YaHei UI", 10))
            painter.setPen(QColor(TEXT_MAIN))
            painter.drawText(rect.x(), max(rect.y() - 8, 14), size_text)
        else:
            painter.fillRect(self.rect(), overlay_color)

        painter.setPen(QColor(TEXT_MAIN))
        painter.setFont(QFont("Microsoft YaHei UI", 13))
        painter.drawText(self.rect().adjusted(0, 40, 0, 0),
                          Qt.AlignHCenter | Qt.AlignTop,
                          "拖动鼠标框选区域    Esc 取消并退出")

    def mousePressEvent(self, event):
        self.start = event.position().toPoint()
        self.end = self.start
        self.selecting = True
        self.update()

    def mouseMoveEvent(self, event):
        if self.selecting:
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        self.selecting = False
        self.end = event.position().toPoint()
        rect = QRect(self.start, self.end).normalized()
        if rect.width() > 5 and rect.height() > 5:
            # 先关闭框选窗口，再触发后续识别/跳转逻辑：
            # 这样即使后面处理时出错，也不会导致框选界面卡住关不掉。
            self._finished = True
            self.close()
            self.selection_made.emit(rect)
        else:
            self.start = self.end = None
            self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self._finished = True
            self.canceled.emit()
            self.close()

    def closeEvent(self, event):
        if not self._finished:
            self._finished = True
            self.canceled.emit()
        super().closeEvent(event)


# ============ 2. 多结果时的可点击标注窗口 ============

class ResultViewer(QWidget):
    closed = Signal()

    def __init__(self, pixmap, boxes, config, screen=None):
        super().__init__()
        self.config = config
        self.pixmap = pixmap
        self.boxes = list(boxes)
        self._screen = (
            screen
            or QGuiApplication.screenAt(QCursor.pos())
            or QGuiApplication.primaryScreen()
        )
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)

        card = QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet(f"""
            QFrame#card {{
                background-color: {CARD_BG};
                border-radius: 14px;
                border-left: 4px solid {ACCENT_OK};
            }}
            QLabel {{
                color: {TEXT_MAIN};
                background: transparent;
                font-family: "Microsoft YaHei UI", "Microsoft YaHei";
            }}
            QPushButton {{
                background-color: #34363b;
                color: {TEXT_MAIN};
                border: none;
                border-radius: 6px;
                padding: 6px 10px;
                font-family: "Microsoft YaHei UI", "Microsoft YaHei";
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: #45484e; }}
        """)
        card.setGraphicsEffect(drop_shadow())
        screen_geo = self._screen.availableGeometry()
        card.setFixedWidth(max(360, min(560, screen_geo.width() - 48)))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        has_openable = any(box.get("openable") for box in self.boxes)
        action_hint = "可复制或打开" if has_openable else "可复制"
        title = QLabel(f"识别到 {len(boxes)} 个结果 · {action_hint}")
        title.setStyleSheet("font-size:14px; font-weight:600;")
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(26, 26)
        close_btn.setStyleSheet("""
            QPushButton { background:#34363b; color:#c8ccd4; border:none; border-radius:13px; font-size:12px; }
            QPushButton:hover { background:#45484e; }
        """)
        close_btn.clicked.connect(self.close)
        header.addWidget(title)
        header.addStretch()
        if len(self.boxes) > 1:
            copy_all_btn = QPushButton("复制全部")
            copy_all_btn.setToolTip("复制所有识别到的文本")
            copy_all_btn.clicked.connect(self._copy_all)
            header.addWidget(copy_all_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setMaximumHeight(max(140, int(screen_geo.height() * 0.55)))

        rows_widget = QWidget()
        rows_widget.setObjectName("rows")
        rows_widget.setStyleSheet("QWidget#rows { background: transparent; }")
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(0, 0, 4, 0)
        rows_layout.setSpacing(8)
        for box in self.boxes:
            rows_layout.addWidget(self._make_row(box))
        rows_layout.addStretch()
        scroll.setWidget(rows_widget)
        layout.addWidget(scroll, 1)

        self.adjustSize()
        x = screen_geo.center().x() - self.width() // 2
        y = screen_geo.center().y() - self.height() // 2
        x = max(screen_geo.left() + 8, min(x, screen_geo.right() - self.width() - 8))
        y = max(screen_geo.top() + 8, min(y, screen_geo.bottom() - self.height() - 8))
        self.move(x, y)

    def _make_row(self, box):
        is_qr = box.get("kind") == "qrcode"
        openable = bool(box.get("openable"))
        accent = "#00e676" if is_qr else "#448aff"
        tag = "二维码" if is_qr else "网址"
        if not openable:
            tag = "二维码文本" if is_qr else "文本"
        text = str(box.get("url", ""))

        row = QFrame()
        row.setObjectName("row")
        row.setStyleSheet(f"""
            QFrame#row {{
                background-color: #2b2d33;
                border: 1px solid #3a3d44;
                border-left: 3px solid {accent};
                border-radius: 8px;
            }}
        """)

        body = QHBoxLayout(row)
        body.setContentsMargins(12, 10, 10, 10)
        body.setSpacing(10)

        info = QVBoxLayout()
        info.setSpacing(4)
        tag_label = QLabel(f"[{tag}]")
        tag_label.setStyleSheet(
            f"color:{accent}; font-size:11px; font-weight:600;"
        )
        text_label = QLabel(text)
        text_label.setWordWrap(True)
        text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_label.setToolTip(text)
        text_label.setStyleSheet(
            "color:#e8e8ea; font-family:'Microsoft YaHei UI','Consolas';"
            "font-size:12px;"
        )
        info.addWidget(tag_label)
        info.addWidget(text_label)
        body.addLayout(info, 1)

        actions = QVBoxLayout()
        actions.setSpacing(6)
        if openable:
            open_btn = QPushButton("打开")
            open_btn.setFixedWidth(64)
            open_btn.setToolTip("在浏览器中打开这个链接")
            open_btn.clicked.connect(
                lambda _checked=False, value=text: self._open_link(value)
            )
            actions.addWidget(open_btn)
        copy_btn = QPushButton("复制")
        copy_btn.setFixedWidth(64)
        copy_btn.setToolTip("复制完整内容")
        copy_btn.clicked.connect(
            lambda _checked=False, value=text, button=copy_btn:
            self._copy_text(value, button)
        )
        actions.addWidget(copy_btn)
        body.addLayout(actions)
        return row

    def _copy_text(self, text, button):
        QApplication.clipboard().setText(text)
        button.setText("已复制")
        QTimer.singleShot(1200, lambda: self._restore_copy_button(button))

    @staticmethod
    def _restore_copy_button(button):
        try:
            button.setText("复制")
        except RuntimeError:
            pass

    def _copy_all(self):
        QApplication.clipboard().setText(
            "\n".join(str(box.get("url", "")) for box in self.boxes)
        )

    def _open_link(self, url):
        try:
            if not open_url(url, self.config):
                QMessageBox.warning(
                    self, "无法打开", f"无法打开这个链接：\n{url}"
                )
        except Exception:
            print("打开链接失败:\n" + traceback.format_exc())

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)


# ============ 3. 结果提示卡片(自动打开 / 未识别到) ============

class Toast(QWidget):
    retry_clicked = Signal()
    dismissed = Signal()

    _TONES = {
        "success": (ACCENT_OK, "#00e0a8", "✓"),
        "error": (ACCENT_ERR, "#ff7373", "!"),
        "info": ("#5b9dff", "#7db0ff", "i"),
    }

    def __init__(
        self, message, is_error=False, title=None, tone=None, show_retry=None
    ):
        super().__init__()
        self._tone = tone or ("error" if is_error else "success")
        accent, accent_hover, symbol = self._TONES.get(
            self._tone, self._TONES["success"]
        )
        if title is None:
            title = {
                "success": "操作完成",
                "error": "识别失败",
                "info": "提示",
            }.get(self._tone, "提示")
        if show_retry is None:
            show_retry = self._tone == "error"
        self._auto_close_ms = {
            "success": 6000,
            "error": 10000,
            "info": 3500,
        }.get(self._tone, 6000)

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setAttribute(Qt.WA_Hover)
        self.setMouseTracking(True)

        card = QFrame(self)
        card.setObjectName("toastCard")
        card.setStyleSheet(f"""
            QFrame#toastCard {{
                background-color: #222429;
                border: 1px solid #353941;
                border-radius: 12px;
            }}
            QLabel {{
                background: transparent;
                font-family: "Microsoft YaHei UI", "Microsoft YaHei";
            }}
            QLabel#toastIcon {{
                color: {accent};
                background-color: {color_with_alpha(accent, 40)};
                border-radius: 14px;
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#toastTitle {{
                color: #f3f4f6;
                font-size: 13px;
                font-weight: 600;
            }}
            QLabel#toastMessage {{
                color: #b8bdc7;
                font-size: 12px;
            }}
            QPushButton#toastClose {{
                background: transparent;
                color: #8e949e;
                border: none;
                border-radius: 6px;
                font-size: 12px;
                padding: 0;
            }}
            QPushButton#toastClose:hover {{
                background: #34373e;
                color: #f3f4f6;
            }}
            QPushButton#toastRetry {{
                background: {accent};
                color: #10131a;
                border: none;
                border-radius: 6px;
                padding: 0 14px;
                min-height: 28px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#toastRetry:hover {{
                background: {accent_hover};
            }}
        """)
        card.setGraphicsEffect(drop_shadow(blur=28, y=6, alpha=150))
        card.setFixedWidth(380)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 14, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(10)

        icon = QLabel(symbol)
        icon.setObjectName("toastIcon")
        icon.setFixedSize(28, 28)
        icon.setAlignment(Qt.AlignCenter)

        title_label = QLabel(title)
        title_label.setObjectName("toastTitle")

        close_btn = QPushButton("✕")
        close_btn.setObjectName("toastClose")
        close_btn.setFixedSize(24, 24)
        close_btn.setToolTip("关闭")
        close_btn.clicked.connect(self.close)

        header.addWidget(icon, 0, Qt.AlignTop)
        header.addWidget(title_label, 1)
        header.addWidget(close_btn, 0, Qt.AlignTop)
        layout.addLayout(header)

        message_label = QLabel(message)
        message_label.setObjectName("toastMessage")
        message_label.setWordWrap(True)
        message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        message_row = QHBoxLayout()
        message_row.setContentsMargins(36, 0, 0, 0)
        message_row.addWidget(message_label)
        layout.addLayout(message_row)

        if show_retry:
            action_row = QHBoxLayout()
            action_row.addStretch()
            retry_btn = QPushButton("重新框选")
            retry_btn.setObjectName("toastRetry")
            retry_btn.setCursor(Qt.PointingHandCursor)
            retry_btn.clicked.connect(self._retry)
            action_row.addWidget(retry_btn)
            layout.addSpacing(2)
            layout.addLayout(action_row)

        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self.close)

        self.adjustSize()
        screen = (
            QGuiApplication.screenAt(QCursor.pos())
            or QGuiApplication.primaryScreen()
        )
        screen_geo = screen.availableGeometry()
        self.move(
            screen_geo.right() - self.width() - 14,
            screen_geo.bottom() - self.height() - 14,
        )

    def _retry(self):
        self.close()
        self.retry_clicked.emit()

    def show_animated(self):
        self.setWindowOpacity(0)
        self.show()
        end_pos = self.pos()
        self.move(QPoint(end_pos.x(), end_pos.y() + 12))

        self._fade_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade_anim.setDuration(200)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim.start()

        self._slide_anim = QPropertyAnimation(self, b"pos", self)
        self._slide_anim.setDuration(240)
        self._slide_anim.setStartValue(self.pos())
        self._slide_anim.setEndValue(end_pos)
        self._slide_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._slide_anim.start()

        if self._auto_close_ms:
            self._auto_timer.start(self._auto_close_ms)

    def enterEvent(self, event):
        self._auto_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._auto_close_ms and self.isVisible():
            self._auto_timer.start(self._auto_close_ms)
        super().leaveEvent(event)

    def closeEvent(self, event):
        self._auto_timer.stop()
        self.dismissed.emit()
        super().closeEvent(event)


# ============ 4. 设置界面 ============

class SettingsDialog(QDialog):
    settings_saved = Signal(dict)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self.setWindowTitle("设置")
        self.setWindowIcon(QIcon(resource_path("icon.ico")))
        self.setFixedWidth(440)
        self.setStyleSheet(f"""
            QDialog {{ background-color: {DARK_BG}; }}
            QLabel {{ color: {TEXT_MAIN}; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 13px; }}
            QLineEdit {{
                background-color: {CARD_BG}; color: {TEXT_MAIN};
                border: 1px solid #3a3d44; border-radius: 6px; padding: 7px;
                font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 12px;
            }}
            QKeySequenceEdit {{
                background-color: {CARD_BG}; color: {TEXT_MAIN};
                border: 1px solid #3a3d44; border-radius: 6px; padding: 6px;
            }}
            QCheckBox {{ color: {TEXT_MAIN}; font-size: 13px; }}
            QPushButton {{
                background-color: #34363b; color: {TEXT_MAIN}; border: none;
                border-radius: 8px; padding: 8px 18px;
                font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 12px;
            }}
            QPushButton:hover {{ background-color: #40434a; }}
            QPushButton#primary {{ background-color: {ACCENT_OK}; color: #10131a; font-weight: 600; }}
            QPushButton#primary:hover {{ background-color: #00e0a8; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 20)
        layout.setSpacing(14)

        title = QLabel("常规设置")
        title.setStyleSheet("font-size:16px; font-weight:600;")
        layout.addWidget(title)

        # ---- 浏览器 ----
        layout.addWidget(QLabel("跳转链接使用的浏览器"))
        browser_row = QHBoxLayout()
        self.browser_edit = QLineEdit(self.config.get("browser_path", ""))
        self.browser_edit.setPlaceholderText("留空则使用系统默认浏览器")
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self._choose_browser)
        browser_row.addWidget(self.browser_edit)
        browser_row.addWidget(browse_btn)
        layout.addLayout(browser_row)

        # ---- 快捷键 ----
        layout.addWidget(QLabel("全局快捷键（在任意界面按下即可截图识别）"))
        self.hotkey_edit = QKeySequenceEdit(
            QKeySequence(self._hotkey_to_qt(self.config.get("hotkey", "ctrl+alt+q")))
        )
        layout.addWidget(self.hotkey_edit)
        hint = QLabel("点击输入框后按下想要的组合键即可，例如 Ctrl+Alt+Q")
        hint.setStyleSheet(f"color:{TEXT_SUB}; font-size:11px;")
        layout.addWidget(hint)
        if not HOTKEY_AVAILABLE:
            warn = QLabel("⚠ 当前系统不支持全局快捷键，仍可用托盘菜单手动截图")
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color:{ACCENT_ERR}; font-size:11px;")
            layout.addWidget(warn)

        # ---- 开机自启动 ----
        self.autostart_check = QCheckBox("开机自动启动")
        self.autostart_check.setChecked(self.config.get("autostart", False))
        layout.addWidget(self.autostart_check)
        if sys.platform != "win32":
            self.autostart_check.setEnabled(False)
            self.autostart_check.setText("开机自动启动（仅支持 Windows）")

        self.confirm_open_check = QCheckBox("打开链接前先显示结果")
        self.confirm_open_check.setChecked(
            self.config.get("confirm_open", True)
        )
        layout.addWidget(self.confirm_open_check)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)

    def _choose_browser(self):
        filt = "可执行文件 (*.exe);;所有文件 (*.*)" if sys.platform == "win32" else "所有文件 (*)"
        path, _ = QFileDialog.getOpenFileName(self, "选择浏览器程序", "", filt)
        if path:
            self.browser_edit.setText(path)

    @staticmethod
    def _hotkey_to_qt(hotkey_str: str) -> str:
        """把 keyboard 库格式 (ctrl+alt+q) 转成 QKeySequence 可识别的字符串 (Ctrl+Alt+Q)"""
        name_map = {"windows": "Meta", "win": "Meta"}
        parts = [p.strip() for p in hotkey_str.split("+") if p.strip()]
        mapped = []
        for p in parts:
            low = p.lower()
            if low in name_map:
                mapped.append(name_map[low])
            elif len(p) == 1:
                mapped.append(p.upper())
            else:
                mapped.append(low.capitalize())
        return "+".join(mapped)

    @staticmethod
    def _qt_to_hotkey(qt_str: str) -> str:
        """把 QKeySequence 字符串转成 keyboard 库格式；只取第一个组合键（不支持连续按键序列）"""
        qt_str = qt_str.split(",")[0].strip()
        name_map = {"meta": "windows"}
        parts = [p.strip() for p in qt_str.split("+") if p.strip()]
        mapped = [name_map.get(p.lower(), p.lower()) for p in parts]
        return "+".join(mapped)

    def _on_save(self):
        seq = self.hotkey_edit.keySequence()
        seq_str = seq.toString(QKeySequence.PortableText) if not seq.isEmpty() else ""
        if not seq_str:
            QMessageBox.warning(self, "提示", "请先设置一个快捷键")
            return

        new_config = dict(self.config)
        new_config["browser_path"] = self.browser_edit.text().strip()
        new_config["hotkey"] = self._qt_to_hotkey(seq_str)
        new_config["autostart"] = self.autostart_check.isChecked()
        new_config["confirm_open"] = self.confirm_open_check.isChecked()

        self.settings_saved.emit(new_config)
        self.accept()


# ============ 5. 托盘主控制器（后台常驻） ============

class TrayApp(QObject):
    _capture_requested = Signal()
    _detection_finished = Signal(object, object)

    def __init__(self):
        if not acquire_single_instance():
            if sys.platform == "win32":
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.MessageBoxW(
                    None,
                    "程序已在运行，请查看屏幕右下角的系统托盘图标。",
                    "截图识码工具",
                    0x40,  # MB_ICONINFORMATION
                )
            sys.exit(0)
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self.config = load_config()

        self._selector = None
        self._toast = None
        self._viewer = None
        self._settings_dialog = None
        self._hotkey_id = None
        self._hotkey_filter = None
        self._pending_pixmap = None
        self._pending_screen = None
        self._detecting = False
        self._quitting = False
        self._hotkey_fallback = False

        self._capture_requested.connect(self.start_capture)
        self._detection_finished.connect(self._on_detection_finished)

        if sys.platform == "win32":
            self._hotkey_filter = WindowsHotkeyFilter(self._on_hotkey_pressed)
            self.app.installNativeEventFilter(self._hotkey_filter)

        self._build_tray()
        self._register_hotkey()

    # ---------- 托盘 ----------

    def _build_tray(self):
        self.tray = QSystemTrayIcon(QIcon(resource_path("icon.ico")))
        self.tray.setToolTip("截图识码工具 - 后台运行中")

        menu = QMenu()
        act_capture = QAction("立即截图识别", menu)
        act_capture.triggered.connect(self.start_capture)
        menu.addAction(act_capture)

        menu.addSeparator()

        act_settings = QAction("设置...", menu)
        act_settings.triggered.connect(self.open_settings)
        menu.addAction(act_settings)

        self.act_autostart = QAction("开机自启动", menu)
        self.act_autostart.setCheckable(True)
        self.act_autostart.setChecked(self.config.get("autostart", False))
        self.act_autostart.setEnabled(sys.platform == "win32")
        self.act_autostart.triggered.connect(self._toggle_autostart)
        menu.addAction(self.act_autostart)

        menu.addSeparator()

        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        hint = self.config.get("hotkey", "ctrl+alt+q").upper().replace("+", " + ")
        if not HOTKEY_AVAILABLE:
            msg = "已在后台运行。当前系统不支持全局快捷键，可通过托盘菜单手动截图。"
        else:
            msg = f"已在后台运行，按 {hint} 即可随时截图识别"
        self.tray.showMessage("截图识码工具", msg, QSystemTrayIcon.Information, 3000)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.start_capture()

    # ---------- 全局快捷键 ----------

    def _on_hotkey_pressed(self):
        try:
            if not self._quitting:
                self._capture_requested.emit()
        except Exception:
            print("触发快捷键失败:\n" + traceback.format_exc())

    def _unregister_hotkey_win(self):
        if self._hotkey_id is not None:
            try:
                _win_unregister_hotkey(self._hotkey_id)
            except Exception:
                pass
            self._hotkey_id = None

    def _unregister_keyboard_hotkey(self):
        if KEYBOARD_AVAILABLE:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
        self._hotkey_fallback = False

    def _register_hotkey(self, hotkey: str = None):
        hotkey = hotkey or self.config.get("hotkey", "ctrl+alt+q")
        if sys.platform == "win32":
            self._unregister_hotkey_win()
            self._unregister_keyboard_hotkey()
            ok, hotkey_id = _win_register_hotkey(hotkey)
            if ok:
                self._hotkey_id = hotkey_id
                return
            if KEYBOARD_AVAILABLE:
                try:
                    keyboard.add_hotkey(hotkey, self._on_hotkey_pressed)
                    self._hotkey_fallback = True
                    self.tray.showMessage(
                        "快捷键",
                        f"系统热键被占用，已改用备用方式监听 {hotkey}",
                        QSystemTrayIcon.Information, 4000,
                    )
                    return
                except Exception as e:
                    print(f"备用快捷键注册失败: {e}")
            self.tray.showMessage(
                "快捷键注册失败",
                f"无法注册快捷键 {hotkey}，可能与其他程序冲突，请在设置中更换。",
                QSystemTrayIcon.Warning, 4000,
            )
            return
        self._unregister_keyboard_hotkey()
        if not KEYBOARD_AVAILABLE:
            return
        try:
            keyboard.add_hotkey(hotkey, self._on_hotkey_pressed)
        except Exception as e:
            print(f"注册全局快捷键失败: {e}")
            self.tray.showMessage(
                "快捷键注册失败",
                f"无法注册快捷键 {hotkey}，可能与其他程序冲突，请在设置中更换。",
                QSystemTrayIcon.Warning, 4000,
            )

    # ---------- 截图识别流程（逻辑与原版一致） ----------

    def start_capture(self):
        if self._selector is not None:
            return  # 已在截图中，避免重复弹出遮罩
        if self._detecting:
            self._show_toast(
                "正在识别上一次截图，请稍候",
                is_error=False,
                title="请稍候",
                tone="info",
                show_retry=False,
            )
            return

        screen = (
            QGuiApplication.screenAt(QCursor.pos())
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            self._show_toast("没有找到可用屏幕", is_error=True, title="无法截图")
            return
        geo = screen.geometry()
        bg_pixmap = screen.grabWindow(0)
        bg_pixmap.setDevicePixelRatio(screen.devicePixelRatio())

        self._selector = OverlaySelector(bg_pixmap, geo)
        self._selector.setScreen(screen)
        self._selector.selection_made.connect(lambda rect: self.on_selected(bg_pixmap, rect, screen))
        self._selector.canceled.connect(self._on_capture_canceled)
        self._selector.showFullScreen()
        self._selector.raise_()
        self._selector.activateWindow()

    def _on_capture_canceled(self):
        self._selector = None

    def on_selected(self, bg_pixmap, rect, screen):
        self._selector = None
        try:
            dpr = screen.devicePixelRatio()
            # 框选坐标是逻辑像素，bg_pixmap 是物理像素，按缩放比例换算后再裁剪
            phys_rect = QRect(
                int(rect.x() * dpr), int(rect.y() * dpr),
                int(rect.width() * dpr), int(rect.height() * dpr)
            )
            cropped_pixmap = bg_pixmap.copy(phys_rect)
            cropped_pixmap.setDevicePixelRatio(dpr)
            cv_img = self._pixmap_to_cv(cropped_pixmap)
            self._pending_pixmap = cropped_pixmap
            self._pending_screen = screen
            self._detecting = True
            threading.Thread(target=self._detect_worker, args=(cv_img,), daemon=True).start()
        except Exception:
            print("处理截图结果时出错:\n" + traceback.format_exc())
            self._detecting = False
            self._pending_pixmap = None
            self._pending_screen = None
            self._show_toast(
                "处理截图结果时出现错误，详情见日志文件",
                is_error=True,
                title="截图失败",
            )

    def _detect_worker(self, cv_img):
        boxes = []
        error = None
        try:
            boxes = run_detection(cv_img)
        except Exception:
            error = traceback.format_exc()
            print("识别过程出错:\n" + error)
        if self._quitting:
            return
        try:
            self._detection_finished.emit(boxes, error)
        except Exception:
            print("发送识别结果失败:\n" + traceback.format_exc())

    def _on_detection_finished(self, boxes, error):
        self._detecting = False
        pixmap = self._pending_pixmap
        screen = self._pending_screen
        self._pending_pixmap = None
        self._pending_screen = None
        if error is not None:
            self._show_toast(
                "处理截图结果时出现错误，详情见日志文件",
                is_error=True,
                title="识别失败",
            )
            return
        if not boxes:
            msg = "未识别到二维码或网址，可以重新框选试试"
            if not OCR_AVAILABLE:
                msg += "\n(未安装 Tesseract，暂不支持文字网址识别)"
            self._show_toast(msg, is_error=True, title="未识别到内容")
            return

        if (
            len(boxes) == 1
            and boxes[0].get("openable")
            and not self.config.get("confirm_open", True)
        ):
            url = boxes[0]["url"]
            if open_url(url, self.config):
                self._show_toast(
                    url,
                    is_error=False,
                    title="已打开链接",
                    show_retry=False,
                )
            else:
                self._show_toast(
                    url,
                    is_error=True,
                    title="打开失败",
                    show_retry=False,
                )
            return

        self._show_results(pixmap, boxes, screen)

    def _show_results(self, pixmap, boxes, screen):
        if self._viewer is not None:
            old_viewer = self._viewer
            self._viewer = None
            old_viewer.close()
        viewer = ResultViewer(pixmap, boxes, self.config, screen=screen)
        viewer.closed.connect(lambda v=viewer: self._on_viewer_closed(v))
        self._viewer = viewer
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()

    def _on_viewer_closed(self, viewer):
        if viewer is self._viewer:
            self._viewer = None

    def _show_toast(
        self, message, is_error, title=None, tone=None, show_retry=None
    ):
        if self._toast is not None:
            old_toast = self._toast
            self._toast = None
            old_toast.close()
        toast = Toast(
            message,
            is_error=is_error,
            title=title,
            tone=tone,
            show_retry=show_retry,
        )
        toast.retry_clicked.connect(self.start_capture)
        toast.dismissed.connect(lambda t=toast: self._on_toast_dismissed(t))
        self._toast = toast
        toast.show_animated()

    def _on_toast_dismissed(self, toast):
        if toast is self._toast:
            self._toast = None

    @staticmethod
    def _pixmap_to_cv(pixmap):
        qimg = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
        w, h = qimg.width(), qimg.height()
        bpl = qimg.bytesPerLine()
        buf = qimg.bits()
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, bpl)
        arr = arr[:, :w * 4].reshape(h, w, 4)  # BGRA
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)

    # ---------- 设置 ----------

    def open_settings(self):
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dlg = SettingsDialog(self.config)
        dlg.settings_saved.connect(self._on_settings_saved)
        dlg.finished.connect(lambda _: setattr(self, "_settings_dialog", None))
        self._settings_dialog = dlg
        dlg.show()

    def _on_settings_saved(self, new_config: dict):
        old_hotkey = self.config.get("hotkey")
        old_autostart = self.config.get("autostart", False)
        new_autostart = bool(new_config.get("autostart", False))

        if new_autostart != old_autostart:
            ok, error = set_autostart(new_autostart)
            if not ok:
                new_config["autostart"] = old_autostart
                QMessageBox.warning(
                    None,
                    "开机自启动",
                    f"修改开机自启动失败：\n{error}",
                )

        self.config = new_config
        if not save_config(self.config):
            QMessageBox.warning(
                None, "设置", "配置保存失败，请检查配置目录的写入权限。"
            )

        if new_config.get("hotkey") != old_hotkey:
            self._register_hotkey(new_config.get("hotkey"))

        self.act_autostart.setChecked(bool(self.config.get("autostart", False)))

    def _toggle_autostart(self, checked: bool):
        ok, error = set_autostart(checked)
        if not ok:
            self.act_autostart.setChecked(not checked)
            QMessageBox.warning(
                None, "开机自启动", f"修改开机自启动失败：\n{error}"
            )
            return
        self.config["autostart"] = checked
        if not save_config(self.config):
            QMessageBox.warning(
                None, "设置", "配置保存失败，请检查配置目录的写入权限。"
            )

    # ---------- 退出 ----------

    def quit_app(self):
        self._quitting = True
        self._unregister_hotkey_win()
        self._unregister_keyboard_hotkey()
        self.tray.hide()
        self.app.quit()

    def run(self):
        sys.exit(self.app.exec())


if __name__ == "__main__":
    TrayApp().run()
