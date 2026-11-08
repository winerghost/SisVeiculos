-- Migration 25c: FOTO DE PERFIL, remocao pelo dono OU por admin ativo (RPC com ator) e auditoria
-- com o ator correto. Nova migration; NAO edita as 122410/122420 (ja aplicadas). Idempotente.
-- Fica depois de 122440 (acessos de login) e antes das pendentes do Abastecimento (122500+).
--
-- Decisoes do usuario: (a) o backend recodifica a imagem no servidor (staging -> Pillow -> caminho
-- final -> RPC -> apaga o staging); (b) o PROPRIO usuario e o ADMIN podem remover a foto.
--
-- 1. MODELO DO ATOR NA AUDITORIA: coluna `removida_por` (uuid, FK perfis), e NAO variavel de
--    sessao. Motivo: a auditoria da foto e um constraint trigger DEFERRED; ele roda no COMMIT.
--    Uma variavel de sessao (set_config local) seria lida no commit, com o valor do ULTIMO set da
--    transacao, e erraria o ator de linhas enfileiradas antes (ex.: remocao pelo admin seguida de
--    troca de outro usuario na mesma transacao). A coluna e gravada no mesmo UPDATE que remove a
--    foto e viaja na propria linha (NEW) ate o gatilho: ator por EVENTO, sem estado oculto.
--      * NULL = removida pelo proprio dono (ou pela troca): ator = usuario_id (comportamento da
--        122410 preservado; trocar_foto_perfil e remover_foto_perfil seguem identicas).
--      * preenchida = quem removeu (remover_foto_perfil_por grava p_ator sempre).
--    O gatilho de imutabilidade da 122410 (perfil_fotos_so_remocao) NAO muda: ele compara
--    id/usuario_id/mime/tamanho/criado_em e ignora removida_por, entao a remocao NULL -> valor
--    continua valendo e qualquer UPDATE posterior (inclusive de removida_por) segue AG003.
--    `perfil_fotos_validar` (BEFORE INSERT) e recriada so para zerar removida_por no INSERT (a
--    foto nasce ativa e sem remocao), no mesmo estilo de `removida_em := null`. As mensagens e
--    codigos dela ficam IDENTICOS aos da 122410. Um CHECK foi evitado de proposito: a violacao de
--    CHECK exporia "Failing row" no DETAIL.
--    `service_role` continua SEM INSERT/UPDATE na tabela (so SELECT): so as RPCs escrevem.
--    A FK removida_por -> perfis(id) ON DELETE RESTRICT so toma FOR KEY SHARE na linha do admin
--    (nao conflita com UPDATE de role/ativo; a FK de usuario_id ja se comporta assim). Sem indice
--    em removida_por: coluna quase nunca consultada, `perfis` nao sofre DELETE (so RESTRICT).
--
-- 2. AUDITORIA: `perfil_fotos_auditar` (create or replace, SECURITY DEFINER, search_path vazio)
--    passa a gravar ator_id = coalesce(removida_por, usuario_id). `registro_id` = id da FOTO (como
--    sempre; o DONO se obtem por join perfil_fotos.id = registro_id, linhas nunca sao apagadas).
--    `detalhes` da acao 'foto_perfil_removida' ganha `por_admin` (boolean: ator <> dono); a acao
--    'foto_perfil_alterada' segue {mime, tamanho_bytes}. Linhas antigas de remocao (anteriores a
--    esta migration) nao tem a chave: ausente = false. Sem caminho, URL, nome nem outro dado
--    pessoal. NAO precisa de valor novo em ck_auditoria_acao/ck_auditoria_tabela (continuam 17
--    acoes e 7 tabelas, nada recriado aqui).
--
-- 3. NOVA RPC remover_foto_perfil_por(p_usuario, p_ator) -> (id_anterior, caminho_anterior)
--    SECURITY DEFINER, search_path vazio, EXECUTE so para service_role.
--      * p_ator = p_usuario: o dono remove a propria foto, ativo ou nao (remover dado pessoal de
--        conta desativada e legitimo, LGPD), como a remover_foto_perfil atual;
--      * p_ator <> p_usuario: exige que p_ator seja ADMIN ATIVO; senao AG022 (mensagem fixa);
--      * p_usuario inexistente => AG017; argumento nulo => 23502;
--      * sem foto ativa => devolve (NULL, NULL), sem erro e sem auditoria (idempotente), inclusive
--        quando o ator e um admin e o alvo nao tem foto.
--    Como o ator vem do JWT validado no backend (nunca do cliente), o banco confia no p_ator
--    informado pelo service_role, como nas demais RPCs; a checagem de admin ativo e uma segunda
--    barreira. O backend DEVE passar o id do JWT em p_ator e decidir p_usuario por rota.
--    LOCKS: mesmo advisory por usuario das 122410 (`sisveiculos.avatar:<p_usuario>`); NAO toma
--    `sisveiculos.perfis_admin` e NAO escreve em `perfis`. O papel do ator e lido de `perfis` SEM
--    lock (como o checklist; limite aceito B4). A checagem de admin ativo roda DUAS vezes quando
--    p_ator <> p_usuario: antes do advisory (recusa barata) e DE NOVO logo depois dele (apos a
--    espera, com snapshot novo em READ COMMITTED). A janela residual e so entre o segundo SELECT e
--    o commit: um admin rebaixado/desativado nesse intervalo ainda pode ser aceito naquela unica
--    operacao; o efeito e a remocao (logica, auditada com o ator) de uma foto, de baixo impacto, e
--    tomar o lock de perfis_admin criaria o ciclo que o contrato da migration 15 proibe. O
--    lock_timeout de 5 s (migration 16) vale: 55P03 => retry limitado no backend.
--    A RPC antiga remover_foto_perfil(uuid) e trocar_foto_perfil(...) ficam como estao.
--
-- 4. NOVO CODIGO DE ERRO: AG022 = remocao de foto de perfil nao autorizada (ator diferente do dono
--    e que nao e admin ativo). Em uso: AG001-AG014, AG015/AG016 (reservados ao Abastecimento),
--    AG017 (foto), AG018 (livre), AG019/AG020 (acessos de login). AG021 fica LIVRE (a reserva do
--    outro trabalho em paralelo); AG023 em diante livres.
--
-- 5. CONVENCAO DE CAMINHO DO BUCKET `avatares` (documentacao; sem DDL, a politica RESTRICTIVE da
--    122420 ja nega anon/authenticated em TODO o bucket, inclusive o prefixo tmp):
--      * staging (nao confiavel, imagem ORIGINAL enviada pelo cliente): avatares/tmp/<usuario_id>/<uuid>
--      * final (apenas a imagem RECODIFICADA pelo backend): avatares/<usuario_id>/<id>.<ext>, o
--        mesmo calculado em perfil_fotos.caminho.
--    "tmp" nunca colide com uma pasta de usuario (o 1o segmento do final e sempre um uuid). O
--    banco NAO enxerga nem valida o staging: quem apaga e o backend (apos a RPC ou na falha);
--    objetos orfaos em tmp/ e no caminho final (RPC falhou depois do upload) dependem de rotina de
--    expurgo FUTURA (pendente; nao criada aqui). O limite de 1 MB e a lista de mimes do bucket
--    valem tambem para o staging.
-- Fuso: timestamptz (armazenado em UTC; exibicao em America/Sao_Paulo).

