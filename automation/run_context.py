"""
Contexto de execução por PLATAFORMA (contextvar).

Permite que LinkedIn e Indeed rodem em paralelo sem se atropelar: cada task de
automação define sua plataforma no topo da corrotina, e todo o estado por
plataforma (driver Selenium, perfil Firefox, pausa/intervenção, step) é resolvido
por esta variável de contexto — que o asyncio propaga automaticamente para tudo
que a task aguarda.

- Nas TASKS de automação: chame `set_platform("linkedin"|"indeed")` como 1ª linha.
- Nos endpoints HTTP do dashboard (sem task de automação): passe a plataforma
  explicitamente para as funções de estado (elas caem no contextvar só como default).
"""

import contextvars

_current_platform: "contextvars.ContextVar[str]" = contextvars.ContextVar(
    "automation_platform", default="default"
)


def set_platform(platform: str) -> None:
    """Define a plataforma da task atual (e das corrotinas que ela aguardar)."""
    _current_platform.set(platform or "default")


def get_platform() -> str:
    """Plataforma da task atual. 'default' quando não definida (ex.: Gupy, HTTP)."""
    return _current_platform.get()
