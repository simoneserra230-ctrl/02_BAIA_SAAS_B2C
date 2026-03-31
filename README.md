# 🚀 GrantLens Pro — AI Analisi Bandi SaaS

Stack completo per trasformare lo strumento locale in un **SaaS vendibile** con auth, pagamenti e cloud sync.

---


## 🔧 Fix v2.1.1 (31/03/2026)

### Bug corretti

| # | Bug | Causa | Fix |
|---|-----|-------|-----|
| 1 | **`pyiceberg` build fail su Windows** | `supabase>=2.15` usa `storage3>=2.x` che dipende da `pyiceberg`, libreria C non compilabile su Python 3.13/3.14 senza MSVC | Pinned `supabase>=2.9.0,<2.15.0` |
| 2 | **`reportlab` mancante** | `backend/haccp_report.py` usa reportlab ma non era in `requirements.txt` | Aggiunto `reportlab>=4.2.0` |
| 3 | **Endpoint HACCP non esposto** | Il modulo HACCP esisteva ma non aveva route FastAPI | Aggiunti `/haccp/report` e `/haccp/demo` |
| 4 | **`avvia.bat` non creava venv** | Il launcher usava Python di sistema senza isolare l'ambiente | Creazione automatica venv + warning Python 3.13/3.14 |

### Requisiti Python
- **Consigliato**: Python 3.11 o 3.12
- **Supportato**: Python 3.10+
- **Sconsigliato**: Python 3.13/3.14 (wheel mancanti per alcune dipendenze)


## 📁 Struttura del Progetto

```
saas_project/
├── backend/
│   ├── main.py                  ← FastAPI SaaS (Groq + Supabase + Stripe)
│   ├── requirements.txt         ← Dipendenze Python
│   ├── .env.example             ← Template variabili d'ambiente
│   ├── supabase_setup.sql       ← Schema database (eseguire in Supabase SQL Editor)
│   └── avvia.bat                ← Avvio backend su Windows
└── frontend/
    └── AI_Analisi_Bandi_SaaS.html  ← App SaaS con auth + cloud sync
```

---

## ⚡ Modalità Locale (sviluppo immediato, zero config)

1. Installa le dipendenze:
   ```bash
   pip install -r requirements.txt
   ```

2. Imposta la variabile GROQ:
   ```bash
   # Windows PowerShell
   $env:GROQ_API_KEY = "gsk_..."
   # Linux/Mac
   export GROQ_API_KEY="gsk_..."
   ```

3. Avvia il backend:
   ```bash
   uvicorn main:app --reload --port 8000
   # oppure doppio click su avvia.bat (Windows)
   ```

4. Apri `AI_Analisi_Bandi_SaaS.html` nel browser.
   → Auth overlay compare ma `SAAS_CONFIG.mode = 'local'` lo bypassa automaticamente.
   → Tutto funziona come prima, dati in localStorage.

---

## ☁️ Modalità SaaS (produzione con auth + cloud)

### 1 — Supabase (Database + Auth)

