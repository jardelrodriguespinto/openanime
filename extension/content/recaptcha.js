// reCAPTCHA v2 / Enterprise (áudio) — roda DENTRO dos iframes do reCAPTCHA
// (google.com/recaptcha/* e recaptcha.net/recaptcha/*, injetado com all_frames). O
// #audio-response, o <audio> e o botão "Verificar" são same-origin AQUI, mas cross-origin
// pro content script do Indeed — por isso o solve mora neste arquivo. Fluxo: o desafio
// abre na IMAGEM → clica "Receber um desafio de áudio" (#recaptcha-audio-button) → baixa o
// áudio (mesmo-origem deste frame) → background faz UPLOAD dos bytes na AssemblyAI (a URL
// enterprise/payload não é buscável remotamente) → preenche a resposta e clica Verificar.
// IMPORTANTE: o bframe renderiza o desafio DEPOIS do document_idle → esperamos aparecer
// (waitFor) em vez de checar uma vez só e desistir. Falhou? NÃO força → fallback humano.
(function () {
  const OA = window.OA;
  if (!OA) return;
  const log = (...a) => { try { console.log("[AutoApply][recaptcha]", ...a); } catch (_) {} };
  const running = async () => (await OA.bg({ type: "run.isRunning" }))?.running;

  // ArrayBuffer → base64 (em blocos, p/ áudio de dezenas de KB) p/ trafegar no sendMessage.
  function bufParaB64(buf) {
    const bytes = new Uint8Array(buf);
    let bin = "";
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    return btoa(bin);
  }

  const audioBtnSel = "#recaptcha-audio-button, .rc-button-audio, button[title*='áudio' i], button[title*='audio' i]";

  // Lê a URL do áudio do desafio atual — prefere o link .mp3, senão o <audio src>. Espera
  // aparecer E ser DIFERENTE da anterior (numa nova rodada o reCAPTCHA troca o src in-place).
  async function urlAudio(anterior) {
    for (let i = 0; i < 24; i++) {
      const u = document.querySelector(".rc-audiochallenge-tdownload-link")?.getAttribute("href")
             || document.querySelector("#audio-source")?.getAttribute("src") || "";
      if (u && u !== anterior) return u;
      await OA.sleep(500);
    }
    return "";
  }

  async function resolverAudio() {
    let anterior = "";
    for (let round = 0; round < 3; round++) {
      if (!(await running())) return;

      // 1) garante o modo ÁUDIO. Se ainda não há #audio-response, estamos na imagem →
      //    clica "Receber um desafio de áudio" (visível na imagem; some quando já é áudio).
      if (!document.querySelector("#audio-response")) {
        const btn = await OA.waitFor(audioBtnSel, { timeout: 8000 });
        if (!btn) { log("botão de áudio não encontrado — humano resolve"); return; }
        log("desafio de imagem → clicando 'desafio de áudio'");
        OA.click(btn);
        if (!(await OA.waitFor("#audio-response", { timeout: 6000 }))) {
          OA.clickForte(btn); // reCAPTCHA às vezes ignora o .click() simples
          if (!(await OA.waitFor("#audio-response", { timeout: 6000 }))) { log("não abriu o áudio"); return; }
        }
      }

      // 2) URL do áudio desta rodada
      const audioUrl = await urlAudio(anterior);
      if (!audioUrl) { log("sem URL de áudio (bloqueio/queries automáticas?) → humano"); return; }
      anterior = audioUrl;
      log("round", round, "áudio:", audioUrl.slice(0, 72));

      // 3) baixa os bytes AQUI (mesmo-origem: este frame é recaptcha.net/google.com) e manda
      //    pro background transcrever (upload na AssemblyAI). CSP barrou o fetch? cai p/ URL.
      let audioB64 = "";
      try {
        const buf = await (await fetch(audioUrl, { credentials: "same-origin" })).arrayBuffer();
        if (buf?.byteLength) { audioB64 = bufParaB64(buf); log("áudio baixado:", buf.byteLength, "bytes"); }
      } catch (e) { log("fetch do áudio falhou (tentará por URL):", e?.message); }

      const t = await OA.bg({ type: "assemblyia.match", payload: { audioUrl, audioB64 } });
      const texto = (t?.texto || "").trim();
      if (!texto) { log("transcrição vazia/erro:", t?.erro || "(sem texto)"); return; }
      log("transcrição:", texto);

      // 4) preenche #audio-response e clica "Verificar"
      const input = document.querySelector("#audio-response");
      if (!input) { log("#audio-response sumiu"); return; }
      OA.fillInput(input, texto);
      await OA.sleep(500);
      const verify = document.querySelector("#recaptcha-verify-button") || OA.findByText(["verificar", "verify"]);
      if (verify) { log("clicando Verificar"); OA.click(verify); } else { log("botão Verificar não encontrado"); return; }
      await OA.sleep(2800);

      // Resolvido? o desafio de áudio some (bframe recolhe / token vai pro doc principal).
      if (!document.querySelector("#audio-response")) { log("resolvido ✅"); return; }
      // Ainda no desafio → resposta errada; tenta outra rodada com o novo áudio.
      const err = (document.querySelector(".rc-audiochallenge-error-message")?.innerText || "").trim();
      log("ainda no desafio (", err || "sem msg", ") → nova rodada");
      await OA.sleep(800);
    }
    log("esgotou as tentativas de áudio — humano resolve");
  }

  (async () => {
    if (!(await running())) { log("automação parada — ignorando", location.href.slice(0, 50)); return; }

    // Espera o frame ficar pronto: anchor (checkbox) OU o desafio (imagem/áudio). O desafio
    // renderiza DEPOIS do document_idle, então esperar aqui é o que faz o solver disparar.
    const alvo = await OA.waitFor("#recaptcha-anchor, #rc-imageselect, #recaptcha-audio-button, #audio-response", { timeout: 15000, visible: false });
    if (!alvo) { log("frame sem reCAPTCHA reconhecível:", location.href.slice(0, 60)); return; }
    log("frame reCAPTCHA pronto:", location.href.slice(0, 70));

    // Frame do CHECKBOX ("não sou um robô") → só abre o desafio (carrega noutro iframe,
    // onde este mesmo script roda de novo e resolve).
    const anchor = document.querySelector("#recaptcha-anchor");
    if (anchor) {
      if (anchor.getAttribute("aria-checked") !== "true") { log("clicando checkbox 'não sou um robô'"); OA.click(anchor); }
      return;
    }

    await resolverAudio();
  })();
})();
