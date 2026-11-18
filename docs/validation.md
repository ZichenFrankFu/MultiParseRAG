# v0.0.1 验证记录

## User Intent 与 Milvus 验证

- 使用 Milvus Standalone `2.6.14` 和 PyMilvus 验证建立索引、余弦相似度检索、知识库和文档过滤、相似度阈值、证据引用及图片关联。重启应用后仍可检索已写入的向量。集合卸载后，索引、状态检查和检索均可自动重新加载集合。
- 使用合成 MinerU ZIP 和确定性的 OpenAI 兼容嵌入测试服务验证 FastAPI 链路。模拟第二批嵌入失败，确认未完成的索引不会出现在证据中，重试后分块无重复。`scripts/smoke_live.py` 也通过了带 API 鉴权的真实 HTTP 调用和匿名图片读取检查。
- 用旧版 SQLite 记录验证保留 Markdown 后可重建 Milvus 索引，旧文档 ID 仍能用于过滤。
- 工作台在 Chromium 的 1440px 和 390px 宽度下，通过模拟 API 验证索引操作、自动和手动意图、证据展示及闲聊状态。
- 嵌入模型在实际资料上的召回效果需用部署的模型验收，可运行下方真实服务脚本。

## 基础版本的验证记录

- 34 项自动化测试通过；GitHub Actions 的 Python 3.11、3.12、3.13 矩阵均通过，包括 Ruff、OpenAPI 一致性、依赖导出一致性和 JavaScript 语法检查。
- MinerU **4.1.7 / flash**：使用包含正文与插图的自制 PDF，分别验证 CLI 和 HTTP file-parse-server 的真实解析。
- 完整本地链路：上传 PDF → 异步解析 → Markdown 图片 URL 替换 → 匿名 HTTP GET 图片 → Markdown 下载。
- 浏览器：桌面 1440px 和手机 390px，导航与无横向溢出；真实上传、图片加载、下载及未配置 RAGFlow 的提示；无 JavaScript 页面错误。
- MinIO：官方固定版本安装包 SHA256 校验、Docker 镜像构建、Compose 启动、图片桶初始化均通过。GitHub Actions 使用真实 MinIO 完成图片上传，并从容器外对公开 URL 进行匿名 GET，确认返回 `image/png` 与有效图片内容。
- [完整 CI 验证记录](https://github.com/ZichenFrankFu/MultiParseRAG/actions/runs/42936461261)：Python 矩阵与容器任务全部成功。

## 仍需部署环境验收

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

配置 Milvus 和向量模型后运行完整链路验收。

```bash
uv run python scripts/smoke_live.py your-image-document.pdf --require-images --dataset-id your-dataset-id --query "文档中的一个可核实问题"
```

测试样本为开发时自制资料；仓库未包含用户文档、凭证或运行数据库。
