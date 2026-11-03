# Arquitetura do Motor Lo-Fi V5

```
Animation-Context/
├── external/motor-lofi/     submódulo: renderizador procedural, rig, áudio, cenários, séries antigas
├── backend/lofi_studio/
│   ├── api.py               FastAPI (rotas /api/*)
│   ├── config.py            configurações LOFI_* (.env)
│   ├── db.py, models.py     SQLModel: SQLite (dev) ou PostgreSQL (LOFI_DATABASE_URL)
│   ├── motor.py             ponte com o motor-lofi (import de lofi.* e lofi.render em subprocesso)
│   ├── style_master.py      presets de estilo e montagem de prompts
│   ├── pipeline.py          tarefas de produção (episódio, cena, imagens-chave, export, arte de personagem/cenário)
│   ├── story_analysis.py    roteiro -> beats: quem fala, quem está em cena, saídas, cenário; elenco no plano
│   ├── voices.py            falas (Chatterbox), cache, trilhas por personagem, tempo das cenas, legendas
│   ├── keyframes.py         cena -> fundo + poses no padrão de nomes do lofi/imagens.py; âncora do IPAdapter
│   ├── jobs.py              fila persistida no banco (backend local com threads)
│   ├── engines.py           catálogo dos serviços e de quem executa cada um
│   └── comfyui/
│       ├── client.py        API oficial: /prompt /history /view /upload/image /queue /interrupt /object_info /models /ws
│       ├── workflow.py      workflows API com marcadores $param e {{param}}, controle de seed
│       ├── dependencies.py  nós e modelos ausentes, com orientação de instalação (nunca baixa nada)
│       ├── registry.py      várias instâncias: detecção local, saúde, cache de capacidades, escolha
│       └── orchestrator.py  execução ponta a ponta: novas tentativas, cancelamento, VRAM, auditoria
├── backend/voice_worker/    worker do Chatterbox (roda no Python com torch; sem dependência do estúdio)
├── workflows/               workflows ComfyUI (formato API) usados pelas tarefas
└── docs/
```

## Decisões

- **Motor como submódulo, não cópia.** O V5 chama `lofi.autodirect.compile_story`, `lofi.studio.quality_report`,
  `lofi.imagens.MASTER_STYLE` e `python -m lofi.render`. Uma melhoria no motor chega ao V5 com
  `git submodule update --remote`.
- **Fichas travadas.** O `personagens.json` da pasta de trabalho é gerado a partir do banco a cada render. O
  `lofi.render` já dá prioridade a esse arquivo. Para alterar o visual de uma ficha travada pela API, é preciso
  mandar `unlock=true`.
- **Prévia sem FFmpeg.** Cada cena gera um quadro pelo `lofi.render --test`. O vídeo final exige FFmpeg.
- **ComfyUI por workflow API.** Qualquer workflow exportado com "Export (API)" funciona. Os parâmetros entram por
  marcadores. A chave `_lofi` guarda os metadados (padrões e parâmetros obrigatórios) e é removida antes do envio.
- **Dependências.** A checagem compara cada entrada de modelo com as opções informadas pelo `/object_info`
  da instância, inclusive os modelos escolhidos por parâmetro com valor padrão. Também lista os nós ausentes,
  com o pacote provável. Nada é baixado nem instalado.
- **Segurança de rede.** A detecção automática varre só `127.0.0.1`. Instâncias remotas entram apenas por
  `LOFI_COMFYUI_INSTANCES` ou `POST /api/comfyui/instances`.
- **Novas tentativas.** Falhas de execução, indisponibilidade e timeout geram nova tentativa, e cada uma vira um
  `GenerationRecord`. Um workflow recusado pelo servidor (`node_errors`) não é repetido. Uma seed fixada pelo
  usuário é mantida entre tentativas.
- **Fila.** Para desenvolvimento há o backend `local` (threads). O estado de cada tarefa fica na tabela `job`, então
  trocar por Celery + Redis não muda a API. O backend Celery **ainda não foi implementado**. Tarefas que estavam
  rodando quando o servidor caiu viram `failed` e podem ser repetidas.

- **Análise do roteiro antes do compilador do motor.** O `lofi.autodirect` põe um único ator por plano. O V5
  identifica falas, rubricas de emoção, quem é citado, quem entra e sai e as quebras de cena. Depois troca o
  ator único pelo elenco certo, sem alterar o motor:
  - até 3 por plano, com lados estáveis na tela (regra dos 180°);
  - 3/4 frente a frente;
  - `camera.close` ou `camera.alvo` em quem fala.
- **Voz em processo separado.** O Chatterbox roda no Python configurado em `LOFI_CHATTERBOX_PYTHON` e conversa
  com a API por JSON. Antes de gerar, o V5 libera a VRAM do ComfyUI local. Depois da voz, a duração de cada cena
  passa a ser a da fala real, e o spec ganha `voices`, `clips` e `subs` no formato do `lofi.render`. Detalhes em
  [VOZES.md](VOZES.md).
- **Imagens-chave, não vídeo gerado.** A IA gera o fundo de cada cenário e as poses de cada personagem, com a
  identidade travada por uma imagem âncora no IPAdapter. O motor anima a composição no modo "imagens": câmera,
  paralaxe, respiração, piscar e quem fala se mexendo. As imagens ficam na biblioteca da série e são
  reaproveitadas entre episódios. Guia para GPUs de 8 GB: [COMFYUI_8GB.md](COMFYUI_8GB.md).

## Estado dos serviços

`GET /api/engines` lista os 21 serviços com o status de cada um:

| status | significado |
|---|---|
| ready | implementado no V5 (ou envolvendo o motor), com testes |
| motor | feito pelo renderizador procedural do motor durante o render |
| partial | existe uma versão básica; a versão generativa ainda não |
| planned | ainda não existe |

Ainda não existem: painel React (Production Dashboard), Video Generation via ComfyUI, segmentação por IA e
backend Celery.

## Dependências

| pacote | uso |
|---|---|
| fastapi, uvicorn | API |
| sqlmodel, pydantic-settings | banco e configuração |
| httpx, websockets | cliente ComfyUI (HTTP e progresso em tempo real) |
| pillow, numpy, scipy | motor-lofi |
| opencv-python-headless | reservado para segmentação e QC de imagem |
| FFmpeg (sistema) | export do vídeo |
| psycopg (opcional) | PostgreSQL |
