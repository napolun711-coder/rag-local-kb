"""本地解析（）：把上传的文件解析为统一 Markdown + 元数据。

设计要点：
1. 只用已声明的依赖：pypdf（PDF）、python-docx（docx）、openpyxl（xlsx）；
   pptx 用标准库 zipfile + ElementTree（零额外依赖）；
2. 纯本地、不联网；任何异常都收敛为 ParseResult(ok=False, detail=原因)，不抛给上层；
3. 输出文本供质量门（quality.py）判分，也供入库（ingest.py）；文本长度受 PARSE_MAX_CHARS 限制。
4. **只抽取内嵌图片的字节**（assets），不在这里做 OCR，也不联网；
   是否把图片送去 OCR 由质量门 + 用户确认决定（see ingest.py）。
"""

from __future__ import annotations

import html
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from xml.etree import ElementTree

from app.features.ingest.config import get_ingest_settings

# ---------- 路由表 ----------
TEXT_EXTENSIONS = (".txt", ".md", ".csv", ".json", ".html", ".htm")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
UNSUPPORTED_EXTENSIONS = (".doc", ".xls")

ROUTE_LABELS = {
    "text": "纯文本",
    "pdf": "PDF",
    "docx": "Word 文档",
    "xlsx": "Excel 表格",
    "pptx": "PPT 演示",
    "image": "图片",
    "unsupported": "不支持的格式",
}

# 文本编码回退顺序（中文资料常见 GBK/GB18030）
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1")

BLANK_LINES = re.compile(r"\n{3,}")
TRAILING_SPACES = re.compile(r"[ \t]+\n")
HTML_SCRIPT = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
HTML_TAG = re.compile(r"(?s)<[^>]+>")
HTML_BLOCK = re.compile(r"(?i)</(p|div|br|tr|li|h[1-6]|table)[^>]*>")

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ---------- 内嵌图片抽取 ----------
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".emf", ".wmf")
DOCX_MEDIA_PREFIX = ("word/media/",)
PPTX_MEDIA_PREFIX = ("ppt/media/",)
XLSX_MEDIA_PREFIX = ("xl/media/",)

# Word 正文里的图片引用（DrawingML 的 a:blip / VML 的 v:imagedata）
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
BLIP_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
IMAGEDATA_TAG = "{urn:schemas-microsoft-com:vml}imagedata"


@dataclass
class Asset:
    """文档内嵌图片：只保存原始字节与出处，内容交给 OCR 处理。"""

    name: str                 # 展示名（如 image1.png）
    source: str               # 出处（如 word/media/image1.png / 第 3 页 / 幻灯片 2）
    data: bytes = b""
    order: int = 0            # 抽取顺序（与文档出现顺序一致）
    kind: str = "image"

    @property
    def size(self) -> int:
        return len(self.data)


@dataclass
class ParseResult:
    """本地解析结果。ok=False 表示解析失败（detail 为中文原因）。"""

    ok: bool
    route: str
    text: str = ""
    pages: int = 0
    images: int = 0
    parser: str = ""
    detail: str = ""
    truncated: bool = False
    assets: List[Asset] = field(default_factory=list)

    @property
    def route_label(self) -> str:
        return ROUTE_LABELS.get(self.route, self.route)

    @property
    def char_total(self) -> int:
        """去空白后的有效字符数。"""
        return sum(1 for ch in self.text if not ch.isspace())

    @property
    def asset_bytes(self) -> int:
        return sum(asset.size for asset in self.assets)


def detect_route(filename: str) -> str:
    """按扩展名判断解析路由。"""
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".docx":
        return "docx"
    if suffix == ".xlsx":
        return "xlsx"
    if suffix == ".pptx":
        return "pptx"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    return "unsupported"


