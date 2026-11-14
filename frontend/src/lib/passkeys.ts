/**
 * Utilitários WebAuthn nativos (sem dependências). Só converte formatos e chama o navegador;
 * a validação criptográfica e toda regra são do backend. Nada é guardado em storage nem no console.
 */
import type { CredencialSerializada, OpcoesWebAuthn, RespostaOpcoesPasskey } from './tipos';

export type SuportePasskey = 'ok' | 'sem_https' | 'sem_suporte';

export function detectarSuportePasskey(): SuportePasskey {
  if (typeof window === 'undefined') return 'sem_suporte';
  if (!window.isSecureContext) return 'sem_https';
  if (!('credentials' in navigator) || typeof window.PublicKeyCredential === 'undefined') {
    return 'sem_suporte';
  }
  return 'ok';
}

export function base64urlParaBuffer(valor: string): ArrayBuffer {
  const preenchido = (valor + '='.repeat((4 - (valor.length % 4)) % 4))
    .replace(/-/g, '+')
    .replace(/_/g, '/');
  const bin = atob(preenchido);
  const bytes = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

export function bufferParaBase64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i] as number);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

type Descritor = { id: string; [k: string]: unknown };

function descritores(lista: unknown): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(lista)) return undefined;
  return (lista as Descritor[]).map((d) => ({
    ...d,
    id: base64urlParaBuffer(d.id),
  })) as PublicKeyCredentialDescriptor[];
}

function raiz(r: RespostaOpcoesPasskey): OpcoesWebAuthn {
  return r.opcoes ?? r;
}

export function opcoesDeCriacao(r: RespostaOpcoesPasskey): PublicKeyCredentialCreationOptions {
  const o = raiz(r) as Record<string, unknown> & { user: Descritor; challenge: string };
  return {
    ...o,
    challenge: base64urlParaBuffer(o.challenge),
    user: { ...o.user, id: base64urlParaBuffer(o.user.id) },
    excludeCredentials: descritores(o.excludeCredentials),
  } as unknown as PublicKeyCredentialCreationOptions;
}

export function opcoesDeLeitura(r: RespostaOpcoesPasskey): PublicKeyCredentialRequestOptions {
  const o = raiz(r) as Record<string, unknown> & { challenge: string };
  return {
    ...o,
    challenge: base64urlParaBuffer(o.challenge),
    allowCredentials: descritores(o.allowCredentials),
  } as unknown as PublicKeyCredentialRequestOptions;
}

function serializar(cred: PublicKeyCredential): CredencialSerializada {
  const r = cred.response;
  const resposta: Record<string, string | null> = {
    clientDataJSON: bufferParaBase64url(r.clientDataJSON),
  };
  if ('attestationObject' in r) {
    resposta.attestationObject = bufferParaBase64url(
      (r as AuthenticatorAttestationResponse).attestationObject,
    );
  }
  if ('authenticatorData' in r) {
    const a = r as AuthenticatorAssertionResponse;
    resposta.authenticatorData = bufferParaBase64url(a.authenticatorData);
    resposta.signature = bufferParaBase64url(a.signature);
    resposta.userHandle = a.userHandle ? bufferParaBase64url(a.userHandle) : null;
  }
  const saida: CredencialSerializada = {
    id: cred.id,
    rawId: bufferParaBase64url(cred.rawId),
    type: cred.type,
    response: resposta,
    clientExtensionResults: cred.getClientExtensionResults() as Record<string, unknown>,
    authenticatorAttachment: cred.authenticatorAttachment,
  };
  if ('getTransports' in r) {
    saida.transports = (r as AuthenticatorAttestationResponse).getTransports();
  }
  return saida;
}

/** Cria a credencial no aparelho; null se o navegador não devolveu nada. */
export async function criarCredencial(r: RespostaOpcoesPasskey) {
  const c = await navigator.credentials.create({ publicKey: opcoesDeCriacao(r) });
  return c ? serializar(c as PublicKeyCredential) : null;
}

/** Obtém a asserção (reautenticação) no aparelho. */
export async function obterAssercao(r: RespostaOpcoesPasskey) {
  const c = await navigator.credentials.get({ publicKey: opcoesDeLeitura(r) });
  return c ? serializar(c as PublicKeyCredential) : null;
}

/** Mensagem neutra para erros do navegador; null = erro não é do WebAuthn (usar tratarErro). */
export function mensagemErroPasskey(e: unknown): string | null {
  if (!(e instanceof DOMException)) return null;
  switch (e.name) {
    case 'NotAllowedError':
    case 'AbortError':
      return 'Operação cancelada ou o tempo acabou. Você pode tentar de novo quando quiser.';
    case 'InvalidStateError':
      return 'Este aparelho já tem uma passkey cadastrada para a sua conta.';
    case 'NotSupportedError':
    case 'SecurityError':
      return 'Este aparelho ou navegador não tem um autenticador compatível. Você continua entrando com e-mail e senha.';
    case 'TimeoutError':
      return 'O tempo para confirmar acabou. Tente novamente.';
    default:
      return 'Não foi possível usar a passkey neste aparelho. Você continua entrando com e-mail e senha.';
  }
}
