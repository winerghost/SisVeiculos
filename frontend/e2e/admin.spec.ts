import { expect, test, type Locator, type Page } from '@playwright/test';
import { mockarAdmin, type OpcoesAdmin } from './mock-admin';

async function entrarAdmin(page: Page, opcoes: OpcoesAdmin = {}) {
  const mock = await mockarAdmin(page, opcoes);
  await page.goto('/login');
  await page.getByLabel('E-mail').fill('admin@agu.gov.br');
  await page.getByLabel('Senha', { exact: true }).fill('senha-correta');
  await page.getByRole('button', { name: 'Entrar' }).click();
  return mock;
}

// Sem os payloads RSC (404 no `serve`), o Next troca a navegação suave por carga completa e uma
// digitação feita no meio da troca se perde. Abrimos o destino do link direto, já carregado.
async function abrir(page: Page, link: Locator) {
  const href = await link.getAttribute('href');
  await page.goto(href ?? '/');
}

test('admin: convida usuário e desativa outra conta conforme acoes_permitidas', async ({
  page,
}) => {
  const mock = await entrarAdmin(page);
  await page.getByRole('link', { name: 'Admin', exact: true }).click();
  await page
    .getByRole('link', { name: /Usuários/ })
    .first()
    .click();

  // E-mail longo termina em reticências (truncate) em vez de quebrar a linha.
  const linhaAna = page.getByRole('link', { name: /Ana Souza/ });
  await expect(linhaAna).toBeVisible();
  const estilo = await linhaAna
    .getByText(/ana\.souza\.com\.um/)
    .evaluate((el) => getComputedStyle(el).textOverflow);
  expect(estilo).toBe('ellipsis');

  // Filtro por role vai à API como query.
  await page.getByLabel('Perfil').selectOption('admin');
  await expect(linhaAna).toHaveCount(0);
  await page.getByLabel('Perfil').selectOption('');

  // Convidar.
  await abrir(page, page.getByRole('link', { name: 'Convidar usuário' }));
  await page.getByLabel('E-mail').fill('pedro.alves@gmail.com');
  await page.getByLabel('Nome completo').fill('Pedro Alves');
  await page.getByLabel('Unidade').selectOption('CJU');
  await page.getByLabel('Perfil').selectOption('autorizador');
  await page.getByRole('button', { name: 'Enviar convite' }).click();
  await expect(page.getByText('Convite enviado para')).toBeVisible();
  expect(mock.corpos.convidar[0]).toMatchObject({
    email: 'pedro.alves@gmail.com',
    nome: 'Pedro Alves',
    unidade: 'CJU',
    role: 'autorizador',
  });
  await abrir(page, page.getByRole('link', { name: 'Ver convites' }));
  await expect(page.getByText('pedro.alves@gmail.com')).toBeVisible();
  await expect(page.getByRole('button', { name: /Revogar convite/ })).toBeVisible();

  // Detalhe de outro usuário: a API permite desativar (com confirmação).
  await page.goto('/admin/usuarios');
  await abrir(page, page.getByRole('link', { name: /Ana Souza/ }));
  await page.getByRole('button', { name: 'Desativar conta' }).click();
  await page.getByRole('button', { name: 'Confirmar' }).click();
  await expect(page.getByText('Inativa', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ativar conta' })).toBeVisible();
  // Inativa que ainda oferece `desativar`: o botão reaplica o bloqueio.
  await expect(page.getByRole('button', { name: 'Reaplicar bloqueio' })).toBeVisible();

  // Conta do próprio admin: a API não oferece desativar nem trocar o perfil.
  await page.goto('/admin/usuarios');
  await abrir(page, page.getByRole('link', { name: /^Marcos Silva/ }));
  await expect(page.getByRole('button', { name: 'Salvar alterações' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Desativar conta' })).toHaveCount(0);
  await expect(page.getByLabel('Perfil')).toBeDisabled();
});

test('admin: configurações exibem 409 dependencia_recurso como veio e respeitam acoes_permitidas', async ({
  page,
}) => {
  const mock = await entrarAdmin(page);
  await page.goto('/admin/configuracoes');

  // MFA sem ação permitida: interruptor desabilitado e motivo da API visível.
  await expect(page.getByText('Para ligar, entre antes com o segundo fator (TOTP).')).toBeVisible();
  await expect(
    page.getByRole('switch', { name: 'Verificação em duas etapas (MFA)' }),
  ).toBeDisabled();

  const abast = page.getByRole('switch', { name: 'Abastecimento e manutenção' });
  await abast.click();
  await expect(
    page
      .getByRole('alert')
      .filter({ hasText: "'Abastecimento e manutenção' exige 'Quilometragem' ligado." }),
  ).toBeVisible();
  await expect(abast).toHaveAttribute('aria-checked', 'false');

  const km = page.getByRole('switch', { name: 'Quilometragem' });
  await km.click();
  await expect(km).toHaveAttribute('aria-checked', 'true');
  await abast.click();
  await expect(abast).toHaveAttribute('aria-checked', 'true');
  expect(mock.corpos.config).toEqual([
    { abastecimento_manutencao: true },
    { quilometragem: true },
    { abastecimento_manutencao: true },
  ]);
});

test('admin: IA mostra a recusa da API e só envia a intenção do admin', async ({ page }) => {
  const mock = await entrarAdmin(page);
  await page.goto('/admin/configuracoes');

  const mestre = page.getByRole('switch', { name: 'Inteligência artificial' });
  const saudacao = page.getByRole('switch', { name: 'Saudação sazonal no login' });
  // Sem a chave geral, a API não oferece ação para a saudação.
  await expect(saudacao).toBeDisabled();
  await expect(page.getByText('Ligue antes a chave geral de IA.')).toBeVisible();

  // A recusa (409) é exibida como veio e o interruptor continua desligado.
  await mestre.click();
  await expect(
    page
      .getByRole('alert')
      .filter({ hasText: 'Escolha um provedor com chave configurada antes de ligar a IA.' }),
  ).toBeVisible();
  await expect(mestre).toHaveAttribute('aria-checked', 'false');

  // Provedor sem chave no servidor aparece desabilitado; o configurado pode ser escolhido.
  await expect(
    page
      .getByLabel('Provedor', { exact: true })
      .getByRole('option', { name: /OpenAI \(chave não configurada\)/ }),
  ).toBeDisabled();
  await page.getByLabel('Provedor', { exact: true }).selectOption('anthropic');
  await mestre.click();
  await expect(mestre).toHaveAttribute('aria-checked', 'true');
  await saudacao.click();
  await expect(saudacao).toHaveAttribute('aria-checked', 'true');

  expect(mock.corpos.ia).toEqual([
    { mestre: true },
    { provedor: 'anthropic' },
    { mestre: true },
    { saudacao: true },
  ]);
});

test('admin: veículos listam e abrem a edição conforme acoes_permitidas', async ({ page }) => {
  await entrarAdmin(page);
  await page.goto('/admin/veiculos');
  await expect(page.getByText('placa ABC1D23')).toBeVisible();
  await expect(page.getByRole('switch', { name: /Disponível para agendamento/ })).toBeVisible();
  await page.getByRole('button', { name: 'Editar Veículo 01' }).click();
  await expect(page.getByRole('dialog').getByLabel('Descrição')).toHaveValue('Veículo 01');
});

test('usuários: busca vai à API como q (com debounce, combinada aos filtros) e erro 400 aparece', async ({
  page,
}) => {
  const mock = await entrarAdmin(page);
  await page.goto('/admin/usuarios');
  const busca = page.getByLabel('Buscar por nome ou e-mail');
  await busca.fill('marcos');
  await expect(page.getByRole('link', { name: /Ana Souza/ })).toHaveCount(0);
  await expect(page.getByRole('link', { name: /^Marcos Silva/ })).toBeVisible();
  expect(mock.corpos.consultas.some((c) => c.includes('q=marcos') && c.includes('offset=0'))).toBe(
    true,
  );
  await expect(page.getByText('1–1 de 1')).toBeVisible();
  await page.getByLabel('Situação').selectOption('true');
  expect(mock.corpos.consultas.at(-1)).toContain('q=marcos');
  await busca.fill('a*b');
  await expect(
    page.getByRole('alert').filter({ hasText: 'O termo de busca contém caracteres inválidos.' }),
  ).toBeVisible();
});

test('criar: botões de convidar e adicionar só aparecem com `criar` em acoes_permitidas', async ({
  page,
}) => {
  await entrarAdmin(page);
  await page.goto('/admin/usuarios');
  await expect(page.getByRole('link', { name: 'Convidar usuário' })).toBeVisible();
  await page.goto('/admin/veiculos');
  await expect(page.getByRole('button', { name: 'Adicionar veículo' })).toBeVisible();
});

test('criar ausente: sem `criar` os botões não são exibidos', async ({ page }) => {
  await entrarAdmin(page, { semCriar: true });
  await page.goto('/admin/usuarios');
  await expect(page.getByRole('link', { name: /Ana Souza/ })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Convidar usuário' })).toHaveCount(0);
  await page.goto('/admin/veiculos');
  await expect(page.getByText('placa ABC1D23')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Adicionar veículo' })).toHaveCount(0);
});

const HASH_CONVITE =
  '#access_token=tok-convidado&refresh_token=r-conv&expires_in=3600&token_type=bearer&type=invite';
const HASH_RECUPERACAO =
  '#access_token=tok-convidado&refresh_token=r-conv&expires_in=3600&token_type=bearer&type=recovery';
const TOKEN = 'token-de-convite-com-mais-de-16-chars';

test('aceite de convite: define a senha, chama /api/convites/aceitar e entra no app', async ({
  page,
}) => {
  const mock = await mockarAdmin(page);
  await page.goto(`/convite?convite=${TOKEN}${HASH_CONVITE}`);
  await expect(page.getByRole('heading', { name: 'Ativar minha conta' })).toBeVisible();
  await page.getByLabel('Nova senha').fill('uma-senha-forte-123');
  await page.getByRole('button', { name: 'Concluir cadastro' }).click();
  await expect(page).toHaveURL(/\/requisicoes$/);
  expect(mock.corpos.senha[0]).toMatchObject({ password: 'uma-senha-forte-123' });
  expect(mock.corpos.aceitar).toEqual([{ token: TOKEN }]);
});

test('aceite via link de RECUPERAÇÃO de senha: mesmo fluxo do convite novo', async ({ page }) => {
  const mock = await mockarAdmin(page);
  await page.goto(`/convite?convite=${TOKEN}${HASH_RECUPERACAO}`);
  await expect(page.getByLabel('Nova senha')).toBeVisible();
  // Sem aviso de "outra conta" e com a URL já limpa.
  await expect(page.getByText('logado em outra conta')).toHaveCount(0);
  await expect
    .poll(() => new URL(page.url()).pathname + new URL(page.url()).search)
    .toBe('/convite');
  await page.getByLabel('Nova senha').fill('uma-senha-forte-123');
  await page.getByRole('button', { name: 'Concluir cadastro' }).click();
  await expect(page).toHaveURL(/\/requisicoes$/);
  expect(mock.corpos.senha[0]).toMatchObject({ password: 'uma-senha-forte-123' });
  expect(mock.corpos.aceitar).toEqual([{ token: TOKEN }]);
});

test('B1: token e fragmento saem da barra de endereço; "Tentar novamente" ainda funciona', async ({
  page,
}) => {
  const mock = await mockarAdmin(page, { conviteExpirado: false });
  await page.goto(`/convite?convite=${TOKEN}${HASH_CONVITE}`);
  await expect(page.getByLabel('Nova senha')).toBeVisible();
  await expect
    .poll(() => new URL(page.url()).pathname + new URL(page.url()).search)
    .toBe('/convite');
  expect(page.url()).not.toContain(TOKEN);
  expect(page.url()).not.toContain('access_token');
  // Falha transitória no aceite: a senha já foi definida e o reenvio usa o token em memória.
  let falhar = true;
  await page.route('**/api/convites/aceitar', async (route) => {
    if (falhar) {
      falhar = false;
      return route.fulfill({
        status: 503,
        json: { erro: { codigo: 'indisponivel', mensagem: 'Serviço indisponível.' } },
      });
    }
    return route.fallback();
  });
  await page.getByLabel('Nova senha').fill('uma-senha-forte-123');
  await page.getByRole('button', { name: 'Concluir cadastro' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'Serviço indisponível.' })).toBeVisible();
  await page.getByRole('button', { name: 'Tentar novamente' }).click();
  await expect(page).toHaveURL(/\/requisicoes$/);
  expect(mock.corpos.aceitar).toEqual([{ token: TOKEN }]);
});

test('B2: sessão de outra conta pede para sair antes de continuar o convite', async ({ page }) => {
  await entrarAdmin(page);
  await expect(page.getByRole('heading', { name: 'Requisições' })).toBeVisible();
  await page.goto(`/convite?convite=${TOKEN}`);
  await expect(page.getByRole('alert').filter({ hasText: 'logado em outra conta' })).toBeVisible();
  await expect(page.getByLabel('Nova senha')).toHaveCount(0);
  await page.getByRole('button', { name: 'Sair da conta atual' }).click();
  await expect(page.getByText('Sessão encerrada.')).toBeVisible();
  await expect(page.getByLabel('Nova senha')).toHaveCount(0);
});

test('aceite sem nome: só o código nome_obrigatorio abre o campo (sem reconhecer pelo texto)', async ({
  page,
}) => {
  await mockarAdmin(page, { exigirNome: true, codigoNome: 'requisicao_invalida' });
  await page.goto(`/convite?convite=${TOKEN}${HASH_CONVITE}`);
  await page.getByLabel('Nova senha').fill('uma-senha-forte-123');
  await page.getByRole('button', { name: 'Concluir cadastro' }).click();
  await expect(page.getByRole('alert').filter({ hasText: "campo 'nome'" })).toBeVisible();
  await expect(page.getByLabel('Nome completo')).toHaveCount(0);
});

test('usuários: aviso busca_parcial só aparece quando a API devolve true', async ({ page }) => {
  await entrarAdmin(page, { buscaParcial: true });
  await page.goto('/admin/usuarios');
  const aviso = page
    .getByRole('status')
    .filter({ hasText: 'A busca pode estar incompleta. Refine o termo.' });
  await expect(page.getByRole('link', { name: /Ana Souza/ })).toBeVisible();
  await expect(aviso).toHaveCount(0);
  await page.getByLabel('Buscar por nome ou e-mail').fill('marcos');
  await expect(aviso).toBeVisible();
  await page.getByLabel('Buscar por nome ou e-mail').fill('');
  await expect(aviso).toHaveCount(0);
});

test('convites: 429 reenvio_muito_recente mostra a mensagem da API e o botão segue utilizável', async ({
  page,
}) => {
  const mock = await entrarAdmin(page, { reenvioRecente: true });
  await page.goto('/admin/convites');
  const botao = page.getByRole('button', { name: /Reenviar convite para maria@gmail.com/ });
  await botao.click();
  await expect(
    page
      .getByRole('alert')
      .filter({ hasText: 'Este convite foi reenviado há pouco. Aguarde alguns minutos.' }),
  ).toBeVisible();
  await expect(botao).toBeEnabled();
  await botao.click();
  await expect(page.getByText('Convite reenviado.')).toBeVisible();
  expect(mock.corpos.reenvios).toBe(2);
});

test('aceite sem nome (nome_obrigatorio): mostra o campo obrigatório e reenvia {token, nome}', async ({
  page,
}) => {
  const mock = await mockarAdmin(page, { exigirNome: true });
  await page.goto(`/convite?convite=${TOKEN}${HASH_CONVITE}`);
  await page.getByLabel('Nova senha').fill('uma-senha-forte-123');
  await page.getByRole('button', { name: 'Concluir cadastro' }).click();
  await expect(page.getByRole('alert').filter({ hasText: "campo 'nome'" })).toBeVisible();
  const campo = page.getByLabel('Nome completo');
  await expect(campo).toHaveAttribute('required', '');
  await campo.fill('Pedro Alves');
  await page.getByRole('button', { name: 'Tentar novamente' }).click();
  await expect(page).toHaveURL(/\/requisicoes$/);
  expect(mock.corpos.aceitar).toEqual([{ token: TOKEN }, { token: TOKEN, nome: 'Pedro Alves' }]);
});

test('aceite de convite expirado exibe a mensagem da API', async ({ page }) => {
  await mockarAdmin(page, { conviteExpirado: true });
  await page.goto(`/convite?convite=${TOKEN}${HASH_CONVITE}`);
  await page.getByLabel('Nova senha').fill('uma-senha-forte-123');
  await page.getByRole('button', { name: 'Concluir cadastro' }).click();
  await expect(
    page
      .getByRole('alert')
      .filter({ hasText: 'Este convite expirou. Peça um novo ao administrador.' }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Ir para o login' })).toBeVisible();
  await expect(page.getByLabel('Nova senha')).toHaveCount(0);
});

test('convite sem sessão na URL pede novo convite', async ({ page }) => {
  await mockarAdmin(page);
  await page.goto(`/convite?convite=${TOKEN}`);
  await expect(
    page.getByRole('alert').filter({ hasText: 'link do convite é inválido' }),
  ).toBeVisible();
});

test('MFA: conta sem autenticador cadastra o TOTP (QR code e verificação)', async ({ page }) => {
  await entrarAdmin(page, { exigirMfa: true, fatores: 0 });
  await expect(page.getByRole('heading', { name: 'Verificação em duas etapas' })).toBeVisible();
  await page.getByRole('button', { name: 'Cadastrar autenticador' }).click();
  await expect(page.getByAltText('QR code para cadastrar o autenticador')).toBeVisible();
  await expect(page.getByText('SEGREDOTOTP123')).toBeVisible();
  await page.getByLabel('Código do autenticador').fill('123456');
  await page.getByRole('button', { name: 'Confirmar cadastro' }).click();
  // Verificado: a API volta a aceitar o token e a área logada abre.
  await expect(page.getByRole('heading', { name: 'Requisições' })).toBeVisible();
});
