'use client';

import { useAuth } from '@/lib/auth';
import { AppShell } from '@/components/app-shell';
import { CalendarioRequisicoes } from '@/components/calendario/calendario-requisicoes';

// O menu só mostra "Calendário" com `ver_calendario` (vindo de /api/eu). Acesso direto sem a
// permissão: a API responde 403 e a mensagem dela aparece na tela.
export default function Calendario() {
  const { eu } = useAuth();
  return (
    <AppShell titulo="Calendário" sobretitulo={eu?.rotulo_perfil} largo>
      <CalendarioRequisicoes />
    </AppShell>
  );
}
