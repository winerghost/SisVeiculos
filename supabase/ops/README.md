# supabase/ops: ferramentas operacionais (NÃO são migrations)

Esta pasta **não** é lida pelo `supabase db push`. Os arquivos SQL são colados à mão no SQL Editor do Supabase (papel `postgres`).

| Arquivo | Para quê |
|---|---|
| `limpar_dados_de_teste.sql` | Apaga **uma vez**, antes do uso real, os dados operacionais dos testes e (opcional) as contas que não são admin ativo. Por padrão é um **ensaio** que não apaga nada. Não mexe em `storage.*`. |
| `lacrar_producao.sql` | Grava o lacre (`comment on database ... 'sisveiculos:lacrado'`). Com o lacre, a limpeza (SQL e Storage) recusa. |
| `backend/app/ops/limpar_storage.py` (fora desta pasta) | Apaga os **arquivos** dos buckets. Roda dentro do container do backend. |

Passo a passo para quem não é técnico: [`docs/limpeza-pre-lancamento.md`](../../docs/limpeza-pre-lancamento.md) e o guia em PDF [`docs/limpeza-geral-producao.pdf`](../../docs/limpeza-geral-producao.pdf) (fonte `docs/limpeza-geral-producao.html`; gerar em `frontend/` com `node scripts/gerar-pdf-limpeza.mjs`). Mudou o procedimento: atualize os três.

## Ordem

1. **Backup** (Database > Backups só em plano pago; senão pg_dump/export antes).
2. **`supabase db push`** pelo pipeline, com todas as migrations pendentes, incluindo `20261127120000_km_percorrido`, `20261128120000/120100/120200` (telefone) e `20261129120000_ops_banco_lacrado` (RPC do lacre para o Storage). Sem as do SQL: LD004. Depois `git pull` + `docker compose up -d --build` na VPS (traz `app/ops`).
3. **Parar o backend**: `docker compose stop backend`.
4. **Ensaio SQL**: rodar `limpar_dados_de_teste.sql` como está (ou só com `v_manter_usuarios := false` no modo só admins). Sai o erro `ENSAIO: nada foi apagado` com contagens por tabela, contas apagadas/mantidas, arquivos por bucket e o total para `v_confere_total`.
5. **Execução SQL**: `v_executar := true`, `v_confirmacao` (tabela abaixo) e `v_confere_total := <total do ensaio>`. Apagar o snippet salvo no Studio.
6. **Religar o backend**: `docker compose start backend` (o Storage roda nele).
7. **Ensaio Storage**: `docker compose exec backend python -m app.ops.limpar_storage [--apagar-usuarios]`.
8. **Execução Storage**: o mesmo com `--executar`; frase `APAGAR ARQUIVOS ofwvoztarroglekfwohs` (stdin ou `--confirmacao`). Sempre **depois** do SQL, para o plano de `avatares` refletir o banco já limpo.
9. **Conferência**: login do admin real, lista vazia, veículo livre, ensaio do Storage sem nada a apagar em `checklists`.
10. **Lacre** no lançamento: `lacrar_producao.sql` com `'LACRAR PRODUCAO ofwvoztarroglekfwohs'`.

## Parâmetros de `limpar_dados_de_teste.sql`

| Parâmetro | Padrão | Efeito |
|---|---|---|
| `v_executar` | `false` | `false` = ensaio (faz tudo e desfaz). |
| `v_confirmacao` | `''` | Frase exata (abaixo). |
| `v_confere_total` | `-1` | Total do ensaio; `-1` nunca vale (LD007 se não bater). |
| `v_ref_esperado` | `'ofwvoztarroglekfwohs'` | Entra na frase; o banco não confere o ref. |
| `v_manter_usuarios` | `true` | `false` = apaga todas as contas que não são admin ativo. |
| `v_apagar_auditoria` | `true` | `false` = preserva a auditoria inteira. |
| `v_lacrar` | `false` | `true` = grava o lacre ao fim da execução. |

Parâmetro `NULL` é recusado (LD003). Ensaio e execução têm de usar o **mesmo modo** (`v_manter_usuarios` e `v_apagar_auditoria`): o total muda com o modo.

Códigos: LD002 lacrado · LD003 frase/parâmetro nulo · LD004 estrutura inesperada ou sem permissão · LD005 verificação final · LD006 lacre recusado · LD007 total não bate · LD008 nenhum admin ativo · LD009 linha mantida aponta para conta a apagar (típico de `v_manter_usuarios = false` com `v_apagar_auditoria = false`).

**Frase de confirmação** (reflete o que será apagado): `APAGAR DADOS DE TESTE <ref>`, mais ` E AUDITORIA` se `v_apagar_auditoria = true`, mais ` E USUARIOS` se `v_manter_usuarios = false`.

