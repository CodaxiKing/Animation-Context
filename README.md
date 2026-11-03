# Motor Lo-Fi V5 · Estúdio automatizado de animação 2D

Camada de produção sobre o [motor-lofi](https://github.com/CodaxiKing/motor-lofi): API, banco de dados, fila de tarefas,
orquestração do ComfyUI e identidade visual (STYLE_MASTER). O motor entra como **submódulo** em `external/motor-lofi`
e é usado como está: nada dele foi copiado ou reescrito, e as séries antigas continuam renderizando do mesmo jeito.

**Máquina de produção (GPU):** guia completo em [docs/INSTALACAO_PRODUCAO.md](docs/INSTALACAO_PRODUCAO.md). Checagem com
`python -m lofi_studio.preflight` e episódio inteiro com `python -m lofi_studio.produzir producao.json`, como no
exemplo em [examples/producao/](examples/producao/).

## Instalar

```bash
git submodule update --init --recursive
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt     # Linux/macOS: .venv/bin/pip
```

FFmpeg no PATH é necessário só para exportar o vídeo final. As prévias de cena não dependem dele.

## Painel

Interface web (React + TypeScript) para criar séries, cadastrar o elenco (referência e voz), escrever o roteiro,
rodar cada etapa da produção com barra de progresso, revisar e aprovar cena a cena e assistir o vídeo final.

```bash
cd frontend
npm install
npm run build
```

Depois de compilado, o painel é servido pela própria API em http://127.0.0.1:8000/. Para desenvolver com recarga
automática, use `npm run dev` (http://localhost:5173, com a API rodando na porta 8000).

## Rodar

```bash
.venv/Scripts/uvicorn lofi_studio.api:app --reload --app-dir backend
```

Documentação interativa: http://127.0.0.1:8000/docs

## Testes

```bash
cd backend && ../.venv/Scripts/python -m pytest -q
```

Os testes usam um ComfyUI falso (sem GPU) e o renderizador real do motor para as prévias.

## Formato do roteiro

Diretivas de interface (valem para a próxima linha; ative a interface na aba "Estilo e interface" da série):
`[ALERTA] título | texto` · `[NOTIFICACAO] app | título | texto` · `[CARTELA] Sábado | 07:00` · `[HORA] 07:00` ·
`[STATUS] CEL 20% ; ÁGUA 50% MEIA` · `[MISSAO] título | prazo` · `[TENSAO]` · `[GLITCH]` · `[SEM INTERFACE]`.
Pronomes: "Ela anda até a mesa" vai para a última personagem citada com pronome "ela" (cadastrado no elenco).

```
CENA 1 · CORREDOR                  cabeçalho: nova cena (CENA, ##, INT., EXT.)
A luz pisca no corredor.           narração: um plano por linha
João abre a porta e vê Bia.        quem é citado entra em cena
BIA (com medo): Fecha isso!        fala, com emoção opcional entre parênteses
João: Calma.                       também aceita "João — Calma."
Bia sai correndo.                  saída: Bia deixa a cena depois desta linha
```

Os nomes são reconhecidos pelo nome cadastrado, pelo primeiro nome ou pelo slug, sem diferenciar maiúsculas
e acentos. Em diálogo, todos que estão na cena aparecem, cada um sempre do mesmo lado da tela, e a câmera
vai para quem fala.

## Fluxo

1. `POST /api/series`: cria a série com o preset de estilo. Alternativa: `POST /api/motor/series/{slug}/import` traz uma série que já existe no motor.
2. `POST /api/series/{id}/characters`: cadastra a ficha travada. Para alterar o visual depois, é preciso mandar `unlock=true`.
3. `POST /api/series/{id}/environments`: cadastra o cenário, opcionalmente ligado a um dos cenários desenhados pelo motor.
4. `POST /api/series/{id}/episodes`: envia o roteiro.
5. `POST /api/episodes/{id}/generate`: dispara a tarefa que transforma o roteiro em planos, cenas e prévias. O episódio fica em `review`.
6. `GET /api/jobs/{id}` mostra o andamento. `GET /api/scenes/{id}/preview` mostra a prévia da cena.
7. `PATCH /api/scenes/{id}` aprova a cena ou registra uma observação. `POST /api/scenes/{id}/regenerate` corrige só aquela cena.
8. `POST /api/episodes/{id}/export` gera o MP4 pelo `lofi.render`. Exige todas as cenas aprovadas.

Vozes (Chatterbox, open-source): envie uma amostra por personagem (`POST /api/characters/{id}/voice-sample`) e rode
`POST /api/episodes/{id}/voices`. As falas são geradas com a emoção de cada plano e as cenas passam a durar o tempo
da fala. Instalação em [docs/VOZES.md](docs/VOZES.md).

Arte por IA: `POST /api/characters/{id}/generate-sheet` e `POST /api/environments/{id}/generate-art`, ambos via ComfyUI.

Mais detalhes em [docs/ARQUITETURA.md](docs/ARQUITETURA.md).
