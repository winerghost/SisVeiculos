-- Migration 21: recurso opcional CHECKLIST, parte 3 (bucket privado no Supabase Storage).
-- Nova migration; nao edita as anteriores. Idempotente.
--
-- Motivo: as fotos do checklist ficam em Storage PRIVADO (CLAUDE.md), com URL assinada.
-- O arquivo nunca vai para o banco (bytea); a tabela checklist_fotos guarda so os metadados.
--
-- Bucket 'checklists':
--  * public = false (nenhuma URL publica);
--  * file_size_limit = 5242880 (5 MB), imposto pelo proprio Storage no upload;
--  * allowed_mime_types = image/jpeg, image/png, image/webp, imposto pelo Storage.
--  No conflito reafirma esses tres valores (se alguem tornar o bucket publico por fora, o
--  proximo db push o fecha de novo).
--
-- Politicas em storage.objects: NEGAM tudo a anon e authenticated neste bucket. Sao politicas
-- RESTRICTIVE (E logico com qualquer politica permissiva): mesmo que no futuro alguem crie uma
-- politica permissiva ampla ("authenticated le tudo"), o bucket 'checklists' continua fechado.
-- Sem politica permissiva para ninguem, o RLS ja nega por padrao; a restritiva e a trava
-- contra erro futuro. So o service_role (backend Flask) acessa: ele ignora RLS e gera a URL
-- assinada de UPLOAD (createSignedUploadUrl) e de LEITURA (createSignedUrl, curta) por
-- chamada ja autorizada no backend. O front nunca recebe a service key.
--
-- Convencao de caminho: <requisicao_id>/<momento>/<uuid>.<ext>, calculada pelo banco em
-- checklist_fotos.caminho (migration 19). O backend usa o mesmo caminho para a URL de upload;
-- uma foto nao aponta para outra requisicao porque o caminho e GERADO da propria linha.
-- LIMITE: o banco NAO verifica que o objeto existe no Storage nem que o tamanho/MIME enviados
-- no registro batem com o arquivo real (tamanho_bytes e mime_type sao metadados validados pelo
-- backend); o bucket garante o teto de 5 MB e os tipos permitidos no upload. Verificar em
-- storage.objects dentro do banco e uma alternativa (acopla ao schema interno do Storage).
--
-- Dependencia: schema storage do Supabase (hospedado e `supabase start`). Em Postgres puro a
-- migration falha de proposito. Se a criacao da politica falhar por permissao no ambiente
-- hospedado ("must be owner of table objects"), a migration inteira reverte; nesse caso criar
-- as politicas pelo painel e ajustar por nova migration.
-- Retencao/expurgo das fotos (LGPD): fora desta migration; ver relatorio.
-- Fuso: nao se aplica (sem timestamptz aqui).

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('checklists', 'checklists', false, 5242880,
        array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update
  set public             = false,
      file_size_limit    = 5242880,
      allowed_mime_types = array['image/jpeg', 'image/png', 'image/webp'];

-- Negacao a anon e authenticated, valendo em qualquer operacao (select/insert/update/delete).
drop policy if exists checklists_bucket_sem_acesso_api on storage.objects;
create policy checklists_bucket_sem_acesso_api on storage.objects
  as restrictive
  for all
  to anon, authenticated
  using (bucket_id <> 'checklists')
  with check (bucket_id <> 'checklists');

-- Trava: o bucket existe, e privado e com os limites esperados.
do $$
begin
  if not exists (select 1 from storage.buckets
                  where id = 'checklists' and public = false
                    and file_size_limit = 5242880
                    and allowed_mime_types = array['image/jpeg', 'image/png', 'image/webp']) then
    raise exception 'Bucket checklists ausente ou fora do padrao (privado, 5 MB, jpeg/png/webp).';
  end if;
end
$$;
