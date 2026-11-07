'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/lib/auth';

export default function Inicio() {
  const { estado } = useAuth();
  const router = useRouter();
  React.useEffect(() => {
    if (estado === 'anonimo') router.replace('/login');
    else if (estado !== 'carregando') router.replace('/requisicoes');
  }, [estado, router]);
  return (
    <div role="status" className="flex min-h-dvh items-center justify-center text-texto-2">
      Carregando...
    </div>
  );
}
