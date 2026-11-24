"""Conteúdos editoriais do SIGEU. Registro de estudo separado de notas oficiais."""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

BASE = Path(__file__).resolve().parent / "course_content"
MAX_SECONDS = 600
VIDEO_FRACTION = 0.90
MAX_HEARTBEAT_GAP = 25
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS estudo_editorial (
    aluno_id INTEGER NOT NULL REFERENCES alunos(id) ON DELETE CASCADE,
    disciplina_id INTEGER NOT NULL REFERENCES disciplinas(id) ON DELETE CASCADE,
    capitulo_id INTEGER NOT NULL REFERENCES capitulos(id) ON DELETE CASCADE,
    versao TEXT NOT NULL,
    recursos JSONB NOT NULL DEFAULT '[]'::jsonb,
    segundos INTEGER NOT NULL DEFAULT 0 CHECK (segundos >= 0 AND segundos <= 600),
    ultimo_heartbeat TIMESTAMPTZ,
    PRIMARY KEY (aluno_id, disciplina_id, capitulo_id, versao)
)
"""

def normalize(value):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", str(value or ""))
                           if not unicodedata.combining(c)).casefold().split())

@lru_cache(maxsize=16)
def _catalogs():
    result = {}
    for path in sorted(BASE.glob("*/catalog.json")):
        catalog = json.loads(path.read_text(encoding="utf-8"))
        if len(catalog["units"]) != 4:
            raise ValueError("Uma disciplina editorial deve ter quatro unidades.")
        for i, unit in enumerate(catalog["units"], start=1):
            unit["paragraphs"] = json.loads((path.parent / f"unidade_{i}.json").read_text(encoding="utf-8"))
            unit["number"] = i
            texts = unit["paragraphs"]
            if not 15 <= len(texts) <= 20 or any(len(x["body"].split()) < 250 for x in texts):
                raise ValueError(f"Conteúdo editorial inválido: {path.parent.name}, unidade {i}")
            ids = [b["id"] for b in unit["blocks"]]
            if len(ids) < 12 or len(ids) != len(set(ids)):
                raise ValueError("Recursos insuficientes ou duplicados")
            coverage = [n for b in unit["blocks"] if b["type"] == "reading"
                        for n in range(b["start"], b["end"])]
            if sorted(coverage) != list(range(len(texts))):
                raise ValueError("Parágrafos ausentes ou repetidos")
            videos = [b for b in unit["blocks"] if b["type"] == "video"]
            complements = [b for b in unit["blocks"] if b["type"] in ("slides", "optional_video")]
            if len(videos) != 1 or not videos[0]["required"] or len(complements) != 2 or any(b["required"] for b in complements):
                raise ValueError("Cada unidade exige um vídeo principal e dois complementares opcionais")
        for alias in catalog["aliases"]:
            key = normalize(alias)
            if key in result:
                raise ValueError("Nome de disciplina editorial duplicado")
            result[key] = catalog
    return result

def get_course(name):
    return _catalogs().get(normalize(name))

def required_ids(unit):
    return {b["id"] for b in unit["blocks"] if b["required"]}

def empty_progress():
    return {"recursos": [], "segundos": 0}

def ready(unit, progress):
    return required_ids(unit).issubset(set(progress.get("recursos", [])))

def open_units(calendar_count, grades, total=4):
    """Preserva as datas existentes e exige prova anterior. Notas já registradas continuam consultáveis."""
    complete = {g["ordem"] for g in grades if g.get("nota") is not None}
    return [n for n in range(1, total + 1)
            if n <= calendar_count and (n in complete or all(k in complete for k in range(1, n)))]

def read_progress(cur, aluno_id, disciplina_id, chapters, version):
    cur.execute("""SELECT capitulo_id, recursos, segundos FROM estudo_editorial
                   WHERE aluno_id=%s AND disciplina_id=%s AND versao=%s""",
                (aluno_id, disciplina_id, version))
    rows = {r["capitulo_id"]: {"recursos": list(r["recursos"]), "segundos": int(r["segundos"])}
            for r in cur.fetchall()}
    return {i: rows.get(c["id"], empty_progress()) for i, c in enumerate(chapters, 1)}

def validate_completion(unit, resource, fraction=None):
    block = next((b for b in unit["blocks"] if b["id"] == resource), None)
    if not block or not block["required"]:
        raise ValueError("Recurso inválido ou opcional.")
    if block["type"] == "video":
        if not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or not VIDEO_FRACTION <= fraction <= 1:
            raise ValueError("Assista a pelo menos 90% da videoaula principal.")
    return resource

def credited_seconds(previous, now, old):
    if previous is None:
        return old
    elapsed = (now - previous).total_seconds()
    if elapsed <= 0 or elapsed > MAX_HEARTBEAT_GAP:
        return old
    return min(MAX_SECONDS, old + int(elapsed))

def save_event(cur, aluno_id, disciplina_id, capitulo_id, version, unit, payload):
    if not isinstance(payload, dict):
        raise ValueError("Evento inválido.")
    event = payload.get("event")
    if event == "complete":
        resource = validate_completion(unit, payload.get("resource"), payload.get("fraction"))
    elif event not in ("heartbeat", "start"):
        raise ValueError("Evento inválido.")
    key = (aluno_id, disciplina_id, capitulo_id, version)
    cur.execute("""INSERT INTO estudo_editorial (aluno_id,disciplina_id,capitulo_id,versao)
                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""", key)
    cur.execute("""SELECT recursos,segundos,ultimo_heartbeat FROM estudo_editorial
                   WHERE aluno_id=%s AND disciplina_id=%s AND capitulo_id=%s AND versao=%s FOR UPDATE""", key)
    row = cur.fetchone()
    resources = set(row["recursos"])
    seconds = int(row["segundos"])
    last = row["ultimo_heartbeat"]
    now = datetime.now(timezone.utc)
    if event == "complete":
        resources.add(resource)
    else:
        if event == "heartbeat":
            seconds = credited_seconds(last, now, seconds)
        last = now
    cur.execute("""UPDATE estudo_editorial SET recursos=%s::jsonb, segundos=%s, ultimo_heartbeat=%s
                   WHERE aluno_id=%s AND disciplina_id=%s AND capitulo_id=%s AND versao=%s""",
                (json.dumps(sorted(resources)), seconds, last, *key))
    progress = {"recursos": sorted(resources), "segundos": seconds}
    progress["ready"] = ready(unit, progress)
    return progress

# Destaca conceitos, sem HTML inseguro. O template usa autoescape em cada segmento.
KEYWORDS = ("Psicologia Positiva", "bem-estar subjetivo", "bem-estar psicológico",
            "modelo PERMA", "forças de caráter", "resiliência", "teoria da autodeterminação",
            "ampliação e construção", "fatores de proteção", "sentido de vida",
            "afeto positivo", "afeto negativo", "satisfação com a vida", "classificação VIA",
            "autorregulação", "validade", "causalidade", "autonomia", "pertencimento")

def emphasis(body):
    # No máximo três destaques por parágrafo; não alterar o texto ou sua contagem.
    pattern = re.compile("|".join(re.escape(x) for x in sorted(KEYWORDS,key=len,reverse=True)), re.I)
    pieces, cursor, used = [], 0, set()
    for match in pattern.finditer(body):
        key = match.group().casefold()
        if key in used:
            continue
        pieces.append((body[cursor:match.start()], False))
        pieces.append((match.group(), True))
        cursor = match.end()
        used.add(key)
        if len(used) >= 3:
            break
    pieces.append((body[cursor:], False))
    return pieces
