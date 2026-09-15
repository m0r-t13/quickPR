"""Publish a frozen upload plan as a single Git commit."""

import base64
from dataclasses import dataclass
from urllib.parse import quote

from .errors import APIError, QuickPRError
from .github import validate_branch


@dataclass(frozen=True)
class UploadResult:
    sha: str
    branch: str
    url: str


def check_writable(api, snapshot):
    if not api.token:
        raise QuickPRError("上传需要登录：运行 gh auth login，或设置 GH_TOKEN / GITHUB_TOKEN。")
    if snapshot.archived:
        raise QuickPRError("仓库已归档，无法上传。")
    if snapshot.can_push is False:
        raise QuickPRError("当前账号没有此仓库的写入权限。请使用具有写入权限的账号或仓库。")


def publish(api, snapshot, changes, message, new_branch=None, progress=None):
    changed = [change for change in changes if change.action != "skip"]
    if not changed:
        return None
    check_writable(api, snapshot)
    if not message.strip():
        raise QuickPRError("提交说明不能为空。")
    target = validate_branch(new_branch or snapshot.branch)

    def assert_current():
        if api.head(snapshot.branch) != snapshot.head:
            raise QuickPRError("预览后远端分支已有变化。请重新运行，检查新的覆盖预览后再上传。")

    assert_current()
    if new_branch:
        try:
            api.head(target)
        except APIError as error:
            if error.status != 404:
                raise
        else:
            raise QuickPRError("新分支已存在，请换一个名称，或使用 --branch 上传到该分支。")

    entries, uploaded = [], set()
    for index, change in enumerate(changed, 1):
        file = change.file
        if progress:
            progress("[{}/{}] 上传 {}".format(index, len(changed), file.remote_path))
        if file.sha not in uploaded:
            blob = api.request("POST", "/git/blobs", {"content": base64.b64encode(file.data).decode("ascii"), "encoding": "base64"})
            if blob.get("sha") != file.sha:
                raise QuickPRError("GitHub 返回的文件校验值不匹配，已停止更新分支。")
            uploaded.add(file.sha)
        entries.append({"path": file.remote_path, "mode": change.mode, "type": "blob", "sha": file.sha})

    # Always retain the base tree; only explicitly selected file entries change.
    tree = api.request("POST", "/git/trees", {"base_tree": snapshot.tree_sha, "tree": entries})
    commit = api.request("POST", "/git/commits", {"message": message, "tree": tree["sha"], "parents": [snapshot.head]})
    sha = commit["sha"]
    result = UploadResult(sha, target, "https://github.com/{}/commit/{}".format(api.repo, sha))
    assert_current()
    if progress:
        progress("正在更新分支 {} …".format(target))
    try:
        if new_branch:
            api.request("POST", "/git/refs", {"ref": "refs/heads/" + target, "sha": sha})
        else:
            api.request("PATCH", "/git/refs/heads/" + quote(target, safe=""), {"sha": sha, "force": False})
    except QuickPRError as error:
        # A response may be lost after GitHub accepted the update. Read back;
        # do not blindly retry a mutation or report an unverified success.
        try:
            if api.head(target) == sha:
                return result
        except QuickPRError:
            pass
        raise QuickPRError(
            "{}\n分支更新未确认成功；请先核实 GitHub，避免重复提交。\n"
            "候选提交：{}\n目标分支：{}\n"
            "如受分支保护限制，可用 --new-branch 新分支名。".format(error, result.url, target)
        ) from None
    return result
