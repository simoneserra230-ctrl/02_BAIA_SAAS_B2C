# ═══════════════════════════════════════════════════════════════════
#  AI Analisi Bandi — Backend SaaS  (FastAPI + Groq + Supabase + Stripe)
#  Version: 2.1 SaaS — FIX: dotenv, modello, limiti
# ═══════════════════════════════════════════════════════════════════
from fastapi import FastAPI, UploadFile, File, Header, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
import httpx, tempfile, os, asyncio, re, json, sys
from pypdf import PdfReader
from typing import Optional

# ── Import modulo HACCP (richiede reportlab) ─────────────────────────────
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))
    from haccp_report import HACCPPdfGenerator, HACCPReportData, TemperatureReading
    _haccp_ok = True
except ImportError as _e:
    _haccp_ok = False
    print(f"[STARTUP] HACCP non disponibile: {_e} — installa reportlab")

# ── Carica .env PRIMA di leggere os.environ ──────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
    print("[STARTUP] .env caricato correttamente")
except ImportError:
    print("[STARTUP] python-dotenv non installato — uso variabili di sistema")

# ── Dipendenze opzionali (installate solo in produzione cloud) ──
try:
    import stripe
    _stripe_ok = True
except ImportError:
    _stripe_ok = False

try:
    from supabase import create_client, Client as SupabaseClient
    _supabase_ok = True
except ImportError:
    _supabase_ok = False

try:
    from jose import jwt as jose_jwt, JWTError
    _jose_ok = True
except ImportError:
    _jose_ok = False

# ══════════════════════════════════════════════════════════════════
# CONFIG  — tutte le variabili d'ambiente
# ══════════════════════════════════════════════════════════════════
GROQ_API_KEY          = os.environ.get("GROQ_API_KEY", "")
GROQ_URL              = "https://api.groq.com/openai/v1/chat/completions"
MODEL                 = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
CHUNK_SIZE            = 60_000
SIMPLE_LIMIT          = 320_000

SUPABASE_URL          = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY          = os.environ.get("SUPABASE_KEY", "")          # service_role key (backend only)
SUPABASE_JWT_SECRET   = os.environ.get("SUPABASE_JWT_SECRET", "")   # Settings → API → JWT Secret

STRIPE_SECRET_KEY     = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID       = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

FRONTEND_URL          = os.environ.get("FRONTEND_URL", "http://localhost:8080")

# Modalità locale (no Supabase, no Stripe) — utile per sviluppo
LOCAL_MODE            = not bool(SUPABASE_URL and SUPABASE_KEY)

# ── Validazione configurazione al boot ──
if not GROQ_API_KEY:
    print("[WARNING] GROQ_API_KEY non impostata — le analisi AI falliranno!")
else:
    print(f"[STARTUP] Groq configurato ✓  modello={MODEL}")
    print(f"[STARTUP] Modalità: {'locale (no Supabase)' if LOCAL_MODE else 'cloud (Supabase attivo)'}")

# ── Init client opzionali ──
supabase: Optional[object] = None
if _supabase_ok and SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

if _stripe_ok and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# ══════════════════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════════════════
app = FastAPI(title="AI Analisi Bandi SaaS", version="2.0")

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
    """
    Verifica il JWT Supabase dal header Authorization: Bearer <token>.
    In LOCAL_MODE, ritorna un utente fittizio per sviluppo locale.
    """
    if LOCAL_MODE:
        return {"sub": "local-user-001", "email": "local@dev", "plan": "pro"}

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token di autenticazione mancante")

    token = authorization.split(" ", 1)[1]

    if _jose_ok and SUPABASE_JWT_SECRET:
        try:
            payload = jose_jwt.decode(
                token, SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False}
            )
            return payload
        except JWTError as e:
            raise HTTPException(status_code=401, detail=f"Token non valido: {e}")

    # Fallback: verifica tramite Supabase API (senza jose)
    if supabase:
        try:
            res = supabase.auth.get_user(token)
            if res.user:
                return {"sub": res.user.id, "email": res.user.email}
        except Exception as e:
            raise HTTPException(status_code=401, detail=f"Autenticazione fallita: {e}")

    raise HTTPException(status_code=401, detail="Impossibile verificare il token: configura SUPABASE_JWT_SECRET")


