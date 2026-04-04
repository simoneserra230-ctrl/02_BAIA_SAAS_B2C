-- ═══════════════════════════════════════════════════════════════
--  BA.IA v3.0 — Supabase Schema Setup
--  Esegui nell'SQL Editor di Supabase
--  Ordine: esegui tutto in una volta
-- ═══════════════════════════════════════════════════════════════

-- ── 1. Estensione pgvector ────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;

-- ── 2. Profili utenti ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profiles (
  id          UUID REFERENCES auth.users PRIMARY KEY,
  email       TEXT,
  plan        TEXT DEFAULT 'trial',
  stripe_customer_id TEXT,
  trial_ends_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '14 days'),
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY "profiles: utente vede solo i propri dati"
  ON profiles FOR ALL USING (auth.uid() = id);

-- Trigger: crea profilo automaticamente alla registrazione
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER AS $$
BEGIN
  INSERT INTO public.profiles (id, email, plan)
  VALUES (NEW.id, NEW.email, 'trial')
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION handle_new_user();

-- ── 3. Bandi utente (analizzati manualmente) ─────────────────
CREATE TABLE IF NOT EXISTS bandi (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES profiles(id) ON DELETE CASCADE,
  name        TEXT,
  file_name   TEXT,
  data        JSONB,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE bandi ENABLE ROW LEVEL SECURITY;
CREATE POLICY "bandi: utente vede solo i propri"
  ON bandi FOR ALL USING (auth.uid() = user_id);

-- ── 4. Profili aziende ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aziende (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES profiles(id) ON DELETE CASCADE,
  data        JSONB,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE aziende ENABLE ROW LEVEL SECURITY;
CREATE POLICY "aziende: utente vede solo le proprie"
  ON aziende FOR ALL USING (auth.uid() = user_id);

-- ── 5. Bandi pubblici con embedding vettoriale (scraper) ──────
CREATE TABLE IF NOT EXISTS bandi_public (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  titolo                TEXT NOT NULL,
  ente                  TEXT,
  testo_completo        TEXT,
  embedding             vector(1536),          -- OpenAI/Anthropic embeddings
  regioni               TEXT[],
  ateco_codes           TEXT[],
  importo_min           NUMERIC,
  importo_max           NUMERIC,
  contributo_percentuale NUMERIC,
  scadenza              DATE,
  fonte_url             TEXT,
  hash_contenuto        TEXT UNIQUE,           -- per deduplicazione
  scheda_json           JSONB,
  attivo                BOOLEAN DEFAULT TRUE,
  created_at            TIMESTAMPTZ DEFAULT NOW()
);

-- Indice per ricerca semantica (pgvector IVFFlat)
-- Decommentare dopo aver inserito almeno 100 bandi:
-- CREATE INDEX ON bandi_public USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Indice per filtro rapido su regione e scadenza
CREATE INDEX IF NOT EXISTS idx_bandi_public_regioni ON bandi_public USING gin(regioni);
CREATE INDEX IF NOT EXISTS idx_bandi_public_scadenza ON bandi_public(scadenza) WHERE attivo = TRUE;
CREATE INDEX IF NOT EXISTS idx_bandi_public_hash ON bandi_public(hash_contenuto);

-- Bandi pubblici leggibili da tutti gli utenti autenticati
ALTER TABLE bandi_public ENABLE ROW LEVEL SECURITY;
CREATE POLICY "bandi_public: leggibili da tutti"
  ON bandi_public FOR SELECT USING (auth.role() = 'authenticated');
-- Solo service_role può inserire (backend)
CREATE POLICY "bandi_public: inserimento solo backend"
  ON bandi_public FOR INSERT WITH CHECK (auth.role() = 'service_role');

-- ── 6. Funzione SQL per matching vettoriale ───────────────────
CREATE OR REPLACE FUNCTION match_bandi(
  azienda_embedding vector(1536),
  top_k             INT     DEFAULT 10,
  filter_regione    TEXT    DEFAULT NULL,
  filter_ateco      TEXT    DEFAULT NULL
)
RETURNS TABLE (
  id          UUID,
  titolo      TEXT,
  ente        TEXT,
  score       FLOAT,
  scheda_json JSONB,
  importo_max NUMERIC,
  scadenza    DATE,
  fonte_url   TEXT
)
LANGUAGE SQL STABLE AS $$
  SELECT
    id,
    titolo,
    ente,
    1 - (embedding <=> azienda_embedding) AS score,
    scheda_json,
    importo_max,
    scadenza,
    fonte_url
  FROM bandi_public
  WHERE
    attivo = TRUE
    AND scadenza > NOW()::DATE
    AND (filter_regione IS NULL OR filter_regione = ANY(regioni) OR array_length(regioni, 1) IS NULL)
    AND (filter_ateco IS NULL OR filter_ateco = ANY(ateco_codes) OR array_length(ateco_codes, 1) IS NULL)
    AND embedding IS NOT NULL
  ORDER BY embedding <=> azienda_embedding
  LIMIT top_k;
$$;

-- ── 7. Risultati matching ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS match_results (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES profiles(id) ON DELETE CASCADE,
  bando_id    UUID REFERENCES bandi_public(id) ON DELETE SET NULL,
  score       FLOAT,
  motivazione TEXT,
  rank        INT,
  viewed      BOOLEAN DEFAULT FALSE,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE match_results ENABLE ROW LEVEL SECURITY;
CREATE POLICY "match_results: utente vede solo i propri"
  ON match_results FOR ALL USING (auth.uid() = user_id);

-- ── 8. Subscriptions alert email ─────────────────────────────
CREATE TABLE IF NOT EXISTS alert_subscriptions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID REFERENCES profiles(id) ON DELETE CASCADE,
  email       TEXT NOT NULL,
  min_score   FLOAT DEFAULT 0.70,
  regione     TEXT,
  ateco       TEXT,
  active      BOOLEAN DEFAULT TRUE,
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id)
);

ALTER TABLE alert_subscriptions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "alert_subscriptions: utente vede solo le proprie"
  ON alert_subscriptions FOR ALL USING (auth.uid() = user_id);

-- ── 9. Sorgenti scraper ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS scraper_sources (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  nome          TEXT NOT NULL,
  url           TEXT NOT NULL,
  tipo          TEXT DEFAULT 'html',
  ultimo_hash   TEXT,
  ultima_run    TIMESTAMPTZ,
  attivo        BOOLEAN DEFAULT TRUE,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Inserimento fonti iniziali
INSERT INTO scraper_sources (nome, url, tipo) VALUES
  ('MIMIT — Incentivi', 'https://www.mimit.gov.it/it/incentivi', 'html'),
  ('Invitalia', 'https://www.invitalia.it/cosa-facciamo/rafforziamo-le-imprese', 'html'),
  ('SIMEST', 'https://www.simest.it/finanziamenti-agevolati/', 'html'),
  ('Regione Sardegna', 'https://www.regione.sardegna.it/j/v/86?s=1&v=9&c=191', 'html'),
  ('Unioncamere', 'https://www.unioncamere.gov.it/node/4286', 'html')
ON CONFLICT DO NOTHING;

-- ═══════════════════════════════════════════════════════════════
--  FINE SETUP
--  Esegui questo file una sola volta nel tuo progetto Supabase.
--  Poi avvia il backend Render — il trigger e le policy sono attivi.
-- ═══════════════════════════════════════════════════════════════
