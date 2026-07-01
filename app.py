"""
Autentique Agency · Briefing Mensal — servidor webhook

Recebe o formulário HTML (briefing-mensal.html), transcreve os áudios
enviados nos campos de histórias (Whisper/Groq), gera um diagnóstico
estratégico com a Groq API e cria uma página organizada no Notion.

Variáveis de ambiente necessárias:
  GROQ_API_KEY        -> chave da Groq (console.groq.com)
  NOTION_TOKEN         -> token da integração interna do Notion
  NOTION_DATABASE_ID   -> ID do banco "Briefings Mensais (Clientes)"
"""

import os
import json
from datetime import datetime

import requests
from flask import Flask, request, jsonify

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    """Permite que o formulário (hospedado em outro domínio, no GitHub Pages)
    consiga chamar este servidor via fetch()."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/webhook", methods=["OPTIONS"])
def webhook_preflight():
    return ("", 204)


GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
NOTION_PAGES_URL = "https://api.notion.com/v1/pages"
NOTION_VERSION = "2022-06-28"

# Campos do Passo 3 que aceitam áudio no formulário (nome do campo -> nome do arquivo enviado é "audio_<campo>")
AUDIO_FIELDS = ["historiasMarcantes", "resultadosClientes", "novidade", "observacoesFinais"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _txt(value):
    """Normaliza valores (listas, None, vazio) para texto legível."""
    if value is None:
        return "—"
    if isinstance(value, list):
        return ", ".join(v for v in value if v) or "—"
    value = str(value).strip()
    return value if value else "—"


def transcrever_audio(file_storage):
    """Envia um arquivo de áudio para a Whisper da Groq e retorna o texto transcrito."""
    if not GROQ_API_KEY:
        return "(Áudio recebido, mas GROQ_API_KEY não configurada para transcrever.)"

    files = {
        "file": (file_storage.filename or "audio.webm", file_storage.stream, file_storage.mimetype),
    }
    data = {
        "model": "whisper-large-v3",
        "language": "pt",
        "response_format": "text",
    }
    resp = requests.post(
        GROQ_TRANSCRIPTION_URL,
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files=files,
        data=data,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.text.strip()


def aplicar_transcricoes(d, files):
    """Transcreve os áudios enviados (audio_<campo>) e mescla com o texto digitado."""
    for field in AUDIO_FIELDS:
        file_storage = files.get(f"audio_{field}")
        if not file_storage or not file_storage.filename:
            continue
        try:
            transcricao = transcrever_audio(file_storage)
        except requests.HTTPError as e:
            transcricao = f"(Falha ao transcrever áudio: {e})"

        texto_existente = (d.get(field) or "").strip()
        bloco_audio = f"🎙️ Transcrição do áudio: {transcricao}"
        d[field] = f"{texto_existente}\n\n{bloco_audio}" if texto_existente else bloco_audio
    return d


def build_sections_text(d):
    """Monta o texto das 4 seções coletadas no formulário."""

    identificacao = (
        f"Nome: {_txt(d.get('nomeCliente'))}\n"
        f"Mês de referência: {_txt(d.get('mesReferencia'))}"
    )

    gravacao = (
        f"Pode gravar no próximo mês: {_txt(d.get('podeGravar'))}\n"
        f"Materiais que consegue enviar: {_txt(d.get('materiais'))}\n"
        f"Preferência de data (05 a 15): {_txt(d.get('temPreferenciaData'))}\n"
        f"Dias preferidos: {_txt(d.get('diasPreferencia'))}\n"
        f"Observações sobre gravação: {_txt(d.get('obsGravacao'))}"
    )

    comercial = (
        f"Funis utilizados: {_txt(d.get('funis'))}\n"
        f"Funil com mais resultado: {_txt(d.get('funilMaisResultado'))}\n"
        f"Calls agendadas: {_txt(d.get('callsAgendadas'))}\n"
        f"Vendas fechadas: {_txt(d.get('vendasFechadas'))}\n"
        f"Ticket médio: {_txt(d.get('ticketMedio'))}\n"
        f"Taxa de conversão: {_txt(d.get('taxaConversao'))}\n"
        f"Principais objeções: {_txt(d.get('objecoes'))}\n"
        f"Motivo dos não-fechamentos: {_txt(d.get('motivoNaoFechamento'))}\n"
        f"Venda/negociação em destaque: {_txt(d.get('vendaDestaque'))}"
    )

    conteudo = (
        f"Histórias marcantes do mês: {_txt(d.get('historiasMarcantes'))}\n"
        f"Resultados de clientes: {_txt(d.get('resultadosClientes'))}\n"
        f"Produtos/serviços em destaque: {_txt(d.get('produtosDestaque'))}\n"
        f"Novidades (lançamento/promoção/evento): {_txt(d.get('novidade'))}\n"
        f"Temas específicos desejados: {_txt(d.get('temasEspecificos'))}\n"
        f"Referências de conteúdo: {_txt(d.get('linkReferencia'))}\n"
        f"Observações finais: {_txt(d.get('observacoesFinais'))}"
    )

    return identificacao, gravacao, comercial, conteudo


def gerar_diagnostico(d, identificacao, gravacao, comercial, conteudo):
    """Chama a Groq API para gerar o diagnóstico estratégico do mês."""

    if not GROQ_API_KEY:
        return "(Diagnóstico não gerado — GROQ_API_KEY não configurada.)"

    prompt = f"""Você é uma estrategista de marketing de conteúdo analisando o briefing
