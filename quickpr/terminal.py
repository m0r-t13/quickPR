"""Single-screen terminal drawing and key input on macOS and Windows."""

import os
import shutil
import sys
import unicodedata

from .errors import QuickPRError


def clip_text(value, columns):
    text = "".join(c if c.isprintable() else "\\u{:04x}".format(ord(c)) for c in str(value))
    widths = [0 if unicodedata.combining(c) else (2 if unicodedata.east_asian_width(c) in ("W", "F") else 1) for c in text]
    if sum(widths) <= columns:
        return text
    if columns <= 0:
        return ""
    result, used = [], 0
    for char, width in zip(text, widths):
        if used + width > columns - 1:
            break
        result.append(char)
        used += width
    return "".join(result) + "…"


def decode_key(raw):
    if raw == "\x03":
        raise KeyboardInterrupt
    named = {"\r": "enter", "\n": "enter", " ": "enter", "\x1b": "escape",
             "\x1b[H": "home", "\x1b[F": "end", "\x1b[1~": "home", "\x1b[4~": "end",
             "\x1b[5~": "page_up", "\x1b[6~": "page_down"}
    for suffix, name in (("A", "up"), ("B", "down"), ("C", "right"), ("D", "left"), ("H", "home"), ("F", "end")):
        named["\x1b[" + suffix] = name
        named["\x1bO" + suffix] = name
    for suffix, name in (("H", "up"), ("P", "down"), ("M", "right"), ("K", "left"),
                         ("G", "home"), ("O", "end"), ("I", "page_up"), ("Q", "page_down")):
        named["\xe0" + suffix] = name
        named["\x00" + suffix] = name
    return named.get(raw, raw.lower() if len(raw) == 1 else "unknown")


class Terminal:
    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin if stdin is not None else sys.stdin
        self.stdout = stdout if stdout is not None else sys.stdout
        self._saved_input = None
        self._saved_windows = None
        self._active = False
        self._lines = []
        self._dimensions = None

    @staticmethod
    def available():
        return sys.stdin.isatty() and sys.stdout.isatty() and (os.name == "nt" or os.environ.get("TERM") != "dumb")

    def __enter__(self):
        try:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes
                import msvcrt
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
                kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.GetConsoleMode.restype = kernel.SetConsoleMode.restype = wintypes.BOOL
                handle = wintypes.HANDLE(msvcrt.get_osfhandle(self.stdout.fileno()))
                mode = wintypes.DWORD()
                if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                    raise QuickPRError("无法读取终端显示模式，请在 Windows Terminal / PowerShell 中运行。")
                if not kernel.SetConsoleMode(handle, mode.value | 0x0001 | 0x0004):
                    raise QuickPRError("此终端不支持固定屏幕显示，请使用 Windows Terminal。")
                self._saved_windows = (kernel, handle, mode.value)
            else:
                import termios
                import tty
                self._saved_input = termios.tcgetattr(self.stdin.fileno())
                tty.setcbreak(self.stdin.fileno())
            self._active = True
            self.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J\x1b[H")
            self.stdout.flush()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            if self._active:
                self.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
                self.stdout.flush()
                self._active = False
        finally:
            if self._saved_input is not None:
                import termios
                termios.tcsetattr(self.stdin.fileno(), termios.TCSADRAIN, self._saved_input)
                self._saved_input = None
            if self._saved_windows is not None:
                kernel, handle, mode = self._saved_windows
                kernel.SetConsoleMode(handle, mode)
                self._saved_windows = None

    def size(self):
        try:
            dimensions = os.get_terminal_size(self.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            dimensions = shutil.get_terminal_size((80, 24))
        return dimensions.columns, dimensions.lines

    def render(self, lines):
        width, height = self.size()
        lines = [clip_text(line, max(0, width - 1)) for line in lines[:height]]
        output = []
        if self._dimensions is not None and self._dimensions != (width, height):
            output.append("\x1b[2J")
            self._lines = []
        self._dimensions = width, height
        for i in range(max(len(lines), len(self._lines))):
            text = lines[i] if i < len(lines) else ""
            if i >= len(self._lines) or self._lines[i] != text:
                output.append("\x1b[{};1H\x1b[2K{}".format(i + 1, text))
        if output:
            self.stdout.write("".join(output))
            self.stdout.flush()
        self._lines = lines

    def read_key(self):
        if os.name == "nt":
            import msvcrt
            raw = msvcrt.getwch()
            if raw in ("\x00", "\xe0"):
                raw += msvcrt.getwch()
            return decode_key(raw)
        import select
        fd = self.stdin.fileno()
        raw = os.read(fd, 1)
        if not raw:
            raise QuickPRError("终端输入已关闭，文件选择已取消。")
        if raw == b"\x1b" and select.select([fd], [], [], 0.08)[0]:
            raw += os.read(fd, 1)
            if raw[-1:] in (b"[", b"O"):
                for _ in range(16):
                    if not select.select([fd], [], [], 0.08)[0]:
                        break
                    char = os.read(fd, 1)
                    if not char:
                        break
                    raw += char
                    if b"@" <= char <= b"~":
                        break
        return decode_key(raw.decode("latin1"))
