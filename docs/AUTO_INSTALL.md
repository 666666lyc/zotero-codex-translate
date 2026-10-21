# 0.2 自动环境安装

XPI 内仅包含插件代码、约几十 KB 的纯 Python 桥接 wheel、安装脚本、固定下载清单和许可证。没有 Python、BabelDOC、模型或字体二进制。

自动安装仅开放 Windows x64 / Zotero 9。其他系统的协议和构建测试不代表自动安装已经支持；可继续使用高级设置中的外部桥接程序。

## 目录与状态

全部托管内容位于当前 Zotero 数据目录：

```text
codex-translate/
  runtime/
    tools/<uv摘要>/uv.exe
    python/cpython-3.12.15-windows-x86_64-none/
    envs/<环境版本>-<安装ID>/
    bundles/<环境版本>/
    cache/                 # uv 的可复用缓存
    current.json           # 全部检查通过后才切换
    previous.json          # 上一可用环境
    state.json             # Python 安装阶段与错误
    install.lock           # 操作系统锁，进程退出自动释放
  engine-assets/.cache/babeldoc/
  jobs/                    # 原文副本、进度和译文
```

`current.json` 含绝对路径；数据目录迁移后创建新的虚拟环境，旧环境不重命名。任务、段落缓存和下载缓存保留。自动安装不用用户的 Python、pip 镜像或 uv 配置，也不注册系统 Python 或修改 PATH。

环境标识由桥接 wheel、安装脚本、依赖锁和 Python 下载目录摘要共同生成。已有匹配环境会先检查原生引擎和完整资源，成功后复用；失败可在设置中“检查与修复”。升级先创建最终路径的新环境，检查成功后原子切换记录。正在翻译时不允许手动修复或切换。

## 固定来源

- uv 0.13.4：Astral GitHub Releases；ZIP 校验 SHA256，只解压固定的 `uv.exe`。
- Python 3.12.15 / 20261013：Astral python-build-standalone；仅将指定条目交给 uv，uv 校验归档摘要。禁用用户可执行入口和 Windows 注册表写入。
- BabelDOC 0.6.4 和 84 个依赖：官方 PyPI 的不可变 wheel URL；锁定单个兼容文件和 SHA256，强制二进制及哈希安装，不从源码编译。
- 全部 182 个资源：BabelDOC 0.6.4 内嵌 SHA3-256；GitHub 资源和 Hugging Face 模型 URL 固定到提交，tiktoken 固定内容摘要。没有最快镜像探测或自动第三方镜像切换。

资源下载的进度只代表资源阶段；uv/Python/依赖阶段显示真实输出或不确定进度。单项资源最多三次尝试，校验成功才将 `.part` 原子替换为正式文件。取消保留已验证文件，当前未完成文件下次重新下载。Zotero 关闭会通知安装器取消，终止 uv 子进程树；强制退出后操作系统释放锁，下一次重新校验。

环境就绪不要求 Codex 登录。Codex CLI 缺失或登录失效时，在设置中检测并按提示处理；无需重装环境。CLI 保持系统独立安装，不放入 XPI 下载清单。

## 网络失败

检查错误中的阶段和主机：`github.com`（uv/Python）、`files.pythonhosted.org`（wheel）、`raw.githubusercontent.com`（字体/映射）、`huggingface.co`（模型）或 `openaipublic.blob.core.windows.net`（分词）。修复网络后选择“准备 / 继续”。磁盘不足需先释放数据目录所在盘空间，建议首次安装至少预留 3 GB。

自定义桥接路径优先，自动安装不会覆盖它。清空高级设置的桥接路径后才能切换为托管环境。旧版离线 ZIP 仅保留为外部桥接兼容入口，不作为 0.2 的默认发行产物。

## 维护者

`scripts/resolve_upstream.py` 从官方源生成 Windows wheel 锁；不能使用已有开发环境的 freeze。`scripts/lock_assets.py` 将 BabelDOC 对应资源固定到上游提交并提取字体版权。更新时重新核对资源、许可证和实际平台测试。

`scripts/runtime_smoke.py` 从空目录运行同一个安装脚本，可用 `--cancel-after 5` 验证取消，`--root` 验证重试与缓存复用。`--repair` 强制重装锁定依赖。真实账号验证单独使用 `scripts/live_translation_smoke.py --bridge <托管Python> --managed --resource-dir <engine-assets>`，会消耗 Codex 额度。
