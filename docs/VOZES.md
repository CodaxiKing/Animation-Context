# Vozes com Chatterbox (open-source, MIT)

O V5 gera as falas com o **Chatterbox Multilingual** (https://github.com/resemble-ai/chatterbox), que tem
português (`pt`), clona a voz a partir de uma amostra curta e permite controlar a intensidade da emoção. O modelo
roda num **Python separado**: o PyTorch fica fora da API e um erro de driver não derruba o estúdio.

## 1. Instalar (Windows, RTX 2060 Super)

O Chatterbox foi testado pelos autores em Python 3.11:

```bash
py -3.11 -m venv C:\tts\.venv
C:\tts\.venv\Scripts\pip install chatterbox-tts soundfile
```

No Windows, o `pip` costuma instalar o PyTorch **só para CPU**. Reinstale a versão CUDA do mesmo número que
o chatterbox instalou (veja com `pip show torch`):

```bash
C:\tts\.venv\Scripts\pip install --force-reinstall torch==<versão> torchaudio==<versão> --index-url https://download.pytorch.org/whl/cu124
C:\tts\.venv\Scripts\python -c "import torch; print(torch.cuda.is_available())"
```

A segunda linha precisa imprimir `True`. Na primeira geração, os pesos do modelo são baixados do Hugging Face
(alguns GB, uma vez só).

No `.env` do estúdio:

```
LOFI_CHATTERBOX_PYTHON=C:/tts/.venv/Scripts/python.exe
# LOFI_CHATTERBOX_MODEL=v3            (v2, v3 ou um arquivo .safetensors do repositório)
# LOFI_CHATTERBOX_CKPT_DIR=C:/tts/pt-br   (checkpoint local, por exemplo um fine-tune pt-BR)
```

**VRAM:** o modelo tem ~500M parâmetros e cabe em 8 GB, mas não ao mesmo tempo que o SDXL. Por isso, antes de
gerar vozes, o V5 pede ao ComfyUI local que descarregue os modelos (`POST /free`). Para desligar:
`LOFI_FREE_COMFYUI_VRAM_BEFORE_VOICE=false`.

## 2. Amostra de voz de cada personagem

```
POST /api/characters/{id}/voice-sample      arquivo .wav, .mp3, .flac ou .ogg
PUT  /api/characters/{id}/voice             {"exaggeration": 0.5, "cfg_weight": 0.5, "temperature": 0.8, "seed": 7}
```

- **6 a 15 segundos de fala limpa**: uma pessoa, sem música, sem eco, sem ruído.
- **O tom da amostra vira o tom da voz.** Uma amostra cansada e baixa dá falas cansadas e baixas. Para terror,
  grave num registro contido.
- **Narrador:** cadastre um personagem com slug `narrador` e envie a amostra dele. Ele lê as linhas sem fala, com
  voz de estúdio (sem o eco do cenário). Sem `narrador`, a narração fica só no tempo estimado, sem voz.
- **Direitos:** use só vozes que você tem direito de usar, como a sua, de quem autorizou ou vozes que você criou.

## 3. Gerar

```
GET  /api/episodes/{id}/voices      o que vai ser falado, por quem, com que emoção; quem está sem amostra
POST /api/episodes/{id}/voices      gera (só o que mudou), monta as trilhas e ajusta o tempo das cenas
POST /api/episodes/{id}/voices  {"seed": 42}    refaz tudo com outra seed (outra "leitura")
```

O que acontece:

1. **Emoção na voz.** Cada fala usa a emoção do personagem naquele plano, vinda da rubrica `BIA (com medo):` ou
   da análise do texto. Medo, raiva e choque aumentam a `exaggeration` e baixam o `cfg_weight` (ritmo mais solto).
2. **Cache.** Uma fala só é refeita se mudar o texto, a amostra, os parâmetros, a seed ou o modelo.
3. **Trilhas.** As falas de cada personagem são aparadas (silêncio do começo e do fim) e juntadas em
   `epNN/voices/<personagem>.wav`. O `lofi.render` usa essas trilhas para mixar o áudio e mexer a boca.
4. **Tempo.** Cada cena com fala passa a durar `0,15 s + fala + 0,45 s`, e as demais cenas andam junto, sem
   lacunas. Legendas de até 4 palavras saem sincronizadas com a fala.

Falhas por linha não param o episódio: o resultado da tarefa lista `errors` por cena, e essas cenas mantêm o
tempo antigo.
