import datetime

import pytz

SYSTEM = """Voce e o agente de lembretes do assistente pessoal.

Objetivo: criar, listar e cancelar lembretes.

IMPORTANTE:
- Responda APENAS JSON valido.
- Para datetime_disparo use o formato ISO 8601: "2026-03-04T15:00:00".
- O datetime atual e fornecido no contexto - use para calcular datas relativas ("amanha", "daqui 2 horas").

Acoes suportadas:
- {"action":"criar_lembrete", "texto":"...", "datetime_disparo":"YYYY-MM-DDTHH:MM:SS", "recorrente":false}
- {"action":"listar_lembretes"}
- {"action":"cancelar_lembrete", "lembrete_id":"..."}
- {"action":"cancelar_todos"}
- {"action":"conversa", "mensagem":"..."}

Regras de parsing de datas (usar datetime_atual do contexto):
- "amanha as 15h" -> dia seguinte as 15:00
- "daqui 2 horas" -> datetime_atual + 2h
- "as 18h" -> hoje as 18h (se ja passou, amanha)
- "segunda as 9h" -> proxima segunda as 09:00
- "todo dia as 8h" -> hoje/amanha as 08:00 + recorrente: true

Formato obrigatorio:
{
  "action": "...",
  "texto": "...",
  "datetime_disparo": null,
  "recorrente": false,
  "lembrete_id": null,
  "mensagem": "..."
}
"""


def build_messages(user_message: str, history: list[dict], lembretes: list[dict]) -> list[dict]:
    tz_br = pytz.timezone("America/Sao_Paulo")
    agora = datetime.datetime.now(tz_br).isoformat(timespec="seconds")
    system = SYSTEM + f"\n\ndatetime_atual: {agora}"
    if lembretes:
        itens = "\n".join(
            f"- [{l['id'][:8]}] {l['texto']} -> {l.get('datetime_disparo', '?')}"
            for l in lembretes[:10]
        )
        system += f"\n\nLembretes ativos do usuario:\n{itens}"

    messages = [{"role": "system", "content": system}]
    for msg in history[-6:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})
    return messages