def parse_file(path: Path, filename: str | None = None) -> ParseResult:
    """解析入口：按路由分派，异常统一收敛。"""
    name = filename or path.name
    route = detect_route(name)

    if route == "image":
        return ParseResult(
            ok=True,
            route=route,
            pages=1,
            images=1,
            parser="none",
            detail="图片文件：本地无法提取文字，需要 OCR 识别",
        )

    if route == "unsupported":
        return ParseResult(
            ok=False,
            route=route,
            parser="none",
            detail=f"暂不支持本地解析 {Path(name).suffix or '该格式'}：请另存为 .docx / .xlsx / .pdf 后重新上传",
        )

    handlers = {
        "text": _parse_text,
        "pdf": _parse_pdf,
        "docx": _parse_docx,
        "xlsx": _parse_xlsx,
        "pptx": _parse_pptx,
    }

    try:
        result = handlers[route](path)
    except Exception as exc:  # noqa: BLE001 - 任何解析异常都转为可展示的原因
        return ParseResult(
            ok=False,
            route=route,
            parser=route,
            detail=f"解析失败（{type(exc).__name__}）：{exc}",
        )

    result.truncated = _truncate(result)
    result.text = _normalize(result.text)
    return result


def _truncate(result: ParseResult) -> bool:
    """超出 PARSE_MAX_CHARS 时截断，并标注（避免超大文件撑爆内存）。"""
    limit = get_ingest_settings().parse_max_chars
    if limit > 0 and len(result.text) > limit:
        result.text = result.text[:limit]
        result.detail = (result.detail + "；文本超过上限，已截断").strip("；")
        return True
    return False


