"""Rewrite MinerU-generated Markdown and HTML image destinations, retaining source text."""

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

from .adapters.storage import AssetStorage
from .models import Asset, ServiceError

# MinerU emits inline Markdown images and HTML <img> inside table blocks.
INLINE_IMAGE = re.compile(r"(!\[(?:\\.|[^\]\\])*\]\(\s*)(<[^>]+>|[^\s)]+)([^)]*\))")
REFERENCE = re.compile(r"(^ {0,3}\[[^\]\n]+\]:\s*)(<[^>]+>|\S+)", re.MULTILINE)
HTML_IMAGE = re.compile(r"(<img\b[^>]*?\bsrc\s*=\s*[\"\'])([^\"\']+)([\"\'])", re.I)


def publish_images(
    markdown_path: Path,
    raw_root: Path,
    document_id: str,
    storage: AssetStorage,
) -> tuple[str, list[Asset]]:
    markdown = markdown_path.read_text(encoding="utf-8")
    sources = set()
    for block in MarkdownIt("commonmark").parse(markdown):
        for token in block.children or []:
            if token.type == "image":
                sources.add(token.attrGet("src"))
    sources.update(match.group(2) for match in HTML_IMAGE.finditer(markdown))
    replacements = {}
    assets = []
    for source in sorted(sources):
        parsed = urlsplit(source)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            raise ServiceError("MinerU 图片必须来自当前结果包内的相对路径。")
        path = (markdown_path.parent / unquote(parsed.path)).resolve()
        if not path.is_relative_to(raw_root.resolve()) or not path.is_file():
            raise ServiceError("解析结果引用了缺失图片或结果目录之外的路径。")
        asset = storage.publish(document_id, path, source)
        assets.append(asset)
        replacements[source] = asset.url

    def replace(match):
        original = match.group(2)
        value = original[1:-1] if original.startswith("<") else original
        value = replacements.get(value, replacements.get(unquote(value)))
        if value is None:
            return match.group(0)
        suffix = match.group(3) if match.lastindex == 3 else ""
        return match.group(1) + value + suffix

    markdown = INLINE_IMAGE.sub(replace, markdown)
    markdown = REFERENCE.sub(replace, markdown)
    markdown = HTML_IMAGE.sub(replace, markdown)
    # Do not mark ready with unresolved local Markdown images.
    for block in MarkdownIt("commonmark").parse(markdown):
        for token in block.children or []:
            if token.type == "image" and token.attrGet("src") not in replacements.values():
                raise ServiceError("图片引用格式无法安全替换，请检查原始 Markdown。")
    return markdown, assets
