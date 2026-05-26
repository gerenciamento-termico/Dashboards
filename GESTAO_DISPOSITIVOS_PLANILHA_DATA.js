window.GESTAO_DISPOSITIVOS_PLANILHA_DATA = {
  "fonte": "Planilha local: Conexão com vtc_stage (1).xlsx",
  "geradoEm": "26/05/2026 14:06:02",
  "ultimaAtualizacaoFonte": "18/05/2026 15:30:24",
  "summary": {
    "ARES": {
      "totalEstoque": 107,
      "loggersTransito": 1806,
      "loggersEntregues": 3149,
      "loggersRetornados": 1917,
      "registrosEntregas": 3149,
      "registrosEstoque": 5765,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": "26/05/2026 13:52",
      "totalRegistros": 8914
    },
    "ARES COM SONDA": {
      "totalEstoque": 78,
      "loggersTransito": 14,
      "loggersEntregues": 165,
      "loggersRetornados": 145,
      "registrosEntregas": 165,
      "registrosEstoque": 196,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": "26/05/2026 13:45",
      "totalRegistros": 361
    },
    "SENSOR VTC": {
      "totalEstoque": 135,
      "loggersTransito": 100,
      "loggersEntregues": 281,
      "loggersRetornados": 220,
      "registrosEntregas": 281,
      "registrosEstoque": 614,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": "26/05/2026 08:46",
      "totalRegistros": 895
    },
    "SHIELD": {
      "totalEstoque": 76,
      "loggersTransito": 0,
      "loggersEntregues": 30,
      "loggersRetornados": 27,
      "registrosEntregas": 30,
      "registrosEstoque": 355,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": "26/05/2026 13:37",
      "totalRegistros": 385
    },
    "SYOS": {
      "totalEstoque": 884,
      "loggersTransito": 326,
      "loggersEntregues": 1018,
      "loggersRetornados": 752,
      "registrosEntregas": 1018,
      "registrosEstoque": 2000,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": "26/05/2026 13:54",
      "totalRegistros": 3018
    },
    "ALL": {
      "totalEstoque": 1280,
      "loggersTransito": 2246,
      "loggersEntregues": 4643,
      "loggersRetornados": 3061,
      "registrosEntregas": 4643,
      "registrosEstoque": 8930,
      "semStatus": 0,
      "semData": 0,
      "estoqueSemQuantidade": 0,
      "ultimaAtualizacao": "26/05/2026 13:54",
      "totalRegistros": 13573
    }
  },
  "diagnostico": {
    "linhasPlanilha": 5995,
    "linhasComCodigoReferencia": 5691,
    "linhasSemCodigoReferencia": 304,
    "linhasComLoggerOuReferencia": 5691,
    "linhasSemLoggerOuReferencia": 304,
    "chavesDistintas": 4643,
    "chavesComTipoControleEntregas": 3695,
    "chavesComTipoEstoqueDataloggers": 948,
    "chavesComTipoInferidoPorPrefixo": 0,
    "retornoPorControleEntregas": 2828,
    "retornoPorEstoqueAtual": 233,
    "retornoPendenteOuSemMatch": 1582,
    "chavesPorTipo": {
      "ARES": 3149,
      "SYOS": 1018,
      "SENSOR VTC": 281,
      "ARES COM SONDA": 165,
      "SHIELD": 30
    },
    "fonteTransito": "vtc_stage.documentos",
    "criterioTransito": "chave nr_pedido + cd_referencia com dt_entregaefetiva vazia",
    "linhasTransitoDocumentos": 3374,
    "chavesTransitoDocumentos": 2246,
    "pedidosTransitoDocumentos": 350,
    "chavesTransitoPorTipo": {
      "ARES": 1806,
      "ARES COM SONDA": 14,
      "SENSOR VTC": 100,
      "SYOS": 326
    }
  },
  "alertas": [
    "Fonte carregada da planilha local Conexao com vtc_stage.",
    "Chave de consolidacao: nr_pedido + cd_referencia.",
    "Entregas da planilha foram deduplicadas por chave para indicadores e registros consolidados.",
    "Loggers em transito: chaves nr_pedido + cd_referencia com dt_entregaefetiva vazia em vtc_stage.documentos.",
    "Estoque foi consolidado do dashboard local ESTOQUE_DATALOGGERS.html.",
    "Status de retorno foi enriquecido pelo CONTROLE_ENTREGAS_20D.csv e, quando ausente, pelo status atual de estoque.",
    "304 linhas da planilha nao possuem cd_referencia e nao entram em indicadores por logger."
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
