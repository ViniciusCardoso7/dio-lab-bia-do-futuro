# Código da Aplicação

import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

import requests
import streamlit as st

# ==================== CONFIGURAÇÃO ====================
OLLAMA_URL = "http://localhost:11434/api/generate"
MODELO = "llama3.2"
# llama3.2 (3B) não tem modo de raciocínio: não use o parâmetro "think".
# num_ctx maior evita que o prompt (regras + perfil + produtos) seja cortado.
OPCOES_MODELO = {"num_ctx": 4096, "num_predict": 500, "temperature": 0.3}

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PERFIS_DIR = DATA_DIR / "perfis"
PRODUTOS_PATH = DATA_DIR / "produtos_financeiros.json"
PERFIL_BASE_PATH = DATA_DIR / "perfil_investidor.json"  # perfil base (exemplo)

st.set_page_config(page_title="Biu — Assistente Financeiro", page_icon="💰")


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


# ==================== PERFIL: ARQUIVO ====================
def caminho_perfil(nome):
    return PERFIS_DIR / slug(nome) / "perfil_investidor.json"


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
        f"Salvei tudo em `{caminho}`. Agora é só perguntar: como posso te ajudar?",
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


def montar_contexto(perfil, base=False):
    linhas = []
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

    produtos = carregar_produtos()
    if produtos:
        linhas.append("")
        linhas.append("PRODUTOS DE REFERÊNCIA (dados da base, não são taxas atuais):")
        for p in produtos:
            linhas.append(
                f"- {p.get('nome')} | {p.get('categoria')} | risco {p.get('risco')} | "
                f"{p.get('rentabilidade')} | aporte mínimo {p.get('aporte_minimo')} | "
                f"indicado para: {p.get('indicado_para')}"
            )
    return "\n".join(linhas)


SYSTEM_PROMPT = """Você é o Biu, assistente de educação financeira para brasileiros.
Responda SEMPRE em português do Brasil, mesmo que a pergunta venha em outro idioma.
Regras:
- o Biu não é consultor financeiro e não substitui orientação profissional.
- As linhas "CÁLCULO" do contexto já foram calculadas pela aplicação: use esses números como verdade e não refaça as contas.
- Use somente os dados do CONTEXTO DO CLIENTE e os valores que o usuário informar. Nunca invente renda, saldos, taxas ou rentabilidades.
- Se faltar dado, diga o que falta e peça só o necessário.
- Mostre contas de forma simples (ex.: R$ 15.000 - R$ 10.000 = R$ 5.000). Avise que a conta ignora rendimentos e inflação.
- Priorize reserva de emergência (3 a 6 meses de despesas essenciais) antes de investir. Não prometa ganhos nem cite taxas atuais; para isso, oriente consultar fontes oficiais.
- Nunca peça senhas ou códigos. Responda só sobre finanças pessoais.
- Tom amigável e informal, em português do Brasil, sem julgamentos.
- Responda em até 120 palavras, no formato: diagnóstico, ação recomendada e próximo passo (uma pergunta).
"""


# ==================== CHAMAR OLLAMA ====================
def perguntar_stream(msg, perfil, historico="", base=False):
    prompt = f"""{SYSTEM_PROMPT}
Data de hoje: {date.today().strftime('%d/%m/%Y')}

CONTEXTO DO CLIENTE:
{montar_contexto(perfil, base)}

CONVERSA ATÉ AGORA:
{historico}

Pergunta: {msg}"""

    payload = {
        "model": MODELO,
        "prompt": prompt,
        "stream": True,
        "keep_alive": "30m",
        "options": OPCOES_MODELO,
    }

    try:
        with requests.post(
            OLLAMA_URL,
            json=payload,
            stream=True,
            timeout=(10, 600),
        ) as r:
            if r.status_code == 404:
                raise RuntimeError(
                    f"O modelo '{MODELO}' não está instalado. "
                    f"Execute no terminal: ollama pull {MODELO}"
                )

            if r.status_code != 200:
                raise RuntimeError(
                    f"O Ollama retornou {r.status_code}: {r.text[:300]}"
                )

            for linha in r.iter_lines():
                if not linha:
                    continue

                try:
                    dados = json.loads(linha)
                except json.JSONDecodeError:
                    continue

                if dados.get("error"):
                    raise RuntimeError(dados["error"])

                if dados.get("response"):
                    yield dados["response"]

                if dados.get("done"):
                    break

    except requests.exceptions.ConnectionError:
        yield (
            "Não consegui conectar ao Ollama. Verifique se ele está em execução "
            "com o comando `ollama serve` e se o modelo está instalado."
        )
    except Exception as erro:
        yield f"Erro ao consultar o modelo: {erro}"

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
    "personalizo as respostas com os seus dados. Se preferir **não**, uso o perfil base "
    "de exemplo, só para demonstração, e nada seu é salvo. (sim/não)"
)


def comecar_onboarding(msg_inicial):
    st.session_state.oferta = False
    st.session_state.perfil_base = False
    st.session_state.perfil = None
    st.session_state.ob = novo_onboarding()
    st.session_state.hist_llm = []
    st.session_state.mensagens = [
        {"role": "assistant", "content": msg_inicial + pergunta_fase(FASES[0])}
    ]


def iniciar_conversa(nome):
    st.session_state.nome = nome
    st.session_state.hist_llm = []
    st.session_state.oferta = False
    st.session_state.perfil_base = False
    perfil = carregar_perfil(nome)
    if perfil is None:
        st.session_state.perfil = None
        st.session_state.ob = None
        st.session_state.oferta = True
        st.session_state.mensagens = [
            {
                "role": "assistant",
                "content": f"Olá, {nome}! Sou o Biu, seu assistente de educação financeira. "
                "Ainda não encontrei um perfil salvo para você. " + PERGUNTA_OFERTA,
            }
        ]
    else:
        st.session_state.perfil = perfil
        st.session_state.ob = None
        st.session_state.mensagens = [
            {
                "role": "assistant",
                "content": f"Que bom te ver de novo, {nome}! Já tenho o seu perfil salvo. "
                "Como posso te ajudar hoje?",
            }
        ]


# ==================== INTERFACE (CHAT) ====================
st.title("BIU — Assistente Financeiro")

with st.sidebar:
    nome_in = st.text_input("Seu nome:").strip()

if not nome_in:
    st.info("👈 Digite seu nome na barra lateral para começar.")
    st.stop()

if st.session_state.get("nome") != nome_in:
    iniciar_conversa(nome_in)

with st.sidebar:
    if st.session_state.perfil:
        titulo = "Perfil base (exemplo)" if st.session_state.perfil_base else "Meu perfil"
        with st.expander(titulo):
            st.json(st.session_state.perfil)
        if st.button("Limpar conversa"):
            st.session_state.hist_llm = []
            st.session_state.mensagens = [
                {"role": "assistant", "content": f"Conversa reiniciada, {nome_in}! Como posso ajudar?"}
            ]
            st.rerun()
    if st.session_state.perfil_base or st.session_state.oferta:
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
            comecar_onboarding("Ótimo! Vou montar o seu perfil. Seus dados ficam salvos só neste computador, e você nunca precisa me passar senhas.\n\n")
        else:
            base = carregar_perfil_base()
            if base is None:
                st.session_state.mensagens.append(
                    {
                        "role": "assistant",
                        "content": "Não encontrei o perfil base (data/perfil_investidor.json), "
                        "então vamos cadastrar o seu mesmo.\n\n" + pergunta_fase(FASES[0]),
                    }
                )
                st.session_state.oferta = False
                st.session_state.ob = novo_onboarding()
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
