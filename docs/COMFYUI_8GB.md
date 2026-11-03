# ComfyUI numa RTX 2060 Super (8 GB): imagens-chave com IPAdapter

## 1. Instalar o ComfyUI

Use a versão portátil para Windows com NVIDIA, disponível nos releases de https://github.com/comfyanonymous/ComfyUI.

Inicie com:

```
run_nvidia_gpu.bat --preview-method none
```

- A série 20 (Turing) não tem bf16, mas o ComfyUI escolhe fp16 sozinho.
- O SDXL com IPAdapter cabe em 8 GB no modo normal. Se aparecer "CUDA out of memory", inicie com `--lowvram`. Fica mais lento, mas funciona.
- Feche navegador com vídeo, jogos e outras abas pesadas enquanto gera: a VRAM é compartilhada com o desktop.

## 2. Nós e modelos (baixe e coloque à mão)

O V5 nunca baixa nada sozinho.

| O quê | Onde colocar |
|---|---|
| Pacote de nós **ComfyUI_IPAdapter_plus** (https://github.com/cubiq/ComfyUI_IPAdapter_plus), pelo ComfyUI-Manager ou com `git clone` | `ComfyUI/custom_nodes/` |
| `ip-adapter-plus_sdxl_vit-h.safetensors` (repositório h94/IP-Adapter no Hugging Face, pasta `sdxl_models`) | `ComfyUI/models/ipadapter/` |
| `CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors` (o encoder ViT-H indicado no README do IPAdapter_plus) | `ComfyUI/models/clip_vision/` |
| Um checkpoint **SDXL** | `ComfyUI/models/checkpoints/` |

**Checkpoint:** o SDXL base funciona, mas um checkpoint SDXL de ilustração ou pintura digital segue melhor o estilo `dark_cinematic_lofi_2d`. O nome do arquivo entra em `defaults.ckpt` nos três workflows de [`workflows/`](../workflows/), ou é passado como parâmetro `ckpt`.

## 3. Conferir antes de gerar

Com o ComfyUI aberto e a API do V5 rodando:

```
POST /api/comfyui/discover                              detecta o ComfyUI em 127.0.0.1:8188
GET  /api/comfyui/workflows/character_pose/check        lista nós e modelos que faltam, com a pasta certa
GET  /api/comfyui/workflows/environment/check
```

## 4. Fluxo das imagens-chave

1. **Âncora de identidade de cada personagem**, por um dos caminhos:
   - `POST /api/characters/{id}/generate-sheet` gera a folha de referência. Escolha a melhor com `POST /api/characters/{id}/anchor`.
   - `POST /api/characters/{id}/reference` envia uma imagem pronta, por exemplo uma feita no ChatGPT com os prompts do motor.

   **A âncora decide a cara do personagem na série inteira.** Vale gastar tempo nela. Se for uma folha deitada (frente, 3/4 e costas), o V5 usa só a vista de frente.
2. `POST /api/episodes/{id}/generate` planeja as cenas a partir do roteiro.
3. `GET /api/episodes/{id}/keyframes` mostra os fundos e poses que cada cena precisa e quais ainda faltam.
4. `POST /api/episodes/{id}/keyframes` gera só o que falta, nesta ordem:
   - gera o fundo de cada cenário;
   - gera cada pose com a âncora no IPAdapter;
   - remove o fundo cinza e corta as sobras;
   - salva tudo em `data/series/<serie>/imagens/`;
   - liga as cenas ao modo "imagens" do motor e refaz as prévias.
5. Na revisão, para refazer uma imagem específica:
   - apague o arquivo dela;
   - rode de novo com `{"only": ["roberta_frente_medo_em_pe"], "seed": 123}`.

As imagens ficam na biblioteca da série e são reaproveitadas em todos os episódios. Um personagem gerado uma vez se mantém igual daí em diante e não gasta GPU de novo.

## Olhos e boca (variantes por inpainting)

Para cada pose, a mesma tarefa gera mais duas imagens:

- **`<pose>_piscando`**: olhos fechados. O motor troca por ela por 0,12 s a cada ~3,6 s.
- **`<pose>_falando`**: boca aberta. O motor troca por ela enquanto a voz do personagem estiver alta.

Como funciona: o V5 acha a cabeça redonda (detecção de círculo) e desenha uma máscara só nos olhos ou só na boca,
sempre dentro da cabeça. O workflow [`face_inpaint.json`](../workflows/face_inpaint.json) refaz apenas essa área e
cola o resultado sobre o original. Fora da máscara, os pixels são idênticos, então a pose, a roupa e o recorte não
mudam e a troca não "pula" na tela. Usa só nós nativos do ComfyUI, sem pacote extra.

- Pose de costas não ganha variantes.
- Se a cabeça não for encontrada (por exemplo, rosto coberto), a variante não é gerada e a cena continua funcionando
  sem ela. O motivo aparece em `errors` no resultado da tarefa.
- Para desligar: `{"face_variants": false}`. Para refazer uma variante, apague o arquivo dela e rode de novo com
  `{"only": ["roberta_frente_medo_em_pe"]}`.
- **`denoise`** (padrão 0,72, em `face_inpaint.json`): se os olhos ou a boca não mudam o suficiente, suba para 0,8.
  Se o rosto muda de estilo, desça para 0,6.

## Ajustes finos

Os parâmetros ficam em `_lofi.defaults` de cada workflow, ou vão no corpo da chamada:

- **`ip_weight`** (padrão 0,75): quanto o IPAdapter puxa da âncora.
  - Se o rosto ou a roupa saem diferentes, suba para 0,85–0,9.
  - Se a pose fica igual à da âncora, baixe para 0,6.
- **`ip_end`** (padrão 0,85): até que ponto da geração a âncora atua. Valores menores deixam o prompt mandar mais nos detalhes finais.
- **Tamanho:** 832×1216 (pose) e 768×1344 (fundo) são resoluções nativas do SDXL. Não aumente em 8 GB. Se precisar de mais definição, faça um upscale depois.
