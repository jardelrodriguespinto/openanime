"""Agente de finanças pessoais — registra gastos e gera resumos."""
import json
import logging
import datetime

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
import prompts.financas as financas_prompt

logger = logging.getLogger(__name__)


def financas_node(state: State) -> dict:
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    messages = financas_prompt.build_messages(user_message, history)

    try:
        raw = openrouter.converse(messages)
        data = _parse_json(raw)
    except Exception as e:
        logger.error("financas: erro LLM: %s", e)
        return {"response": "Não consegui processar. Tenta de novo!"}

    action = data.get("action", "conversa")
    mensagem = data.get("mensagem", "")
    valor = data.get("valor")
    categoria = (data.get("categoria") or "outros").strip().lower()
    descricao = (data.get("descricao") or "").strip()
    data_str = (data.get("data") or datetime.date.today().isoformat()).strip()
    mes = data.get("mes")
    ano = data.get("ano")
    gasto_id = (data.get("gasto_id") or "").strip()

    logger.info("financas: user=%s action=%s valor=%s cat=%s", user_id, action, valor, categoria)

    neo4j = get_neo4j()

    try:
        if action == "registrar_gasto" and valor is not None:
            valor_f = float(valor)
            neo4j.registrar_gasto(user_id, valor_f, categoria, descricao, data_str)
            desc_txt = f" ({descricao})" if descricao else ""
            mensagem = f"Gasto registrado: <b>R$ {valor_f:.2f}</b> em {categoria}{desc_txt}."

        elif action in ("listar_gastos", "resumo_por_categoria"):
            mes_q = int(mes) if mes else datetime.date.today().month
            ano_q = int(ano) if ano else datetime.date.today().year
            gastos = neo4j.get_gastos(user_id, mes=mes_q, ano=ano_q)
            if action == "resumo_por_categoria" and data.get("categoria"):
                cat_f = data["categoria"].lower()
                gastos = [g for g in gastos if g.get("categoria", "").lower() == cat_f]
            mensagem = _formatar_lista_gastos(gastos, mes_q, ano_q)

        elif action == "resumo_mensal":
            mes_q = int(mes) if mes else datetime.date.today().month
            ano_q = int(ano) if ano else datetime.date.today().year
            resumo = neo4j.resumo_gastos(user_id, mes_q, ano_q)
            mensagem = _formatar_resumo(resumo)

        elif action == "deletar_gasto" and gasto_id:
            ok = neo4j.deletar_gasto(user_id, gasto_id)
            mensagem = "Gasto removido!" if ok else "Não encontrei esse gasto."

        elif not mensagem:
            mensagem = "Me diz um gasto! Ex: \"gastei 50 no mercado\" ou \"quanto gastei esse mês?\""

    except Exception as e:
        logger.error("financas: erro action=%s: %s", action, e)
        mensagem = "Tive um problema. Tenta de novo?"

    return {"response": mensagem}


def _formatar_lista_gastos(gastos: list[dict], mes: int, ano: int) -> str:
    if not gastos:
        return f"Nenhum gasto registrado em {mes:02d}/{ano}."
    total = sum(g.get("valor", 0) for g in gastos)
    linhas = [f"<b>Gastos em {mes:02d}/{ano} — Total: R$ {total:.2f}</b>\n"]
    for g in gastos[:20]:
        desc = g.get("descricao") or ""
        desc_txt = f" — {desc}" if desc else ""
        linhas.append(f"- <b>R$ {g.get('valor', 0):.2f}</b> · {g.get('categoria','?')}{desc_txt} · {g.get('data','?')}")
    return "\n".join(linhas)


def _formatar_resumo(resumo: dict) -> str:
    mes = resumo.get("mes", 0)
    ano = resumo.get("ano", 0)
    total = resumo.get("total", 0)
    por_cat = resumo.get("por_categoria", {})
    linhas = [f"<b>Resumo {mes:02d}/{ano} — Total: R$ {total:.2f}</b>\n"]
    for cat, val in sorted(por_cat.items(), key=lambda x: -x[1]):
        pct = (val / total * 100) if total else 0
        linhas.append(f"- {cat}: R$ {val:.2f} ({pct:.0f}%)")
    return "\n".join(linhas)


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except Exception:
            pass
    return {"action": "conversa", "mensagem": raw}
