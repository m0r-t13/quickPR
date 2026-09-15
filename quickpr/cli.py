"""Cross-platform terminal interface. No shell parsing or GUI dependency."""

import argparse
from collections import Counter
import getpass
import os
from pathlib import Path
import shutil
import subprocess
import sys

from . import __version__
from .errors import QuickPRError
from .files import DEFAULT_EXCLUDES, collect_files, is_link, normalize_destination, plan_upload
from .github import GitHub, RemoteTree, parse_repo, validate_branch
from .picker import include_path, pick_files, selection_marker, toggle_path
from .terminal import Terminal
from .upload import check_writable, publish


def display(value):
    """Escape terminal controls from filenames and remote metadata."""
    return "".join(c if (c.isprintable() or c == " ") else "\\u{:04x}".format(ord(c)) for c in str(value))


def ask(prompt, default=None):
    suffix = " [{}]".format(default) if default is not None else ""
    try:
        answer = input(prompt + suffix + ": ").strip()
    except EOFError:
        raise QuickPRError("未收到输入。自动上传请显式使用 --yes；仅查看预览可用 --dry-run。") from None
    return answer if answer else (default if default is not None else "")


def resolve_token(required=False):
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(name, "").strip()
        if token:
            return token
    gh = shutil.which("gh")
    if gh:
        try:
            result = subprocess.run([gh, "auth", "token", "--hostname", "github.com"],
                                    capture_output=True, text=True, encoding="utf-8", timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired, UnicodeError):
            pass
    if required:
        if not sys.stdin.isatty():
            raise QuickPRError("上传需要登录。请先运行 gh auth login，或设置 GH_TOKEN / GITHUB_TOKEN。")
        print("未找到 GitHub 登录状态。可按 Ctrl+C 后运行 gh auth login。")
        token = getpass.getpass("或粘贴访问令牌（隐藏输入，仅本次使用）: ").strip()
        if not token:
            raise QuickPRError("没有提供访问令牌，已取消上传。")
        return token
    return None


def select_local(root):
    if Terminal.available():
        return pick_files(root)
    return select_local_numbered(root)


