"""Padrão universal do conteúdo interno do SIGEU.

Catálogos autorais completos têm precedência. Disciplinas antigas usam seus próprios
materiais cadastrados, sem textos fictícios nem novas exigências indisponíveis.
"""
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse
import json
import re
import course_editorial as ce

def key(value):
    return re.sub(r"[^a-z0-9]+", " ", ce.normalize(value)).strip()

def is_transito(name):
    k = key(name)
    return "psicologia" in k and "transito" in k

def is_positiva(name):
    k = key(name)
    return "psicologia positiva" in k

@lru_cache(maxsize=1)
def transit_material():
    p = Path(__file__).resolve().parent / "course_content" / "transito_source.json"
    return json.loads(p.read_text(encoding="utf-8"))

def block(kind, id, title, **kwargs):
    return dict(type=kind, id=id, title=title, required=False, **kwargs)

def usable_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if value.startswith("/") and not value.startswith("//"):
        return value
    if parsed.scheme in ("http","https") and parsed.netloc:
        # Páginas iniciais cadastradas provisoriamente não são vídeos.
        if parsed.hostname in ("www.sigeu.com","sigeu.com","sigeueducacional.com.br","www.sigeueducacional.com.br") and parsed.path in ("","/"):
            return ""
        return value
    return ""

def media_info(value):
    url = usable_url(value)
    if not url:
        return "", ""
    p = urlparse(url)
    host = (p.hostname or "").lower()
    path = p.path.lower()
    if path.endswith((".mp4",".webm",".mov",".m4v",".ogv")):
        return url, "file"
    if host in ("youtu.be","www.youtu.be"):
        video_id = p.path.strip("/").split("/")[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}",video_id):
            return "https://www.youtube.com/embed/" + video_id, "iframe"
    if host in ("youtube.com","www.youtube.com","m.youtube.com"):
        from urllib.parse import parse_qs
        video_id = parse_qs(p.query).get("v",[""])[0] if p.path=="/watch" else p.path.split("/")[-1]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}",video_id):
            return "https://www.youtube.com/embed/" + video_id, "iframe"
    if host == "drive.google.com":
        return re.sub(r"/(?:view|edit)(?:\?.*)?$","/preview",url), "iframe"
    return url, "link"

TRANSIT_ACTIVITIES = [
    block("graph","associacao","Associe os processos cognitivos",track=True,pairs=[
        ["Sensação","Detecção de estímulos sensoriais"],
        ["Percepção","Organização e interpretação da informação"],
        ["Atenção seletiva","Priorização de informações relevantes"],
        ["Consciência situacional","Percepção, compreensão e antecipação"]]),
    block("classify","classificacao","Oficina de classificação: decisões e aprendizagem",track=True,
        categories=["Memória de trabalho","Aprendizagem social","Hábito","Autorregulação"],
        items=[["Manter temporariamente uma instrução recente",0],["Observar uma conduta de outro usuário",1],["Repetir uma sequência familiar",2],["Monitorar e ajustar o próprio comportamento",3]]),
    block("scenario","casos","Análise de situações: fatores humanos",track=True,cases=[
        dict(prompt="Um condutor percebe sinais de sonolência. Qual resposta reduz a exposição ao risco?",options=["Continuar até o destino","Interromper a condução em local seguro e descansar","Usar o telefone para manter a atenção"],answer=1,feedback="A fadiga compromete vigilância; interromper a condução reduz a exposição."),
        dict(prompt="Uma via apresenta sinalização confusa e conflitos recorrentes. Qual análise é adequada?",options=["Investigar o desenho da via e os comportamentos","Atribuir todos os conflitos à personalidade","Ignorar a infraestrutura"],answer=0,feedback="Fatores humanos e ergonomia exigem examinar pessoa, tarefa e ambiente.")]),
    block("order","sequencia","Planeje uma ação educativa",track=True,steps=[
        "Identificar o problema e o público","Investigar fatores de risco e barreiras","Definir objetivos e ações de prevenção","Implementar a ação","Avaliar resultados e revisar"])
]

TRANSIT_DECKS = [
    [[["Sistema sociotécnico","Usuários, veículos, infraestrutura e regras interagem."],["Sensopercepção","Detectar não é o mesmo que interpretar."],["Atenção","Priorizar informações relevantes exige recursos cognitivos."]],
     [["Consciência situacional","Perceber, compreender e projetar acontecimentos."],["Contexto","A visibilidade e a sinalização influenciam decisões."],["Prevenção","Examine pessoa, tarefa e ambiente."]]],
    [[["Memória de trabalho","Mantém informações temporárias durante a ação."],["Memória de longo prazo","Reúne conhecimentos e procedimentos aprendidos."],["Esquemas","Expectativas orientam a interpretação, mas podem falhar."]],
     [["Aprendizagem social","A observação contribui para a aprendizagem."],["Hábitos","A automatização pode facilitar ações ou perpetuar riscos."],["Autorregulação","Monitore escolhas e ajuste estratégias."]]],
    [[["Emoções","Podem influenciar atenção e avaliação de risco."],["Estresse","A carga mental modifica a qualidade do processamento."],["Fadiga","Sono insuficiente compromete a vigilância."]],
     [["Distração","Demandas concorrentes disputam recursos atencionais."],["Ergonomia","Adapte a tarefa e o ambiente às capacidades humanas."],["Prevenção","Examine fatores individuais e sistêmicos."]]],
    [[["Cidadania","Mobilidade envolve direitos e responsabilidades."],["Inclusão","Barreiras podem limitar a participação dos usuários."],["Ética","Respeite os limites profissionais e a confidencialidade."]],
     [["Problema","Identifique riscos, população e contexto."],["Intervenção","Defina estratégias de educação e prevenção."],["Avaliação","Examine indicadores, acesso e resultados."]]]
]

