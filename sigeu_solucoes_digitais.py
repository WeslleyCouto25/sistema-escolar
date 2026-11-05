"""SIGEU Soluções Digitais — blueprint único para Flask.

Instalação: copiar este arquivo ao lado do app.py e copiar a pasta
`templates/seo_public/solucoes_digitais/` sem substituir os HTMLs existentes.
Em app.py, ANTES de iniciar a aplicação e somente uma vez:

    from sigeu_solucoes_digitais import solucoes_digitais_bp
    app.register_blueprint(solucoes_digitais_bp)

Não registrar os blueprints antigos dos lotes 01 e 02 simultaneamente.
A variável WHATSAPP_NUMERO abaixo alimenta as 50 páginas e a landing geral.
"""
from pathlib import Path
from datetime import datetime, timezone
from flask import Blueprint, render_template, send_from_directory, abort, Response
from xml.sax.saxutils import escape as xml_escape

WHATSAPP_NUMERO = "5521990020262"  # Editar aqui, sem espaços: Brasil(55) + DDD(21) + número.
DOMINIO = "https://sigeueducacional.com.br"
BASE = Path(__file__).resolve().parent / "templates" / "seo_public" / "solucoes_digitais"
CATEGORIAS = frozenset({"sites", "apps", "softwares", "gerais", "jogos"})
solucoes_digitais_bp = Blueprint("sigeu_solucoes_digitais", __name__)


@solucoes_digitais_bp.get("/solucoes-digitais")
def pagina_comercial_geral():
    return render_template("seo_public/solucoes_digitais/index.html", whatsapp_num=WHATSAPP_NUMERO)


@solucoes_digitais_bp.get("/solucoes-digitais/<categoria>/<slug>")
def pagina_digital(categoria, slug):
    """Sem duplicar rotas: futuras páginas entram nas pastas das cinco categorias."""
    if categoria not in CATEGORIAS or not slug or not slug.replace("-", "").isalnum():
        abort(404)
    pagina = BASE / categoria / (slug + ".html")
    if not pagina.is_file():
        abort(404)
    return render_template(f"seo_public/solucoes_digitais/{categoria}/{slug}.html", whatsapp_num=WHATSAPP_NUMERO)


@solucoes_digitais_bp.get("/solucoes-digitais/assets/<path:arquivo>")
def recursos_digitais(arquivo):
    """CSS, JS e SVG leves; recurso servido sem mover arquivos para static/."""
    return send_from_directory(str(BASE / "assets"), arquivo, max_age=604800)


@solucoes_digitais_bp.get("/solucoes-digitais/sitemap.xml")
def sitemap_solucoes_digitais():
    """Sitemap ADICIONAL, sem sobrescrever o sitemap.xml já existente."""
    urls = [f"{DOMINIO}/solucoes-digitais"]
    for categoria in sorted(CATEGORIAS):
        pasta = BASE / categoria
        if pasta.is_dir():
            urls.extend(f"{DOMINIO}/solucoes-digitais/{categoria}/{pagina.stem}"
                        for pagina in sorted(pasta.glob("*.html"))
                        if not pagina.name.startswith("_") and pagina.stem != "index")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += ''.join(f"  <url><loc>{xml_escape(url)}</loc></url>\n" for url in urls)
    xml += '</urlset>'
    return Response(xml, mimetype="application/xml")


@solucoes_digitais_bp.get("/google1b74013caeb4c852.html")
def verificar_google_search_console():
    """Verificação URL-prefix por arquivo HTML no endereço exato da raiz."""
    # Texto EXATO informado pelo usuário; preservar conteúdo e nome do arquivo.
    return Response("google-site-verification: google1b74013caeb4c852.html", mimetype="text/html")
