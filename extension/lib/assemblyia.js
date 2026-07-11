const fs = require("fs");
const https = require("https");
const { By, until } = require("selenium-webdriver");

const ASSEMBLY_ENDPOINT = "https://api.assemblyai.com/v2";

/**
 * Baixa o áudio do reCAPTCHA
 */
async function downloadRecaptchaAudio(driver, outputFile = "audio.mp3") {

    await driver.switchTo().defaultContent();

    const frames = await driver.findElements(By.css("iframe"));

    for (const frame of frames) {

        const title = await frame.getAttribute("title");

        if (title && title.toLowerCase().includes("recaptcha")) {

            await driver.switchTo().frame(frame);

            try {

                const audio = await driver.wait(
                    until.elementLocated(By.css("audio")),
                    5000
                );

                const audioUrl = await audio.getAttribute("src");

                console.log("URL do áudio:", audioUrl);

                await new Promise((resolve, reject) => {

                    const file = fs.createWriteStream(outputFile);

                    https.get(audioUrl, response => {

                        response.pipe(file);

                        file.on("finish", () => {
                            file.close(resolve);
                        });

                    }).on("error", reject);

                });

                await driver.switchTo().defaultContent();

                return outputFile;

            } catch (e) {
                // continua procurando
            }

            await driver.switchTo().defaultContent();
        }
    }

    throw new Error("Áudio não encontrado.");
}

/**
 * Envia o arquivo para AssemblyAI
 */
async function uploadAudio(apiKey, filePath) {

    const audioBuffer = fs.readFileSync(filePath);

    const response = await fetch(`${ASSEMBLY_ENDPOINT}/upload`, {

        method: "POST",

        headers: {
            Authorization: apiKey,
            "Content-Type": "application/octet-stream"
        },

        body: audioBuffer
    });

    if (!response.ok) {

        throw new Error(await response.text());

    }

    const json = await response.json();

    return json.upload_url;
}

/**
 * Solicita a transcrição
 */
async function createTranscript(apiKey, uploadUrl) {

    const response = await fetch(`${ASSEMBLY_ENDPOINT}/transcript`, {

        method: "POST",

        headers: {
            Authorization: apiKey,
            "Content-Type": "application/json"
        },

        body: JSON.stringify({
            audio_url: uploadUrl,
            language_code: "pt"
        })

    });

    if (!response.ok) {

        throw new Error(await response.text());

    }

    const json = await response.json();

    return json.id;
}

/**
 * Aguarda a transcrição terminar
 */
async function waitTranscript(apiKey, transcriptId) {

    while (true) {

        const response = await fetch(
            `${ASSEMBLY_ENDPOINT}/transcript/${transcriptId}`,
            {
                headers: {
                    Authorization: apiKey
                }
            }
        );

        if (!response.ok) {

            throw new Error(await response.text());

        }

        const json = await response.json();

        if (json.status === "completed") {

            return json.text;

        }

        if (json.status === "error") {

            throw new Error(json.error);

        }

        await new Promise(r => setTimeout(r, 3000));
    }
}

/**
 * Função principal
 */
async function transcreverRecaptcha(driver, apiKey) {

    // 1. Baixa o áudio
    const arquivo = await downloadRecaptchaAudio(driver);

    console.log("Áudio salvo:", arquivo);

    // 2. Upload
    const uploadUrl = await uploadAudio(apiKey, arquivo);

    console.log("Upload concluído.");

    // 3. Cria a transcrição
    const transcriptId = await createTranscript(apiKey, uploadUrl);

    console.log("Transcript ID:", transcriptId);

    // 4. Aguarda finalizar
    const texto = await waitTranscript(apiKey, transcriptId);

    console.log("Texto:", texto);

    return texto;
}