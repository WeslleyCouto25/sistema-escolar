"""Blueprint independente das páginas comerciais SIGEU.
O projeto Flask importa e registra `solucoes_digitais_bp` apenas uma vez.
Preserva rotas existentes e acrescenta categoria /servicos e cinco hubs.
"""
from pathlib import Path
import os
from flask import Blueprint, render_template, send_from_directory, abort, Response, redirect, request
from xml.sax.saxutils import escape as xml_escape

WHATSAPP_NUMERO = os.getenv("SIGEU_WHATSAPP_NUMERO", "5521990020262")  # Número legado do pacote anterior. Confirme ou configure no Render.
DOMINIO = "https://sigeueducacional.com.br"
BASE = Path(__file__).resolve().parent / "templates" / "seo_public" / "solucoes_digitais"
CATEGORIAS = frozenset({"sites", "apps", "softwares", "gerais", "servicos", "jogos"})
CATEGORIAS_PUBLICAS = ("sites", "apps", "softwares", "servicos", "jogos")
solucoes_digitais_bp = Blueprint("sigeu_solucoes_digitais", __name__)

@solucoes_digitais_bp.get("/solucoes-digitais")
def pagina_comercial_geral():
    return render_template("seo_public/solucoes_digitais/index.html", whatsapp_num=WHATSAPP_NUMERO)

@solucoes_digitais_bp.get("/solucoes-digitais/<categoria>")
def pagina_categoria(categoria):
    if categoria == "gerais":
        return redirect("/solucoes-digitais/servicos", code=301)
    if categoria not in CATEGORIAS_PUBLICAS:
        abort(404)
    pagina = BASE / categoria / "index.html"
    if not pagina.is_file():
        abort(404)
    return render_template(f"seo_public/solucoes_digitais/{categoria}/index.html", whatsapp_num=WHATSAPP_NUMERO)

@solucoes_digitais_bp.get("/solucoes-digitais/<categoria>/<slug>")
def pagina_digital(categoria, slug):
    if categoria == "gerais":
        # Alias histórico, sem gerar duplicatas indexáveis.
        return redirect(f"/solucoes-digitais/servicos/{slug}", code=301)
    if categoria not in CATEGORIAS_PUBLICAS or not slug or not slug.replace("-", "").isalnum() or slug == "index":
        abort(404)
    pagina = BASE / categoria / (slug + ".html")
    if not pagina.is_file():
        abort(404)
    return render_template(f"seo_public/solucoes_digitais/{categoria}/{slug}.html", whatsapp_num=WHATSAPP_NUMERO)

@solucoes_digitais_bp.get("/solucoes-digitais/assets/<path:arquivo>")
def recursos_digitais(arquivo):
    return send_from_directory(str(BASE / "assets"), arquivo, max_age=604800)

@solucoes_digitais_bp.get("/solucoes-digitais/sitemap.xml")
def sitemap_solucoes_digitais():
    # O sitemap acadêmico de /sitemap.xml permanece intacto.
    urls = [f"{DOMINIO}/solucoes-digitais"]
    for categoria in CATEGORIAS_PUBLICAS:
        pasta = BASE / categoria
        if not pasta.is_dir():
            continue
        if (pasta / "index.html").is_file():
            urls.append(f"{DOMINIO}/solucoes-digitais/{categoria}")
        urls.extend(f"{DOMINIO}/solucoes-digitais/{categoria}/{pagina.stem}"
                    for pagina in sorted(pasta.glob("*.html"))
                    if not pagina.name.startswith("_") and pagina.stem != "index")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += ''.join(f'  <url><loc>{xml_escape(url)}</loc></url>\n' for url in urls)
    xml += '</urlset>'
    return Response(xml, mimetype="application/xml")

@solucoes_digitais_bp.get("/google1b74013caeb4c852.html")
def verificar_google_search_console():
    return Response("google-site-verification: google1b74013caeb4c852.html", mimetype="text/html")


@solucoes_digitais_bp.after_app_request
def acrescentar_sitemap_comercial_no_robots(response):
    """Preserva /robots.txt existente do app.py e anuncia o sitemap das 250 páginas."""
    if request.path != "/robots.txt" or response.status_code != 200 or response.direct_passthrough:
        return response
    linha = f"Sitemap: {DOMINIO}/solucoes-digitais/sitemap.xml"
    conteudo = response.get_data(as_text=True)
    if linha not in conteudo:
        response.set_data(conteudo.rstrip("\n") + "\n" + linha + "\n")
    return response


# Rebranding publico das paginas legadas de Solucoes Digitais.
# Mantem URLs, slugs, scripts e recursos estaticos; altera somente HTML textual.
@solucoes_digitais_bp.after_app_request
def padronizar_marca_sigeduc(response):
    if (not request.path.startswith("/solucoes-digitais")
            or response.status_code != 200
            or response.direct_passthrough
            or response.mimetype != "text/html"):
        return response
    html = response.get_data(as_text=True)
    substituicoes = (
        ("Instituto SIGEU Educacional", "Instituto SIGEduc"),
        ("Instituto SIGEU", "Instituto SIGEduc"),
        ("SiGEU Educacional", "Instituto SIGEduc"),
        ("SIGEU Educacional", "Instituto SIGEduc"),
        ("SIGEU EDUCACIONAL", "Instituto SIGEduc"),
        ("SiGEU Educação", "Instituto SIGEduc"),
        ("SIGEU Educação", "Instituto SIGEduc"),
        ("SIGEU Soluções Digitais", "Instituto SIGEduc Soluções Digitais"),
    )
    for antigo, novo in substituicoes:
        html = html.replace(antigo, novo)
    import re
    html = re.sub(r"\bSIGEU\b", "SIGEduc", html)
    html = re.sub(r"\bSiGEU\b", "SIGEduc", html)
    response.set_data(html)
    response.headers.pop("ETag", None)
    return response
