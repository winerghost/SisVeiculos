/** Utilitários de Web Push do navegador. Só detecção de suporte; a API decide o resto. */

export type SuportePush = 'ok' | 'ios_fora_da_tela_inicial' | 'sem_suporte';

function ehIos(): boolean {
  const ua = navigator.userAgent;
  // iPadOS 13+ se identifica como Mac com tela de toque.
  return /iPhone|iPad|iPod/.test(ua) || (ua.includes('Macintosh') && navigator.maxTouchPoints > 1);
}

function emTelaInicial(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean };
  return window.matchMedia('(display-mode: standalone)').matches || nav.standalone === true;
}

export function detectarSuporte(): SuportePush {
  if (typeof window === 'undefined') return 'sem_suporte';
  // No iOS/iPadOS o push só existe no app adicionado à Tela de Início (16.4+).
  if (ehIos() && !emTelaInicial()) return 'ios_fora_da_tela_inicial';
  if (
    !('serviceWorker' in navigator) ||
    !('PushManager' in window) ||
    !('Notification' in window)
  ) {
    return 'sem_suporte';
  }
  return 'ok';
}

/** Chave VAPID (base64url) para o formato exigido pelo subscribe. */
export function chaveParaBytes(base64Url: string): Uint8Array<ArrayBuffer> {
  const preenchida = (base64Url + '='.repeat((4 - (base64Url.length % 4)) % 4))
    .replace(/-/g, '+')
    .replace(/_/g, '/');
  const bin = atob(preenchida);
  const bytes = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

async function registro(): Promise<ServiceWorkerRegistration> {
  return navigator.serviceWorker.ready;
}

/** Inscrição atual deste aparelho (ou null). */
export async function inscricaoAtual(): Promise<PushSubscription | null> {
  try {
    // getRegistration() resolve null sem service worker; `ready` nunca resolveria e travaria o logout.
    const reg = await navigator.serviceWorker.getRegistration();
    return reg ? await reg.pushManager.getSubscription() : null;
  } catch {
    return null;
  }
}

export async function inscreverAparelho(chavePublica: string): Promise<PushSubscription> {
  const reg = await registro();
  return reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: chaveParaBytes(chavePublica),
  });
}