def _normalize(text: str) -> str:
    """统一换行、去掉行尾空白与多余空行、清理 NUL/控制字符。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = CONTROL_CHARS.sub("", text)
    text = TRAILING_SPACES.sub("\n", text)
    text = BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _decode(raw: bytes) -> str:
    """按 ENCODINGS 顺序尝试解码（含 GBK 系列），全部失败时用 latin-1 兜底。"""
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace")


# ============================================================
# 内嵌图片抽取（）：只取字节，不联网、不识别
# ============================================================

def _asset_limits() -> Tuple[int, int]:
    """(最多抽取张数, 单张字节上限)；0 表示不限制。"""
    settings = get_ingest_settings()
    limit = int(getattr(settings, "parse_max_assets", 0) or 0)
    max_mb = int(getattr(settings, "parse_max_asset_mb", 0) or 0)
    return (limit if limit > 0 else 0), (max_mb * 1024 * 1024 if max_mb > 0 else 0)


def _build_asset(name: str, source: str, data: bytes, order: int) -> Asset:
    """构造一个 Asset（文件名收敛为单段安全名）。"""
    return Asset(name=Path(name).name or f"image{order + 1}.bin", source=source, data=data, order=order)


def _assets_from_office(
    path: Path, prefixes: Tuple[str, ...], *, start_order: int = 0
) -> Tuple[List[Asset], List[str]]:
    """从 Office 压缩包按前缀抽图（docx: word/media、xlsx: xl/media、pptx: ppt/media）。"""
    assets: List[Asset] = []
    notes: List[str] = []
    limit, max_bytes = _asset_limits()
    try:
        with zipfile.ZipFile(str(path)) as archive:
            names = [
                name
                for name in archive.namelist()
                if any(name.startswith(prefix) for prefix in prefixes)
            ]
            names.sort()  # image1/image2… 命名天然有序
            for name in names:
                if Path(name).suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                if limit and start_order + len(assets) >= limit:
                    notes.append(f"内嵌图片超过上限（{limit} 张），其余已忽略")
                    break
                try:
                    data = archive.read(name)
                except (KeyError, OSError, zipfile.BadZipFile):
                    continue
                if not data:
                    continue
                if max_bytes and len(data) > max_bytes:
                    notes.append(
                        f"{Path(name).name} 超过单张上限（{max_bytes // 1048576} MB），已忽略"
                    )
                    continue
                assets.append(_build_asset(name, name, data, start_order + len(assets)))
    except (OSError, zipfile.BadZipFile) as exc:
        notes.append(f"内嵌图片抽取失败（{type(exc).__name__}）")
    return assets, notes


def _append_notes(result: ParseResult, notes: List[str]) -> None:
    """把抽取备注拼到 detail（不影响 ok，仅供界面展示原因）。"""
    if not notes:
        return
    extra = "；".join(dict.fromkeys(notes))
    result.detail = f"{result.detail}；{extra}".strip("；")


def _docx_assets(document, path: Path) -> Tuple[List[Asset], List[str]]:
    """Word：按正文出现顺序抽图；正文之外（页眉/页脚）的图片追加在后面。"""
    assets: List[Asset] = []
    notes: List[str] = []
    limit, max_bytes = _asset_limits()
    seen: set = set()
    truncated = False
    rels = getattr(getattr(document, "part", None), "rels", None) or {}

    def add_by_rel_id(rel_id: str) -> None:
        nonlocal truncated
        if not rel_id:
            return
        rel = rels.get(rel_id) if hasattr(rels, "get") else None
        target = getattr(rel, "target_part", None) if rel is not None else None
        if target is None:
            return
        part_name = str(getattr(target, "partname", "") or "").lstrip("/")
        if not part_name or part_name in seen:
            return
        data = getattr(target, "blob", b"") or b""
        if not data:
            return
        if limit and len(assets) >= limit:
            truncated = True
            return
        if max_bytes and len(data) > max_bytes:
            seen.add(part_name)
            notes.append(
                f"{Path(part_name).name} 超过单张上限（{max_bytes // 1048576} MB），已忽略"
            )
            return
        seen.add(part_name)
        assets.append(_build_asset(part_name, part_name, data, len(assets)))

    try:
        body = document.element.body
    except Exception:  # noqa: BLE001 - 结构异常时退回压缩包扫描
        body = None

    if body is not None:
        for node in body.iter():
            tag = str(getattr(node, "tag", ""))
            if tag == BLIP_TAG:
                add_by_rel_id(str(node.get(REL_NS + "embed") or ""))
            elif tag == IMAGEDATA_TAG:
                add_by_rel_id(str(node.get(REL_NS + "id") or ""))

    leftovers, extra_notes = _assets_from_office(
        path, DOCX_MEDIA_PREFIX, start_order=len(assets)
    )
    for asset in leftovers:
        if asset.source in seen:
            continue
        seen.add(asset.source)
        asset.order = len(assets)
        assets.append(asset)

    notes.extend(extra_notes)
    if truncated or (limit and len(assets) > limit):
        notes.append(f"内嵌图片超过上限（{limit} 张），其余已忽略")
        assets = assets[:limit]
    return assets, notes


def _pdf_assets(reader) -> Tuple[List[Asset], List[str]]:
    """PDF：逐页抽取内嵌图片（pypdf）；单页失败只记录备注。"""
    assets: List[Asset] = []
    notes: List[str] = []
    limit, max_bytes = _asset_limits()

    for index, page in enumerate(reader.pages, start=1):
        try:
            images = list(getattr(page, "images", []) or [])
        except Exception as exc:  # noqa: BLE001 - 抽图失败不影响文本解析
            notes.append(f"第 {index} 页图片抽取失败（{type(exc).__name__}）")
            continue
        for image in images:
            if limit and len(assets) >= limit:
                notes.append(f"内嵌图片超过上限（{limit} 张），其余已忽略")
                return assets, notes
            data = getattr(image, "data", b"") or b""
            if not data:
                continue
            name = str(getattr(image, "name", "") or f"page{index}-image.png")
            if max_bytes and len(data) > max_bytes:
                notes.append(f"{name} 超过单张上限（{max_bytes // 1048576} MB），已忽略")
                continue
            assets.append(_build_asset(name, f"第 {index} 页", data, len(assets)))
    return assets, notes


def _pptx_assets(path: Path, *, start_order: int = 0) -> Tuple[List[Asset], List[str]]:
    """PPT：按幻灯片顺序抽图（读 slides/_rels），再补上未被引用的媒体文件。"""
    assets: List[Asset] = []
    notes: List[str] = []
    limit, max_bytes = _asset_limits()
    slide_pattern = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
    seen: set = set()

    try:
        archive = zipfile.ZipFile(str(path))
    except (OSError, zipfile.BadZipFile) as exc:
        return [], [f"内嵌图片抽取失败（{type(exc).__name__}）"]

    with archive:
        names = archive.namelist()
        slides = [name for name in names if slide_pattern.match(name)]
        slides.sort(key=lambda name: int(slide_pattern.match(name).group(1)))

        for name in slides:
            if limit and start_order + len(assets) >= limit:
                notes.append(f"内嵌图片超过上限（{limit} 张），其余已忽略")
                break
            index = int(slide_pattern.match(name).group(1))
            rels_name = f"ppt/slides/_rels/{Path(name).name}.rels"
            if rels_name not in names:
                continue
            try:
                root = ElementTree.fromstring(archive.read(rels_name))
            except (ElementTree.ParseError, KeyError, OSError):
                continue
            for node in root.iter():
                if not str(node.tag).endswith("}Relationship"):
                    continue
                if "image" not in str(node.get("Type") or ""):
                    continue
                target = str(node.get("Target") or "")
                media = "ppt/" + target.lstrip("/").replace("../", "")
                if media in seen or media not in names:
                    continue
                if Path(media).suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                seen.add(media)
                if limit and start_order + len(assets) >= limit:
                    notes.append(f"内嵌图片超过上限（{limit} 张），其余已忽略")
                    break
                try:
                    data = archive.read(media)
                except (KeyError, OSError):
                    continue
                if not data:
                    continue
                if max_bytes and len(data) > max_bytes:
                    notes.append(
                        f"{Path(media).name} 超过单张上限（{max_bytes // 1048576} MB），已忽略"
                    )
                    continue
                assets.append(
                    _build_asset(media, f"幻灯片 {index}", data, start_order + len(assets))
                )

    leftovers, extra_notes = _assets_from_office(
        path, PPTX_MEDIA_PREFIX, start_order=start_order + len(assets)
    )
    for asset in leftovers:
        if asset.source in seen:
            continue
        seen.add(asset.source)
        asset.order = start_order + len(assets)
        assets.append(asset)
    notes.extend(extra_notes)
    if limit and start_order + len(assets) > limit:
        assets = assets[: max(limit - start_order, 0)]
    return assets, notes


def _parse_text(path: Path) -> ParseResult:
    """txt / md / csv / json / html：标准库读取。"""
    raw = path.read_bytes()
    text = _decode(raw)
    suffix = path.suffix.lower()

    if suffix in (".html", ".htm"):
        text = HTML_SCRIPT.sub(" ", text)
        text = HTML_BLOCK.sub("\n", text)
        text = HTML_TAG.sub(" ", text)
        text = html.unescape(text)
    elif suffix == ".json":
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except ValueError:
            pass  # 非法 JSON 就按原文处理，交给质量门判定

    return ParseResult(
        ok=True,
        route="text",
        text=text,
        pages=1,
        parser=f"stdlib/{suffix.lstrip('.') or 'txt'}",
    )


def _parse_pdf(path: Path) -> ParseResult:
    """PDF：pypdf 逐页抽取文本（每页之间空行分隔）。"""
    import pypdf

    reader = pypdf.PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:  # noqa: BLE001 - 空密码解密失败则继续尝试读取
            pass

    chunks: List[str] = []
    for page in reader.pages:
        try:
            chunks.append((page.extract_text() or "").strip())
        except Exception:  # noqa: BLE001 - 单页失败不影响整份文件
            chunks.append("")

    assets, notes = _pdf_assets(reader)
    result = ParseResult(
        ok=True,
        route="pdf",
        text="\n\n".join(chunks),
        pages=len(reader.pages),
        images=len(assets),
        parser=f"pypdf/{getattr(pypdf, '__version__', '')}",
        detail=f"内嵌图片 {len(assets)} 张（未识别，需 OCR）" if assets else "",
        assets=assets,
    )
    _append_notes(result, notes)
    return result


def _parse_docx(path: Path) -> ParseResult:
    """Word 文档：python-docx 读段落与表格；统计内嵌图片数（不解析图片内容）。"""
    import docx

    document = docx.Document(str(path))
    parts: List[str] = []

    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        if text:
            parts.append(text)

    for table in document.tables:
        for row in table.rows:
            cells = [
                (cell.text or "").strip().replace("\n", " ")
                for cell in row.cells
            ]
            if any(cells):
                parts.append("| " + " | ".join(cells) + " |")

    assets, notes = _docx_assets(document, path)
    result = ParseResult(
        ok=True,
        route="docx",
        text="\n\n".join(parts),
        pages=1,
        images=len(assets),
        parser=f"python-docx/{getattr(docx, '__version__', '')}",
        detail=f"内嵌图片 {len(assets)} 张（未识别，需 OCR）" if assets else "",
        assets=assets,
    )
    _append_notes(result, notes)
    return result


def _parse_xlsx(path: Path) -> ParseResult:
    """Excel：openpyxl 读单元格；小文件顺带统计内嵌图片数。"""
    from openpyxl import load_workbook

    small = path.stat().st_size <= 20 * 1024 * 1024
    workbook = load_workbook(filename=str(path), read_only=not small, data_only=True)
    parts: List[str] = []
    images = 0
    sheet_count = 0

    try:
        for sheet in workbook.worksheets:
            sheet_count += 1
            parts.append("## " + str(sheet.title))
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if value is None else str(value).strip() for value in row]
                if any(cells):
                    parts.append("| " + " | ".join(cells) + " |")
            if small:
                images += len(getattr(sheet, "_images", []) or [])
    finally:
        workbook.close()

    import openpyxl

    assets, notes = _assets_from_office(path, XLSX_MEDIA_PREFIX)
    result = ParseResult(
        ok=True,
        route="xlsx",
        text="\n".join(parts),
        pages=sheet_count,
        images=len(assets) or images,
        parser=f"openpyxl/{getattr(openpyxl, '__version__', '')}",
        detail=f"内嵌图片 {len(assets)} 张（未识别，需 OCR）" if assets else "",
        assets=assets,
    )
    _append_notes(result, notes)
    return result


def _parse_pptx(path: Path) -> ParseResult:
    """PPT：标准库解压 + 解析 XML 取幻灯片文字（零额外依赖）。"""
    slide_pattern = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
    chunks: List[str] = []

    with zipfile.ZipFile(str(path)) as archive:
        names = [
            name for name in archive.namelist() if slide_pattern.match(name)
        ]
        names.sort(key=lambda name: int(slide_pattern.match(name).group(1)))

        for name in names:
            index = int(slide_pattern.match(name).group(1))
            try:
                root = ElementTree.fromstring(archive.read(name))
            except ElementTree.ParseError:
                continue
            lines = [
                (node.text or "").strip()
                for node in root.iter()
                if node.tag.endswith("}t") and (node.text or "").strip()
            ]
            if lines:
                chunks.append(f"## 幻灯片 {index}\n" + "\n".join(lines))

    assets, notes = _pptx_assets(path)

    result = ParseResult(
        ok=True,
        route="pptx",
        text="\n\n".join(chunks),
        pages=len(chunks),
        images=len(assets),
        parser="stdlib/pptx",
        detail=f"内嵌图片 {len(assets)} 张（未识别，需 OCR）" if assets else "",
        assets=assets,
    )
    _append_notes(result, notes)
    return result

