# Notificações push (Web Push)

Recurso opcional, **desligado por padrão**. Plano e contrato entre times: [plano-web-push.md](plano-web-push.md).

## Arquitetura
- Reaproveita a tabela `notificacoes` (o "sino") como fila. Os destinatários vêm das regras do banco;
  o push não cria regra nova e **não depende do n8n**.
- O despachante do backend (thread própria) reserva notificações com `push_status` pendente
  (`FOR UPDATE SKIP LOCKED`), envia por VAPID aos aparelhos inscritos do destinatário e marca o
  resultado. Falha de push nunca desfaz a operação já confirmada. Entrega "pelo menos uma vez";
  no aparelho, `tag` = id da notificação evita duplicar.
- O backend manda: a flag `notificacao_push` (Configurações) e as chaves VAPID decidem se há envio.
  O front só pede permissão, inscreve o aparelho e exibe.

## Fluxo
1. Usuário ativa no aparelho (tela Notificações): o navegador gera a inscrição e o front envia
   `POST /api/push/inscricoes`.
2. Um evento gera linha em `notificacoes`; o despachante envia o payload genérico ao serviço push.
3. O `sw.js` exibe o aviso; ao tocar, abre a tela de destino (login exigido para ver detalhes).
4. Inscrição expirada (404/410) é revogada; falhas repetidas também.

## Logout e novo login (migration 20261206120000)
Ativou uma vez, não precisa reativar a cada login no **mesmo** aparelho e com o **mesmo** usuário.
- Logout: `DELETE /api/push/inscricoes` com `{endpoint, logout: true}` revoga no servidor com a
  marca `revogada_por_logout` (o aparelho para de receber os avisos de quem saiu) e responde
  `{revogada}`. O front só mantém a inscrição do navegador se `revogada` for `true`; se for
  `false`, der erro ou passar de 1,5 s, cancela a inscrição local (como antes).
- Login: com permissão concedida e inscrição no navegador, o front chama
  `POST /api/push/reativar` `{endpoint}` → `{reativada}`. O banco (`reativar_inscricao_push`) só
  religa se o endpoint for do próprio usuário, a revogação tiver sido por logout há até 30 dias,
  o perfil estiver ativo e houver vaga no limite de 10. Qualquer outro caso: `false`, sem revelar
  o dono. Exige o recurso ligado e VAPID (senão 404); a marca de logout nasce mesmo com o recurso
  desligado.
- Outro usuário no mesmo aparelho (portaria): nada é religado; ele vê o botão "Ativar avisos" e só
  o clique (opt-in) passa o aparelho para ele (`POST /api/push/inscricoes`).
- "Desativar" manual: `DELETE` sem `logout` (204) e cancelamento local; nunca religa sozinho.
- Sessão que expira (401) sem logout: o `DELETE` falha e a inscrição local é cancelada; o usuário
  ativa de novo pelo botão.

## Compatibilidade
| Plataforma | Suporte |
| --- | --- |
| Android / Chrome (e Edge, Firefox) | Sim, no navegador ou PWA instalado. |
| iOS / iPadOS 16.4+ | Só com o PWA **instalado na tela inicial** (Safari: Compartilhar > Adicionar à Tela de Início). |
| iOS antes de 16.4 | Sem suporte. |
| Desktop (Chrome, Edge, Firefox, Safari 16+) | Sim. |
HTTPS é obrigatório (o Cloudflare já entrega).

## Ativação (passo a passo)
1. No seu computador: `python scripts/gerar-vapid.py` (imprime no terminal, não grava arquivo).
2. Na VPS, edite o `.env` (fora do git): `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`
   (`mailto:` institucional). Opcionais: `PUSH_ENDPOINT_HOSTS`, `PUSH_INTERVALO_SEGUNDOS`.
3. Aplique a migration pelo pipeline (`supabase db push`) e suba: `docker compose up -d --build`
   (muda `backend/` e `frontend/`; só o `.env`, `docker compose up -d`).
4. Como admin, ligue "Notificações push" em Configurações (a mudança vai para `auditoria`).
5. Em cada aparelho, o usuário abre Notificações e toca em ativar, aceitando a permissão.

## Privacidade e LGPD
O payload é só `{titulo, texto, destino, tag}`, montado de mapas fixos. **NUNCA** vai no push:
nome, e-mail, unidade, placa, trajeto, localização, km, observação, motorista, checklist ou
qualquer dado da requisição. Detalhes só após login. Novos tipos de notificação devem manter
texto genérico. O serviço push (Google, Apple, Mozilla, Microsoft) vê só o endpoint e o
payload cifrado. Chave privada: só no `.env` do backend, nunca em log, front ou git.

## Rotação das chaves VAPID
Gere novo par, atualize o `.env` e rode `docker compose up -d`. **Isso invalida todas as
inscrições existentes**: os usuários precisam reativar o push em cada aparelho. Faça só em
caso de vazamento ou necessidade; avise os usuários.

## Checklist de teste manual
- [ ] Recurso desligado: `GET /api/push/config` não habilita e nada é enviado.
- [ ] Ligado e ativado: evento gera aviso com o **app fechado** (Android e desktop).
- [ ] iOS 16.4+ com PWA instalado recebe o aviso.
- [ ] Clique no aviso abre a tela correta (pede login se preciso).
- [ ] Aviso não traz dados pessoais.
- [ ] Inscrição expirada (revogada no navegador): é marcada como revogada, sem erro ao usuário.
- [ ] Falha do serviço push (bloqueie o host): a operação original segue e o envio é reprocessado/descartado sem travar.
- [ ] Logout revoga a inscrição do aparelho (nenhum aviso de quem saiu chega).
- [ ] Logout e login do MESMO usuário: avisos voltam sem clicar em "Ativar".
- [ ] Logout de A e login de B no mesmo aparelho: B vê "Ativar avisos"; A não recebe nada ali.
- [ ] "Desativar" e depois logout/login: continua desativado.
- [ ] Sem duplicidade para a mesma notificação.

## Antes da produção
Confirmar a política de segurança da AGU (uso de serviços push de terceiros), gerar chaves
próprias e manter o `.env` fora do git.
