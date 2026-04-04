# ═══════════════════════════════════════════════════════════════════
#  BA.IA — Backend SaaS v3.0
#  FastAPI + Multi-AI (Groq/Anthropic/OpenAI/Gemini/Mistral)
#  + Supabase pgvector + Stripe + Scraper 24h + Resend Email Alerts
# ═══════════════════════════════════════════════════════════════════
from fastapi import FastAPI, UploadFile, File, Header, HTTPException, Request, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
import httpx, tempfile, os, asyncio, re, json, sys, hashlib, time
from pypdf import PdfReader
from typing import Optional
from datetime import datetime, date

# ── .env ──────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
    print("[STARTUP] .env caricato")
except ImportError:
    print("[STARTUP] python-dotenv non disponibile — uso variabili di sistema")

# ── Dipendenze opzionali ─────────────────────────────────────────
try:
    import stripe; _stripe_ok = True
except ImportError:
    _stripe_ok = False

try:
    from supabase import create_client; _supabase_ok = True
except ImportError:
    _supabase_ok = False

try:
    from jose import jwt as jose_jwt, JWTError; _jose_ok = True
except ImportError:
    _jose_ok = False

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler; _scheduler_ok = True
except ImportError:
    _scheduler_ok = False
    print("[STARTUP] APScheduler non disponibile — scraper auto disabilitato")

try:
    from bs4 import BeautifulSoup; _bs4_ok = True
except ImportError:
    _bs4_ok = False

# ── HACCP ────────────────────────────────────────────────────────
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
    from haccp_report import HACCPPdfGenerator, HACCPReportData, TemperatureReading
    _haccp_ok = True
except ImportError:
    _haccp_ok = False

# ══════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════
GROQ_API_KEY          = os.environ.get("GROQ_API_KEY", "")
GROQ_URL              = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL            = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

ANTHROPIC_API_KEY     = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY        = os.environ.get("OPENAI_API_KEY", "")
GEMINI_API_KEY        = os.environ.get("GEMINI_API_KEY", "")
MISTRAL_API_KEY       = os.environ.get("MISTRAL_API_KEY", "")

SUPABASE_URL          = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY          = os.environ.get("SUPABASE_KEY", "")
SUPABASE_JWT_SECRET   = os.environ.get("SUPABASE_JWT_SECRET", "")

STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_BASE     = os.environ.get("STRIPE_PRICE_BASE", os.environ.get("STRIPE_PRICE_ID", ""))
STRIPE_PRICE_PRO      = os.environ.get("STRIPE_PRICE_PRO", "")
STRIPE_PRICE_BUSINESS = os.environ.get("STRIPE_PRICE_BUSINESS", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

RESEND_API_KEY        = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM           = os.environ.get("RESEND_FROM", "BA.IA <noreply@baia.it>")

FRONTEND_URL          = os.environ.get("FRONTEND_URL", "https://02-baia-saas-b2-c.vercel.app")

CHUNK_SIZE            = 60_000
SIMPLE_LIMIT          = 320_000
LOCAL_MODE            = not bool(SUPABASE_URL and SUPABASE_KEY)

print(f"[STARTUP] BA.IA v3.0 | LOCAL_MODE={LOCAL_MODE} | Groq={'✓' if GROQ_API_KEY else '✗'} | Anthropic={'✓' if ANTHROPIC_API_KEY else '✗'} | Resend={'✓' if RESEND_API_KEY else '✗'}")

# ── Init Supabase ─────────────────────────────────────────────────
supabase = None
if _supabase_ok and SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print(f"[STARTUP] Supabase connesso ✓")

if _stripe_ok and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# ══════════════════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════════════════
app = FastAPI(title="BA.IA SaaS Backend", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ══════════════════════════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════════════════════════
async def get_current_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    if LOCAL_MODE:
        return {"sub": "local-user-001", "email": "local@dev", "plan": "pro"}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token mancante")
    token = authorization.split(" ", 1)[1]
    if _jose_ok and SUPABASE_JWT_SECRET:
        try:
            return jose_jwt.decode(token, SUPABASE_JWT_SECRET, algorithms=["HS256"], options={"verify_aud": False})
        except JWTError as e:
            raise HTTPException(status_code=401, detail=f"Token non valido: {e}")
    if supabase:
        try:
            res = supabase.auth.get_user(token)
            if res.user:
                return {"sub": res.user.id, "email": res.user.email}
        except Exception as e:
            raise HTTPException(status_code=401, detail=str(e))
    raise HTTPException(status_code=401, detail="Impossibile verificare il token")


async def require_active_subscription(user: dict = Depends(get_current_user)) -> dict:
    if LOCAL_MODE or not supabase:
        return user
    try:
        res = supabase.table("profiles").select("plan").eq("id", user["sub"]).single().execute()
        plan = (res.data or {}).get("plan", "free")
        if plan not in ("pro", "trial", "base", "business"):
            raise HTTPException(status_code=402, detail="Abbonamento richiesto")
    except HTTPException:
        raise
    except Exception:
        pass
    return user

# ══════════════════════════════════════════════════════════════════
# MULTI-PROVIDER AI ENGINE
# ══════════════════════════════════════════════════════════════════

async def ai_call(
    prompt: str,
    provider: str = "auto",
    model: str = None,
    api_key: str = None,
    json_mode: bool = False,
    timeout: int = 120,
) -> tuple[str, str]:
    """
    Universal AI call supporting: groq, anthropic, openai, gemini, mistral, auto.
    Returns (response_text, provider_used).
    'auto' tries: groq → anthropic → openai → error
    """
    if provider == "auto":
        for p in ["groq", "anthropic", "openai"]:
            key = {"groq": GROQ_API_KEY, "anthropic": ANTHROPIC_API_KEY, "openai": OPENAI_API_KEY}[p]
            if key:
                try:
                    return await ai_call(prompt, p, model, api_key, json_mode, timeout)
                except Exception as e:
                    print(f"[AI] {p} failed: {e}, trying next...")
        raise RuntimeError("Nessun provider AI configurato (GROQ_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY)")

    # Resolve key
    key_map = {
        "groq":      api_key or GROQ_API_KEY,
        "anthropic": api_key or ANTHROPIC_API_KEY,
        "openai":    api_key or OPENAI_API_KEY,
        "gemini":    api_key or GEMINI_API_KEY,
        "mistral":   api_key or MISTRAL_API_KEY,
    }
    resolved_key = key_map.get(provider, api_key or "")
    if not resolved_key:
        raise RuntimeError(f"API key mancante per provider '{provider}'")

    print(f"[AI] {provider} | {len(prompt)} chars | json={json_mode}", end=" → ", flush=True)

    async with httpx.AsyncClient(timeout=timeout) as client:

        # ── GROQ ──────────────────────────────────────────────────
        if provider == "groq":
            chosen_model = model or GROQ_MODEL
            body = {
                "model": chosen_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 8192,
                "temperature": 0.1,
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            for attempt in range(3):
                r = await client.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {resolved_key}", "Content-Type": "application/json"},
                    json=body
                )
                if r.status_code == 429 and attempt < 2:
                    wait = float(re.search(r"try again in ([\d.]+)s", r.text or "").group(1) if re.search(r"try again in ([\d.]+)s", r.text or "") else "20") + 1
                    print(f"rate-limit {wait:.0f}s...", end=" ", flush=True)
                    await asyncio.sleep(wait)
                    continue
                if r.status_code != 200:
                    raise RuntimeError(f"Groq {r.status_code}: {r.json().get('error', {}).get('message', r.text[:200])}")
                text = r.json()["choices"][0]["message"]["content"]
                print(f"{len(text)} chars OK")
                return text, "groq"

        # ── ANTHROPIC (Claude) ────────────────────────────────────
        elif provider == "anthropic":
            chosen_model = model or "claude-haiku-4-5-20251001"
            body = {
                "model": chosen_model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            }
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": resolved_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=body
            )
            if r.status_code != 200:
                raise RuntimeError(f"Anthropic {r.status_code}: {r.text[:200]}")
            text = r.json()["content"][0]["text"]
            print(f"{len(text)} chars OK")
            return text, "anthropic"

        # ── OPENAI ────────────────────────────────────────────────
        elif provider == "openai":
            chosen_model = model or "gpt-4o-mini"
            body = {
                "model": chosen_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
                "temperature": 0.1,
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {resolved_key}", "Content-Type": "application/json"},
                json=body
            )
            if r.status_code != 200:
                raise RuntimeError(f"OpenAI {r.status_code}: {r.json().get('error', {}).get('message', r.text[:200])}")
            text = r.json()["choices"][0]["message"]["content"]
            print(f"{len(text)} chars OK")
            return text, "openai"

        # ── GEMINI ────────────────────────────────────────────────
        elif provider == "gemini":
            chosen_model = model or "gemini-1.5-flash"
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent?key={resolved_key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]}
            )
            if r.status_code != 200:
                raise RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            print(f"{len(text)} chars OK")
            return text, "gemini"

        # ── MISTRAL ───────────────────────────────────────────────
        elif provider == "mistral":
            chosen_model = model or "mistral-small-latest"
            body = {
                "model": chosen_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4096,
            }
            r = await client.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {resolved_key}", "Content-Type": "application/json"},
                json=body
            )
            if r.status_code != 200:
                raise RuntimeError(f"Mistral {r.status_code}: {r.text[:200]}")
            text = r.json()["choices"][0]["message"]["content"]
            print(f"{len(text)} chars OK")
            return text, "mistral"

        else:
            raise RuntimeError(f"Provider '{provider}' non supportato")

    raise RuntimeError(f"AI call failed after retries")


