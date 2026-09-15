"""GitHub.com transport and on-demand Git tree browsing."""

from dataclasses import dataclass
import http.client
import json
import re
import socket
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .errors import APIError, NetworkError, QuickPRError
from .files import normalize_destination


def parse_repo(value: str) -> str:
    value = value.strip().rstrip("/")
    if value.startswith("git@github.com:"):
        value = value[len("git@github.com:"):]
    elif "://" in value:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.netloc.lower() != "github.com"
                or parsed.query or parsed.fragment):
            raise QuickPRError("请输入 GitHub.com 仓库首页地址，例如 https://github.com/owner/repo。")
        value = parsed.path.lstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", value):
        raise QuickPRError("仓库地址格式应为 owner/repo 或 https://github.com/owner/repo。")
    if value.split("/")[1] in (".", ".."):
        raise QuickPRError("仓库名称无效。")
    return value


def validate_branch(value: str) -> str:
    if (not value or value.startswith(("-", "/")) or value.endswith(("/", "."))
            or ".." in value or "@{" in value or "//" in value or value == "@"
            or any(ord(c) < 33 or ord(c) == 127 or c in "~^:?*[\\" for c in value)
            or any(p.startswith(".") or p.endswith(".lock") for p in value.split("/"))):
        raise QuickPRError("分支名称无效，请使用例如 main 或 uploads/update。")
    return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization to another repository or host.
        return None


@dataclass(frozen=True)
class Snapshot:
    branch: str
    head: str
    tree_sha: str
    can_push: Optional[bool] = None
    archived: bool = False


class GitHub:
    def __init__(self, repo: str, token: Optional[str] = None, opener=None):
        self.repo = parse_repo(repo)
        self.token = token.strip() if token else None
        if self.token and (not self.token.isascii() or any(c.isspace() for c in self.token)):
            raise QuickPRError("访问令牌格式无效，请检查环境变量或重新登录。")
        self._opener = opener if opener is not None else build_opener(NoRedirect())

    def request(self, method: str, path: str, data=None):
        url = "https://api.github.com/repos/" + self.repo + path
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "quickPR/0.1.0",
                   "X-GitHub-Api-Version": "2026-03-10"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=True).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            message = ""
            try:
                payload = json.loads(error.read().decode("utf-8"))
                if isinstance(payload, dict):
                    message = str(payload.get("message", ""))
            except (ValueError, UnicodeError, OSError):
                pass
            hints = {
                301: "仓库已迁移，请输入最新仓库地址。", 302: "仓库地址发生跳转，请检查地址。",
                401: "GitHub 身份验证失败，请重新登录 gh 或检查令牌。",
                403: "GitHub 拒绝请求，请检查仓库权限、分支规则或 API 速率限制。",
                404: "仓库、分支或路径不存在，或者当前凭据没有访问权限。",
                409: "仓库为空或远端发生冲突，请刷新后重试。",
                422: "GitHub 未接受变更，请检查分支保护、分支名称和文件限制。",
                429: "GitHub 请求过于频繁，请稍后重试。",
            }
            hint = hints.get(error.code, "GitHub 接口返回错误。")
            if error.code >= 500:
                raise NetworkError("GitHub 服务暂时不可用，未收到确定的操作结果。") from None
            if self.token:
                message = message.replace(self.token, "[已隐藏]")
            message = "".join(c for c in message if ord(c) >= 32 and ord(c) != 127)[:400]
            raise APIError(error.code, "{} HTTP {}{}".format(hint, error.code, ": " + message if message else "")) from None
        except (URLError, socket.timeout, OSError, http.client.HTTPException, ValueError, UnicodeError):
            raise NetworkError("连接 GitHub 失败或响应不完整，请检查网络、代理及证书设置。") from None

    def head(self, branch: str) -> str:
        branch = validate_branch(branch)
        return self.request("GET", "/git/ref/heads/" + quote(branch, safe=""))["object"]["sha"]

    def snapshot(self, branch=None) -> Snapshot:
        repo = self.request("GET", "")
        branch = validate_branch(branch or repo["default_branch"])
        try:
            head = self.head(branch)
        except APIError as error:
            if error.status == 409:
                raise QuickPRError("该仓库尚无提交。请先在 GitHub 添加 README，再使用 quickPR。") from None
            raise
        commit = self.request("GET", "/git/commits/" + quote(head, safe=""))
        return Snapshot(branch, head, commit["tree"]["sha"], repo.get("permissions", {}).get("push"), repo.get("archived", False))


class RemoteTree:
    def __init__(self, api: GitHub, sha: str):
        self.api = api
        self.sha = sha
        self._cache = {}

    def children(self, sha: str):
        if sha not in self._cache:
            result = self.api.request("GET", "/git/trees/" + quote(sha, safe=""))
            if result.get("truncated"):
                raise QuickPRError("GitHub 返回的单层目录不完整，无法可靠查询或覆盖，请缩小目录规模。")
            self._cache[sha] = {entry["path"]: entry for entry in result["tree"]}
        return self._cache[sha]

    def lookup(self, path: str):
        if not path:
            return {"type": "tree", "sha": self.sha, "mode": "040000", "path": ""}
        sha = self.sha
        parts = path.split("/")
        for index, part in enumerate(parts):
            entry = self.children(sha).get(part)
            if entry is None:
                return None
            if index == len(parts) - 1:
                return entry
            if entry["type"] != "tree":
                return None
            sha = entry["sha"]
        return None

    def walk(self, path="", depth=2):
        if depth < 1:
            raise QuickPRError("目录显示深度必须大于零。")
        path = normalize_destination(path)
        entry = self.lookup(path)
        if entry is None or entry["type"] != "tree":
            raise QuickPRError("远端目录不存在：{}".format(path or "/"))

        def visit(sha, prefix, remaining):
            for child in sorted(self.children(sha).values(), key=lambda e: (e["type"] != "tree", e["path"].casefold(), e["path"])):
                full = prefix + child["path"]
                yield full, child
                if child["type"] == "tree" and remaining > 1:
                    yield from visit(child["sha"], full + "/", remaining - 1)

        yield from visit(entry["sha"], path + "/" if path else "", depth)
