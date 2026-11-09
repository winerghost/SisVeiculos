-- Migration: saudacoes, reforco do CHECK de texto (correcao da revisao de seguranca B1). Nova migration;
-- NAO edita a 20261115120100. Idempotente.
--
-- Motivo: o CHECK anterior barrava so controles ASCII ([[:cntrl:]]), 'http', '<' e '>'. Ficavam de fora
-- caracteres invisiveis/de formato (zero-width, marcas bidi: texto que parece uma coisa e e outra, ou que
-- inverte a ordem de exibicao), e formas de endereco sem 'http' ('www.dominio', e-mail com '@').
--
-- O que muda (as demais regras de ck_saudacoes_texto_* permanecem):
--   * ck_saudacoes_texto_sem_controle e substituido por ck_saudacoes_texto_sem_invisiveis, que bloqueia:
--     U+0001-U+001F e U+007F-U+009F (controles; U+0000 nem existe em text), U+200B-U+200F (zero-width e
--     marcas LRM/RLM), U+2028-U+2029 (separadores de linha/paragrafo), U+202A-U+202E e U+2066-U+2069
--     (embeddings/isolates bidi), U+2060 (word joiner) e U+FEFF (BOM/zero-width no-break space).
--     Os tres ultimos grupos (U+2028-2029, U+2060) alem do pedido: mesmo risco, custo zero.
--   * ck_saudacoes_texto_sem_endereco (novo): sem 'www.' (case-insensitive) e sem '@'.
-- Cada constraint entra NOT VALID e e validada em seguida (as linhas existentes precisam passar: a
-- validacao falha a migration se alguma violar). Nenhum dado e alterado.

alter table public.saudacoes drop constraint if exists ck_saudacoes_texto_sem_controle;
alter table public.saudacoes drop constraint if exists ck_saudacoes_texto_sem_invisiveis;
alter table public.saudacoes
  add constraint ck_saudacoes_texto_sem_invisiveis
  check (texto !~ '[\u0001-\u001f\u007f-\u009f​-‏  ‪-‮⁠⁦-⁩﻿]')
  not valid;
alter table public.saudacoes validate constraint ck_saudacoes_texto_sem_invisiveis;

alter table public.saudacoes drop constraint if exists ck_saudacoes_texto_sem_endereco;
alter table public.saudacoes
  add constraint ck_saudacoes_texto_sem_endereco
  check (texto !~* 'www\.' and position('@' in texto) = 0)
  not valid;
alter table public.saudacoes validate constraint ck_saudacoes_texto_sem_endereco;
