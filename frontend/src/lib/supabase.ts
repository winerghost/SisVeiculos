import { createClient, type SupabaseClient } from '@supabase/supabase-js';

// O SDK é usado só para login/sessão/MFA. Dados passam pelo Flask (/api).
// Criação preguiçosa: o build estático não pode falhar sem as variáveis.
let cliente: SupabaseClient | null = null;

export function supabase(): SupabaseClient {
  if (!cliente) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const chave = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !chave) {
      throw new Error('Configuração de login ausente (NEXT_PUBLIC_SUPABASE_*).');
    }
    cliente = createClient(url, chave);
  }
  return cliente;
}
