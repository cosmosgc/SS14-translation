# Tradutor de Arquivos FTL (Argos + Fluent)

Este projeto automatiza a tradução de arquivos `.ftl` (Fluent) do SS14, traduzindo do idioma base (padrão `en-US`) para um idioma alvo (padrão `pt-BR`).

Ele foi feito para:
- Ler todos os `.ftl` de uma pasta raiz de locale.
- Traduzir mensagem por mensagem usando **Argos Translate**.
- Preservar variáveis, expressões Fluent e tags de formatação.
- Não sobrescrever chaves que já existem no idioma de destino.
- Registrar chaves cuja tradução ficou idêntica ao texto original.

## O que o script preserva

O tradutor mantém intacto:
- Variáveis Fluent: `{$seedName}`, `{$seedNoun}`
- Expressões/métodos Fluent: `{ CAPITALIZE(THE($name)) }`
- Tags em texto: `[color=red]...[/color]`

## Requisitos

- Windows
- Python 3.10+ (recomendado)
- Modelo Argos (`.argosmodel`) compatível com o par de idiomas

## Download de modelos Argos

Você pode baixar modelos aqui:
- https://www.argosopentech.com/argospm/index/

Depois de baixar, coloque o arquivo `.argosmodel` na pasta `Tools/translation` (ou configure o caminho no `.env`).

## Instalação rápida

Na pasta `Tools/translation`:

1. Execute `install.bat`
2. Aguarde a instalação das dependências

## Execução rápida

Na pasta `Tools/translation`:

1. Execute `start.bat`

O script vai:
- Contar todos os arquivos `.ftl` da origem
- Mostrar barra de progresso com `tqdm`
- Traduzir apenas chaves faltantes no destino
- Exibir resumo final no console

## Execução manual (alternativa)

```powershell
cd g:\Development\ss14\Andromeda-v\Tools\translation
python -m pip install -r requirements.txt
python translate_ftl.py
```

## Configuração (`.env`)

Principais variáveis:

- `PROJECT_ROOT`: raiz do projeto (`Andromeda-v`)
- `SOURCE_LOCALE`: locale de origem (ex.: `en-US`)
- `TARGET_LOCALE`: locale de destino (ex.: `pt-BR`)
- `SOURCE_ARGOS_CODE`: código Argos de origem (ex.: `en`)
- `TARGET_ARGOS_CODE`: código Argos de destino (ex.: `pb`)
- `SOURCE_LOCALE_DIR`: pasta origem dos `.ftl`
- `TARGET_LOCALE_DIR`: pasta destino dos `.ftl`
- `ARGOS_MODEL_PATH`: caminho do `.argosmodel`
- `AUTO_INSTALL_MODEL`: instala automaticamente o modelo local (`true/false`)
- `ARGOS_DATA_DIR`: pasta interna de dados/cache do Argos
- `LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR`
- `LOG_FILE_PROGRESS`: mostra logs por arquivo (`true/false`)
- `SHOW_PROGRESS_BAR`: mostra barra de progresso (`true/false`)
- `UNCHANGED_LOG_PATH`: arquivo `.txt` para registrar traduções idênticas ao original

## Comportamento do merge de traduções

- Se a chave já existe no `.ftl` de destino: **pula**.
- Se a chave não existe: **adiciona** no fim do arquivo correspondente em destino.
- Se a tradução ficar igual ao original: registra em `UNCHANGED_LOG_PATH`.

## Observações

- A qualidade final depende do modelo Argos usado.
- Recomenda-se revisão humana após tradução automática.
- Se trocar idioma de destino, ajuste `TARGET_LOCALE` e `TARGET_ARGOS_CODE`.
