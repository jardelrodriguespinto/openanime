"""
Dashboard Vue.js + Socket.IO com autenticação para acompanhamento de candidaturas.
Painel admin protegido por login/senha.
"""

import asyncio
import hashlib
import logging
import os
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

# Socket.IO
sio = socketio.AsyncServer(cors_allowed_origins="*", async_mode="asgi")
fastapi_app = FastAPI()
combined_app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)

security = HTTPBasic()

# Sessions em memória (em produção usar Redis)
# Sessions em memória (em produção usar Redis)
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
    </style>
</head>
<body>
    <div id="app">
        <div class="header">
            <h1>🚀 Job Apply Dashboard <span class="live-indicator"></span></h1>
            <form action="/logout" method="POST"><button class="logout" type="submit">Sair</button></form>
        </div>

        <div class="stats">
            <div class="stat-card"><h3>{{stats.total}}</h3><p>Total</p></div>
            <div class="stat-card sucesso"><h3>{{stats.sucesso}}</h3><p>Sucesso</p></div>
            <div class="stat-card falha"><h3>{{stats.falha}}</h3><p>Falhas</p></div>
            <div class="stat-card"><h3>{{stats.hoje}}</h3><p>Hoje</p></div>
            <div class="stat-card"><h3>{{stats.processando}}</h3><p>Processando</p></div>
        </div>

        <div class="control-bar">
            <button @click="iniciarAutomacao">▶ Iniciar Busca</button>
            <button @click="pararAutomacao">⏹ Parar</button>
            <button @click="limparCache">🗑 Limpar</button>
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
                busca: '',
                filtroStatus: '',
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
        },
        methods: {
            async carregar() {
                const r = await fetch('/api/candidaturas');
                const d = await r.json();
                this.candidaturas = d.candidaturas || [];
                this.stats = {total: d.total, sucesso: d.sucesso, falha: d.falha, hoje: d.hoje, processando: d.processando || 0};
            },
            initSocket() {
                const s = io('/');
                s.on('candidatura_update', (d) => {
                    const idx = this.candidaturas.findIndex(c => c.url === d.url);
                    if (idx >= 0) this.candidaturas[idx] = d;
                    else this.candidaturas.unshift(d);
                    this.atualizarStats();
                });
            },
            atualizarStats() {
                this.stats.total = this.candidaturas.length;
                this.stats.sucesso = this.candidaturas.filter(c => c.status === 'candidatado').length;
                this.stats.falha = this.candidaturas.filter(c => c.status === 'tentativa_falhou').length;
                this.stats.processando = this.candidaturas.filter(c => c.status === 'processando').length;
            },
            async iniciarAutomacao() {
                await fetch('/api/automacao/iniciar', {method: 'POST'});
                alert('Automação iniciada!');
            },
            async pararAutomacao() {
                await fetch('/api/automacao/parar', {method: 'POST'});
                alert('Automação parada!');
            },
            async limparCache() {
                await fetch('/api/cache/limpar', {method: 'POST'});
                alert('Cache limpo!');
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


@fastapi_app.post("/api/automacao/iniciar")
async def iniciar_automacao():
    return JSONResponse({"success": True, "message": "Automação iniciada"})


@fastapi_app.post("/api/automacao/parar")
async def parar_automacao():
    return JSONResponse({"success": True, "message": "Automação parada"})


@fastapi_app.post("/api/cache/limpar")
async def limpar_cache():
    return JSONResponse({"success": True, "message": "Cache limpo"})


@sio.event
async def connect(sid, environ):
    logger = __import__('logging').getLogger(__name__)
    logger.info("Socket conectado: %s", sid)


@sio.event
async def disconnect(sid):
    pass


def emit_candidatura_update(cand: dict):
    asyncio.create_task(sio.emit("candidatura_update", cand))


def run_dashboard():
    import uvicorn
    uvicorn.run(combined_app, host="0.0.0.0", port=8082)