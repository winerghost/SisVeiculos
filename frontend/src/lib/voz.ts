/**
 * Ditado por voz com a Web Speech API nativa. Nenhum áudio é gravado, guardado ou enviado por
 * este código (sem MediaRecorder, sem Blob de áudio): só o texto reconhecido chega à página.
 *
 * Privacidade: com processamento LOCAL o áudio fica no aparelho. Sem ele, o navegador usa o
 * serviço do fornecedor (Chrome = Google, Safari = Apple) e o uso exige consentimento explícito
 * (mantido apenas em memória da sessão; nunca em localStorage).
 */

export interface AlternativaVoz {
  transcript: string;
}
export interface ResultadoVoz {
  readonly isFinal: boolean;
  readonly length: number;
  [indice: number]: AlternativaVoz;
}
export interface EventoResultadoVoz {
  readonly resultIndex: number;
  readonly results: { readonly length: number; [indice: number]: ResultadoVoz };
}
export interface EventoErroVoz {
  readonly error: string;
}

export interface ReconhecedorVoz {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  processLocally?: boolean;
  onstart: (() => void) | null;
  onresult: ((e: EventoResultadoVoz) => void) | null;
  onerror: ((e: EventoErroVoz) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}

type DisponibilidadeLocal = 'available' | 'downloadable' | 'downloading' | 'unavailable';
interface OpcoesLocal {
  langs: string[];
  processLocally: boolean;
}
interface ConstrutorVoz {
  new (): ReconhecedorVoz;
  available?: (opcoes: OpcoesLocal) => Promise<DisponibilidadeLocal>;
  install?: (opcoes: OpcoesLocal) => Promise<boolean>;
}

export const IDIOMA_VOZ = 'pt-BR';
/** Tempo máximo de captura; passado isso, a parada é automática. */
export const LIMITE_VOZ_MS = 60_000;

function construtor(): ConstrutorVoz | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    SpeechRecognition?: ConstrutorVoz;
    webkitSpeechRecognition?: ConstrutorVoz;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function vozSuportada(): boolean {
  return construtor() !== null;
}

/** 'local' = áudio processado no aparelho; 'servico' = serviço do fornecedor do navegador. */
export type ModoVoz = 'local' | 'servico';

/**
 * Consulta (com feature detection) se há reconhecimento local para pt-BR. Se o modelo puder ser
 * baixado, tenta instalá-lo; qualquer falha cai em 'servico' (que exige consentimento).
 */
export async function detectarModoVoz(): Promise<ModoVoz> {
  const C = construtor();
  if (!C || typeof C.available !== 'function') return 'servico';
  const opcoes: OpcoesLocal = { langs: [IDIOMA_VOZ], processLocally: true };
  try {
    const d = await C.available(opcoes);
    if (d === 'available') return 'local';
    if ((d === 'downloadable' || d === 'downloading') && typeof C.install === 'function') {
      return (await C.install(opcoes)) ? 'local' : 'servico';
    }
  } catch {
    // Sem suporte confiável: trata como serviço do fornecedor.
  }
  return 'servico';
}

export function criarReconhecedor(modo: ModoVoz): ReconhecedorVoz | null {
  const C = construtor();
  if (!C) return null;
  const r = new C();
  r.lang = IDIOMA_VOZ;
  r.continuous = false;
  r.interimResults = true;
  r.maxAlternatives = 1;
  if (modo === 'local') r.processLocally = true;
  return r;
}

// Consentimento só em memória do módulo: some ao recarregar/fechar a aba (nunca persistido).
let consentimentoServico = false;
export const consentimentoConcedido = (): boolean => consentimentoServico;
export const concederConsentimento = (): void => {
  consentimentoServico = true;
};
