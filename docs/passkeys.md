# Passkeys (WebAuthn) — análise arquitetural

Recurso opcional `passkeys` (Configurações, desligado por padrão). Plano geral em
[plano-recursos-web-nativos.md](plano-recursos-web-nativos.md), fase 5. O backend é a fonte da verdade:
com o recurso desligado a API ignora e recusa os endpoints; o front só exibe o que `eu.recursos` informar.

## 1. O que a 1ª etapa entrega
- Cadastro, listagem e revogação de passkeys pelo próprio usuário (nome do aparelho, data de uso).
- Uso da passkey como **reautenticação / fator adicional** em ações sensíveis (ex.: cadastrar ou revogar
  outra passkey, ações administrativas que o backend decidir proteger).
- O **login atual (e-mail/senha + MFA TOTP) não muda**.
- O servidor **nunca recebe biometria** nem PIN: o autenticador do aparelho valida o usuário
  localmente e entrega apenas uma asserção assinada.

## 2. Integração com o Supabase Auth, sem sistema paralelo
- O Supabase segue como **único emissor de JWT** e provedor de identidade. **Nenhum JWT é emitido pelo Flask.**
- O Flask apenas valida a asserção WebAuthn (biblioteca `webauthn`/py_webauthn) e grava uma
  **reautenticação de curta duração** (ex.: 5 min) ligada ao `session_id` do JWT vigente e ao `user_id`.
- Rotas sensíveis consultam essa reautenticação (ou exigem login recente pelo `iat`). Sair, trocar de
  sessão ou expirar o prazo a invalida.
- Cadastro de nova passkey exige login recente (`iat`) ou reautenticação por passkey já existente.
- Papéis continuam vindo de `perfis`; passkey não altera permissões.

## 3. Opções para a etapa 2 (login só com passkey ou passkey como 2º fator do gate de MFA)

| Opção | Descrição | Vantagens | Riscos / custos |
|---|---|---|---|
| (a) Suporte nativo do Supabase Auth | Aguardar WebAuthn/passkeys no Supabase Auth e usar o fluxo oficial | Um só emissor de sessão; sem código criptográfico próprio de login; menor superfície | Depende do roadmap do fornecedor; prazo incerto; pode exigir migração das credenciais já cadastradas |
| (b) Sessão via API administrativa do Supabase após validação própria | O Flask valida a passkey e pede ao Supabase (API admin, `service_role`) uma sessão para o usuário | Login por passkey antes do suporte nativo | Parece "JWT arbitrário" emitido por fora do fluxo normal; **aumenta a superfície** (o Flask passa a decidir quem recebe sessão; falha na validação = tomada de conta); depende de API admin e de comportamento não garantido; contorna MFA/políticas do Supabase se mal feita; mais difícil de auditar |
| (c) Manter passkey só como reautenticação | Etapa 1 permanente | Menor risco; sem mudar a identidade; já entrega valor em ações sensíveis | Não elimina senha; sem login sem senha |

**Recomendação:** manter (c) agora e acompanhar (a); adotar (a) quando disponível e estável. Evitar (b)
salvo necessidade comprovada e revisão de segurança dedicada.

> **Substituir o login atual por passkey, ou aceitá-la como segundo fator no gate de MFA, EXIGE aprovação
> expressa do dono do projeto antes de qualquer implementação** (e confirmação com a política de
> segurança da AGU).

## 4. Modelo de dados e RPCs (migration nova; RLS deny-by-default)
- `credenciais_webauthn`: `id`, `usuario_id` (FK `perfis`), `credential_id` (único), `chave_publica`,
  `contador`, `transportes`, `apelido`, `criada_em`, `ultimo_uso_em`, `revogada_em`. Nunca apagar: revogar.
- `desafios_webauthn`: `id`, `usuario_id`, `desafio` (hash ou valor aleatório de 32+ bytes), `tipo`
  (`registro` | `autenticacao`), `session_id`, `criado_em`, `expira_em`, `usado_em`. Limpeza periódica de expirados.
- `reautenticacoes_webauthn` (ou coluna equivalente): `usuario_id`, `session_id`, `expira_em`.
- RPCs `SECURITY DEFINER` só para `service_role` (sem acesso de `anon`/`authenticated`): criar desafio,
  consumir desafio (atômico, uso único), registrar credencial, atualizar contador, revogar, registrar
  reautenticação. Cada cadastro, revogação e reautenticação gera linha em `auditoria` (sem chave pública
  nem credential_id no texto).

