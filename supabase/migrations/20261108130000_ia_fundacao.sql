-- Migration: IA opcional, fundacao (Fase 0). Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: recursos de IA (saudacao sazonal, resumos) sao OPCIONAIS e nunca decidem regra de negocio.
-- O banco guarda (1) a configuracao escolhida pelo admin na tela de Configuracoes e (2) o consumo
-- (para cota por usuario/unidade/mes). O backend e a fonte da verdade e o unico chamador
-- (service_role). A variavel de ambiente AI_ENABLED=false vence qualquer valor daqui (checado no
-- backend). As CHAVES DE API nunca ficam no banco: so no ambiente do backend.
--
-- 1. public.ia_configuracoes: linha unica (id boolean fixo em true), tudo desligado por padrao.
--      mestre/saudacao ........ chaves liga/desliga (novo recurso = nova coluna booleana)
--      provedor / modelo ...... provedor ativo ('openai' | 'anthropic') e modelo (a lista permitida
--                               e do backend; aqui so se limita o formato)
--      provedor_reserva ....... opcional, diferente do ativo; usado se o ativo falhar
--      teto_tokens_mes ........ opcional; vale o MENOR entre este e AI_MONTHLY_TOKEN_CAP do .env
--    Alteracao so por admin ativo (alterado_por validado e zerado pelo trigger, como em
--    `configuracoes`). Cada coluna alterada gera UMA linha em `auditoria` reaproveitando
--    acao='configuracao_alterada' e tabela='configuracoes' (nenhum CHECK de auditoria e recriado),
--    com detalhes {"recurso": "ia.<coluna>"}. Valores de chaves liga/desliga, provedor, modelo e
--    teto: nenhum e dado pessoal nem segredo.
--
-- 2. public.ia_uso: registro append-only de cada chamada ao provedor. SEM conteudo de prompt nem
--    de resposta, sem nome, e-mail ou trajeto: so ids, unidade, provedor, modelo, tokens, status e
--    latencia. Sem UPDATE/DELETE/TRUNCATE (nem para service_role).
--
-- 3. RPC public.ia_consumo(p_usuario, p_unidade, p_desde_hora, p_desde_mes): contagens para as
--    cotas, numa ida so ao banco. SECURITY DEFINER, search_path vazio, STABLE, EXECUTE so service_role.
--    Retorno: {usuario_hora, unidade_hora, tokens_mes:{openai, anthropic}}.
--    Contam como tentativa (limite por hora) as chamadas com status ok, erro e timeout; chamadas
--    barradas por cota ou circuito ('cota', 'circuito') nao consomem a cota.
--
-- Erros: reaproveita AG002 (responsavel ausente/inativo/nao admin). Nenhum erro novo.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. ia_configuracoes
-- ---------------------------------------------------------------------------
create table if not exists public.ia_configuracoes (
  id                boolean primary key default true,
  mestre            boolean not null default false,
  saudacao          boolean not null default false,
  provedor          text,
  modelo            text,
  provedor_reserva  text,
  teto_tokens_mes   integer,
  alterado_por      uuid,
  atualizado_em     timestamptz not null default now(),

  constraint ck_ia_configuracoes_linha_unica check (id),
  constraint ck_ia_configuracoes_provedor
    check (provedor is null or provedor in ('openai', 'anthropic')),
  constraint ck_ia_configuracoes_reserva
    check (provedor_reserva is null or provedor_reserva in ('openai', 'anthropic')),
  constraint ck_ia_configuracoes_reserva_diferente
    check (provedor_reserva is null or provedor_reserva is distinct from provedor),
  constraint ck_ia_configuracoes_modelo
    check (modelo is null or (char_length(modelo) between 1 and 80 and modelo ~ '^[A-Za-z0-9._:-]+$')),
  constraint ck_ia_configuracoes_teto
    check (teto_tokens_mes is null or teto_tokens_mes between 1 and 2000000000)
);

comment on table public.ia_configuracoes is
  'IA opcional (linha unica). Tudo desligado por padrao. Chaves de API NUNCA ficam aqui. Alteracao so por admin ativo e auditada.';
comment on column public.ia_configuracoes.alterado_por is
  'Transporte: admin responsavel. Validado e zerado pelo trigger; nunca persiste.';

insert into public.ia_configuracoes (id) values (true) on conflict (id) do nothing;

drop trigger if exists trg_ia_configuracoes_atualizado_em on public.ia_configuracoes;
create trigger trg_ia_configuracoes_atualizado_em
  before update on public.ia_configuracoes
  for each row execute function public.set_atualizado_em();

drop trigger if exists trg_ia_configuracoes_sem_delete on public.ia_configuracoes;
create trigger trg_ia_configuracoes_sem_delete
  before delete on public.ia_configuracoes
  for each row execute function public.bloquear_alteracao();
drop trigger if exists trg_ia_configuracoes_sem_truncate on public.ia_configuracoes;
create trigger trg_ia_configuracoes_sem_truncate
  before truncate on public.ia_configuracoes
  for each statement execute function public.bloquear_alteracao();

