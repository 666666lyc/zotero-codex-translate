# 本机桥接协议 v1

插件使用 Gecko Subprocess 启动桥接程序，传入 `--data-dir`。UTF-8 JSON Lines；每行一个对象。stdout 仅用于协议，stderr 与之隔离。stdin EOF 触发工作进程及 Codex 子进程清理。

请求：`{"protocolVersion":1,"id":1,"method":"hello","params":{}}`

响应：`{"protocolVersion":1,"id":1,"result":{...}}`，或 `error:{code,message,retryable}`。事件没有 id。客户端忽略已超时请求的迟到响应。

| 方法 | 参数 | 结果 |
| --- | --- | --- |
| `hello` | 无 | 程序版本、协议版本、PDF 引擎固定版本；可选 `capabilities`，包含 `jobs.relayout`、`layout.upstreamAPI`、`jobs.recoverOutput` 等 |
| `resources.prepare` | 无 | 立即返回 started，后台准备完整固定资源；拒绝与翻译并发 |
| `resources.cancel` | 无 | 通知资源准备停止，已校验缓存保留 |
| `diagnose` | `codexPath?` | 登录检测、运行时模型列表、引擎就绪状态 |
| `document.inspect` | `sourcePath` | PDF 页数、文本量、SHA256 指纹 |
| `engine.check` | 无 | 无需联网或账号，验证 PDF 引擎及打包的原生库可导入 |
| `jobs.list` | 无 | 以创建时间倒序排列的任务 |
| `jobs.start` | `sourcePath, sourceItemKey, parentItemKey, libraryID, model?, codexPath?, offlineAssets?` | 新任务；model 留空使用运行时默认模型 |
| `jobs.relayout` | `jobId, sourcePath` | 保留原模型与指纹，创建仅用有效缓存的新排版任务；不检测登录或调用模型；原任务不改动 |
| `jobs.recoverOutput` | `jobId, sourcePath` | 恢复旧版自定义检查拦截的现有 PDF，独立记录、原字节复制、零模型请求；重复操作复用记录 |
| `jobs.status` | `jobId` | 当前任务快照 |
| `jobs.cancel` | `jobId` | 取消运行中的任务，保留段落缓存 |
| `jobs.resume` | `jobId` | 使用原文副本和原模型继续任务 |
| `jobs.acknowledge` | `jobId, outputItemKey` | 确认 Zotero 已完成幂等导入 |
| `shutdown` | 无 | 停止工作进程并退出 |

进度事件：`event:"job.progress",jobId,stage,completed,total,percent?`。
资源事件：`resources.progress` 含 stage/file/completed/total，按资源字节计；终态为 `resources.completed` 或 `resources.failed`，后者含标准 error。旧版外部桥接没有能力列表时不调用新增方法。环境安装不调用 Codex。
状态事件：`event:"job.changed",jobId,job`。状态为 starting/running/completed/imported/failed/cancelled/interrupted；progress 内 detail 为引擎阶段名。百分比不可用时客户端显示不确定进度。

任务结果仅指向该任务目录中的 `translated.zh-CN.pdf`，携带页数，页数一致时提供 1:1 页码映射。附件关联存于译文附件的 `dc:relation`：`urn:zotero-codex:translation:<sourceItemKey>:<fingerprint>:<jobId>`。Zotero 自行管理导入事务，附件采用确定性的 `codex-<jobId>.zh-CN.pdf` 文件名。若导入后、保存关系或确认结果前崩溃，重启时按同父条目下的确定性文件名恢复，避免重复导入。文件指纹变化后不复用旧文献的任务。

v0.1.0（承接本地开发版 0.1.5）的官方 API 结果附可选 `engine:"BabelDOC"`、`engineVersion:"0.6.4"`、`layoutMode:"upstream-api"`、`generatedByVersion`、`sourcePageCount`、`comparisonAvailable` 和 `sourceJobId`。恢复输出使用 `layoutMode:"recovered-existing"`，标明旧版生成来源，原任务不修改。缓存重排记录 `cacheOnly:true`；缺缓存返回 `CACHE_INCOMPLETE`，原文变化返回 `INPUT_CHANGED`。输出文件不存在、损坏或不可打开才返回 `INVALID_OUTPUT`；不因字号、重叠、字符计数或上游普通警告拒绝导入。页数不同时 `pageMap:[]`、`comparisonAvailable:false`，附件关系记录两侧页数。旧桥接未声明能力时不发送新增方法。
