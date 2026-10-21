# Zotero × Codex 文献翻译

在 Zotero 9 中调用本机 Codex，把英文文本 PDF 翻译为中文附件，并在同一标签页左右对照阅读。

**0.1.0 开发预览，支持 Windows x64 / Zotero 9。** 适用于单篇有文本层的英文 PDF。扫描件 OCR、批量翻译和图片内文字翻译不在首版范围。测试记录见 [验证矩阵](docs/VALIDATION.md)。

项目仓库：https://github.com/666666lyc/zotero-codex-translate

## 安装与使用

1. 安装 Zotero 9 和 [Codex CLI](https://github.com/openai/codex)，在终端运行 `codex login`，选择 ChatGPT 登录。插件不读取或储存登录令牌。
2. 在 Zotero“工具 → 插件”中从文件安装 `zotero-codex-translate-0.1.0.xpi`。
3. 插件初始化后自动准备环境。通过“工具 → Codex 文献翻译：连接设置”查看阶段、取消、继续或检查与修复。无需安装 Python、uv、解压软件或填写桥接路径。
4. 环境就绪后点击“检测登录与模型”。找不到系统 CLI 时填写完整路径；模型列表来自当前 Codex，留空使用其默认模型。
5. 选中文献或 PDF，右键“使用 Codex 翻译全文”。完成后自动导入“中文译文”附件，选择单独打开或中英对照。

**首次固定内容约 603.9 MB（十进制），另有 HTTP 元数据和重试流量。** 其中 wheel 214.2 MB、完整资源 352.5 MB、uv 15.2 MB、Python 22.0 MB。不含 Codex CLI。本机空缓存测试耗时约 8 分 54 秒，文件内容按硬链接去重后约 1.10 GB（未计文件系统开销）；网络和磁盘不同，结果会变化。建议预留至少 3 GB。后续启动复用已验证环境，不重新下载。发行时不提供本项目的大型运行包或完整资源 ZIP。

全部工具、专用 Python、虚拟环境和缓存位于 Zotero 数据目录的 `codex-translate/`。具体来源、版本、目录及故障诊断见 [自动安装说明](docs/AUTO_INSTALL.md)。下载失败显示主机与阶段；检查网络后继续，不自动切换第三方镜像。

## 阅读、缓存与数据

- 多个原文 PDF 时先选择；孤立 PDF 需先创建父文献。支持单篇有文本层的英文 PDF。
- 已有译文时优先打开；取消或失败后可续跑。默认一个文档、一个翻译请求并发。
- “重新翻译为新版本”保留已有译文和批注。源文件指纹改变后使用新任务，不覆盖原文。
- 对照视图左英文、右中文，按对应页码同步。右键“取消主视图”进入独立浏览，再选“设为主视图”恢复同步。两侧批注分别保存。
- 公式标记、翻译或排版完整性检查失败时任务报错，不自动导入。参考文献书目信息、图片与图片内文字保留。复杂排版仍需人工检查。

段落文本经 Codex 发送给模型，使用当前账号的 Codex 权限和额度。模型列表不保证账号有相应额度。任务、原文副本与段落缓存保存在本机；中文附件按 Zotero 自身的同步设置处理。关闭任务窗口不取消翻译；关闭 Zotero 会停止子进程，重启后可续跑。

桥接仅通过标准输入输出通信，不监听端口。Codex 在隔离工作目录、只读权限和受限工具配置下运行，不操作其他聊天。

## 排障

`CODEX_MISSING`：设置 CLI 路径。`AUTH_REQUIRED`：终端重新 `codex login`。`RATE_LIMIT`：恢复额度后续跑。`SCANNED_PDF`：缺少文本层。`PLACEHOLDER_MISMATCH` / `INCOMPLETE_DOCUMENT`：公式或内容检查失败，可重试并检查原文。环境错误先查看失败主机，再使用“准备 / 继续”或“检查与修复”。

高级设置仍兼容旧版外部桥接和离线 ZIP，有效手动桥接路径优先。清空它后才能使用自动环境。

## 开发与验证

构建需要 Node.js 24 和 Python 3.12。构建本身不需要 BabelDOC：

```text
npm ci --ignore-scripts
npm run build
npm test
```

XPI 位于 `dist/`。`PYTHON` 可指定构建工具使用的解释器。Windows 自动环境测试：

```text
python scripts/runtime_smoke.py
python scripts/test_bridge.py
```

桥接测试的运行器需有 PyMuPDF 和 psutil，可使用已准备的托管 Python。真实账号测试与无账号测试分开；`scripts/live_translation_smoke.py --bridge <托管Python> --managed --resource-dir <engine-assets>` 仅使用合成文献，但会消耗 Codex 额度。

依赖由官方源重新解析并锁定 wheel URL 与 SHA256；升级步骤见安装说明。CI 使用同一安装器从空缓存准备环境，其他平台仅执行源码/协议检查。协议见 [JSON Lines v1](docs/PROTOCOL.md)。`scripts/package_bridge.py` 仅保留为开发诊断工具，不是默认发行流程。

## 开源

本项目新增代码采用 AGPL-3.0-only；复用的 Split-View Reader 保留 AGPL-3.0-or-later 授权及原作者声明。BabelDOC、字体、模型和所有依赖保持各自许可，详见 [第三方声明](THIRD_PARTY_NOTICES.md)、`third_party/` 和 `runtime/dependency-inventory.json`。发行须提供与 XPI 一致的对应源码、构建脚本、清单和摘要。Release 仅在公开内容核查和验证完成后发布；发布状态以仓库实际 Release 页面为准。