def select_local_numbered(root):
    """Line-input fallback for redirected input or terminals without a screen."""
    root = Path(os.path.abspath(Path(root).expanduser()))
    if not root.is_dir() or is_link(root):
        raise QuickPRError("请选择普通本地目录。")
    current, selected, page = root, set(), 0
    page_size = 40
    while True:
        entries = sorted([p for p in current.iterdir() if p.name not in DEFAULT_EXCLUDES],
                         key=lambda p: (not p.is_dir(), p.name.casefold(), p.name))
        max_page = max(0, (len(entries) - 1) // page_size)
        page = min(page, max_page)
        print("\n本地根目录：{}\n当前目录：{}".format(display(root), display(current)))
        print("已选 {} 项 · 第 {}/{} 页".format(len(selected), page + 1, max_page + 1))
        for i, entry in enumerate(entries[page * page_size:(page + 1) * page_size], page * page_size + 1):
            marker = selection_marker(selected, entry)
            print("  {:>3}. [{}] {}{}".format(i, marker, display(entry.name), "/" if entry.is_dir() else ""))
        print("输入编号切换选择（如 1,3-5）；cd 编号 进入目录；.. 返回；n/p 翻页")
        print("all 选择当前目录全部内容；clear 清空；done 完成；q 取消")
        answer = ask("选择")
        if answer == "q":
            return [], root
        if answer == "done":
            if selected:
                return sorted(selected), root
            print("请先选择文件，或输入 q 取消。")
        elif answer == "all":
            selected = include_path(selected, current)
        elif answer == "clear":
            selected.clear()
        elif answer == "..":
            if current != root:
                current, page = current.parent, 0
        elif answer in ("n", "p"):
            page = min(max_page, page + 1) if answer == "n" else max(0, page - 1)
        elif answer.startswith("cd "):
            try:
                index = int(answer[3:])
                if not 1 <= index <= len(entries):
                    raise ValueError
                chosen = entries[index - 1]
                if not chosen.is_dir() or is_link(chosen):
                    raise ValueError
                current, page = chosen, 0
            except ValueError:
                print("请输入普通目录对应的有效编号。")
        else:
            try:
                indices = set()
                for section in answer.replace("，", ",").split(","):
                    parts = section.strip().split("-")
                    if len(parts) == 1:
                        start = end = int(parts[0])
                    elif len(parts) == 2:
                        start, end = map(int, parts)
                    else:
                        raise ValueError
                    if not 1 <= start <= end <= len(entries):
                        raise ValueError
                    indices.update(range(start, end + 1))
                for index in sorted(indices):
                    chosen = entries[index - 1]
                    selected = toggle_path(selected, chosen)
            except ValueError:
                print("输入无效，请使用编号、cd 编号、all 或 done。")


def show_tree(tree, path="", depth=2, limit=300):
    print("\n" + display(path or "/"))
    count = 0
    prefix_size = len(path.split("/")) if path else 0
    for full, entry in tree.walk(path, depth):
        if count >= limit:
            print("  … 仅显示前 {} 项；请指定 --path 子目录或调大 --limit。".format(limit))
            break
        indent = "  " * (len(full.split("/")) - prefix_size)
        suffix = "/" if entry["type"] == "tree" else ""
        if entry["mode"] == "120000":
            suffix = " [链接]"
        elif entry["type"] == "commit":
            suffix = " [子模块]"
        print(indent + "|-- " + display(full.rsplit("/", 1)[-1]) + suffix)
        count += 1
    if count == 0:
        print("  （空目录）")


def select_destination(tree):
    current = ""
    while True:
        show_tree(tree, current, depth=1, limit=100)
        print("输入目标相对路径可选择已有或新目录；ls 路径 浏览；done 使用当前目录；q 取消。")
        answer = ask("远端目标目录（回车使用当前目录）")
        if answer == "q":
            return None
        if answer in ("", "done"):
            return current
        try:
            if answer.startswith("ls "):
                path = normalize_destination(answer[3:].strip())
                entry = tree.lookup(path)
                if not entry or entry["type"] != "tree":
                    raise QuickPRError("该目录不存在。")
                current = path
            else:
                return normalize_destination(answer)
        except QuickPRError as error:
            print("提示：" + str(error))


def preview(api, snapshot, batch, changes, new_branch):
    labels = {"add": "+ 新增", "update": "~ 覆盖", "skip": "= 不变"}
    print("\n上传预览\n仓库：{}\n分支：{}{}\n本地根目录：{}".format(
        api.repo, display(new_branch or snapshot.branch),
        "（从 {} 新建）".format(display(snapshot.branch)) if new_branch else "", display(batch.root)))
    for change in changes:
        print("  {}  {} -> {}  ({} B)".format(labels[change.action],
              display(change.file.source.relative_to(batch.root)), display(change.file.remote_path), len(change.file.data)))
    counts = Counter(c.action for c in changes)
    print("共 {} 个文件：新增 {} / 覆盖 {} / 不变 {}".format(len(changes), counts["add"], counts["update"], counts["skip"]))
    if batch.skipped:
        print("已排除 {} 项：{}".format(len(batch.skipped), ", ".join(display(x) for x in batch.skipped[:10])))
    print("上传使用此刻读取的文件内容；本地后续修改需再次上传。")


def run_upload(api, snapshot, tree, paths, root=None, destination="", excludes=(),
               allow_sensitive=False, dry_run=False, yes=False, message=None, new_branch=None):
    if new_branch:
        validate_branch(new_branch)
    batch = collect_files(paths, root=root, destination=destination, excludes=excludes, allow_sensitive=allow_sensitive)
    changes = plan_upload(batch, tree.lookup)
    preview(api, snapshot, batch, changes, new_branch)
    if dry_run:
        print("预览完成，未发送任何写入请求。")
        return 0
    count = sum(c.action != "skip" for c in changes)
    if not count:
        print("所有文件内容相同，无需上传。")
        return 0
    check_writable(api, snapshot)
    message = message or "Upload {} file(s) via quickPR".format(count)
    print("提交说明：" + display(message))
    if not yes and ask("确认新增和覆盖以上文件？输入 y 确认", "n").lower() not in ("y", "yes"):
        print("已取消上传。")
        return 0
    result = publish(api, snapshot, changes, message, new_branch=new_branch,
                     progress=lambda text: print(display(text), flush=True))
    print("\n上传完成 · 分支 {}\n{}".format(display(result.branch), result.url))
    return 0


def wizard():
    print("\nquickPR {} · GitHub 文件快速上传\n".format(__version__))
    print("  1. 选择本地文件并上传\n  2. 查看仓库文件结构\n  q. 退出")
    operation = ask("请选择", "1")
    if operation == "q":
        return 0
    if operation not in ("1", "2"):
        raise QuickPRError("请选择 1、2 或 q。")
    repo = parse_repo(ask("GitHub 仓库地址"))
    branch = ask("分支（回车使用默认分支）") or None
    api = GitHub(repo, resolve_token(required=operation == "1"))
    print("正在读取仓库 …", flush=True)
    snapshot = api.snapshot(branch)
    tree = RemoteTree(api, snapshot.tree_sha)
    print("仓库 {} · 分支 {}".format(api.repo, display(snapshot.branch)))
    if operation == "2":
        path = ""
        while True:
            show_tree(tree, path)
            value = ask("输入其他目录继续查看；q 退出", "q")
            if value == "q":
                return 0
            path = normalize_destination(value)
    check_writable(api, snapshot)
    root = ask("本地上传根目录", str(Path.cwd())).strip('"')
    paths, root = select_local(root)
    if not paths:
        print("已取消。")
        return 0
    destination = select_destination(tree)
    if destination is None:
        print("已取消。")
        return 0
    new_branch = ask("新建分支名（回车直接上传当前分支）") or None
    message = ask("提交说明", "Upload files via quickPR")
    return run_upload(api, snapshot, tree, paths, root=root, destination=destination,
                      message=message, new_branch=new_branch)


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是大于零的整数")
    return number


def parser():
    result = argparse.ArgumentParser(prog="quickpr", description="快速上传本地文件到 GitHub；无参数启动交互向导。")
    result.add_argument("--version", action="version", version="quickPR " + __version__)
    commands = result.add_subparsers(dest="command")
    upload = commands.add_parser("upload", help="选择或指定文件，预览并上传")
    upload.add_argument("paths", nargs="*", help="本地文件或文件夹，含空格时加引号")
    upload.add_argument("--repo", "-r", help="仓库地址或 owner/repo")
    upload.add_argument("--branch", "-b", help="现有源分支，默认仓库默认分支")
    upload.add_argument("--new-branch", help="从源分支新建此分支并上传")
    upload.add_argument("--root", help="本地根目录，保留根目录内的相对路径")
    upload.add_argument("--dest", "-d", default="", help="远端相对目录，默认仓库根目录")
    upload.add_argument("--message", "-m", help="提交说明")
    upload.add_argument("--exclude", action="append", default=[], help="排除 glob，可重复，例如 --exclude '*.log'")
    upload.add_argument("--allow-sensitive", action="store_true", help="明确允许上传检测到的敏感文件")
    upload.add_argument("--dry-run", action="store_true", help="只读预览，不写入 GitHub")
    upload.add_argument("--yes", "-y", action="store_true", help="确认预览中的新增和覆盖，跳过确认输入")
    tree = commands.add_parser("tree", help="查询仓库文件结构")
    tree.add_argument("repo", help="仓库地址或 owner/repo")
    tree.add_argument("--branch", "-b", help="分支名")
    tree.add_argument("--path", default="", help="只查询指定相对目录")
    tree.add_argument("--depth", type=positive_int, default=2, help="展开层数，默认 2")
    tree.add_argument("--limit", type=positive_int, default=300, help="最多显示条目数，默认 300")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command is None:
            return wizard()
        if args.command == "tree":
            api = GitHub(args.repo, resolve_token())
            snapshot = api.snapshot(args.branch)
            print("{} · {} · {}".format(api.repo, display(snapshot.branch), snapshot.head[:12]))
            show_tree(RemoteTree(api, snapshot.tree_sha), normalize_destination(args.path), args.depth, args.limit)
            return 0
        repo = parse_repo(args.repo or ask("GitHub 仓库地址"))
        api = GitHub(repo, resolve_token(required=not args.dry_run))
        print("正在读取仓库 …", flush=True)
        snapshot = api.snapshot(args.branch)
        paths, root = args.paths, args.root
        if not paths:
            paths, root = select_local(root or Path.cwd())
            if not paths:
                print("已取消。")
                return 0
        return run_upload(api, snapshot, RemoteTree(api, snapshot.tree_sha), paths, root=root,
                          destination=args.dest, excludes=args.exclude, allow_sensitive=args.allow_sensitive,
                          dry_run=args.dry_run, yes=args.yes, message=args.message, new_branch=args.new_branch)
    except KeyboardInterrupt:
        print("\n已中断。如中断时正在更新分支，请在再次上传前核实远端结果。", file=sys.stderr)
        return 130
    except (QuickPRError, OSError) as error:
        print("\n错误：" + display(error).replace("\\u000a", "\n"), file=sys.stderr)
        return 1


def entrypoint():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return main()
