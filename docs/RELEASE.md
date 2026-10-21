# 公开发行步骤与边界

默认仓库名 `zotero-codex-translate`，公开。创建前确认授权账号和同名仓库归属；若同名仓库是其他项目，停止，不覆盖。

1. 核对 `THIRD_PARTY_NOTICES.md`、`third_party/MODIFICATIONS.md`、所有原许可证与资源版权。`runtime/dependency-inventory.json` 必须覆盖全部锁定 wheel；摘要对应实际官方安装版本。字体、模型许可独立于训练代码和项目许可。
2. `npm ci --ignore-scripts`、`npm run build`、`npm test`、托管 Python 运行 `scripts/test_bridge.py`。Windows 空缓存 `scripts/runtime_smoke.py`、实际 Zotero 与 Codex 测试需留证。未实测平台不标支持。
3. 通过已授权 GitHub 连接或官方 gh CLI 确认账号，创建公开空仓库；普通插件用户不需要本项目 GitHub 授权。
4. 执行 `python scripts/configure_repository.py <已确认账号>/zotero-codex-translate`。重建并测试。该脚本写入真实源码地址和有效空更新清单 URL，不广告自动升级。
5. `scripts/package_release.py` 使用源码目录白名单，排除缓存、环境、生成译文、文献、测试输出和凭据，并检查常见令牌/私钥标记。另检查 Git 暂存列表和大文件，不能只依赖扫描器。
6. 初始化本地 Git（如尚未初始化），只加入核查通过的源码，配置本仓库提交身份为账号公开用户名和 noreply 地址。提交、推送 `main`，标记 `v0.1.0`。所有推送都必须使用确认过的目标仓库。
7. `python scripts/package_release.py --public` 要求干净 Git 工作区、HEAD 与版本标签相符、真实项目地址。上传 `dist/release/` 清单中的产物为 **prerelease**，明确开发预览和验收范围；禁止上传遗留 `dist/windows-*` 或离线字体模型 ZIP。
8. Release 需提供轻量 XPI、SHA256、同版本对应源码包、依赖与资源清单、许可证、中文安装说明、验证矩阵和实测流量/空间/耗时。核对远端标签、附件摘要与源码包内容。

`scripts/package_release.py` 不带 `--public` 仅生成 `dist/local-preview/` 本地可审阅产物，不代表已经建仓库或发布。账号未授权、组件授权不清或必需验收失败时，不创建公开 Release。公开发行不得含占位项目或更新地址。

项目自己的新增代码为 AGPL-3.0-only；Split-View Reader 原有 AGPL-3.0-or-later 许可继续保留。对应源码包含可读 TS/Python、锁文件、原始声明和构建脚本。BabelDOC 匹配源码档案另随源码包保存；用户直接从上游下载的其他运行库记录准确文件、摘要、上游源码地址和原许可，不伪装为本项目分发的二进制。
