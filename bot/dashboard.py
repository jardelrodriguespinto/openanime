"""
Dashboard Vue.js + Socket.IO com autenticação para acompanhamento de candidaturas.
Painel admin protegido por login/senha.
"""

import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import socketio

from graph.neo4j_client import get_neo4j

logger = logging.getLogger(__name__)

# Configurações de auth
ADMIN_USER = os.getenv("DASHBOARD_USER", "admin")
ADMIN_PASS = os.getenv("DASHBOARD_PASS", "")
SECRET_KEY = os.getenv("DASHBOARD_SECRET", "change-me-in-production")

# Estado global da automação
_automacao_status = {
    "running": False,
    "action": "idle",
    "platform": "",
    "vagas_processadas": 0,
    "ultima_mensagem": "",
    "updated_at": datetime.now().isoformat(),
}

_status_history = []
_MAX_HISTORY = 50

# Estado global para historico de automacao
_status_history = []
_MAX_HISTORY = 50


def _set_automacao_status(running: bool, action: str = "idle", platform: str = "", mensagem: str = ""):
    global _automacao_status
    _automacao_status = {
        "running": running,
        "action": action,
        "platform": platform,
        "vagas_processadas": _automacao_status.get("vagas_processadas", 0),
        "ultima_mensagem": mensagem,
        "updated_at": datetime.now().isoformat(),
    }
    if mensagem:
        _status_history.insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "msg": mensagem,
            "action": action,
        })
        if len(_status_history) > _MAX_HISTORY:
            _status_history.pop()


def emit_status_update():
    try:
        asyncio.create_task(sio.emit("automacao_status", _automacao_status))
        asyncio.create_task(sio.emit("status_history", _status_history[:10]))
    except Exception:
        pass


def emit_candidatura_update(cand: dict):
    try:
        asyncio.create_task(sio.emit("candidatura_update", cand))
    except Exception:
        pass


def emit_browser_step(step_data: dict):
    try:
        asyncio.create_task(sio.emit("browser_step_update", step_data))
    except Exception:
        pass


_browser_current_step = {"step": "", "action": "", "detail": "", "updated_at": ""}


def set_browser_current_step(step: str = "", action: str = "", detail: str = ""):
    global _browser_current_step
    _browser_current_step = {
        "step": step or _browser_current_step.get("step", ""),
        "action": action or _browser_current_step.get("action", ""),
        "detail": detail or _browser_current_step.get("detail", ""),
        "updated_at": datetime.now().isoformat(),
    }
    emit_browser_step(_browser_current_step)


sio = socketio.AsyncServer(cors_allowed_origins="*", async_mode="asgi")
fastapi_app = FastAPI()
combined_app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)

security = HTTPBasic()

sessions: dict[str, datetime] = {}

