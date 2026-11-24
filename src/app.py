# Código de BIU - Assistente Financeiro

import atexit
import json
import re
import shutil
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime
from pathlib import Path

import requests
import streamlit as st

try:  # APIs do Streamlit usadas para detectar o fim da sessão
    from streamlit.runtime import Runtime
    from streamlit.runtime.scriptrunner import get_script_run_ctx
except Exception:  # versão sem essas APIs: o vigia fica desativado
    Runtime = None
    get_script_run_ctx = None

# ==================== CONFIGURAÇÃO ====================
MODELO = "gemini-2.5-flash"
GEMINI_URL = "https://googleapis.com" + MODELO + ":generateContent"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PERFIS_DIR = DATA_DIR / "perfis"
PRODUTOS_PATH = DATA_DIR / "produtos_financeiros.json"
PERFIL_BASE_PATH = DATA_DIR / "perfil_investidor.json"  # perfil base (exemplo)

st.set_page_config(page_title="BIU, o seu Assistente Financeiro", page_icon="💰")


# ==================== UTILITÁRIOS ====================
def norm(texto):
    """Minúsculas e sem acentos, para comparar respostas."""
    s = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return s.lower().strip()


def slug(nome):
    s = re.sub(r"[^a-z0-9]+", "_", norm(nome)).strip("_")
    return s or "usuario"


def brl(valor):
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


# ==================== SESSÃO: PERFIL TEMPORÁRIO ====================
# O perfil cadastrado existe só enquanto a sessão durar. Ele é apagado:
#   1) quando a sessão do navegador termina (aba fechada ou página recarregada):
#      um vigia confere a cada poucos segundos se a sessão ainda está conectada;
#   2) quando o servidor é desligado (atexit);
#   3) na próxima vez que o servidor iniciar (sobras de queda ou encerramento forçado);
#   4) quando o usuário clica em "Apagar meu perfil agora".
INTERVALO_VIGIA = 2.0   # segundos entre verificações
TOLERANCIA_VIGIA = 8.0  # segundos desconectado antes de apagar (evita apagar numa oscilação de rede)


def _id_da_sessao_atual():
    try:
        ctx = get_script_run_ctx()
        return ctx.session_id if ctx else None
    except Exception:
        return None


def _vigiar_sessoes(ativas):
    """Thread de fundo: apaga a pasta de toda sessão que deixou de estar conectada."""
    inativas_desde = {}
    while True:
        time.sleep(INTERVALO_VIGIA)
        try:
            runtime = Runtime.instance()
        except Exception:
            continue
        agora = time.time()
        for pasta, sessao_id in list(ativas.items()):
            if sessao_id is None:
                continue
            try:
                conectada = runtime.is_active_session(sessao_id)
            except Exception:
                continue
            if conectada:
                inativas_desde.pop(pasta, None)
                continue
            desde = inativas_desde.setdefault(pasta, agora)
            if agora - desde >= TOLERANCIA_VIGIA:
                shutil.rmtree(pasta, ignore_errors=True)
                ativas.pop(pasta, None)
                inativas_desde.pop(pasta, None)


@st.cache_resource
def _registro_global():
    """Executa uma vez por servidor. Retorna {pasta do perfil: id da sessão dona}."""
    ativas = {}
    if PERFIS_DIR.exists():
        for pasta in PERFIS_DIR.iterdir():
            if pasta.is_dir() and re.fullmatch(r".+-[0-9a-f]{8}", pasta.name):
                shutil.rmtree(pasta, ignore_errors=True)

    def limpar_tudo():
        for pasta in list(ativas):
            shutil.rmtree(pasta, ignore_errors=True)

    atexit.register(limpar_tudo)
    if Runtime is not None:
        threading.Thread(target=_vigiar_sessoes, args=(ativas,), daemon=True).start()
    return ativas