# Legacy compat
async def groq_call(prompt: str, json_mode: bool = False, timeout: int = 120, _retry: int = 0) -> tuple[str, float]:
    text, _ = await ai_call(prompt, "groq", json_mode=json_mode, timeout=timeout)
    return text, 0.0


# ══════════════════════════════════════════════════════════════════
# PDF UTILS
# ══════════════════════════════════════════════════════════════════
def extract_text_from_pdf(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    return "".join(page.extract_text() or "" for page in reader.pages)

async def analyze_auto(text: str, provider: str = "auto") -> str:
    if len(text) <= SIMPLE_LIMIT:
        result, _ = await ai_call(
            "Sei un esperto di finanza agevolata italiana. Analizza questo bando e restituisci:\n"
            "- Obiettivo principale\n- Beneficiari ammessi\n- Requisiti chiave\n"
            "- Scadenze importanti\n- Opportunità strategiche\n\n"
            f"Testo:\n{text[:SIMPLE_LIMIT]}",
            provider
        )
        return result
    # chunked
    chunks = [text[i:i+CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
    parts = []
    for i, chunk in enumerate(chunks):
        result, _ = await ai_call(
            f"Parte {i+1}/{len(chunks)} di un bando. Estrai Obiettivo, Beneficiari, Requisiti, Scadenze, Opportunità.\n\n{chunk}",
            provider
        )
        parts.append(result)
        if i < len(chunks) - 1:
            await asyncio.sleep(2)
    result, _ = await ai_call(
        "Crea un'analisi finale completa da questi estratti:\n\n" +
        "".join(f"--- Parte {i+1} ---\n{r}\n" for i, r in enumerate(parts)),
        provider, timeout=180
    )
    return result

# ══════════════════════════════════════════════════════════════════
# EMAIL — RESEND
# ══════════════════════════════════════════════════════════════════
async def send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        print(f"[EMAIL] Resend non configurato — skip email a {to}")
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                json={"from": RESEND_FROM, "to": [to], "subject": subject, "html": html}
            )
            ok = r.status_code in (200, 201, 202)
            print(f"[EMAIL] {'✓' if ok else '✗'} → {to} | {r.status_code}")
            return ok
    except Exception as e:
        print(f"[EMAIL] Errore: {e}")
        return False


def build_alert_email(user_email: str, bando: dict, score: float, motivazione: str) -> str:
    nome = bando.get("titolo", "Nuovo bando")
    ente = bando.get("ente", "")
    importo = bando.get("importo_max", "")
    scadenza = bando.get("scadenza", "")
    url = bando.get("fonte_url", FRONTEND_URL)
    score_pct = int(score * 100)

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f8f9fb;font-family:'Segoe UI',Arial,sans-serif;">
  <div style="max-width:600px;margin:32px auto;background:white;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
    <div style="background:linear-gradient(135deg,#0f172a,#1e1b4b);padding:32px;text-align:center;">
      <div style="display:inline-block;background:linear-gradient(135deg,#c69229,#e4a820);border-radius:12px;padding:10px 20px;margin-bottom:12px;">
        <span style="color:white;font-weight:800;font-size:18px;">📊 BA.IA</span>
      </div>
      <h1 style="color:white;font-size:22px;margin:0;font-weight:800;">🎯 Nuovo Bando Compatibile!</h1>
      <p style="color:rgba(255,255,255,0.6);margin:8px 0 0;font-size:14px;">Match score: <strong style="color:#e4a820;">{score_pct}%</strong></p>
    </div>
    <div style="padding:32px;">
      <h2 style="font-size:18px;color:#111827;margin:0 0 8px;">{nome}</h2>
      <p style="color:#6b7280;font-size:13px;margin:0 0 20px;">{ente}</p>

      <div style="display:flex;gap:12px;margin-bottom:24px;">
        <div style="flex:1;background:#f8f9fb;border-radius:10px;padding:14px;text-align:center;">
          <div style="font-size:11px;color:#9ca3af;text-transform:uppercase;font-weight:700;margin-bottom:4px;">Match Score</div>
          <div style="font-size:28px;font-weight:800;color:#c69229;">{score_pct}%</div>
        </div>
        {'<div style="flex:1;background:#f8f9fb;border-radius:10px;padding:14px;text-align:center;"><div style="font-size:11px;color:#9ca3af;text-transform:uppercase;font-weight:700;margin-bottom:4px;">Importo Max</div><div style="font-size:18px;font-weight:800;color:#111827;">€' + f'{importo:,.0f}' + '</div></div>' if importo else ''}
        {'<div style="flex:1;background:#f8f9fb;border-radius:10px;padding:14px;text-align:center;"><div style="font-size:11px;color:#9ca3af;text-transform:uppercase;font-weight:700;margin-bottom:4px;">Scadenza</div><div style="font-size:15px;font-weight:800;color:#dc2626;">' + str(scadenza) + '</div></div>' if scadenza else ''}
      </div>

      <div style="background:#fffdf5;border:1px solid rgba(198,146,41,0.2);border-radius:10px;padding:16px;margin-bottom:24px;">
        <div style="font-size:11px;font-weight:700;color:#92650a;text-transform:uppercase;margin-bottom:8px;">🤖 Perché è adatto alla tua azienda</div>
        <p style="font-size:14px;color:#374151;margin:0;line-height:1.6;">{motivazione}</p>
      </div>

      <a href="{url}" style="display:block;background:linear-gradient(135deg,#c69229,#e4a820);color:white;text-decoration:none;text-align:center;padding:14px;border-radius:10px;font-weight:700;font-size:15px;">
        → Analizza questo bando in BA.IA
      </a>

      <p style="font-size:11px;color:#9ca3af;text-align:center;margin-top:20px;">
        Hai ricevuto questa email perché hai attivato gli alert su BA.IA.<br>
        <a href="{FRONTEND_URL}?unsubscribe=1" style="color:#9ca3af;">Disattiva notifiche</a>
      </p>
    </div>
  </div>
</body>
</html>
"""

# ══════════════════════════════════════════════════════════════════
# SCRAPER — 20 FONTI ISTITUZIONALI
# ══════════════════════════════════════════════════════════════════
SCRAPER_SOURCES = [
    {"nome": "MIMIT — Mise Incentivi", "url": "https://www.mimit.gov.it/it/incentivi", "tipo": "html"},
    {"nome": "Invitalia", "url": "https://www.invitalia.it/cosa-facciamo/rafforziamo-le-imprese", "tipo": "html"},
    {"nome": "SIMEST", "url": "https://www.simest.it/finanziamenti-agevolati/", "tipo": "html"},
    {"nome": "CDP — Cassa Depositi", "url": "https://www.cdp.it/sitointernet/it/imprese.page", "tipo": "html"},
    {"nome": "Regione Sardegna — SFIRS", "url": "https://www.regione.sardegna.it/j/v/86?s=1&v=9&c=191", "tipo": "html"},
    {"nome": "Regione Lombardia", "url": "https://www.regione.lombardia.it/wps/portal/istituzionale/HP/servizi-e-informazioni/imprese/bandi", "tipo": "html"},
    {"nome": "Regione Lazio", "url": "https://www.lazioeuropa.it/bandi", "tipo": "html"},
    {"nome": "Regione Sicilia", "url": "https://www.euroinfosicilia.it/bandi/", "tipo": "html"},
    {"nome": "Regione Campania", "url": "https://www.regione.campania.it/regione/it/categorie/fondi-europei", "tipo": "html"},
    {"nome": "Regione Puglia", "url": "https://www.regione.puglia.it/area-tematica/-/argomenti/sviluppo-economico", "tipo": "html"},
    {"nome": "Unioncamere — Bandi", "url": "https://www.unioncamere.gov.it/node/4286", "tipo": "html"},
    {"nome": "EU Funding & Tenders", "url": "https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/calls-for-proposals", "tipo": "html"},
    {"nome": "PNRR — Italia Domani", "url": "https://italiadomani.gov.it/it/home.html", "tipo": "html"},
    {"nome": "Gazzetta Ufficiale", "url": "https://www.gazzettaufficiale.it/", "tipo": "html"},
    {"nome": "Confidi — Garanzia Italia", "url": "https://www.mediocredito.it/bandi", "tipo": "html"},
    {"nome": "Regione Toscana", "url": "https://www.regione.toscana.it/-/bandi-e-opportunita", "tipo": "html"},
    {"nome": "Regione Emilia-Romagna", "url": "https://imprese.regione.emilia-romagna.it/bandi", "tipo": "html"},
    {"nome": "Regione Veneto", "url": "https://www.regione.veneto.it/web/economia/bandi-e-opportunit", "tipo": "html"},
    {"nome": "MISE — Bandi PMI", "url": "https://www.mimit.gov.it/it/incentivi/pmi", "tipo": "html"},
    {"nome": "Fondo Cresci al Sud", "url": "https://www.invitalia.it/cosa-facciamo/rafforziamo-le-imprese/fondo-cresci-al-sud", "tipo": "html"},
]

_scraper_running = False
_last_scraper_run: Optional[datetime] = None
_scraper_stats = {"total_found": 0, "new_today": 0, "last_run": None, "errors": []}


async def scrape_source(source: dict) -> list[dict]:
    """Scrape una singola fonte e restituisce bandi trovati."""
    if not _bs4_ok:
        return []
    bandi = []
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; BAIABot/3.0; +https://baia.it/bot)"}
            r = await client.get(source["url"], headers=headers)
            if r.status_code != 200:
                return []

            soup = BeautifulSoup(r.text, "html.parser")

            # Extract all link texts that look like bando titles
            for a_tag in soup.find_all("a", href=True):
                text = a_tag.get_text(strip=True)
                href = a_tag["href"]
                if len(text) < 15 or len(text) > 300:
                    continue

                # Filter: look for finance/grant keywords
                keywords = ["bando", "contributo", "agevolazione", "finanziamento", "voucher",
                           "incentivo", "fondo", "sussidio", "misura", "opportunità", "aiuto",
                           "sovvenzione", "credito d'imposta", "bonus", "grant"]
                if not any(kw.lower() in text.lower() for kw in keywords):
                    continue

                # Build full URL
                if href.startswith("http"):
                    full_url = href
                elif href.startswith("/"):
                    from urllib.parse import urlparse
                    base = urlparse(source["url"])
                    full_url = f"{base.scheme}://{base.netloc}{href}"
                else:
                    continue

                # Hash for dedup
                content_hash = hashlib.sha256(f"{text}{full_url}".encode()).hexdigest()[:32]

                bandi.append({
                    "titolo": text[:200],
                    "ente": source["nome"],
                    "fonte_url": full_url,
                    "hash_contenuto": content_hash,
                    "regioni": [],
                    "ateco_codes": [],
                    "attivo": True,
                    "scheda_json": None,
                    "testo_completo": text,
                })

    except Exception as e:
        print(f"[SCRAPER] Errore {source['nome']}: {e}")
        _scraper_stats["errors"].append({"fonte": source["nome"], "error": str(e)[:100]})

    return bandi[:10]  # max 10 per fonte


async def run_scraper(notify_users: bool = True):
    """Esegue lo scraper su tutte le fonti e salva i nuovi bandi su Supabase."""
    global _scraper_running, _last_scraper_run, _scraper_stats

    if _scraper_running:
        print("[SCRAPER] Già in esecuzione, skip")
        return

    _scraper_running = True
    _scraper_stats["errors"] = []
    new_count = 0
    print(f"[SCRAPER] Avvio — {len(SCRAPER_SOURCES)} fonti")

    try:
        tasks = [scrape_source(s) for s in SCRAPER_SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_bandi = []
        for r in results:
            if isinstance(r, list):
                all_bandi.extend(r)

        print(f"[SCRAPER] Trovati {len(all_bandi)} bandi candidati")

        # Save new ones to Supabase
        if supabase and all_bandi:
            for bando in all_bandi:
                try:
                    # Check if already exists (by hash)
                    existing = supabase.table("bandi_public") \
                        .select("id") \
                        .eq("hash_contenuto", bando["hash_contenuto"]) \
                        .execute()

                    if not (existing.data or []):
                        # New bando — enrich with AI if possible
                        if GROQ_API_KEY:
                            try:
                                scheda_prompt = (
                                    f"Analizza questo bando italiano e restituisci JSON:\n"
                                    f"Titolo: {bando['titolo']}\nEnte: {bando['ente']}\nURL: {bando['fonte_url']}\n\n"
                                    "Restituisci SOLO JSON valido con campi: titolo, ente, obiettivo, beneficiari, "
                                    "importo_max (numero o null), scadenza (YYYY-MM-DD o null), "
                                    "regioni (array di nomi regione o [] per nazionale), "
                                    "ateco_codes (array o []), contributo_percentuale (numero 0-100 o null)"
                                )
                                scheda_text, _ = await ai_call(scheda_prompt, "auto", json_mode=True, timeout=30)
                                clean = scheda_text.strip().lstrip("```json").lstrip("```").rstrip("```")
                                scheda = json.loads(clean)
                                bando["scheda_json"] = scheda
                                bando["regioni"] = scheda.get("regioni", [])
                                bando["ateco_codes"] = scheda.get("ateco_codes", [])
                                if scheda.get("importo_max"):
                                    bando["importo_max"] = float(scheda["importo_max"]) if scheda["importo_max"] else None
                                if scheda.get("scadenza"):
                                    bando["scadenza"] = scheda["scadenza"]
                            except Exception as e:
                                print(f"[SCRAPER] Enrich AI failed: {e}")

                        # Insert
                        insert_data = {
                            "titolo": bando["titolo"],
                            "ente": bando["ente"],
                            "fonte_url": bando["fonte_url"],
                            "hash_contenuto": bando["hash_contenuto"],
                            "regioni": bando.get("regioni", []),
                            "ateco_codes": bando.get("ateco_codes", []),
                            "attivo": True,
                            "scheda_json": bando.get("scheda_json"),
                            "testo_completo": bando["testo_completo"][:5000],
                        }
                        if bando.get("importo_max"):
                            insert_data["importo_max"] = bando["importo_max"]
                        if bando.get("scadenza"):
                            insert_data["scadenza"] = bando["scadenza"]

                        supabase.table("bandi_public").insert(insert_data).execute()
                        new_count += 1

                        # Notify users with matching alerts
                        if notify_users and new_count <= 20:  # max 20 notifiche per run
                            asyncio.create_task(notify_matching_users(bando))

                except Exception as e:
                    print(f"[SCRAPER] Insert error: {e}")

        _scraper_stats["new_today"] = new_count
        _scraper_stats["total_found"] = len(all_bandi)
        _scraper_stats["last_run"] = datetime.now().isoformat()
        _last_scraper_run = datetime.now()
        print(f"[SCRAPER] Completato — {new_count} nuovi bandi inseriti")

    finally:
        _scraper_running = False


async def notify_matching_users(bando: dict):
    """Notifica via email gli utenti con match score > 70 per un nuovo bando."""
    if not supabase or not RESEND_API_KEY:
        return
    try:
        # Get all users with alerts enabled
        alerts = supabase.table("alert_subscriptions") \
            .select("user_id, email, min_score, regione, ateco") \
            .eq("active", True) \
            .execute()

        for alert in (alerts.data or []):
            try:
                # Quick score heuristic (full pgvector matching requires embedding)
                score = _quick_match_score(bando, alert)
                if score >= (alert.get("min_score") or 0.70):
                    motivazione = f"Il bando '{bando['titolo']}' di {bando['ente']} è compatibile con il tuo profilo aziendale."
                    await send_email(
                        to=alert["email"],
                        subject=f"🎯 Nuovo bando compatibile — {int(score*100)}% match | BA.IA",
                        html=build_alert_email(alert["email"], bando, score, motivazione)
                    )
            except Exception as e:
                print(f"[ALERTS] Notifica fallita per {alert.get('email')}: {e}")
    except Exception as e:
        print(f"[ALERTS] Errore recupero subscriptions: {e}")


def _quick_match_score(bando: dict, alert: dict) -> float:
    """Score veloce basato su regione e ATECO (senza embedding)."""
    score = 0.5  # base

    bando_regioni = [r.lower() for r in (bando.get("regioni") or [])]
    if not bando_regioni:
        score += 0.1  # nazionale → compatibile con tutti

    alert_regione = (alert.get("regione") or "").lower()
    if alert_regione and bando_regioni and alert_regione in bando_regioni:
        score += 0.25

    bando_ateco = [a.lower() for a in (bando.get("ateco_codes") or [])]
    alert_ateco = (alert.get("ateco") or "").lower()
    if alert_ateco and bando_ateco:
        if any(alert_ateco[:4] in ba for ba in bando_ateco):
            score += 0.25

    return min(score, 1.0)


# ══════════════════════════════════════════════════════════════════
# APScheduler — SCRAPER AUTO 24H
# ══════════════════════════════════════════════════════════════════
scheduler = None

@app.on_event("startup")
async def startup_event():
    global scheduler
    if _scheduler_ok:
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            run_scraper,
            "interval",
            hours=24,
            id="scraper_24h",
            kwargs={"notify_users": True},
            next_run_time=datetime.now()  # run once at startup too (after 5s)
        )
        scheduler.start()
        print("[SCHEDULER] Scraper 24h schedulato ✓")

@app.on_event("shutdown")
async def shutdown_event():
    if scheduler and scheduler.running:
        scheduler.shutdown()

# ══════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════
class PromptRequest(BaseModel):
    prompt: str
    provider: str = "auto"
    model: Optional[str] = None
    api_key: Optional[str] = None

class EnrichRequest(BaseModel):
    bando_title: str
    bando_context: str
    fields: list[dict]
    provider: str = "auto"

class CheckoutRequest(BaseModel):
    email: str = ""
    plan: str = "pro"
    user_email: str = ""

class BandiSaveRequest(BaseModel):
    bandi: list[dict]

class AziendeSaveRequest(BaseModel):
    aziende: list[dict]

class MatchRequest(BaseModel):
    azienda: dict
    top_k: int = 10
    provider: str = "auto"

class AlertSubscribeRequest(BaseModel):
    email: str
    min_score: float = 0.70
    regione: Optional[str] = None
    ateco: Optional[str] = None

class ProviderTestRequest(BaseModel):
    provider: str
    api_key: str

class HACCPRequest(BaseModel):
    hotel_name: str
    hotel_citta: str
    hotel_piva: str
    responsabile_haccp: str
    anno: int
    mese: int
    note_generali: str = ""
    data_compilazione: str = ""
    lettori: list[dict] = []

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — ANALISI (richiedono abbonamento)
# ══════════════════════════════════════════════════════════════════
@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    provider: str = "auto",
    user: dict = Depends(require_active_subscription)
):
    if not file.filename.lower().endswith(".pdf"):
        return {"error": "Solo file PDF supportati"}
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        return {"result": await analyze_auto(extract_text_from_pdf(tmp_path), provider)}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/extract-text")
async def extract_text_endpoint(
    file: UploadFile = File(...),
    user: dict = Depends(require_active_subscription)
):
    if not file.filename.lower().endswith(".pdf"):
        return {"error": "Solo file PDF supportati", "text": ""}
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        text = extract_text_from_pdf(tmp_path)
        return {"text": text, "length": len(text)}
    except Exception as e:
        return {"error": str(e), "text": ""}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.post("/prompt")
async def prompt_endpoint(
    req: PromptRequest,
    user: dict = Depends(require_active_subscription)
):
    try:
        is_chat = "_chatMode" in req.prompt
        json_mode = (not is_chat) and "{" in req.prompt and (
            "sezione" in req.prompt.lower() or
            "compila ESATTAMENTE" in req.prompt or
            "schema JSON" in req.prompt
        )
        result, provider_used = await ai_call(
            req.prompt,
            provider=req.provider,
            model=req.model,
            api_key=req.api_key,
            json_mode=json_mode
        )
        return {"result": result, "provider": provider_used}
    except Exception as e:
        return {"error": str(e)}


@app.post("/enrich")
async def enrich_fields_endpoint(
    req: EnrichRequest,
    user: dict = Depends(require_active_subscription)
):
    if not req.fields:
        return {"campi": []}
    fields_text = "\n".join(
        f'  - "{f.get("label","?")}" (sezione: {f.get("section","?")})'
        for f in req.fields
    )
    prompt = (
        "Sei un esperto senior di finanza agevolata e normativa italiana. "
        "Per il bando indicato, i seguenti campi NON sono stati estratti dal testo PDF. "
        "Devi ricercarli usando le tue conoscenze approfondite su:\n"
        "  • Gazzetta Ufficiale (GU), Ministeri (MIMIT, MEF, MUR, MIPAAF), Invitalia, CDP, Regioni\n"
        "  • Normative UE: GBER (Reg. 651/2014), de minimis (Reg. 1407/2013), PSC, PNRR\n"
        "  • Prassi consolidate per bandi simili\n\n"
        f'BANDO: "{req.bando_title}"\n'
        f"CONTESTO DISPONIBILE: {req.bando_context[:900]}\n\n"
        f"CAMPI DA RICERCARE:\n{fields_text}\n\n"
        "ISTRUZIONI:\n"
        "  1. Per ogni campo fornisci il valore più accurato possibile.\n"
        "  2. Se non puoi determinarlo con sicurezza, metti null come valore.\n"
        "  3. Indica sempre la fonte ufficiale (nome e URL se disponibile).\n"
        "  4. Confidenza: 'alta'=fonte certa/verificabile, 'media'=dedotto da prassi, 'bassa'=stima.\n\n"
        "RISPOSTA: JSON valido ESCLUSIVAMENTE:\n"
        '{"campi":[{"label":"nome campo","valore":"valore o null",'
        '"fonte_nome":"es. GU n.123/2024","fonte_url":"https://...",'
        '"confidenza":"alta|media|bassa","nota":"breve nota"}]}'
    )
    try:
        result, _ = await ai_call(prompt, req.provider, json_mode=True, timeout=150)
        if isinstance(result, str):
            clean = result.strip().lstrip("```json").lstrip("```").rstrip("```")
            parsed = json.loads(clean.strip())
        else:
            parsed = result
        return parsed
    except Exception as exc:
        return {"error": str(exc), "campi": []}

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — MATCHING TOP 3 (pgvector + AI)
# ══════════════════════════════════════════════════════════════════
@app.post("/match/top3")
async def match_top3(
    req: MatchRequest,
    user: dict = Depends(require_active_subscription)
):
    """
    Restituisce i Top 3 bandi più compatibili con l'azienda.
    Strategia: pgvector cosine-similarity → AI motivation generation.
    Fallback: keyword matching su bandi_public se pgvector non disponibile.
    """
    azienda = req.azienda
    top_k = min(req.top_k, 20)

    candidates = []

    # ── STEP 1: get candidates from Supabase bandi_public ────────
    if supabase:
        try:
            # Try pgvector if embedding column exists
            # First get all active bandi (fallback if no vector function)
            query = supabase.table("bandi_public") \
                .select("id, titolo, ente, scheda_json, regioni, ateco_codes, importo_max, scadenza, fonte_url") \
                .eq("attivo", True)

            # Filter by region if specified
            regione = azienda.get("regione", "")
            if regione:
                # Get both regional and national (empty regioni)
                res_reg = query.execute()
                all_bandi = res_reg.data or []
                # Filter: bandi with matching region OR national (empty regioni)
                candidates_raw = [
                    b for b in all_bandi
                    if not b.get("regioni") or regione in (b.get("regioni") or [])
                ]
            else:
                res = query.limit(100).execute()
                candidates_raw = res.data or []

            print(f"[MATCH] {len(candidates_raw)} bandi candidati da Supabase")

            for b in candidates_raw[:50]:  # limit processing
                score = _score_bando_azienda(b, azienda)
                candidates.append({"bando": b, "score": score})

        except Exception as e:
            print(f"[MATCH] Supabase error: {e}")

    # ── STEP 2: If no candidates, use AI-only matching ────────────
    if not candidates:
        # Generate synthetic bandi based on company profile
        ateco = azienda.get("ateco", "")
        regione = azienda.get("regione", "")
        investimento = azienda.get("investimento", 0)
        dipendenti = azienda.get("dipendenti", 0)

        size_label = "micro" if dipendenti < 10 else "piccola" if dipendenti < 50 else "media" if dipendenti < 250 else "grande"

        prompt = (
            "Sei un esperto di finanza agevolata italiana. "
            f"L'azienda ha queste caratteristiche:\n"
            f"- ATECO: {ateco}\n"
            f"- Regione: {regione}\n"
            f"- Dimensione: {size_label} impresa ({dipendenti} dipendenti)\n"
            f"- Investimento pianificato: €{investimento:,.0f}\n"
            f"- Settore: {azienda.get('settore', 'non specificato')}\n\n"
            "Identifica i 3 bandi/misure attualmente più adatti in Italia (2024-2025). "
            "Per ogni bando restituisci JSON con: titolo, ente, obiettivo, importo_max, "
            "scadenza (YYYY-MM-DD o null), fonte_url, score (0.0-1.0), motivazione (2-3 frasi).\n"
            "RISPOSTA: solo JSON array [{...},{...},{...}]"
        )

        try:
            result, _ = await ai_call(prompt, req.provider, json_mode=False, timeout=60)
            # Parse JSON from response
            json_match = re.search(r'\[.*\]', result, re.DOTALL)
            if json_match:
                ai_bandi = json.loads(json_match.group())
                for b in ai_bandi[:3]:
                    candidates.append({
                        "bando": {
                            "id": f"ai_{hashlib.md5(b.get('titolo','').encode()).hexdigest()[:8]}",
                            "titolo": b.get("titolo", ""),
                            "ente": b.get("ente", ""),
                            "scheda_json": b,
                            "regioni": [regione] if regione else [],
                            "fonte_url": b.get("fonte_url", ""),
                            "importo_max": b.get("importo_max"),
                            "scadenza": b.get("scadenza"),
                        },
                        "score": float(b.get("score", 0.75)),
                        "motivazione": b.get("motivazione", "")
                    })
        except Exception as e:
            print(f"[MATCH] AI fallback error: {e}")
            return {"error": str(e), "top3": []}

    # ── STEP 3: Sort and take top 3 ───────────────────────────────
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top3_raw = candidates[:3]

    # ── STEP 4: Generate AI motivations for top 3 ─────────────────
    top3 = []
    for item in top3_raw:
        bando = item["bando"]
        score = item["score"]
        motivazione = item.get("motivazione", "")

        if not motivazione:
            try:
                mot_prompt = (
                    f"Spiega in 2-3 frasi concise perché il bando '{bando['titolo']}' "
                    f"di {bando.get('ente','')} è adatto a questa azienda:\n"
                    f"- ATECO: {azienda.get('ateco','')}\n"
                    f"- Regione: {azienda.get('regione','')}\n"
                    f"- Investimento: €{azienda.get('investimento',0):,.0f}\n"
                    f"- Dipendenti: {azienda.get('dipendenti',0)}\n"
                    "Sii specifico e pratico. Solo le frasi, zero intestazioni."
                )
                motivazione, _ = await ai_call(mot_prompt, req.provider, timeout=30)
            except Exception:
                motivazione = f"Il bando è compatibile con il profilo aziendale (score: {int(score*100)}%)."

        top3.append({
            "bando_id": bando.get("id"),
            "titolo": bando.get("titolo", ""),
            "ente": bando.get("ente", ""),
            "score": round(score, 3),
            "score_pct": int(score * 100),
            "motivazione": motivazione,
            "importo_max": bando.get("importo_max"),
            "scadenza": str(bando.get("scadenza", "")) if bando.get("scadenza") else None,
            "fonte_url": bando.get("fonte_url", ""),
            "scheda_json": bando.get("scheda_json"),
        })

    # ── STEP 5: Save results to Supabase match_results ────────────
    if supabase and top3:
        try:
            rows = [
                {
                    "user_id": user["sub"],
                    "bando_id": t["bando_id"] if not str(t["bando_id"]).startswith("ai_") else None,
                    "score": t["score"],
                    "motivazione": t["motivazione"],
                    "rank": i + 1,
                    "viewed": False,
                }
                for i, t in enumerate(top3)
            ]
            supabase.table("match_results").insert([r for r in rows if r["bando_id"]]).execute()
        except Exception as e:
            print(f"[MATCH] Save error: {e}")

    return {
        "top3": top3,
        "total_candidates": len(candidates),
        "azienda": azienda.get("name", ""),
        "generated_at": datetime.now().isoformat(),
    }


def _score_bando_azienda(bando: dict, azienda: dict) -> float:
    """Scoring euristico bando × azienda (0.0 → 1.0)."""
    score = 0.40  # base

    # Region match
    bando_regioni = [r.lower() for r in (bando.get("regioni") or [])]
    azienda_regione = (azienda.get("regione") or "").lower()
    if not bando_regioni:
        score += 0.10  # nazionale
    elif azienda_regione and any(azienda_regione in br or br in azienda_regione for br in bando_regioni):
        score += 0.20

    # ATECO match
    bando_ateco = [a.lower() for a in (bando.get("ateco_codes") or [])]
    azienda_ateco = (azienda.get("ateco") or "").lower()
    if not bando_ateco:
        score += 0.05  # tutti i settori
    elif azienda_ateco and any(azienda_ateco[:2] in ba for ba in bando_ateco):
        score += 0.20
    elif azienda_ateco and any(azienda_ateco[:4] in ba for ba in bando_ateco):
        score += 0.30

    # Investment fit
    importo_max = bando.get("importo_max")
    investimento = azienda.get("investimento", 0)
    if importo_max and investimento:
        ratio = investimento / importo_max
        if 0.1 <= ratio <= 1.5:
            score += 0.10

    # Active/non-expired
    scadenza = bando.get("scadenza")
    if scadenza:
        try:
            scad_date = datetime.strptime(str(scadenza)[:10], "%Y-%m-%d").date()
            if scad_date < date.today():
                score -= 0.30
            elif (scad_date - date.today()).days < 30:
                score += 0.05  # urgency bonus
        except Exception:
            pass

    return max(0.0, min(1.0, score))

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — SCRAPER
# ══════════════════════════════════════════════════════════════════
@app.post("/scraper/run")
async def scraper_run(
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_active_subscription)
):
    """Avvia manualmente lo scraper in background."""
    if _scraper_running:
        return {"ok": False, "message": "Scraper già in esecuzione", "stats": _scraper_stats}
    background_tasks.add_task(run_scraper, True)
    return {"ok": True, "message": "Scraper avviato in background", "sources": len(SCRAPER_SOURCES)}


@app.get("/scraper/status")
async def scraper_status(user: dict = Depends(get_current_user)):
    """Stato corrente dello scraper."""
    return {
        "running": _scraper_running,
        "last_run": _last_scraper_run.isoformat() if _last_scraper_run else None,
        "stats": _scraper_stats,
        "sources": len(SCRAPER_SOURCES),
        "scheduled": _scheduler_ok,
    }


@app.get("/bandi/public")
async def list_public_bandi(
    regione: Optional[str] = None,
    limit: int = 50,
    user: dict = Depends(get_current_user)
):
    """Lista bandi pubblici dal database (scraper)."""
    if not supabase:
        return {"bandi": [], "total": 0}
    try:
        query = supabase.table("bandi_public") \
            .select("id, titolo, ente, regioni, ateco_codes, importo_max, scadenza, fonte_url, scheda_json") \
            .eq("attivo", True) \
            .order("created_at", desc=True) \
            .limit(limit)
        res = query.execute()
        bandi = res.data or []
        if regione:
            bandi = [b for b in bandi if not b.get("regioni") or regione in (b.get("regioni") or [])]
        return {"bandi": bandi, "total": len(bandi)}
    except Exception as e:
        return {"bandi": [], "total": 0, "error": str(e)}

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — ALERT EMAIL SUBSCRIPTIONS
# ══════════════════════════════════════════════════════════════════
@app.post("/alerts/subscribe")
async def subscribe_alerts(
    req: AlertSubscribeRequest,
    user: dict = Depends(get_current_user)
):
    """Iscrive l'utente agli alert email per nuovi bandi compatibili."""
    if not supabase:
        return {"ok": True, "note": "Alert locali — Supabase non configurato"}
    try:
        supabase.table("alert_subscriptions").upsert({
            "user_id": user["sub"],
            "email": req.email,
            "min_score": req.min_score,
            "regione": req.regione,
            "ateco": req.ateco,
            "active": True,
        }).execute()

        # Send confirmation email
        await send_email(
            to=req.email,
            subject="✅ Alert BA.IA attivati — riceverai notifiche sui bandi compatibili",
            html=f"""
<div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:24px;">
  <h2>🎯 Alert BA.IA attivati!</h2>
  <p>Riceverai un'email ogni volta che un nuovo bando con compatibilità ≥{int(req.min_score*100)}% viene rilevato per la tua azienda.</p>
  <p><strong>Regione:</strong> {req.regione or 'Tutte'}<br>
  <strong>ATECO:</strong> {req.ateco or 'Tutti'}</p>
  <a href="{FRONTEND_URL}" style="background:#c69229;color:white;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block;margin-top:12px;">Apri BA.IA →</a>
</div>
"""
        )
        return {"ok": True, "email": req.email, "min_score": req.min_score}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/alerts/unsubscribe")
async def unsubscribe_alerts(user: dict = Depends(get_current_user)):
    """Disiscrive l'utente dagli alert."""
    if not supabase:
        return {"ok": True}
    try:
        supabase.table("alert_subscriptions").update({"active": False}).eq("user_id", user["sub"]).execute()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/alerts/status")
async def alerts_status(user: dict = Depends(get_current_user)):
    """Stato degli alert per l'utente corrente."""
    if not supabase:
        return {"active": False, "note": "Supabase non configurato"}
    try:
        res = supabase.table("alert_subscriptions") \
            .select("email, min_score, regione, ateco, active") \
            .eq("user_id", user["sub"]) \
            .execute()
        data = (res.data or [{}])[0] if res.data else {}
        return {"active": data.get("active", False), **data}
    except Exception as e:
        return {"active": False, "error": str(e)}

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — MULTI-PROVIDER TEST
# ══════════════════════════════════════════════════════════════════
@app.post("/provider/test")
async def test_provider(
    req: ProviderTestRequest,
    user: dict = Depends(get_current_user)
):
    """Testa una chiave API per un provider AI."""
    try:
        result, used = await ai_call(
            "Rispondi solo 'OK' in italiano.",
            provider=req.provider,
            api_key=req.api_key,
            timeout=15
        )
        return {"ok": True, "provider": used, "response": result[:100]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/providers")
async def list_providers(user: dict = Depends(get_current_user)):
    """Lista dei provider AI disponibili con status."""
    return {
        "providers": [
            {"id": "groq",      "name": "Groq (LLaMA)",   "configured": bool(GROQ_API_KEY),      "model": GROQ_MODEL,            "cost": "€0.01/1M tok"},
            {"id": "anthropic", "name": "Claude",          "configured": bool(ANTHROPIC_API_KEY), "model": "claude-haiku-4-5",     "cost": "€0.25/1M tok"},
            {"id": "openai",    "name": "OpenAI (GPT)",    "configured": bool(OPENAI_API_KEY),    "model": "gpt-4o-mini",          "cost": "€0.15/1M tok"},
            {"id": "gemini",    "name": "Google Gemini",   "configured": bool(GEMINI_API_KEY),    "model": "gemini-1.5-flash",     "cost": "€1.25/1M tok"},
            {"id": "mistral",   "name": "Mistral",         "configured": bool(MISTRAL_API_KEY),   "model": "mistral-small-latest", "cost": "€2.00/1M tok"},
        ]
    }

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — ABBONAMENTO / STRIPE (multi-piano)
# ══════════════════════════════════════════════════════════════════
PLAN_PRICES = {
    "base":     {"price_id_env": STRIPE_PRICE_BASE,     "amount": 29,  "name": "Piano Base"},
    "pro":      {"price_id_env": STRIPE_PRICE_PRO,      "amount": 79,  "name": "Piano Pro"},
    "business": {"price_id_env": STRIPE_PRICE_BUSINESS, "amount": 199, "name": "Piano Business"},
}

@app.get("/subscription/status")
async def get_subscription_status(user: dict = Depends(get_current_user)):
    if LOCAL_MODE:
        return {"active": True, "plan": "pro", "mode": "local"}
    if not supabase:
        return {"active": True, "plan": "unknown"}
    try:
        res = supabase.table("profiles").select("plan, stripe_customer_id, trial_ends_at").eq("id", user["sub"]).single().execute()
        profile = res.data or {}
        plan = profile.get("plan", "free")
        return {"active": plan in ("pro", "trial", "base", "business"), "plan": plan, "stripe_customer_id": profile.get("stripe_customer_id")}
    except Exception as e:
        return {"active": False, "plan": "free", "error": str(e)}


@app.post("/checkout")
async def create_checkout_session(
    req: CheckoutRequest,
    user: dict = Depends(get_current_user)
):
    if not _stripe_ok or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe non configurato")

    plan = req.plan or "pro"
    plan_info = PLAN_PRICES.get(plan, PLAN_PRICES["pro"])
    price_id = plan_info["price_id_env"]

    # Fallback: use generic STRIPE_PRICE_ID if specific not set
    if not price_id:
        price_id = os.environ.get("STRIPE_PRICE_ID", "")

    if not price_id:
        raise HTTPException(status_code=501, detail=f"STRIPE_PRICE_{plan.upper()} non configurato")

    email = req.user_email or req.email or user.get("email", "")
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            customer_email=email or None,
            client_reference_id=user["sub"],
            success_url=f"{FRONTEND_URL}?checkout=success&plan={plan}&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}?checkout=cancel",
            subscription_data={"trial_period_days": 14 if plan == "business" else 14},
        )
        return {"url": session.url, "plan": plan}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    if not _stripe_ok:
        raise HTTPException(status_code=501, detail="Stripe non disponibile")
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    ev_type = event["type"]
    obj = event["data"]["object"]

    if ev_type == "checkout.session.completed":
        user_id = obj.get("client_reference_id")
        customer_id = obj.get("customer")
        # Determine plan from metadata or URL
        plan = "pro"
        success_url = obj.get("success_url", "")
        if "plan=base" in success_url: plan = "base"
        elif "plan=business" in success_url: plan = "business"

        if user_id and supabase:
            supabase.table("profiles").upsert({"id": user_id, "plan": plan, "stripe_customer_id": customer_id}).execute()
            print(f"[STRIPE] Piano {plan} attivato per {user_id}")

    elif ev_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        customer_id = obj.get("customer")
        if customer_id and supabase:
            supabase.table("profiles").update({"plan": "free"}).eq("stripe_customer_id", customer_id).execute()

    return {"ok": True}

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — PERSISTENZA CLOUD
# ══════════════════════════════════════════════════════════════════
@app.get("/bandi")
async def list_bandi(user: dict = Depends(require_active_subscription)):
    if not supabase: return {"bandi": []}
    try:
        res = supabase.table("bandi").select("id, name, file_name, data, created_at").eq("user_id", user["sub"]).order("created_at", desc=True).execute()
        return {"bandi": res.data or []}
    except Exception as e:
        return {"bandi": [], "error": str(e)}

@app.post("/bandi")
async def save_bandi(req: BandiSaveRequest, user: dict = Depends(require_active_subscription)):
    if not supabase: return {"ok": True, "note": "Modalità locale"}
    try:
        supabase.table("bandi").delete().eq("user_id", user["sub"]).execute()
        if req.bandi:
            rows = [{"user_id": user["sub"], "name": b.get("name",""), "file_name": b.get("fileName",""), "data": b} for b in req.bandi]
            supabase.table("bandi").insert(rows).execute()
        return {"ok": True, "saved": len(req.bandi)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/aziende")
async def list_aziende(user: dict = Depends(require_active_subscription)):
    if not supabase: return {"aziende": []}
    try:
        res = supabase.table("aziende").select("id, data, created_at").eq("user_id", user["sub"]).order("created_at", desc=True).execute()
        return {"aziende": res.data or []}
    except Exception as e:
        return {"aziende": [], "error": str(e)}

@app.post("/aziende")
async def save_aziende(req: AziendeSaveRequest, user: dict = Depends(require_active_subscription)):
    if not supabase: return {"ok": True, "note": "Modalità locale"}
    try:
        supabase.table("aziende").delete().eq("user_id", user["sub"]).execute()
        if req.aziende:
            supabase.table("aziende").insert([{"user_id": user["sub"], "data": a} for a in req.aziende]).execute()
        return {"ok": True, "saved": len(req.aziende)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — HACCP
# ══════════════════════════════════════════════════════════════════
@app.post("/haccp/report")
async def generate_haccp_report(req: HACCPRequest, user: dict = Depends(require_active_subscription)):
    if not _haccp_ok:
        raise HTTPException(status_code=501, detail="Modulo HACCP non disponibile — installa reportlab")
    try:
        readings = [TemperatureReading(data=r.get("data",""), ora=r.get("ora",""), zona=r.get("zona",""), sensor_id=r.get("sensor_id",""), temperatura=float(r.get("temperatura",0)), temp_min=float(r.get("temp_min",0)), temp_max=float(r.get("temp_max",0)), alert=bool(r.get("alert",False)), severity=r.get("severity","ok"), rilevato_da=r.get("rilevato_da","iot"), operatore=r.get("operatore",""), azione_correttiva=r.get("azione_correttiva","")) for r in req.lettori]
        report_data = HACCPReportData(hotel_name=req.hotel_name, hotel_citta=req.hotel_citta, hotel_piva=req.hotel_piva, responsabile_haccp=req.responsabile_haccp, anno=req.anno, mese=req.mese, lettori=readings, note_generali=req.note_generali, data_compilazione=req.data_compilazione)
        pdf_bytes = HACCPPdfGenerator(report_data).generate()
        return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="registro_haccp_{req.anno}_{req.mese:02d}.pdf"'})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/haccp/demo")
async def haccp_demo(user: dict = Depends(require_active_subscription)):
    if not _haccp_ok:
        raise HTTPException(status_code=501, detail="Modulo HACCP non disponibile")
    try:
        from haccp_report import make_demo_data
        pdf_bytes = HACCPPdfGenerator(make_demo_data()).generate()
        return Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="registro_haccp_demo.pdf"'})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ══════════════════════════════════════════════════════════════════
# ENDPOINTS — UTILITY
# ══════════════════════════════════════════════════════════════════
@app.get("/model")
def get_model():
    return {"model": GROQ_MODEL, "provider": "groq", "local_mode": LOCAL_MODE}

@app.get("/")
def home():
    return {
        "status": "ok",
        "version": "3.0-saas",
        "providers": {
            "groq": bool(GROQ_API_KEY),
            "anthropic": bool(ANTHROPIC_API_KEY),
            "openai": bool(OPENAI_API_KEY),
            "gemini": bool(GEMINI_API_KEY),
            "mistral": bool(MISTRAL_API_KEY),
        },
        "local_mode": LOCAL_MODE,
        "supabase": bool(supabase),
        "stripe": _stripe_ok and bool(STRIPE_SECRET_KEY),
        "resend": bool(RESEND_API_KEY),
        "scraper": {"running": _scraper_running, "sources": len(SCRAPER_SOURCES), "scheduled": _scheduler_ok},
    }