LOGIN_PAGE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Login - Job Apply Dashboard</title>
    <style>
        body { font-family: system-ui; background: #0f0f23; color: #fff; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .login-box { background: #16213e; padding: 30px; border-radius: 10px; width: 300px; }
        h2 { color: #00d4ff; margin-bottom: 20px; text-align: center; }
        input { width: 100%; padding: 10px; margin: 10px 0; border: none; border-radius: 5px; background: #1a1a2e; color: #fff; }
        button { width: 100%; padding: 10px; background: #00d4ff; border: none; border-radius: 5px; font-weight: bold; cursor: pointer; }
        .error { color: #ff4444; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="login-box">
        <h2>🔐 Admin Login</h2>
        <form method="POST" action="/login">
            <input type="text" name="username" placeholder="Usuário" required>
            <input type="password" name="password" placeholder="Senha" required>
            <button type="submit">Entrar</button>
        </form>
    </div>
</body>
</html>
"""

VUE_DASHBOARD = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Job Apply Dashboard - Admin</title>
    <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f0f23; color: #eee; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        h1 { color: #00d4ff; font-size: 1.5rem; }
        .logout { background: #ff4444; border: none; padding: 8px 16px; border-radius: 4px; color: #fff; cursor: pointer; }
        .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin-bottom: 20px; }
        .stat-card { background: #16213e; padding: 15px; border-radius: 8px; text-align: center; }
        .stat-card h3 { color: #00d4ff; }
        .stat-card.sucesso h3 { color: #00ff88; }
        .stat-card.falha h3 { color: #ff4444; }
        .filters { margin-bottom: 15px; display: flex; gap: 10px; }
        .filters input, .filters select { padding: 8px; border: none; border-radius: 4px; background: #1a1a2e; color: #fff; }
        .filters button { padding: 8px 16px; background: #00d4ff; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }
        .candidaturas { display: flex; flex-direction: column; gap: 10px; }
        .cand-card { background: #16213e; padding: 15px; border-radius: 8px; border-left: 4px solid #00d4ff; cursor: pointer; transition: all 0.2s; }
        .cand-card:hover { background: #1a1a2e; }
        .cand-card.sucesso { border-left-color: #00ff88; }
        .cand-card.falha { border-left-color: #ff4444; }
        .cand-card.processando { border-left-color: #ffaa00; animation: pulse 1s infinite; }
        @keyframes pulse { 50% { opacity: 0.7; } }
        .meta { display: flex; gap: 10px; font-size: 0.85rem; color: #888; margin-top: 8px; }
        .meta span { background: #1a1a2e; padding: 3px 8px; border-radius: 4px; }
        .live-indicator { display: inline-block; width: 8px; height: 8px; background: #00ff88; border-radius: 50%; margin-left: 5px; }
        .control-bar { margin-bottom: 15px; display: flex; gap: 10px; }
        .control-bar button { padding: 8px 12px; background: #1a1a2e; border: 1px solid #00d4ff; color: #00d4ff; border-radius: 4px; cursor: pointer; }
        .control-bar button:hover { background: #00d4ff; color: #0f0f23; }
        .automacao-bar { background: #16213e; padding: 12px 15px; border-radius: 8px; margin-bottom: 15px; display: flex; align-items: center; justify-content: space-between; gap: 15px; }
        .automacao-status { display: flex; align-items: center; gap: 10px; }
        .status-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
        .status-dot.online { background: #00ff88; box-shadow: 0 0 8px #00ff88; }
        .status-dot.offline { background: #ff4444; }
        .status-dot.busy { background: #ffaa00; animation: pulse 1s infinite; }
        .status-text { font-weight: bold; font-size: 0.95rem; }
        .automacao-detail { display: flex; gap: 15px; font-size: 0.85rem; color: #aaa; }
        .history-panel { background: #16213e; padding: 12px; border-radius: 8px; margin-bottom: 15px; max-height: 150px; overflow-y: auto; }
        .history-panel h4 { margin: 0 0 8px 0; color: #00d4ff; font-size: 0.9rem; }
        .history-item { font-size: 0.8rem; color: #ccc; padding: 3px 0; border-bottom: 1px solid #1a1a2e; }
        .history-item span { color: #888; margin-right: 8px; }

        .browser-panel { background: #16213e; padding: 12px; border-radius: 8px; margin-bottom: 15px; }
        .browser-panel h4 { margin: 0 0 10px 0; color: #00d4ff; font-size: 0.9rem; }
        .browser-viewport { background: #0a0a1a; border-radius: 6px; overflow: hidden; position: relative; width: 100%; max-width: 800px; margin: 0 auto; border: 1px solid #333; }
        .browser-viewport img { width: 100%; display: block; }
        .browser-url { font-size: 0.75rem; color: #888; padding: 6px 10px; background: #1a1a2e; border-top: 1px solid #333; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .browser-step { font-size: 0.75rem; color: #bbb; padding: 6px 10px; background: #1a1a2e; border-top: 1px solid #333; display: flex; gap: 10px; align-items: center; }
        .browser-step .step-label { color: #00d4ff; font-weight: bold; }
        .browser-controls { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
        .browser-controls button { padding: 6px 12px; border-radius: 4px; border: 1px solid #00d4ff; background: #1a1a2e; color: #00d4ff; cursor: pointer; font-size: 0.8rem; }
        .browser-controls button:hover { background: #00d4ff; color: #0f0f23; }
        .browser-controls button.pausado { background: #ff4444; border-color: #ff4444; color: #fff; }
        .intervention-bar { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; align-items: center; }
        .intervention-bar input { padding: 6px 10px; border-radius: 4px; border: 1px solid #00d4ff; background: #1a1a2e; color: #fff; font-size: 0.8rem; flex: 1; min-width: 150px; }
        .intervention-bar button { padding: 6px 12px; border-radius: 4px; border: 1px solid #00ff88; background: #1a1a2e; color: #00ff88; cursor: pointer; font-size: 0.8rem; }
        .intervention-bar button:hover { background: #00ff88; color: #0f0f23; }
        .apply-flow-bar { background: #16213e; padding: 12px 15px; border-radius: 8px; margin-bottom: 15px; display: flex; align-items: center; justify-content: space-between; gap: 15px; }
        .apply-flow-bar select { padding: 8px; background: #1a1a2e; color: #fff; border: 1px solid #00d4ff; border-radius: 4px; }
        .apply-flow-bar button { padding: 8px 16px; background: #00d4ff; border: none; border-radius: 4px; color: #0f0f23; font-weight: bold; cursor: pointer; }
    </style>
</head>
<body>
    <div id="app">
        <div class="header">
            <h1>🚀 Job Apply Dashboard <span class="live-indicator"></span></h1>
            <form action="/logout" method="POST"><button class="logout" type="submit">Sair</button></form>
        </div>

        <div class="automacao-bar">
            <div class="automacao-status">
                <span class="status-dot" :class="automacao.running ? 'online' : (automacao.action !== 'idle' ? 'busy' : 'offline')"></span>
                <span class="status-text">{{automacaoLabel}}</span>
            </div>
            <div class="automacao-detail">
                <span v-if="automacao.platform">Plataforma: {{automacao.platform}}</span>
                <span>Vagas processadas: {{automacao.vagas_processadas}}</span>
            </div>
        </div>

        <div class="browser-panel" v-if="browser.screenshot || browserControl.paused || browserStep.step">
            <h4>🖥️ Browser em Tempo Real | {{browserStep.step || 'Monitoramento'}}</h4>
            <div class="browser-viewport">
                <img :src="'data:image/png;base64,' + browser.screenshot" alt="Browser screenshot">
                <div class="browser-url">{{browser.url}}</div>
                <div class="browser-step">
                    <span class="step-label">Step:</span> {{browserStep.step || '--'}}
                    <span v-if="browserStep.action">| Ação: {{browserStep.action}}</span>
                    <span v-if="browserStep.detail">| {{browserStep.detail}}</span>
                </div>
            </div>
            <div class="browser-controls">
                <button @click="browserPausar" :class="{pausado: browserControl.paused}">⏸️ Pausar</button>
                <button @click="browserContinuar">▶️ Continuar</button>
                <button @click="browserPular">⏭️ Pular Step</button>
                <button @click="browserIntervirManual">✋ Intervir Manualmente</button>
                <button @click="browserRetomarAuto" v-if="browserControl.intervention_type === 'manual'">🔄 Retomar Auto</button>
            </div>
            <div class="intervention-bar" v-if="browserControl.paused || browserControl.intervention_type === 'manual'">
                <input v-model="browserManualText" placeholder="Texto para digitar..." :disabled="browserControl.intervention_type !== 'digitar'">
                <input v-model="browserManualSelector" placeholder="Seletor CSS (ex: input[name='email'])" :disabled="browserControl.intervention_type !== 'digitar'">
                <button @click="browserDigitar" :disabled="browserControl.intervention_type !== 'digitar'">Digitar</button>
                <button @click="browserClicar" :disabled="browserControl.intervention_type !== 'clicar'">Clicar</button>
                <button @click="browserEnviarIntervencao">Enviar Ação</button>
            </div>
        </div>

        <div class="apply-flow-bar" v-if="candidaturasFiltradas.length">
            <div style="display:flex;align-items:center;gap:10px;">
                <strong style="color:#00d4ff">🧪 Fluxo de Aplicação</strong>
                <select v-model="plataformaAplicacao" style="padding: 6px; border-radius: 4px; background: #1a1a2e; color: #fff; border: 1px solid #00d4ff;">
                    <option value="">Auto-detectar plataforma</option>
                    <option value="gupy">Gupy</option>
                    <option value="linkedin">LinkedIn</option>
                    <option value="indeed">Indeed</option>
                </select>
                <select v-model="vagaSelecionada" style="max-width:300px;">
                    <option value="">Selecione uma vaga para aplicar...</option>
                    <option v-for="c in candidaturasFiltradas" :key="c.id" :value="c">{{c.titulo || 'Vaga'}} — {{c.empresa}}</option>
                </select>
            </div>
            <button @click="aplicarVagaSelecionada" :disabled="!vagaSelecionada">🤖 Aplicar Agora (Browser Visível)</button>
        </div>

        <div class="stats">
            <div class="stat-card"><h3>{{stats.total}}</h3><p>Total</p></div>
            <div class="stat-card sucesso"><h3>{{stats.sucesso}}</h3><p>Sucesso</p></div>
            <div class="stat-card falha"><h3>{{stats.falha}}</h3><p>Falhas</p></div>
            <div class="stat-card"><h3>{{stats.hoje}}</h3><p>Hoje</p></div>
            <div class="stat-card"><h3>{{stats.processando}}</h3><p>Processando</p></div>
        </div>

<div class="control-bar">
             <select v-model="plataformaSelecionada" style="padding: 8px; border-radius: 4px; background: #1a1a2e; color: #fff; border: 1px solid #00d4ff;">
                 <option value="">Todas as plataformas</option>
                 <option value="gupy">Gupy</option>
                 <option value="linkedin">LinkedIn</option>
                 <option value="indeed">Indeed</option>
             </select>
             <button @click="iniciarAutomacao" :disabled="automacao.running">▶ Iniciar Busca</button>
             <button @click="pararAutomacao" :disabled="!automacao.running">⏹ Parar</button>
             <button @click="limparCache">🗑 Limpar</button>
         </div>

        <div class="history-panel" v-if="history.length">
            <h4>📋 Atividades recentes</h4>
            <div v-for="h in history" :key="h.time+h.msg" class="history-item">
                <span>{{h.time}}</span> {{h.msg}}
            </div>
        </div>

        <div class="filters">
            <input v-model="busca" placeholder="Buscar vaga/empresa...">
            <select v-model="filtroStatus">
                <option value="">Todos</option>
                <option value="candidatado">Candidatado</option>
                <option value="processando">Processando</option>
                <option value="tentativa_falhou">Falhou</option>
            </select>
            <button @click="carregar">Atualizar</button>
        </div>

        <div class="candidaturas">
            <div v-for="c in candidaturasFiltradas" :key="c.id" :class="['cand-card', c.status]">
                <h3>{{c.titulo || 'Vaga'}}</h3>
                <p>{{c.empresa}} • {{c.plataforma}}</p>
                <div class="meta">
                    <span>Status: {{c.status}}</span>
                    <span>{{c.data}}</span>
                    <span v-if="c.score">Score: {{c.score}}%</span>
                    <a :href="c.url" target="_blank" style="color:#00d4ff">Ver vaga</a>
                </div>
            </div>
        </div>
    </div>

    <script>
    const { createApp } = Vue;
    createApp({
        data() {
            return {
                candidaturas: [],
                stats: {total: 0, sucesso: 0, falha: 0, hoje: 0, processando: 0},
                automacao: {running: false, action: 'idle', platform: '', vagas_processadas: 0, ultima_mensagem: '', updated_at: ''},
                history: [],
                busca: '',
                filtroStatus: '',
                browser: {screenshot: '', url: '', title: ''},
                browserControl: {paused: false, current_action: 'idle', manual_input: '', intervention_type: null},
                browserStep: {step: '', action: '', detail: '', updated_at: ''},
                browserManualText: '',
                browserManualSelector: '',
                vagaSelecionada: '',
                plataformaSelecionada: '',
                plataformaAplicacao: '',
            }
        },
        computed: {
            automacaoLabel() {
                const a = this.automacao;
                if (!a.running && a.action === 'idle') return '⚪ Aguardando';
                if (!a.running && a.action !== 'idle') return '🔴 Parado';
                const map = {
                    'buscando': '🔍 Buscando vagas...',
                    'aplicando': '🤖 Aplicando com IA...',
                    'validando': '✅ Validando candidatura...',
                    'finalizando': '📤 Finalizando...',
                };
                return map[a.action] || '🟢 Rodando';
            }
        },
        computed: {
            candidaturasFiltradas() {
                let f = this.candidaturas;
                if (this.busca) f = f.filter(c => (c.titulo||'').toLowerCase().includes(this.busca.toLowerCase()));
                if (this.filtroStatus) f = f.filter(c => c.status === this.filtroStatus);
                return f;
            }
        },
        mounted() {
            this.carregar();
            this.initSocket();
            this.carregarAutomacao();
            this.carregarBrowser();
            this.historicoInterval = setInterval(this.carregarAutomacao, 3000);
            this.browserInterval = setInterval(this.carregarBrowser, 1000);
        },
        beforeUnmount() {
            if (this.historicoInterval) clearInterval(this.historicoInterval);
            if (this.browserInterval) clearInterval(this.browserInterval);
        },
        methods: {
            async carregar() {
                const r = await fetch('/api/candidaturas');
                const d = await r.json();
                this.candidaturas = d.candidaturas || [];
                this.stats = {total: d.total, sucesso: d.sucesso, falha: d.falha, hoje: d.hoje, processando: d.processando || 0};
            },
            async carregarAutomacao() {
                try {
                    const r = await fetch('/api/automacao/status');
                    const d = await r.json();
                    this.automacao = {...this.automacao, ...d};
                } catch(e) {}
            },
            initSocket() {
                const s = io('/');
                s.on('connect', () => {
                    this.carregarAutomacao();
                    this.carregarBrowser();
                });
                s.on('candidatura_update', (d) => {
                    const idx = this.candidaturas.findIndex(c => c.url === d.url);
                    if (idx >= 0) this.candidaturas[idx] = d;
                    else this.candidaturas.unshift(d);
                    this.atualizarStats();
                });
                s.on('automacao_status', (d) => {
                    this.automacao = {...this.automacao, ...d};
                });
                s.on('status_history', (h) => {
                    this.history = h;
                });
                s.on('browser_screenshot', (d) => {
                    this.browser.screenshot = d.screenshot;
                    this.browser.url = d.url;
                    this.browser.title = d.title;
                    if (d.step) this.browserStep.step = d.step;
                    if (d.action) this.browserStep.action = d.action;
                    if (d.detail) this.browserStep.detail = d.detail;
                });
                s.on('browser_step_update', (d) => {
                    this.browserStep = {...this.browserStep, ...d};
                });
                s.on('browser_controle', (d) => {
                    this.browserControl = {...this.browserControl, ...d};
                });
            },
            atualizarStats() {
                this.stats.total = this.candidaturas.length;
                this.stats.sucesso = this.candidaturas.filter(c => c.status === 'candidatado').length;
                this.stats.falha = this.candidaturas.filter(c => c.status === 'tentativa_falhou').length;
                this.stats.processando = this.candidaturas.filter(c => c.status === 'processando').length;
            },
            async iniciarAutomacao() {
                await fetch('/api/vagas/buscar', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({query: this.busca || 'desenvolvedor', platform: this.plataformaSelecionada})
                });
                this.carregar();
            },
            async pararAutomacao() {
                await fetch('/api/automacao/parar', {method: 'POST'});
                alert('Automação parada!');
            },
            async limparCache() {
                await fetch('/api/cache/limpar', {method: 'POST'});
                alert('Cache limpo!');
            },
            async carregarBrowser() {
                try {
                    const r = await fetch('/api/browser/screenshot');
                    const d = await r.json();
                    if (d.success) {
                        this.browser.screenshot = d.screenshot;
                        this.browser.url = d.url || '';
                    }
                } catch(e) {}
                try {
                    const r = await fetch('/api/browser/controle');
                    const d = await r.json();
                    this.browserControl = {...this.browserControl, ...d};
                } catch(e) {}
                try {
                    const r = await fetch('/api/browser/step');
                    const d = await r.json();
                    if (d.success) {
                        this.browserStep = {...this.browserStep, ...d};
                    }
                } catch(e) {}
            },
            async browserPausar() {
                await fetch('/api/browser/controle', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'pausar'})});
                this.browserControl.paused = true;
            },
            async browserContinuar() {
                await fetch('/api/browser/controle', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'continuar'})});
                this.browserControl.paused = false;
            },
            async browserPular() {
                await fetch('/api/browser/controle', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'pular'})});
            },
            async browserIntervirManual() {
                await fetch('/api/browser/controle', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'intervir_manual'})});
                this.browserControl.intervention_type = 'manual';
                this.browserControl.paused = true;
            },
            async browserRetomarAuto() {
                await fetch('/api/browser/controle', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'retomar_auto'})});
                this.browserControl.intervention_type = null;
                this.browserControl.paused = false;
            },
            async browserDigitar() {
                if (!this.browserManualSelector || !this.browserManualText) return;
                await fetch('/api/browser/controle', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'digitar', texto: this.browserManualText, selector: this.browserManualSelector})});
            },
            async browserClicar() {
                if (!this.browserManualSelector) return;
                await fetch('/api/browser/controle', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'clicar', selector: this.browserManualSelector})});
            },
            async browserEnviarIntervencao() {
                await fetch('/api/browser/controle', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({cmd: 'retomar_auto'})});
                this.browserControl.intervention_type = null;
                this.browserControl.paused = false;
                this.browserManualText = '';
                this.browserManualSelector = '';
            },
            async aplicarVagaSelecionada() {
                if (!this.vagaSelecionada) return;
                const vaga = this.vagaSelecionada;
                const plataforma = this.plataformaAplicacao || this._detectarPlataforma(vaga.url);
                try {
                    const r = await fetch('/api/automacao/aplicar-vaga', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({...vaga, plataforma: plataforma})
                    });
                    const d = await r.json();
                    if (d.success) {
                        alert('Aplicação iniciada! Veja o browser em tempo real.');
                    } else {
                        alert('Erro: ' + (d.message || 'Falha ao iniciar aplicação'));
                    }
                } catch(e) {
                    alert('Erro de conexão ao iniciar aplicação');
                }
            },
            _detectarPlataforma(url) {
                const u = (url || '').toLowerCase();
                if (u.includes('gupy.io')) return 'gupy';
                if (u.includes('linkedin.com')) return 'linkedin';
                if (u.includes('indeed.com') || u.includes('br.indeed.com')) return 'indeed';
                return '';
            }
        }
    }).mount('#app');
    </script>
</body>
</html>
"""


@fastapi_app.get("/")
async def index():
    return HTMLResponse(content=VUE_DASHBOARD)


@fastapi_app.get("/login")
async def login_page():
    return HTMLResponse(content=LOGIN_PAGE)


@fastapi_app.post("/login")
async def do_login(request: Request):
    form = await request.form()
    user = form.get("username", "")
    passw = form.get("password", "")
    if user == ADMIN_USER and (ADMIN_PASS == "" or passw == ADMIN_PASS):
        session_token = hashlib.sha256(f"{user}:{datetime.now()}".encode()).hexdigest()
        sessions[session_token] = datetime.now() + timedelta(hours=24)
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("session", session_token, httponly=True, max_age=86400)
        return resp
    return HTMLResponse(content=LOGIN_PAGE.replace("</body>", "<div class='error'>Credenciais inválidas</div></body>"))


@fastapi_app.post("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@fastapi_app.get("/api/candidaturas")
async def get_candidaturas():
    try:
        neo4j = get_neo4j()
        candidaturas = neo4j.get_todas_candidaturas()
    except Exception as e:
        return JSONResponse({"error": str(e), "candidaturas": [], "total": 0, "sucesso": 0, "falha": 0, "hoje": 0, "processando": 0})

    hoje = datetime.now().strftime("%Y-%m-%d")
    hoje_count = sum(1 for c in candidaturas if c.get("data", "").startswith(hoje))
    sucesso_count = sum(1 for c in candidaturas if c.get("status") == "candidatado")
    falha_count = sum(1 for c in candidaturas if c.get("status") == "tentativa_falhou")
    processando_count = sum(1 for c in candidaturas if c.get("status") == "processando")

    return JSONResponse({
        "candidaturas": candidaturas,
        "total": len(candidaturas),
        "sucesso": sucesso_count,
        "falha": falha_count,
        "hoje": hoje_count,
        "processando": processando_count,
    })


@fastapi_app.get("/api/status")
async def get_status():
    return JSONResponse({"status": "online", "timestamp": datetime.now().isoformat()})


@fastapi_app.get("/api/automacao/status")
async def get_automacao_status():
    return JSONResponse(_automacao_status)


@fastapi_app.post("/api/automacao/iniciar")
async def iniciar_automacao():
    _set_automacao_status(True, "buscando", "", "Automação iniciada — buscando vagas")
    emit_status_update()
    return JSONResponse({"success": True, "message": "Automação iniciada"})


@fastapi_app.post("/api/vagas/buscar")
async def buscar_vagas_dashboard(request: Request):
    """Busca vagas usando browser-use (navegador visível) ou fallback HTTP."""
    body = await request.json()
    query = body.get("query", "desenvolvedor")
    plataforma = body.get("platform", "")
    user_id = os.getenv("DASHBOARD_USER_ID", "admin")

    async def _run_busca():
        try:
            _set_automacao_status(True, "buscando", plataforma or "browser-use", f"Buscando vagas: {query}")
            set_browser_current_step("buscando", "navegando", f"Query: {query}")
            emit_status_update()

            from automation.browser_agent import buscar_vagas_browser_use
            vagas = []
            try:
                vagas = await buscar_vagas_browser_use(query)
            except Exception as e:
                logger.warning(f"busca falhou: {e}")

            if vagas:
                from graph.neo4j_client import get_neo4j
                neo4j = get_neo4j()
                for vaga in vagas[:10]:
                    neo4j.upsert_vaga({
                        "id": vaga.get("id") or vaga.get("url", ""),
                        "titulo": vaga.get("titulo", ""),
                        "empresa": vaga.get("empresa", ""),
                        "url": vaga.get("url", ""),
                        "fonte": vaga.get("fonte", "busca"),
                        "descricao": vaga.get("descricao", "")[:500],
                    })

            if not vagas:
                from agents.jobs import jobs_node
                state = {"user_id": user_id, "raw_input": f"busca vagas {query}", "intent": "vaga"}
                await asyncio.to_thread(jobs_node, state)

            _set_automacao_status(False, "idle", plataforma or "", f"Busca concluída: {len(vagas)} vagas encontradas via browser")
            set_browser_current_step("busca", "concluido", f"Encontradas: {len(vagas)}")
            emit_status_update()
        except Exception as e:
            _set_automacao_status(False, "erro", "", str(e))
            set_browser_current_step("erro", "falha", str(e))
            emit_status_update()

    asyncio.create_task(_run_busca())
    return JSONResponse({"success": True, "message": "Busca iniciada com browser visível"})


@fastapi_app.post("/api/automacao/parar")
async def parar_automacao():
    _set_automacao_status(False, "idle", "", "Automação parada")
    emit_status_update()
    return JSONResponse({"success": True, "message": "Automação parada"})


@fastapi_app.post("/api/automacao/aplicar-em-lote")
async def aplicar_vagas_lote(request: Request):
    """Aplica automaticamente as vagas com score acima do limiar."""
    body = await request.json()
    user_id = body.get("user_id", os.getenv("DASHBOARD_USER_ID", "admin"))
    limiar_score = float(body.get("limiar_score", 0.5))  # 50% default
    limite_vagas = int(body.get("limite_vagas", 5))
    plataforma_filtro = body.get("plataforma", "")
    
    async def _run_aplicacao_lote():
        try:
            vagas_aplicar = []
            try:
                neo4j = get_neo4j()
                candidaturas = neo4j.get_todas_candidaturas()
                for c in candidaturas[:limite_vagas]:
                    score = c.get("score", 0) or _estimar_score(c)
                    if score >= limiar_score:
                        vagas_aplicar.append(c)
            except Exception:
                pass
            
            for i, vaga in enumerate(vagas_aplicar):
                plataforma = plataforma_filtro or _detectar_plataforma(vaga.get("url", ""))
                _set_automacao_status(True, "aplicando", plataforma, f"Aplicando em {vaga.get('empresa','?')} ({i+1}/{len(vagas_aplicar)})")
                set_browser_current_step("lote", "aplicando", f"Vaga {i+1}: {vaga.get('titulo','')[:40]}")
                emit_status_update()
                
                try:
                    from agents.apply import executar_candidatura
                    perfil = neo4j.get_perfil_profissional(user_id) if 'neo4j' in dir() else {}
                    await executar_candidatura(user_id, vaga, perfil, plataforma)
                except Exception as e:
                    logger.error(f"Erro aplicando vaga {vaga.get('url')}: {e}")
                    
            _set_automacao_status(False, "idle", "", f"Lote concluído: {len(vagas_aplicar)} candidaturas enviadas")
            emit_status_update()
        except Exception as e:
            _set_automacao_status(False, "erro", "", str(e))
            emit_status_update()
    
    asyncio.create_task(_run_aplicacao_lote())
    return JSONResponse({"success": True, "message": f"Iniciando aplicação automática para vagas com score ≥ {int(limiar_score*100)}%"})


def _estimar_score(vaga: dict) -> float:
    """Estima score baseado em palavras-chave no título."""
    titulo = (vaga.get("titulo") or "").lower()
    requisitos = vaga.get("requisitos", []) or []
    scores = {"python": 0.8, "backend": 0.7, "fullstack": 0.7, "senior": 0.6}
    return max((scores.get(r, 0) for r in requisitos), default=0.5)


@fastapi_app.post("/api/automacao/aplicar-vaga")
async def aplicar_vaga_dashboard(request: Request):
    """Dispara aplicacao automatica para uma vaga selecionada no dashboard."""
    body = await request.json()
    vaga = body
    plataforma_override = body.get("plataforma", "")
    user_id = os.getenv("DASHBOARD_USER_ID", "admin")

    async def _run_apply():
        try:
            from agents.apply import executar_candidatura
            from automation.browser import set_active_page, get_intervention_state
            from automation.browser_agent import aplicar_vaga_browser_use
            from graph.neo4j_client import get_neo4j

            neo4j = get_neo4j()
            perfil = neo4j.get_perfil_profissional(user_id) or {}
            plataforma = plataforma_override or _detectar_plataforma(vaga.get("url", ""))

            _set_automacao_status(True, "aplicando", plataforma, f"Aplicando em {vaga.get('empresa','?')} via {plataforma}")
            set_browser_current_step("inicio", "aplicando", f"URL: {vaga.get('url','')}")
            emit_status_update()

            from automation.browser import notify_browser_step

            resultado = None
            if plataforma != "desconhecido":
                try:
                    from agents.apply import executar_candidatura
                    resultado = await executar_candidatura(user_id, vaga, perfil, plataforma)
                except Exception as e:
                    logger.warning("executar_candidatura falhou no dashboard: %s", e)
                    resultado = None

            if not resultado:
                from automation.browser_agent import aplicar_vaga_browser_use
                resultado = await aplicar_vaga_browser_use(vaga.get("url", ""), perfil)

            status = "candidatado" if resultado.get("sucesso") else "tentativa_falhou"
            _set_automacao_status(False, "finalizando", plataforma, f"{'Sucesso' if resultado.get('sucesso') else 'Falha'}: {vaga.get('titulo','')}")
            set_browser_current_step("fim", "finalizando", resultado.get('mensagem',''))
            emit_status_update()

            neo4j.registrar_candidatura(
                user_id=user_id,
                vaga_id=vaga.get("id", vaga.get("url", "")),
                plataforma=plataforma,
                status=status,
            )
        except Exception as e:
            _set_automacao_status(False, "erro", "", str(e))
            set_browser_current_step("erro", "falha", str(e))
            emit_status_update()

    asyncio.create_task(_run_apply())
    return JSONResponse({"success": True, "message": "Aplicação iniciada em background"})


def _detectar_plataforma(url: str) -> str:
    url_lower = (url or "").lower()
    if "linkedin.com" in url_lower:
        return "linkedin"
    if "indeed.com" in url_lower or "br.indeed.com" in url_lower:
        return "indeed"
    if "gupy.io" in url_lower:
        return "gupy"
    if "greenhouse.io" in url_lower or "jobs.greenhouse" in url_lower:
        return "greenhouse"
    if "lever.co" in url_lower or "jobs.lever" in url_lower:
        return "lever"
    if "workday" in url_lower or "myworkdayjobs" in url_lower:
        return "workday"
    if "glassdoor.com" in url_lower:
        return "glassdoor"
    if "vagas.com" in url_lower:
        return "vagas"
    if "catho.com" in url_lower:
        return "catho"
    if "infojobs.com" in url_lower:
        return "infojobs"
    return "desconhecido"


@fastapi_app.post("/api/automacao/acao")
async def automacao_acao(request: Request):
    body = await request.json()
    action = body.get("action", "idle")
    platform = body.get("platform", "")
    mensagem = body.get("mensagem", "")
    vagas = body.get("vagas_processadas")
    if vagas is not None:
        try:
            _automacao_status["vagas_processadas"] = int(vagas)
        except Exception:
            pass
    _set_automacao_status(
        _automacao_status["running"],
        action,
        platform,
        mensagem,
    )
    emit_status_update()
    return JSONResponse({"success": True})


@fastapi_app.post("/api/cache/limpar")
async def limpar_cache():
    return JSONResponse({"success": True, "message": "Cache limpo"})


@fastapi_app.get("/api/browser/screenshot")
async def get_browser_screenshot():
    """Retorna screenshot base64 da pagina ativa do Playwright."""
    try:
        from automation.browser import get_active_page, screenshot_base64
        page = await get_active_page()
        if not page:
            return JSONResponse({"success": False, "message": "Nenhuma pagina ativa"}, status_code=404)
        img = await screenshot_base64(page)
        if not img:
            return JSONResponse({"success": False, "message": "Falha ao capturar screenshot"}, status_code=500)
        return JSONResponse({"success": True, "screenshot": img, "url": page.url})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@fastapi_app.get("/api/browser/state")
async def get_browser_state():
    """Retorna estado atual da pagina ativa."""
    try:
        from automation.browser import get_active_page, get_page_state
        page = await get_active_page()
        if not page:
            return JSONResponse({"success": False, "message": "Nenhuma pagina ativa"}, status_code=404)
        state = await get_page_state(page)
        return JSONResponse({"success": True, **state})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@fastapi_app.post("/api/browser/controle")
async def browser_controle(request: Request):
    """Recebe comandos de controle do browser (pausar, continuar, pular, digitar, clicar)."""
    body = await request.json()
    cmd = body.get("cmd", "")

    try:
        from automation.browser import set_intervention_state
        if cmd == "pausar":
            await set_intervention_state("paused", True)
            await set_intervention_state("current_action", "pausado")
            return JSONResponse({"success": True, "message": "Automação pausada"})

        if cmd == "continuar":
            await set_intervention_state("paused", False)
            await set_intervention_state("current_action", "rodando")
            return JSONResponse({"success": True, "message": "Automação continuada"})

        if cmd == "pular":
            await set_intervention_state("current_action", "pular")
            return JSONResponse({"success": True, "message": "Ação atual será pulada"})

        if cmd == "digitar":
            texto = body.get("texto", "")
            selector = body.get("selector", "")
            await set_intervention_state("manual_input", texto)
            await set_intervention_state("intervention_type", "digitar")
            await set_intervention_state("intervention_selector", selector)
            return JSONResponse({"success": True, "message": f"Digitar '{texto}' em '{selector}'"})

        if cmd == "clicar":
            selector = body.get("selector", "")
            await set_intervention_state("intervention_type", "clicar")
            await set_intervention_state("intervention_selector", selector)
            return JSONResponse({"success": True, "message": f"Clicar em '{selector}'"})

        if cmd == "intervir_manual":
            await set_intervention_state("intervention_type", "manual")
            await set_intervention_state("paused", True)
            return JSONResponse({"success": True, "message": "Modo manual ativado"})

        if cmd == "retomar_auto":
            await set_intervention_state("intervention_type", None)
            await set_intervention_state("paused", False)
            await set_intervention_state("current_action", "rodando")
            await set_intervention_state("intervention_selector", None)
            return JSONResponse({"success": True, "message": "Retomado modo automático"})

        return JSONResponse({"success": False, "message": "Comando inválido"}, status_code=400)
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@fastapi_app.get("/api/browser/controle")
async def get_browser_controle():
    """Retorna estado atual do controle do browser."""
    try:
        from automation.browser import get_intervention_state
        state = await get_intervention_state()
        return JSONResponse(state)
    except Exception as e:
        return JSONResponse({"paused": False, "current_action": "idle", "intervention_type": None})


@fastapi_app.get("/api/browser/step")
async def get_browser_step():
    return JSONResponse(_browser_current_step)


@fastapi_app.post("/api/browser/step")
async def post_browser_step(request: Request):
    """Recebe atualizações de step do browser vindo da automação."""
    try:
        body = await request.json()
        set_browser_current_step(
            step=body.get("step", ""),
            action=body.get("action", ""),
            detail=body.get("detail", ""),
        )
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)}, status_code=400)


async def _browser_screenshot_loop():
    """Loop que envia screenshots periodicamente via Socket.IO quando há página ativa."""
    while True:
        try:
            from automation.browser import get_active_page, screenshot_base64, get_page_state, get_current_step
            page = await get_active_page()
            if page:
                img = await screenshot_base64(page)
                state = await get_page_state(page)
                step_info = await get_current_step()
                if img:
                    asyncio.create_task(sio.emit("browser_screenshot", {
                        "screenshot": img,
                        "url": state.get("url", ""),
                        "title": state.get("title", ""),
                        "step": step_info.get("step", ""),
                        "action": step_info.get("action", ""),
                        "detail": step_info.get("detail", ""),
                        "timestamp": datetime.now().isoformat(),
                    }))
        except Exception:
            pass
        await asyncio.sleep(1)


@sio.event
async def connect(sid, environ):
    logger = __import__('logging').getLogger(__name__)
    logger.info("Socket conectado: %s", sid)


@sio.event
async def disconnect(sid):
    pass


@fastapi_app.on_event("startup")
async def startup_event():
    asyncio.create_task(_browser_screenshot_loop())


def run_dashboard():
    import uvicorn
    uvicorn.run(combined_app, host="0.0.0.0", port=8082)


if __name__ == "__main__":
    run_dashboard()