class SessaoPerfil:
    """Guarda a pasta do perfil desta sessão; ela é apagada quando a sessão termina."""

    def __init__(self):
        self.id = uuid.uuid4().hex[:8]
        self.pasta = None
        self._ativas = _registro_global()

    def registrar(self, pasta):
        self.pasta = pasta
        self._ativas[pasta] = _id_da_sessao_atual()

    def apagar(self):
        pasta, self.pasta = self.pasta, None
        if pasta is not None:
            shutil.rmtree(pasta, ignore_errors=True)
            self._ativas.pop(pasta, None)


# ==================== PERFIL: ARQUIVO ====================
def caminho_perfil(nome):
    sessao = st.session_state.sessao
    return PERFIS_DIR / f"{slug(nome)}-{sessao.id}" / "perfil_investidor.json"


def carregar_perfil(nome):
    caminho = caminho_perfil(nome)
    if not caminho.exists():
        return None
    try:
        with open(caminho, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def salvar_perfil(nome, perfil):
    caminho = caminho_perfil(nome)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(perfil, f, ensure_ascii=False, indent=2)
    st.session_state.sessao.registrar(caminho.parent)
    return caminho


def carregar_perfil_base():
    """Perfil de exemplo (data/perfil_investidor.json). Nunca é sobrescrito."""
    try:
        with open(PERFIL_BASE_PATH, "r", encoding="utf-8-sig") as f:
            perfil = json.load(f)
        return perfil if isinstance(perfil, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def carregar_produtos():
    try:
        with open(PRODUTOS_PATH, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


# ==================== ONBOARDING: LEITURA DAS RESPOSTAS ====================
def p_texto(t):
    t = t.strip()
    if len(t) < 2:
        raise ValueError("Pode escrever um pouco mais?")
    return t[:120]


def p_idade(t):
    m = re.search(r"\d+", t)
    if not m:
        raise ValueError("Digite sua idade em números, por exemplo: 32.")
    v = int(m.group())
    if not 14 <= v <= 110:
        raise ValueError("Digite uma idade válida, entre 14 e 110 anos.")
    return v


def p_valor(t):
    s = re.sub(r"[^\d,.]", "", t)
    if not re.search(r"\d", s):
        raise ValueError("Não entendi o valor. Digite algo como 2500 ou R$ 2.500,00.")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        raise ValueError("Não entendi o valor. Digite algo como 2500 ou R$ 2.500,00.")


def p_valor_positivo(t):
    v = p_valor(t)
    if v <= 0:
        raise ValueError("O valor da meta precisa ser maior que zero.")
    return v


def p_sim_nao(t):
    n = norm(t).strip(" .!")
    if n in ("sim", "s", "aceito", "yes", "claro", "com certeza"):
        return True
    if n in ("nao", "n", "no", "nunca"):
        return False
    raise ValueError("Responda com *sim* ou *não*.")


def p_perfil(t):
    n = norm(t)
    if "conserv" in n:
        return "conservador"
    if "moder" in n:
        return "moderado"
    if "arroj" in n or "agress" in n:
        return "arrojado"
    raise ValueError("Escolha uma opção: *conservador*, *moderado* ou *arrojado*.")


def p_prazo(t):
    n = t.strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", n)
    if m:
        ano, mes = int(m.group(1)), int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{1,2})[-/](\d{4})", n)
        if not m:
            raise ValueError("Use o formato AAAA-MM, por exemplo: 2027-12.")
        mes, ano = int(m.group(1)), int(m.group(2))
    if not 1 <= mes <= 12:
        raise ValueError("O mês precisa estar entre 01 e 12.")
    hoje = date.today()
    if (ano, mes) < (hoje.year, hoje.month):
        raise ValueError("Esse prazo já passou. Informe uma data futura (AAAA-MM).")
    return f"{ano:04d}-{mes:02d}"


ETAPAS = [
    ("idade", "Quantos anos você tem?", p_idade),
    ("profissao", "Qual é a sua profissão?", p_texto),
    ("renda_mensal", "Qual é a sua renda mensal líquida (em R$)?", p_valor),
    (
        "objetivo_principal",
        "Qual é o seu principal objetivo financeiro hoje? "
        "(ex.: construir reserva de emergência, comprar um imóvel, viajar)",
        p_texto,
    ),
    (
        "patrimonio_total",
        "Quanto você tem guardado ou investido no total hoje (R$)? "
        "Se não tiver nada, digite 0.",
        p_valor,
    ),
    (
        "reserva_emergencia_atual",
        "Desse total, quanto está separado como reserva de emergência (R$)? "
        "Se nada, digite 0.",
        p_valor,
    ),
    (
        "aceita_risco",
        "Você aceita correr risco de perder parte do dinheiro em busca de mais "
        "rentabilidade? (sim/não)",
        p_sim_nao,
    ),
    (
        "perfil_investidor",
        "Como você se define como investidor: *conservador*, *moderado* ou *arrojado*?",
        p_perfil,
    ),
]

PERGUNTAS_META = {
    "meta_nome": (
        "Quer cadastrar uma meta financeira? Digite o nome dela "
        "(ex.: Entrada do apartamento) ou *nenhuma* para pular."
    ),
    "meta_valor": "Quanto você precisa juntar para essa meta (R$)?",
    "meta_prazo": "Até quando? Use o formato AAAA-MM (ex.: 2027-12).",
    "meta_outra": "Quer cadastrar outra meta? (sim/não)",
}

FASES = [chave for chave, _, _ in ETAPAS] + [
    "meta_nome",
    "meta_valor",
    "meta_prazo",
    "meta_outra",
]

PARSERS = {chave: parser for chave, _, parser in ETAPAS}


def pergunta_fase(fase):
    for chave, pergunta, _ in ETAPAS:
        if chave == fase:
            return pergunta
    return PERGUNTAS_META[fase]


def novo_onboarding():
    return {"i": 0, "dados": {"metas": []}, "meta_tmp": {}}


def processar_resposta(ob, texto):
    fase = FASES[ob["i"]]
    dados = ob["dados"]

    try:
        if fase in PARSERS:
            valor = PARSERS[fase](texto)

            if (
                fase == "reserva_emergencia_atual"
                and valor > dados.get("patrimonio_total", 0)
            ):
                raise ValueError(
                    "A reserva de emergência não pode ser maior que o total guardado "
                    f"({brl(dados['patrimonio_total'])}). Confere o valor?"
                )

            dados[fase] = valor

        elif fase == "meta_nome":
            if norm(texto).strip(" .!") in ("nenhuma", "nao", "n", "pular", "nenhum"):
                return [], True

            ob["meta_tmp"] = {"meta": p_texto(texto)}

        elif fase == "meta_valor":
            ob["meta_tmp"]["valor_necessario"] = p_valor_positivo(texto)

        elif fase == "meta_prazo":
            ob["meta_tmp"]["prazo"] = p_prazo(texto)
            dados["metas"].append(ob["meta_tmp"])
            ob["meta_tmp"] = {}

        elif fase == "meta_outra":
            if p_sim_nao(texto):
                ob["i"] = FASES.index("meta_nome")
                return ["Qual é o nome da próxima meta?"], False

            return [], True

    except ValueError as erro:
        return [str(erro), pergunta_fase(fase)], False

    proximo_indice = ob["i"] + 1

    if proximo_indice >= len(FASES):
        return [], True

    ob["i"] = proximo_indice
    return [pergunta_fase(FASES[ob["i"]])], False


def montar_perfil(nome, dados):
    return {
        "nome": nome,
        "idade": dados["idade"],
        "profissao": dados["profissao"],
        "renda_mensal": dados["renda_mensal"],
        "perfil_investidor": dados["perfil_investidor"],
        "objetivo_principal": dados["objetivo_principal"],
        "patrimonio_total": dados["patrimonio_total"],
        "reserva_emergencia_atual": dados["reserva_emergencia_atual"],
        "aceita_risco": dados["aceita_risco"],
        "metas": dados["metas"],
        "criado_em": datetime.now().isoformat(timespec="seconds"),
    }


def resumo_perfil(perfil):
    linhas = [
        "Pronto! Montei o seu perfil de investidor:",
        "",
        f"- **Nome:** {perfil['nome']}, {perfil['idade']} anos, {perfil['profissao']}",
        f"- **Renda mensal:** {brl(perfil['renda_mensal'])}",
        f"- **Perfil:** {perfil['perfil_investidor']} (aceita risco: "
        f"{'sim' if perfil['aceita_risco'] else 'não'})",
        f"- **Objetivo:** {perfil['objetivo_principal']}",
        f"- **Patrimônio:** {brl(perfil['patrimonio_total'])} "
        f"(reserva de emergência: {brl(perfil['reserva_emergencia_atual'])})",
    ]
    for m in perfil.get("metas", []):
        linhas.append(f"- **Meta:** {m['meta']} — {brl(m['valor_necessario'])} até {m['prazo']}")
    caminho = caminho_perfil(perfil["nome"]).relative_to(BASE_DIR)
    linhas += [
        "",
        f"Guardei o perfil temporariamente em `{caminho}`. Ele será **apagado quando você "
        "encerrar a sessão** (fechar ou recarregar a página) ou se clicar em *Apagar meu "
        "perfil agora*. Como posso te ajudar?",
    ]
    return "\n".join(linhas)


# ==================== CONTEXTO PARA O MODELO ====================
def meses_ate(prazo):
    ano, mes = map(int, prazo.split("-"))
    hoje = date.today()
    return (ano - hoje.year) * 12 + (mes - hoje.month)


def calculos_metas(perfil):
    """Contas feitas em Python (modelos pequenos erram aritmética)."""
    linhas = []
    renda = perfil.get("renda_mensal") or 0
    reserva = perfil.get("reserva_emergencia_atual") or 0
    for m in perfil.get("metas", []):
        nome_meta = m.get("meta", "?")
        try:
            valor = float(m["valor_necessario"])
            meses = meses_ate(m["prazo"])
        except (KeyError, ValueError, TypeError):
            continue
        eh_reserva = "reserva" in norm(nome_meta)
        acumulado = reserva if eh_reserva else 0
        restante = max(valor - acumulado, 0)
        if eh_reserva:
            base_txt = f"{brl(valor)} - {brl(acumulado)} (reserva atual) = {brl(restante)}"
        else:
            base_txt = (
                f"{brl(valor)} (valor total; o quanto já foi guardado para esta meta "
                "não foi informado)"
            )
        if meses <= 0:
            linhas.append(
                f"CÁLCULO — {nome_meta}: falta {base_txt}. O prazo já venceu ou é neste "
                "mês: peça ao usuário um novo prazo antes de sugerir um aporte mensal."
            )
            continue
        aporte = restante / meses
        pct = f" ({aporte / renda * 100:.1f}% da renda mensal)" if renda else ""
        linhas.append(
            f"CÁLCULO — {nome_meta}: falta {base_txt}; {meses} meses até {m['prazo']}; "
            f"aporte mensal estimado = {brl(restante)} ÷ {meses} = {brl(aporte)}{pct}. "
            "Conta simplificada, sem rendimentos nem inflação."
        )
    return linhas


def bloco_produtos():
    produtos = carregar_produtos()
    if not isinstance(produtos, list) or not produtos:
        return []
    linhas = ["", "PRODUTOS DE REFERÊNCIA (dados da base, não são taxas atuais):"]
    for p in produtos:
        if not isinstance(p, dict):
            continue
        linhas.append(
            f"- {p.get('nome')} | {p.get('categoria')} | risco {p.get('risco')} | "
            f"{p.get('rentabilidade')} | aporte mínimo {p.get('aporte_minimo')} | "
            f"indicado para: {p.get('indicado_para')}"
        )
    return linhas


def montar_contexto(perfil, base=False):
    linhas = []
    if perfil is None:
        linhas.append(
            "O usuário optou por NÃO cadastrar um perfil. Não há dados pessoais, renda, "
            "patrimônio nem metas registrados. Responda de forma geral e educativa; para "
            "personalizar, peça só os valores de que precisar (ex.: renda e despesas) e use "
            "apenas o que o usuário informar na conversa."
        )
        return "\n".join(linhas + bloco_produtos())
    if base:
        linhas.append(
            "ATENÇÃO: o usuário NÃO cadastrou um perfil. Os dados abaixo são de um PERFIL BASE "
            f"DE EXEMPLO (cliente fictício {perfil.get('nome', 'João Silva')}), usado só para "
            "demonstração. Deixe claro, quando usar esses números, que são do perfil de "
            "exemplo e não do usuário, e ofereça cadastrar o perfil real."
        )
    linhas += [
        f"CLIENTE: {perfil['nome']}, {perfil['idade']} anos, {perfil['profissao']}",
        f"RENDA MENSAL LÍQUIDA: {brl(perfil['renda_mensal'])}",
        f"PERFIL DE INVESTIDOR: {perfil['perfil_investidor']} | "
        f"ACEITA RISCO: {'sim' if perfil['aceita_risco'] else 'não'}",
        f"OBJETIVO PRINCIPAL: {perfil['objetivo_principal']}",
        f"PATRIMÔNIO TOTAL: {brl(perfil['patrimonio_total'])} | "
        f"RESERVA DE EMERGÊNCIA ATUAL: {brl(perfil['reserva_emergencia_atual'])}",
    ]
    for m in perfil.get("metas", []):
        try:
            meses = meses_ate(m["prazo"])
            falta = f"faltam {meses} meses" if meses > 0 else "prazo vencido ou no mês atual"
        except (KeyError, ValueError):
            falta = "prazo não informado"
        linhas.append(
            f"META: {m.get('meta', '?')} — {brl(m.get('valor_necessario', 0))} "
            f"até {m.get('prazo', '?')} ({falta})"
        )
    linhas += calculos_metas(perfil)
    linhas.append(
        "Não há transações nem histórico de atendimento registrados para este cliente."
    )

    linhas += bloco_produtos()
    return "\n".join(linhas)


SYSTEM_PROMPT = """Você é o Biu, assistente de educação financeira para brasileiros.
Responda SEMPRE em português do Brasil, mesmo que a pergunta venha em outro idioma.
Regras:
- O Biu não é consultor financeiro e não substitui orientação profissional.
- Biu só responde perguntas FINANCEIRAS e de cume FINANCEIRO.
- O perfil cadastrado vale só durante a sessão e é apagado quando ela termina (ao fechar ou recarregar a página); sem cadastro, nada é salvo.
- As linhas "CÁLCULO" do contexto já foram calculadas pela aplicação: use esses números como verdade e não refaça as contas.
- Use somente os dados do CONTEXTO DO CLIENTE e os valores que o usuário informar. Nunca invente renda, saldos, taxas ou rentabilidades.
- Se faltar dado, diga o que falta e peça só o necessário.
- Mostre contas de forma simples (ex.: R$ 15.000 - R$ 10.000 = R$ 5.000). Avise que a conta ignora rendimentos e inflação.
- Priorize reserva de emergência (3 a 6 meses de despesas essenciais) antes de investir. Não prometa ganhos nem cite taxas atuais; para isso, oriente consultar fontes oficiais.
- Nunca peça senhas ou códigos. Responda só sobre finanças pessoais.
- Tom amigável e informal, em português do Brasil, sem julgamentos.
- Responda em até 120 palavras, no formato: diagnóstico, ação recomendada e próximo passo (uma pergunta).
"""


# ==================== CHAMAR GEMINI ====================
@st.cache_data(ttl=3600)
def descobrir_modelo_ativo(api_key: str) -> str:
    dominio = "generativelanguage" + ".googleapis.com"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key
    }
    
    # 1. Busca todos os modelos disponíveis na sua conta
    candidatos = []
    try:
        resp = requests.get(f"https://{dominio}/v1beta/models", headers=headers, timeout=10)
        if resp.ok:
            for m in resp.json().get("models", []):
                nome = m.get("name", "").replace("models/", "")
                metodos = m.get("supportedGenerationMethods", [])
                if "generateContent" in metodos and "gemini" in nome.lower():
                    # Ignora modelos de imagem, áudio ou TTS
                    if not any(x in nome.lower() for x in ["image", "audio", "tts", "vision", "embedding"]):
                        candidatos.append(nome)
    except Exception:
        pass

    # Prioriza modelos 'flash' mais recentes no topo da fila
    candidatos.sort(key=lambda x: ("flash" not in x, x), reverse=False)
    
    # 2. Testa qual modelo realmente aceita geração de texto na prática
    teste_payload = {"contents": [{"parts": [{"text": "oi"}]}]}
    for nome in candidatos:
        url_teste = f"https://{dominio}/v1beta/models/{nome}:generateContent"
        try:
            r = requests.post(url_teste, headers=headers, json=teste_payload, timeout=8)
            if r.status_code == 200:
                return nome
        except Exception:
            continue
            
    return "gemini-flash-latest"

def perguntar_stream(prompt_usuario: str, *args, **kwargs):
    dominio = "generativelanguage" + ".googleapis.com"
    api_key = st.secrets["GEMINI_API_KEY"]
    modelo = descobrir_modelo_ativo(api_key)
    url = f"https://{dominio}/v1beta/models/{modelo}:generateContent"

    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key
    }

    payload = dict(
        systemInstruction=dict(parts=[dict(text=SYSTEM_PROMPT)]),
        contents=[dict(parts=[dict(text=str(prompt_usuario))])],
        generationConfig=dict(
            temperature=0.3,
            maxOutputTokens=2048,
            thinkingConfig=dict(thinkingBudget=0)
        )
    )

    try:
        resposta = requests.post(url, headers=headers, json=payload, timeout=20)
        resposta.raise_for_status()
        dados = resposta.json()

        candidatos = dados.get("candidates")
        if not candidatos or not isinstance(candidatos, list):
            yield "Desculpe, só posso ajudar com dúvidas sobre educação financeira e seus dados cadastrados."
            return

        primeiro = next(iter(candidatos))
        partes = primeiro.get("content", {}).get("parts", [])
        texto = "".join(p.get("text", "") for p in partes).strip()

        if not texto:
            yield "Desculpe, só posso ajudar com dúvidas sobre educação financeira e seus dados cadastrados."
            return

        for palavra in texto.split(" "):
            yield palavra + " "
            time.sleep(0.015)

    except requests.exceptions.Timeout:
        yield "O servidor demorou para responder. Tente enviar sua pergunta novamente."
    except Exception as e:
        yield f"Erro técnico ({modelo}): {e}"


