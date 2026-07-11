// reCAPTCHA v2 (áudio) — roda DENTRO dos iframes do reCAPTCHA (google.com/recaptcha/*,
// injetado com all_frames). O #audio-response, o <audio> e o botão "Verificar" são
// same-origin AQUI, mas cross-origin pro content script do Indeed — por isso o solve
// mora neste arquivo, e não em content/indeed.js. Transcreve o áudio via AssemblyAI
// (background) e preenche a resposta. Se algo falhar, NÃO força nada: o fluxo do Indeed
// cai no fallback human-in-the-loop (esperar o token e, se não vier, pedir ajuda).
(function () {
  const OA = window.OA;
  if (!OA) return;

  const running = async () => (await OA.bg({ type: "run.isRunning" }))?.running;

  (async () => {
    if (!(await running())) return;

    // ── Frame do CHECKBOX ("não sou um robô") → só abre o desafio ────────────────
    // (o desafio de áudio carrega noutro iframe, onde este mesmo script roda de novo)
    const anchor = document.querySelector("#recaptcha-anchor");
    if (anchor) {
      if (anchor.getAttribute("aria-checked") !== "true") OA.click(anchor);
      return;
    }

    // ── Frame do DESAFIO (bframe) → resolve por áudio ───────────────────────────
    const ehDesafio = document.querySelector(
      "#rc-imageselect, #recaptcha-audio-button, .rc-audiochallenge-tabloop, #audio-response"
    );
    if (!ehDesafio) return;

    // 1) troca pro áudio (o padrão é o desafio de imagem). O botão é um ícone
    // (<button id="recaptcha-audio-button" class="rc-button-audio" title="Receber um
    // desafio de áudio">) e às vezes ignora o .click() simples do reCAPTCHA — usa clique
    // FORTE (sequência de ponteiro) e confirma que o desafio de áudio realmente abriu.
    if (!document.querySelector("#audio-source, #audio-response")) {
      const audioBtn = await OA.waitFor(
        "#recaptcha-audio-button, .rc-button-audio, button[title*='áudio' i], button[title*='audio' i]",
        { timeout: 8000 }
      );
      if (audioBtn) {
        OA.clickForte(audioBtn);
        if (!(await OA.waitFor("#audio-source, #audio-response", { timeout: 4000, visible: false }))) {
          OA.click(audioBtn); await OA.sleep(1500); // 2ª tentativa (fallback)
        }
      }
    }

    // 2) pega a URL do áudio (tag <audio id="audio-source"> ou link de download)
    const src = await OA.waitFor("#audio-source", { timeout: 8000, visible: false });
    const link = document.querySelector(".rc-audiochallenge-tdownload-link");
    const audioUrl = src?.getAttribute("src") || link?.getAttribute("href") || "";
    if (!audioUrl) return; // sem áudio (ex.: bloqueio "queries automáticas") → humano resolve

    // 3) transcreve via AssemblyAI (background — a URL do reCAPTCHA é pública/buscável)
    const t = await OA.bg({ type: "assemblyia.match", payload: { audioUrl } });
    const texto = (t?.texto || "").trim();
    if (!texto) return;

    // 4) preenche #audio-response e clica "Verificar"
    const input = await OA.waitFor("#audio-response", { timeout: 5000 });
    if (!input) return;
    OA.fillInput(input, texto);
    await OA.sleep(600);
    const verify = document.querySelector("#recaptcha-verify-button") || OA.findByText(["verificar", "verify"]);
    if (verify) OA.click(verify);
  })();
})();
