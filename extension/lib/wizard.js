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
    } = opts || {};

    const log = (...a) => { try { console.log("[AutoApply][wizard]", ...a); } catch (_) {} };
    const habil = (b) => b && !b.disabled && b.getAttribute("aria-disabled") !== "true";
    const assinatura = () => location.href + "|" + document.querySelectorAll("input,select,textarea,button").length + "|" + (document.body.innerText || "").length;

    // Diagnóstico: quais campos obrigatórios estão VAZIOS (é o que desabilita o botão).
    const camposVazios = (container) => {
      const out = [];
      for (const el of container.querySelectorAll("input[required],textarea[required],select[required],[aria-required='true']")) {
        const tag = el.tagName.toLowerCase();
        let vazio = false;
        if (tag === "fieldset") vazio = ![...el.querySelectorAll("input[type='radio']")].some((r) => r.checked);
        else if (tag === "select") vazio = !el.value || /selecione|escolha|^--/i.test(el.options?.[el.selectedIndex]?.text || "");
        else if (tag === "input" || tag === "textarea") vazio = !(el.value || "").trim();
        if (vazio) out.push((OA.labelFor(el) || el.name || tag).slice(0, 45));
      }
      // grupos de radio MUI (obrigatório via "*" no h3, sem [required]) sem nada marcado
      const grp = new Map();
      for (const r of container.querySelectorAll("input[type='radio']")) { const k = r.name || "?"; (grp.get(k) || grp.set(k, []).get(k)).push(r); }
      for (const [, radios] of grp) if (!radios.some((r) => r.checked)) out.push((OA.headingLabel(radios[0]) || OA.labelFor(radios[0]) || "pergunta").slice(0, 45));
      return [...new Set(out)];
    };
    let travado = 0;

    for (let step = 0; step < maxSteps; step++) {
      if (!(await isRunning())) return "parou";
      await OA.sleep(700);
      const container = (typeof getContainer === "function" ? getContainer() : getContainer) || document.body;

      // hook por-plataforma ANTES do preenchimento genérico (ex.: Gupy força "Não" no
      // referral e escolhe a 1ª opção do combobox opcional). NUNCA derruba o wizard.
      try { if (typeof preencher === "function") await preencher(container, step); } catch (e) { log("preencher(hook) erro:", e?.message); }
      // preencher NUNCA pode derrubar o wizard (senão a automação "para" silenciosa).
      try { await OA.preencherCampos(container, { ...ctx, onStatus }); } catch (e) { log("preencherCampos erro:", e?.message); }
      await OA.sleep(400);
      const sigAntes = assinatura();

      // FINALIZAR (envio). Seletor CSS (ex.: #dialog-give-up-personalization-step do
      // Gupy = envio) ou texto — busca no documento INTEIRO (NÃO escopar num "dialog":
      // `[id*='dialog']` casava o próprio botão give-up do Gupy e escondia o "Continuar").
      const finalCss = finalizarSel.map((s) => document.querySelector(s)).find((b) => b && OA.isVisible(b));
      const btnFinal = finalCss || OA.findByText(finalizar);
      if (btnFinal && OA.isVisible(btnFinal) && habil(btnFinal)) {
        if (pausarAntesEnvio) { onStatus("🔒 Revise e finalize/envie você mesmo (pausa antes do envio)."); return "pausa"; }
        const forte = !!finalCss; // seletor CSS específico (ex.: give-up dialog) = envio real
        log("step", step, "FINALIZAR:", (btnFinal.innerText || "").trim().slice(0, 30), "forte:", forte);
        OA.click(btnFinal); await OA.sleep(1800);
        // botão sc-* do Gupy ("Finalizar candidatura") ignora o .click() simples → se não
        // mudou nada, reforça com clique FORTE (sequência pointer/mouse completa).
        if (assinatura() === sigAntes) { log("step", step, "finalizar: clique normal não mudou → clique FORTE"); OA.clickForte(btnFinal); await OA.sleep(1600); }
        for (const s of finalizarSel) { const d = document.querySelector(s); if (d && OA.isVisible(d)) { OA.click(d); await OA.sleep(600); OA.clickForte(d); await OA.sleep(1400); break; } }
        // VERIFICA o envio (senão marca falso-sucesso e ENVENENA o dedup — lição do
        // Selenium). Botão forte (CSS) OU frase de sucesso na página = enviado; senão
        // "incerto" (NÃO conta como aplicada → pode tentar de novo depois).
        await OA.sleep(1000);
        const txt = (document.body.innerText || "").toLowerCase();
        const confirmado = sucessoFrases.some((f) => txt.includes(f));
        if (forte || confirmado || !sucessoFrases.length) { log("ENVIADO (forte/confirmado)"); return "enviado"; }
        log("finalizar clicado mas SEM frase de sucesso → INCERTO (não conta)");
        return "incerto";
      }

      // AVANÇAR. O botão "Continuar" fica DESABILITADO até a validação (React) do campo
      // rodar — que é ASSÍNCRONA. Então: acha o botão e ESPERA ele habilitar (re-buscando)
      // por ~4s antes de decidir. Se habilitar → CLICA. Se seguir desabilitado → re-preenche.
      // Resolução do botão (mesma usada no re-teste abaixo): (1) avancarSel EXATO — só
      // quando CASA (ex.: Gupy "Dados adicionais" tem name='saveAndContinueButton'); (2)
      // preferUltimo — clica o ÚLTIMO visível/habilitado (CTA primário, não o sticky);
      // (3) findByText. O avancarSel NUNCA bloqueia: se não casar (ex.: tela de perguntas
      // do Gupy cujo "Salvar e continuar" NÃO tem name), cai no texto (senão trava em
      // "continuar").
      // Retorna TODOS os candidatos a avançar (em ordem de prioridade), não só um. O
      // Gupy repete o botão na MESMA tela — ex.: DOIS "Continuar", um sticky no topo e o
      // do rodapé — e só UM avança de verdade. Então, em vez de apostar num só, tentamos
      // cada um até a página mudar. (1) avancarSel EXATO primeiro (ex.: "Dados adicionais"
      // tem name='saveAndContinueButton'); (2) o texto de MAIOR prioridade que tenha botão
      // visível ("salvar e continuar"/"responder agora" antes do "continuar" genérico),
      // todos os botões daquele texto — preferUltimo põe o do rodapé na frente.
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
            OA.click(b); await OA.sleep(1500);
            if (assinatura() !== sigAntes) break;
            // clique normal não mudou a página → SEMPRE reforça com clique FORTE (o
            // botão sc-* do Gupy ignora o .click() simples). O guard de assinatura já
            // garante que nada aconteceu, então não há duplo-envio. (Antes eu condicionava
            // a camposVazios — um falso-positivo bloqueava o reforço e o botão "não clicava".)
            log("step", step, "clique normal não avançou → clique FORTE"); OA.clickForte(b); await OA.sleep(1600);
            if (assinatura() !== sigAntes) break;
            log("step", step, "botão não avançou → tentando o próximo 'continuar'");
          }
        } else {
          const vazios = camposVazios(container);
          log("step", step, "AVANÇAR DESABILITADO:", (cands[0]?.innerText || "").trim().slice(0, 30), "| campos vazios:", vazios);
          onStatus(vazios.length ? `Falta preencher: ${vazios.join(" · ")}`.slice(0, 110) : "Aguardando preencher campo obrigatório…");
          if (++travado >= 5) { onStatus(`Não consegui preencher: ${vazios.join(" · ") || "um campo obrigatório"}. Complete à mão e ▶️.`.slice(0, 120)); return "falhou"; }
          continue;
        }
      } else {
        log("step", step, "SEM botão de avançar/finalizar");
        onStatus("Sem botão de avançar/finalizar — pulei.");
        return "falhou";
      }

      // detecta não-avanço (assinatura inclui innerText → pega mudança de SPA)
      const avancou = assinatura() !== sigAntes;
      log("step", step, "avançou:", avancou);
      if (!avancou) {
        const vazios = camposVazios(container);
        if (vazios.length) log("step", step, "não avançou — campos vazios:", vazios);
        if (++travado >= 3) { onStatus(`Formulário travou${vazios.length ? " (falta: " + vazios.join(" · ") + ")" : ""} — pulei.`.slice(0, 120)); return "falhou"; }
      } else travado = 0;
    }
    return "falhou";
  }

  OA.rodarWizard = rodarWizard;
})();
