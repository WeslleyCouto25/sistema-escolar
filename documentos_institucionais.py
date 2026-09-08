"""Padrão institucional único dos documentos acadêmicos do SIGEU.

Este módulo concentra o HTML de Plano de Ensino, Declaração e Histórico para
que os três documentos usem o mesmo cabeçalho, certificação, marca d'água,
autenticação, assinatura eletrônica e paginação A4.
"""
from __future__ import annotations

import base64
import hashlib
import mimetypes
import re
from datetime import date, datetime
from html import escape
from pathlib import Path
from typing import Iterable


ASSINANTES = (
    ("Tatiane R. L. Costa", "Coordenação Acadêmica e Secretaria Geral - SIGEU Educacional - Executora / FACOP Certificadora"),
    ("Natalia Nunes de Couto", "Coordenação Acadêmica e Secretaria Geral - SIGEU Educacional - Executora / FACOP Certificadora"),
)


def _assinante(codigo: str):
    """Distribui os documentos entre as duas responsáveis sem mudar ao reabrir."""
    digest = hashlib.sha256(str(codigo or "SIGEU").encode("utf-8")).digest()
    return ASSINANTES[digest[0] % len(ASSINANTES)]


def _txt(valor, padrao="N/I"):
    valor = "" if valor is None else str(valor).strip()
    return escape(valor or padrao)


def _num(valor, casas=2, padrao="N/I"):
    if valor in (None, ""):
        return padrao
    try:
        return f"{float(str(valor).replace(',', '.')):.{casas}f}"
    except Exception:
        return _txt(valor, padrao)


def _freq(valor):
    if valor in (None, ""):
        return "N/I"
    try:
        return f"{float(str(valor).replace('%', '').replace(',', '.')):.0f}%"
    except Exception:
        return _txt(valor)


def _data(valor, padrao="N/I"):
    if valor in (None, ""):
        return padrao
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    texto = str(valor).strip()
    # PostgreSQL costuma devolver YYYY-MM-DD ou YYYY-MM-DD HH:MM:SS.
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T].*)?$", texto)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    return escape(texto or padrao)


def _status(valor):
    texto = str(valor or "").strip()
    chave = texto.casefold()
    mapa = {
        "aprovado": "APROVADO",
        "aprovada": "APROVADO",
        "reprovado": "REPROVADO",
        "reprovada": "REPROVADO",
        "cursando": "CURSANDO",
        "em curso": "CURSANDO",
        "em andamento": "CURSANDO",
        "concluido": "CONCLUÍDO",
        "concluído": "CONCLUÍDO",
        "concluida": "CONCLUÍDO",
        "concluída": "CONCLUÍDO",
        "nao iniciada": "NÃO INICIADA",
        "não iniciada": "NÃO INICIADA",
        "nao iniciado": "NÃO INICIADA",
        "não iniciado": "NÃO INICIADA",
    }
    return escape(mapa.get(chave, texto.upper() if texto else "N/I"))


def _strip_inline_style(html: str) -> str:
    """Remove aparência antiga e elementos executáveis, preservando a estrutura textual."""
    html = str(html or "")
    html = re.sub(r"<(script|style|iframe)\b[^>]*>.*?</\1\s*>", "", html, flags=re.I | re.S)
    html = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", html, flags=re.I)
    html = re.sub(r"\sstyle=(\"[^\"]*\"|'[^']*')", "", html, flags=re.I)
    html = re.sub(r"\sclass=(\"[^\"]*\"|'[^']*')", "", html, flags=re.I)
    return html


