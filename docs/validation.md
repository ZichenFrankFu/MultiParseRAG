# v0.0.1 验证记录

## 已在开发环境完成

- Python 3.12：自动化测试、Ruff 检查、OpenAPI 一致性、JavaScript 语法检查。
- MinerU **4.1.7 / flash**：使用包含正文与插图的自制 PDF，分别验证 CLI 和 HTTP file-parse-server 的真实解析。
- 完整本地链路：上传 PDF → 异步解析 → Markdown 图片 URL 替换 → 匿名 HTTP GET 图片 → Markdown 下载。
- 浏览器：桌面 1440px 和手机 390px，导航与无横向溢出；真实上传、图片加载、下载及未配置 RAGFlow 的提示；无 JavaScript 页面错误。
- MinIO：官方固定版本安装包的 SHA256 校验通过；SDK 上传、匿名权限验证、URL 生成通过契约测试。

## 仍需部署环境验收

- 本次开发执行器禁止 MinIO 获取主机网卡信息，服务报告 `netlinkrib: operation not permitted`。因此该执行器内的真实 MinIO 服务验收未完成。CI 的容器任务包含专用桶初始化、实际图片上传及宿主端匿名 URL GET。
- 开发执行器未提供 Docker daemon，本地只检查 Compose YAML；镜像构建与服务启动由 GitHub Actions 容器任务验证。
- RAGFlow 尚未配置部署、嵌入模型及 API Key。已完成 HTTP 契约、业务错误、索引恢复、状态枚举和上下文证据测试；真实索引与检索需要运行 `scripts/smoke_live.py --dataset-id ...`。
- MinerU `basic/standard/advanced` 和复杂扫描资料尚未进行质量验收。

## 复验命令

```bash
uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check src tests scripts
uv run pytest -q
uv run python scripts/export_schema.py --check
node --check src/multiparserag/static/app.js
uv run python scripts/smoke_live.py your-image-document.pdf --require-images
```

配置 RAGFlow 后：

```bash
uv run python scripts/smoke_live.py your-image-document.pdf --require-images --dataset-id your-dataset-id --query "文档中的一个可核实问题"
```

测试样本为开发时自制资料；仓库未包含用户文档、凭证或运行数据库。
