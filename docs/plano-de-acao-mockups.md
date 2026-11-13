# Plano de ação — telas implementadas × mockups

Base: análise das rotas em `frontend/src/app` e capturas das telas reais (390×844 @2x, API mockada) em `docs/mockups/`.
Em conflito com o `CLAUDE.md`, vale o `CLAUDE.md`.

## 1. O que foi feito
- Os PNGs 01–11 foram regenerados a partir das telas reais.
- Foram criados 12–19, 30–36 e 50–58 (telas que não tinham mockup).
- Regerar: servir `frontend/out` em 3100 e rodar `cd frontend && npx playwright test -c playwright.capturas.config.ts`. Os specs ficam em `frontend/e2e/capturas/` e não entram no `test:e2e` nem na imagem.

## 2. Achados (nenhum corrigido no app; decisão do usuário)
Prioridade **Alta** (UX/acessibilidade):
1. Abas do `AdminShell` e dos Dashboards ficam cortadas na borda e não rolam até a aba ativa (ex.: "Configurações" quase fora da tela). Rolar a aba ativa para a vista e/ou indicar rolagem.
2. Botão "Cancelar requisição" quebra em 2 linhas e fica menor que Negar/Autorizar (`cartao-requisicao.tsx`).
3. Botão fixo "Nova requisição" cobre o último cartão da lista; reservar espaço inferior (padding) na lista.
4. Campos de data nativos (Acessos, filtros de Relatórios) mostram `mm/dd/yyyy`. O padrão do projeto é Drawer + Calendar pt-BR.
5. Menu inferior com 5–6 itens fica apertado (itens encostam nas bordas).

Prioridade **Média**:
6. Sem erro por campo (`aria-invalid` + borda `#B3261E`): só existe o Alerta no topo (Nova requisição).
7. Select de Veículo truncado em meia largura ("Veículo 01 (ABC-").
8. Lista de usuários: e-mail e "Último acesso" truncados demais.
9. Chave TOTP quebra no meio da palavra (usar `break-all` controlado/agrupar).
10. Cabeçalho: título colado à logo; o sobretítulo ("UEA · AUTORIZADOR") só aparece em algumas telas.
11. Saudações (IA): filtro quebra em 2 linhas; "Teto de tokens" sem unidade; "Modelo" vazio com provedor "Nenhum".
12. Chips "Vazio/Cheio" do checklist com pouco padding.

Prioridade **Baixa**:
13. Anel de foco mais grosso que a borda padrão; reticências coladas em e-mail longo (detalhe do usuário).
14. Campos e botões reais têm 48 px (`h-12`, `min-h-11`); o `CLAUDE.md` diz 44 px. Decidir qual vale e alinhar o `CLAUDE.md` ou o código.

## 3. Divergências mockup antigo × app (resolvidas pela recaptura)
Título "Requisições" (não "Minhas requisições"), rodapé do login ("ao administrador"), abas de admin no topo (não menu inferior), chips "Administrador" e "Inativo" vermelho, filtros de usuários por select, paginação, campos extras (Nome, Validade em dias, foto), confirmação ao desativar conta. O app é a fonte de verdade; os PNGs antigos descreviam um desenho que nunca foi implementado.

## 4. Ordem proposta
1. Itens 1–5 (alta) em um PR de ajustes visuais, com `frontend-next`, mais testes Playwright; depois recapturar.
2. Itens 6–12 (média).
3. Decisão sobre 44 × 48 px (item 14) e eventuais ajustes do `CLAUDE.md`.
4. `revisor-seguranca` não é necessário (sem mudança de dados/permissões).

Deploy: as mudanças desta entrega (specs e PNGs) não exigem `--build`; as correções do item 2 em diante mexem em `frontend/` e exigirão `docker compose up -d --build`.

## 5. Limitações das capturas
Dados e textos são de mocks (ex.: "Texto do aviso 1."); QR da tela 35 e foto da 36 são ilustrativos; hora do motorista (15, 16) usa a hora atual; `/tela-mfa` (MFA no login) não foi capturada; recursos Checklist/MFA ligados na 33 foram simulados na captura.

## 6. Atualização de 13/11/2026
- Mockups regerados (01–58) e criados 59–76; ver README.
- Correções de UI já aplicadas durante a recaptura (não commitadas): espaçador no fim da lista de requisições (item 3), título menor para nomes longos no `AppShell`, ajustes em `admin-shell`, `bloco-saudacoes` (botão Excluir), `cadastro-totp` e `item-arrastavel`. Revisar o `git diff` antes do commit.
- Ainda abertos: itens 1, 4, 5, 6–12 e 14 da seção 2 (data nativa em Acessos usa `type="date"`, permitido pelo CLAUDE.md).
- Deploy: mudou `frontend/src`, então o servidor precisa de `docker compose up -d --build`.