# ==================== ESTADO DA CONVERSA ====================
def limitar_historico(historico, limite=12):
    """Mantém o histórico recente do modelo e pares usuário/assistente válidos."""
    if not isinstance(historico, list):
        return []

    mensagens_validas = [
        {"role": item.get("role"), "content": item.get("content", "")}
        for item in historico
        if isinstance(item, dict)
        and item.get("role") in ("user", "assistant")
        and isinstance(item.get("content", ""), str)
    ]

    recorte = mensagens_validas[-limite:] if limite > 0 else []
    # Evita começar o contexto com uma resposta sem o usuário correspondente.
    while recorte and recorte[0]["role"] == "assistant":
        recorte.pop(0)
    return recorte


PERGUNTA_OFERTA = (
    "Quer cadastrar o seu perfil de investidor? São algumas perguntas rápidas e eu "
    "personalizo as respostas com os seus dados. O perfil vale só durante esta sessão: "
    "**é apagado quando você fechar ou recarregar a página**. Se preferir **não**, tudo "
    "bem: você conversa normalmente comigo, usando um perfil base de exemplo (se existir) "
    "ou com respostas gerais, e nada seu é salvo. (sim/não)"
)

DICA_CANCELAR = "\n\n*(Digite **cancelar** a qualquer momento para conversar sem cadastro.)*"


