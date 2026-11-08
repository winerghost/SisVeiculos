/** Posição obtida do aparelho. Só vive na memória do envio: nunca guardar nem registrar em log. */
export interface Posicao {
  latitude: number;
  longitude: number;
  precisao_m?: number;
}

export const MSG_SEM_LOCALIZACAO =
  'Não foi possível obter sua localização. Permita o acesso à localização do aparelho e tente novamente.';

/**
 * Lê a posição atual (alta precisão, sem cache). Rejeita se o navegador negar, não tiver suporte
 * ou estourar o tempo: o chamador NÃO envia a ação nesse caso. A decisão de exigir ou não é da API.
 */
export function obterPosicao(): Promise<Posicao> {
  return new Promise((resolver, rejeitar) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      rejeitar(new Error(MSG_SEM_LOCALIZACAO));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) =>
        resolver({
          latitude: p.coords.latitude,
          longitude: p.coords.longitude,
          ...(Number.isFinite(p.coords.accuracy) ? { precisao_m: p.coords.accuracy } : {}),
        }),
      () => rejeitar(new Error(MSG_SEM_LOCALIZACAO)),
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 0 },
    );
  });
}
