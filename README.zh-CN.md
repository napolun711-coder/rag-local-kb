# 本地 RAG 知识库（中文使用说明）

这是一个运行在个人电脑上的本地知识库：上传文档，在本地完成解析和质量提示，由你决定是否入库，然后用对话检索自己的资料。

- 默认只监听 `127.0.0.1`，适合个人电脑使用
- Windows 10/11 提供一键部署脚本
- 支持上传、审核、入库、文件管理、长期记忆、知识图谱和局域网手机访问
- 支持本机 Ollama，也可以配置 OpenAI 兼容的生成/嵌入服务

[English README](README.md) · [故障排查](docs/TROUBLESHOOTING.md) · [发布与回滚](docs/RELEASE.md)

## 1. 准备环境

### 必需

- Windows 10/11
- 约 10 GB 可用磁盘空间（模型和依赖另计）
- 能访问 GitHub / Python 包源（仅首次安装需要）
- 如果想完全本地运行，安装 [Ollama](https://ollama.com/)，并准备：
  ```powershell
  ollama pull bge-m3
  ollama pull qwen2.5:3b
  ```

### 不需要

- 不需要预先安装 Python、Git 或 uv
- 不需要 Visual Studio / Visual C++ Build Tools
- 不需要 Docker

部署脚本会在项目目录内准备 Python 3.12、uv 和依赖，不会安装系统级软件。

## 2. 下载与安装

1. 打开 [最新 Release](https://github.com/napolun711-coder/rag-local-kb/releases/latest)。
2. 下载 **`local-rag-kb-<版本>-win64.zip`** 和对应的 **`.sha256`**。
3. 解压到一个有写入权限的普通文件夹，例如 `D:\Apps\rag-local-kb`。
4. 双击 **`deploy.cmd`**。

`deploy.cmd` 会：

- 准备项目本地 Python 3.12 和依赖
- 检测本机 Ollama 和模型
- 检查数据库目录、端口和环境
- 启动知识库引擎、HTTP shell 和主应用
- 在项目目录创建启动快捷方式

如果缺少 Ollama 或模型，它会停止并显示需要执行的 `ollama pull` 命令，不会假装部署成功。

安装完成后，双击 **`start.cmd`** 日常启动。默认地址：

```text
http://127.0.0.1:18080/web
```

如果端口被占用，启动器会自动向后寻找可用端口；实际地址以启动窗口输出为准。

## 3. 首次使用检查清单

- [ ] `deploy.cmd` 没有红色失败信息
- [ ] `start.cmd` 能打开网页
- [ ] 在设置页看到生成模型、嵌入模型、知识服务状态
- [ ] 上传一个 `.txt`、`.md`、`.pdf` 或 `.docx`
- [ ] 等待本地解析和质量提示
- [ ] 点击「入库」或「仍要入库」
- [ ] 在文件页看到已入库记录
- [ ] 提问一个能从该文档得到答案的问题
- [ ] 关闭应用后重新 `start.cmd`，确认数据仍在

## 4. 日常使用

### 上传资料

- 左侧「上传资料」：文档、PDF、图片都可以选，支持批量
- 左侧「上传图片」：图片/扫描件可以 OCR，也可以手写描述
- 批量上传会聚合为一张批次卡，减少页面干扰

### 质量提示与入库决定

质量校验是**辅助筛选**，不是替用户做最终决定：

- 质检结果会显示字数、可读性、重复、乱码、疑似扫描件等提示
- 质检有提示或没有自动通过时，文件不会未经你同意就入库
- 你可以修改正文、重试、跳过，或点击 **「仍要入库」** 由自己确认
- 「仍要入库」会在本地记录人工放行痕迹

### 文件管理

在左侧「文件管理」或 `/files` 页面可以：

- 搜索、筛选、查看本机记录
- 打开或下载原件
- 删除文件；删除操作有确认和本机权限保护
- 管理主题标签

### 对话与记忆

- 输入问题后，应用会通过 LightRAG 检索知识库并生成回答
- 会话默认保存在本地 SQLite
- 长期记忆默认使用 Balanced Local Memory，可在设置页调整或关闭
- 记忆管理、确认、删除和导出都在本机 `/memory` 页面完成

### 知识图谱

- 左侧「知识图谱」打开只读图谱
- 可以搜索实体、查看关系、下钻邻居
- 不提供上传、删除或重建索引入口，避免误操作

### 局域网手机访问

1. 电脑和手机连接同一个可信局域网。
2. 设置页 →「局域网设备授权」。
3. 手机打开页面，电脑端确认授权。
4. 手机可以聊天、上传、使用 TTS、查看文档/记忆/设置；设置保持只读。

注意：局域网使用 HTTP，不是公网级认证。不要把服务暴露到公网。

## 5. 配置说明

打开 `/settings` 可以配置：

- 生成模型（本机 Ollama 或 OpenAI 兼容服务）
- 嵌入模型和接口
- 知识服务连接
- 上传大小、解析和质量提示参数
- 记忆开关、上下文预算和提取策略
- OCR、TTS、重排和端口

保存后通常需要重启服务才会让运行中的进程使用新配置。页面会明确提示是否需要重启。

### 本机 Ollama 示例

```text
生成接口：http://127.0.0.1:11434/v1
生成模型：qwen2.5:3b
嵌入接口：http://127.0.0.1:11434
嵌入模型：bge-m3:latest
```

### 使用云端兼容接口

在设置页填写接口根地址、模型和 API Key。密钥不会回传到浏览器；留空保存表示保持原值。

## 6. 数据、升级和备份

### 数据位置

在项目目录内：

- `data/`：上传、解析、SQLite 数据库和本地状态
- `knowledge/`：知识库相关本机目录
- `logs/`：运行日志
- `.env`、`config/`：本地配置

这些内容不应提交到 Git，也不应在升级时被覆盖。

### 升级

优先使用 GitHub Release 升级：

1. 停止应用（或在升级器提示时确认）。
2. 运行：
   ```powershell
   update.cmd -CheckOnly
   update.cmd
   ```
3. 更新器会备份数据库和配置到 `.updates/backups/`，只替换代码，重启并检查健康；失败时回滚。

使用 GitHub Release 模式时，可先把 `config/update.example.json` 复制为 `config/update.json`（示例已指向本项目仓库），再运行：

```powershell
update.cmd -Release
```

离线升级：

```powershell
update.cmd -ArchivePath <zip> -Checksum <sha256>
```

### 备份

升级器会自动备份关键数据。长期使用建议定期复制整个项目目录，或至少复制 `data/`、`config/` 和 `.env`。

## 7. 停止与诊断

```powershell
.\status.ps1
.\status.ps1 --json
.\stop.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\doctor.ps1"
```

- `status.ps1`：查看 engine / shell / app 状态
- `stop.ps1`：只停止本项目启动的服务
- `doctor.ps1`：检查 Python、依赖、Ollama、目录、端口和磁盘

常见问题见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。

## 8. 安全边界

这是面向个人/小团队的本地工具，不是企业级公网服务：

- 默认只绑定 `127.0.0.1`
- 没有多租户、企业 SSO、审计平台或公网 TLS
- 局域网授权面向可信家庭/办公网络
- 不要在公网直接暴露端口
- 不要把 `.env`、密钥、数据库、上传文件或完整日志发到公开 Issue

## 9. 常见问题

### 页面显示知识服务不可用

先运行 `status.ps1`，再运行 `start.cmd`。如果引擎未启动，查看 `logs/runtime/` 下的错误日志。

### 上传后提示入库失败

现在会直接显示 LightRAG 的原始失败回执。常见原因是知识服务不可达或文档没有被 LightRAG 接受；先根据回执处理，再点「入库」重试。质量提示不会替你决定是否入库。

### 修改设置后没有生效

保存设置通常需要重启服务。启动器/设置页会提示。

### 端口被占用

启动器会自动寻找后续端口，实际地址看窗口输出；不要随意终止不属于本项目的进程。

### 升级失败

不要删除 `.updates/backups/`。查看更新器最后的错误，再运行：

```powershell
update.cmd -CheckOnly
```

必要时使用备份回滚。

## 10. 参与贡献

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。

本项目使用 MIT License。
