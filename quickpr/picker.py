"""Local selection state and keyboard-driven file picker."""

import os
from pathlib import Path

from .errors import QuickPRError
from .files import DEFAULT_EXCLUDES, is_link
from .terminal import Terminal, clip_text


def selection_marker(selected, entry):
    if entry in selected or any(p in selected for p in entry.parents):
        return "✓"
    if any(entry in p.parents for p in selected):
        return "-"
    return " "


def include_path(selected, path):
    if any(p in selected for p in path.parents):
        return set(selected)
    return {p for p in selected if path not in p.parents} | {path}


def toggle_path(selected, chosen):
    if chosen in selected:
        return selected - {chosen}
    ancestor = next((p for p in chosen.parents if p in selected), None)
    if ancestor is None:
        return include_path(selected, chosen)
    # Split the selected ancestor into siblings along this path. The returned
    # upload paths then exclude the toggled file, even after selecting all.
    remaining = selected - {ancestor}
    cursor = ancestor
    for part in chosen.relative_to(ancestor).parts:
        child = cursor / part
        remaining.update(p for p in cursor.iterdir() if p != child and p.name not in DEFAULT_EXCLUDES)
        cursor = child
    return remaining


def directory_entries(directory):
    return sorted((p for p in directory.iterdir() if p.name not in DEFAULT_EXCLUDES),
                  key=lambda p: (not p.is_dir(), p.name.casefold(), p.name))


def pick_files(root, terminal=None):
    root = Path(os.path.abspath(Path(root).expanduser()))
    if not root.is_dir() or is_link(root):
        raise QuickPRError("请选择普通本地目录。")
    current, selected = root, set()
    entries = directory_entries(current)
    cursor, top = 0, 0
    positions = {}
    status = "回车勾选/取消当前项，选好后按 F。"
    with terminal if terminal is not None else Terminal() as screen:
        while True:
            width, height = screen.size()
            visible = max(1, height - 10)
            cursor = max(0, min(cursor, len(entries) - 1))
            top = min(top, cursor)
            top = max(top, cursor - visible + 1)
            header = ["quickPR · 本地文件选择", "根目录：" + str(root), "当前：" + str(current),
                      "已选 {} 项    光标 {}/{}    [✓] 已选  [-] 含已选子项".format(len(selected), cursor + 1 if entries else 0, len(entries)), ""]
            body = []
            for i, entry in enumerate(entries[top:top + visible], top):
                pointer = ">" if i == cursor else " "
                body.append("{} [{}] {}{}".format(pointer, selection_marker(selected, entry), entry.name, "/" if entry.is_dir() else ""))
            if not body:
                body = ["  （空目录）"]
            body += [""] * (visible - len(body))
            footer = ["", status, "↑↓ 移动   回车/空格 勾选或取消   → 进入文件夹",
                      "← 返回上级   Home/End 首尾   PgUp/PgDn 翻页", "A 全选当前目录   C 清空   F 完成   Q 取消"]
            if width < 32 or height < 12:
                frame = ["请放大终端窗口（至少 32 列 × 12 行）", "Q 取消"]
            else:
                frame = header + body + footer
            screen.render([clip_text(line, max(0, width - 1)) for line in frame[:height]])
            key = screen.read_key()
            if key in ("q", "escape"):
                return [], root
            if width < 32 or height < 12:
                continue
            status = "回车勾选/取消当前项，选好后按 F。"
            if key == "f":
                if selected:
                    return sorted(selected), root
                status = "请先选择文件，或按 Q 取消。"
            elif key == "a":
                if entries:
                    selected = include_path(selected, current)
            elif key == "c":
                selected.clear()
            elif key in ("up", "down", "home", "end", "page_up", "page_down"):
                if key == "up":
                    cursor -= 1
                elif key == "down":
                    cursor += 1
                elif key == "home":
                    cursor = 0
                elif key == "end":
                    cursor = len(entries) - 1
                elif key == "page_up":
                    cursor -= visible
                else:
                    cursor += visible
            elif key == "left" and current != root:
                parent = current.parent
                try:
                    new_entries = directory_entries(parent)
                    current, entries = parent, new_entries
                    cursor, top = positions.get(current, (0, 0))
                except OSError:
                    status = "无法读取上级目录，请检查目录权限。"
            elif entries and key in ("enter", "right"):
                chosen = entries[cursor]
                try:
                    if key == "enter":
                        if is_link(chosen):
                            status = "不支持选择符号链接或 Windows junction。"
                        else:
                            selected = toggle_path(selected, chosen)
                    elif chosen.is_dir() and not is_link(chosen):
                        new_entries = directory_entries(chosen)
                        positions[current] = cursor, top
                        current, entries = chosen, new_entries
                        cursor, top = positions.get(current, (0, 0))
                    else:
                        status = "右方向键用于进入普通文件夹；选择文件请按回车。"
                except OSError:
                    status = "无法读取此路径，请检查权限或确认文件仍然存在。"
