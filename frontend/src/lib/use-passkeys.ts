'use client';

import * as React from 'react';
import { api, ApiErro } from './api';
import { useAuth } from './auth';
import { criarCredencial, mensagemErroPasskey, obterAssercao } from './passkeys';
import { useTatil } from './tatil';
import type { ListaPasskeys, Passkey } from './tipos';

type Operacao = 'adicionar' | 'confirmar' | 'revogar';

/** Estado e ações de passkeys. Tudo que a API decide (recurso, permissão, prazo) vem dela. */
export function usePasskeys() {
  const { tratarErro } = useAuth();
  const tatil = useTatil();
  const [itens, setItens] = React.useState<Passkey[] | null>(null);
  const [lista, setLista] = React.useState<ListaPasskeys | null>(null);
  const [reautenticadoAte, setReautenticadoAte] = React.useState<string | null>(null);
  const [indisponivel, setIndisponivel] = React.useState(false);
  const [ocupado, setOcupado] = React.useState<Operacao | null>(null);
  const [erro, setErro] = React.useState<string | null>(null);
  const [reautRequerida, setReautRequerida] = React.useState(false);
  const trava = React.useRef(false);

  const carregar = React.useCallback(async () => {
    try {
      const l = await api.passkeys();
      setItens(l.itens);
      setLista(l);
    } catch {
      // Recurso desligado ou indisponível: o componente some sem ruído.
      setIndisponivel(true);
    }
  }, []);

  React.useEffect(() => {
    let ativo = true;
    void api
      .passkeys()
      .then((l) => {
        if (!ativo) return;
        setItens(l.itens);
        setLista(l);
      })
      .catch(() => {
        // Recurso desligado ou indisponível: o componente some sem ruído.
        if (ativo) setIndisponivel(true);
      });
    return () => {
      ativo = false;
    };
  }, []);

  const executar = React.useCallback(
    async (tipo: Operacao, fn: () => Promise<void>): Promise<boolean> => {
      if (trava.current) return false;
      trava.current = true;
      setOcupado(tipo);
      setErro(null);
      setReautRequerida(false);
      let ok = false;
      try {
        await fn();
        ok = true;
        tatil.sucesso();
        await carregar();
      } catch (e) {
        if (!ok) tatil.erro();
        if (e instanceof ApiErro && e.codigo === 'reautenticacao_requerida') {
          setReautRequerida(true);
          setErro(e.message);
        } else {
          setErro(mensagemErroPasskey(e) ?? tratarErro(e));
        }
      } finally {
        trava.current = false;
        setOcupado(null);
      }
      return ok;
    },
    [carregar, tatil, tratarErro],
  );

  const adicionar = (apelido: string) =>
    executar('adicionar', async () => {
      const ap = apelido.trim() || undefined;
      const opcoes = await api.passkeysOpcoesRegistro(ap ? { apelido: ap } : {});
      const credencial = await criarCredencial(opcoes);
      if (!credencial) throw new DOMException('sem credencial', 'NotAllowedError');
      await api.passkeysConfirmarRegistro({ credencial, ...(ap ? { apelido: ap } : {}) });
    });

  const confirmar = () =>
    executar('confirmar', async () => {
      const opcoes = await api.passkeysOpcoesAutenticacao();
      const credencial = await obterAssercao(opcoes);
      if (!credencial) throw new DOMException('sem credencial', 'NotAllowedError');
      const r = await api.passkeysConfirmarAutenticacao({ credencial });
      setReautenticadoAte(r.reautenticado_ate ?? null);
    });

  const revogar = (id: string) =>
    executar('revogar', async () => {
      await api.passkeysRevogar(id);
    });

  return {
    itens,
    acoes: lista?.acoes_permitidas ?? [],
    identidadeConfirmada: lista?.identidade_confirmada === true,
    reautenticadoAte,
    indisponivel,
    ocupado,
    erro,
    reautRequerida,
    adicionar,
    confirmar,
    revogar,
  };
}
