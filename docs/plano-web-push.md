# Plano de ação — Notificações Web Push (sem Capacitor)

Recurso opcional, **desligado por padrão**: só funciona se o admin ligar "Notificações push" em
Configurações (flag `notificacao_push`). Desligado, a API recusa os endpoints e nada é enviado.

## Decisão de arquitetura (a que muda o resto)
O push **reaproveita a tabela `notificacoes`** (o "sino") como fila de saída, como o e-mail já faz:
- Os **destinatários** já são decididos pelos gatilhos do banco (regras e permissões existentes);
  o push não cria regra nova nem toca nas transições de estado.
- O envio é **assíncrono** na thread do despachante: falha de push nunca desfaz operação confirmada.
- **Sem duplicidade**: cada notificação tem `push_status` (pendente, enviando, enviado, falhou,
  ignorado) reservado com `FOR UPDATE SKIP LOCKED`; no aparelho, `tag` = id da notificação.
- **Não depende do n8n.**
- Trade-off: reaproveitar a fila acopla o push aos tipos de notificação existentes (aceitável: são
  exatamente os eventos pedidos). Entrega "pelo menos uma vez", como o e-mail.

## Contrato entre os times
**Banco** (migration nova `20261120120000_web_push.sql`, nunca editar as antigas):
- `configuracoes.notificacao_push boolean not null default false` (sem dependências) e
  `configuracoes_validar` com a 14ª chave (cópia fiel da versão atual).
- `push_inscricoes` (id, usuario_id → perfis, endpoint, endpoint_hash único, p256dh, auth, criada_em,
  ultimo_sucesso_em, falhas, revogada_em); RLS ligado, sem policy, sem grant a anon/authenticated;
  no máximo 10 ativas por usuário; um endpoint pertence a um usuário por vez (reinscrever reatribui).
- `notificacoes`: `push_status` (linhas antigas = `ignorado`), `push_tentativas`, `push_enviado_em`.
- RPCs só `service_role`: `reservar_push_notificacao(p_limite)`, `marcar_push_notificacao(p_id,
  p_status, p_codigo)`, `registrar_inscricao_push(p_usuario, p_endpoint, p_p256dh, p_auth)`,
  `revogar_inscricao_push(p_usuario, p_endpoint)`, `listar_inscricoes_push(p_usuario)`,
  `marcar_resultado_inscricao_push(p_id, p_ok, p_expirada)`, `purgar_inscricoes_push()`.

**API Flask** (JWT; o usuário vem do token, nunca do body):
- `GET /api/push/config` → `{habilitado, chave_publica}` (habilitado = flag ligada + VAPID configurado).
- `POST /api/push/inscricoes` `{endpoint, keys:{p256dh, auth}}` → 201; `DELETE /api/push/inscricoes`
  `{endpoint}` → 204. Flag desligada → recusa. Rate limit. **Anti-SSRF**: o servidor faz POST no
  endpoint, então só `https`, host público e domínio de serviço push conhecido (`PUSH_ENDPOINT_HOSTS`).
- Env: `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` (nunca no front), `VAPID_SUBJECT`, `PUSH_ENDPOINT_HOSTS`.

**Payload** (genérico, sem PII): `{titulo, texto, destino, tag}` montado de `montar_texto` e
`montar_destino` (mapas fixos). Nunca nome, placa, trajeto, localização, km. Detalhes só após login.

**Front**: `sw.js` (push, notificationclick), `lib/push.ts`, componente de ativação em Notificações.

## Times (cada um só nos seus arquivos)
| Time | Painel | Arquivos |
|---|---|---|
| DB | banco: migration web push | `supabase/migrations/20261120120000_web_push.sql`, testes SQL |
| API | api: config, rotas, repo | `config.py`, `modelos.py`, `services/admin_configuracoes.py`, `services/validacao_admin.py`, `repositorios/*`, `services/push.py`, `rotas/push.py`, registro em `__init__.py`, testes |
| Envio | envio: pywebpush + despachante | `notificacoes/push.py`, `notificacoes/despachante.py`, `requirements*.txt`, testes |
| Front | front: permissão + sw.js | `public/sw.js`, `lib/push.ts`, `components/ativar-push.tsx`, `app/notificacoes/page.tsx`, `lib/api.ts`, `lib/tipos.ts`, `lib/auth.tsx` (revogar no logout), tela de Configurações se a lista for fixa |
| Docs/infra | docs: VAPID, env, nginx | `.env.example`, `docker-compose.yml`, `frontend/nginx.conf`, `scripts/gerar-vapid.py`, `docs/web-push.md`, `CLAUDE.md` |
| Testes | testes: e2e push | `frontend/e2e/push.spec.ts` (depois que Front terminar) |
| Revisão | revisor: segurança/LGPD | somente leitura |

## Ordem
1. DB + API + Envio + Front + Docs em paralelo (contrato acima). 2. Testes e2e. 3. Revisão de
segurança/LGPD. 4. Correções. 5. Validação manual em Android, iOS 16.4+ (PWA instalado) e desktop.

## Antes da produção
Confirmar a política de segurança da AGU; gerar chaves VAPID próprias (`scripts/gerar-vapid.py`);
`supabase db push` pelo pipeline; servidor com `docker compose up -d --build`.