def _plain_or_html(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if "<" in value and ">" in value:
        return _strip_inline_style(value)
    return escape(value).replace("\n", "<br>")


def _base_css(codigo: str):
    wm = escape(str(codigo or "SIGEU"))
    return f"""
    @page {{ size:A4; margin:0; }}
    * {{ box-sizing:border-box; }}
    html,body {{ margin:0; padding:0; background:#ececec; color:#111; font-family:Arial,Helvetica,sans-serif; }}
    body {{ font-size:9pt; line-height:1.33; }}
    .sheet {{
        width:210mm; height:297mm; margin:0 auto; background:#fff;
        padding:10.5mm 11.5mm 13mm; position:relative; overflow:hidden;
        page-break-after:always; break-after:page; page-break-inside:avoid; break-inside:avoid;
    }}
    .sheet:last-child {{ page-break-after:auto; break-after:auto; }}
    .sheet::before {{
        content:"DOCUMENTO AUTÊNTICO • {wm}";
        position:absolute; left:50%; top:50%; transform:translate(-50%,-50%) rotate(-34deg);
        width:165%; text-align:center; font-size:24pt; font-weight:700; letter-spacing:1.4px;
        color:rgba(35,35,35,.072); white-space:nowrap; z-index:0; pointer-events:none;
    }}
    .content {{ position:relative; z-index:1; }}
    .doc-header {{ display:flex; align-items:center; gap:6mm; border-bottom:1px solid #999; padding-bottom:3.5mm; margin-bottom:4mm; }}
    .doc-logo {{ width:45mm; max-height:16mm; object-fit:contain; object-position:left center; }}
    .doc-headtext {{ flex:1; min-width:0; color:#333; font-size:8.2pt; line-height:1.25; }}
    .doc-headtext strong {{ display:block; font-size:10pt; color:#111; margin-bottom:.8mm; }}
    .cert-box {{ width:55mm; text-align:right; font-size:6.7pt; line-height:1.24; color:#4b4b4b; }}
    .cert-box b {{ display:block; font-size:6.4pt; color:#555; text-transform:uppercase; letter-spacing:.45px; }}
    .cert-box strong {{ display:block; font-size:7.8pt; color:#111; margin:.5mm 0 .3mm; }}
    .title {{ text-align:center; margin:2.5mm 0 4mm; }}
    .title h1 {{ margin:0; font-size:16.5pt; line-height:1.12; }}
    .title h2 {{ margin:1mm 0 0; font-size:11.2pt; font-weight:700; color:#333; }}
    .section-title {{
        margin:3mm 0 1.7mm; padding:1mm 2mm; background:#ececec;
        border-left:1.1mm solid #606060; font-weight:700; font-size:9.3pt; text-transform:uppercase;
    }}
    table {{ border-collapse:collapse; width:100%; }}
    .info-table {{ font-size:8pt; margin-bottom:2.3mm; table-layout:fixed; }}
    .info-table th,.info-table td {{ border:1px solid #c7c7c7; padding:1.25mm 1.8mm; vertical-align:top; }}
    .info-table th {{ width:27%; text-align:left; background:#f2f2f2; font-weight:700; }}
    .bodytext {{ text-align:justify; margin:0; font-size:8.35pt; line-height:1.39; }}
    .compact {{ font-size:7.8pt; line-height:1.29; }}
    .compact p {{ margin:0 0 1.2mm; }}
    .compact ul,.compact ol {{ margin:1mm 0 1.4mm 5mm; padding-left:3mm; }}
    .compact li {{ margin:0 0 .5mm; }}
    .rich,.rich * {{ color:#111 !important; background:transparent !important; box-shadow:none !important; text-shadow:none !important; }}
    .rich p {{ margin:0 0 1.2mm; text-align:justify; }}
    .rich ul,.rich ol {{ margin:1mm 0 1.4mm 5mm; padding-left:3mm; }}
    .rich li {{ margin:0 0 .5mm; }}
    .page-note {{
        position:absolute; left:11.5mm; right:11.5mm; bottom:6.1mm; z-index:2;
        display:flex; justify-content:space-between; align-items:flex-end; gap:4mm; border-top:1px solid #d0d0d0;
        padding-top:1.6mm; font-size:6.0pt; color:#727272;
    }}
    .page-note > span:first-child {{ flex:1 1 auto; min-width:0; font-size:5.55pt; line-height:1.12; }}
    .page-note .auth-code {{ flex:0 0 auto; white-space:nowrap; text-align:right; }}
    .auth-code {{ font-family:Consolas,'Courier New',monospace; letter-spacing:.08px; }}
    .signature {{ text-align:center; margin:3mm auto 2mm; max-width:105mm; }}
    .signature strong {{ display:block; font-size:9.8pt; }}
    .signature .role {{ display:block; font-size:7.5pt; margin-top:.7mm; }}
    .signature .electronic {{ display:block; font-size:6.8pt; margin-top:1mm; color:#444; line-height:1.25; }}
    .auth-block {{ display:flex; gap:3mm; align-items:center; border-top:1px solid #aaa; padding-top:2mm; margin-top:2.2mm; font-size:6.6pt; }}
    .auth-block img {{ width:14mm; height:14mm; flex:0 0 14mm; object-fit:contain; }}
    .auth-block > div {{ flex:1; min-width:0; }}
    .validation-row {{ display:flex; align-items:center; gap:6mm; border-top:1px solid #aaa; padding-top:2.2mm; margin-top:2.7mm; }}
    .validation-row .signature {{ flex:1; margin:0; max-width:none; }}
    .validation-row .auth-block {{ flex:1.25; border-top:0; padding-top:0; margin-top:0; }}
    .hash {{ word-break:break-all; font-family:Consolas,'Courier New',monospace; font-size:5.7pt; color:#555; margin-top:.8mm; }}

    .history-table {{ table-layout:fixed; font-size:6.55pt; }}
    .history-table th,.history-table td {{ border:1px solid #bcbcbc; padding:1mm .9mm; vertical-align:middle; overflow-wrap:anywhere; }}
    .history-table th {{ background:#ededed; text-transform:uppercase; font-size:6.25pt; text-align:center; line-height:1.15; }}
    .history-table tr {{ page-break-inside:avoid; break-inside:avoid; }}
    .history-table td:nth-child(2),.history-table td:nth-child(4),.history-table td:nth-child(5),.history-table td:nth-child(6),.history-table td:nth-child(7) {{ text-align:center; }}
    .history-table tr.long-row td {{ font-size:6.15pt; line-height:1.18; }}
    .history-table tr.very-long-row td {{ font-size:5.8pt; line-height:1.14; padding-top:.75mm; padding-bottom:.75mm; }}
    .history-identity {{ margin-bottom:2.5mm; }}
    .history-carry {{ margin:0 0 2.4mm; font-size:7.5pt; }}
    .summary-grid {{ display:flex; gap:1.8mm; margin-top:2.6mm; }}
    .summary-grid div {{ flex:1; border:1px solid #c8c8c8; padding:1.7mm; font-size:7.2pt; text-align:center; }}
    .summary-grid b {{ display:block; font-size:8.8pt; margin-top:.5mm; }}

    .units-grid {{ display:grid; grid-template-columns:repeat(3,1fr); grid-template-rows:repeat(4,54mm); gap:2mm 2.6mm; width:100%; }}
    .unit-slot {{ height:54mm; padding:1.2mm 1.35mm; border:1px solid #d8d8d8; background:rgba(255,255,255,.86); overflow:hidden; }}
    .unit-slot.empty {{ border-color:#efefef; background:rgba(250,250,250,.35); }}
    .unit-slot h3 {{ margin:0 0 .8mm; font-size:6.2pt; line-height:1.08; overflow-wrap:anywhere; text-transform:none; }}
    .unit-slot ul {{ margin:0; padding-left:3.6mm; font-size:4.75pt; line-height:1.08; }}
    .unit-slot li {{ margin:0 0 .22mm; overflow-wrap:anywhere; }}
    .unit-slot.dense h3 {{ font-size:5.9pt; }} .unit-slot.dense ul {{ font-size:4.5pt; line-height:1.06; }}
    .unit-slot.tight h3 {{ font-size:5.6pt; }} .unit-slot.tight ul {{ font-size:4.25pt; line-height:1.04; }}
    .unit-slot.micro h3 {{ font-size:5.3pt; }} .unit-slot.micro ul {{ font-size:4pt; line-height:1.02; padding-left:3.2mm; }}
    .ref-list {{ font-size:7.7pt; line-height:1.33; }}
    .ref-list div {{ margin:0 0 1.6mm; text-align:justify; }}
    .appendix-unit {{ margin-bottom:4mm; }}
    .appendix-unit h3 {{ margin:0 0 1.5mm; font-size:9.3pt; }}
    .appendix-unit ul {{ margin:0; padding-left:6mm; font-size:7.6pt; line-height:1.3; }}
    .appendix-unit li {{ margin-bottom:.8mm; }}

    @media print {{
        html,body {{ background:#fff; }}
        .sheet {{ margin:0; box-shadow:none; }}
        * {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
    }}
    """


_LOGO_DATA_URI = None


def _logo_data_uri():
    """Embute a marca para a renderização PDF não depender de URL externa."""
    global _LOGO_DATA_URI
    if _LOGO_DATA_URI is not None:
        return _LOGO_DATA_URI
    logo = Path(__file__).resolve().parent / "static" / "img" / "logo.png"
    try:
        mime = mimetypes.guess_type(str(logo))[0] or "image/jpeg"
        payload = base64.b64encode(logo.read_bytes()).decode("ascii")
        _LOGO_DATA_URI = f"data:{mime};base64,{payload}"
    except Exception:
        _LOGO_DATA_URI = "/static/img/logo.png"
    return _LOGO_DATA_URI


def _header(titulo: str, subtitulo: str = ""):
    return f"""
    <div class="doc-header">
      <img class="doc-logo" src="{_logo_data_uri()}" alt="FACOP Certificadora e SIGEU Educacional">
      <div class="doc-headtext"><strong>SIGEU Educacional / FACOP Certificadora</strong>Grupo Educacional Unificado / Faculdade do Centro Oeste Paulista LTDA - Certificadora</div>
      <div class="cert-box"><b>CERTIFICADORA PARCEIRA</b><strong>FACOP CERTIFICADORA</strong>Razão Social: FACULDADE DO CENTRO OESTE PAULISTA FACOP LTDA<br>CNPJ Matriz: 04.344.730/0001-60<br>Natureza Jurídica: Sociedade Empresária Limitada</div>
    </div>
    <div class="title"><h1>{escape(titulo)}</h1>{f'<h2>{escape(subtitulo)}</h2>' if subtitulo else ''}</div>
    """


def _footer(codigo: str, pagina: int, total: int):
    return (
        "<div class='page-note'>"
        "<span>SIGEU EDUCACIONAL / FACOP CERTIFICADORA • GRUPO EDUCACIONAL UNIFICADO / FACULDADE DO CENTRO OESTE PAULISTA LTDA - CERTIFICADORA</span>"
        f"<span class='auth-code'>{escape(codigo)} • Página {pagina} de {total}</span>"
        "</div>"
    )


def _signature(codigo: str):
    nome, cargo = _assinante(codigo)
    return f"""
    <div class="signature">
      <strong>{escape(nome)}</strong>
      <span class="role">{escape(cargo)}</span>
      <span class="electronic">Documento assinado eletronicamente pelo SIGEU Educacional - Executora. Certificação institucional vinculada à FACOP Certificadora.</span>
    </div>
    """


def _auth(codigo: str, qr_code: str, hash_documento: str):
    emissao = datetime.now().strftime("%d/%m/%Y %H:%M")
    qr_html = f'<img src="{qr_code}" alt="QR Code de autenticação">' if qr_code else ""
    return f"""
    <div class="auth-block">
      {qr_html}
      <div><b>Autenticação eletrônica</b><br>Código: <span class="auth-code">{escape(codigo)}</span><br>Emissão: {emissao}<div class="hash">SHA-256: {escape(str(hash_documento or ''))}</div></div>
    </div>
    """


def _validation(codigo: str, qr_code: str, hash_documento: str):
    return f"<div class='validation-row'>{_signature(codigo)}{_auth(codigo, qr_code, hash_documento)}</div>"


def _document_start(codigo: str):
    return (
        "<!doctype html><html lang='pt-br'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='sigeu-document-template' content='institucional-2026-v2'>"
        f"<style>{_base_css(codigo)}</style></head><body>"
    )


def _document_end():
    return "</body></html>"


def build_declaration(aluno: dict, disciplina: dict, codigo: str, qr_code: str, hash_documento: str) -> str:
    nome = aluno.get("nome") or ""
    ra = aluno.get("ra") or ""
    cpf = aluno.get("cpf_formatado") or aluno.get("cpf") or "N/I"
    disc = disciplina.get("nome") or disciplina.get("disciplina_nome") or "Disciplina"
    carga = int(disciplina.get("carga_horaria") or disciplina.get("carga") or 80)
    media = disciplina.get("media_final") if disciplina.get("media_final") is not None else disciplina.get("nota_final")
    freq = disciplina.get("frequencia")
    data_conclusao = disciplina.get("data_realizacao") or disciplina.get("data_conclusao") or datetime.now()
    unidade = aluno.get("curso_referencia") or "Disciplinas / Unidades Curriculares"

    body = f"""
    <section class="sheet"><div class="content">
      {_header('DECLARAÇÃO DE CONCLUSÃO DE DISCIPLINA', disc)}
      <div class="section-title">Identificação acadêmica</div>
      <table class="info-table">
        <tr><th>Aluno</th><td>{_txt(nome)}</td></tr>
        <tr><th>Matrícula / RA</th><td>{_txt(ra)}</td></tr>
        <tr><th>CPF</th><td>{_txt(cpf)}</td></tr>
        <tr><th>Unidade Curricular</th><td>{_txt(unidade)}</td></tr>
        <tr><th>Instituição</th><td>Grupo Educacional Unificado / Faculdade do Centro Oeste Paulista LTDA - Certificadora</td></tr>
      </table>
      <div class="section-title">Declaração</div>
      <p class="bodytext">O <b>GRUPO EDUCACIONAL UNIFICADO</b>, por meio do <b>SIGEU Educacional</b>, <b>DECLARA</b>, para os devidos fins, que <b>{_txt(nome)}</b>, matrícula/RA <b>{_txt(ra)}</b>, concluiu com aproveitamento o componente curricular <b>{_txt(disc)}</b>, com carga horária de <b>{carga} horas</b>, frequência acadêmica registrada de <b>{_freq(freq)}</b> e média final <b>{_num(media)}</b>.</p>
      <p class="bodytext" style="margin-top:4mm">A conclusão acadêmica encontra-se registrada em <b>{_data(data_conclusao)}</b>.</p>
      <p class="bodytext" style="margin-top:4mm">A correspondente certificação acadêmica encontra-se formalizada pela <b>FACOP CERTIFICADORA – Faculdade do Centro Oeste Paulista LTDA</b>, responsável pela certificação da conclusão da respectiva Unidade Curricular. A presente declaração mantém vinculação documental com os registros da certificadora, sendo sua correspondência registrada e validada internamente no SIGEU Educacional para fins de identificação, controle e verificação acadêmico-documental.</p>
      {_validation(codigo, qr_code, hash_documento)}
    </div>{_footer(codigo, 1, 1)}</section>
    """
    return _document_start(codigo) + body + _document_end()


def _history_row(d: dict) -> str:
    media = d.get("media_final") if d.get("media_final") is not None else d.get("nota_final")
    nome = str(d.get("nome") or d.get("disciplina_nome") or "")
    docente = str(d.get("docente") or d.get("docente_nome") or "Docente não cadastrado")
    maior = max(len(nome), len(docente))
    soma = len(nome) + len(docente)
    classe = ""
    if maior > 78 or soma > 125:
        classe = "very-long-row"
    elif maior > 52 or soma > 92:
        classe = "long-row"
    return f"""
      <tr class="{classe}">
        <td>{_txt(nome)}</td>
        <td>{int(d.get('carga_horaria') or d.get('carga') or 80)}h</td>
        <td>{_txt(docente)}</td>
        <td>{_num(media)}</td>
        <td>{_freq(d.get('frequencia'))}</td>
        <td>{_status(d.get('status_final') or d.get('status'))}</td>
        <td>{_data(d.get('data_inicio') or d.get('periodo'))}</td>
      </tr>"""


def _paginar_historico(disciplinas: Iterable[dict]) -> list[list[dict]]:
    """Calcula o menor número seguro de folhas sem quebrar linhas.

    - até 8 componentes: uma folha completa (cadastro + tabela + validação);
    - em duas ou mais folhas: primeira e última comportam até 8 linhas;
    - folhas intermediárias comportam até 12 linhas.

    A distribuição é balanceada, mas respeita a capacidade reservada do início e
    do fechamento. Assim 16 componentes ficam 8+8 e 40 ficam 8+12+12+8.
    """
    itens = [dict(x) for x in disciplinas]
    n = len(itens)
    if n <= 8:
        return [itens]

    paginas = 2
    while n > 16 + 12 * max(0, paginas - 2):
        paginas += 1
    capacidades = [8] + ([12] * max(0, paginas - 2)) + [8]

    grupos: list[list[dict]] = []
    pos = 0
    restante = n
    for idx, cap in enumerate(capacidades):
        pags_restantes = len(capacidades) - idx
        capacidade_futura = sum(capacidades[idx + 1:])
        minimo_agora = max(1, restante - capacidade_futura)
        alvo = (restante + pags_restantes - 1) // pags_restantes
        qtd = min(cap, max(minimo_agora, alvo))
        grupos.append(itens[pos:pos + qtd])
        pos += qtd
        restante -= qtd
    return [g for g in grupos if g]


def build_history(aluno: dict, disciplinas: list[dict], codigo: str, qr_code: str, hash_documento: str,
                  ira=None, carga_total=None, carga_aprovada=None, total_aprovadas=None, ano_referencia=None) -> str:
    disciplinas = [dict(x) for x in (disciplinas or [])]
    paginas_dados = _paginar_historico(disciplinas)
    total_paginas = len(paginas_dados)

    if carga_total is None:
        carga_total = sum(int(d.get("carga_horaria") or d.get("carga") or 80) for d in disciplinas)
    if total_aprovadas is None:
        total_aprovadas = sum(1 for d in disciplinas if str(d.get("status_final") or d.get("status") or "").strip().casefold() in {"aprovado", "aprovada"})
    if carga_aprovada is None:
        carga_aprovada = sum(
            int(d.get("carga_horaria") or d.get("carga") or 80)
            for d in disciplinas
            if str(d.get("status_final") or d.get("status") or "").strip().casefold() in {"aprovado", "aprovada"}
        )
    if ira in (None, "", "N/I", "NI"):
        notas, pesos = [], []
        for d in disciplinas:
            m = d.get("media_final") if d.get("media_final") is not None else d.get("nota_final")
            if m is not None:
                try:
                    notas.append(float(m))
                    pesos.append(int(d.get("carga_horaria") or d.get("carga") or 80))
                except Exception:
                    pass
        ira = (sum(n * p for n, p in zip(notas, pesos)) / sum(pesos)) if pesos and sum(pesos) else None

    pai = str(aluno.get("nome_pai") or "").strip()
    mae = str(aluno.get("nome_mae") or "").strip()
    filiacao = " e ".join(x for x in (pai, mae) if x) or "N/I"
    ano_referencia = ano_referencia or datetime.now().year

    sheets = []
    for idx, chunk in enumerate(paginas_dados):
        first = idx == 0
        last = idx == total_paginas - 1
        if first:
            cadastro = f"""
            <div class="section-title">Dados do estudante</div>
            <table class="info-table history-identity">
              <tr><th>Aluno</th><td>{_txt(aluno.get('nome'))}</td><th>RA</th><td>{_txt(aluno.get('ra'))}</td></tr>
              <tr><th>CPF</th><td>{_txt(aluno.get('cpf_formatado') or aluno.get('cpf'))}</td><th>RG</th><td>{_txt(aluno.get('rg'))}</td></tr>
              <tr><th>Nascimento</th><td>{_data(aluno.get('data_nascimento'))}</td><th>Nacionalidade</th><td>{_txt(aluno.get('nacionalidade'), 'Brasileira')}</td></tr>
              <tr><th>Naturalidade</th><td>{_txt(aluno.get('naturalidade'))}</td><th>Estado civil</th><td>{_txt(aluno.get('estado_civil'))}</td></tr>
              <tr><th>Filiação</th><td colspan="3">{_txt(filiacao)}</td></tr>
              <tr><th>Unidade Curricular</th><td>{_txt(aluno.get('curso_referencia'), 'Disciplinas / Unidades Curriculares')}</td><th>Ano</th><td>{_txt(ano_referencia)}</td></tr>
              <tr><th>Instituição</th><td colspan="3">Grupo Educacional Unificado / Faculdade do Centro Oeste Paulista LTDA - Certificadora</td></tr>
            </table>"""
        else:
            cadastro = f"<div class='history-carry'><b>Aluno:</b> {_txt(aluno.get('nome'))} &nbsp;&nbsp; <b>RA:</b> {_txt(aluno.get('ra'))}</div>"

        fechamento = ""
        if last:
            fechamento = f"""
            <div class="summary-grid">
              <div>IRA<b>{_num(ira) if ira is not None else 'N/I'}</b></div>
              <div>Disciplinas<b>{len(disciplinas)}</b></div>
              <div>Aprovadas<b>{total_aprovadas}</b></div>
              <div>Carga total<b>{int(carga_total or 0)}h</b></div>
            </div>
            <div class="compact" style="margin-top:2.2mm"><b>Carga horária integralizada:</b> {int(carga_aprovada or 0)}h. Certificação formalizada pela <b>FACOP CERTIFICADORA – Faculdade do Centro Oeste Paulista LTDA</b>, vinculada aos respectivos registros autenticados.</div>
            <div class="compact" style="margin-top:1.5mm">A FACOP CERTIFICADORA encontra-se identificada neste documento por seus dados cadastrais e institucionais; a certificação vinculada ao histórico é formalizada nos registros da certificadora e submetida aos mecanismos oficiais de regulação, supervisão e verificação institucional aplicáveis ao sistema federal de ensino, conforme a situação regulatória vigente.</div>
            {_validation(codigo, qr_code, hash_documento)}
            """

        linhas = "".join(_history_row(d) for d in chunk)
        sheets.append(f"""
        <section class="sheet"><div class="content">
          {_header('HISTÓRICO ESCOLAR', 'Registro de componentes curriculares')}
          {cadastro}
          <div class="section-title">Componentes curriculares</div>
          <table class="history-table">
            <colgroup><col style="width:30%"><col style="width:7%"><col style="width:25%"><col style="width:8%"><col style="width:9%"><col style="width:11%"><col style="width:10%"></colgroup>
            <thead><tr><th>Componente Curricular</th><th>CH</th><th>Docente</th><th>Média</th><th>Frequência</th><th>Situação</th><th>Início</th></tr></thead>
            <tbody>{linhas}</tbody>
          </table>
          {fechamento}
        </div>{_footer(codigo, idx + 1, total_paginas)}</section>
        """)
    return _document_start(codigo) + "".join(sheets) + _document_end()


def _refs(value: str) -> list[str]:
    value = str(value or "").replace("<br />", "<br>").replace("<br/>", "<br>")
    parts = [re.sub(r"<[^>]+>", "", x).strip() for x in re.split(r"<br>|\n", value, flags=re.I)]
    return [p for p in parts if p]


def _normalizar_topicos(topicos) -> list[str]:
    if isinstance(topicos, str):
        return [x.strip(" •-\t") for x in topicos.splitlines() if x.strip()]
    if isinstance(topicos, (list, tuple)):
        return [str(x).strip() for x in topicos if str(x).strip()]
    return []


def _slot_unidade(indice: int, unidade: dict) -> str:
    titulo = str(unidade.get("titulo") or f"UNIDADE {indice}").strip()
    topicos = _normalizar_topicos(unidade.get("topicos"))
    tamanho = len(titulo) + sum(len(x) for x in topicos)
    densidade = ""
    if len(topicos) >= 12 or tamanho > 1050:
        densidade = "micro"
    elif len(topicos) >= 11 or tamanho > 850:
        densidade = "tight"
    elif len(topicos) >= 9 or tamanho > 650:
        densidade = "dense"
    lis = "".join(f"<li>{escape(str(t))}</li>" for t in topicos)
    return f"<div class='unit-slot {densidade}'><h3>{escape(titulo)}</h3><ul>{lis}</ul></div>"


def _units_grid(slots: list[str]) -> str:
    cells = list(slots[:12])
    while len(cells) < 12:
        cells.append("<div class='unit-slot empty' aria-hidden='true'></div>")
    return "<div class='units-grid'>" + "".join(cells) + "</div>"


def build_plan(*, disciplina: str, codigo: str, hash_documento: str, carga_horaria: str,
               modalidade: str, docente: str, data_formatada: str, qr_code: str,
               objetivo_geral: str = "", objetivos_especificos: str = "", ementa: str = "",
               habilidades: str = "", pre_requisitos: str = "", enquadramento_curricular: str = "",
               metodologia_html: str = "", avaliacao_html: str = "", bibliografia_basica: str = "",
               bibliografia_complementar: str = "", unidades: list[dict] | None = None,
               numero_unidades: int = 8) -> str:
    try:
        numero_unidades = max(8, min(12, int(numero_unidades or 8)))
    except Exception:
        numero_unidades = 8

    unidades = list(unidades or [])[:numero_unidades]
    slots: list[str] = []
    for i in range(12):
        if i < len(unidades):
            u = unidades[i] if isinstance(unidades[i], dict) else {}
            slots.append(_slot_unidade(i + 1, u))
        else:
            slots.append("<div class='unit-slot empty' aria-hidden='true'></div>")

    refs_bas = _refs(bibliografia_basica)
    refs_comp = _refs(bibliografia_complementar)
    refs_bas_html = "".join(f"<div>{escape(x)}</div>" for x in refs_bas)
    refs_comp_html = "".join(f"<div>{escape(x)}</div>" for x in refs_comp)

    total = 4
    p1 = f"""
    <section class="sheet"><div class="content">
      {_header('PLANO DE ENSINO', disciplina)}
      <table class="info-table">
        <tr><th>Instituição</th><td>Grupo Educacional Unificado / Faculdade do Centro Oeste Paulista LTDA - Certificadora</td></tr>
        <tr><th>Identificação institucional</th><td>SIGEU Educacional / FACOP Certificadora</td></tr>
        <tr><th>Pré-requisito</th><td>{_txt(pre_requisitos, 'Nenhum')}</td></tr>
        <tr><th>Nome da Disciplina</th><td>{_txt(disciplina)}</td></tr>
        <tr><th>Coordenação</th><td>Coordenação Acadêmica - SIGEU Educacional / FACOP Certificadora</td></tr>
        <tr><th>Núcleo</th><td>{_txt(enquadramento_curricular, 'Unidade Curricular / Formação Acadêmica')}</td></tr>
        <tr><th>Oferta</th><td>Oferta acadêmica vinculada ao SIGEU Educacional</td></tr>
        <tr><th>Carga Horária</th><td>{_txt(carga_horaria)}</td></tr>
        <tr><th>Modelo</th><td>{_txt(modalidade, 'EaD')}</td></tr>
        <tr><th>Docente</th><td>{_txt(docente)}</td></tr>
      </table>
      <div class="section-title">Ementa</div><div class="bodytext">{_plain_or_html(ementa)}</div>
      <div class="section-title">Objetivos</div><div class="compact"><b>Geral:</b> {_plain_or_html(objetivo_geral)}<br><b>Específicos:</b> {_plain_or_html(objetivos_especificos)}</div>
      <div class="section-title">Habilidades e Competências</div><div class="compact">{_plain_or_html(habilidades)}</div>
    </div>{_footer(codigo, 1, total)}</section>"""

    p2 = f"""
    <section class="sheet"><div class="content">
      {_header('PLANO DE ENSINO', disciplina)}
      <div class="section-title">Plano de Ensino - Unidades 1 a 12</div>
      {_units_grid(slots)}
    </div>{_footer(codigo, 2, total)}</section>"""

    p3 = f"""
    <section class="sheet"><div class="content">
      {_header('PLANO DE ENSINO', disciplina)}
      <div class="section-title">Metodologia</div><div class="rich compact">{_strip_inline_style(metodologia_html)}</div>
      <div class="section-title">Avaliação da Aprendizagem</div><div class="rich compact">{_strip_inline_style(avaliacao_html)}</div>
    </div>{_footer(codigo, 3, total)}</section>"""

    p4 = f"""
    <section class="sheet"><div class="content">
      {_header('PLANO DE ENSINO', disciplina)}
      <div class="section-title">Referências Básicas</div><div class="ref-list">{refs_bas_html or '<div>N/I</div>'}</div>
      <div class="section-title">Referências Complementares</div><div class="ref-list">{refs_comp_html or '<div>N/I</div>'}</div>
      <div class="section-title">Validação Institucional</div>
      <div class="compact">Plano emitido em {_txt(data_formatada)}. Certificação formalizada pela <b>FACOP CERTIFICADORA – Faculdade do Centro Oeste Paulista LTDA</b>, vinculada aos respectivos registros autenticados. Código de autenticação, QR Code e hash permitem a conferência eletrônica do documento.</div>
      {_validation(codigo, qr_code, hash_documento)}
    </div>{_footer(codigo, 4, total)}</section>"""

    return _document_start(codigo) + p1 + p2 + p3 + p4 + _document_end()
