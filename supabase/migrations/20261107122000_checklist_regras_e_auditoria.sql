-- Migration 20: recurso opcional CHECKLIST, parte 2 (regras de registro, auditoria e escrita atomica).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: a migration 19 criou as tabelas. Aqui ficam as regras que dependem de OUTRAS tabelas
-- (estado da requisicao, papel do ator), a auditoria sem dado pessoal e a funcao de escrita.
--
-- Regras de registro (trigger BEFORE INSERT em checklists, SECURITY DEFINER, ENABLE ALWAYS):
--  * Janela: checklist 'saida' so com a requisicao 'autorizada'; 'chegada' so com 'em_uso'.
--    Outro estado => AG014. Assim o checklist de saida e feito ANTES da saida (e a migration 22
--    o exige na transicao para em_uso) e o de chegada antes da conclusao. Nao ha checklist
--    retroativo: com o recurso ligado numa viagem JA em_uso sem checklist de saida, o de saida
--    nao pode mais ser registrado (AG014) e a conclusao exige apenas o de chegada (ver 22).
--  * Ator: registrado_por precisa ser perfil ativo com papel autorizador ou admin (os mesmos que
--    registram saida/chegada); senao AG002. A linha da requisicao e lida com FOR SHARE, para o
--    estado nao mudar entre a checagem e o INSERT; sem FOR SHARE em perfis/veiculos/configuracoes
--    (contrato da migration 15). Ordem de lock: requisicao -> (nada depois). A FK ja toma
--    FOR KEY SHARE em requisicoes/perfis, que nao conflita com UPDATE de colunas nao-chave.
--  * Carimbo registrado_em e do servidor (clock_timestamp), sobrescreve o valor enviado.
--  * Com o recurso DESLIGADO o banco nao recusa um checklist valido (igual a quilometragem):
--    ignorar a entrada e recusar os endpoints com o recurso desligado e do BACKEND.
--
-- Fotos so na criacao do checklist: o trigger de checklist_fotos exige que o checklist tenha
-- sido criado NA MESMA TRANSACAO (variavel de sessao local sisveiculos.checklist_aberto_<id>,
-- padrao da migration 12). Efeitos: fotos nao entram depois (checklist imutavel de verdade),
-- a contagem para auditoria e definitiva no COMMIT e nao ha corrida entre transacoes. Limite
-- herdado: SQL livre como postgres pode forjar a variavel (o service_role nao tem SQL livre
-- nem INSERT direto nas tabelas).
--
-- Escrita pelo backend: public.registrar_checklist(...) grava checklist + fotos em UMA
-- transacao (o PostgREST executa cada chamada RPC em uma transacao). EXECUTE so para
-- service_role. p_fotos = [{"id": "<uuid gerado pelo backend>", "mime_type": "...",
-- "tamanho_bytes": n}, ...]; a posicao 1..6 e a ordem do array; mais de 6 => 23514.
-- O backend ja precisa do id para calcular o caminho e pedir a URL assinada de upload.
--
-- Auditoria (constraint trigger DEFERRED, AFTER INSERT): uma linha por checklist, SEM dado
-- pessoal: tabela 'checklists', acao 'checklist_registrado', ator_id = quem registrou,
-- para = momento, detalhes = {requisicao_id, momento, avarias (bool), fotos (contagem)}.
-- Nao leva descricao_avarias, combustivel, caminhos nem nomes. E DEFERRED para contar as
-- fotos inseridas depois do checklist, na mesma transacao. Os CHECKs de auditoria
-- (ck_auditoria_acao / ck_auditoria_tabela, migration 10) sao recriados com os valores novos.
--
-- Erros novos: AG014 (checklist/foto fora da janela permitida). AG013 (transicao sem checklist)
-- e da migration 22. Reaproveitados: AG002 (ator), AG003 (imutavel).
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Validacao do checklist
-- ---------------------------------------------------------------------------
create or replace function public.checklists_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_status public.status_requisicao;
  v_role   public.role_usuario;
  v_ativo  boolean;
begin
  new.registrado_em := clock_timestamp();  -- carimbo do servidor

  -- momento invalido ou nulo: o CHECK/NOT NULL da tabela responde com o nome da constraint.
  if new.momento is null or new.momento not in ('saida', 'chegada') then
    return new;
  end if;

  select status into v_status from public.requisicoes where id = new.requisicao_id for share;
  if not found then
    return new;  -- a FK responde (23503)
  end if;

  if not ((new.momento = 'saida' and v_status = 'autorizada')
          or (new.momento = 'chegada' and v_status = 'em_uso')) then
    raise exception 'Checklist de % não pode ser registrado com a requisição %.', new.momento, v_status
      using errcode = 'AG014';
  end if;

  select role, ativo into v_role, v_ativo from public.perfis where id = new.registrado_por;
  if not found or not v_ativo or v_role not in ('autorizador', 'admin') then
    raise exception 'Apenas autorizador ou admin ativo registra checklist.' using errcode = 'AG002';
  end if;

  -- Libera as fotos deste checklist apenas nesta transacao.
  perform set_config('sisveiculos.checklist_aberto_' || replace(new.id::text, '-', ''), '1', true);
  return new;
