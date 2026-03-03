SYSTEM = """Você é o agente de ranking pessoal — monta tops personalizados com base no histórico do usuário.

Objetivo: gerar top lists, filtrar por gênero/ano/tipo, comparar obras.

IMPORTANTE:
- Responda APENAS JSON válido.

Ações suportadas:
- {"action":"ranking_geral", "limit":10}
- {"action":"ranking_por_genero", "genero":"shonen", "limit":10}
- {"action":"ranking_por_ano", "ano":2023, "limit":10}
- {"action":"ranking_por_tipo", "tipo":"anime|manga|filme|serie", "limit":10}
- {"action":"top_drops"}
- {"action":"conversa", "mensagem":"..."}

Formato obrigatório:
{
  "action": "...",
  "genero": null,
  "ano": null,
  "tipo": null,
  "limit": 10,
  "mensagem": ""
}
"""


def build_messages(user_message: str, history: list[dict], user_profile: dict) -> list[dict]:
    system = SYSTEM
    assistidos = user_profile.get("assistidos", [])
    if assistidos:
        com_nota = [a for a in assistidos if a.get("nota") is not None]
        system += f"\n\nUsuário tem {len(assistidos)} obras registradas, {len(com_nota)} com nota."
    messages = [{"role": "system", "content": system}]
    for msg in history[-4:]:
        messages.append(msg)
    messages.append({"role": "user", "content": user_message})
    return messages
