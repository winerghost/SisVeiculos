-- Migration 18: recurso opcional QUILOMETRAGEM em requisicoes (km de saida e de chegada).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: CLAUDE.md ("Recursos opcionais"): com o recurso ligado em configuracoes.quilometragem,
-- km de saida e de chegada sao obrigatorios e o km de chegada nao pode ser menor que o de saida.
-- Desligado por padrao; desligar nunca apaga dados ja gravados.
--
-- FRONTEIRA BANCO x BACKEND (decisao):
--  * O banco garante as INVARIANTES DOS DADOS GRAVADOS, valendo sempre (ligado ou desligado):
--      - km_saida/km_chegada inteiros >= 0, nulos por padrao;
--      - km_chegada >= km_saida quando ambos existem;
--      - km_chegada exige km_saida;
--      - km_saida so existe com saida real registrada; km_chegada so com chegada real.
--    E a OBRIGATORIEDADE na transicao, lida de configuracoes.quilometragem NA HORA da escrita
--    (fonte da verdade unica, imune a corrida entre "backend leu o flag" e "admin ligou"):
--      - ligado: transicao para em_uso exige km_saida; para concluida exige km_chegada e km_saida
--        (km_saida pode vir junto da chegada se o recurso foi ligado com a viagem em curso);
--        falta => AG012.
--  * Desligado, o banco NAO recusa nem zera km valido: IGNORAR a entrada e RECUSAR os endpoints
--    do recurso e responsabilidade do BACKEND (CLAUDE.md: "a API ignora os campos"). Motivo: o
--    banco nao deve apagar/descartar silenciosamente dado (e desligar nunca apaga), e a
--    obrigatoriedade ja nao se aplica. Alternativa nao adotada: trigger recusar km novo com
--    recurso desligado (defesa em profundidade, ao custo de falhar se o backend esquecer de
--    filtrar); ver relatorio.
--  * Uma vez gravados, km_saida e km_chegada sao imutaveis (AG004), e so mudam de null para valor
--    junto de uma TRANSICAO (em_uso ou concluida). Assim toda escrita de km gera linha de auditoria.
--
-- Auditoria: a transicao para em_uso/concluida ja gera linha em auditoria; aqui requisicoes_auditar
-- passa a incluir km_saida/km_chegada em detalhes (jsonb, sem nulos). Odometro de veiculo nao e dado
-- pessoal; a linha continua sem nome/e-mail (so ids, status e km). Append-only intacto (a funcao so
-- INSERE; triggers de bloqueio e grants da auditoria nao mudam).
--
-- Erros: AG012 km obrigatorio (recurso ligado); AG004 km ja gravado/fora de transicao;
-- CHECKs (SQLSTATE 23514, nomes ck_requisicoes_km_*) para negativo, chegada < saida e incoerencia.
-- O backend traduz pelo nome da constraint.
-- RLS/grants: nao muda. Colunas novas herdam o deny-by-default da tabela (sem politicas nem grants
-- a anon/authenticated); service_role segue sem DELETE/TRUNCATE/TRIGGER/REFERENCES.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo). Colunas de km nao tem fuso.

-- ---------------------------------------------------------------------------
-- 1. Colunas (nulas por padrao; nao quebra linhas existentes)
-- ---------------------------------------------------------------------------
alter table public.requisicoes
  add column if not exists km_saida   integer,
  add column if not exists km_chegada integer;

comment on column public.requisicoes.km_saida is
  'Quilometragem na saida real (recurso Quilometragem). Nulo se o recurso estava desligado. Imutavel apos gravado.';
comment on column public.requisicoes.km_chegada is
  'Quilometragem na chegada real (recurso Quilometragem). Nulo se o recurso estava desligado. Imutavel apos gravado.';

-- ---------------------------------------------------------------------------
-- 2. CHECKs (valem sempre, independentemente do recurso estar ligado)
-- ---------------------------------------------------------------------------
alter table public.requisicoes drop constraint if exists ck_requisicoes_km_saida_nao_negativo;
alter table public.requisicoes
  add constraint ck_requisicoes_km_saida_nao_negativo
  check (km_saida is null or km_saida >= 0);

alter table public.requisicoes drop constraint if exists ck_requisicoes_km_chegada_nao_negativo;
alter table public.requisicoes
  add constraint ck_requisicoes_km_chegada_nao_negativo
  check (km_chegada is null or km_chegada >= 0);

