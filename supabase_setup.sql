-- ═══════════════════════════════════════════════════════════════
--  AI Analisi Bandi SaaS — Schema Supabase
--  Incolla questo script in: Supabase → SQL Editor → New query
-- ═══════════════════════════════════════════════════════════════

-- ─── 1. PROFILI UTENTE ────────────────────────────────────────
-- Estende la tabella auth.users con dati di piano/abbonamento
CREATE TABLE IF NOT EXISTS profiles (
  id                  uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email               text,
  plan                text NOT NULL DEFAULT 'free',   -- 'free' | 'trial' | 'pro'
  stripe_customer_id  text,
  trial_ends_at       timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Trigger: crea automaticamente il profilo alla registrazione
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO public.profiles (id, email, plan)
  VALUES (
    new.id,
    new.email,
    'trial'   -- 14 giorni di trial alla registrazione
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN new;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE PROCEDURE public.handle_new_user();

-- ─── 2. BANDI ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bandi (
  id          uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name        text,
  file_name   text,
  data        jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ─── 3. AZIENDE ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aziende (
  id          uuid DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  data        jsonb NOT NULL DEFAULT '{}',
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ─── 4. ROW LEVEL SECURITY ────────────────────────────────────
-- Ogni utente vede SOLO i propri dati

-- profiles
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Utente vede solo il proprio profilo"
  ON profiles FOR ALL USING (auth.uid() = id);

-- bandi
ALTER TABLE bandi ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Utente vede solo i propri bandi"
  ON bandi FOR ALL USING (auth.uid() = user_id);

-- aziende
ALTER TABLE aziende ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Utente vede solo le proprie aziende"
  ON aziende FOR ALL USING (auth.uid() = user_id);

-- ─── 5. INDICI ────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_bandi_user_id   ON bandi(user_id);
CREATE INDEX IF NOT EXISTS idx_aziende_user_id ON aziende(user_id);

-- ─── 6. AGGIORNAMENTO TIMESTAMP ───────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE TRIGGER set_updated_at_bandi
  BEFORE UPDATE ON bandi
  FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();

CREATE TRIGGER set_updated_at_aziende
  BEFORE UPDATE ON aziende
  FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();

CREATE TRIGGER set_updated_at_profiles
  BEFORE UPDATE ON profiles
  FOR EACH ROW EXECUTE PROCEDURE update_updated_at_column();

-- ═══════════════════════════════════════════════════════════════
--  VERIFICA: dopo aver eseguito questo script, vai su
--  Table Editor e verifica che esistano le tabelle:
--  profiles, bandi, aziende
-- ═══════════════════════════════════════════════════════════════
