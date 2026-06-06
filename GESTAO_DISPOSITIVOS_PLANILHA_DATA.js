window.GESTAO_DISPOSITIVOS_PLANILHA_DATA = {
  "fonte": "Planilha local: Conexão com vtc_stage (1).xlsx",
  "geradoEm": "05/06/2026 21:11:15",
  "ultimaAtualizacaoFonte": "18/05/2026 15:30:24",
  "summary": {
    "ARES": {
      "totalEstoque": 0,
      "loggersTransito": 32,
      "loggersEntregues": 3148,
      "loggersRetornados": 5,
      "registrosEntregas": 3148,
      "registrosEstoque": 0,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": null,
      "totalRegistros": 3148
    },
    "ARES COM SONDA": {
      "totalEstoque": 0,
      "loggersTransito": 0,
      "loggersEntregues": 166,
      "loggersRetornados": 0,
      "registrosEntregas": 166,
      "registrosEstoque": 0,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": null,
      "totalRegistros": 166
    },
    "SENSOR VTC": {
      "totalEstoque": 0,
      "loggersTransito": 0,
      "loggersEntregues": 281,
      "loggersRetornados": 0,
      "registrosEntregas": 281,
      "registrosEstoque": 0,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": null,
      "totalRegistros": 281
    },
    "SHIELD": {
      "totalEstoque": 0,
      "loggersTransito": 0,
      "loggersEntregues": 30,
      "loggersRetornados": 0,
      "registrosEntregas": 30,
      "registrosEstoque": 0,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": null,
      "totalRegistros": 30
    },
    "SYOS": {
      "totalEstoque": 0,
      "loggersTransito": 0,
      "loggersEntregues": 1018,
      "loggersRetornados": 1,
      "registrosEntregas": 1018,
      "registrosEstoque": 0,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": null,
      "totalRegistros": 1018
    },
    "ALL": {
      "totalEstoque": 0,
      "loggersTransito": 32,
      "loggersEntregues": 4643,
      "loggersRetornados": 6,
      "registrosEntregas": 4643,
      "registrosEstoque": 0,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": null,
      "totalRegistros": 4643
    }
  },
  "operacionalEstoqueFonte": "ESTOQUE_DATALOGGERS.html :: const STATES",
  "operacionalEstoque": {},
  "diagnostico": {
    "linhasPlanilha": 5995,
    "linhasComCodigoReferencia": 5691,
    "linhasSemCodigoReferencia": 304,
    "linhasComLoggerOuReferencia": 5691,
    "linhasSemLoggerOuReferencia": 304,
    "chavesDistintas": 4643,
    "chavesComTipoControleEntregas": 9,
    "chavesComTipoEstoqueDataloggers": 0,
    "chavesComTipoInferidoPorPrefixo": 4634,
    "retornoPorControleEntregas": 6,
    "retornoPorEstoqueAtual": 0,
    "retornoPendenteOuSemMatch": 4637,
    "chavesPorTipo": {
      "ARES": 3148,
      "SYOS": 1018,
      "SENSOR VTC": 281,
      "ARES COM SONDA": 166,
      "SHIELD": 30
    },
    "fonteTransito": "vtc_stage.documentos",
    "criterioTransito": "chave nr_pedido + '-' + cd_referencia agrupada primeiro; conta se existe dt_coletaefetiva e nao existe dt_entregaefetiva",
    "linhasTransitoDocumentos": 37,
    "chavesTransitoDocumentos": 32,
    "pedidosTransitoDocumentos": 22,
    "chavesTransitoPorTipo": {
      "ARES": 32
    }
  },
  "alertas": [
    "Fonte carregada da planilha local Conexao com vtc_stage.",
    "Chave de consolidacao: nr_pedido + cd_referencia.",
    "Entregas da planilha foram deduplicadas por chave para indicadores e registros consolidados.",
    "Loggers em transito: chaves nr_pedido + '-' + cd_referencia agrupadas em vtc_stage.documentos; conta quando existe dt_coletaefetiva e nao existe dt_entregaefetiva.",
    "Estoque foi consolidado do dashboard local ESTOQUE_DATALOGGERS.html.",
    "Status de retorno foi enriquecido pelo CONTROLE_ENTREGAS_20D.csv e, quando ausente, pelo status atual de estoque.",
    "304 linhas da planilha nao possuem cd_referencia e nao entram em indicadores por logger.",
    "4634 chaves tiveram tipo inferido pelo prefixo da tag por falta de match nos dashboards locais.",
    "Nao foi possivel carregar dados operacionais de ESTOQUE_DATALOGGERS.html (const STATES)."
  ],
  "campos": {
    "pedido": "nr_pedido",
    "logger": "cd_referencia",
    "tipo": "CONTROLE_ENTREGAS_20D.csv / ESTOQUE_DATALOGGERS.html / inferencia por prefixo",
    "coleta": "dt_coletaefetiva",
    "entrega": "dt_entregaefetiva",
    "estoque": "ESTOQUE_DATALOGGERS.html",
    "retorno": "Status Retorno / status atual em estoque",
    "chave": "normalize(nr_pedido) + '|' + normalize(cd_referencia)"
  }
};
window.GESTAO_DISPOSITIVOS_STAGE_DATA = window.GESTAO_DISPOSITIVOS_PLANILHA_DATA;