async def require_active_subscription(user: dict = Depends(get_current_user)) -> dict:
    """
    Verifica che l'utente abbia un abbonamento attivo.
    In LOCAL_MODE, sempre OK.
    """
    if LOCAL_MODE:
        return user

    if not supabase:
        return user  # senza DB non possiamo verificare, permetti

    try:
        res = supabase.table("profiles") \
            .select("plan") \
            .eq("id", user["sub"]) \
            .single() \
            .execute()
        plan = (res.data or {}).get("plan", "free")
        if plan not in ("pro", "trial"):
            raise HTTPException(status_code=402, detail="Abbonamento richiesto — attiva il piano Pro")
    except HTTPException:
        raise
    except Exception:
        pass  # se il check fallisce, non bloccare

    return user


# ══════════════════════════════════════════════════════════════════
# GROQ  —  funzioni di analisi (invariate da v1, solo estratte)
# ══════════════════════════════════════════════════════════════════
def extract_text_from_pdf(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    return "".join(page.extract_text() or "" for page in reader.pages)


async def groq_call(
    prompt: str,
    json_mode: bool = False,
    timeout: int = 120,
    _retry: int = 0
) -> tuple[str, float]:
    """
    Chiama Groq con retry automatico su rate-limit 429.
    Ritorna (testo_risposta, secondi_attesi_per_rate_limit).
    """
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type":  "application/json",
    }
    body: dict = {
        "model":       MODEL,
        "messages":    [{"role": "user", "content": prompt}],
        "max_tokens":  8192,
        "temperature": 0.1,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    print(f"[GROQ] {len(prompt)} char | json={json_mode}", end=" → ", flush=True)

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(GROQ_URL, headers=headers, json=body)

        if r.status_code == 429 and _retry < 3:
            err_msg = r.json().get("error", {}).get("message", "")
            m = re.search(r"try again in ([\d.]+)s", err_msg)
            wait = float(m.group(1)) + 1.0 if m else float(r.headers.get("retry-after", 20))
            print(f"rate limit → attendo {wait:.1f}s (tentativo {_retry+1}/3)")
            await asyncio.sleep(wait)
            result, prev_wait = await groq_call(prompt, json_mode, timeout, _retry + 1)
            return result, wait + prev_wait

        if r.status_code != 200:
            detail = r.json().get("error", {}).get("message", r.text)
            raise RuntimeError(f"Groq error {r.status_code}: {detail}")

        result = r.json()["choices"][0]["message"]["content"]
        print(f"{len(result)} char OK")
        return result, 0.0


async def analyze_simple(text: str) -> str:
    result, _ = await groq_call(
        "Sei un esperto di finanza agevolata italiana. "
        "Analizza questo bando e restituisci:\n"
        "- Obiettivo principale\n- Beneficiari ammessi\n- Requisiti chiave\n"
        "- Scadenze importanti\n- Opportunità strategiche\n\n"
        f"Testo:\n{text[:SIMPLE_LIMIT]}"
    )
    return result


async def analyze_in_chunks(text: str) -> str:
    chunks = [text[i:i+CHUNK_SIZE] for i in range(0, len(text), CHUNK_SIZE)]
    parts  = []
    for i, chunk in enumerate(chunks):
        result, _ = await groq_call(
            f"Parte {i+1}/{len(chunks)} di un bando. "
            f"Estrai Obiettivo, Beneficiari, Requisiti, Scadenze, Opportunità.\n\n{chunk}"
        )
        parts.append(result)
        if i < len(chunks) - 1:
            await asyncio.sleep(3)
    result, _ = await groq_call(
        "Crea un'analisi finale completa da questi estratti:\n\n" +
        "".join(f"--- Parte {i+1} ---\n{r}\n" for i, r in enumerate(parts)),
        timeout=180
    )
    return result


async def analyze_auto(text: str) -> str:
    if len(text) <= SIMPLE_LIMIT:
        return await analyze_simple(text)
    return await analyze_in_chunks(text)


# ══════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════
class PromptRequest(BaseModel):
    prompt: str
    _chatMode: bool = False


class EnrichRequest(BaseModel):
    bando_title: str
    bando_context: str
    fields: list[dict]


class CheckoutRequest(BaseModel):
    email: str


class BandiSaveRequest(BaseModel):
    bandi: list[dict]


class AziendeSaveRequest(BaseModel):
    aziende: list[dict]


# ══════════════════════════════════════════════════════════════════
# ENDPOINTS  —  ANALISI  (richiedono abbonamento attivo)
# ══════════════════════════════════════════════════════════════════
@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    user: dict = Depends(require_active_subscription)
):
    if not file.filename.lower().endswith(".pdf"):
        return {"error": "Solo file PDF supportati"}
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name
        return {"result": await analyze_auto(extract_text_from_pdf(tmp_path))}
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
        is_chat = "_chatMode" in req.prompt or (
            "Rispondi in italiano" in req.prompt and
            "Cita dati precisi" in req.prompt
        )
        json_mode = (not is_chat) and "{" in req.prompt and (
            "sezione" in req.prompt.lower() or
            "compila ESATTAMENTE" in req.prompt or
            "schema JSON" in req.prompt
        )
        result, wait_secs = await groq_call(req.prompt, json_mode=json_mode)
        response: dict = {"result": result}
        if wait_secs > 0:
            response["waitSecs"] = round(wait_secs, 1)
        return response
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
        f'  - "{f.get("label","?")}"\t(sezione: {f.get("section","?")})'
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
        "  4. Confidenza: 'alta'=fonte certa/verificabile, 'media'=dedotto da prassi, 'bassa'=stima.\n"
        "  5. Non inventare — preferisci null a dati non verificabili.\n\n"
        "RISPOSTA: JSON valido ESCLUSIVAMENTE, zero testo aggiuntivo, zero backtick:\n"
        '{"campi":[{"label":"nome esatto campo","valore":"valore o null",'
        '"fonte_nome":"es. GU n.123/2024 – MIMIT","fonte_url":"https://...",'
        '"confidenza":"alta|media|bassa","nota":"breve nota metodologica"}]}'
    )

    try:
        result, _ = await groq_call(prompt, json_mode=True, timeout=150)
        if isinstance(result, str):
            clean = result.strip()
            if clean.startswith("```"):
                clean = clean.split("```")[1]
                if clean.startswith("json"):
                    clean = clean[4:]
            parsed = json.loads(clean.strip())
        else:
            parsed = result
        return parsed
    except Exception as exc:
        return {"error": str(exc), "campi": []}