-- Validacao + auditoria (BEFORE UPDATE), no mesmo padrao de configuracoes_validar: o ator precisa
-- ser zerado na mesma linha e a auditoria some junto se o UPDATE falhar depois. Generico: compara
-- todas as colunas exceto as de controle, entao um flag novo nao exige reescrever esta funcao.
create or replace function public.ia_configuracoes_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator  uuid := new.alterado_por;
  v_chave text;
  v_novo  jsonb := to_jsonb(new) - 'alterado_por' - 'atualizado_em';
  v_velho jsonb := to_jsonb(old) - 'alterado_por' - 'atualizado_em';
begin
  new.alterado_por := null;  -- nunca persiste

  if v_novo = v_velho then
    return new;
  end if;

  perform privado.exigir_admin_ativo(v_ator);

  for v_chave in
    select k from jsonb_object_keys(v_novo) as k
     where (v_novo -> k) is distinct from (v_velho -> k)
     order by k
  loop
    insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
    values ('configuracoes', null, 'configuracao_alterada', v_ator,
            v_velho ->> v_chave, v_novo ->> v_chave,
            jsonb_build_object('recurso', 'ia.' || v_chave));
  end loop;

  return new;
end;
$$;

revoke all on function public.ia_configuracoes_validar() from public, anon, authenticated;

drop trigger if exists trg_ia_configuracoes_validar on public.ia_configuracoes;
create trigger trg_ia_configuracoes_validar
  before update on public.ia_configuracoes
  for each row execute function public.ia_configuracoes_validar();

alter table public.ia_configuracoes enable row level security;
revoke all on table public.ia_configuracoes from anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. ia_uso (append-only)
-- ---------------------------------------------------------------------------
create table if not exists public.ia_uso (
  id              bigint generated always as identity primary key,
  ocorrido_em     timestamptz not null default now(),
  funcao          text not null,
  provedor        text not null,
  modelo          text not null,
  usuario_id      uuid references public.perfis (id) on delete restrict,
  unidade         public.unidade,
  tokens_entrada  integer not null default 0,
  tokens_saida    integer not null default 0,
  status          text not null,
  latencia_ms     integer,

  constraint ck_ia_uso_funcao check (funcao in ('saudacao')),
  constraint ck_ia_uso_provedor check (provedor in ('openai', 'anthropic')),
  constraint ck_ia_uso_modelo check (char_length(modelo) between 1 and 80),
  constraint ck_ia_uso_status check (status in ('ok', 'erro', 'timeout', 'cota', 'circuito')),
  constraint ck_ia_uso_tokens check (tokens_entrada >= 0 and tokens_saida >= 0),
  constraint ck_ia_uso_latencia check (latencia_ms is null or latencia_ms >= 0)
);

comment on table public.ia_uso is
  'Uso de IA (append-only). Sem conteudo de prompt/resposta e sem dado pessoal: so ids, unidade, provedor, modelo, tokens, status e latencia.';

create index if not exists ix_ia_uso_ocorrido_em on public.ia_uso (ocorrido_em);
create index if not exists ix_ia_uso_usuario_ocorrido_em on public.ia_uso (usuario_id, ocorrido_em);
create index if not exists ix_ia_uso_unidade_ocorrido_em on public.ia_uso (unidade, ocorrido_em);

drop trigger if exists trg_ia_uso_imutavel on public.ia_uso;
create trigger trg_ia_uso_imutavel
  before update or delete on public.ia_uso
  for each row execute function public.bloquear_alteracao();
drop trigger if exists trg_ia_uso_sem_truncate on public.ia_uso;
create trigger trg_ia_uso_sem_truncate
  before truncate on public.ia_uso
  for each statement execute function public.bloquear_alteracao();

alter table public.ia_uso enable row level security;
revoke all on table public.ia_uso from anon, authenticated;
-- Defesa em profundidade: nem o service_role altera ou apaga (so INSERT/SELECT).
revoke update, delete, truncate on table public.ia_uso from service_role;

-- ---------------------------------------------------------------------------
-- 3. ia_consumo
-- ---------------------------------------------------------------------------
create or replace function public.ia_consumo(
  p_usuario    uuid,
  p_unidade    public.unidade,
  p_desde_hora timestamptz,
  p_desde_mes  timestamptz
)
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select jsonb_build_object(
    'usuario_hora', coalesce(count(*) filter (
        where u.ocorrido_em >= p_desde_hora and u.usuario_id = p_usuario
          and u.status in ('ok', 'erro', 'timeout')), 0),
    'unidade_hora', coalesce(count(*) filter (
        where u.ocorrido_em >= p_desde_hora and u.unidade = p_unidade
          and u.status in ('ok', 'erro', 'timeout')), 0),
    'tokens_mes', jsonb_build_object(
        'openai', coalesce(sum(u.tokens_entrada + u.tokens_saida) filter (
            where u.provedor = 'openai' and u.ocorrido_em >= p_desde_mes), 0),
        'anthropic', coalesce(sum(u.tokens_entrada + u.tokens_saida) filter (
            where u.provedor = 'anthropic' and u.ocorrido_em >= p_desde_mes), 0))
  )
  from public.ia_uso u
  where u.ocorrido_em >= least(p_desde_hora, p_desde_mes);
$$;

revoke all on function public.ia_consumo(uuid, public.unidade, timestamptz, timestamptz)
  from public, anon, authenticated;
grant execute on function public.ia_consumo(uuid, public.unidade, timestamptz, timestamptz)
  to service_role;