| `v_manter_usuarios` | `v_apagar_auditoria` | Frase |
|---|---|---|
| `true` | `true` (padrão) | `APAGAR DADOS DE TESTE ofwvoztarroglekfwohs E AUDITORIA` |
| `true` | `false` | `APAGAR DADOS DE TESTE ofwvoztarroglekfwohs` |
| `false` | `true` | `APAGAR DADOS DE TESTE ofwvoztarroglekfwohs E AUDITORIA E USUARIOS` |
| `false` | `false` | `APAGAR DADOS DE TESTE ofwvoztarroglekfwohs E USUARIOS` |

## Classificação das 21 tabelas de `public`

- **Sempre apagadas:** `requisicoes`, `requisicoes_arquivadas`, `checklists`, `checklist_fotos`, `notificacoes`, `localizacoes_viagem`, `envios_email`, `ia_uso`, `acessos_login`, `desafios_webauthn`, `reautenticacoes_webauthn`.
- **Auditoria:** `auditoria` apagada por padrão; fica só a linha da própria limpeza (`acao_administrativa` / `limpeza_pre_lancamento`, só contagens, sem PII).
- **Dependem de `v_manter_usuarios`:** `perfis`, `convites`, `perfil_fotos`, `push_inscricoes`, `credenciais_webauthn` (e `auth.users`).
  - `true`: tudo mantido.
  - `false`: apaga `perfis` + `auth.users` (dependências em `auth.*` pelo cascade do Auth) de quem não é admin ativo, e as `perfil_fotos`, `push_inscricoes` e `credenciais_webauthn` dessas contas; apaga **todos** os `convites`. Recusa se não houver ao menos 1 admin ativo (LD008) ou se alguma linha que fica apontar para conta a apagar (LD009).
- `requisicoes_arquivadas` não tem gatilho de DELETE (desarquivar já é DELETE).
- **Sempre mantidas:** `veiculos` (com km atual), `configuracoes`, `ia_configuracoes`, `saudacoes`.

Tabela de `public` fora dessa lista: recusa com LD004 (`ESTRUTURA INESPERADA`).

## Travas (mantidas)

- Ensaio por padrão; execução exige frase + total do ensaio.
- Lacre `sisveiculos:lacrado` recusa sempre (LD002); `sisveiculos:descartavel` marca o banco local de testes e nunca é sobrescrito pelo lacre (LD006).
- Transação única com `lock_timeout` de 15 s: qualquer erro desfaz tudo.
- Gatilhos de proteção são desligados **pelo nome**, religados **no mesmo modo de antes** (`ENABLE ALWAYS` ou `ENABLE`) e o estado de todos os gatilhos de `public` é comparado no fim (LD005 se mudar). Gatilhos de TRUNCATE nunca são tocados.
- No fim confere: ≥ 1 admin ativo e contagens das mantidas iguais às do ensaio.
- Mensagens e logs só com contagens (LGPD).

## Storage (`app.ops.limpar_storage`)

- Usa `SUPABASE_URL` e a `service_role` já presentes no container; o ref vem de `SUPABASE_URL`. Nunca imprime segredos, URLs assinadas nem caminhos com id de pessoa: só contagens por bucket.
- `checklists`: sempre esvaziado. `avatares`: fica só a foto **vigente** de cada perfil (`perfil_fotos.removida_em IS NULL`); apaga órfãos (sem linha em `perfil_fotos`), uploads temporários `tmp/` e fotos já trocadas/removidas. Com `--apagar-usuarios`, apaga também as vigentes de quem não é admin ativo. Outros buckets: só reportados.
- Recusa se o banco estiver lacrado. O lacre é lido pela RPC `public.ops_banco_lacrado()` (só `service_role`); migration `20261129120000_ops_banco_lacrado` (`security definer`, só devolve booleano, `true` se o comentário contém `sisveiculos:lacrado`). Sem ela o ensaio avisa e `--executar` recusa (falha fechada).
- Saída: `0` ok; `2` recusado (nada apagado); `1` falha (pode ter apagado parte; rode o ensaio de novo).
- A frase é a mesma nos dois modos.
- Container `read_only`, não-root (uid 10001) e com `PYTHONDONTWRITEBYTECODE=1`: o comando não escreve em disco (só `/tmp`, que é tmpfs).

## Avisos

**NUNCA** rode depois de haver uso real. Exceção única pré-lançamento ao "sem DELETE físico / auditoria append-only": dados fictícios, gatilhos religados e verificados. Não toca em `pg_cron`, nas funções de expurgo nem em `auth.sessions`/`auth.audit_log_entries` além do cascade do Auth ao apagar contas no modo só admins.

**Lacre:** protege contra erro e esquecimento, **não contra má-fé** (o dono desfaz com `comment on database postgres is 'default administrative connection database';`), e pode se perder em restore ou recriação do projeto.

Testes: `supabase/tests/test_ops_limpeza.py` (só banco local descartável, marcador `sisveiculos:descartavel`), `supabase/tests/test_ops_banco_lacrado.py` (RPC do lacre) e `backend/tests/test_ops_limpar_storage.py` (Storage com cliente falso).