# ══════════════════════════════════════════════════════════════════
# ENDPOINTS  —  ABBONAMENTO / STRIPE
# ══════════════════════════════════════════════════════════════════
@app.get("/subscription/status")
async def get_subscription_status(user: dict = Depends(get_current_user)):
    """Ritorna il piano attuale dell'utente."""
    if LOCAL_MODE:
        return {"active": True, "plan": "pro", "mode": "local"}

    if not supabase:
        return {"active": True, "plan": "unknown"}

    try:
        res = supabase.table("profiles") \
            .select("plan, stripe_customer_id, trial_ends_at") \
            .eq("id", user["sub"]) \
            .single() \
            .execute()
        profile = res.data or {}
        plan    = profile.get("plan", "free")
        active  = plan in ("pro", "trial")
        return {
            "active": active,
            "plan": plan,
            "stripe_customer_id": profile.get("stripe_customer_id"),
        }
    except Exception as e:
        return {"active": False, "plan": "free", "error": str(e)}


@app.post("/checkout")
async def create_checkout_session(
    req: CheckoutRequest,
    user: dict = Depends(get_current_user)
):
    """Crea una Stripe Checkout Session per l'abbonamento mensile."""
    if not _stripe_ok or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=501, detail="Stripe non configurato")

    if not STRIPE_PRICE_ID:
        raise HTTPException(status_code=501, detail="STRIPE_PRICE_ID non configurato")

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="subscription",
            customer_email=req.email,
            client_reference_id=user["sub"],  # user_id Supabase
            success_url=f"{FRONTEND_URL}?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}?checkout=cancel",
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    """Gestisce gli eventi Stripe (pagamento, cancellazione abbonamento)."""
    if not _stripe_ok:
        raise HTTPException(status_code=501, detail="Stripe non disponibile")

    payload    = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail=f"Firma webhook non valida: {e}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    ev_type = event["type"]
    obj     = event["data"]["object"]

    if ev_type == "checkout.session.completed":
        user_id     = obj.get("client_reference_id")
        customer_id = obj.get("customer")
        if user_id and supabase:
            supabase.table("profiles").upsert({
                "id": user_id,
                "plan": "pro",
                "stripe_customer_id": customer_id,
            }).execute()
            print(f"[STRIPE] Abbonamento attivato per utente {user_id}")

    elif ev_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        customer_id = obj.get("customer")
        if customer_id and supabase:
            supabase.table("profiles") \
                .update({"plan": "free"}) \
                .eq("stripe_customer_id", customer_id) \
                .execute()
            print(f"[STRIPE] Abbonamento cancellato per customer {customer_id}")

    elif ev_type == "invoice.payment_failed":
        customer_id = obj.get("customer")
        print(f"[STRIPE] Pagamento fallito per customer {customer_id}")
        # Opzionale: invia email di avviso tramite Supabase Edge Function

    return {"ok": True}


