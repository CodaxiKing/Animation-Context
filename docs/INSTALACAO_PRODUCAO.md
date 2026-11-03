# Instalar na máquina de produção

Passo a passo para deixar outro computador (com a GPU) produzindo episódios. Testado no projeto com placa de
8 GB (RTX 2060 Super) como referência mínima.

## 0. Requisitos

| Item | Mínimo |
|---|---|
| GPU NVIDIA | 8 GB de VRAM (12 GB+ deixa o vídeo bem mais rápido) |
| Disco livre | **40 GB**: ~25 GB de modelos do ComfyUI, ~4 GB do Chatterbox, o resto para episódios |
| Python | 3.12 para o estúdio; 3.11 para o Chatterbox (ambiente separado) |
| Git | com acesso aos repositórios Animation-Context e motor-lofi |

## 1. Estúdio (este repositório)

```bash
git clone --recursive https://github.com/CodaxiKing/Animation-Context.git
cd Animation-Context
py -3.12 -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt
copy .env.example .env
```

O `imageio-ffmpeg` já traz o FFmpeg. Instalar o FFmpeg completo no PATH (com `ffprobe`) é opcional e deixa o
render mais rápido.

## 2. ComfyUI e modelos

Instale o ComfyUI portátil para NVIDIA e o pacote de nós **ComfyUI_IPAdapter_plus**. Coloque os modelos nas pastas
(detalhes e links em [COMFYUI_8GB.md](COMFYUI_8GB.md)):

| Para quê | Arquivo | Pasta em `ComfyUI/models/` |
|---|---|---|
| Imagens (todas) | um checkpoint SDXL de ilustração | `checkpoints/` |
| Identidade dos personagens | `ip-adapter-plus_sdxl_vit-h.safetensors` | `ipadapter/` |
| Identidade dos personagens | `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` | `clip_vision/` |
| Vídeo | `wan2.2_ti2v_5B_fp16.safetensors` | `diffusion_models/` |
| Vídeo | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `text_encoders/` |
| Vídeo | `wan2.2_vae.safetensors` | `vae/` |

Se o checkpoint SDXL tiver outro nome, troque `defaults.ckpt` nos arquivos de [`workflows/`](../workflows/)
(character_sheet, character_pose, environment, face_inpaint, harmonize).

Inicie com `run_nvidia_gpu.bat --preview-method none`. Se faltar VRAM, acrescente `--lowvram`.

## 3. Vozes (Chatterbox)

Siga [VOZES.md](VOZES.md) e ponha no `.env`:

```
LOFI_CHATTERBOX_PYTHON=C:/tts/.venv/Scripts/python.exe
```

## 3b. Opcionais

- **Fila com Celery e Redis** (para várias máquinas ou para a fila sobreviver a um reinício da API):
  ```
  LOFI_QUEUE_BACKEND=celery
  LOFI_REDIS_URL=redis://localhost:6379/0
  LOFI_DATABASE_URL=postgresql+psycopg://usuario:senha@host:5432/lofi   (necessário com mais de uma máquina)
  ```
  Em cada máquina com GPU: `cd backend` e `..\.venv\Scripts\celery -A lofi_studio.celery_app worker --pool=solo`.
  `--pool=solo` roda uma tarefa por vez, que é o certo para uma GPU.
- **Segmentação por IA** (poses com fundo detalhado em vez de cinza liso): `..\.venv\Scripts\pip install rembg`.
  Instalar é a autorização: na primeira vez, o rembg baixa o modelo u2net (~170 MB). Sem ele, essas poses ficam
  sem recorte e o controle de qualidade avisa.

## 4. Conferir tudo

Com o ComfyUI aberto:

```bash
cd backend
..\.venv\Scripts\python -m lofi_studio.preflight
```

A checagem diz, item por item, o que está ok e o que falta, com a correção ao lado: Python, motor, FFmpeg, disco,
ComfyUI, cada workflow (nós e modelos) e o Chatterbox com CUDA. Produza só quando aparecer
**PRONTO PARA PRODUZIR**.

## 5. Produzir um episódio

Copie [`examples/producao/`](../examples/producao/) para uma pasta sua. Ponha nela a imagem de referência e a
amostra de voz de cada personagem e o roteiro. Ajuste o `producao.json` e rode:

```bash
cd backend
..\.venv\Scripts\python -m lofi_studio.produzir C:\minha-serie\producao.json
```

Isso faz, em ordem:
1. série, personagens (referência e voz) e cenários;
2. planejamento das cenas: quem fala, quem está em cena, ações;
3. imagens-chave: poses com a cara travada, fundos, olhos fechados e boca aberta;
4. vozes, com a duração das cenas ajustada à fala;
5. clipes Wan 2.2: ações encadeadas, por exemplo beber e depois andar até a mesa;
6. MP4 final.

Pode rodar de novo à vontade: o que já existe é reaproveitado. Para refazer uma imagem, apague o arquivo dela em
`data/series/<série>/imagens/`. Para refazer as falas, mude a `seed` da voz.

### `steps` no producao.json

