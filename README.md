# Banco Aura Dashboard

Este repositorio gera e publica os dashboards HTML:

- `ESTOQUE_DATALOGGERS.html`
- `CONTROLE_ENTREGAS_20D.html`
- `HTMLACOMPANHAMENTO.html`

## Atualizador automatico

Use sempre a partir da pasta `Banco_Aura`:

```bat
ATUALIZAR_TUDO_10_MIN.bat __CHECK__
ATUALIZAR_TUDO_10_MIN.bat __ONCE__
ATUALIZAR_TUDO_10_MIN.bat
```

Modos:

- `__CHECK__`: valida Python, Git, sintaxe dos scripts, `.env`, variaveis obrigatorias, conexoes e payload real do `HTMLACOMPANHAMENTO`, sem gerar commit nem push.
- `__ONCE__`: roda um ciclo completo uma unica vez, valida os HTMLs, commita somente se houver alteracao real e envia para `origin/main`.
- Sem argumento: abre uma janela em loop, com novo inicio a cada 600 segundos. O tempo de execucao do ciclo e descontado da espera.

Os logs ficam em `logs/atualizacao_YYYY-MM-DD.log` e nao sao versionados.

## Variaveis de ambiente

Copie `.env.example` para `.env` e preencha as senhas localmente. Nao grave senhas no codigo.

Uso dos prefixos:

- `AURA_DB_*`: banco principal do Aura usado por `HTMLACOMPANHAMENTO.py` e `gerar_dashboard_entregas.py`.
- `AURA_POSTGRES_*`: banco do portal/loggers usado por `gerar_html_estoque.py` e `gerar_html_controle_entregas.py`.
- `AURA_START_DATE`: data inicial do acompanhamento, no formato `YYYY-MM-DD`.
- `AURA_END_DATE`: opcional. Se ficar vazia, o acompanhamento usa a data atual.
- `AURA_SQLSERVER_CONN_STRING`: opcional, usado apenas para o card complementar de tempo de lancamento quando disponivel.

## Protecoes do fluxo

Antes de publicar, o BAT:

- sincroniza com `origin/main`;
- roda `py_compile` nos scripts principais;
- executa os geradores Python;
- valida existencia, tamanho, data de modificacao e payload dos HTMLs;
- bloqueia commit e push se algum script ou validacao falhar;
- ignora/restaura alteracoes que sejam somente horario de geracao;
- faz `git add` apenas dos arquivos necessarios;
- cria commit com mensagem `Atualiza dashboards Aura - YYYY-MM-DD HH:mm`;
- envia para `origin/main` apenas quando houver commit local pendente.
