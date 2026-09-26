# 开发、安装验收与版本发布

## 开发环境

```bash
uv sync --frozen
uv run pytest -q
uv run nanocursor
```

需要让全局命令跟随当前源码时，可以在仓库中执行：

```bash
uv tool install --editable --python 3.12 \
  --constraints packaging/runtime-constraints.txt \
  --build-constraints packaging/build-constraints.txt .
```

editable 安装依赖源码路径，适合开发；普通安装会复制安装包，不依赖源码目录。依赖变动后需重新安装工具环境，仅同步开发 `.venv` 不会更新它。不要把仓库 `.venv/bin/python` 指定为对外工具的运行时。

`uv sync` 同步仓库虚拟环境；`uv run` 使用项目环境运行并按需要同步；普通用户日常执行的是已经安装的 `nanocursor`。

## 更新依赖约束

修改依赖后更新锁文件并导出约束：

```bash
uv lock
uv run python scripts/export_constraints.py
uv run python scripts/export_constraints.py --check
```

运行约束排除 dev 依赖和根项目，保留平台 markers。构建环境单独锁定：

```bash
uv pip compile --no-header --no-annotate packaging/build-requirements.in \
  --output-file packaging/build-constraints.txt
```

`uv tool install` 不隐式复用 `uv.lock`，推荐安装路径显式携带两份约束。代码仍保留包元数据声明，CI 还会验证不带运行约束的安装，发现依赖兼容范围问题。

## 构建与独立安装验收

```bash
uv build --build-constraints packaging/build-constraints.txt
uv run python scripts/check_distribution.py dist
uv run python scripts/smoke_install.py dist/*.whl --python 3.12
uv run python scripts/smoke_install.py dist/*.tar.gz --python 3.12
```

检查脚本要求目录只有一个 wheel 和一个 sdist；重新构建前把之前其他版本产物移到单独目录。

安装验收创建临时 uv 工具目录、用户目录和两个业务目录，移除 API Key 环境变量，不使用开发 `.venv`。源码包会被解压、普通安装，再移走安装源目录。验证帮助/版本没有写入、必需资源存在、真实终端 setup 隐藏输入、doctor、工作路径以及读写工具。模型 API 是本地 fixture，不产生外部模型费用。脚本依赖 POSIX PTY，因此属于 macOS/Linux 验收。

产物检查排除运行数据、技术债讨论、凭据、日志和缓存，核对版本及入口，生成 `SHA256SUMS`。发布时还要包含对应依赖约束并更新校验和。

CI 覆盖 Ubuntu Python 3.11/3.12、macOS Python 3.12；WSL、其他架构和真实服务商仍需单独记录验收。CI 尚未运行的改动不能声称已通过远端矩阵。

## 发布

当前版本在 `pyproject.toml` 中维护。CLI、TUI 和 `/status` 从安装元数据读取；源码环境有 fallback。准备新版本时一并更新 Changelog、版本和 lock 根项目元数据，不能把不同代码重新发布成同一个版本。

`.github/workflows/release.yml` 默认只构建、检查并上传 Actions 产物；手动选择 `draft_release` 才创建 GitHub 草稿，且必须在匹配 `v<版本>` 的既有 tag 上运行。发布工作使用已经测试的产物，不重新构建。可给 `release` Environment 设置审批规则。

首次对外发布前，由维护者决定版本号、检查 Linux/macOS CI、核对支持平台与变更记录，再将草稿公开。工作流不自动发布到 PyPI；先确认包名所有权，后续才配置受信发布者。原生二进制、自定义安装器和后台自动更新不在当前实现范围。

## 升级兼容检查

发布前分别验证新用户、旧 v1 配置、明文旧 Key、环境变量、新凭据引用、坏 YAML 和未知 schema。保持 v1 Hooks/Provider 列表语义，部分字段显式 false/default 的修复由测试约束。

旧版程序不具备后来新增的 schema 防护，无法由新版代码补救。跨到改造前版本回退时需要恢复对应配置备份；当前 schema-aware 版本遇到未来未知格式则停止读取。不要承诺任意版本间的无损自动回退。
