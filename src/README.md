# Passo a passo de Execução
## Settup do Ollama (5 minutos)

1. Instalar o Ollama (ollama.com)

2. Baixar um modelo leve
```bash
ollama pull llama3.2
```
3.Testar se Funciona
```bash
ollama run llama3.2 "Olá!"
```

## Código Completo

Todo código completo está no arquivo `app.py`.


## Como Rodar

```bash
# 1.Instalar dependências.
pip install streamlit pandas requests

# 2.Garantir que o ollama está rodando.
ollama serve

# 3.Rodar app.
streamlit run .\src\app.py
```

## Evidencias de execução
<img width="1917" height="913" alt="image" src="https://github.com/user-attachments/assets/7579a8ce-6710-4a90-b03b-196ff9d1153e" />