def comecar_onboarding(msg_inicial):
    st.session_state.oferta = False
    st.session_state.perfil_base = False
    st.session_state.perfil = None
    st.session_state.ob = novo_onboarding()
    st.session_state.hist_llm = []
    st.session_state.mensagens = [
        {
            "role": "assistant",
            "content": msg_inicial + pergunta_fase(FASES[0]) + DICA_CANCELAR,
        }
    ]


def cancelar_cadastro(nome):
    """Sai do cadastro sem salvar nada; volta ao perfil salvo, se houver."""
    st.session_state.ob = None
    st.session_state.oferta = False
    st.session_state.perfil_base = False
    salvo = carregar_perfil(nome)
    st.session_state.perfil = salvo
    if salvo:
        texto = "Cadastro cancelado. Mantive o seu perfil salvo. Como posso ajudar?"
    else:
        texto = (
            "Cadastro cancelado. Vamos conversar sem perfil: respondo de forma geral e "
            "nada seu é salvo. Se mudar de ideia, clique em **Cadastrar meu perfil** na "
            "barra lateral. Como posso ajudar?"
        )
    st.session_state.mensagens.append({"role": "assistant", "content": texto})


def iniciar_conversa(nome):
    st.session_state.nome = nome
    st.session_state.hist_llm = []
    st.session_state.oferta = False
    st.session_state.perfil_base = False
    st.session_state.sessao.apagar()  # trocou de nome: descarta o perfil anterior
    st.session_state.perfil = None
    st.session_state.ob = None
    st.session_state.oferta = True
    st.session_state.mensagens = [
        {
            "role": "assistant",
            "content": f"Olá, {nome}! Sou o Biu, seu assistente de educação financeira. "
            + PERGUNTA_OFERTA,
        }
    ]


