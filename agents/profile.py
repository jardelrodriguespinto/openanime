import json
import logging

from agents.orchestrator import State
from ai.openrouter import openrouter
from graph.neo4j_client import get_neo4j
import prompts.profile as profile_prompt

logger = logging.getLogger(__name__)


def profile_node(state: State) -> dict:
    """Agente de perfil - registra historico, preferencias e memoria do usuario no Neo4j."""
    user_message = state["raw_input"]
    history = state.get("messages", [])
    user_id = state.get("user_id", "?")

    neo4j = get_neo4j()
    neo4j.get_or_create_user(user_id)

    try:
        user_profile = neo4j.get_user_profile(user_id)
    except Exception as e:
        logger.error("Perfil: erro ao carregar perfil: %s", e)
        user_profile = {}

    messages = profile_prompt.build_messages(user_message, history, user_profile)

    logger.info("Agente Perfil: classificando acao para user=%s", user_id)
    try:
        raw = openrouter.converse(messages)
        data = _parse_json(raw)
    except Exception as e:
        logger.error("Agente Perfil: erro OpenRouter: %s", e)
        return {"response": "Nao consegui processar isso. Tenta de novo?"}

    action = data.get("action", "")
    mensagem = data.get("mensagem", "Feito!")
    titulo = (data.get("titulo") or "").strip()
    nota = data.get("nota")
    episodio = data.get("episodio")
    capitulo = data.get("capitulo")
    porcentagem = data.get("porcentagem")
    mood = data.get("mood")
    tempo_min = data.get("tempo_min")
    preferencia_audio = data.get("preferencia_audio")
    permitir_nsfw = data.get("permitir_nsfw")
    limite_violencia = data.get("limite_violencia")
    limite_ecchi = data.get("limite_ecchi")
    comentario = data.get("comentario")
    curti = data.get("curti")
    generos_alerta = _to_list(data.get("generos_alerta"))
    estudios_alerta = _to_list(data.get("estudios_alerta"))

    logger.info("Agente Perfil: action=%s titulo=%s user=%s", action, titulo, user_id)

    try:
        if action == "registrar_assistido" and titulo:
            neo4j.registrar_assistido(user_id, titulo, nota)
            if nota is None:
                mensagem = f"Registrei que voce assistiu *{titulo}*! Que nota voce da de 0 a 10?"

        elif action == "registrar_drop" and titulo:
            neo4j.registrar_drop(user_id, titulo, _to_int_or_none(episodio))
            if episodio is None:
                mensagem = f"Registrei o drop de *{titulo}*. Em qual episodio voce parou?"

        elif action == "registrar_quer_ver" and titulo:
            neo4j.registrar_quer_ver(user_id, titulo)

        elif action == "registrar_progresso" and titulo:
            neo4j.registrar_progresso(
                user_id,
                titulo,
                episodio=_to_int_or_none(episodio),
                capitulo=_to_int_or_none(capitulo),
                porcentagem=_to_float_or_none(porcentagem),
            )
            if episodio is None and capitulo is None and porcentagem is None:
                mensagem = f"Registrei progresso em *{titulo}*. Quer me dizer episodio/capitulo atual?"

        elif action == "atualizar_nota" and titulo and nota is not None:
            atualizado = neo4j.atualizar_nota(user_id, titulo, nota)
            if not atualizado:
                mensagem = f"Nao encontrei *{titulo}* no seu historico. Voce assistiu?"

        elif action == "mostrar_historico":
            historico = neo4j.get_historico(user_id)
            mensagem = _formatar_historico(historico)

        elif action == "mostrar_padroes":
            profile = neo4j.get_user_profile(user_id)
            mensagem = _formatar_padroes(profile)

        elif action == "mostrar_ponte_pos_obra":
            rota = neo4j.get_franchise_timeline(titulo or "")
            if rota:
                mensagem = _formatar_rota_franquia(titulo, rota)
            else:
                mensagem = "Nao achei rota de franquia para essa obra ainda."

        elif action == "definir_mood" and mood:
            neo4j.set_mood_diario(user_id, str(mood))
            mensagem = f"Mood salvo: *{str(mood).strip().lower()}*."

        elif action == "definir_tempo":
            mins = _to_int_or_none(tempo_min)
            neo4j.set_tempo_disponivel(user_id, mins)
            if mins:
                mensagem = f"Tempo salvo: *{mins} min* por sessao."
            else:
                mensagem = "Tempo diario removido."

        elif action == "definir_maturidade":
            neo4j.set_filtros_maturidade(
                user_id,
                permitir_nsfw=_to_bool_or_none(permitir_nsfw),
                limite_violencia=(limite_violencia or "").strip().lower() or None,
                limite_ecchi=(limite_ecchi or "").strip().lower() or None,
            )
            mensagem = "Filtro de maturidade atualizado."

        elif action == "definir_audio" and preferencia_audio:
            neo4j.set_preferencia_audio(user_id, str(preferencia_audio))
            mensagem = f"Preferencia de audio salva: *{str(preferencia_audio).strip().lower()}*."

        elif action == "definir_alertas":
            neo4j.set_alertas(user_id, generos=generos_alerta, estudios=estudios_alerta)
            gtxt = ", ".join(generos_alerta) if generos_alerta else "sem mudanca"
            etxt = ", ".join(estudios_alerta) if estudios_alerta else "sem mudanca"
            mensagem = f"Alertas atualizados.\nGeneros: {gtxt}\nEstudios: {etxt}"

        elif action == "gerar_desafio":
            desafio = neo4j.gerar_desafio_semanal(user_id)
            mensagem = f"*Desafio da semana:*\n{desafio}"

        elif action == "mostrar_desafio":
            settings = neo4j.get_user_settings(user_id)
            desafio = settings.get("desafio_semanal")
            if not desafio:
                desafio = neo4j.gerar_desafio_semanal(user_id)
            mensagem = f"*Seu desafio atual:*\n{desafio}"

        elif action == "mostrar_ranking":
            ranking = neo4j.get_ranking_pessoal(user_id, limit=10)
            mensagem = _formatar_ranking(ranking)

        elif action == "mostrar_watchlist_inteligente":
            watchlist = neo4j.get_watchlist_inteligente(user_id, limit=8)
            mensagem = _formatar_watchlist(watchlist)

        elif action == "detectar_queda_interesse":
            profile = neo4j.get_user_profile(user_id)
            mensagem = _formatar_queda_interesse(profile.get("queda_interesse", {}))

        elif action == "resumo_retorno":
            resumo = neo4j.get_resumo_retorno(user_id, titulo=titulo or None)
            if resumo:
                mensagem = _formatar_resumo_retorno(resumo)
            else:
                mensagem = "Nao achei nada em progresso para recapitular agora."

        elif action == "registrar_feedback" and titulo and isinstance(curti, bool):
            neo4j.registrar_feedback_recomendacao(
                user_id,
                titulo=titulo,
                curti=curti,
                comentario=(comentario or "").strip() or None,
            )
            mensagem = (
                f"Feedback salvo: voce curtiu *{titulo}*."
                if curti
                else f"Feedback salvo: vou evitar recomendacoes no estilo de *{titulo}*."
            )

        elif action == "pedir_nota":
            pass

        elif action == "pedir_episodio":
            pass

    except Exception as e:
        logger.error("Agente Perfil: erro ao executar action=%s: %s", action, e)
        mensagem = "Tive um problema ao salvar. Tenta de novo?"

    return {"response": mensagem}


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except Exception:
            pass

    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except Exception:
            pass

    logger.warning("Perfil: nao conseguiu parsear JSON: %s", raw[:140])
    return {"action": "conversa", "mensagem": raw}


