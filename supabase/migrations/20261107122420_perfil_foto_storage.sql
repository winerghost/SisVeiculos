-- Migration 25b: FOTO DE PERFIL, parte 2 (bucket privado `avatares` no Supabase Storage).
-- Nova migration; nao edita as anteriores. Idempotente. Mesmo molde da migration 21 (checklists).
--
-- Bucket 'avatares':
--  * public = false (nenhuma URL publica);
--  * file_size_limit = 1048576 (1 MB), imposto pelo proprio Storage no upload;
--  * allowed_mime_types = image/jpeg, image/png, image/webp, imposto pelo Storage (valida so o
--    Content-Type DECLARADO; o backend deve conferir tamanho real e magic bytes antes de chamar
--    trocar_foto_perfil, e remover EXIF/GPS; o banco nao confere o objeto).
--  No conflito reafirma os tres valores (se alguem tornar o bucket publico por fora, o proximo
--  db push o fecha de novo).
--
-- Politica em storage.objects: RESTRICTIVE (E logico com qualquer permissiva) negando tudo a anon
-- e authenticated neste bucket, mesmo que no futuro surja uma politica permissiva ampla. So o
-- service_role (backend Flask) acessa: gera URL assinada de UPLOAD e de LEITURA (curta) e remove
-- o objeto anterior DEPOIS do commit de trocar/remover_foto_perfil. Nome da politica distinto da
-- do bucket `checklists`.
--
-- Convencao de caminho: <usuario_id>/<uuid>.<ext>, calculada pelo banco em perfil_fotos.caminho
-- (migration 25a). Cada usuario so tem caminhos sob o proprio usuario_id.
--
-- Dependencia: schema storage do Supabase. Em Postgres puro a migration falha de proposito. Se a
-- criacao da politica falhar por permissao no hospedado ("must be owner of table objects"), a
-- migration reverte; ver plano B da migration 21 em docs/deploy-banco.md.
-- Fuso: nao se aplica.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('avatares', 'avatares', false, 1048576,
        array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do update
  set public             = false,
      file_size_limit    = 1048576,
      allowed_mime_types = array['image/jpeg', 'image/png', 'image/webp'];

drop policy if exists avatares_bucket_sem_acesso_api on storage.objects;
create policy avatares_bucket_sem_acesso_api on storage.objects
  as restrictive
  for all
  to anon, authenticated
  using (bucket_id <> 'avatares')
  with check (bucket_id <> 'avatares');

-- Trava: bucket privado com os limites esperados e politica restritiva para anon/authenticated.
do $$
begin
  if not exists (select 1 from storage.buckets
                  where id = 'avatares' and public = false
                    and file_size_limit = 1048576
                    and allowed_mime_types = array['image/jpeg', 'image/png', 'image/webp']) then
    raise exception 'Bucket avatares ausente ou fora do padrao (privado, 1 MB, jpeg/png/webp).';
  end if;
  if not exists (select 1 from pg_policies
                  where schemaname = 'storage' and tablename = 'objects'
                    and policyname = 'avatares_bucket_sem_acesso_api'
                    and permissive = 'RESTRICTIVE'
                    and roles::text[] @> array['anon', 'authenticated']) then
    raise exception 'Politica avatares_bucket_sem_acesso_api ausente ou nao RESTRICTIVE.';
  end if;
end
$$;