-- ---------------------------------------------------------------------------
-- 1. Coluna removida_por
-- ---------------------------------------------------------------------------
-- O pipeline roda como postgres e NAO herda o lock_timeout do service_role (migration 16). ADD COLUMN
-- com REFERENCES toma SHARE ROW EXCLUSIVE em `perfis` (breve; a coluna nova e nullable, sem
-- rewrite): se houver transacao longa em `perfis`, falha em 5 s em vez de enfileirar escritas.
set local lock_timeout = '5s';

alter table public.perfil_fotos
  add column if not exists removida_por uuid references public.perfis (id) on delete restrict;

comment on column public.perfil_fotos.removida_por is
  'Quem removeu a foto (admin ou o proprio dono), gravado por remover_foto_perfil_por. NULL = o dono (ou troca). Nao informavel no INSERT.';

-- ---------------------------------------------------------------------------
-- 2. perfil_fotos_validar: identica a da 122410, mais `removida_por := null` no INSERT
-- ---------------------------------------------------------------------------
create or replace function public.perfil_fotos_validar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ativo boolean;
begin
  new.criado_em    := clock_timestamp();  -- carimbo do servidor
  new.removida_em  := null;               -- nasce ativa
  new.removida_por := null;               -- e sem remocao

  -- Campos obrigatorios (NOT NULL). Mensagem fixa: so o nome do campo, nunca valores.
  if new.id is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: id.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'id';
  end if;
  if new.usuario_id is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: usuario_id.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'usuario_id';
  end if;
  if new.mime is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: mime.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'mime';
  end if;
  if new.tamanho_bytes is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: tamanho_bytes.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'tamanho_bytes';
  end if;

  if new.mime not in ('image/jpeg', 'image/png', 'image/webp') then
    raise exception 'Violação da regra ck_perfil_fotos_mime: tipo de imagem não permitido.'
      using errcode = '23514', schema = 'public', table = 'perfil_fotos',
            column = 'mime', constraint = 'ck_perfil_fotos_mime';
  end if;
  if new.tamanho_bytes < 1 or new.tamanho_bytes > 1048576 then
    raise exception 'Violação da regra ck_perfil_fotos_tamanho: tamanho deve ficar entre 1 byte e 1 MB.'
      using errcode = '23514', schema = 'public', table = 'perfil_fotos',
            column = 'tamanho_bytes', constraint = 'ck_perfil_fotos_tamanho';
  end if;

  -- Usuario sem lock (contrato da migration 15). Inexistente: a FK responde (23503).
  select ativo into v_ativo from public.perfis where id = new.usuario_id;
  if not found then
    return new;
  end if;
  if not v_ativo then
    raise exception 'Usuário inativo não pode ter foto de perfil.' using errcode = 'AG017';
  end if;
  return new;
