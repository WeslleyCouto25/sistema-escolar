"""Migra armazenamento legado do PostgreSQL/disco local para Cloudflare R2.

Seguro por padrão:
  python migrate_storage_to_r2.py                 # só contabiliza
  python migrate_storage_to_r2.py --apply         # copia para R2, mantém legado
  python migrate_storage_to_r2.py --apply --purge-db  # copia/confirma e limpa blobs legados

O segundo comando funciona mesmo se o primeiro já tiver sido executado.
Nenhum objeto R2 é apagado por este script.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
from pathlib import Path

from db_pool import get_db_connection
from r2_storage import (
    decode_data_url, extension_for_mime, is_configured, make_key,
    object_exists, upload_bytes, upload_path,
)

DATA_LINK_RE = re.compile(
    r'(<a\b[^>]*?href=["\'])data:([^;"\']+);base64,([A-Za-z0-9+/=\r\n]+)(["\'][^>]*>)',
    re.I,
)


def _legacy_static_path(value):
    if not value:
        return None
    text = str(value).replace("\\", "/").lstrip("/")
    if text.startswith("static/"):
        return Path(text)
    return Path("static") / text


def _key_confirmed(key, apply):
    if not key:
        return False
    if not apply:
        return True
    return object_exists(key)


def migrate(apply=False, purge_db=False):
    if purge_db and not apply:
        raise RuntimeError("--purge-db exige --apply.")
    if apply and not is_configured():
        raise RuntimeError("Configure o Cloudflare R2 antes de executar a migração.")

    counters = {
        "contratos": 0, "integrados": 0, "projetos": 0,
        "anexos": 0, "documentos_embutidos": 0, "ignorados": 0,
    }
    conn=get_db_connection(); cur=conn.cursor()
    paths_to_delete = []
    try:
        # 1) Contratos: assinatura, foto e PDF. Permite purga em uma segunda execução.
        cur.execute("""
            SELECT id FROM contratos_alunos
            WHERE assinatura_base64 IS NOT NULL OR foto_assinatura_base64 IS NOT NULL OR pdf_assinado IS NOT NULL
            ORDER BY id
        """)
        contrato_ids = [r["id"] for r in cur.fetchall()]
        for contrato_id in contrato_ids:
            # Um contrato pesado por vez: evita carregar todos os BYTEA/base64 na RAM durante a migração.
            cur.execute("""
                SELECT id,assinatura_base64,foto_assinatura_base64,pdf_assinado,
                       assinatura_r2_key,foto_assinatura_r2_key,pdf_assinado_r2_key
                FROM contratos_alunos WHERE id=%s
            """, (contrato_id,))
            row = cur.fetchone()
            if not row:
                continue
            updates={}; confirmed={}
            for src,key_col,mime_col,category,default_name in [
                ("assinatura_base64","assinatura_r2_key","assinatura_mime","contratos/assinaturas","assinatura"),
                ("foto_assinatura_base64","foto_assinatura_r2_key","foto_assinatura_mime","contratos/fotos","foto"),
            ]:
                key=row.get(key_col); raw_value=row.get(src); mime=None
                if raw_value and not key:
                    raw,mime=decode_data_url(raw_value)
                    key=make_key(category,f"{default_name}{extension_for_mime(mime)}",row["id"])
                    if apply: upload_bytes(raw,key,mime,{"contrato_id":row["id"]})
                    updates[key_col]=key; updates[mime_col]=mime
                confirmed[src]=_key_confirmed(key,apply)
            pdf_key=row.get("pdf_assinado_r2_key")
            if row.get("pdf_assinado") is not None and not pdf_key:
                pdf_key=make_key("contratos/pdfs","contrato.pdf",row["id"])
                if apply: upload_bytes(bytes(row["pdf_assinado"]),pdf_key,"application/pdf",{"contrato_id":row["id"]})
                updates["pdf_assinado_r2_key"]=pdf_key
            confirmed["pdf_assinado"]=_key_confirmed(pdf_key,apply)

            sets=[f"{k}=%s" for k in updates]; vals=list(updates.values())
            if purge_db:
                if row.get("assinatura_base64") and confirmed["assinatura_base64"]: sets.append("assinatura_base64=NULL")
                if row.get("foto_assinatura_base64") and confirmed["foto_assinatura_base64"]: sets.append("foto_assinatura_base64=NULL")
                if row.get("pdf_assinado") is not None and confirmed["pdf_assinado"]: sets.append("pdf_assinado=NULL")
            if sets:
                counters["contratos"]+=1
                if apply: cur.execute(f"UPDATE contratos_alunos SET {', '.join(sets)} WHERE id=%s",(*vals,row["id"]))

        # 2) Pacotes integrados. Busca também registros já migrados para permitir a purga posterior.
        cur.execute("""
            SELECT id FROM solicitacoes_documentos_integrados
            WHERE pdf_final IS NOT NULL OR pdf_previa IS NOT NULL
            ORDER BY id
        """)
        integrado_ids = [r["id"] for r in cur.fetchall()]
        for integrado_id in integrado_ids:
            # Um pacote por vez para não recriar o mesmo problema de memória que estamos corrigindo.
            cur.execute("""
                SELECT id,pdf_previa,pdf_final,nome_arquivo,arquivo_r2_key
                FROM solicitacoes_documentos_integrados WHERE id=%s
            """, (integrado_id,))
            row = cur.fetchone()
            if not row:
                continue
            key=row.get("arquivo_r2_key")
            if not key:
                blob=row.get("pdf_final") if row.get("pdf_final") is not None else row.get("pdf_previa")
                if blob is None: continue
                key=make_key("documentos-integrados",row.get("nome_arquivo") or "documentos.pdf",row["id"])
                if apply: upload_bytes(bytes(blob),key,"application/pdf",{"solicitacao_id":row["id"]})
            sets=[]; vals=[]
            if key != row.get("arquivo_r2_key"):
                sets.append("arquivo_r2_key=%s"); vals.append(key)
            if purge_db and _key_confirmed(key,apply):
                sets.extend(["pdf_previa=NULL","pdf_final=NULL"])
            if sets:
                counters["integrados"]+=1
                if apply: cur.execute(f"UPDATE solicitacoes_documentos_integrados SET {', '.join(sets)} WHERE id=%s",(*vals,row["id"]))

        # 3) Projeto Final e arquivo da atividade: disco efêmero -> R2.
        cur.execute("""SELECT id,arquivo_path,arquivo_atividade_path,arquivo_r2_key,arquivo_atividade_r2_key
                       FROM projetos_finais""")
        for row in cur.fetchall():
            sets=[]; vals=[]; touched=False
            for old_col,new_col,category in [
                ("arquivo_path","arquivo_r2_key","projetos-finais/alunos"),
                ("arquivo_atividade_path","arquivo_atividade_r2_key","projetos-finais/atividades"),
            ]:
                old=row.get(old_col); key=row.get(new_col)
                if old and not key:
                    path=_legacy_static_path(old)
                    if path and path.exists():
                        key=make_key(category,path.name,row["id"])
                        if apply: upload_path(path,key,mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                        sets.append(f"{new_col}=%s"); vals.append(key); touched=True
                    else:
                        counters["ignorados"]+=1
                if purge_db and old and _key_confirmed(key,apply):
                    sets.append(f"{old_col}=NULL"); touched=True
                    path_to_remove = _legacy_static_path(old)
                    if path_to_remove and path_to_remove.exists():
                        paths_to_delete.append(path_to_remove)
            if sets:
                counters["projetos"]+=1
                if apply: cur.execute(f"UPDATE projetos_finais SET {', '.join(sets)} WHERE id=%s",(*vals,row["id"]))

        # 4) Anexos de disciplinas alternativas.
        cur.execute("SELECT to_regclass('public.anexos_disciplina_alternativa') AS reg")
        if (cur.fetchone() or {}).get("reg"):
            cur.execute("SELECT id,url_arquivo,nome_arquivo,r2_key,content_type FROM anexos_disciplina_alternativa WHERE url_arquivo IS NOT NULL")
            for row in cur.fetchall():
                key=row.get("r2_key"); url=row.get("url_arquivo")
                if url and not key:
                    path=_legacy_static_path(url)
                    if path and path.exists():
                        mime=row.get("content_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                        key=make_key("disciplinas-alternativas",row.get("nome_arquivo") or path.name,row["id"])
                        if apply: upload_path(path,key,mime)
                        if apply: cur.execute("UPDATE anexos_disciplina_alternativa SET r2_key=%s,content_type=%s WHERE id=%s",(key,mime,row["id"]))
                        counters["anexos"]+=1
                    else:
                        counters["ignorados"]+=1
                if purge_db and url and _key_confirmed(key,apply):
                    if apply:
                        cur.execute("UPDATE anexos_disciplina_alternativa SET url_arquivo=NULL WHERE id=%s",(row["id"],))
                        path_to_remove = _legacy_static_path(url)
                        if path_to_remove and path_to_remove.exists():
                            paths_to_delete.append(path_to_remove)

        # 5) Anexos antigos que foram embutidos em base64 dentro do HTML do documento.
        cur.execute("""
            SELECT id FROM documentos_autenticados
            WHERE conteudo_html LIKE '%href="data:%;base64,%'
               OR conteudo_html LIKE '%href=''data:%;base64,%'
            ORDER BY id
        """)
        documento_ids = [r["id"] for r in cur.fetchall()]
        for documento_id in documento_ids:
            cur.execute("""
                SELECT id,codigo,codigo_autenticacao,tipo,conteudo_html,metadados,
                       arquivo_r2_key,arquivo_nome,arquivo_mime
                FROM documentos_autenticados WHERE id=%s
            """, (documento_id,))
            row = cur.fetchone()
            if not row:
                continue
            html=row.get("conteudo_html") or ""
            match=DATA_LINK_RE.search(html)
            if not match:
                counters["ignorados"]+=1; continue
            mime=match.group(2) or "application/octet-stream"
            payload=re.sub(r"\s+","",match.group(3))
            key=row.get("arquivo_r2_key")
            meta={}
            try: meta=json.loads(row.get("metadados") or "{}")
            except Exception: pass
            nome=row.get("arquivo_nome") or meta.get("arquivo") or f"documento-{row['id']}{extension_for_mime(mime)}"
            if not key:
                raw=base64.b64decode(payload,validate=True)
                key=make_key("documentos-anexos",nome,row.get("codigo") or row.get("codigo_autenticacao") or row["id"])
                if apply: upload_bytes(raw,key,mime,{"documento_id":row["id"]})
            codigo=row.get("codigo") or row.get("codigo_autenticacao") or str(row["id"])
            replacement=f'{match.group(1)}/documento-anexo/{codigo}{match.group(4)}'
            html_leve=html[:match.start()]+replacement+html[match.end():]
            sets=[]; vals=[]
            if key != row.get("arquivo_r2_key"): sets.append("arquivo_r2_key=%s"); vals.append(key)
            if nome != row.get("arquivo_nome"): sets.append("arquivo_nome=%s"); vals.append(nome)
            if mime != row.get("arquivo_mime"): sets.append("arquivo_mime=%s"); vals.append(mime)
            if purge_db and _key_confirmed(key,apply): sets.append("conteudo_html=%s"); vals.append(html_leve)
            if sets:
                counters["documentos_embutidos"]+=1
                if apply: cur.execute(f"UPDATE documentos_autenticados SET {', '.join(sets)} WHERE id=%s",(*vals,row["id"]))

        if apply:
            conn.commit()
            # Só remove cópias locais depois de o banco ter confirmado as chaves R2.
            if purge_db:
                for path_to_remove in paths_to_delete:
                    try:
                        path_to_remove.unlink(missing_ok=True)
                    except Exception as exc:
                        print(f"[R2 MIGRATION] aviso: não foi possível apagar {path_to_remove}: {exc}")
        else:
            conn.rollback()
        print("[R2 MIGRATION]",counters,"APLICADO" if apply else "DRY-RUN")
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--purge-db",action="store_true",help="Limpa blobs/caminhos legados somente quando o objeto R2 está confirmado.")
    args=parser.parse_args()
    migrate(apply=args.apply,purge_db=args.purge_db)
