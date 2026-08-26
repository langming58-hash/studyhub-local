#!/usr/bin/env python3
"""Synthetic document-preview acceptance checks. Uses no real course material."""

from __future__ import annotations

import concurrent.futures
import importlib.util
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any


def load_server(tmp: Path):
    os.environ["STUDY_LIBRARY_PATH"] = str(tmp / "StudyLibrary")
    os.environ["DATABASE_PATH"] = str(tmp / "studyhub.sqlite")
    os.environ["OPENAI_API_KEY"] = ""
    os.environ["OPENAI_VECTOR_STORE_ID"] = ""
    server_path = Path(__file__).resolve().parents[1] / "server.py"
    spec = importlib.util.spec_from_file_location("studyhub_server_document_preview_test", server_path)
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    server.DATA_DIR = tmp / "data"
    server.CACHE_DIR = tmp / "cache"
    server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
    server.LOG_DIR = tmp / "logs"
    server.DB_PATH = tmp / "studyhub.sqlite"
    server.DEFAULT_STUDY_ROOT = tmp / "StudyLibrary"
    server._OFFICE_CONVERTER_VERSION_CACHE.clear()
    return server


class FakeHandler:
    def __init__(self, headers: dict[str, str] | None = None):
        self.headers = headers or {"Host": "localhost:8765"}
        self.rfile = io.BytesIO()
        self.wfile = io.BytesIO()
        self.sent: dict[str, Any] = {}
        self.response_headers: list[tuple[str, str]] = []
        self.path = "/preview/1"

    def send_response(self, status: int, message: str | None = None) -> None:
        self.sent["status_code"] = status

    def send_header(self, key: str, value: str) -> None:
        self.response_headers.append((key, value))

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'self';",
        )
        return None

    def send_error(self, status: int, message: str | None = None) -> None:
        self.sent["status_code"] = status
        self.sent["error"] = message


def bind_methods(server: Any, fake: FakeHandler) -> FakeHandler:
    for name in (
        "handle_preview",
        "send_escaped_text_preview",
        "send_preview_file",
        "send_preview_notice",
        "validate_loopback_request_host",
        "handle_exception",
        "send_json",
        "send_error_json",
        "do_GET",
    ):
        setattr(fake, name, getattr(server.StudyHubHandler, name).__get__(fake, server.StudyHubHandler))
    return fake


def preview_response(server: Any, file_id: int) -> tuple[int | None, dict[str, str], bytes]:
    handler = bind_methods(server, FakeHandler())
    handler.path = f"/preview/{file_id}"
    server.StudyHubHandler.handle_preview(handler, file_id)
    headers = {key.lower(): value for key, value in handler.response_headers}
    return handler.sent.get("status_code"), headers, handler.wfile.getvalue()


