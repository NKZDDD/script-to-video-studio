# -*- coding: utf-8 -*-
"""剧本文档解析：txt / md / docx / pdf → 纯文本。

- txt/md：多编码探测（utf-8-sig → utf-8 → gbk → gb18030 → latin-1）
- docx  ：stdlib zipfile 解 word/document.xml，无第三方依赖
- pdf   ：pypdf 优先，PyMuPDF(fitz) 兜底；两者都没有时给出明确提示
- doc   ：老二进制格式不支持，提示另存为 docx
"""

from __future__ import annotations

import io
import os
import re
import zipfile

SUPPORTED = (".txt", ".md", ".markdown", ".docx", ".pdf")

_ENT = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " "}


class ParseError(RuntimeError):
    pass


def _decode_entities(s: str) -> str:
    s = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), s)
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    return re.sub(r"&([a-zA-Z][a-zA-Z0-9]*);",
                  lambda m: _ENT.get(m.group(1).lower(), m.group(0)), s)


def _read_text_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def parse_docx(raw: bytes) -> str:
    """docx = zip；正文在 word/document.xml，按 <w:p> 切段。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ParseError("不是有效的 docx（老版 .doc 请先另存为 .docx）") from exc
    try:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except KeyError as exc:
        raise ParseError("docx 中找不到 word/document.xml") from exc

    paras = []
    for m in re.finditer(r"<w:p\b[^>]*>([\s\S]*?)</w:p>", xml):
        body = m.group(1)
        body = re.sub(r"<w:tab\b[^>]*/?>", "\t", body)
        body = re.sub(r"<w:br\b[^>]*/?>", "\n", body)
        runs = re.findall(r"<w:t\b[^>]*>([\s\S]*?)</w:t>", body)
        paras.append(_decode_entities("".join(runs)))
    text = "\n".join(paras)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_pdf(raw: bytes) -> str:
    """优先 pypdf；失败或未装则用 PyMuPDF。"""
    errs = []
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        pages = [(p.extract_text() or "") for p in reader.pages]
        text = "\n\n".join(pages).strip()
        if text:
            return re.sub(r"\n{3,}", "\n\n", text)
        errs.append("pypdf 提取为空（可能是扫描件）")
    except ImportError:
        errs.append("未安装 pypdf")
    except Exception as exc:                                  # noqa: BLE001
        errs.append(f"pypdf 失败: {exc}")

    try:
        import fitz

        doc = fitz.open(stream=raw, filetype="pdf")
        text = "\n\n".join(page.get_text() for page in doc).strip()
        if text:
            return re.sub(r"\n{3,}", "\n\n", text)
        errs.append("PyMuPDF 提取为空（可能是扫描件，需要 OCR）")
    except ImportError:
        errs.append("未安装 PyMuPDF")
    except Exception as exc:                                  # noqa: BLE001
        errs.append(f"PyMuPDF 失败: {exc}")

    raise ParseError("PDF 解析失败：" + "；".join(errs) +
                     "。可 pip install pypdf，或把 PDF 内容复制到文本框。")


def parse_bytes(filename: str, raw: bytes) -> str:
    """按扩展名分派。返回纯文本，失败抛 ParseError。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in (".txt", ".md", ".markdown"):
        return _read_text_bytes(raw).strip()
    if ext == ".docx":
        return parse_docx(raw)
    if ext == ".pdf":
        return parse_pdf(raw)
    if ext == ".doc":
        raise ParseError("不支持老版 .doc 二进制格式，请用 Word 另存为 .docx")
    # 无扩展名或未知：尝试按 zip(docx) → 文本
    if raw[:2] == b"PK":
        return parse_docx(raw)
    if raw[:5] == b"%PDF-":
        return parse_pdf(raw)
    return _read_text_bytes(raw).strip()


def parse_file(path: str) -> str:
    with open(path, "rb") as f:
        return parse_bytes(os.path.basename(path), f.read())


def stats(text: str) -> dict:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return {"chars": len(text), "lines": len(lines),
            "preview": text[:300] + ("…" if len(text) > 300 else "")}
