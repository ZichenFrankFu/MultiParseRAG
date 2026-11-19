# v0.0.1 验证记录

## 已在开发环境完成

- 34 项自动化测试通过；GitHub Actions 的 Python 3.11、3.12、3.13 矩阵均通过，包括 Ruff、OpenAPI 一致性、依赖导出一致性和 JavaScript 语法检查。
- MinerU **4.1.7 / flash**：使用包含正文与插图的自制 PDF，分别验证 CLI 和 HTTP file-parse-server 的真实解析。
- 完整本地链路：上传 PDF → 异步解析 → Markdown 图片 URL 替换 → 匿名 HTTP GET 图片 → Markdown 下载。
- 浏览器：桌面 1440px 和手机 390px，导航与无横向溢出；真实上传、图片加载、下载及未配置 RAGFlow 的提示；无 JavaScript 页面错误。
- MinIO：官方固定版本安装包 SHA256 校验、Docker 镜像构建、Compose 启动、图片桶初始化均通过。GitHub Actions 使用真实 MinIO 完成图片上传，并从容器外对公开 URL 进行匿名 GET，确认返回 `image/png` 与有效图片内容。
- [完整 CI 验证记录](https://github.com/ZichenFrankFu/MultiParseRAG/actions/runs/42936461261)：Python 矩阵与容器任务全部成功。

## 仍需部署环境验收

- RAGFlow 尚未配置部署、嵌入模型及 API Key。已完成 HTTP 契约、业务错误、索引恢复、状态枚举和上下文证据测试；真实索引与检索需要运行 `scripts/smoke_live.py --dataset-id ...`。
- MinerU `basic/standard/advanced` 和复杂扫描资料尚未进行质量验收。

开发执行器未提供 Docker daemon，并限制 MinIO 获取主机网卡信息；相关验证已在 GitHub Actions 的 Docker 环境完成。

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
