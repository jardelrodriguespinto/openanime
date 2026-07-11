# captcha_cv — estudo de visão computacional (dataset PRÓPRIO)

Projeto **isolado de aprendizado** para estudar reconhecimento de texto em imagem
(o "coração" técnico por trás de OCR e de CAPTCHAs de texto): pré-processamento com
OpenCV, segmentação de caracteres e um classificador CNN.

## ⚖️ Escopo ético (leia antes)

Este projeto treina e avalia **exclusivamente** em CAPTCHAs que **você gera localmente**
(`gerar_dataset.py`) ou em datasets públicos rotulados para pesquisa. Ele **não**:

- se conecta a nenhum site de produção de terceiros;
- baixa desafios de serviços reais (Indeed, Google reCAPTCHA, hCaptcha etc.);
- está integrado à extensão / automação de candidaturas.

Estudar a técnica em dados próprios é aprendizado de CV/ML. Apontá-la para o controle
anti-bot de um serviço que não é seu é outra coisa — e não é o que este repo faz.
Mantém assim.

## Estrutura

```
captcha_cv/
├── requirements.txt
├── gerar_dataset.py        # gera CAPTCHAs de texto locais → data/{train,val}/
├── data/                   # (gitignored) suas imagens: <texto>_<n>.png
└── src/
    ├── preprocess.py       # OpenCV: cinza, threshold, denoise, (segmentação TODO)
    ├── model.py            # esqueleto da CNN (arquitetura = seu TODO de estudo)
    ├── train.py            # loop de treino (loss/otim/step = seu TODO)
    └── predict.py          # inferência numa imagem (decodificação = seu TODO)
```

## Fluxo sugerido de estudo

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1) gera o dataset local (o resto do estudo parte daqui)
python gerar_dataset.py --n 2000 --chars 5

# 2) inspeciona o pré-processamento numa imagem
python src/preprocess.py data/train/<algum>.png --show

# 3) treina (você preenche os TODOs — é onde o aprendizado acontece)
python src/train.py

# 4) prediz
python src/predict.py data/val/<alguma>.png
```

Os TODOs em `model.py`/`train.py`/`predict.py` são de propósito: a parte que ensina
é você fechar a arquitetura, a loss e o loop de treino.
