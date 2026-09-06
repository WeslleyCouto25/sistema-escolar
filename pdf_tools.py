"""Ferramentas de PDF com isolamento de memória em subprocesso."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

_BASE_DIR = Path(__file__).resolve().parent
_PDF_SEMAPHORE = threading.BoundedSemaphore(max(1, int(os.getenv("PDF_MAX_CONCURRENCY", "1"))))


def _run(args: list[str]) -> None:
    timeout = max(60, int(os.getenv("PDF_PROCESS_TIMEOUT", "240")))
    with _PDF_SEMAPHORE:
        subprocess.run(
            [sys.executable, str(_BASE_DIR / "pdf_worker.py"), *args],
            check=True,
            timeout=timeout,
            cwd=str(_BASE_DIR),
        )


def render_html_to_pdf_file(html_text: str, output_path: str, base_url: str | None = None) -> str:
    fd, html_path = tempfile.mkstemp(prefix="sigeu_pdf_", suffix=".html")
    os.close(fd)
    try:
        Path(html_path).write_text(html_text, encoding="utf-8")
        args = ["render", html_path, str(output_path)]
        if base_url:
            args.extend(["--base-url", str(base_url)])
        _run(args)
        return str(output_path)
    finally:
        try:
            os.unlink(html_path)
        except FileNotFoundError:
            pass


def render_html_to_pdf_bytes(html_text: str, base_url: str | None = None) -> bytes:
    fd, out_path = tempfile.mkstemp(prefix="sigeu_pdf_", suffix=".pdf")
    os.close(fd)
    try:
        render_html_to_pdf_file(html_text, out_path, base_url)
        with open(out_path, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(out_path)
        except FileNotFoundError:
            pass


def merge_pdf_files(input_paths: list[str], output_path: str) -> str:
    if not input_paths:
        raise ValueError("Nenhum PDF informado para mesclagem.")
    _run(["merge", str(output_path), *[str(p) for p in input_paths]])
    return str(output_path)
