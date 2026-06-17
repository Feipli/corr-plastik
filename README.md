# Corr Plastik CT-e email downloader

Este workspace contem a lista de CT-e/NF/processo da Corr Plastik e um script
para baixar anexos relacionados a partir de uma caixa de email via IMAP.

## Arquivos

- `cte corr plastik.txt`: lista original com 75 documentos.
- `download_corr_plastik_cte.py`: automacao para procurar e baixar anexos.
- `downloads/corr-plastik-cte/`: pasta padrao de saida, ignorada pelo Git.

## Conferir a lista sem conectar ao email

```bash
python3 download_corr_plastik_cte.py --plan-only
```

## Configurar acesso ao email

Defina as variaveis abaixo antes de executar. Para Gmail ou Microsoft 365,
normalmente e necessario criar uma senha de app ou habilitar IMAP na conta.

```bash
export IMAP_HOST="imap.gmail.com"
export IMAP_USER="seu-email@empresa.com"
export IMAP_PASSWORD="senha-de-app-ou-senha-imap"
export IMAP_MAILBOX="INBOX"
```

Exemplos comuns:

- Gmail: `IMAP_HOST=imap.gmail.com`
- Outlook/Microsoft 365: `IMAP_HOST=outlook.office365.com`

## Baixar os anexos

```bash
python3 download_corr_plastik_cte.py
```

Para limitar a busca por data:

```bash
python3 download_corr_plastik_cte.py --since 2026-01-01
```

Para testar a busca sem gravar anexos:

```bash
python3 download_corr_plastik_cte.py --dry-run
```

## Resultado

Os anexos serao organizados assim:

```text
downloads/corr-plastik-cte/
  CRR0260/
    cte-8273_nf-35784/
      anexo.xml
      anexo.pdf
  manifest.csv
```

O `manifest.csv` indica, para cada CT-e, se encontrou anexos, de qual email
vieram e onde foram salvos.

## Observacao

O ambiente deste agente nao tem acesso a caixa de email. Por isso, o script deve
ser executado em um ambiente onde as credenciais IMAP estejam disponiveis.