## 5. Validações (todas no backend)
- **Desafio:** aleatório, criptograficamente seguro, ligado ao usuário e ao `session_id`.
- **Expiração:** curta (ex.: 2 min) e verificada no banco.
- **Uso único:** consumo atômico; reuso é recusado.
- **Assinatura:** verificada com a chave pública guardada.
- **Origem:** `clientDataJSON.origin` deve estar em `WEBAUTHN_ORIGINS` (https).
- **RP ID:** hash do RP ID na `authenticatorData` deve bater com `WEBAUTHN_RP_ID`.
- **Associação ao usuário:** o `credential_id` pertence ao usuário do JWT e não está revogado.
- **Contador:** deve aumentar; regressão é recusada e auditada (possível clone). Autenticadores que
  mantêm contador 0 (comum em passkeys sincronizadas) são tratados como exceção documentada.
- **User verification** exigida (`userVerification: required`).
- Mensagens de erro genéricas; rate limit nas rotas.

## 6. Variáveis de ambiente
| Variável | Conteúdo |
|---|---|
| `WEBAUTHN_RP_ID` | Domínio registrável, **sem esquema, porta ou caminho** (ex.: `dominio.gov.br`) |
| `WEBAUTHN_RP_NAME` | Nome exibido no diálogo do navegador |
| `WEBAUTHN_ORIGINS` | Origens `https://...` completas, separadas por vírgula |

**Atenção:** o RP ID **não pode mudar** sem invalidar todas as passkeys já cadastradas (elas ficam presas
ao RP ID). Defina com cuidado antes de ligar o recurso; mudar exige novo cadastro por todos os usuários.
Sem as três variáveis, o recurso fica indisponível mesmo ligado. Só `.env` (fora do git): `docker compose up -d`.
Não há segredos nessas variáveis.

## 7. LGPD
- Sem biometria no servidor, nunca.
- `credential_id`, chave pública, desafios e asserções **não vão a logs nem ao n8n**; logs só com
  códigos de erro genéricos, sem dados pessoais.
- Coleta mínima: apelido do aparelho (escolhido pelo usuário) e datas. Revogação preserva o histórico.
- Confirmar a política de segurança da AGU antes de produção.

## 8. Compatibilidade e limitações
- Android/Chrome: suportado (Google Password Manager ou chave de segurança).
- iOS/iPadOS 16+ (Safari e PWA na tela inicial): suportado; exige bloqueio de tela e, para sincronizar, iCloud Keychain.
- Desktop (Chrome, Edge, Safari, Firefox): suportado com Windows Hello, Touch ID ou chave FIDO2.
- Passkeys sincronizadas (iCloud/Google) migram entre aparelhos do mesmo provedor, não entre ecossistemas;
  perder a conta do provedor pode perder a passkey: o login por senha continua como recuperação.
- PWA instalado: o RP ID é o do domínio, então vale igual ao navegador; testar em cada plataforma.
- Exige HTTPS (exceto `localhost` em desenvolvimento) e domínio estável.
- Navegadores sem WebAuthn: a UI não oferece o recurso.

## 9. Checklist de teste manual
1. Recurso desligado: rotas recusam e a UI não mostra passkeys.
2. Ligar em Configurações com `WEBAUTHN_*` definidos; confirmar auditoria da alteração.
3. Cadastrar passkey (Android/Chrome, iOS/Safari, iOS/PWA, desktop); conferir apelido e listagem.
4. Cadastrar sem login recente: deve exigir reautenticação.
5. Reautenticar com a passkey e executar ação sensível; após o prazo, deve pedir de novo.
6. Reutilizar o mesmo desafio/asserção: deve ser recusado.
7. Origem ou RP ID incorreto (ambiente de teste): deve ser recusado.
8. Passkey de outro usuário no mesmo aparelho: recusada (anti-IDOR).
9. Revogar passkey: não autentica mais; histórico e auditoria mantidos.
10. Logout/novo login: reautenticação anterior não vale.
11. Login por senha (e MFA) inalterado.
12. Conferir que logs e payloads do n8n não contêm `credential_id`, chave pública nem desafios.
