'use client';

import * as React from 'react';
import { Download, Upload } from 'lucide-react';
import { admin } from '@/lib/api';
import { salvarArquivo } from '@/lib/relatorios';
import type { RespostaImportacao } from '@/lib/tipos';
import { useEnvio } from '@/lib/use-envio';
import { Alerta } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';

/** Só apresentação: rótulo amigável de cada tabela; tabela desconhecida aparece pela chave. */
const ROTULOS: Record<string, string> = {
  veiculos: 'Veículos',
  requisicoes: 'Requisições',
  km: 'Quilometragem',
  localizacao: 'Localização',
  localizacoes: 'Localização',
  configuracoes: 'Configurações',
  checklist: 'Checklist',
  checklists: 'Checklist',
};

/**
 * Exportar e importar dados. O front só leva o arquivo e exibe a resposta: validação, conflitos
 * e a decisão de poder confirmar (`acoes_permitidas`) são da API. O arquivo fica só na memória.
 */
export function SecaoDados() {
  const envio = useEnvio();
  const [texto, setTexto] = React.useState<string | null>(null);
  const [nomeArquivo, setNomeArquivo] = React.useState('');
  const [resposta, setResposta] = React.useState<RespostaImportacao | null>(null);
  const [concluida, setConcluida] = React.useState(false);
  const [acao, setAcao] = React.useState<'exportar' | 'previa' | 'confirmar' | null>(null);
  const entrada = React.useRef<HTMLInputElement>(null);

  async function exportar() {
    setAcao('exportar');
    const r = await envio.executar(() => admin.exportarDados());
    setAcao(null);
    if (r.ok) salvarArquivo(r.valor.blob, r.valor.nome);
  }

  async function escolher(e: React.ChangeEvent<HTMLInputElement>) {
    const arquivo = e.target.files?.[0];
    setResposta(null);
    setConcluida(false);
    envio.setErro(null);
    if (!arquivo) {
      setTexto(null);
      setNomeArquivo('');
      return;
    }
    const conteudo = await arquivo.text();
    setTexto(conteudo);
    setNomeArquivo(arquivo.name);
    setAcao('previa');
    const r = await envio.executar(() => admin.importarDados(conteudo, 'previa'));
    setAcao(null);
    if (r.ok) setResposta(r.valor);
  }

  async function confirmar() {
    if (texto === null) return;
    setAcao('confirmar');
    const r = await envio.executar(() => admin.importarDados(texto, 'confirmar'));
    setAcao(null);
    if (r.ok) {
      setResposta(r.valor);
      // Só conclui (e descarta o arquivo) se a API confirmar que gravou; senão mostra os erros.
      if (r.valor.aplicado) {
        setConcluida(true);
        setTexto(null);
        setNomeArquivo('');
        if (entrada.current) entrada.current.value = '';
      }
    }
  }

  const podeConfirmar = !!resposta?.acoes_permitidas.includes('confirmar') && !concluida;

  return (
    <section aria-labelledby="secao-dados" className="mt-8">
      <h2 id="secao-dados" className="text-xl font-semibold">
        Dados
      </h2>
      <p className="mb-4 text-texto-2">
        Exporte um arquivo com veículos, requisições, quilometragem, localização, configurações e
        checklists (sem fotos e sem usuários) ou importe um arquivo exportado. A importação só
        acrescenta e atualiza: nada é apagado.
      </p>
      {envio.erro ? <Alerta className="mb-4">{envio.erro}</Alerta> : null}

      <div className="pagina-entra flex flex-col gap-4 rounded-xl border border-borda bg-white p-4">
        <div>
          <h3 className="text-lg font-semibold">Exportar</h3>
          <Button
            variante="contorno"
            className="mt-2"
            carregando={acao === 'exportar'}
            disabled={envio.ocupado}
            onClick={() => void exportar()}
          >
            <Download aria-hidden className="size-5" />
            Exportar dados
          </Button>
          <p className="mt-2 text-texto-2">
            O arquivo contém dados pessoais de servidores e deslocamentos oficiais (LGPD). Guarde-o
            em local seguro, compartilhe só com quem precisa e apague quando não for mais
            necessário.
          </p>
        </div>

        <div>
          <h3 className="text-lg font-semibold">Importar</h3>
          <input
            ref={entrada}
            id="arquivo-importacao"
            data-testid="arquivo-importacao"
            type="file"
            accept="application/json,.json"
            className="sr-only"
            disabled={envio.ocupado}
            onChange={(e) => void escolher(e)}
          />
          <Button
            variante="contorno"
            className="mt-2"
            carregando={acao === 'previa'}
            disabled={envio.ocupado}
            onClick={() => entrada.current?.click()}
          >
            <Upload aria-hidden className="size-5" />
            Selecionar arquivo
          </Button>
          <p className="mt-2 text-texto-2">
            Histórico que cite autorizador, motorista ou vigilante inativo é recusado.
          </p>
          {nomeArquivo ? <p className="mt-2 break-all text-texto-2">{nomeArquivo}</p> : null}
        </div>

        {resposta ? (
          <div role="status" aria-live="polite">
            <h3 className="text-lg font-semibold">
              {concluida ? 'Importação concluída' : 'Prévia da importação'}
            </h3>
            <ul className="lista-animada mt-2 flex flex-col gap-2">
              {Object.entries(resposta.resumo).map(([tabela, r]) => (
                <li
                  key={tabela}
                  className="flex flex-wrap justify-between gap-x-4 rounded-padrao border border-borda px-3 py-2"
                >
                  <span className="font-semibold">{ROTULOS[tabela] ?? tabela}</span>
                  <span className="text-texto-2">
                    {r.novos} novos · {r.existentes} existentes · {r.conflitos} conflitos
                  </span>
                </li>
              ))}
            </ul>
            {resposta.configuracoes_alteradas.length > 0 ? (
              <div className="mt-3">
                <h4 className="font-semibold">
                  Configurações que serão alteradas ({resposta.configuracoes_alteradas.length})
                </h4>
                <ul className="mt-1 flex flex-col gap-1">
                  {resposta.configuracoes_alteradas.map((c) => (
                    <li key={c.chave}>
                      <span className="font-semibold">{c.rotulo}</span>:{' '}
                      {c.de ? 'ligado' : 'desligado'} → {c.para ? 'ligado' : 'desligado'}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {resposta.erros.length > 0 ? (
              <div className="mt-3">
                <h4 className="font-semibold">Erros ({resposta.erros.length})</h4>
                <ul className="mt-1 flex flex-col gap-1 text-[#8a1f17]">
                  {resposta.erros.map((er, i) => (
                    <li key={`${er.tabela}-${er.indice}-${i}`}>
                      {ROTULOS[er.tabela] ?? er.tabela}, linha {er.indice + 1}: {er.mensagem}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        {podeConfirmar ? (
          <Button
            carregando={acao === 'confirmar'}
            disabled={envio.ocupado}
            onClick={() => void confirmar()}
          >
            Confirmar importação
          </Button>
        ) : null}
      </div>
    </section>
  );
}