TRANSIT_REFS = [
    "ROZESTRATEN, Reinier J. A. Psicologia do trânsito: conceitos e processos básicos. São Paulo: EPU, 1988.",
    "ENDSLEY, Mica R. Toward a theory of situation awareness in dynamic systems. Human Factors, v. 37, n. 1, p. 32–64, 1995.",
    "BANDURA, Albert. Social learning theory. Englewood Cliffs: Prentice Hall, 1977.",
    "AJZEN, Icek. The theory of planned behavior. Organizational Behavior and Human Decision Processes, v. 50, n. 2, p. 179–211, 1991.",
    "KAHNEMAN, Daniel. Thinking, fast and slow. New York: Farrar, Straus and Giroux, 2011.",
    "Material-base: Psicologia e Cognição no Trânsito. Cadernos de Formação, edição 2026. Quatro apostilas institucionais."
]

def resolve_course(name, chapters):
    """Sempre retorna um percurso para os capítulos existentes; sem inventar disciplinas."""
    chapters = list(chapters)
    curated = ce.get_course(name)
    if curated is None and is_positiva(name):
        curated = ce.get_course("Fundamentos da Psicologia Positiva")
    if curated and len(chapters) == len(curated["units"]):
        return {**curated, "mode":"full"}
    transit = is_transito(name) and len(chapters)==4
    source = transit_material() if transit else None
    units = []
    for i, chapter in enumerate(chapters,1):
        title = str(chapter.get("titulo") or f"Unidade {i}")
        url, kind = media_info(chapter.get("video_url"))
        video = block("existing_video","video-principal","Videoaula principal",url=url,media_kind=kind,track=bool(url))
        book = block("existing_book","livro","Livro e material didático",url=usable_url(chapter.get("pdf_url")))
        if transit:
            sections = source["units"][i-1]
            readings = [block("source_reading",f"texto-{j+1}",s["title"],paragraphs=s["paragraphs"],source=source["source"],track=True) for j,s in enumerate(sections)]
            decks = [block("slides",f"slides-{j+1}",f"Apresentação complementar {j+1}",frames=frames) for j,frames in enumerate(TRANSIT_DECKS[i-1])]
            activity = TRANSIT_ACTIVITIES[i-1]
            audio = block("audio","audio","Síntese em áudio")
            references = block("references","referencias","Referências e material-base")
            layouts = [
              [*readings[:3],video,*readings[3:5],decks[0],activity,*readings[5:7],book,decks[1],*readings[7:],audio,references],
              [video,*readings[:2],activity,*readings[2:5],decks[0],book,*readings[5:7],audio,decks[1],*readings[7:],references],
              [*readings[:2],decks[0],*readings[2:4],video,*readings[4:6],activity,book,*readings[6:],decks[1],audio,references],
              [book,*readings[:3],video,decks[0],*readings[3:6],activity,*readings[6:8],decks[1],*readings[8:],audio,references]
            ]
            blocks = layouts[i-1]
            summary = ". ".join(frame[1] for deck in TRANSIT_DECKS[i-1] for frame in deck)
            subtitle = "Leitura das apostilas institucionais, atividades e aplicações"
        else:
            # A interface universal está ativa, mas material ausente não se torna conteúdo fictício.
            blocks = [
                video,book,
                block("pending_content","texto","Conteúdo acadêmico expandido"),
                block("pending_complement","complemento-1","Material audiovisual complementar 1"),
                block("reflection","notas","Caderno de estudo",prompt=f"Registre os conceitos centrais de {title} com base no material cadastrado."),
                block("study_check","revisao","Revisão de aprendizagem",track=True,prompts=["Identifique os principais conceitos do capítulo.","Explique uma aplicação do conteúdo.","Revise as dúvidas antes da avaliação."]),
                block("pending_complement","complemento-2","Material audiovisual complementar 2"),
            ]
            summary = ""
            subtitle = "Percurso de estudo com os materiais cadastrados pela instituição"
        units.append(dict(number=i,title=title,subtitle=subtitle,video=url,blocks=blocks,audio_summary=summary))
    return dict(name=name,slug="transito" if transit else "material_cadastrado",mode="source" if transit else "materials",version="transito-source-1" if transit else "materiais-1",units=units,references=TRANSIT_REFS if transit else [])