# ==================== INTERFACE (CHAT) ====================
if "sessao" not in st.session_state:
    st.session_state.sessao = SessaoPerfil()

st.title("BIU, o seu Assistente Financeiro")

with st.sidebar:
    nome_in = st.text_input("Seu nome:").strip()

if not nome_in:
    st.info("👈 Digite seu nome na barra lateral para começar.")
    st.stop()

if st.session_state.get("nome") != nome_in:
    iniciar_conversa(nome_in)

with st.sidebar:
    em_cadastro = st.session_state.ob is not None
    sem_perfil = (
        st.session_state.perfil is None and not em_cadastro and not st.session_state.oferta
    )
    if st.session_state.perfil:
        titulo = "Perfil base (exemplo)" if st.session_state.perfil_base else "Meu perfil"
        with st.expander(titulo):
            st.json(st.session_state.perfil)
    if st.session_state.perfil and not st.session_state.perfil_base:
        st.caption("🔒 Seu perfil é apagado quando a sessão terminar.")
        if st.button("Apagar meu perfil agora"):
            st.session_state.sessao.apagar()
            st.session_state.perfil = None
            st.session_state.hist_llm = []
            st.session_state.mensagens = [
                {
                    "role": "assistant",
                    "content": "Pronto, apaguei o seu perfil. Seguimos sem perfil: respondo "
                    "de forma geral e nada seu é salvo. Quando quiser, clique em "
                    "**Cadastrar meu perfil**. Como posso ajudar?",
                }
            ]
            st.rerun()
    if not em_cadastro and not st.session_state.oferta:
        if st.button("Limpar conversa"):
            st.session_state.hist_llm = []
            st.session_state.mensagens = [
                {"role": "assistant", "content": f"Conversa reiniciada, {nome_in}! Como posso ajudar?"}
            ]
            st.rerun()
    if st.session_state.perfil_base or st.session_state.oferta or sem_perfil:
        rotulo = "Cadastrar meu perfil"
        aviso_inicio = "Vamos cadastrar o seu perfil de investidor.\n\n"
    else:
        rotulo = "Refazer meu perfil"
        aviso_inicio = (
            "Vamos refazer o seu perfil. O anterior só será substituído quando terminarmos.\n\n"
        )
    if st.button(rotulo):
        comecar_onboarding(aviso_inicio)
        st.rerun()

