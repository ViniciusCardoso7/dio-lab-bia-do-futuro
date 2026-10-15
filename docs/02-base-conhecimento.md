# Base de Conhecimento

## Dados Utilizados

Descreva se usou os arquivos da pasta `data`, por exemplo:

| Arquivo | Formato | Utilização no Agente |
|---------|---------|---------------------|
| `historico_atendimento.csv` | CSV | Contextualizar interações anteriores, ou seja, conhecendo melhor o cliente. |
| `perfil_investidor.json` | JSON | Personalizar as explicações sobre as duvidás e necessidades do cliente. |
| `produtos_financeiros.json` | JSON | COnhecer os produtos disponiveis e esplica-los ao cliente |
| `transacoes.csv` | CSV | Analisar padrão de gastos do cliente e usar essas informações em formas de dados. |

> [!TIP]
> **Quer um dataset mais robusto?** Você pode utilizar datasets públicos do [Hugging Face](https://huggingface.co/datasets) relacionados a finanças, desde que sejam adequados ao contexto do desafio.

---

## Adaptações nos Dados

> Você modificou ou expandiu os dados mockados? Descreva aqui.

Não teve até o momento.

---

## Estratégia de Integração

### Como os dados são carregados?
> Descreva como seu agente acessa a base de conhecimento.

Existem duas possibilidades, uma é "injetar" os dados diretamente no prompt (Ctrl + C, Ctrl + V), outra é via código, como no exemplo abaixo:

```
import pandas as pd
import json
import urllib.request


# IMPORTAÇÃO DE DADOS — dio-lab-bia-do-futuro
# github.com/ViniciusCardoso7/dio-lab-bia-do-futuro


BASE_URL = "https://raw.githubusercontent.com/ViniciusCardoso7/dio-lab-bia-do-futuro/main/data/"


# 1. Importar transacoes.csv com pandas


url_csv = BASE_URL + "transacoes.csv"
df_transacoes = pd.read_csv(url_csv, parse_dates=["data"])

print("=== TRANSAÇÕES ===")
print(df_transacoes)
print(f"\nShape: {df_transacoes.shape[0]} linhas × {df_transacoes.shape[1]} colunas")
print(f"\nTipos:\n{df_transacoes.dtypes}")



# 2. Importar perfil_investidor.json


url_json = BASE_URL + "perfil_investidor.json"
with urllib.request.urlopen(url_json) as resposta:
    perfil = json.load(resposta)

print("\n=== PERFIL DO INVESTIDOR ===")
print(f"Nome            : {perfil['nome']}")
print(f"Perfil          : {perfil['perfil_investidor']}")
print(f"Renda mensal    : R$ {perfil['renda_mensal']:.2f}")
print(f"Patrimônio total: R$ {perfil['patrimonio_total']:.2f}")
print(f"Objetivo        : {perfil['objetivo_principal']}")
print(f"\nMetas:")
for meta in perfil["metas"]:
    print(f"  • {meta['meta']} — R$ {meta['valor_necessario']:,.2f} até {meta['prazo']}")
```

### Como os dados são usados no prompt?
> Os dados vão no system prompt? São consultados dinamicamente?

Para simplificar podemos simplesmente "injetar" os dados no prompt, garantindo que a agente tenha o melhor contexto possivel. Lembrando que, em soluções mais robustas é que sejam carregasdas dinamicamente.

```text
DADOS DO CLIENTE E PERFIL(data/perfil_investidor.json):
{
  "nome": "João Silva",
  "idade": 32,
  "profissao": "Analista de Sistemas",
  "renda_mensal": 5000.00,
  "perfil_investidor": "moderado",
  "objetivo_principal": "Construir reserva de emergência",
  "patrimonio_total": 15000.00,
  "reserva_emergencia_atual": 10000.00,
  "aceita_risco": false,
  "metas": [
    {
      "meta": "Completar reserva de emergência",
      "valor_necessario": 15000.00,
      "prazo": "2026-06"
    },
    {
      "meta": "Entrada do apartamento",
      "valor_necessario": 50000.00,
      "prazo": "2027-12"
    }
  ]
}

TRANSACOES DO CLIENTE(data/transacoes.csv):
data,descricao,categoria,valor,tipo
2025-10-01,Salário,receita,5000.00,entrada
2025-10-02,Aluguel,moradia,1200.00,saida
2025-10-03,Supermercado,alimentacao,450.00,saida
2025-10-05,Netflix,lazer,55.90,saida
2025-10-07,Farmácia,saude,89.00,saida
2025-10-10,Restaurante,alimentacao,120.00,saida
2025-10-12,Uber,transporte,45.00,saida
2025-10-15,Conta de Luz,moradia,180.00,saida
2025-10-20,Academia,saude,99.00,saida
2025-10-25,Combustível,transporte,250.00,saida

HISTORICO DE ATENDIMENTO DO CLIENTE(data/historico_atendimento.csv):
data,canal,tema,resumo,resolvido
2025-09-15,chat,CDB,Cliente perguntou sobre rentabilidade e prazos,sim
2025-09-22,telefone,Problema no app,Erro ao visualizar extrato foi corrigido,sim
2025-10-01,chat,Tesouro Selic,Cliente pediu explicação sobre o funcionamento do Tesouro Direto,sim
2025-10-12,chat,Metas financeiras,Cliente acompanhou o progresso da reserva de emergência,sim
2025-10-25,email,Atualização cadastral,Cliente atualizou e-mail e telefone,sim

PRODUTOS FINANCEIROS (data/produtos_financeiros.json):
[
  {
    "nome": "Tesouro Selic",
    "categoria": "renda_fixa",
    "risco": "baixo",
    "rentabilidade": "100% da Selic",
    "aporte_minimo": 30.00,
    "indicado_para": "Reserva de emergência e iniciantes"
  },
  {
    "nome": "CDB Liquidez Diária",
    "categoria": "renda_fixa",
    "risco": "baixo",
    "rentabilidade": "102% do CDI",
    "aporte_minimo": 100.00,
    "indicado_para": "Quem busca segurança com rendimento diário"
  },
  {
    "nome": "LCI/LCA",
    "categoria": "renda_fixa",
    "risco": "baixo",
    "rentabilidade": "95% do CDI",
    "aporte_minimo": 1000.00,
    "indicado_para": "Quem pode esperar 90 dias (isento de IR)"
  },
  {
    "nome": "Fundo Multimercado",
    "categoria": "fundo",
    "risco": "medio",
    "rentabilidade": "CDI + 2%",
    "aporte_minimo": 500.00,
    "indicado_para": "Perfil moderado que busca diversificação"
  },
  {
    "nome": "Fundo de Ações",
    "categoria": "fundo",
    "risco": "alto",
    "rentabilidade": "Variável",
    "aporte_minimo": 100.00,
    "indicado_para": "Perfil arrojado com foco no longo prazo"
  }
]

```

---

## Exemplo de Contexto Montado

> Mostre um exemplo de como os dados são formatados para o agente.

O exemplo do contexto montado abaixo, foram feitos utilizando dados originais de base de conhecimento do projeto, mas sintetiza deixando apenas informações mais relevantes, otimizando consumo de tokens. Vale ressaltar que economizar tokens é ter todas as informações relevantes para o contexto.

```
Dados do Cliente:
- Nome: João Silva.
- Perfil: Moderado.
- Objetivo: Juntar R$ 300 por mês.
- Saldo disponível: R$ 5.000.

Resumo de Gastos:
- Moradia: R$ 1.800,00
- Alimentação: R$ 600
- Transporte: R$ 578,90
- Plano de saúde: R$ 686,80
- Remédios: R$ 522,77
- Lazer: R$ 500
- TOTAL DE SAÍDAS: R$ 4.688,47

Produtos Disponiveis:
-Tesouro Selic. / risco": "baixo"
-CDB Liquidez Diária. / risco": "baixo"
-LCI/LCA. / "risco": "baixo"
-Fundo Multimercado. / risco": "medio"
-Fundo de Ações. / risco": "alto"

Últimas transações:
- 01/11: Supermercado - R$ 450
- 03/11: Streaming - R$ 55
...
```