| Chave | Valores | Efeito |
|---|---|---|
| `clips` | `"action"` (padrão), `"all"`, `false` ou lista de cenas `[0, 3]` | `action`: só cenas com movimento (correr, andar, pedalar...); `all`: episódio inteiro animado, como os vídeos de referência |
| `start_frame` | `"motor"` (padrão) / `"generated"` | de onde o vídeo parte: a cena montada pelo motor (recortes sobre o fundo, sem legendas nem selo) ou a cena **desenhada inteira** pelo ComfyUI, com o personagem dentro do cenário (mais próximo dos vídeos de referência, mais lento) |
| `harmonize` | `true` / `false` | passada leve de img2img no quadro inicial (modo `motor`) para o recorte pegar a luz do cenário |
| `voices` | `true` / `false` | sem Chatterbox ou sem amostras, a etapa é pulada com aviso |
| `character_sheets` | `true` / `false` | gera a folha de referência de quem não tem imagem de referência |
| `allow_unapproved` | `true` / `false` | exporta sem esperar a aprovação cena a cena |

**Tempo estimado (8 GB):** com `clips: "all"`, cada cena leva alguns minutos de vídeo. Um episódio de ~2,5 min
leva de 1 a 2 horas. Deixe rodando à noite.

## Como o vídeo final é montado

1. **Quadro inicial:** o motor monta a cena **no instante em que o clipe começa**, só com cenário, personagens, luz
   e câmera. Saem do quadro as legendas, o selo do episódio, o contador e as barras. Com
   `start_frame: "generated"`, o ComfyUI desenha a cena inteira (workflow `scene_full`).
2. **Wan 2.2:** anima esse quadro, encadeando as ações da linha.
3. **Exportação:** o motor usa o clipe como **fundo** daquele plano e desenha por cima, como em qualquer cena,
   legendas, selo, contador, barras, efeitos de susto, cor e vinheta. Se o clipe for mais curto que a fala, o
   último quadro fica parado até o fim do plano.

Um clipe que sai corrompido (o Wan falhou no meio) não entra na cena. O erro aparece em `errors` e a cena continua
com a imagem animada pelo motor.

## Painel

```bash
cd frontend
npm install
npm run build
```

Abra http://127.0.0.1:8000/ com a API rodando (`.venv\Scripts\uvicorn lofi_studio.api:app --app-dir backend`).

Fluxo no painel:
1. Crie a série (ou importe do motor).
2. Em **Elenco**, envie a referência, a voz e o pronome de cada personagem.
3. Em **Estilo e interface**, escolha o estilo (escuro ou claro) e ligue a interface de sobrevivência e a caixa
   de diálogo.
4. Escreva o roteiro e rode as etapas 1 a 5.
5. Em **Qualidade das imagens**, confira as poses e aprove ou corrija cena a cena antes de exportar.

## Produção automática no estilo da sua imagem (TikTok, Reels, Shorts)

1. Na série, aba **Estilo e produção**:
   - escolha o estilo **Gritty Comic 2D**, ou o que combinar com a sua imagem;
   - envie a **imagem de referência de estilo**;
   - escolha a qualidade do vídeo e se aparece o selo "EP X".
2. No **Elenco**, cada personagem precisa da imagem de referência (a cara dele) e, para falar, da amostra de voz.
3. No episódio, clique em **Produzir episódio**. O sistema:
   - divide o roteiro em cenas e descobre quem está em cada uma;
   - gera as vozes, e a duração de cada cena passa a seguir a fala;
   - **desenha cada cena** no estilo da sua imagem, com os personagens da cena dentro dela. Com 2 personagens,
     cada um fica com a cara travada no seu lado da tela;
   - **anima** cada cena com o Wan 2.2, encadeando as ações da linha (por exemplo, beber e depois andar);
   - monta o **MP4 vertical 1080×1920** com legenda normal.

Workflows usados: `scene_styled_0` (sem personagem), `scene_styled_1`, `scene_styled_2` e `wan22_i2v`. Para
regerar esses workflows depois de mudar a base, rode `python tools/gerar_workflows_estilo.py`.

**Limites:**
- Até 2 personagens têm a cara travada por cena. Do terceiro em diante, vêm só da descrição.
- Nos clipes, a boca não segue a voz. A legenda e a voz estão certas, mas a boca mexe de forma genérica.
- Sem vozes (sem Chatterbox ou sem amostras), o episódio sai sem legenda, porque a legenda vem do áudio.

## Escrevendo o roteiro para animar bem

```
CENA 1 · COZINHA
Bia toma um gole de café e anda até a mesa.      -> 2 clipes encadeados: beber, depois andar
BIA (com medo): Ninguém sai daqui.               -> fala com voz e boca, câmera nela
Marcos se levanta e vai até a janela.            -> levantar, depois andar até a janela
João corre pela rua, depois se esconde atrás do carro.
```

- **Uma linha é um plano.** Para encadear ações, use "e", "depois" ou "e então". São no máximo 3 por linha.
- **Verbos reconhecidos**, no presente, passado ou gerúndio: beber/tomar café, comer, correr, fugir, pedalar,
  dirigir, andar/caminhar/ir até, sentar, levantar, abrir, fechar, pegar, ligar, olhar o celular, olhar,
  esconder/agachar, pular, cair no chão, chorar, gritar, abraçar.
- **Lugares e objetos traduzidos para o vídeo:** mesa, porta, janela, geladeira, carro, rua, cozinha, sofá, cama,
  portão, escada, corredor, sala, quarto, garagem, estrada, pia, fogão... O que não estiver na lista segue em
  português no prompt.
