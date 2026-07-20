// Preenchedor GENÉRICO de formulários — reusado por LinkedIn/Indeed/Gupy/GeekHunter/
// Senior. Cobre text/number/textarea/select/radio/checkbox + COMBOBOX (MUI Autocomplete,
// ARIA combobox, mat-select — opções em portal/overlay). Perguntas vão pro cérebro;
// contato/consentimento são locais. DEFENSIVO: cada campo em try/catch, um campo ruim
// NUNCA aborta o preenchimento inteiro (era o que fazia o LinkedIn "não aplicar mais").
(function () {
  const OA = window.OA;
  if (!OA || OA.preencherCampos) return;

  const CONTATO = ["nome", "name", "sobrenome", "last name", "first name", "email", "e-mail", "telefone", "phone", "celular", "cidade", "city", "país", "pais", "country", "endereço", "address", "cep", "linkedin", "cpf"];
  const CONSENT = ["aceito", "concordo", "consinto", "li e", "termos", "privacidade", "autorizo", "declaro", "agree", "consent", "terms"];
  const ehContato = (l) => { l = (l || "").toLowerCase(); return l.length < 45 && CONTATO.some((c) => l.includes(c)); };
  const ehConsent = (l) => { l = (l || "").toLowerCase(); return CONSENT.some((c) => l.includes(c)); };

  async function responder(pergunta, tipo, opcoes, ctx) {
    // Sem race próprio: o OA.bg (dom.js) já tem watchdog de 90s e o servidor (responderPergunta)
    // faz 1 retry antes do fallback. O antigo race de 30s era MENOR que o abort do fetch (40s) →
    // descartava resposta VÁLIDA lenta e o campo caía no genérico "Tenho disponibilidade…".
    const r = await OA.bg({ type: "brain.answer", payload: { pergunta, tipo, opcoes, vagaTitulo: ctx.vagaTitulo || "", vagaEmpresa: ctx.vagaEmpresa || "", idioma: ctx.idioma || "pt" } });
    return r?.resposta || "";
  }

  // opções de um dropdown custom (mat-option / [role=option] / MUI / li) em QUALQUER
  // lugar do documento (renderizam em portal/overlay fora do container).
  function opcoesAbertas() {
    return [...document.querySelectorAll(
      "mat-option, .cdk-overlay-container [role='option'], [role='listbox'] [role='option'], [role='option'], " +
      ".MuiAutocomplete-popper [role='option'], .MuiPopover-root [role='option'], ul[role='listbox'] li, " +
      ".chakra-menu__menuitem, .basic-typeahead__selectable, .artdeco-typeahead__result, " +
      "[data-test-text-selectable-option], .artdeco-dropdown__item"
    )].filter((o) => OA.isVisible(o) && (o.innerText || "").trim());
  }

  async function preencherCampos(container, ctx = {}) {
    if (!container) return;
    const wrap = async (fn) => { try { await fn(); } catch (_) { /* campo ruim não aborta o resto */ } };

    // 0) CURRÍCULO: injeta o arquivo salvo em qualquer <input type=file> vazio
    // (GeekHunter/LinkedIn/etc.). No Selenium era send_keys(path); aqui é DataTransfer.
    for (const fi of container.querySelectorAll("input[type='file']")) await wrap(async () => {
      if (fi.files && fi.files.length) return;
      const r = await OA.bg({ type: "resume.get" });
      if (r?.resume?.dataUrl) {
        const ok = await OA.uploadArquivo(fi, r.resume);
        if (ok && ctx.onStatus) ctx.onStatus("📎 Currículo anexado.");
      } else if (ctx.onStatus) {
        ctx.onStatus("⚠️ Sem currículo salvo — anexe o PDF na dashboard da extensão.");
      }
    });

    const log = (...a) => { try { console.log("[AutoApply]", ...a); } catch (_) {} };
    // NÃO usar isVisible (checa opacity) p/ controles de form: o LinkedIn estiliza o
    // <select> nativo com opacity:0 → seria pulado. Pula só o que está display:none.
    const naoOculto = (el) => { try { return getComputedStyle(el).display !== "none" && !el.disabled; } catch (_) { return true; } };

    // 0.5) CONTATO: nome/email/confirmar-email/celular/LinkedIn/cidade. Antes eu PULAVA
    // esses (achando que a plataforma pré-preenche) — mas GeekHunter/etc. NÃO preenchem
    // → form obrigatório ficava vazio ("não preenche nada"). Agora preenche DO PERFIL.
    const perfil = (((await OA.bg({ type: "config.get" })).config) || {}).perfil || {};
    // Só os dígitos NACIONAIS (DDD+número): o widget (react-phone-input-2) já mostra o "+55"
    // fixo → preenchemos SÓ o número, NUNCA o +55. Tira o código de país 55 só quando o número
    // vem completo (12–13 dígitos); um DDD 55 nacional (10–11 dígitos) NÃO é tocado.
    const telNacional = (tel) => {
      let d = String(tel || "").replace(/\D/g, "");
      if (d.length >= 12 && d.startsWith("55")) d = d.slice(2);
      return d;
    };
    const valorContato = (label, inp) => {
      const l = (label || "").toLowerCase();
      const type = (inp.type || "").toLowerCase();
      const auto = (inp.getAttribute("autocomplete") || "").toLowerCase();
      // pista extra p/ telefone: máscara/prefixo no placeholder ou valor (ex.: GeekHunter
      // mostra "+55" e o rótulo "Celular com DDD" às vezes não é detectável no input).
      const hint = ((inp.getAttribute("placeholder") || "") + " " + (inp.value || "")).toLowerCase();
      if (/linkedin/.test(l) || /linkedin/.test(auto)) return perfil.linkedin || "";
      if (/(e-mail|email)/.test(l) || type === "email" || auto.includes("email")) return perfil.email || "";
      if (/(celular|telefone|phone|whatsapp|\bddd\b)/.test(l) || type === "tel" || auto === "tel" || /\+55|\bddd\b|\(\d{2}\)/.test(hint)) return telNacional(perfil.telefone); // só o número, sem +55
      if (/(nome completo|nome|name|full name)/.test(l) && !/(empresa|company|usu[aá]rio|user|arquivo)/.test(l)) return perfil.nome || "";
      if (/(cidade|city|localiza|location)/.test(l)) return perfil.localizacao || "";
      return "";
    };
    // CONTATOS varridos no DOCUMENTO INTEIRO (não só no container): "Celular com DDD"/
    // LinkedIn às vezes ficam num bloco fora do form que o melhorContainer escolheu → nunca
    // eram preenchidos. naoOculto + valorContato (só rótulos de contato) evitam campo errado.
    for (const inp of document.querySelectorAll("input[type='text'], input[type='email'], input[type='tel'], input[type='url'], input:not([type])")) await wrap(async () => {
      if (!naoOculto(inp) || inp.readOnly || inp.getAttribute("role") === "combobox") return;
      const label = OA.labelFor(inp);
      const ehTel = inp.type === "tel" || /(celular|telefone|phone|whatsapp|\bddd\b)/i.test(label) || /\+55|\bddd\b|\(\d{2}\)/.test(((inp.getAttribute("placeholder") || "") + " " + (inp.value || "")).toLowerCase());
      const v = valorContato(label, inp);
      if (!v) {
        // Achou o "Celular com DDD" mas o TELEFONE está VAZIO no perfil da extensão → avisa
        // (é a causa nº1 de "não preenche e não continua": o número não está salvo em Opções).
        if (ehTel && !(perfil.telefone || "").trim() && ctx.onStatus) ctx.onStatus("⚠️ Telefone VAZIO no perfil da extensão (Opções) — 'Celular com DDD' fica obrigatório.");
        return;
      }
      // "já preenchido?" — TELEFONE não decide aqui: o preencherTelefone decide sozinho
      // (pula se já tem número real E o widget não marcou inválido; senão limpa e
      // re-digita). Antes o valor QUEBRADO — ex.: "+55 (47) 992578109" com "informe um
      // número de telefone válido" — contava como preenchido e nunca era corrigido.
      const cur = (inp.value || "").trim();
      if (cur && !ehTel) return;
      if (ehTel) await preencherTelefone(inp, v); else OA.fillInput(inp, v);
      log("contato:", (label || "").slice(0, 30), "→", String(v).slice(0, 25), "| ficou:", (inp.value || "").slice(0, 20));
    });
    log("preencherCampos: selects=", container.querySelectorAll("select").length,
      "combos=", container.querySelectorAll("mat-select, [role='combobox'], [aria-haspopup='listbox']").length,
      "radios=", container.querySelectorAll("input[type='radio']").length,
      "checks=", container.querySelectorAll("input[type='checkbox']").length,
      "texts=", container.querySelectorAll("input[type='text'],input[type='number'],textarea").length);

    // 1) selects nativos (React-controlados → selectOption usa native setter)
    for (const sel of container.querySelectorAll("select")) await wrap(async () => {
      if (!naoOculto(sel)) return;
      const cur = sel.options[sel.selectedIndex]?.text || "";
      if (sel.value && !/selecione|selecionar|select|choose|escolha|--/i.test(cur)) return; // já respondido
      const label = OA.labelFor(sel) || "pergunta";
      if (ehContato(label)) return;
      const opcoes = [...sel.options].map((o) => o.text).filter((t) => t && !/selecione|selecionar|select|choose|escolha|--/i.test(t));
      const resp = await responder(label, "SELECT", opcoes, ctx);
      const ok = OA.selectOption(sel, resp);
      log("select:", label.slice(0, 40), "| opções:", opcoes.length, "| resp:", resp, "| ok:", ok, "| ficou:", sel.options[sel.selectedIndex]?.text);
    });

    // 2) COMBOBOX. (a) input[role=combobox] TYPEAHEAD (ex.: cidade do LinkedIn): pega a
    // resposta → DIGITA → espera as sugestões → clica a 1ª (ou Tab pra comprometer). (b)
    // mat-select/MUI/dropdown custom: abre → escolhe no overlay (opções em portal).
    const combos = container.querySelectorAll("mat-select, input[role='combobox'], [role='combobox'], .MuiAutocomplete-root input, [aria-haspopup='listbox'], button[aria-haspopup='true']");
    for (const cb of combos) await wrap(async () => {
      if (!naoOculto(cb) || cb.getAttribute("aria-disabled") === "true") return;
      // Seletor de PAÍS do react-phone-input-2 (GeekHunter: .selected-flag dentro de
      // .flag-dropdown, com aria-haspopup=listbox) → NUNCA tocar: o default +55 (Brasil)
      // já serve, e clicá-lo DEPOIS do número re-formata o valor e dispara "informe um
      // número de telefone válido". (Se um dia precisar trocar o país, é ANTES de
      // digitar o número — nunca depois.)
      if (cb.closest(".flag-dropdown, .react-tel-input") || cb.classList?.contains("selected-flag")) return;
      if (cb.value && cb.value.trim() && cb.getAttribute("aria-invalid") !== "true") return; // já preenchido
      if (cb.querySelector?.(".mat-select-value-text, .mat-mdc-select-value-text")?.innerText.trim()) return;
      const label = OA.labelFor(cb) || cb.closest("mat-form-field, .MuiFormControl-root, .form-group")?.querySelector("mat-label, label")?.innerText || "pergunta";
      if (ehContato(label)) return;

      if (cb.tagName === "INPUT") {
        // TYPEAHEAD: resposta da IA → digita → clica a sugestão.
        const resp = (await responder(label, "TEXT", [], ctx)).trim();
        if (!resp) return;
        try { cb.focus(); } catch (_) {}
        OA.setNativeValue(cb, resp);
        await OA.sleep(1300);
        const opts = opcoesAbertas();
        if (opts.length) {
          OA.click(opts.find((o) => o.innerText.trim().toLowerCase().includes(resp.toLowerCase())) || opts[0]);
        } else {
          cb.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", keyCode: 9, bubbles: true }));
          try { cb.blur(); } catch (_) {}
        }
        log("combobox-typeahead:", label.slice(0, 40), "| resp:", resp, "| sugestões:", opts.length);
        await OA.sleep(350);
      } else {
        // mat-select / dropdown custom (div/button): abre e escolhe. No Angular MDC
        // clicar o <mat-select> às vezes não abre → clica o trigger interno; espera mais.
        const trigger = cb.querySelector(".mat-mdc-select-trigger, .mat-select-trigger, [aria-haspopup]") || cb;
        OA.click(trigger); await OA.sleep(700);
        let opts = opcoesAbertas();
        if (!opts.length) { OA.click(cb); await OA.sleep(700); opts = opcoesAbertas(); }
        log("combobox-dropdown:", label.slice(0, 40), "| opções abertas:", opts.length);
        if (!opts.length) { try { document.body.click(); } catch (_) {} return; }
        const opcoes = opts.map((o) => o.innerText.trim()).filter(Boolean);
        const resp = await responder(label, "SELECT", opcoes, ctx);
        OA.click(opts.find((o) => o.innerText.trim().toLowerCase().includes((resp || "").toLowerCase())) || opts[0]);
        await OA.sleep(400);
      }
    });

    // 3) grupos de radio. O MUI (Gupy/GeekHunter) ESCONDE o <input type=radio> com
    // CSS opacity:0 (o controle visível é o <label> MuiFormControlLabel-root) → o
    // OA.isVisible() (que barra opacity:0) os PULAVA e as perguntas NUNCA eram
    // respondidas. Usa naoOculto (só display:none/visibility:hidden) pra incluí-los.
    const grupos = new Map();
    for (const r of container.querySelectorAll("input[type='radio']")) {
      if (!naoOculto(r)) continue;
      if (r.id && /^skill-option/.test(r.id)) continue; // Solides: skills eliminatórias → content/solides.js
      const key = r.name || "g" + [...container.querySelectorAll("input[type='radio']")].indexOf(r);
      if (!grupos.has(key)) grupos.set(key, []);
      grupos.get(key).push(r);
    }
    for (const [, radios] of grupos) await wrap(async () => {
      if (radios.some((r) => r.checked)) return;
      const grp = radios[0].closest("fieldset, [role='radiogroup'], .form-group, li, div");
      // Prefere legend (radio nativo) > heading <h3> (MUI/Gupy: enunciado é h3 irmão do
      // grupo, não há fieldset) > label/p (texto da opção) > labelFor. Sem isso a IA
      // recebia o TEXTO DA OPÇÃO como "pergunta" e escolhia errado.
      const label = (grp && grp.querySelector("legend")?.innerText) || OA.headingLabel(radios[0])
        || (grp && grp.querySelector("label, p")?.innerText) || OA.labelFor(radios[0]) || "";
      if (!label) return;
      const opcoes = radios.map((r) => OA.labelFor(r) || r.value || "").filter(Boolean);
      let escolha;
      if (ehConsent(label)) escolha = radios.find((r) => /sim|yes|aceito|concordo/i.test(OA.labelFor(r) || r.value)) || radios[0];
      else if (ehContato(label)) return;
      else { const resp = await responder(label, "RADIO", opcoes, ctx); escolha = radios.find((r) => (OA.labelFor(r) || r.value || "").toLowerCase().includes((resp || "").toLowerCase())) || radios[0]; }
      OA.setChecked(escolha, true, container); // radio React-controlado → label-click + native setter
    });

    // 4) checkboxes — NÃO pular por visibilidade: o LinkedIn ESCONDE o input com CSS
    // (is_displayed=false) e é React-controlado. OA.setChecked faz label-click + native
    // setter (igual ao Selenium). Consentimento/obrigatório/label-curto → marca;
    // pergunta booleana com label → IA decide (não marca se responder "não").
    for (const cb of container.querySelectorAll("input[type='checkbox']")) await wrap(async () => {
      if (cb.checked) return;
      if (cb.dataset.oaGupyMui) return; // opção de pergunta MUI do Gupy: tratada em gupy.js (grupo por h3, escolhe UMA) — mexer aqui marcaria opção errada/toggle
      const label = OA.labelFor(cb) || (cb.closest("label, .mat-checkbox, .chakra-checkbox, div")?.innerText || "").split("\n")[0] || "";
      const obrig = cb.required || cb.getAttribute("aria-required") === "true";
      if (ehConsent(label) || obrig || label.trim().length < 3) {
        OA.setChecked(cb, true, container);
      } else if (label) {
        const resp = await responder(label, "CHECKBOX", ["sim", "não"], ctx);
        if (!/^\s*(n[ãa]o|no|false|0)\s*$/i.test(resp)) OA.setChecked(cb, true, container);
      }
    });

    // 4.5) REDE DE SEGURANÇA p/ grupo de checkbox OBRIGATÓRIO vazio — SÓ quando a
    // plataforma pede (ctx.destravarGrupoCheckbox; ex.: Gupy "Perguntas criadas pela
    // empresa"). O loop (4) avalia cada checkbox ISOLADO (IA sim/não) e pode não marcar
    // NENHUM; se o grupo é "selecione ao menos uma" (obrigatório), isso deixa o grupo
    // insatisfeito e TRAVA o "Salvar e continuar" desabilitado. Roda DEPOIS de (4) (a IA
    // tem prioridade); só marca a 1ª opção do grupo que ficou 100% vazio. OFF por padrão
    // (LinkedIn/Indeed/etc. NÃO entram — evita marcar opt-in/consent indevido).
    if (ctx.destravarGrupoCheckbox) {
      const obrigCb = (cb) => cb.required || cb.getAttribute("aria-required") === "true"
        || /\*/.test(OA.labelFor(cb) || "")
        || /\*|obrigat/i.test((cb.closest("fieldset, [role='group'], .form-group, li, div")?.innerText || "").slice(0, 140));
      const grupos = new Map();
      for (const cb of container.querySelectorAll("input[type='checkbox']")) {
        if (cb.dataset.oaGupyMui) continue; // opção de pergunta MUI do Gupy → tratada em gupy.js
        if (ehConsent(OA.labelFor(cb) || "")) continue; // consentimento já tratado em (4)
        if (!obrigCb(cb)) continue;
        // agrupa por name; sem name, por fieldset/[role=group]; solto → grupo próprio
        const k = cb.name || cb.closest("fieldset, [role='group']") || cb;
        if (!grupos.has(k)) grupos.set(k, []);
        grupos.get(k).push(cb);
      }
      for (const [, cbs] of grupos) await wrap(async () => {
        if (cbs.some((c) => c.checked)) return; // grupo já satisfeito (IA marcou algo)
        OA.setChecked(cbs[0], true, container);
        log("checkbox-grupo obrig destravado:", (OA.headingLabel(cbs[0]) || OA.labelFor(cbs[0]) || "grupo").slice(0, 40));
      });
    }

    // 5) text / number / textarea. NUMERO: CLAMP no min/max do input (o LinkedIn expõe
    // "between 0 and 99" como max=99; fora do range é RECUSADO → descartaria a vaga).
    // Campo de moeda com default "R$ 0,00" conta como VAZIO (senão o salário nunca entra).
    const jaTem = (v) => v && v.trim() && !(/r\$/i.test(v) && /^[r$\s]*0([.,]0+)?$/i.test(v));
    // Inclui DATA (date/month/datetime-local): o Gupy usa datas nas "Perguntas criadas
    // pela empresa" (ex.: disponibilidade de início) e, sem cobrir esse tipo aqui, o
    // campo obrigatório ficava VAZIO → "Salvar e continuar" nunca habilitava (o
    // camposVazios do wizard JÁ contava esses inputs, mas o preenchedor os ignorava).
    for (const inp of container.querySelectorAll("input[type='text'], input[type='number'], input[type='date'], input[type='month'], input[type='datetime-local'], input:not([type]), textarea")) await wrap(async () => {
      if (!naoOculto(inp) || jaTem(inp.value) || inp.readOnly) return;
      if (inp.getAttribute("role") === "combobox") return; // já tratado em (2)
      const label = OA.labelFor(inp) || "";
      if (ehContato(label)) return;
      // SALÁRIO/MOEDA: vem do CONFIG verbatim (nunca IA, nunca number-coerce). Isso evita
      // o "R$ 0,00 preenchido com NaN": o clampNum de um valor não-numérico virava NaN no
      // input mascarado. Sem valor no config → NÃO preenche (deixa o default do site).
      const ehMoeda = /r\$/i.test(inp.value || "") || /r\$/i.test(inp.getAttribute("placeholder") || "") || /(remunera|sal[aá]ri|pretens)/i.test(label);
      if (ehMoeda) {
        const val = /(pj|pessoa jur|cnpj|jur[ií]dic)/i.test(label) ? perfil.remuneracao_pj
                  : /(d[oó]lar|usd|dollar)/i.test(label) ? perfil.remuneracao_dolar
                  : /clt/i.test(label) ? perfil.remuneracao_clt
                  : (perfil.remuneracao_clt || perfil.pretensao_salarial);
        const limpo = String(val || perfil.pretensao_salarial || "").trim();
        if (limpo) { OA.fillInput(inp, limpo); log("salário:", label.slice(0, 30), "→", limpo); }
        else log("salário SEM valor no config (não preenchi p/ não virar NaN):", label.slice(0, 30));
        return;
      }
      // Obrigatório? (input required/aria-required OU * no rótulo/wrapper — o Gupy marca
      // "*" num <span> ao lado). Um campo obrigatório NÃO pode ficar vazio, senão o
      // "Salvar e continuar" trava — é o caso das "Perguntas criadas pela empresa".
      const wrapTxt = (inp.closest("[class*='FormControl'], .form-group, li, fieldset, div")?.innerText || "").slice(0, 120);
      const obrig = inp.required || inp.getAttribute("aria-required") === "true" || /\*/.test(label) || /\*|obrigat/i.test(wrapTxt);
      // Sem rótulo E opcional → deixa quieto (não enche campo opcional com lixo, ex.: LinkedIn).
      // Sem rótulo MAS obrigatório → responde mesmo assim (a etapa de perguntas do Gupy às
      // vezes não expõe rótulo detectável e o campo é obrigatório).
      if (!label && !obrig) return;
      const ehData = /^(date|month|datetime-local)$/.test(inp.type);
      const tipo = ehData ? "DATA"
        : (inp.type === "number" || /quantos|anos|years|how many|qtd/i.test(label) ? "NUMERO" : "TEXT");
      let resp = await responder(label || "Pergunta obrigatória da empresa", tipo, [], ctx);
      if (tipo === "NUMERO") resp = clampNum(inp, resp);
      else if (ehData) {
        // opcional sem resposta da IA → não força (não inventa data em campo opcional)
        if (!obrig && !String(resp).trim()) return;
        // <input type=date> RECUSA valor fora do formato ISO → ficaria vazio e travaria o
        // botão. Normaliza (dd/mm/aaaa→aaaa-mm-dd; "imediata"/inválido → hoje).
        resp = dataISO(inp, resp);
      }
      // IA devolveu vazio (timeout/recusa) num campo obrigatório → fallback seguro (nunca trava).
      if (obrig && !String(resp).trim()) resp = tipo === "NUMERO" ? "0" : "Tenho interesse e disponibilidade para a vaga.";
      if (!String(resp).trim()) return; // opcional sem resposta → não força
      OA.fillInput(inp, resp);
      log("texto:", (label || "(sem rótulo)").slice(0, 40), "| tipo:", tipo, "| obrig:", obrig, "| resp:", String(resp).slice(0, 30));
    });
  }

  // Preenche telefone em widget CONTROLADO (react-phone-input-2 do GeekHunter:
  // input[type=tel][name=phone], já vem com "+55 "). Fiel ao Selenium que FUNCIONAVA
  // (_preencher_telefone_geekhunter): NUNCA substitui o valor inteiro — setar
  // "11912345678" faz o widget re-parsear o DDI pelos PRIMEIROS dígitos (vira +1…) ou
  // reverter pro "+55" logo DEPOIS do nosso check → "não preenche o telefone". Aqui:
  // caret no FIM (depois do "+55 ") e DIGITA o número nacional — o widget formata.
  // NUNCA escrever "+55" no valor: o GeekHunter valida o campo como NACIONAL e o código
  // do país escrito dispara "informe um número de telefone válido". Cadência humana
  // (a 45ms/char a validação não assentava); se não assentou, limpa e re-digita.
  async function preencherTelefone(inp, nacional) {
    const dig = String(nacional || "").replace(/\D/g, "");
    if (!dig) return;
    const digitos = () => (inp.value || "").replace(/\D/g, "");
    const ok = () => digitos().endsWith(dig);
    // erro de validação do widget VISÍVEL (ex.: GeekHunter "Por favor, informe um número
    // de telefone válido")? → o valor atual NÃO vale mesmo com 10+ dígitos: limpa e
    // re-digita (o digitar() abaixo já limpa quando há mais que o DDI no campo).
    const erroVisivel = () => { let n = inp.parentElement; for (let i = 0; i < 5 && n; i++, n = n.parentElement) { if (/n[uú]mero de telefone v[aá]lido|telefone inv[aá]lido/i.test(n.innerText || "")) return true; } return false; };
    if (digitos().length >= 10 && !erroVisivel()) return; // já tem um número real e VÁLIDO
    const digitar = async (cadencia) => {
      try { inp.focus(); } catch (_) {}
      // sobrou valor parcial (mais que o DDI, menos que um número) → limpa antes
      if (digitos().length > 3) { try { inp.select(); document.execCommand("delete"); } catch (_) {} await OA.sleep(300); }
      try { const n = (inp.value || "").length; inp.setSelectionRange(n, n); } catch (_) {}
      for (const ch of dig) {
        let ins = false;
        try { ins = document.execCommand("insertText", false, ch); } catch (_) {}
        if (!ins) { try { OA.setNativeValue(inp, (inp.value || "") + ch); } catch (_) {} }
        inp.dispatchEvent(new KeyboardEvent("keyup", { key: ch, bubbles: true }));
        await OA.sleep(cadencia + Math.floor(Math.random() * 70));
      }
      await OA.sleep(800); // widget re-formata/valida antes de conferir
    };
    await digitar(90);
    if (!ok()) await digitar(150); // não assentou → limpa e re-digita mais devagar
    if (!ok()) OA.fillInput(inp, dig); // último recurso: input simples sem widget (SÓ o número, sem DDI)
    try { inp.blur(); } catch (_) {}
  }

  function clampNum(inp, resp) {
    let n = parseFloat(String(resp).replace(/[^\d.-]/g, ""));
    if (!isFinite(n)) n = 0;
    const mn = parseFloat(inp.getAttribute("min")), mx = parseFloat(inp.getAttribute("max"));
    if (isFinite(mn) && n < mn) n = mn;
    if (isFinite(mx) && n > mx) n = mx;
    return String(Number.isInteger(n) ? n : Math.round(n));
  }

  // Normaliza a resposta da IA para o formato que o <input type=date/month/datetime-local>
  // exige (senão o valor é RECUSADO e o campo fica vazio). Aceita ISO e dd/mm/aaaa;
  // "imediata"/vazio/inválido → hoje (campo obrigatório nunca pode ficar em branco).
  function dataISO(inp, resp) {
    const pad = (n) => String(n).padStart(2, "0");
    const s = String(resp || "").trim();
    let d = null, m;
    if ((m = s.match(/(\d{4})-(\d{1,2})-(\d{1,2})/))) d = new Date(+m[1], +m[2] - 1, +m[3]);
    else if ((m = s.match(/(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})/))) {
      let y = +m[3]; if (y < 100) y += 2000; d = new Date(y, +m[2] - 1, +m[1]);
    }
    if (!d || isNaN(d.getTime())) d = new Date(); // "imediata"/inválido → hoje
    const ymd = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    if (inp.type === "month") return ymd.slice(0, 7);
    if (inp.type === "datetime-local") return `${ymd}T09:00`;
    return ymd;
  }

  OA.preencherCampos = preencherCampos;
})();
