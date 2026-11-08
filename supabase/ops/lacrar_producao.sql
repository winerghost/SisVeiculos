-- ============================================================================
-- LACRE DO BANCO DE PRODUCAO (ferramenta de operacao; NAO e migration)
-- ============================================================================
-- Rode UMA vez, no SQL Editor do Supabase (papel postgres), no dia do lancamento, DEPOIS da
-- limpeza. Grava o comentario do banco 'sisveiculos:lacrado'. Com o lacre, o script
-- limpar_dados_de_teste.sql recusa rodar para sempre (protege contra apagar dados reais).
--
-- Recusa se: o banco ja esta lacrado; o banco e o de testes ('sisveiculos:descartavel');
-- o banco ja tem outro comentario (nao sobrescreve); o papel atual nao e dono do banco;
-- a frase de confirmacao nao for exatamente a pedida.
-- O comentario padrao 'default administrative connection database' (e comentario vazio) e
-- substituido; o texto anterior aparece no resultado final (comentario_anterior_substituido).
-- Para DESFAZER o lacre (decisao consciente do dono do banco), ver docs/limpeza-pre-lancamento.md.
-- Para conferir o lacre: select shobj_description(oid, 'pg_database') from pg_database
--   where datname = current_database();
-- ============================================================================

do $lacre$
declare
  -- ======================= PARAMETRO (so mude este) ==============================
  -- Para lacrar escreva exatamente:  LACRAR PRODUCAO ofwvoztarroglekfwohs
  v_confirmacao  text := '';
  -- Identificador do projeto (faz parte da frase; o banco nao o conhece, confira no navegador).
  v_ref_esperado text := 'ofwvoztarroglekfwohs';
  -- ===================== FIM DO PARAMETRO ========================================

  c_lacre       constant text := 'sisveiculos:lacrado';
  c_descartavel constant text := 'sisveiculos:descartavel';
  -- Comentario padrao do banco `postgres`: conta como "sem comentario proprio" (pode ser substituido).
  c_padrao      constant text := 'default administrative connection database';
  v_banco       text := current_database();
  v_comentario  text;
  v_dono        oid;
begin
  -- Parametro NULL contornaria a comparacao da frase (NULL is not distinct from NULL): recusa primeiro.
  if v_confirmacao is null or v_ref_esperado is null then
    raise exception 'RECUSADO: parametro nulo. Nada foi feito.' using errcode = 'LD003';
  end if;

  select shobj_description(d.oid, 'pg_database'), d.datdba
    into v_comentario, v_dono
    from pg_database d where d.datname = v_banco;

  if coalesce(v_comentario, '') like c_lacre || '%' then
    raise exception 'Este banco ja esta LACRADO. Nada foi feito.' using errcode = 'LD002';
  end if;
  if coalesce(v_comentario, '') = c_descartavel then
    raise exception 'RECUSADO: banco marcado como descartavel (sisveiculos:descartavel); o marcador de teste nao e sobrescrito.'
      using errcode = 'LD006';
  end if;
  if btrim(coalesce(v_comentario, '')) <> '' and v_comentario <> c_padrao then
    raise exception 'RECUSADO: o banco ja tem um comentario proprio ("%"); o lacre nao o sobrescreve.', left(v_comentario, 80)
      using errcode = 'LD006';
  end if;
  if v_confirmacao is distinct from 'LACRAR PRODUCAO ' || v_ref_esperado then
    raise exception 'RECUSADO: v_confirmacao nao e exatamente a frase "LACRAR PRODUCAO %". Nada foi feito.', v_ref_esperado
      using errcode = 'LD003';
  end if;
  if not pg_has_role(current_user, v_dono, 'usage') then
    raise exception 'RECUSADO: o papel atual (%) nao e dono do banco e nao pode gravar o lacre.', current_user
      using errcode = 'LD006';
  end if;

  execute format('comment on database %I is %L', v_banco, c_lacre);
  perform set_config('sisveiculos.comentario_anterior', coalesce(v_comentario, ''), true);
  raise notice 'LACRE gravado em %. Comentario anterior substituido: %', v_banco, coalesce(nullif(v_comentario, ''), '(vazio)');
end
$lacre$;

-- Confirmacao visivel (so aparece se o bloco acima terminou sem erro).
select shobj_description(oid, 'pg_database') as lacre_do_banco,
       coalesce(nullif(current_setting('sisveiculos.comentario_anterior', true), ''), '(vazio)')
         as comentario_anterior_substituido
from pg_database where datname = current_database();
