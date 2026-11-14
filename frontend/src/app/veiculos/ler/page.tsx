'use client';

import { useAuth } from '@/lib/auth';
import { AppShell } from '@/components/app-shell';
import { Alerta } from '@/components/ui/alert';
import { LeitorQr } from '@/components/leitor-qr';

function Conteudo() {
  const { eu } = useAuth();
  // A flag vem da API; com o recurso desligado a própria API também recusa a consulta.
  if (eu?.recursos?.leitor_qr_veiculos !== true) {
    return <Alerta>O leitor de QR Code não está habilitado.</Alerta>;
  }
  return <LeitorQr />;
}

export default function LerVeiculo() {
  return (
    <AppShell titulo="Ler QR Code">
      <Conteudo />
    </AppShell>
  );
}