def _to_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [v.strip() for v in text.split(",") if v.strip()]


def _to_int_or_none(value):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _to_float_or_none(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _to_bool_or_none(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "sim", "yes"}:
        return True
    if text in {"0", "false", "nao", "não", "no"}:
        return False
    return None


def _formatar_historico(historico: dict) -> str:
    lines = ["*Seu historico:*"]
    assistidos = historico.get("assistidos", [])
    dropados = historico.get("dropados", [])
    progresso = historico.get("progresso", [])

    if assistidos:
        lines.append("\n*Assistidos:*")
        for a in assistidos:
            nota = a.get("nota")
            nota_str = f" - {nota}/10" if nota is not None else ""
            lines.append(f"  - {a['titulo']}{nota_str}")

    if progresso:
        lines.append("\n*Em progresso:*")
        for p in progresso[:10]:
            detalhe = []
            if p.get("episodio"):
                detalhe.append(f"ep {p['episodio']}")
            if p.get("capitulo"):
                detalhe.append(f"cap {p['capitulo']}")
            if p.get("porcentagem"):
                detalhe.append(f"{p['porcentagem']}%")
            extra = f" ({' | '.join(detalhe)})" if detalhe else ""
            lines.append(f"  - {p['titulo']}{extra}")

    if dropados:
        lines.append("\n*Dropados:*")
        for d in dropados:
            ep = d.get("episodio")
            ep_str = f" (ep {ep})" if ep else ""
            lines.append(f"  - {d['titulo']}{ep_str}")

    if not assistidos and not dropados and not progresso:
        lines.append("Nada registrado ainda.")

    return "\n".join(lines)


def _formatar_padroes(profile: dict) -> str:
    lines = ["*Seus padroes de gosto:*"]

    generos = profile.get("generos_favoritos", [])
    if generos:
        lines.append(f"\n*Generos favoritos:* {', '.join(generos)}")

    temas = profile.get("temas_favoritos", [])
    if temas:
        lines.append(f"\n*Temas favoritos:* {', '.join(temas)}")

    assistidos = profile.get("assistidos", [])
    com_nota = [a for a in assistidos if a.get("nota") is not None]
    if com_nota:
        media = sum(a["nota"] for a in com_nota) / len(com_nota)
        lines.append(f"\n*Media das notas:* {media:.1f}/10")
        top = sorted(com_nota, key=lambda x: x["nota"], reverse=True)[:3]
        lines.append("\n*Top avaliados:*")
        for a in top:
            lines.append(f"  - {a['titulo']} - {a['nota']}/10")

    dropados = profile.get("dropados", [])
    if dropados:
        lines.append(f"\n*Drops:* {len(dropados)} obras")

    mood = profile.get("mood_atual")
    if mood:
        lines.append(f"\n*Mood atual:* {mood}")

    desafio = profile.get("desafio_semanal")
    if desafio:
        lines.append(f"\n*Desafio semanal:* {desafio}")

    return "\n".join(lines)


def _formatar_ranking(ranking: list[dict]) -> str:
    if not ranking:
        return "Ainda nao tenho ranking suficiente. Me manda mais notas."
    lines = ["*Seu ranking dinamico (top 10):*"]
    for i, item in enumerate(ranking[:10], start=1):
        nota = item.get("nota")
        nota_txt = f"{nota}/10" if nota is not None else "sem nota"
        lines.append(f"{i}. {item.get('titulo', '?')} - {nota_txt}")
    return "\n".join(lines)


def _formatar_watchlist(items: list[dict]) -> str:
    if not items:
        return "Sua watchlist esta vazia ou ja foi consumida."
    lines = ["*Watchlist inteligente (prioridade):*"]
    for i, item in enumerate(items[:8], start=1):
        titulo = item.get("titulo", "?")
        score = item.get("score_watchlist", 0)
        eps = item.get("episodios")
        eps_txt = f"{eps} eps" if eps else "eps ?"
        lines.append(f"{i}. {titulo} - score {score} - {eps_txt}")
    return "\n".join(lines)


def _formatar_queda_interesse(qi: dict) -> str:
    if not qi:
        return "Nao consegui calcular queda de interesse agora."
    sinais = qi.get("sinais") or []
    sinais_txt = ", ".join(sinais) if sinais else "sem sinais fortes"
    return (
        "*Detector de queda de interesse:*\n"
        f"Nivel: {qi.get('nivel', 'baixo')}\n"
        f"Taxa de drop: {qi.get('ratio_drop', 0)}\n"
        f"Sinais: {sinais_txt}\n"
        f"Sugestao: {qi.get('sugestao', 'seguir ritmo atual')}"
    )


def _formatar_resumo_retorno(resumo: dict) -> str:
    titulo = resumo.get("titulo", "?")
    ep = resumo.get("episodio_atual")
    cap = resumo.get("capitulo_atual")
    pct = resumo.get("porcentagem")
    posicoes = []
    if ep is not None:
        posicoes.append(f"ep {ep}")
    if cap is not None:
        posicoes.append(f"cap {cap}")
    if pct is not None:
        posicoes.append(f"{pct}%")
    pos_txt = " | ".join(posicoes) if posicoes else "posicao nao registrada"
    sinopse = (resumo.get("sinopse") or "").strip()
    if len(sinopse) > 420:
        sinopse = sinopse[:420] + "..."
    return (
        f"*Resumo para voltar em {titulo}:*\n"
        f"Voce parou em: {pos_txt}\n\n"
        f"{sinopse or 'Sem sinopse disponivel.'}\n\n"
        "Se quiser, eu monto um recap sem spoiler em 5-8 linhas com foco no essencial."
    )


def _formatar_rota_franquia(titulo: str, rota: dict) -> str:
    nome = rota.get("franquia") or titulo or "franquia"
    pos = rota.get("pos_obra", []) or []
    ponte = rota.get("ponte_animemanga", []) or []
    lines = [f"*Rota da franquia ({nome}):*"]

    if pos:
        lines.append("\n*Pos-obra sugerida:*")
        for item in pos[:5]:
            lines.append(f"  - {item}")

    if ponte:
        lines.append("\n*Ponte anime-manga:*")
        for item in ponte[:4]:
            lines.append(f"  - {item}")

    if len(lines) == 1:
        lines.append("Sem detalhes extras disponiveis.")

    return "\n".join(lines)

