# quickPR 第一版设计

用户已确认产品方向并授权实现。提供 macOS / Windows 共用的 Python 3.9+ 命令行工具，运行时仅依赖标准库；无需本地 Git 仓库或安装 Git。

- 无参数启动交互向导，数字选择本地文件/文件夹，浏览远端目录并输入目标路径；同时提供 `upload`、`tree` 子命令。
- 一个本地根目录，选择根目录内的条目，保留其相对路径。只传一个目录时默认上传该目录的内容；多个独立条目默认以共同父目录为根。`--root` 可明确指定。
- 默认分支可覆盖，支持从已有分支创建新分支。新增/覆盖/不变逐项预览，默认确认后上传，`--yes` 用于明确的自动执行。
- GitHub Git Database API：冻结本地文件内容，对比远端 blob SHA，基于原 tree 新建 tree，创建一个 commit，非强制更新分支。保留未选择的远端条目以及被覆盖文件的可执行位。
- 查询使用逐层 tree 请求，按路径/深度展示；上传也按需要获取目标路径所在的 tree，避免整库遍历和截断造成误判。
- 本地文件在预览时读入内存并固定；确认后的修改不会偷偷进入提交。单文件最多 100 MiB、单次最多 200 MiB / 1000 个文件。
- 凭据来源依次为 GH_TOKEN、GITHUB_TOKEN、已登录的 gh、终端隐藏输入；不保存凭据，不发送到 GitHub 之外的域名。
- 默认忽略 .git、node_modules、.venv、venv、__pycache__、.DS_Store；支持重复 --exclude glob。第一版不解释 .gitignore。
- 拒绝符号链接/Windows junction、路径穿越、文件与目录冲突；对 .env、私钥和常见 token 提示，只有 --allow-sensitive 才允许上传。
- 提交前检查源分支是否变化，最终更新 force=false。未知网络结果先查目标分支，仍无法确认时显示 commit 链接并要求核实，不盲目重试写请求。
- 第一版要求 GitHub.com 仓库至少已有一次提交（创建时勾选 README）。不自动初始化空仓库，不支持 Git LFS、PR 创建或删除同步。
- 文档提供两平台启动方法；CI 配置 macOS / Windows / Linux 和 Python 版本矩阵。本机验证结果与未运行的 Windows / 真实上传验证明确区分。
