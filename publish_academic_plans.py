"""Publica PDFs institucionais (versao textual) em R2 a partir dos
planos fornecidos. O texto integral fica versionado no repositorio, sem
dados pessoais dos estudantes. PDFs privados sao acessados por URL assinada.
"""
from __future__ import annotations

import hashlib
import html
import io
from pathlib import Path

from weasyprint import HTML

from db_pool import get_db_connection
from r2_storage import get_client, bucket_name, upload_bytes

SOURCES = (
    ("Psicologia e Cognição no Trânsito", "transito", "psicologia-cognicao-transito"),
    ("Fundamentos da Psicologia Positiva", "positiva", "fundamentos-psicologia-positiva"),
    ("Biologia Celular e Tecidual - 80H", "biologia", "biologia-celular-tecidual-80h"),
)
BASE = Path(__file__).resolve().parent / "academic_plans"


def _render_pdf(title, source, draft=False):
    # Cada linha deriva da transcricao do PDF original, sem modificar as
    # ementas, unidades, metodologia, avaliacoes e bibliografia fornecidas.
    blocks = []
    for j, page in enumerate(source.split("=== PRÓXIMA PÁGINA ==="), 1):
        cleaned = page.strip()
        blocks.append(
            '<section class="sourcepage">'
            f'<div class="page">Documento-fonte · seção {j}</div>'
            f'<pre>{html.escape(cleaned)}</pre>'
            '</section>'
        )
    subtitulo = (
        "MINUTA ACADÊMICA DEMONSTRATIVA · pendente de homologação institucional · "
        "sem comprovação de frequência ou conclusão"
        if draft else
        "FACOP/SIGEU — Grupo Educacional Unificado · 80 horas · modelo híbrido. "
        "Versão textual integral do documento institucional enviado, com paginação adaptada."
    )
    webpage = f"""<!doctype html><html lang="pt-BR"><head>
    <meta charset="utf-8"><style>
    @page {{size:A4; margin:17mm 14mm 16mm 14mm;
      @bottom-center {{content:"SIGEU Educacional • Plano de ensino institucional • " counter(page);font-size:8pt;color:#60656c;}} }}
    body {{font-family:DejaVu Sans,Arial,sans-serif;color:#1d2630}}
    h1 {{font-size:18pt;line-height:1.35;border-bottom:3px solid #b27c35;padding-bottom:8px;margin-bottom:4px}}
    .sub {{font-size:9pt;color:#53606b;margin:0 0 18px}}
    .page {{font-weight:700;color:#374955;font-size:9pt;border-bottom:1px solid #dde1e5;margin:10px 0 6px}}
    pre {{font-family:DejaVu Sans Mono,monospace; white-space:pre-wrap;
           overflow-wrap:anywhere;word-break:break-word;font-size:7.25pt;line-height:1.3}}
    .sourcepage {{break-before:page}}
    .sourcepage:first-child {{break-before:auto}}
    </style></head><body><h1>{html.escape(title)}</h1>
    <p class="sub">{html.escape(subtitulo)}. Não substitui aprovação institucional ou versão assinada.</p>
    {''.join(blocks)}</body></html>"""
    return HTML(string=webpage).write_pdf()


def publish_academic_plans():
    client = get_client()  # valida credenciais do R2 antes de tocar o banco
    conn = get_db_connection()
    published = 0
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS planos_ensino_r2 (
                disciplina_id INTEGER PRIMARY KEY REFERENCES disciplinas(id),
                r2_key TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                formato TEXT NOT NULL DEFAULT 'versao_textual',
                atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        for course, slug, keyslug in SOURCES:
            textfile = BASE / ("source_" + slug + ".txt")
            text = textfile.read_text(encoding="utf-8")
            if len(text) < 12000:
                raise RuntimeError("Transcricao incompleta do plano de ensino")
            source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            key = f"planos-ensino/2026/{keyslug}-{source_hash[:16]}.pdf"
            cur.execute("SELECT id FROM disciplinas WHERE nome=%s LIMIT 2", (course,))
            courses = cur.fetchall()
            if len(courses) != 1:
                raise RuntimeError("Disciplina sem correspondencia univoca")
            course_id = courses[0]["id"]
            try:
                client.head_object(Bucket=bucket_name(), Key=key)
            except Exception:
                pdf = _render_pdf(course, text, draft=(slug == "biologia"))
                if not pdf.startswith(b"%PDF") or len(pdf) < 3000:
                    raise RuntimeError("PDF renderizado invalido")
                digest = hashlib.sha256(pdf).hexdigest()
                upload_bytes(pdf, key, "application/pdf",
                             {"source-sha256": source_hash,
                              "sha256": digest, "tipo": "plano-ensino-institucional"})
            remote = client.head_object(Bucket=bucket_name(), Key=key)
            if int(remote["ContentLength"]) < 3000:
                raise RuntimeError("Documento armazenado incompleto no R2")
            # Tabela preserva chave persistente, nao link temporario assinado.
            digest = remote.get("Metadata", {}).get("sha256") or source_hash
            cur.execute("""
                INSERT INTO planos_ensino_r2 (disciplina_id,r2_key,sha256,formato)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (disciplina_id) DO UPDATE SET
                  r2_key=EXCLUDED.r2_key,
                  sha256=EXCLUDED.sha256,
                  formato=EXCLUDED.formato,
                  atualizado_em=NOW()
            """, (course_id, key, digest,
                  "minuta_demonstrativa" if slug == "biologia" else "versao_textual"))
            published += 1
        conn.commit()
        return {"planos": published}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    print(publish_academic_plans())