1. Crea account su [supabase.com](https://supabase.com) (gratis)
2. Crea nuovo progetto → regione: **Europe (Frankfurt)** per GDPR
3. Vai su **SQL Editor** → incolla e lancia `supabase_setup.sql`
4. Vai su **Settings → API** e copia:
   - **Project URL** → `SUPABASE_URL`
   - **anon public** key → `supabaseAnonKey` (nel frontend HTML)
   - **service_role** key → `SUPABASE_KEY` (nel backend .env, mai nel frontend!)
   - **JWT Secret** → `SUPABASE_JWT_SECRET`

### 2 — Stripe (Pagamenti)

1. Crea account su [stripe.com](https://stripe.com)
2. **Dashboard → Products → Add product**:
   - Nome: `GrantLens Pro`
   - Prezzo: `129,00 €` / mese (ricorrente)
3. Copia il **Price ID** → `STRIPE_PRICE_ID`
4. **Developers → API Keys** → copia Secret Key → `STRIPE_SECRET_KEY`
5. **Developers → Webhooks → Add endpoint**:
   - URL: `https://tuo-backend.railway.app/stripe/webhook`
   - Events da selezionare:
     - `checkout.session.completed`
     - `customer.subscription.deleted`
     - `customer.subscription.paused`
     - `invoice.payment_failed`
6. Copia **Webhook signing secret** → `STRIPE_WEBHOOK_SECRET`

> 💡 **Test prima del live**: usa le carte di test Stripe (es. `4242 4242 4242 4242`) finché non sei pronto per il live.

### 3 — Railway (Backend Cloud)

1. Vai su [railway.app](https://railway.app) → Sign up con GitHub
2. **New Project → Deploy from GitHub repo** (carica prima il codice su GitHub)
3. Vai su **Variables** e aggiungi tutte le variabili da `.env.example`
4. Railway build e deploy automaticamente (3-5 minuti)
5. Copia l'URL generato, es. `https://ai-bandi.railway.app`

### 4 — Frontend su Netlify

1. Vai su [netlify.com](https://netlify.com) → Add new site → Deploy manually
2. Trascina la cartella `frontend/` nell'area upload
3. Nel file HTML, aggiorna `SAAS_CONFIG`:
   ```js
   const SAAS_CONFIG = {
     supabaseUrl:     'https://XXXXXXXXX.supabase.co',
     supabaseAnonKey: 'eyJhbGciOi...',
     backendUrl:      'https://ai-bandi.railway.app',
     mode:            'saas',   // ← cambia da 'local' a 'saas'
   };
   ```
4. Ri-deploya il file aggiornato

### 5 — Dominio Personalizzato

1. Compra dominio su Namecheap/OVH (~10-15€/anno)
   Suggerimenti: `grantlens.it` / `aibandi.it` / `bandoai.it`
2. In Netlify → **Domain management** → Add custom domain: `app.tuodominio.it`
3. Configura i DNS del tuo registrar con i nameserver Netlify
4. SSL automatico con Let's Encrypt (attivato da Netlify)
5. In Railway → **Settings → Domain**: aggiungi `api.tuodominio.it`

---

## 💰 Costi Mensili

| Servizio   | Costo              |
|------------|--------------------|
| Railway    | ~5-20€/mese        |
| Netlify    | Gratis             |
| Supabase   | Gratis (fino a 500MB) |
| Dominio    | ~1€/mese           |
| **Totale** | **~10-25€/mese**   |

**Break-even**: 1 cliente a 129€/mese → già in profitto dal primo giorno.

---

## 🔑 Variabili d'Ambiente (backend)

| Variabile               | Obbligatoria | Dove si trova                             |
|-------------------------|:------------:|-------------------------------------------|
| `GROQ_API_KEY`          | ✅            | console.groq.com → API Keys              |
| `GROQ_MODEL`            | ❌            | Default: `meta-llama/llama-4-scout-17b`  |
| `SUPABASE_URL`          | ✅ (SaaS)    | Supabase → Settings → API                |
| `SUPABASE_KEY`          | ✅ (SaaS)    | Supabase → Settings → API (service_role) |
| `SUPABASE_JWT_SECRET`   | ✅ (SaaS)    | Supabase → Settings → API → JWT Secret   |
| `STRIPE_SECRET_KEY`     | ✅ (SaaS)    | Stripe → Developers → API Keys           |
| `STRIPE_PRICE_ID`       | ✅ (SaaS)    | Stripe → Products → Price ID             |
| `STRIPE_WEBHOOK_SECRET` | ✅ (SaaS)    | Stripe → Developers → Webhooks           |
| `FRONTEND_URL`          | ✅ (SaaS)    | Es. `https://app.tuodominio.it`           |

---

## 🗄️ Schema Database (Supabase)

| Tabella    | Descrizione                                           |
|------------|-------------------------------------------------------|
| `profiles` | Estende `auth.users` con piano (`free`/`trial`/`pro`) |
| `bandi`    | Bandi analizzati per utente (JSONB)                   |
| `aziende`  | Profili aziendali per utente (JSONB)                  |

Tutte le tabelle hanno **Row Level Security** attiva: ogni utente vede solo i propri dati.

Alla registrazione, un trigger Supabase crea automaticamente il profilo con piano `trial` (14 giorni gratuiti).

---

## 🔒 Sicurezza

- **JWT Supabase** verificato lato backend su ogni richiesta protetta
- **service_role key** mai esposta nel frontend
- **RLS** (Row Level Security) su tutte le tabelle
- **Webhook Stripe** verificato con firma crittografica
- PDF caricati scritti in file temporanei e **cancellati immediatamente** dopo l'elaborazione
- CORS configurato — in produzione restringi `allow_origins` al dominio del frontend

---

## 🧪 Test Locale Completo

```bash
# 1. Avvia backend
export GROQ_API_KEY="gsk_..."
uvicorn main:app --reload --port 8000

# 2. Verifica health check
curl http://localhost:8000/
# → {"status":"ok","version":"2.0-saas","local_mode":true,...}

# 3. Apri il frontend
# Apri AI_Analisi_Bandi_SaaS.html nel browser
# → L'auth overlay si chiude automaticamente (mode: 'local')
# → Tutte le funzionalità disponibili
```

---

## 📈 Roadmap Suggerita

- **Settimana 1-2**: Sviluppo locale, test con bandi reali
- **Settimana 3**: Setup Supabase + Railway (backend cloud)
- **Settimana 4**: Stripe test mode, prima versione SaaS live
- **Mese 2**: Dominio personalizzato, landing page, primi clienti
- **Mese 3+**: Multi-utente studio (invita colleghi), dashboard admin

---

*GrantLens Pro · Stack: FastAPI + Groq/LLaMA + Supabase + Stripe + Railway + Netlify*
