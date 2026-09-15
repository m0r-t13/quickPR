# 第一版验证记录

日期：2026-09-15。开发及本机测试环境：macOS，Python 3.9.6。

最终自动测试结果：54 项通过，0 失败，0 跳过。

## 已验证

- 自动测试覆盖本地路径映射、单/多文件和目录、中文/空格、二进制、大小限制、敏感文件、.git 排除、符号链接、远端文件/目录冲突、保留执行位。
- GitHub 传输测试覆盖固定域名、认证请求、错误隐藏令牌、网络错误、树按需读取、目录深度和截断结果拒绝。
- 上传测试覆盖保留原始 base tree、一次提交、非强制更新、并发修改中止、新分支、保护规则拒绝和丢失成功响应后的核实。
- 完整终端向导测试覆盖选择本地文件、预览、确认到上传；另测 dry-run、取消、无变化不提交及只读目录查询。
- 使用临时真实 Git 对象库执行 CLI 集成测试：更新一个文件、增加一个二进制文件、跳过相同内容文件，确认远端同目录的其他文件保留，提交数只增加一。
- 无凭据联网查询 `octocat/Hello-World` 成功：默认分支 `master`，根目录包含 `README`。通过项目自身 HTTPS 客户端执行。
- macOS 启动脚本 `sh start-macos.command --version` 输出 `quickPR 0.1.0`；交互启动后输入 q 正常退出。
- 在项目内 `.venv` 安装成功，并从项目之外运行 `quickpr --version` 和 `quickpr upload --help`，确认加载已安装包。
- 源码语法编译通过（将编译缓存放到项目内以适配开发环境权限）。

## 尚未实际验证

- Windows 上的人工运行、`.cmd` 双击行为和凭据登录。
- GitHub Actions 任务尚未在远端运行。已配置 Windows、Linux、macOS Intel 的 Python 3.9 / 3.13，加 macOS Apple Silicon 的 Python 3.13。
- 没有指定真实目标仓库，因此未执行任何真实 GitHub 上传/覆盖；写入流程通过本地 Git 集成测试和 GitHub API 边界测试验证。账户权限、组织规则和分支保护还需在实际仓库上验证。

## 复现

在项目根目录运行 `python3 -m unittest discover -s tests -v`；Windows 使用 `py -3 -m unittest discover -s tests -v`。有 Git 时运行本地对象集成测试，无 Git 时该项跳过，其余测试仍可运行。应用本身无需 Git。

构建可跨平台复制的源码包：`python3 scripts/build_release.py`，产物为 `dist/quickPR-0.1.0.zip`，不包含本机虚拟环境、缓存或凭据。