alter table public.requisicoes drop constraint if exists ck_requisicoes_km_chegada_maior_igual_saida;
alter table public.requisicoes
  add constraint ck_requisicoes_km_chegada_maior_igual_saida
  check (km_saida is null or km_chegada is null or km_chegada >= km_saida);

alter table public.requisicoes drop constraint if exists ck_requisicoes_km_chegada_exige_saida;
alter table public.requisicoes
  add constraint ck_requisicoes_km_chegada_exige_saida
  check (km_chegada is null or km_saida is not null);

-- km so existe depois do fato real: saida real para km_saida, chegada real para km_chegada.
alter table public.requisicoes drop constraint if exists ck_requisicoes_km_coerente_com_real;
alter table public.requisicoes
  add constraint ck_requisicoes_km_coerente_com_real
  check ((km_saida is null or saida_real_em is not null)
         and (km_chegada is null or chegada_real_em is not null));

-- ---------------------------------------------------------------------------
-- 3. Trigger de km (BEFORE INSERT OR UPDATE).
-- Separado de requisicoes_validar para nao reescrever a funcao de transicao. O nome
-- 'trg_requisicoes_validar_km' ordena DEPOIS de 'trg_requisicoes_validar' (ordem alfabetica
-- de gatilhos BEFORE), entao a transicao, o ator e os carimbos ja foram validados aqui.
-- SECURITY DEFINER para ler configuracoes independentemente do papel de quem escreve.
-- Leitura do flag SEM FOR SHARE de proposito: nao cria espera nem ordem de lock nova entre
-- requisicoes e configuracoes (ver contrato da migration 15). Corrida com o admin que liga/desliga
-- o recurso e inerente: vale o valor visivel ao statement.
-- ---------------------------------------------------------------------------
create or replace function public.requisicoes_validar_km()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ligado boolean;
begin
  if tg_op = 'INSERT' then
    return new;  -- nasce 'solicitada': os CHECKs recusam qualquer km.
  end if;

  -- Gravado uma vez, imutavel.
  if (old.km_saida is not null and new.km_saida is distinct from old.km_saida)
     or (old.km_chegada is not null and new.km_chegada is distinct from old.km_chegada) then
    raise exception 'Quilometragem já gravada não pode mudar.' using errcode = 'AG004';
  end if;

  -- Sem mudar de status, km nao muda (so entra junto da transicao, que e auditada).
  if new.status = old.status then
    if new.km_saida is distinct from old.km_saida
       or new.km_chegada is distinct from old.km_chegada then
      raise exception 'Quilometragem só é registrada junto da saída ou da chegada.'
        using errcode = 'AG004';
    end if;
    return new;
  end if;

  select quilometragem into v_ligado from public.configuracoes where id;
  if coalesce(v_ligado, false) then
    if new.status = 'em_uso' and new.km_saida is null then
      raise exception 'Quilometragem de saída é obrigatória.' using errcode = 'AG012';
    end if;
    if new.status = 'concluida' and (new.km_saida is null or new.km_chegada is null) then
      raise exception 'Quilometragem de saída e de chegada é obrigatória.' using errcode = 'AG012';
    end if;
  end if;

  return new;
end;
$$;

revoke all on function public.requisicoes_validar_km() from public, anon, authenticated;

drop trigger if exists trg_requisicoes_validar_km on public.requisicoes;
create trigger trg_requisicoes_validar_km
  before insert or update on public.requisicoes
  for each row execute function public.requisicoes_validar_km();
alter table public.requisicoes enable always trigger trg_requisicoes_validar_km;

-- ---------------------------------------------------------------------------
-- 4. Auditoria: km nos detalhes da transicao (substitui a funcao da migration 5)
-- ---------------------------------------------------------------------------
create or replace function public.requisicoes_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para)
    values ('requisicoes', new.id, 'criada', public.requisicao_ator(new), null, new.status::text);
  elsif new.status is distinct from old.status then
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
    values ('requisicoes', new.id, 'transicao', public.requisicao_ator(new),
            old.status::text, new.status::text,
            case when new.status in ('em_uso', 'concluida')
                 then jsonb_strip_nulls(jsonb_build_object(
                        'km_saida', new.km_saida, 'km_chegada', new.km_chegada))
                 else '{}'::jsonb
            end);
  end if;
  return null;
end;
$$;

revoke all on function public.requisicoes_auditar() from public, anon, authenticated;
