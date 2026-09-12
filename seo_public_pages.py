from flask import Blueprint, render_template, abort, Response

seo_pages_bp = Blueprint("seo_pages", __name__)
SEO_PAGES = {'servicos-academicos': 'seo_public/servicos-academicos.html', 'servicos-de-software': 'seo_public/servicos-de-software.html', 'arquitetura-e-projetos': 'seo_public/arquitetura-e-projetos.html', 'pos-graduacao-e-pesquisa': 'seo_public/pos-graduacao-e-pesquisa.html', 'orientacao-tcc': 'seo_public/orientacao-tcc.html', 'formatacao-abnt': 'seo_public/formatacao-abnt.html', 'revisao-tcc-artigo': 'seo_public/revisao-tcc-artigo.html', 'apoio-artigo-cientifico': 'seo_public/apoio-artigo-cientifico.html', 'projeto-de-pesquisa': 'seo_public/projeto-de-pesquisa.html', 'pre-projeto-mestrado-doutorado': 'seo_public/pre-projeto-mestrado-doutorado.html', 'slides-apresentacao-tcc': 'seo_public/slides-apresentacao-tcc.html', 'relatorio-estagio-supervisionado': 'seo_public/relatorio-estagio-supervisionado.html', 'portfolio-academico': 'seo_public/portfolio-academico.html', 'projeto-integrador': 'seo_public/projeto-integrador.html', 'pim-projeto-integrado-multidisciplinar': 'seo_public/pim-projeto-integrado-multidisciplinar.html', 'monografia-pos-graduacao': 'seo_public/monografia-pos-graduacao.html', 'metodologia-cientifica': 'seo_public/metodologia-cientifica.html', 'revisao-sistematica-prisma': 'seo_public/revisao-sistematica-prisma.html', 'correcao-pos-banca': 'seo_public/correcao-pos-banca.html', 'projeto-academico-arquitetura': 'seo_public/projeto-academico-arquitetura.html', 'projeto-academico-engenharia': 'seo_public/projeto-academico-engenharia.html', 'projeto-academico-computacao': 'seo_public/projeto-academico-computacao.html', 'desenvolvimento-sistema-academico': 'seo_public/desenvolvimento-sistema-academico.html', 'software-sob-medida': 'seo_public/software-sob-medida.html'}

@seo_pages_bp.route("/<slug>")
def pagina_seo(slug):
    template = SEO_PAGES.get(slug)
    if not template:
        abort(404)
    return render_template(template)

@seo_pages_bp.route("/sitemap-servicos.xml")
def sitemap_servicos():
    base = "https://sigeueducacional.com.br"
    urls = "\n".join(f"  <url><loc>{base}/{slug}</loc><changefreq>monthly</changefreq><priority>0.8</priority></url>" for slug in SEO_PAGES)
    xml = f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n{urls}\n</urlset>"
    return Response(xml, mimetype="application/xml")
