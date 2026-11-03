# Exemplo de produção

Copie esta pasta, coloque os arquivos que faltam e rode:

```bash
cd backend
..\.venv\Scripts\python -m lofi_studio.produzir C:\caminho\da\pasta\producao.json
```

Arquivos que **você** coloca nesta pasta (o `producao.json` aponta para eles):

| Arquivo | O que é |
|---|---|
| `refs/bia.png`, `refs/marcos.png` | imagem de referência de cada personagem (de corpo inteiro, de frente, no estilo da série). Define a cara do personagem em todas as cenas |
| `vozes/bia.wav`, `vozes/marcos.wav`, `vozes/narrador.wav` | 6 a 15 s de fala limpa de cada voz (docs/VOZES.md) |
| `ep01.txt` | o roteiro (formato em docs/INSTALACAO_PRODUCAO.md) |

Sem a imagem de referência, use `"character_sheets": true` em `steps` para gerar a folha de referência pelo ComfyUI.
Sem a amostra de voz, o personagem fica sem fala gerada e a cena usa o tempo estimado.