# ══════════════════════════════════════════════════════════════════
# ENDPOINTS  —  PERSISTENZA CLOUD  (bandi e aziende su Supabase)
# ══════════════════════════════════════════════════════════════════
@app.get("/bandi")
async def list_bandi(user: dict = Depends(require_active_subscription)):
    """Carica tutti i bandi dell'utente da Supabase."""
    if not supabase:
        return {"bandi": []}
    try:
        res = supabase.table("bandi") \
            .select("id, name, file_name, data, created_at") \
            .eq("user_id", user["sub"]) \
            .order("created_at", desc=True) \
            .execute()
        return {"bandi": res.data or []}
    except Exception as e:
        return {"bandi": [], "error": str(e)}


@app.post("/bandi")
async def save_bandi(
    req: BandiSaveRequest,
    user: dict = Depends(require_active_subscription)
):
    """
    Salva (sostituisce) tutti i bandi dell'utente.
    Strategia: delete-and-insert per semplicità.
    """
    if not supabase:
        return {"ok": True, "note": "Modalità locale — nessun salvataggio cloud"}

    try:
        # 1. Elimina i bandi esistenti per questo utente
        supabase.table("bandi") \
            .delete() \
            .eq("user_id", user["sub"]) \
            .execute()

        # 2. Inserisce i nuovi
        if req.bandi:
            rows = [
                {
                    "user_id":   user["sub"],
                    "name":      b.get("name", ""),
                    "file_name": b.get("fileName", ""),
                    "data":      b,   # intero oggetto bando come JSONB
                }
                for b in req.bandi
            ]
            supabase.table("bandi").insert(rows).execute()

        return {"ok": True, "saved": len(req.bandi)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/aziende")
async def list_aziende(user: dict = Depends(require_active_subscription)):
    """Carica tutti i profili aziendali dell'utente da Supabase."""
    if not supabase:
        return {"aziende": []}
    try:
        res = supabase.table("aziende") \
            .select("id, data, created_at") \
            .eq("user_id", user["sub"]) \
            .order("created_at", desc=True) \
            .execute()
        return {"aziende": res.data or []}
    except Exception as e:
        return {"aziende": [], "error": str(e)}


@app.post("/aziende")
async def save_aziende(
    req: AziendeSaveRequest,
    user: dict = Depends(require_active_subscription)
):
    """Salva (sostituisce) tutti i profili aziendali dell'utente."""
    if not supabase:
        return {"ok": True, "note": "Modalità locale — nessun salvataggio cloud"}

    try:
        supabase.table("aziende") \
            .delete() \
            .eq("user_id", user["sub"]) \
            .execute()

        if req.aziende:
            rows = [
                {"user_id": user["sub"], "data": a}
                for a in req.aziende
            ]
            supabase.table("aziende").insert(rows).execute()

        return {"ok": True, "saved": len(req.aziende)}
    except Exception as e:
        return {"ok": False, "error": str(e)}



# ══════════════════════════════════════════════════════════════════
# ENDPOINTS  —  HACCP  (Registro mensile PDF)
# ══════════════════════════════════════════════════════════════════

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


@app.post("/haccp/report")
async def generate_haccp_report(
    req: HACCPRequest,
    user: dict = Depends(require_active_subscription)
):
    """
    Genera il Registro HACCP mensile in formato PDF.
    Restituisce il PDF come download diretto.
    """
    if not _haccp_ok:
        raise HTTPException(
            status_code=501,
            detail="Modulo HACCP non disponibile — installa reportlab: pip install reportlab>=4.2.0"
        )
    try:
        readings = [
            TemperatureReading(
                data=r.get("data", ""),
                ora=r.get("ora", ""),
                zona=r.get("zona", ""),
                sensor_id=r.get("sensor_id", ""),
                temperatura=float(r.get("temperatura", 0)),
                temp_min=float(r.get("temp_min", 0)),
                temp_max=float(r.get("temp_max", 0)),
                alert=bool(r.get("alert", False)),
                severity=r.get("severity", "ok"),
                rilevato_da=r.get("rilevato_da", "iot"),
                operatore=r.get("operatore", ""),
                azione_correttiva=r.get("azione_correttiva", ""),
            )
            for r in req.lettori
        ]
        report_data = HACCPReportData(
            hotel_name=req.hotel_name,
            hotel_citta=req.hotel_citta,
            hotel_piva=req.hotel_piva,
            responsabile_haccp=req.responsabile_haccp,
            anno=req.anno,
            mese=req.mese,
            lettori=readings,
            note_generali=req.note_generali,
            data_compilazione=req.data_compilazione,
        )
        pdf_bytes = HACCPPdfGenerator(report_data).generate()
        filename = f"registro_haccp_{req.anno}_{req.mese:02d}_{req.hotel_name.replace(' ', '_')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore generazione HACCP: {e}")


@app.get("/haccp/demo")
async def haccp_demo(user: dict = Depends(require_active_subscription)):
    """Genera un PDF HACCP di demo con dati sintetici."""
    if not _haccp_ok:
        raise HTTPException(status_code=501, detail="Modulo HACCP non disponibile — installa reportlab")
    try:
        from haccp_report import make_demo_data
        pdf_bytes = HACCPPdfGenerator(make_demo_data()).generate()
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="registro_haccp_demo.pdf"'},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ══════════════════════════════════════════════════════════════════
# ENDPOINTS  —  UTILITY
# ══════════════════════════════════════════════════════════════════
@app.get("/model")
def get_model():
    return {
        "model":      MODEL,
        "provider":   "groq",
        "local_mode": LOCAL_MODE,
    }


@app.get("/")
def home():
    return {
        "status":     "ok",
        "version":    "2.1-saas",
        "provider":   "groq",
        "model":      MODEL,
        "local_mode": LOCAL_MODE,
        "supabase":   bool(supabase),
        "stripe":     _stripe_ok and bool(STRIPE_SECRET_KEY),
    }
