# Mockups do app (referência visual)

Imagens de 390×844 px (celular, escala 2x), geradas por captura das telas reais (API mockada; ver `docs/plano-de-acao-mockups.md`) do sistema de controle de veículos da AGU.
Servem como referência de **layout e visual**. Regras de negócio, validações e permissões estão no `CLAUDE.md`; em caso de conflito, vale o `CLAUDE.md`.

## Telas

| Arquivo | Tela | Quem usa |
|---|---|---|
| `01-login.png` | Login (e-mail e senha) | Todos |
| `02-minhas-requisicoes.png` | Lista de requisições com status e botão "Nova requisição" | Solicitante |
| `03-nova-requisicao.png` | Formulário no estilo shadcn/ui (referência final), com erro de horário vindo do servidor, campo com erro e campo com foco | Solicitante |
| `04-painel-autorizador.png` | Pendentes (Autorizar/Negar) e autorizadas (Registrar saída) | Autorizador |
| `05-registrar-chegada.png` | Viagem em uso, histórico e botão "Registrar chegada" | Autorizador |
| `06-veiculos.png` | Gestão de veículos, com interruptor de disponibilidade | Admin |
| `07-usuarios.png` | Lista de usuários, busca, filtros e "Convidar usuário" | Admin |
| `08-convidar-usuario.png` | Convite por e-mail (institucional ou Gmail), nome, unidade e perfil | Admin |
| `09-detalhe-usuario.png` | Alterar perfil e unidade, desativar conta | Admin |
| `10-configuracoes.png` | Recursos opcionais: Quilometragem, Abastecimento e manutenção, Checklist, MFA | Admin |
| `11-seletor-data-hora.png` | Seletor de data e hora em gaveta (Drawer) com calendário e hora/minuto | Solicitante |

## Telas capturadas das rotas reais (novas)

| Arquivo | Tela |
|---|---|
| `12-detalhe-requisicao` | Detalhe da requisição com linha do tempo |
| `13-viagem` | Lista de viagens em uso (com km) |
| `14-checklist-saida` | Checklist na saída |
| `15-motorista`, `16-motorista-detalhe` | Motorista |
| `17-notificacoes`, `56-sino-notificacoes` | Notificações |
| `18-redefinir-senha`, `19-convite-aceite` | Redefinir senha e aceite de convite |
| `30-admin-inicio`, `31-convites-pendentes`, `32-acessos` | Admin: início, convites e acessos |
| `33-configuracoes-recursos-ligados`, `34-configuracoes-saudacoes-ia` | Configurações |
| `35-seguranca-mfa`, `36-detalhe-usuario-foto` | Segurança (TOTP) e foto do usuário |
| `50` a `52` | Dashboards: operação, painel, resumo com IA |
| `53`, `54` | Relatórios: filtros e prévia com resumo IA |
| `55-home-saudacao-sazonal` | Banner de saudação |
| `57-menu-inferior-autorizador`, `58-menu-admin` | Menus |

Regerar: `cd frontend && npx serve out -l 3100` e `npx playwright test -c playwright.capturas.config.ts`.

## Como usar

- A biblioteca de componentes é o shadcn/ui com Tailwind. Reproduza a estrutura, a hierarquia e o espaçamento das telas.
- Todos os PNGs são capturas das telas reais (campos de 48 px, raio de 8 px nos campos). A divergência com os 44 px do `CLAUDE.md` está registrada no plano de ação.
- O frontend só exibe. Mensagens de erro, botões disponíveis e estados vêm da API (por exemplo, o aviso de "Horário indisponível" na tela 03 é texto devolvido pelo servidor). O calendário da tela 11 não calcula disponibilidade.
- Nomes, e-mails, horários e placas são dados de exemplo.
- As opções (Quilometragem, Checklist etc.) só mostram campos extras quando o recurso estiver habilitado em Configurações; essas variações ainda não foram desenhadas.
- Na lista de usuários (tela 07), o e-mail pode quebrar de linha em telas estreitas: usar truncamento com reticências.

## Valores visuais

- Fonte: Inter (400, 500, 600). Fundo `#F4F6F9` nas telas de lista e `#FFFFFF` nos formulários, borda `#DDE3EC`, borda de campo `#8493AB`.
- Texto `#0E1B2E`, texto secundário `#55627A`. Cor primária `#0F3D75` (botões, item ativo do menu, interruptor ligado). Foco `#1A5FB4`. Erro `#B3261E`. Interruptor desligado `#8493AB`.
- Botões e campos com 44 a 52 px de altura; menu inferior com 64 px.
- Chips de status (fundo / texto): solicitada `#FFF1D6` / `#7A4A00`; autorizada `#DCEBFF` / `#0B3C7A`; em uso `#D9F2E6` / `#0A5A3A`; concluída `#E8ECF2` / `#3B4658`; negada ou inativo `#FDE2E0` / `#8A1F17`; admin `#E9E3FA` / `#3F2A7A`.
- Ação destrutiva (Negar, Desativar conta): borda e texto `#8A1F17`.
- Logo da AGU no login e no cabeçalho das telas: `logo-agu.webp` nesta pasta é a cópia usada nos mockups. Copie-a para `public/` ou use o arquivo oficial, se houver.

## Telas sem mockup de referência

As telas do Motorista (`/motorista` e `/motorista/detalhe`), o registro de localização e a troca de foto de usuário pelo admin foram implementadas sem mockup de referência, seguindo apenas os tokens e padrões visuais do projeto. Pendente de validação visual.

Também sem mockup, pendentes de validação visual: o banner de saudação sazonal (texto curto e discreto no topo das telas iniciais pós-login, exibido só se a API devolver texto) e o bloco "Saudações" em Configurações, seção de IA (filtro Pendentes/Aprovadas/Rejeitadas com Aprovar/Rejeitar, formulário de frase manual com a dica `{nome}` e "Gerar com IA").

Também sem mockup, pendente de validação visual: o botão "Resumir com IA" com a caixa do resumo e o selo "Resumo gerado por IA; confira os números do painel", nos painéis de Dashboards (exceto Operação agora) e na prévia de Relatórios. O botão só aparece quando a API devolve `resumo_ia: true`; o resumo é gerado no clique e some quando os filtros mudam.
