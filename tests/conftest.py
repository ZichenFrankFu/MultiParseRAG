import io
import zipfile

import pytest
from PIL import Image

from multiparserag.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        storage_backend="local",
        public_base_url="http://testserver",
        ragflow_dataset_ids="kb-1",
        ragflow_api_key="test-key",
    )


@pytest.fixture
def mineru_zip():
    png = io.BytesIO()
    Image.new("RGB", (16, 16), "#ed794c").save(png, format="PNG")
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as bundle:
        bundle.writestr(
            "markdown.md", "# 示例文档\n\n可引用的事实。\n\n![示意图](images/chart.png)\n"
        )
        bundle.writestr("images/chart.png", png.getvalue())
        bundle.writestr("middle_json.json", '{"pdf_info": []}')
        bundle.writestr("structured_content.json", "[]")
    return result.getvalue()
