"""Freeze selected bytes and map local paths to Git repository paths."""

from dataclasses import dataclass
from fnmatch import fnmatchcase
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Callable, List, Optional

from .errors import QuickPRError

DEFAULT_EXCLUDES = {".git", "node_modules", ".venv", "venv", "__pycache__", ".DS_Store"}
MIB = 1024 * 1024


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def normalize_destination(value: str) -> str:
    value = value.replace("\\", "/")
    if value in ("", "."):
        return ""
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise QuickPRError("远端目录必须是相对仓库根目录的路径，例如 docs/images。")
    parts = value.rstrip("/").split("/")
    if any(p in ("", ".", "..") or p.casefold() == ".git" for p in parts):
        raise QuickPRError("路径不能含空段、.、.. 或 .git。")
    if any(ord(c) < 32 or ord(c) == 127 or c in ':*?"<>|' for c in value):
        raise QuickPRError("路径含不兼容 macOS / Windows 的字符。")
    if any(p.endswith((" ", ".")) for p in parts):
        raise QuickPRError("路径段不能以空格或句点结尾。")
    return "/".join(parts)


@dataclass(frozen=True)
class LocalFile:
    source: Path
    remote_path: str
    data: bytes
    sha: str
    mode: str


@dataclass
class Batch:
    root: Path
    files: List[LocalFile]
    skipped: List[str]


@dataclass(frozen=True)
class Change:
    file: LocalFile
    action: str
    mode: str


def is_link(path: Path) -> bool:
    # Junctions are reparse points on Windows, including Python versions
    # before Path.is_junction was introduced.
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def sensitive_reason(path: str, data: bytes) -> Optional[str]:
    name = PurePosixPath(path).name.lower()
    if (name == ".env" or name.startswith(".env.")) and not name.endswith((".example", ".sample", ".template")):
        return "环境变量文件"
    if name in {"id_rsa", "id_ed25519", "id_ecdsa", ".npmrc", ".pypirc"} or name.endswith((".p12", ".pfx", ".key")):
        return "可能含凭据的文件"
    if re.search(rb"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----", data):
        return "私钥内容"
    if re.search(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16})", data):
        return "疑似访问令牌"
    return None


def collect_files(paths, root=None, destination="", excludes=(), allow_sensitive=False,
                  max_file_bytes=100 * MIB, max_total_bytes=200 * MIB, max_files=1000) -> Batch:
    if not paths:
        raise QuickPRError("请至少选择一个文件或文件夹。")
    selected = [Path(os.path.abspath(Path(p).expanduser())) for p in paths]
    destination = normalize_destination(destination)
    for path in selected:
        if not path.exists() and not path.is_symlink():
            raise QuickPRError("本地路径不存在：{}".format(path))
        if is_link(path):
            raise QuickPRError("不支持符号链接或 Windows junction：{}".format(path))
    if root is None:
        if len(selected) == 1:
            root = selected[0] if selected[0].is_dir() else selected[0].parent
        else:
            try:
                root = Path(os.path.commonpath([str(p.parent) for p in selected]))
            except ValueError:
                raise QuickPRError("所选路径位于不同磁盘，请分批上传。") from None
    root = Path(os.path.abspath(Path(root).expanduser()))
    if any(part.casefold() == ".git" for part in root.parts):
        raise QuickPRError("不能从 .git 元数据目录上传文件，请选择项目目录中的实际文件。")
    if not root.is_dir() or is_link(root):
        raise QuickPRError("本地根目录必须是普通目录：{}".format(root))
    skipped, candidates = [], {}

    def excluded(relative):
        parts = relative.parts
        return any(p in DEFAULT_EXCLUDES for p in parts) or any(
            fnmatchcase(relative.as_posix(), pattern) or fnmatchcase(relative.name, pattern)
            for pattern in excludes
        )

    def visit(path):
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise QuickPRError("文件不在本地根目录中：{}".format(path)) from None
        if excluded(relative):
            skipped.append(relative.as_posix())
            return
        if is_link(path):
            raise QuickPRError("不支持符号链接或 Windows junction：{}".format(path))
        if path.is_dir():
            for child in sorted(path.iterdir(), key=lambda x: x.name):
                visit(child)
        elif path.is_file():
            # A POSIX literal backslash must not silently become a directory.
            if any("\\" in part for part in relative.parts):
                raise QuickPRError("本地文件名不能含反斜杠：{}".format(relative))
            target = normalize_destination("/".join(filter(None, [destination, relative.as_posix()])))
            candidates[target] = path
            if len(candidates) > max_files:
                raise QuickPRError("一次最多上传 {} 个文件，请分批上传。".format(max_files))
        else:
            raise QuickPRError("只能上传普通文件：{}".format(path))

    for path in selected:
        try:
            relative = path.relative_to(root)
        except ValueError:
            raise QuickPRError("文件不在本地根目录中：{}".format(path)) from None
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if is_link(cursor):
                raise QuickPRError("路径经过符号链接或 junction：{}".format(cursor))
        visit(path)

    files, total = [], 0
    for target, path in sorted(candidates.items()):
        info = path.stat()
        if info.st_size > max_file_bytes:
            raise QuickPRError("文件超过单文件大小限制（{} MiB）：{}".format(max_file_bytes // MIB, path))
        if total + info.st_size > max_total_bytes:
            raise QuickPRError("本次文件总大小超过 {} MiB，请分批上传。".format(max_total_bytes // MIB))
        with path.open("rb") as stream:
            data = stream.read(min(max_file_bytes, max_total_bytes - total) + 1)
        if len(data) > max_file_bytes or total + len(data) > max_total_bytes:
            raise QuickPRError("读取时文件大小发生变化并超过限制，请重新选择。")
        reason = sensitive_reason(target, data)
        if reason and not allow_sensitive:
            raise QuickPRError("{}：{}。确认可公开/共享后，可显式使用 --allow-sensitive。".format(reason, target))
        total += len(data)
        mode = "100755" if os.name != "nt" and info.st_mode & stat.S_IXUSR else "100644"
        files.append(LocalFile(path, target, data, git_blob_sha(data), mode))
    if not files:
        raise QuickPRError("没有可上传的文件（目录为空，或文件均被排除）。")
    return Batch(root, files, sorted(set(skipped)))


def plan_upload(batch: Batch, lookup: Callable) -> List[Change]:
    changes = []
    for file in batch.files:
        for parent in reversed(PurePosixPath(file.remote_path).parents):
            if str(parent) == ".":
                continue
            entry = lookup(str(parent))
            if entry and entry["type"] != "tree":
                raise QuickPRError("远端路径的上级不是目录：{}".format(parent))
        existing = lookup(file.remote_path)
        if existing and (existing["type"] != "blob" or existing["mode"] not in ("100644", "100755")):
            raise QuickPRError("不能用普通文件替换远端目录、链接或子模块：{}".format(file.remote_path))
        action = "add" if existing is None else ("skip" if existing["sha"] == file.sha else "update")
        changes.append(Change(file, action, existing["mode"] if existing else file.mode))
    return changes