mensal de uma cliente de agência de conteúdo digital. Fale SOBRE O NEGÓCIO DA CLIENTE
(nunca sobre a agência). Use um tom profissional, direto e estratégico, em português.

DADOS DO MÊS:
{identificacao}

GRAVAÇÃO:
{gravacao}

DADOS COMERCIAIS:
{comercial}

HISTÓRIAS E CONTEÚDO:
{conteudo}

Estruture sua resposta em exatamente estas 6 seções, cada uma com um título em
negrito markdown (**Título**) seguido do texto:
1. Visão Geral do Mês
2. Análise de Performance Comercial
3. Pontos Fortes
4. Desafios e Oportunidades
5. Recomendações Estratégicas de Conteúdo
6. Próximos Passos Prioritários

Seja específico, cite os números e informações fornecidas sempre que possível."""

    resp = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.6,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def callout_block(emoji, title, text):
    content = f"{title}\n\n{text}"
    # Notion limita cada rich_text a 2000 caracteres
    content = content[:1990]
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": emoji},
            "rich_text": [{"type": "text", "text": {"content": content}}],
            "color": "gray_background",
        },
    }


def diagnostico_blocks(diagnostico_texto):
    blocks = [
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {"type": "text", "text": {"content": "🧠 Diagnóstico Estratégico (IA)"}}
                ]
            },
        }
    ]
    # divide o texto da IA em parágrafos (respeitando o limite de 2000 chars)
    for paragrafo in diagnostico_texto.split("\n"):
        paragrafo = paragrafo.strip()
        if not paragrafo:
            continue
        for chunk_start in range(0, len(paragrafo), 1990):
            chunk = paragrafo[chunk_start:chunk_start + 1990]
            blocks.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": chunk}}]
                    },
                }
            )
    return blocks


def criar_pagina_notion(d, identificacao, gravacao, comercial, conteudo, diagnostico):
    nome_cliente = _txt(d.get("nomeCliente"))
    data_hoje = datetime.now().strftime("%d/%m/%Y")
    titulo = f"Briefing {nome_cliente} — {data_hoje}"

    children = [
        callout_block("🪪", "Identificação", identificacao),
        callout_block("🎥", "Disponibilidade para Gravação", gravacao),
        callout_block("💼", "Dados Comerciais", comercial),
        callout_block("✨", "Histórias & Conteúdo", conteudo),
        {"object": "block", "type": "divider", "divider": {}},
    ] + diagnostico_blocks(diagnostico)

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Briefing": {"title": [{"text": {"content": titulo}}]},
            "Dia Recebido": {"date": {"start": datetime.now().strftime("%Y-%m-%d")}},
            "Status": {"select": {"name": "Não usado"}},
        },
        "children": children,
    }

    resp = requests.post(
        NOTION_PAGES_URL,
        headers={
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def status():
    return jsonify({"status": "online"})


@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        raw = request.form.get("dados")
        if not raw:
            return jsonify({"error": "campo 'dados' ausente"}), 400

        d = json.loads(raw)
        d = aplicar_transcricoes(d, request.files)

        identificacao, gravacao, comercial, conteudo = build_sections_text(d)
        diagnostico = gerar_diagnostico(d, identificacao, gravacao, comercial, conteudo)
        notion_page = criar_pagina_notion(
            d, identificacao, gravacao, comercial, conteudo, diagnostico
        )

        return jsonify({"status": "ok", "notion_url": notion_page.get("url")}), 200

    except requests.HTTPError as e:
        return jsonify({"error": "falha em chamada externa", "detail": str(e)}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