end;
$$;

revoke all on function public.perfil_fotos_validar() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 3. perfil_fotos_auditar: ator por evento (removida_por) e por_admin na remocao
-- ---------------------------------------------------------------------------
create or replace function public.perfil_fotos_auditar()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_ator    uuid := coalesce(new.removida_por, new.usuario_id);
  v_detalhe jsonb := jsonb_build_object('mime', new.mime, 'tamanho_bytes', new.tamanho_bytes);
begin
  if tg_op = 'UPDATE' then
    v_detalhe := v_detalhe || jsonb_build_object('por_admin', v_ator <> new.usuario_id);
  end if;
  -- Sem caminho, URL ou nome de arquivo. registro_id = id da foto (o dono: join por esse id).
  insert into public.auditoria (tabela, registro_id, acao, ator_id, de, para, detalhes)
  values ('perfil_fotos', new.id,
          case tg_op when 'INSERT' then 'foto_perfil_alterada' else 'foto_perfil_removida' end,
          v_ator, null, null, v_detalhe);
  return null;
end;
$$;

revoke all on function public.perfil_fotos_auditar() from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 4. RPC remover_foto_perfil_por
-- ---------------------------------------------------------------------------
create or replace function public.remover_foto_perfil_por(p_usuario uuid, p_ator uuid)
returns table (id_anterior uuid, caminho_anterior text)
language plpgsql
security definer
set search_path = ''
as $$
begin
  if p_usuario is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: usuario_id.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'usuario_id';
  end if;
  if p_ator is null then
    raise exception 'Campo obrigatório da foto de perfil ausente: removida_por.'
      using errcode = '23502', schema = 'public', table = 'perfil_fotos', column = 'removida_por';
  end if;

  -- Autorizacao (sem lock em perfis; limite B4 documentado no cabecalho): o dono, ou admin ativo.
  if p_ator <> p_usuario
     and not exists (select 1 from public.perfis
                      where id = p_ator and role = 'admin' and ativo) then
    raise exception 'Remoção da foto de perfil não autorizada.' using errcode = 'AG022';
  end if;

  -- So inexistente e recusado aqui (conta desativada pode ter a foto removida); a mensagem diz isso.
  if not exists (select 1 from public.perfis where id = p_usuario) then
    raise exception 'Usuário inexistente.' using errcode = 'AG017';
  end if;

  -- Mesmo lock por usuario das demais RPCs de foto. Nunca tomar sisveiculos.perfis_admin aqui.
  perform pg_advisory_xact_lock(hashtextextended('sisveiculos.avatar:' || p_usuario::text, 0));

  -- Repete a checagem de admin ativo apos a espera pelo lock (snapshot novo).
  if p_ator <> p_usuario
     and not exists (select 1 from public.perfis
                      where id = p_ator and role = 'admin' and ativo) then
    raise exception 'Remoção da foto de perfil não autorizada.' using errcode = 'AG022';
  end if;

  update public.perfil_fotos
     set removida_em  = clock_timestamp(),
         removida_por = p_ator
   where usuario_id = p_usuario and removida_em is null
  returning id, caminho into id_anterior, caminho_anterior;

  return next;  -- idempotente: sem foto ativa devolve NULLs (e nao audita)
end;
$$;

revoke all on function public.remover_foto_perfil_por(uuid, uuid) from public, anon, authenticated;
grant execute on function public.remover_foto_perfil_por(uuid, uuid) to service_role;

-- ---------------------------------------------------------------------------
-- 5. Trava: a migration falha se sobrar acesso indevido
-- ---------------------------------------------------------------------------
do $$
declare
  f text := 'public.remover_foto_perfil_por(uuid, uuid)';
  r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if has_function_privilege(r, f::regprocedure, 'execute') then
      raise exception '% executa %.', r, f;
    end if;
  end loop;
  if not has_function_privilege('service_role', f::regprocedure, 'execute') then
    raise exception 'service_role nao executa %.', f;
  end if;
  if exists (select 1 from pg_proc x, aclexplode(coalesce(x.proacl, acldefault('f', x.proowner))) a
              where x.oid = f::regprocedure and a.grantee = 0 and a.privilege_type = 'EXECUTE') then
    raise exception 'PUBLIC executa %.', f;
  end if;
  if not exists (select 1 from pg_proc x
                  where x.oid = f::regprocedure and x.prosecdef
                    and array_to_string(x.proconfig, ',') like '%search_path=%') then
    raise exception '% sem SECURITY DEFINER ou search_path fixo.', f;
  end if;

  foreach f in array array['public.perfil_fotos_validar()', 'public.perfil_fotos_auditar()'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if has_function_privilege(r, f::regprocedure, 'execute') then
        raise exception '% ainda executa %.', r, f;
      end if;
    end loop;
  end loop;

  foreach r in array array['insert', 'update', 'delete', 'truncate'] loop
    if has_table_privilege('service_role', 'public.perfil_fotos', r) then
      raise exception 'service_role tem % em public.perfil_fotos.', r;
    end if;
  end loop;
end
$$;
