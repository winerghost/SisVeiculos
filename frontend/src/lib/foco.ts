/** id do <h1> do AppShell (tabIndex -1): destino do foco quando o elemento focado some. */
export const ID_TITULO_PAGINA = 'titulo-pagina';

/**
 * Se o foco caiu no <body> (ex.: o botão usado sumiu depois da recarga), leva-o ao título da
 * página, para o leitor de tela não ficar sem contexto. Não mexe se o foco está em outro lugar.
 */
export function focarTituloSePerdido(): void {
  requestAnimationFrame(() => {
    const ativo = document.activeElement;
    if (ativo && ativo !== document.body && ativo.isConnected) return;
    document.getElementById(ID_TITULO_PAGINA)?.focus();
  });
}
