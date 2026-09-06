"""Processo auxiliar isolado para render/mesclagem de PDFs.

A separação do processo evita que a memória nativa usada por WeasyPrint/Pango/Cairo
fique presa no worker web do Gunicorn após PDFs grandes.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def render(html_path: str, output_path: str, base_url: str | None = None) -> None:
    from weasyprint import HTML
    HTML(filename=html_path, base_url=base_url or None).write_pdf(target=output_path)


def merge(output_path: str, input_paths: list[str]) -> None:
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    handles = []
    try:
        for item in input_paths:
            fh = open(item, "rb")
            handles.append(fh)
            reader = PdfReader(fh)
            for page in reader.pages:
                writer.add_page(page)
        with open(output_path, "wb") as out:
            writer.write(out)
    finally:
        for fh in handles:
            try:
                fh.close()
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render")
    p_render.add_argument("html_path")
    p_render.add_argument("output_path")
    p_render.add_argument("--base-url", default="")

    p_merge = sub.add_parser("merge")
    p_merge.add_argument("output_path")
    p_merge.add_argument("input_paths", nargs="+")

    args = parser.parse_args()
    if args.command == "render":
        render(args.html_path, args.output_path, args.base_url)
    else:
        merge(args.output_path, args.input_paths)


if __name__ == "__main__":
    main()