end;
$$;

revoke all on function public.checklists_validar() from public, anon, authenticated;

drop trigger if exists trg_checklists_validar on public.checklists;
create trigger trg_checklists_validar
  before insert on public.checklists
  for each row execute function public.checklists_validar();
alter table public.checklists enable always trigger trg_checklists_validar;

-- ---------------------------------------------------------------------------
-- 2. Validacao das fotos: so na transacao em que o checklist nasceu
-- ---------------------------------------------------------------------------
create or replace function public.checklist_fotos_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_checklist uuid;
begin
  new.registrado_em := clock_timestamp();  -- carimbo do servidor

  select id into v_checklist
    from public.checklists
   where requisicao_id = new.requisicao_id and momento = new.momento;
  if not found then
    return new;  -- a FK responde (23503)
  end if;

  if coalesce(current_setting('sisveiculos.checklist_aberto_' || replace(v_checklist::text, '-', ''), true), '')
       <> '1' then
    raise exception 'Fotos só podem ser anexadas na criação do checklist.' using errcode = 'AG014';
  end if;
  return new;
end;
$$;

revoke all on function public.checklist_fotos_validar() from public, anon, authenticated;

drop trigger if exists trg_checklist_fotos_validar on public.checklist_fotos;
create trigger trg_checklist_fotos_validar
  before insert on public.checklist_fotos
  for each row execute function public.checklist_fotos_validar();
alter table public.checklist_fotos enable always trigger trg_checklist_fotos_validar;

-- ---------------------------------------------------------------------------
-- 3. Auditoria (valores novos nos CHECKs + trigger deferred sem dado pessoal)
-- ---------------------------------------------------------------------------
alter table public.auditoria drop constraint if exists ck_auditoria_acao;
alter table public.auditoria
  add constraint ck_auditoria_acao check (acao in (
    'criada', 'transicao',
    'convite_criado', 'convite_usado', 'convite_revogado',
    'perfil_criado', 'perfil_alterado', 'perfil_unidade_alterada', 'admin_semeado',
    'configuracao_alterada',
    'veiculo_criado', 'veiculo_alterado',
    'acao_administrativa', 'acesso_negado',
    'checklist_registrado'
  ));

alter table public.auditoria drop constraint if exists ck_auditoria_tabela;
alter table public.auditoria
  add constraint ck_auditoria_tabela
  check (tabela in ('requisicoes', 'convites', 'perfis', 'veiculos', 'configuracoes', 'checklists'));

create or replace function public.checklists_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_fotos integer;
begin
  select count(*) into v_fotos
    from public.checklist_fotos
   where requisicao_id = new.requisicao_id and momento = new.momento;

  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('checklists', new.id, 'checklist_registrado', new.registrado_por, null, new.momento,
          jsonb_build_object('requisicao_id', new.requisicao_id, 'momento', new.momento,
                             'avarias', new.avarias, 'fotos', v_fotos));
  return null;
end;
$$;

revoke all on function public.checklists_auditar() from public, anon, authenticated;

drop trigger if exists trg_checklists_auditoria on public.checklists;
create constraint trigger trg_checklists_auditoria
  after insert on public.checklists
  deferrable initially deferred
  for each row execute function public.checklists_auditar();
alter table public.checklists enable always trigger trg_checklists_auditoria;

-- ---------------------------------------------------------------------------
-- 4. Escrita atomica pelo backend
-- ---------------------------------------------------------------------------
create or replace function public.registrar_checklist(
  p_requisicao_id     uuid,
  p_momento           text,
  p_combustivel       public.nivel_combustivel,
  p_avarias           boolean,
  p_descricao_avarias text,
  p_registrado_por    uuid,
  p_fotos             jsonb default '[]'::jsonb
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_id uuid;
begin
  if p_fotos is null or jsonb_typeof(p_fotos) <> 'array' then
    raise exception 'As fotos devem ser uma lista.' using errcode = 'AG014';
  end if;

  insert into public.checklists
    (requisicao_id, momento, combustivel, avarias, descricao_avarias, registrado_por)
  values
    (p_requisicao_id, p_momento, p_combustivel, p_avarias, p_descricao_avarias, p_registrado_por)
  returning id into v_id;

  insert into public.checklist_fotos (id, requisicao_id, momento, posicao, mime_type, tamanho_bytes)
  select (f.item ->> 'id')::uuid, p_requisicao_id, p_momento, f.ordem::smallint,
         f.item ->> 'mime_type', (f.item ->> 'tamanho_bytes')::integer
    from jsonb_array_elements(p_fotos) with ordinality as f(item, ordem);

  return v_id;
end;
$$;

revoke all on function public.registrar_checklist(uuid, text, public.nivel_combustivel, boolean, text, uuid, jsonb)
  from public, anon, authenticated;
grant execute on function public.registrar_checklist(uuid, text, public.nivel_combustivel, boolean, text, uuid, jsonb)
  to service_role;