for m in st.session_state.mensagens:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

if texto := st.chat_input("Digite aqui..."):
    st.session_state.mensagens.append({"role": "user", "content": texto})

    # ---- Fase 0: oferta de cadastro ----
    if st.session_state.oferta:
        try:
            quer_cadastrar = p_sim_nao(texto)
        except ValueError as erro:
            st.session_state.mensagens.append(
                {"role": "assistant", "content": f"{erro} {PERGUNTA_OFERTA}"}
            )
            st.rerun()
        if quer_cadastrar:
            comecar_onboarding("Ótimo! Vou montar o seu perfil. Seus dados ficam só nesta sessão (são apagados quando você fechar ou recarregar a página), e você nunca precisa me passar senhas.\n\n")
        else:
            base = carregar_perfil_base()
            if base is None:
                st.session_state.oferta = False
                st.session_state.mensagens.append(
                    {
                        "role": "assistant",
                        "content": "Tudo bem! Vamos conversar sem perfil: respondo de forma "
                        "geral, sem dados pessoais, e nada seu é salvo. Se quiser "
                        "personalizar depois, clique em **Cadastrar meu perfil** na barra "
                        "lateral. Como posso ajudar?",
                    }
                )
            else:
                st.session_state.oferta = False
                st.session_state.perfil_base = True
                st.session_state.perfil = base
                st.session_state.mensagens.append(
                    {
                        "role": "assistant",
                        "content": f"Tudo bem! Vou usar o **perfil base de exemplo** "
                        f"({base.get('nome', 'cliente fictício')}). Os números dele não são "
                        "seus e nada seu será salvo. Quando quiser, clique em **Cadastrar meu "
                        "perfil** na barra lateral. Como posso ajudar?",
                    }
                )
        st.rerun()

    # ---- Fase 1: montando o perfil ----
    elif st.session_state.ob is not None:
        if norm(texto).strip(" .!") in ("cancelar", "sair"):
            cancelar_cadastro(nome_in)
            st.rerun()
        respostas, concluido = processar_resposta(st.session_state.ob, texto)
        for r in respostas:
            st.session_state.mensagens.append({"role": "assistant", "content": r})
        if concluido:
            perfil = montar_perfil(nome_in, st.session_state.ob["dados"])
            salvar_perfil(nome_in, perfil)
            st.session_state.perfil = perfil
            st.session_state.perfil_base = False
            st.session_state.ob = None
            st.session_state.mensagens.append(
                {"role": "assistant", "content": resumo_perfil(perfil)}
            )
        st.rerun()

    # ---- Fase 2: conversa com o Biu ----
    else:
        with st.chat_message("user"):
            st.markdown(texto)

        historico = "\n".join(
            f"{'Usuário' if h['role'] == 'user' else 'Biu'}: {h['content']}"
            for h in st.session_state.hist_llm[-6:]
        )

        with st.chat_message("assistant"):
            aviso = st.empty()
            aviso.caption("⏳ O Biu está pensando...")

            def gerar():
                primeiro = True
                for pedaco in perguntar_stream(
                    texto, st.session_state.perfil, historico, st.session_state.perfil_base
                ):
                    if primeiro:
                        aviso.empty()
                        primeiro = False
                    yield pedaco

            resposta = st.write_stream(gerar())

            st.session_state.mensagens.append(
                {"role": "assistant", "content": resposta}
            )

            if not resposta.startswith("Erro"):
                st.session_state.hist_llm.append({"role": "user", "content": texto})
                st.session_state.hist_llm.append(
                    {"role": "assistant", "content": resposta}
                )
                st.session_state.hist_llm = limitar_historico(st.session_state.hist_llm)