def file_row(server: Any, filename: str) -> sqlite3.Row:
    conn = sqlite3.connect(server.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM files WHERE filename=? AND active=1 ORDER BY id LIMIT 1", (filename,)).fetchone()
    conn.close()
    assert row is not None, filename
    return row


def chunk_rows(server: Any, file_id: int) -> list[sqlite3.Row]:
    conn = sqlite3.connect(server.DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM document_chunks WHERE file_id=? ORDER BY chunk_index", (file_id,)).fetchall()
    conn.close()
    return rows


def write_synthetic_pptx(path: Path, title_suffix: str = "Projects Fail") -> None:
    slide1 = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="7315200" cy="914400"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Why Data Science </a:t></a:r><a:r><a:rPr b="1"/><a:t>{title_suffix}</a:t></a:r></a:p><a:p><a:r><a:t>https://example.</a:t></a:r><a:r><a:t>org/studyhub</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>"""
    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/><Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/><Override PartName="/ppt/slides/slide2.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/><Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/><Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/><Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/></Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>""",
        "ppt/presentation.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId3"/></p:sldIdLst><p:sldSz cx="9144000" cy="6858000" type="screen4x3"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>""",
        "ppt/_rels/presentation.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/></Relationships>""",
        "ppt/slides/slide1.xml": slide1,
        "ppt/slides/slide2.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr><p:sp><p:nvSpPr><p:cNvPr id="2" name="Content"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:pPr lvl="0"><a:buChar char="•"/></a:pPr><a:r><a:t>Problem one</a:t></a:r></a:p><a:p><a:pPr lvl="0"><a:buChar char="•"/></a:pPr><a:r><a:t>Problem two</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>""",
        "ppt/slides/_rels/slide1.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        "ppt/slides/_rels/slide2.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>',
        "ppt/slideMasters/slideMaster1.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/><p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles/></p:sldMaster>""",
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>""",
        "ppt/slideLayouts/slideLayout1.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>""",
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>""",
        "ppt/theme/theme1.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme"><a:themeElements><a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1></a:clrScheme><a:fontScheme name="Office"><a:majorFont><a:latin typeface="Arial"/></a:majorFont><a:minorFont><a:latin typeface="Arial"/></a:minorFont></a:fontScheme><a:fmtScheme name="Office"/></a:themeElements></a:theme>""",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files.items():
            zf.writestr(name, text)


def write_synthetic_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        zf.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        zf.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Formatted </w:t></w:r><w:r><w:t>document preview</w:t></w:r></w:p></w:body></w:document>',
        )


def write_png(path: Path) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA"
        b"\x89\x81\xb3\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def install_fake_converter(server: Any, tmp: Path, delay: float = 0) -> dict[str, int]:
    fake = tmp / "fake-soffice"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    calls = {"convert": 0, "version": 0}

    def fake_run(cmd, text=True, capture_output=True, timeout=None):
        if "--version" in cmd:
            calls["version"] += 1
            return subprocess.CompletedProcess(cmd, 0, stdout="FakeOffice 1.0\n", stderr="")
        if "--convert-to" not in cmd or "--outdir" not in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="fake converter ignores non-office command")
        calls["convert"] += 1
        if delay:
            time.sleep(delay)
        out_dir = Path(cmd[cmd.index("--outdir") + 1])
        source = Path(cmd[-1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{source.stem}.pdf").write_bytes(b"%PDF-1.4\n% synthetic converted preview\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="converted\n", stderr="")

    server.office_converter_path = lambda: str(fake)
    server.subprocess.run = fake_run
    server._OFFICE_CONVERTER_VERSION_CACHE.clear()
    return calls


def call_ask(server: Any, body: dict[str, Any]) -> dict[str, Any]:
    handler = object.__new__(server.StudyHubHandler)
    handler.parse_body_json = lambda: body
    sent: dict[str, Any] = {}
    handler.send_json = lambda data, status=200: sent.update({"status_code": status, "data": data})
    server.StudyHubHandler.handle_ask(handler)
    return sent["data"]


def setup_library(server: Any) -> Path:
    root = server.DEFAULT_STUDY_ROOT
    lecture_dir = root / "TEST4001 - Synthetic Document Preview" / "Week 01" / "01 Course Materials" / "Lecture"
    code_dir = root / "TEST4001 - Synthetic Document Preview" / "Week 01" / "02 Exercises" / "Lab"
    lecture_dir.mkdir(parents=True)
    code_dir.mkdir(parents=True)
    write_synthetic_pptx(lecture_dir / "Synthetic Slides.pptx")
    write_synthetic_docx(lecture_dir / "Synthetic Notes.docx")
    (lecture_dir / "Synthetic PDF.pdf").write_bytes(b"%PDF-1.4\n% synthetic PDF preview\n")
    write_png(lecture_dir / "Synthetic Image.png")
    (code_dir / "Synthetic Code.py").write_text("print('synthetic code preview')\n", encoding="utf-8")
    (code_dir / "Active Content.html").write_text("<script>window.bad=true</script>\n", encoding="utf-8")
    server.scan_library(root)
    return root


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        server = load_server(tmp)
        setup_library(server)
        pptx = file_row(server, "Synthetic Slides.pptx")
        docx = file_row(server, "Synthetic Notes.docx")
        pdf = file_row(server, "Synthetic PDF.pdf")
        image = file_row(server, "Synthetic Image.png")
        code = file_row(server, "Synthetic Code.py")
        active = file_row(server, "Active Content.html")

        legacy_cache = server.TEXT_CACHE_DIR / f"{pptx['sha256']}.txt"
        legacy_cache.write_text("Why Data Science \nProjects Fail\nhttps://\nexample.\norg/studyhub\n", encoding="utf-8")
        with server.connect_db() as conn:
            conn.execute("UPDATE files SET text_cache_path=? WHERE id=?", (str(legacy_cache), pptx["id"]))
            conn.execute("UPDATE file_versions SET text_cache_path=? WHERE file_id=?", (str(legacy_cache), pptx["id"]))
            conn.commit()
        legacy_stats = server.scan_library(server.DEFAULT_STUDY_ROOT)
        pptx = file_row(server, "Synthetic Slides.pptx")
        text = Path(pptx["text_cache_path"]).read_text(encoding="utf-8")
        chunks = chunk_rows(server, pptx["id"])
        legacy_cache_rebuilt_ok = (
            legacy_stats.updated_files >= 1
            and Path(pptx["text_cache_path"]).name == server.text_cache_filename(pptx["sha256"])
            and "Why Data Science Projects Fail" in text
            and "https://example.org/studyhub" in text
        )
        original_office_converter_path = server.office_converter_path
        original_subprocess_run = server.subprocess.run

        calls = install_fake_converter(server, tmp)
        pptx_status, pptx_headers, pptx_body = preview_response(server, pptx["id"])
        cached_status, cached_headers, cached_body = preview_response(server, pptx["id"])
        cache_files = sorted(server.preview_cache_dir().glob(f"file-{pptx['id']}-*.pdf"))
        old_cache = cache_files[0] if cache_files else None
        cache_reused_ok = cached_status == 200 and cached_headers.get("x-studyhub-preview-mode") == "converted_pdf" and cached_body.startswith(b"%PDF") and calls["convert"] == 1

        write_synthetic_pptx(Path(pptx["original_path"]), "Projects Succeed")
        server.scan_library(server.DEFAULT_STUDY_ROOT)
        updated = file_row(server, "Synthetic Slides.pptx")
        invalidated_status, invalidated_headers, invalidated_body = preview_response(server, updated["id"])
        new_cache_files = sorted(server.preview_cache_dir().glob(f"file-{updated['id']}-*.pdf"))
        cache_invalidated_ok = (
            invalidated_status == 200
            and invalidated_headers.get("x-studyhub-preview-mode") == "converted_pdf"
            and invalidated_body.startswith(b"%PDF")
            and calls["convert"] == 2
            and new_cache_files
            and (old_cache is None or not old_cache.exists())
        )

        for stale in list(server.preview_cache_dir().glob(f"file-{updated['id']}-*.pdf")):
            stale.unlink()
        server._OFFICE_CONVERTER_VERSION_CACHE.clear()
        concurrent_calls = install_fake_converter(server, tmp, delay=0.15)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_results = list(pool.map(lambda _idx: server.convert_office_document_to_pdf(updated, Path(updated["original_path"])), range(2)))
        concurrent_safe_ok = all(result.path and result.path.exists() for result in concurrent_results)

        server.office_converter_path = original_office_converter_path
        server.subprocess.run = original_subprocess_run
        server._OFFICE_CONVERTER_VERSION_CACHE.clear()
        real_converter = server.office_converter_path()
        real_conversion_checked = False
        if real_converter:
            real_result = server.convert_office_document_to_pdf(updated, Path(updated["original_path"]))
            real_conversion_checked = real_result.path is not None and real_result.path.exists() and real_result.path.read_bytes().startswith(b"%PDF")

        missing_server = load_server(tmp / "missing")
        setup_library(missing_server)
        missing_pptx = file_row(missing_server, "Synthetic Slides.pptx")
        missing_server.office_converter_path = lambda: ""
        missing_status, missing_headers, missing_body = preview_response(missing_server, missing_pptx["id"])

        timeout_server = load_server(tmp / "timeout")
        setup_library(timeout_server)
        timeout_pptx = file_row(timeout_server, "Synthetic Slides.pptx")
        fake = tmp / "timeout-soffice"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        timeout_server.office_converter_path = lambda: str(fake)

        def timeout_run(cmd, text=True, capture_output=True, timeout=None):
            if "--version" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="FakeOffice timeout\n", stderr="")
            raise subprocess.TimeoutExpired(cmd, timeout or 1)

        timeout_server.subprocess.run = timeout_run
        timeout_status, timeout_headers, timeout_body = preview_response(timeout_server, timeout_pptx["id"])

        doc_calls = install_fake_converter(server, tmp)
        doc_status, doc_headers, doc_body = preview_response(server, docx["id"])
        pdf_status, pdf_headers, pdf_body = preview_response(server, pdf["id"])
        image_status, image_headers, image_body = preview_response(server, image["id"])
        code_status, code_headers, code_body = preview_response(server, code["id"])
        active_status, active_headers, active_body = preview_response(server, active["id"])
        ask = call_ask(server, {"context": {"fileId": updated["id"]}, "prompt": "Explain the first slide."})

        checks = {
            "pptx_extracted_text_readable": "Why Data Science Projects Fail" in text and "Why Data Science \nProjects Fail" not in text,
            "pptx_url_reconstructed": "https://example.org/studyhub" in text,
            "pptx_bullets_reconstructed": "• Problem one" in text and "• Problem two" in text,
            "pptx_multi_slide_ordering": text.index("Slide 1") < text.index("Slide 2"),
            "pptx_slide_citations": [row["slide_start"] for row in chunks[:2]] == [1, 2]
            and all(row["source_location"].startswith("Slide ") for row in chunks[:2]),
            "pptx_legacy_text_cache_rebuilt": legacy_cache_rebuilt_ok,
            "pptx_visual_preview_converted_pdf": pptx_status == 200
            and pptx_headers.get("x-studyhub-preview-mode") == "converted_pdf"
            and pptx_headers.get("content-type", "").startswith("application/pdf")
            and pptx_body.startswith(b"%PDF"),
            "pptx_conversion_cache_reused": cache_reused_ok,
            "pptx_cache_invalidated": cache_invalidated_ok,
            "pptx_concurrent_conversion_safe": concurrent_safe_ok,
            "pptx_concurrent_conversion_single_writer": concurrent_calls["convert"] == 1,
            "missing_converter_fallback": missing_status == 200
            and missing_headers.get("x-studyhub-preview-mode") == "unavailable"
            and b"LibreOffice" in missing_body
            and b"Why Data Science Projects Fail" not in missing_body,
            "conversion_timeout_fallback": timeout_status == 200
            and timeout_headers.get("x-studyhub-preview-mode") == "unavailable"
            and b"timed out" in timeout_body
            and b"Traceback" not in timeout_body,
            "pdf_preview_regression": pdf_status == 200
            and pdf_headers.get("x-studyhub-preview-mode") == "native_pdf"
            and pdf_body.startswith(b"%PDF"),
            "docx_preview_regression": doc_status == 200
            and doc_headers.get("x-studyhub-preview-mode") == "converted_pdf"
            and doc_body.startswith(b"%PDF")
            and doc_calls["convert"] >= 1,
            "image_preview_regression": image_status == 200
            and image_headers.get("x-studyhub-preview-mode") == "image"
            and image_body.startswith(b"\x89PNG"),
            "code_text_preview_regression": code_status == 200
            and code_headers.get("x-studyhub-preview-mode") == "text"
            and b"synthetic code preview" in code_body,
            "active_content_preview_security": active_status == 200
            and active_headers.get("content-type", "").startswith("text/plain")
            and b"&lt;script&gt;" in active_body
            and b"<script>" not in active_body,
            "ask_ai_indexing_preserved": ask["status"] == "local"
            and ask["sources"]
            and ask["sources"][0]["source_file_id"] == updated["id"]
            and ask["sources"][0]["slide_start"] == 1,
            "real_libreoffice_conversion_if_available": real_conversion_checked if real_converter else True,
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
