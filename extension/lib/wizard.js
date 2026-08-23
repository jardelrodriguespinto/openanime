// Wizard genérico multi-step p/ SPAs (Gupy/GeekHunter/Senior). Preenche o container,
// avança por texto de botão, e PARA antes do envio final (humano confirma). Exposto
// em window.OA.rodarWizard. Retorna 'enviado' | 'pausa' | 'parou' | 'falhou'.
(function () {
  const OA = window.OA;
  if (!OA || OA.rodarWizard) return;

  async function rodarWizard(getContainer, opts) {
    const {
      avancar = ["avançar", "continuar", "próximo", "next", "continue"],
      finalizar = ["finalizar", "enviar candidatura", "enviar", "submit", "concluir"],
      finalizarSel = [], // seletores CSS do botão de envio (ex.: #dialog-give-up-... do Gupy)
      sucessoFrases = [], // frases ESPECÍFICAS de "candidatura enviada" (verifica o envio)
      pausarAntesEnvio = true,
      isRunning = async () => true,
      onStatus = () => {},
      maxSteps = 16,
      ctx = {},
      preencher = null, // hook por-plataforma ANTES do preenchimento genérico (ex.: Gupy)
      preferUltimo = false, // Gupy: página repete o botão (sticky + rodapé) → clica o ÚLTIMO
      avancarSel = [], // seletores CSS EXATOS do botão de avançar (elimina ambiguidade de
                      // "continuar" casando em 2 botões — ex.: Gupy usa name='saveAndContinueButton')
      finalizarPrioridade = false, // finalizarSel visível+habilitado TEM prioridade sobre avançar
    } = opts || {};

    // Frases genéricas de sucesso — somadas às específicas de cada plataforma. Gupy/
    // GeekHunter variam o texto pós-envio ("Candidatura realizada!", "Inscrição
    // concluída"…) e sem frase casável o envio VERIFICADO virava "incerto".
    const SUCESSO_GENERICAS = ["candidatura enviada", "candidatura realizada", "inscrição realizada", "inscrição concluída", "application submitted", "you have applied", "you've applied", "obrigado pela sua candidatura"];

    const log = (...a) => { try { console.log("[AutoApply][wizard]", ...a); } catch (_) {} };
    // Delays HUMANOS: sleeps randômicos p/ a automação não ficar RÁPIDA DEMAIS (Gupy/GeekHunter
    // avançavam antes do campo/validação React assentar → "preenche e não dá certo").
    const rint = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
    const rsleep = (a, b) => OA.sleep(rint(a, b));
    const habil = (b) => b && !b.disabled && b.getAttribute("aria-disabled") !== "true";
    const assinatura = () => location.href + "|" + document.querySelectorAll("input,select,textarea,button").length + "|" + (document.body.innerText || "").length;

    // Diagnóstico: quais campos obrigatórios estão VAZIOS (é o que desabilita o botão).
    // Espelha a heurística de "obrigatório" do forms.js: [required]/[aria-required] OU
    // "*" no rótulo/wrapper — o Gupy marca as "Perguntas criadas pela empresa" só com "*"
    // num <span>/<h3>, SEM atributo. Sem cobrir esse caso, o campo travava o botão mas NÃO
    // aparecia aqui, e a mensagem "Falta preencher…" saía vazia (sem apontar o culpado).
    // NEUTRO: só alimenta log/status, não altera preenchimento.
    const marcadoObrig = (el) => el.required || el.getAttribute("aria-required") === "true"
      || /\*/.test(OA.labelFor(el) || "")
      || /\*|obrigat/i.test((el.closest("[class*='FormControl'], .form-group, li, fieldset, div")?.innerText || "").slice(0, 120));
    const nomeCampo = (el) => (OA.headingLabel(el) || OA.labelFor(el) || el.name || el.tagName.toLowerCase()).slice(0, 45);
    const vazioTexto = (el) => el.tagName.toLowerCase() === "select"
      ? (!el.value || /selecione|escolha|^--/i.test(el.options?.[el.selectedIndex]?.text || ""))
      : !(el.value || "").trim();
    const camposVazios = (container) => {
      const out = [];
      // (a/b) text/number/textarea/select obrigatórios (por atributo OU por "*") vazios
      for (const el of container.querySelectorAll("input:not([type='radio']):not([type='checkbox']):not([type='hidden']):not([type='file']):not([type='submit']):not([type='button']), textarea, select")) {
        try { if (getComputedStyle(el).display === "none") continue; } catch (_) {}
        if (marcadoObrig(el) && vazioTexto(el)) out.push(nomeCampo(el));
      }
      // (c) grupos de radio (obrigatório via "*" no h3, sem [required]) sem nada marcado
      const rg = new Map();
      for (const r of container.querySelectorAll("input[type='radio']")) { const k = r.name || "?"; (rg.get(k) || rg.set(k, []).get(k)).push(r); }
      for (const [, radios] of rg) if (!radios.some((r) => r.checked)) out.push(nomeCampo(radios[0]));
      // (d) grupos de checkbox OBRIGATÓRIOS ("selecione ao menos uma") sem nada marcado —
      // o forms.js avalia cada checkbox isolado (IA sim/não) e pode não marcar NENHUM,
      // deixando um grupo obrigatório insatisfeito → botão travado sem campo "vazio" óbvio.
      const cg = new Map();
      for (const c of container.querySelectorAll("input[type='checkbox']")) {
        if (!marcadoObrig(c)) continue;
        // Opções de pergunta MUI do Gupy: cada uma tem name ÚNICO (checkbox-<idQ>-<i>) → agrupar
        // por name marcaria CADA opção não marcada como "vazia". Agrupa pelo índice da pergunta
        // (data-oa-gupy-mui) → conta a pergunta UMA vez, só se NENHUMA opção dela foi marcada.
        const k = c.dataset.oaGupyMui != null ? "muiq:" + c.dataset.oaGupyMui
          : (c.name || (c.closest("fieldset, [role='group']")?.className || "cbgrp"));
        (cg.get(k) || cg.set(k, []).get(k)).push(c);
      }
      for (const [, cbs] of cg) if (!cbs.some((c) => c.checked)) out.push(nomeCampo(cbs[0]));
      return [...new Set(out)];
    };
    // Dump de diagnóstico (o usuário NÃO tem console numa extensão): captura o form travado
    // (URL, campos com name/type/label/obrigatório/valor, botões) → dashboard baixa .txt.
    const dumpDiag = async (motivo, container) => {
      try {
        const root = container || document;
        const botoes = [...document.querySelectorAll("button, a, [role='button']")]
          .filter((b) => OA.isVisible(b)).slice(0, 40)
          .map((b) => ({ txt: (b.innerText || b.getAttribute("aria-label") || "").trim().slice(0, 40), off: !habil(b), name: b.getAttribute("name") || "", id: b.id || "" }));
        const campos = [...root.querySelectorAll("input, select, textarea")].slice(0, 120).map((el) => ({
          tag: el.tagName.toLowerCase(), type: (el.type || "").slice(0, 12), name: (el.getAttribute("name") || "").slice(0, 40), id: (el.id || "").slice(0, 40),
          label: (OA.headingLabel(el) || OA.labelFor(el) || "").slice(0, 70),
          req: !!(el.required || el.getAttribute("aria-required") === "true" || /\*/.test(OA.labelFor(el) || "")),
          val: (el.type === "checkbox" || el.type === "radio") ? (el.checked ? "MARCADO" : "-") : (el.value || "").slice(0, 30),
        }));
        await OA.bg({ type: "debug.push", tag: motivo, data: { url: location.href, title: (document.title || "").slice(0, 90), vazios: camposVazios(root), botoes, campos } });
      } catch (_) {}
    };
    let travado = 0;

    for (let step = 0; step < maxSteps; step++) {
      if (!(await isRunning())) return "parou";
      await rsleep(1200, 2800); // pausa humana entre passos (não em rajada)
      // Banner de cookie/LGPD por cima do form intercepta cliques ("ACEITAR/NÃO,
      // OBRIGADO" do Gupy) → fecha cedo; barato: só casa botão DENTRO de container
      // de consentimento (nunca um "não" do formulário).
      try { OA.fecharBanners?.(); } catch (_) {}
      const container = (typeof getContainer === "function" ? getContainer() : getContainer) || document.body;

      // hook por-plataforma ANTES do preenchimento genérico (ex.: Gupy força "Não" no
      // referral e escolhe a 1ª opção do combobox opcional). NUNCA derruba o wizard.
      try { if (typeof preencher === "function") await preencher(container, step); } catch (e) { log("preencher(hook) erro:", e?.message); }
      // preencher NUNCA pode derrubar o wizard (senão a automação "para" silenciosa).
      try { await OA.preencherCampos(container, { ...ctx, onStatus }); } catch (e) { log("preencherCampos erro:", e?.message); }
      // Espera o React ASSENTAR o que foi preenchido (telefone/validação) ANTES de avançar —
      // era o "preenche mas não dá certo": clicava rápido demais e o campo ainda estava vazio.
      await rsleep(1400, 3000);
      const sigAntes = assinatura();

      // CANDIDATOS A AVANÇAR — definido ANTES do finalizar (a ordem importa). Retorna
      // TODOS os botões de avançar VISÍVEIS, por prioridade. O Gupy repete o botão na
      // MESMA tela (ex.: DOIS "Continuar", sticky + rodapé) e só UM avança → tentamos cada
      // um. (1) avancarSel EXATO (ex.: "Dados adicionais" tem name='saveAndContinueButton');
      // (2) o texto de MAIOR prioridade com botão visível ("salvar e continuar"/"responder
      // agora" antes do "continuar"), preferUltimo põe o do rodapé na frente. O avancarSel
      // NUNCA bloqueia: se não casar (ex.: "Salvar e continuar" sem name/id), cai no texto.
      const candidatosAvancar = () => {
        const out = [];
        if (avancarSel && avancarSel.length) {
          for (const s of avancarSel) { const b = document.querySelector(s); if (b && OA.isVisible(b) && !out.includes(b)) out.push(b); }
        }
        for (const txt of avancar) {
          const vis = OA.findButtonsByText([txt]).filter((b) => OA.isVisible(b));
          if (!vis.length) continue;
          const ord = preferUltimo ? vis.slice().reverse() : vis; // rodapé primeiro
          for (const b of ord) if (!out.includes(b)) out.push(b);
          break; // só o texto de MAIOR prioridade que tenha botão visível
        }
        return out;
      };
      let cands = candidatosAvancar();

      // PRIORIDADE DE FINALIZAR (Gupy): o diálogo terminal ("Personalizar candidatura" |
      // "Finalizar candidatura", #dialog-give-up-...) é o FIM do fluxo, mas botões de
      // avançar da tela DE TRÁS continuam "visíveis" ATRÁS do overlay → o wizard clicava
      // o fundo em loop e "travava em Finalizar candidatura". Seletor EXATO + habilitado
      // = tela final real → zera os candidatos e vai direto pro envio.
      const ehBotaoReal = (b) => b.tagName === "BUTTON" || b.tagName === "A" || b.getAttribute("role") === "button";
      if (finalizarPrioridade) {
        const prio = finalizarSel.map((s) => document.querySelector(s)).find((b) => b && ehBotaoReal(b) && OA.isVisible(b) && habil(b));
        if (prio) { log("step", step, "FINALIZAR prioritário (diálogo terminal)"); cands = []; }
      }

      // FINALIZAR (envio) — SÓ quando NÃO há botão de AVANÇAR visível. Ordem crítica p/ o
      // Gupy: na tela de perguntas o "Salvar e continuar" coexiste com o give-up "Finalizar
      // candidatura"; se finalizasse primeiro, clicava o give-up e o "Salvar e continuar"
      // NUNCA era clicado. Só finaliza quando o avanço acabou — ex.: o diálogo final
      // "Personalizar candidatura" | "Finalizar candidatura" (#dialog-give-up-...), que não
      // tem botão de avançar. Seletor CSS ou texto, no documento INTEIRO.
      if (!cands.length) {
        // Guard: o seletor CSS pode casar o CONTAINER do diálogo (div) em vez do botão —
        // clicar div não faz nada e parecia "botão travado". Só aceita botão/link real.
        const ehBotao = ehBotaoReal;
        const finalCss = finalizarSel.map((s) => document.querySelector(s)).find((b) => b && ehBotao(b) && OA.isVisible(b));
        const btnFinal = finalCss || OA.findByText(finalizar);

        // Botão de envio EXISTE mas DESABILITADO (termo/campo pendente) → re-preenche e
        // insiste. Antes caía direto no "SEM botão de avançar/finalizar" = vaga morta
        // ("travando na parte de finalizar").
        if (btnFinal && OA.isVisible(btnFinal) && !habil(btnFinal)) {
          const vazios = camposVazios(container);
          log("step", step, "FINALIZAR desabilitado | vazios:", vazios);
          onStatus(vazios.length ? `Falta preencher: ${vazios.join(" · ")}`.slice(0, 110) : "Aguardando o botão de envio habilitar…");
          if (++travado >= 5) { await dumpDiag("finalizar-desabilitado", container); onStatus(`Não consegui concluir${vazios.length ? " (falta: " + vazios.join(" · ") + ")" : ""}. Baixe o Diagnóstico.`.slice(0, 120)); return "falhou"; }
          continue;
        }

        if (btnFinal && OA.isVisible(btnFinal) && habil(btnFinal)) {
          if (pausarAntesEnvio) { onStatus("🔒 Revise e finalize/envie você mesmo (pausa antes do envio)."); return "pausa"; }
          log("step", step, "FINALIZAR:", (btnFinal.innerText || "").trim().slice(0, 30));
          // validação VISÍVEL antes do clique: clicar form inválido não faz NADA (e
          // parecia "botão travado") — mostra no popup qual campo está reclamando.
          try {
            const invalido = [...container.querySelectorAll("[aria-invalid='true']")].find((el) => el !== btnFinal && OA.isVisible(el));
            if (invalido) onStatus(`Campo com erro/pendente: ${nomeCampo(invalido)}`.slice(0, 100));
          } catch (_) {}
          const urlAntes = location.href;
          const frases = [...new Set([...sucessoFrases.map((f) => f.toLowerCase()), ...SUCESSO_GENERICAS])];
          OA.click(btnFinal); await OA.sleep(2000);
          // botões styled-components ignoram .click() simples → reforça com FORTE
          if (assinatura() === sigAntes) { OA.clickForte(btnFinal); await OA.sleep(1800); }
          // VERIFICAÇÃO REAL do envio (até ~18s): frase de sucesso OU evidência estrutural
          // (mudança de URL/botão sumiu). Confirma também diálogos pós-clique ("Confirmar
          // candidatura?"). REFORÇO ÚNICO aos ~9s se a página não reagiu EM NADA — o sc-*
          // do Gupy às vezes ignora os dois primeiros cliques; sem reação não há risco de
          // duplo envio. Antes: 9s sem reclick → "incerto" → a vaga voltava todo run.
          const tEnvio = Date.now();
          let reClicou = false;
          let enviado = false;
          while (Date.now() - tEnvio < 18000) {
            await OA.sleep(700);
            try { OA.fecharBanners?.(); } catch (_) {}
            const txt2 = (document.body.innerText || "").toLowerCase();
            if (frases.some((f) => txt2.includes(f))) { enviado = true; break; }
            // diálogo de confirmação aberto após o clique → confirma dentro DELE
            const dlg = [...document.querySelectorAll("[role='dialog'], [role='alertdialog'], .artdeco-modal, .chakra-modal__content")]
              .find((d) => OA.isVisible(d));
            if (dlg) {
              const conf = [...dlg.querySelectorAll("button, [role='button']")].find((b) => {
                const t = ((b.innerText || b.getAttribute("aria-label") || "")).trim().toLowerCase();
                return habil(b) && OA.isVisible(b) && /^(confirmar( candidatura| inscri[çã]o| envio)?|enviar( candidatura| inscri[çã]o)?|finalizar( candidatura)?|efetuar candidatura|concluir|sim)$/.test(t);
              });
              if (conf) { OA.click(conf); await OA.sleep(1500); }
            }
            if (!reClicou && Date.now() - tEnvio > 9000 && assinatura() === sigAntes
              && document.contains(btnFinal) && OA.isVisible(btnFinal) && habil(btnFinal)) {
              log("step", step, "sem reação ao enviar (~9s) → REFORÇO FORTE único");
              reClicou = true; OA.clickForte(btnFinal); await OA.sleep(1500);
            }
            // evidência estrutural: botão de envio sumiu/escondeu (fluxo avançou)
            if (!document.contains(btnFinal) || !OA.isVisible(btnFinal)) {
              await OA.sleep(1500); // dá tempo da frase renderizar
              const txt3 = (document.body.innerText || "").toLowerCase();
              enviado = frases.some((f) => txt3.includes(f)) || location.href !== urlAntes || !sucessoFrases.length;
              break;
            }
          }
          if (enviado) { log("step", step, "ENVIADO (verificado)"); return "enviado"; }
          log("step", step, "finalizar clicado mas SEM evidência de envio → INCERTO (não conta no dedup)");
          onStatus("Cliquei em enviar mas não vi a confirmação — revise a página.");
          return "incerto";
        }
      }
      if (cands.length) {
        // ESPERA algum habilitar (a validação React do campo é ASSÍNCRONA) por ~4s.
        for (let w = 0; w < 8 && !cands.some((b) => habil(b)); w++) { await OA.sleep(500); cands = candidatosAvancar(); }
        const habilitados = cands.filter((b) => habil(b));
        if (habilitados.length) {
          // Tenta CADA botão candidato até a página mudar. Guard de assinatura evita
          // duplo-envio: assim que a assinatura muda, para. Botão teimoso (React/
          // styled-components sc-* do Gupy) → reforça com clique FORTE no MESMO botão.
          for (const b of habilitados) {
            if (!OA.isVisible(b) || !habil(b)) continue; // pode ter sumido após clique anterior
            log("step", step, "AVANÇAR (clicando):", (b.innerText || "").trim().slice(0, 30));
            await rsleep(500, 1400); // "hesitação" humana antes de clicar
            OA.click(b); await rsleep(1600, 3000);
            if (assinatura() !== sigAntes) break;
            // clique normal não mudou a página → SEMPRE reforça com clique FORTE (o
            // botão sc-* do Gupy ignora o .click() simples). O guard de assinatura já
            // garante que nada aconteceu, então não há duplo-envio. (Antes eu condicionava
            // a camposVazios — um falso-positivo bloqueava o reforço e o botão "não clicava".)
            log("step", step, "clique normal não avançou → clique FORTE"); OA.clickForte(b); await rsleep(1700, 3000);
            if (assinatura() !== sigAntes) break;
            log("step", step, "botão não avançou → tentando o próximo 'continuar'");
          }
        } else {
          const vazios = camposVazios(container);
          log("step", step, "AVANÇAR DESABILITADO:", (cands[0]?.innerText || "").trim().slice(0, 30), "| campos vazios:", vazios);
          onStatus(vazios.length ? `Falta preencher: ${vazios.join(" · ")}`.slice(0, 110) : "Aguardando preencher campo obrigatório…");
          if (++travado >= 5) { await dumpDiag("avancar-desabilitado", container); onStatus(`Não consegui preencher: ${vazios.join(" · ") || "um campo obrigatório"}. Baixe o Diagnóstico no dashboard.`.slice(0, 120)); return "falhou"; }
          continue;
        }
      } else {
        log("step", step, "SEM botão de avançar/finalizar");
        await dumpDiag("sem-botao", container);
        onStatus("Sem botão de avançar/finalizar — pulei.");
        return "falhou";
      }

      // detecta não-avanço (assinatura inclui innerText → pega mudança de SPA)
      const avancou = assinatura() !== sigAntes;
      log("step", step, "avançou:", avancou);
      if (!avancou) {
        const vazios = camposVazios(container);
        if (vazios.length) log("step", step, "não avançou — campos vazios:", vazios);
        if (++travado >= 3) { await dumpDiag("formulario-travou", container); onStatus(`Formulário travou${vazios.length ? " (falta: " + vazios.join(" · ") + ")" : ""} — baixe o Diagnóstico.`.slice(0, 120)); return "falhou"; }
      } else travado = 0;
    }
    return "falhou";
  }

  OA.rodarWizard = rodarWizard;
})();
