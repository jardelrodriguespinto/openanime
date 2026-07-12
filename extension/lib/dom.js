// Helpers de DOM para content scripts. Exposto como window.OA (scripts do mesmo
// content_scripts compartilham o escopo). Sem ES modules aqui (content script).
(function () {
  if (window.OA) return;

  const sleepLocal = (ms) => new Promise((r) => setTimeout(r, ms));
  // Aba OCULTA (ex.: "Iniciar tudo" abre 5 abas em background): o Chrome agrupa os
  // timers de aba oculta em ~1/minuto (throttling intensivo após 5 min) e a automação
  // inteira "fica parada" — inclusive os sleeps curtos do waitFor/digitação. TODO sleep
  // roda no SERVICE WORKER (não sofre throttling de visibilidade); timeout local com
  // folga como rede de segurança caso o SW seja suspenso no meio.
  function sleep(ms) {
    if (!document.hidden || !chrome.runtime?.id) return sleepLocal(ms);
    return new Promise((resolve) => {
      let fim = false;
      const done = () => { if (!fim) { fim = true; resolve(); } };
      try {
        chrome.runtime.sendMessage({ type: "util.sleep", ms }, () => { void chrome.runtime.lastError; done(); });
      } catch (_) { return void sleepLocal(ms).then(done); }
      setTimeout(done, ms + 70000);
    });
  }

  function isVisible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none" && s.opacity !== "0";
  }

  // Espera um seletor aparecer (dentro de root). Retorna o elemento ou null.
  async function waitFor(selector, { root = document, timeout = 10000, visible = true } = {}) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      const el = root.querySelector(selector);
      if (el && (!visible || isVisible(el))) return el;
      await sleep(200);
    }
    return null;
  }

  // Clique robusto (scroll + click nativo).
  function click(el) {
    if (!el) return false;
    try {
      el.scrollIntoView({ block: "center" });
    } catch (_) {}
    try {
      el.click();
      return true;
    } catch (_) {
      try {
        el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
        return true;
      } catch (_) {
        return false;
      }
    }
  }

  // Clique FORTE: sequência completa de eventos pointer/mouse. Alguns botões React/
  // styled-components (ex.: "Salvar e continuar" do Gupy, classes sc-*) só reagem à
  // sequência real de ponteiro — o .click() simples não dispara o handler. Usado como
  // FALLBACK pelo wizard quando o clique normal não mudou a página (evita duplo-envio).
  function clickForte(el) {
    if (!el) return false;
    try { el.scrollIntoView({ block: "center" }); } catch (_) {}
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const base = { bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy, button: 0 };
    const seq = [
      ["pointerover", PointerEvent], ["pointerenter", PointerEvent],
      ["mouseover", MouseEvent], ["pointerdown", PointerEvent], ["mousedown", MouseEvent],
      ["focus", FocusEvent], ["pointerup", PointerEvent], ["mouseup", MouseEvent], ["click", MouseEvent],
    ];
    try {
      try { el.focus(); } catch (_) {}
      for (const [type, Ctor] of seq) {
        try { el.dispatchEvent(new Ctor(type, type === "focus" ? { bubbles: true } : base)); } catch (_) {
          try { el.dispatchEvent(new Event(type, { bubbles: true })); } catch (_) {}
        }
      }
      return true;
    } catch (_) { return false; }
  }

  // Acha um clicável por texto (button/a/[role=button]) dentro de root. Se houver
  // MAIS DE UM botão casando (ex.: Gupy tem "Salvar e continuar" no rodapé E um
  // sticky no topo, ou um "Continuar" de step anterior ainda no DOM), prefere o
  // HABILITADO (ignora duplicatas desabilitadas/sticky) em vez do primeiro achado —
  // senão a automação clica num botão inútil e o wizard "não avança".
  function findByText(texts, { root = document, sel = "button, a, [role='button']" } = {}) {
    const alvos = (Array.isArray(texts) ? texts : [texts]).map((t) => t.toLowerCase());
    const habil = (el) => el && !el.disabled && el.getAttribute("aria-disabled") !== "true";
    let fallback = null;
    for (const el of root.querySelectorAll(sel)) {
      if (!isVisible(el)) continue;
      const t = (el.innerText || el.textContent || el.getAttribute("aria-label") || "").trim().toLowerCase();
      if (alvos.some((a) => t.includes(a))) {
        if (habil(el)) return el;
        if (!fallback) fallback = el;
      }
    }
    return fallback;
  }

  // CRÍTICO p/ React: setar .value direto NÃO dispara o onChange do React.
  // Usa o setter nativo do prototype + dispara input/change.
  function setNativeValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function fillInput(el, value) {
    if (!el) return false;
    try {
      el.focus();
      setNativeValue(el, value);
      el.blur();
      return true;
    } catch (_) {
      return false;
    }
  }

  const _placeholderOpt = (t) => !t || /^(selecione|selecionar|select|choose|escolha|--)/i.test(t.trim());

  // Preenche um <select>. CRÍTICO: o <select> do LinkedIn é REACT-CONTROLADO — setar
  // `.value` direto o React REVERTE p/ "Selecione uma opção" (o onChange não passa pelo
  // React) → dropdown fica vazio → "Avançar" desabilitado → automação PARADA. A correção
  // (igual ao _preencher_select_selenium do Selenium) é o NATIVE SETTER do prototype
  // (que o React intercepta) + input+change. Fallback: 1ª opção real (não trava).
  function selectOption(selectEl, value) {
    if (!selectEl) return false;
    const v = String(value || "").trim().toLowerCase();
    const opts = [...selectEl.options].filter((o) => o.text.trim() && !_placeholderOpt(o.text));
    if (!opts.length) return false;
    const alvo =
      opts.find((o) => o.text.trim().toLowerCase() === v) ||
      (v && opts.find((o) => o.text.trim().toLowerCase().includes(v) || v.includes(o.text.trim().toLowerCase()))) ||
      opts[0]; // fallback: 1ª opção real → nunca deixa o select vazio (destrava)
    try {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set;
      setter.call(selectEl, alvo.value);
    } catch (_) {
      selectEl.value = alvo.value;
    }
    selectEl.dispatchEvent(new Event("input", { bubbles: true }));
    selectEl.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }

  // Rótulo de "heading" (h1-h6) para um campo. O Gupy (e GeekHunter/Senior) usam
  // <h3> como ENUNCIADO da pergunta e o textarea/radio FICA SOLTO logo abaixo,
  // SEM <label for> nem fieldset/legend — então labelFor() retornava "" e o campo
  // NÃO era preenchido. Caminha alguns ancestrais e pega um <h3> FILHO direto
  // (o enunciado costuma ser irmão do grupo de opções/campo).
  function headingLabel(el) {
    let node = el?.parentElement;
    for (let i = 0; i < 4 && node; i++, node = node.parentElement) {
      const h = node.querySelector(":scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6");
      if (h && h.innerText.trim()) {
        return h.innerText.trim().replace(/^\d+[\.\)]\s*/, "").replace(/\s*\*\s*$/, "").trim();
      }
    }
    return "";
  }

  // Rótulo associado a um input. ORDEM importa: o <label> PRÓPRIO (opção do MUI radio,
  // campo de contato) vem ANTES do heading/legend (que é o ENUNCIADO do grupo) — senão
  // toda opção de radio recebia o texto da pergunta. Placeholder é o ÚLTIMO recurso
  // ("Digite sua resposta aqui" / "Escolha uma opção" não são a pergunta).
  function labelFor(el) {
    const aria = el.getAttribute("aria-label");
    if (aria && aria.trim() && aria.trim().toLowerCase() !== "mais opções") return aria.trim();
    const alby = el.getAttribute("aria-labelledby");
    if (alby) {
      const t = alby.split(/\s+/).map((id) => document.getElementById(id)?.innerText || "").join(" ").trim();
      if (t) return t;
    }
    const id = el.id;
    if (id) {
      const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (lab && lab.innerText.trim()) return lab.innerText.trim();
    }
    // <label> PRÓPRIO (ex.: <label class="MuiFormControlLabel-root">Menos de 1 ano</label>
    // envolve o radio; ou <label>Nome<input></label>) — é a opção/campo, não a pergunta.
    const own = el.closest("label");
    if (own && own.innerText.trim()) return own.innerText.trim();
    const fieldset = el.closest("fieldset");
    if (fieldset) {
      const legend = fieldset.querySelector("legend");
      if (legend && legend.innerText.trim()) return legend.innerText.trim();
    }
    const h = headingLabel(el);
    if (h) return h;
    const wrap = el.closest("div, li");
    if (wrap) {
      const lab = wrap.querySelector("label");
      if (lab && lab.innerText.trim()) return lab.innerText.trim();
    }
    const ph = el.getAttribute("placeholder");
    if (ph && ph.trim()) return ph.trim();
    return "";
  }

  // Chama o service worker (o cérebro / storage). WATCHDOG: se o SW nunca responder
  // (ex.: fetch pendurado segurando o canal aberto), o await do caller ficava PENDENTE
  // PRA SEMPRE e a automação "ficava parada" na aba (o guard _fluxo não solta e nem o
  // cs.kick reentra). 90s > o pior caso legítimo (fetch do cérebro com abort em 60s).
  // Também engole o throw síncrono de contexto invalidado (extensão recarregada).
  function bg(message) {
    return new Promise((resolve) => {
      let fim = false;
      const done = (r) => { if (!fim) { fim = true; resolve(r); } };
      const t = setTimeout(() => done({ ok: false, erro: "sem resposta do service worker (timeout)" }), 90000);
      try {
        chrome.runtime.sendMessage(message, (resp) => { clearTimeout(t); void chrome.runtime.lastError; done(resp || { ok: false }); });
      } catch (e) { clearTimeout(t); done({ ok: false, erro: String(e?.message || e) }); }
    });
  }

  // Marca/desmarca checkbox React que o LinkedIn ESCONDE com CSS (is_displayed=false)
  // e controla via React. Igual ao _preencher_checkbox_selenium: 1) clica o label (JS),
  // 2) clica o input, 3) native setter de 'checked' + dispara click/input/change.
  function setChecked(cb, checked, root) {
    if (!cb) return false;
    if (cb.checked === checked) return true;
    root = root || document;
    // Alvos clicáveis: label[for], <label> pai, e o host/inner do Angular Material
    // (mat-checkbox / MDC) e Chakra — nessas libs o <input> real é escondido.
    const alvos = [
      cb.id && root.querySelector(`label[for="${CSS.escape(cb.id)}"]`),
      cb.closest("label"),
      cb.closest("mat-checkbox, mat-mdc-checkbox")?.querySelector(".mdc-checkbox, .mat-checkbox-inner-container, .mdc-checkbox__background, label"),
      cb.closest("mat-checkbox, mat-mdc-checkbox"),
      cb.closest(".chakra-checkbox")?.querySelector(".chakra-checkbox__control"),
    ].filter(Boolean);
    for (const a of alvos) { if (cb.checked === checked) break; try { a.click(); } catch (_) {} }
    if (cb.checked !== checked) { try { cb.click(); } catch (_) {} }
    if (cb.checked !== checked) {
      try {
        const s = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "checked").set;
        s.call(cb, checked);
        cb.dispatchEvent(new Event("click", { bubbles: true }));
        cb.dispatchEvent(new Event("input", { bubbles: true }));
        cb.dispatchEvent(new Event("change", { bubbles: true }));
      } catch (_) {}
    }
    return cb.checked === checked;
  }

  // Injeta um arquivo (currículo) num <input type=file>. Content scripts NÃO podem
  // setar input.files direto, mas PODEM via DataTransfer — o Selenium usava send_keys;
  // aqui é o equivalente. `resume` = { dataUrl, name, type } vindo do storage.
  async function uploadArquivo(input, resume) {
    if (!input || !resume?.dataUrl) return false;
    try {
      const blob = await (await fetch(resume.dataUrl)).blob();
      const file = new File([blob], resume.name || "curriculo.pdf", { type: resume.type || blob.type || "application/pdf" });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return input.files.length > 0;
    } catch (_) {
      return false;
    }
  }

  // Entre vários containers candidatos (form/modal/main), escolhe o que TEM MAIS campos.
  // O `querySelector` simples pegava o PRIMEIRO no DOM — que pode ser um form/modal VAZIO
  // (busca no topo, toast/portal Chakra) e fazia o preenchedor "não ver" os campos da
  // etapa (ex.: GeekHunter não preenchia celular/salário; Gupy não respondia perguntas).
  // Fallback: document.body.
  function melhorContainer(sel = "form, [role='dialog'], main") {
    const cands = [...document.querySelectorAll(sel)].filter((e) => isVisible(e));
    let best = null, n = -1;
    for (const c of cands) {
      const k = c.querySelectorAll("input, select, textarea, [role='radio'], [role='combobox'], [role='option']").length;
      if (k > n) { n = k; best = c; }
    }
    return best && n > 0 ? best : document.body;
  }

  // Igual findByText, mas retorna TODOS os clicáveis visíveis que casam (não só o 1º).
  // Usado quando a página repete o botão (ex.: Gupy tem "Salvar e continuar" no topo
  // sticky E no rodapé) e queremos o ÚLTIMO (CTA primário) em vez do sticky.
  function findButtonsByText(texts, { root = document, sel = "button, a, [role='button']" } = {}) {
    const alvos = (Array.isArray(texts) ? texts : [texts]).map((t) => t.toLowerCase());
    const out = [];
    for (const el of root.querySelectorAll(sel)) {
      if (!isVisible(el)) continue;
      const t = (el.innerText || el.textContent || el.getAttribute("aria-label") || "").trim().toLowerCase();
      if (alvos.some((a) => t.includes(a))) out.push(el);
    }
    return out;
  }

  // Detecta um CAPTCHA "não sou um robô" (reCAPTCHA v2 / hCaptcha / Turnstile) VISÍVEL na
  // página. NÃO resolve nada — serve pra PAUSAR e deixar o HUMANO resolver
  // (human-in-the-loop). O widget renderiza num iframe cross-origin, mas o container e o
  // <textarea> de resposta ficam no documento principal, então dá pra detectar por aqui.
  function captchaPresente() {
    const sels = [
      ".g-recaptcha", "#g-recaptcha", ".recaptcha-checkbox",
      "iframe[src*='recaptcha/api2/anchor']", "iframe[src*='recaptcha/api2/bframe']", "iframe[title*='reCAPTCHA' i]",
      ".h-captcha", "iframe[src*='hcaptcha.com']", "iframe[title*='hcaptcha' i]",
      ".cf-turnstile", "iframe[src*='challenges.cloudflare.com']",
    ];
    for (const s of sels) { const el = document.querySelector(s); if (el && isVisible(el)) return true; }
    return false;
  }

  // reCAPTCHA v2 preenche <textarea#g-recaptcha-response> (no documento PRINCIPAL, legível)
  // com um token ao ser resolvido; hCaptcha/Turnstile idem. Token não-vazio = resolvido.
  function captchaResolvido() {
    const t = document.querySelector("#g-recaptcha-response, textarea[name='g-recaptcha-response'], textarea[name='h-captcha-response'], textarea[name='cf-turnstile-response']");
    return !!(t && (t.value || "").trim());
  }

  window.OA = { sleep, isVisible, waitFor, click, clickForte, findByText, findButtonsByText, melhorContainer, setNativeValue, fillInput, selectOption, labelFor, headingLabel, bg, setChecked, uploadArquivo, captchaPresente, captchaResolvido };
})();
