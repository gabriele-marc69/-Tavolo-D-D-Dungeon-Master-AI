"""
Backend webchat: pilota Chromium via Playwright per parlare con una
pagina chat IA pubblica (default: DeepSeek). Tutta l'interazione
Playwright gira in un singolo worker thread dedicato.

Bugfix rispetto alla versione precedente:
- Estrazione testo via page.evaluate() JS: prende l'innerText COMPLETO
  del contenitore dell'ultima risposta assistente, non il singolo
  paragrafo finale.
- Briefing iniziale opzionale via send_briefing().
"""
from __future__ import annotations

import io
import os
import queue
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

# Console Windows default cp1252: print di frecce → / simboli Unicode
# solleva UnicodeEncodeError e abortisce l'invio del prompt a Chromium.
# Riconfiguriamo stdout/stderr a UTF-8 con replacement per blindare
# i log [WEBCHAT] anche quando il modulo è importato fuori da app.py.
try:
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

try:
    from playwright.sync_api import sync_playwright
    _PW_AVAILABLE = True
except ImportError:
    sync_playwright = None
    _PW_AVAILABLE = False


BASE_DIR = os.path.dirname(os.path.dirname(__file__))

# Build PORTABLE (chiavetta USB): se PLAYWRIGHT_BROWSERS_PATH non è già
# impostato dal launcher, usa la cartella ms-playwright accanto all'app
# (../ms-playwright rispetto a questo file). Così il Chromium della chiavetta
# parte anche se l'utente avvia app.py senza passare da avvia.bat (doppio
# click, py app.py, ecc.) e su un PC pulito senza cache browser di sistema.
if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    _portable_browsers = os.path.join(os.path.dirname(BASE_DIR), "ms-playwright")
    if os.path.isdir(_portable_browsers):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = _portable_browsers
        print(f"[WEBCHAT] PLAYWRIGHT_BROWSERS_PATH (portable) -> {_portable_browsers}",
              flush=True)


@dataclass
class WebchatConfig:
    url: str = "https://chat.deepseek.com/"
    name: str = "DeepSeek Chat"
    timeout: int = 180        # secondi per la risposta complessiva
    stable_seconds: int = 3   # quanto attendere testo "fermo" prima di considerarlo finito
    poll_interval_ms: int = 1200
    profile_dir: str = field(default_factory=lambda: os.path.join(
        BASE_DIR, ".browser_profiles", "deepseek"))
    # ── Chunking dei prompt lunghi ──────────────────────────────────────
    # Alcune chat web (DeepSeek, Claude, ecc.) rifiutano messaggi oltre
    # ~2000 token. Se il prompt supera `chunk_max_chars` viene spezzato in
    # più blocchi: prima un'istruzione di accumulo, poi i blocchi che
    # raccolgono solo conferme "Ricevuto blocco X", poi un blocco finale
    # che chiude col marker FINE e invita il modello a rispondere.
    # Target: ~2000 token a blocco. Per italiano misto a tag SRD un token
    # vale mediamente ~4 caratteri → 8000 char ≈ 2000 token. Tieni il valore
    # leggermente sotto per non superare il limite della chat anche con
    # tokenizer più aggressivi.
    chunk_max_tokens: int = 2000
    chunk_max_chars: int = 8000
    chunk_intermediate_timeout: int = 60  # timeout per ogni conferma "Ricevuto"


# ── Profili per sito: selettori della chat per ciascun modello DM ───────
# Il profilo è scelto dall'host dell'URL configurato in Setup. Per ogni
# sito: selettori del campo input, del pulsante invia e dei messaggi
# dell'assistente. In mancanza si usa il profilo generico + euristica.
SITE_PROFILES = {
    "deepseek": {
        "hosts": ("deepseek.com",),
        "input": ["textarea", "div[contenteditable='true']"],
        "send": ["div[role='button'][aria-label]", "button[type='submit']"],
        "assistant": [
            '[data-message-author-role="assistant"]',
            '.ds-message[data-role="response"]',
            '.ds-message:not(.ds-message-user)',
            '.ds-markdown',
        ],
    },
    "qwen": {
        "hosts": ("qwen.ai", "tongyi"),
        "input": ["textarea", "div[contenteditable='true']"],
        "send": ["button[class*='send']", "button[aria-label*='send' i]",
                 "button[aria-label*='invia' i]"],
        "assistant": [
            '[class*="markdown"]',
            '[class*="message"][class*="assistant"]',
            '[class*="answer"]',
            '[class*="response"]',
        ],
    },
    "grok": {
        # selettori reali della UI di Grok (Tailwind): composer dentro
        # "div.absolute.bottom-0", messaggi con classe ".message-bubble".
        "hosts": ("grok.com", "x.ai"),
        "input": ["div.absolute.bottom-0 textarea",
                  "div.absolute.bottom-0 [contenteditable='true']",
                  "textarea"],
        "send": ["div.absolute.bottom-0 button[type='submit']",
                 "div.flex.justify-end > button",
                 "div.absolute.bottom-0 button"],
        "assistant": [".message-bubble", "div.relative.group.flex.flex-col"],
    },
    "claude": {
        # Claude.ai (claude.ai/chat/ — homepage chat + /chat/<id> dopo
        # creazione conversazione). Composer = editor ProseMirror (Tiptap)
        # contenteditable; pulsante invia con aria-label "Send Message" (la
        # capitalizzazione varia tra build, quindi usiamo match
        # case-insensitive); risposta assistente nel contenitore
        # `font-claude-message` o `font-claude-response` a seconda della
        # versione del frontend; fallback su data-attr `data-is-streaming`
        # e sul pannello con `data-test-render-count` (build recenti).
        "hosts": ("claude.ai",),
        "input": ["div.ProseMirror[contenteditable='true']",
                  "div[contenteditable='true'].ProseMirror",
                  "[contenteditable='true'][role='textbox']",
                  "div[contenteditable='true']",
                  "textarea"],
        "send": ["button[aria-label='Send Message']",
                 "button[aria-label='Send message']",
                 "button[aria-label*='send' i]",
                 "button[aria-label*='invia' i]",
                 "button[type='submit']",
                 "fieldset button[type='button']"],
        "assistant": [
            "div.font-claude-message",
            "div.font-claude-response",
            "[class*='font-claude-message']",
            "[class*='font-claude-response']",
            "[data-testid='assistant-message']",
            "div[data-is-streaming]",
            "[data-test-render-count]",
        ],
        # Mentre Claude genera (incluso il "pensiero" extended-thinking) il
        # contenitore della risposta porta data-is-streaming="true". Finché
        # è presente NON si deve considerare il testo definitivo: senza
        # questo gate la pausa fra thinking e risposta veniva scambiata per
        # risposta "stabile" e si catturava solo l'etichetta del pensiero.
        "streaming": ['[data-is-streaming="true"]'],
    },
}

_GENERIC_PROFILE = {
    "hosts": (),
    "input": ["textarea", "div[contenteditable='true']",
              "[contenteditable='true']", "input[type='text']"],
    "send": ["button[type='submit']", "button[aria-label*='send' i]",
             "button[aria-label*='invia' i]"],
    "assistant": [],
}


def _host_of(url: str) -> str:
    """Host (dominio) di un URL, in minuscolo."""
    return (url or "").split("//", 1)[-1].split("/", 1)[0].lower()


def _profile_for(url: str) -> dict:
    """Profilo di selettori per l'host dell'URL. Default = generico."""
    host = _host_of(url)
    for prof in SITE_PROFILES.values():
        if any(h in host for h in prof["hosts"]):
            return prof
    return _GENERIC_PROFILE


# JS eseguito nella pagina: estrae il testo dell'ULTIMA risposta
# dell'assistente. Riceve i selettori specifici del sito; se non bastano
# prova selettori comuni e infine un'euristica (individua la lista dei
# messaggi e prende l'ultimo turno). Funziona così su DeepSeek, Qwen,
# Grok e altre chat senza configurazione manuale.
_JS_LAST_ASSISTANT = r"""
(siteSelectors) => {
  const clean = (s) => (s || '').replace(/\u00a0/g, ' ').trim();
  // Legge il testo di un nodo ESCLUDENDO controlli e pannelli di
  // "ragionamento". Su claude.ai il blocco extended-thinking \u00e8 una
  // disclosure (button) la cui etichetta \u00e8 un riassunto tipo "Greeting in
  // Italian": senza rimuoverla finisce scambiata per la risposta. Si
  // rimuovono anche toolbar (copia/retry) e SVG. Si lavora su un clone per
  // non toccare la pagina reale.
  const textOf = (node) => {
    if (!node) return '';
    let clone;
    try { clone = node.cloneNode(true); } catch (_) { return clean(node.innerText || node.textContent); }
    const junk = clone.querySelectorAll(
      'button, [role="button"], svg, [aria-hidden="true"], ' +
      '[data-testid*="thinking" i], [class*="thinking" i], ' +
      '[data-testid*="reasoning" i], [class*="reasoning" i]'
    );
    junk.forEach((n) => { try { n.remove(); } catch (_) {} });
    // innerText riflette i ritorni a capo SOLO se il nodo è renderizzato
    // (attaccato al DOM e con layout). Un clone STACCATO fa fallback a
    // textContent → tutti i \n spariscono e la mappa ASCII collassa in una
    // riga sola. Attacchiamo il clone fuori schermo per leggere innerText
    // con i ritorni a capo, poi lo rimuoviamo.
    let txt = '';
    try {
      clone.style.position = 'fixed';
      clone.style.left = '-99999px';
      clone.style.top = '0';
      clone.style.whiteSpace = 'pre-wrap';
      document.body.appendChild(clone);
      txt = clone.innerText || clone.textContent;
    } catch (_) {
      txt = clone.innerText || clone.textContent;
    } finally {
      try { clone.remove(); } catch (_) {}
    }
    return clean(txt);
  };
  const pick = (sels) => {
    let best = '';
    for (const sel of (sels || [])) {
      let nodes;
      try { nodes = document.querySelectorAll(sel); } catch (_) { continue; }
      if (!nodes || !nodes.length) continue;
      const t = textOf(nodes[nodes.length - 1]);
      if (t.length > best.length) best = t;
    }
    return best;
  };
  // 1. selettori specifici del sito (passati da Python)
  let best = pick(siteSelectors);
  // 2. selettori comuni a molte chat IA
  if (best.length < 12) {
    best = pick([
      '[data-message-author-role="assistant"]',
      'div[class*="markdown-body"]',
      'div[class*="markdown"]',
      '.prose',
      'div[class*="message"]:not([class*="user"]):not([class*="human"])',
    ]);
  }
  // 3. euristica: individua la lista della conversazione (l'elemento con
  //    più figli "messaggio") e prende l'ultimo turno = risposta corrente.
  if (best.length < 12) {
    let list = null, score = 1;
    for (const el of document.querySelectorAll('div,main,section,ul,ol')) {
      const kids = [...el.children].filter((c) => {
        const t = (c.innerText || '').trim();
        return t.length > 30 && !c.querySelector('textarea,[contenteditable="true"]');
      });
      if (kids.length > score) { score = kids.length; list = el; }
    }
    if (list) {
      const kids = [...list.children];
      for (let i = kids.length - 1; i >= 0; i--) {
        const t = textOf(kids[i]);
        if (t.length > 12) { best = t; break; }
      }
    }
  }
  return best;
}
"""


def _split_prompt(text: str, max_chars: int) -> list[str]:
    """Spezza `text` in blocchi <= max_chars rispettando i confini naturali:
    prima i doppi a-capo (paragrafi), poi i singoli a-capo, infine taglio
    duro a carattere se una riga è da sola più grande del limite.
    Ritorna sempre almeno 1 blocco; nessun blocco supera max_chars."""
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    def _flush(buf: str, out: list[str]) -> str:
        if buf:
            out.append(buf)
        return ""

    parts: list[str] = []
    cur = ""
    for para in text.split("\n\n"):
        candidate = (cur + "\n\n" + para) if cur else para
        if len(candidate) <= max_chars:
            cur = candidate
            continue
        # paragrafo non entra: chiudi il blocco corrente
        cur = _flush(cur, parts)
        if len(para) <= max_chars:
            cur = para
            continue
        # paragrafo da solo troppo grande: spezzalo per riga
        buf = ""
        for ln in para.split("\n"):
            cand2 = (buf + "\n" + ln) if buf else ln
            if len(cand2) <= max_chars:
                buf = cand2
                continue
            buf = _flush(buf, parts)
            if len(ln) <= max_chars:
                buf = ln
                continue
            # riga singola troppo grande: taglio duro a carattere
            for i in range(0, len(ln), max_chars):
                seg = ln[i:i + max_chars]
                if i + max_chars < len(ln):
                    parts.append(seg)
                else:
                    buf = seg
        if buf:
            cur = buf
    if cur:
        parts.append(cur)
    return parts


class Webchat:
    """
    Wrapper su Playwright + Chromium con worker thread dedicato.

    Tutte le operazioni Playwright DEVONO essere eseguite tramite
    `_run(fn, ...)` per garantire che girino sul thread che ha creato
    le risorse (requisito Playwright sync).
    """

    def __init__(self, config: Optional[WebchatConfig] = None):
        self.config = config or WebchatConfig()
        self._pw = None
        self._context = None
        self._page = None
        self._tasks: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._briefing_sent = False
        self._briefing_in_flight = False
        self._open = False
        self._working = False   # True mentre il worker esegue un task Playwright
        # Stato osservabile dal frontend: {ts, phase, detail, error}.
        # phase ∈ {"idle","opening","waiting_inflight","sending","success",
        # "error","empty"}. Aggiornato a ogni transizione importante della
        # send_briefing così l'utente vede esattamente cosa sta succedendo
        # quando preme «Carica».
        self.last_status: dict = {
            "ts":     datetime.now().isoformat(timespec="seconds"),
            "phase":  "idle",
            "detail": "",
            "error":  "",
        }

    def _set_status(self, phase: str, detail: str = "", error: str = "") -> None:
        self.last_status = {
            "ts":     datetime.now().isoformat(timespec="seconds"),
            "phase":  phase,
            "detail": detail,
            "error":  error,
        }
        print(f"[WEBCHAT STATUS] {phase}: {detail or ''} {('err=' + error) if error else ''}",
              flush=True)

    # ──────────────── worker thread ────────────────

    def _worker_loop(self):
        while True:
            fn, args, kwargs, reply_q = self._tasks.get()
            self._working = True
            try:
                reply_q.put(("ok", fn(*args, **kwargs)))
            except Exception as e:
                reply_q.put(("error", str(e)))
            finally:
                self._working = False

    def _run(self, fn: Callable, *args, timeout: int = 180, **kwargs):
        if self._thread is None or not self._thread.is_alive():
            # reset stato resource del vecchio thread (Playwright sync non è
            # cross-thread)
            self._pw = self._context = self._page = None
            self._open = False
            self._briefing_sent = False
            self._thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._thread.start()

        reply_q: queue.Queue = queue.Queue(maxsize=1)
        self._tasks.put((fn, args, kwargs, reply_q))
        try:
            status, data = reply_q.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("Timeout worker Chromium.")
        if status == "error":
            raise RuntimeError(data)
        return data

    # ──────────────── lifecycle Playwright ────────────────

    @staticmethod
    def _clean_singleton_locks(profile_dir: str) -> None:
        """Rimuove i file SingletonLock/Cookie/Socket lasciati da un Chromium
        ucciso male. Senza questa pulizia il successivo
        `launch_persistent_context` muore con
        «Target page, context or browser has been closed» perché Chrome trova
        il profilo "occupato" da un PID ormai morto. Idempotente."""
        if not profile_dir or not os.path.isdir(profile_dir):
            return
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket",
                     "lockfile"):
            p = os.path.join(profile_dir, name)
            try:
                if os.path.exists(p) or os.path.islink(p):
                    os.remove(p)
                    print(f"[WEBCHAT] rimosso file stantio: {name}", flush=True)
            except OSError as e:
                print(f"[WEBCHAT] impossibile rimuovere {name}: {e}", flush=True)

    def _launch_context(self):
        assert self._pw is not None, "Playwright non avviato"
        os.makedirs(self.config.profile_dir, exist_ok=True)
        self._clean_singleton_locks(self.config.profile_dir)
        base_args = ["--disable-blink-features=AutomationControlled"]
        launch_kwargs = {
            "headless": False,
            "viewport": {"width": 1280, "height": 900},
            "args": list(base_args),
        }

        attempts = [
            # 1) bundled Chromium (più affidabile su Windows perché versione
            #    sempre allineata a Playwright)
            {"label": "chromium bundled", "kwargs": dict(launch_kwargs)},
            # 2) Chrome stabile installato dall'utente — utile se il bundled
            #    è bloccato da AV; fallisce silente se Chrome non c'è.
            {"label": "channel=chrome",
             "kwargs": dict(launch_kwargs, channel="chrome")},
            # 3) Retry con --no-sandbox e profilo ripulito da nuovo: alcune
            #    Windows con policy aziendali rifiutano il sandbox standard.
            {"label": "no-sandbox",
             "kwargs": dict(launch_kwargs,
                            args=base_args + ["--no-sandbox",
                                              "--disable-gpu",
                                              "--disable-features=RendererCodeIntegrity"])},
        ]

        last_err: Optional[Exception] = None
        for att in attempts:
            try:
                print(f"[WEBCHAT] tentativo: {att['label']}", flush=True)
                ctx = self._pw.chromium.launch_persistent_context(
                    self.config.profile_dir, **att["kwargs"])
                print(f"[WEBCHAT] launch riuscito con {att['label']}", flush=True)
                return ctx
            except Exception as e:
                last_err = e
                msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                print(f"[WEBCHAT] {att['label']} fallito: {msg}", flush=True)
                # se il profilo si è corrotto durante il tentativo, ripulisci
                # i lock prima del prossimo round.
                self._clean_singleton_locks(self.config.profile_dir)

        raise last_err if last_err else RuntimeError(
            "Nessun tentativo di launch riuscito")

    def _ensure(self):
        """Garantisce pagina viva. Da chiamare SOLO nel worker thread."""
        if not _PW_AVAILABLE:
            raise RuntimeError(
                "Playwright non installato. py -m pip install playwright && "
                "py -m playwright install chromium")

        if self._page is not None:
            try:
                if not self._page.is_closed():
                    # se da Setup è stata scelta un'altra chat, naviga lì:
                    # Chromium passa al sito selezionato (Grok, Qwen, ...).
                    want = _host_of(self.config.url)
                    cur = _host_of(self._page.url or "")
                    if want and want not in cur:
                        print(f"[WEBCHAT] cambio chat → {self.config.url}", flush=True)
                        self._page.goto(self.config.url,
                                        wait_until="domcontentloaded", timeout=60000)
                        self._briefing_sent = False
                        self._page.bring_to_front()
                    return self._page
            except Exception:
                pass

        # cleanup
        for obj in (self._page, self._context):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self._page = self._context = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
        self._briefing_sent = False
        self._open = False

        # bootstrap
        assert sync_playwright is not None  # garantito da _PW_AVAILABLE sopra
        print(f"[WEBCHAT] Avvio Playwright + Chromium su {self.config.url}", flush=True)
        self._pw = sync_playwright().start()
        try:
            self._context = self._launch_context()
        except Exception as e:
            try: self._pw.stop()
            except Exception: pass
            self._pw = None
            # Estrai prima riga: i messaggi Playwright sono multilinea con
            # tutto l'output di Chromium → impossibile da leggere in UI.
            first_line = str(e).splitlines()[0] if str(e) else type(e).__name__
            hint = (
                "Verifica: (a) nessun'altra istanza di Chromium che usa lo "
                "stesso profilo .browser_profiles\\... sia aperta; (b) "
                "l'antivirus non stia bloccando il binario; (c) rieseguire "
                "'py -m playwright install chromium' se aggiornato di "
                "recente."
            )
            raise RuntimeError(f"Impossibile avviare Chromium: {first_line}. {hint}")

        page = self._context.pages[0] if self._context.pages else self._context.new_page()
        host = self.config.url.split("//", 1)[-1].split("/", 1)[0]
        if not page.url or page.url == "about:blank" or host not in (page.url or ""):
            page.goto(self.config.url, wait_until="domcontentloaded", timeout=60000)
        self._page = page
        self._open = True
        print(f"[WEBCHAT] aperto: {page.url}", flush=True)
        return page

    # ──────────────── operazioni di pagina ────────────────

    def _find_input(self, page):
        # selettori del profilo del sito, poi quelli generici
        selectors = list(self._profile().get("input", []))
        for s in ("textarea", "div[contenteditable='true']",
                  "[contenteditable='true']", "input[type='text']"):
            if s not in selectors:
                selectors.append(s)
        for sel in selectors:
            try:
                loc = page.locator(sel).last
                if loc.count() and loc.is_visible(timeout=1500):
                    return loc
            except Exception:
                continue
        raise RuntimeError(
            "Campo input non trovato nella pagina. Se richiede login, "
            "accedi manualmente in Chromium e riprova.")

    def _input_text(self, input_box) -> str:
        """Testo attualmente nel campo input: `value` per textarea/input,
        `innerText` per i contenteditable. Stringa vuota se non leggibile."""
        try:
            return (input_box.input_value(timeout=500) or "").strip()
        except Exception:
            pass
        try:
            return (input_box.inner_text(timeout=500) or "").strip()
        except Exception:
            return ""

    def _submitted(self, input_box) -> bool:
        """L'invio è andato a buon fine se il composer si è svuotato: tutte
        le chat configurate (DeepSeek/Qwen/Grok/Claude) ripuliscono il campo
        quando il messaggio parte. È il segnale di conferma più affidabile,
        indipendente dal selettore del pulsante."""
        return self._input_text(input_box) == ""

    def _click_send(self, page, input_box):
        # Strategia robusta con VERIFICA: dopo ogni tentativo controlla che
        # il campo si sia svuotato (= messaggio partito). Senza la verifica
        # un click sul controllo sbagliato lasciava il testo digitato ma MAI
        # inviato. Tutte le chat configurate inviano con Invio, quindi è il
        # tentativo primario; il pulsante è il fallback.
        def _try(action) -> bool:
            # Se il campo è GIÀ vuoto un tentativo precedente ha inviato:
            # non agire di nuovo (eviterebbe un doppio invio).
            if self._submitted(input_box):
                return True
            try:
                action()
            except Exception:
                return False
            # poll fino a ~1.2s: il framework può impiegare un attimo a
            # processare l'invio e svuotare il composer.
            for _ in range(6):
                page.wait_for_timeout(200)
                if self._submitted(input_box):
                    return True
            return False

        # 1) Invio da tastiera sul campo focalizzato (caso comune)
        if _try(lambda: input_box.press("Enter")):
            return

        # 2) Pulsante invia: selettori del profilo, poi generici
        button_sels = list(self._profile().get("send", []))
        for s in ("button[type='submit']", "button[aria-label*='send' i]",
                  "button[aria-label*='invia' i]", "button[data-testid*='send']",
                  "div[role='button'][aria-label]", "button:has(svg)"):
            if s not in button_sels:
                button_sels.append(s)
        for sel in button_sels:
            try:
                btn = page.locator(sel).last
                if not (btn.count() and btn.is_visible(timeout=500)):
                    continue
            except Exception:
                continue
            if _try(lambda b=btn: b.click(timeout=1500)):
                return

        # 3) ultimo tentativo: Ctrl+Invio (alcune UI lo usano per inviare)
        if _try(lambda: input_box.press("Control+Enter")):
            return
        print("[WEBCHAT] ATTENZIONE: invio non confermato (campo non svuotato) "
              "— il messaggio potrebbe non essere partito.", flush=True)

    def _profile(self) -> dict:
        """Profilo di selettori per la chat attualmente configurata."""
        return _profile_for(self.config.url)

    def _last_assistant_text(self, page) -> str:
        try:
            sels = self._profile().get("assistant", [])
            return page.evaluate(_JS_LAST_ASSISTANT, sels) or ""
        except Exception:
            return ""

    def _is_streaming(self, page) -> bool:
        """True se la chat sta ANCORA generando la risposta. Usa i selettori
        `streaming` del profilo (es. data-is-streaming="true" su Claude).
        Senza selettori configurati ritorna False (comportamento invariato
        per DeepSeek/Qwen/Grok)."""
        sels = self._profile().get("streaming", [])
        if not sels:
            return False
        for sel in sels:
            try:
                if page.locator(sel).count():
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _looks_incomplete(text: str) -> bool:
        """True se la risposta sembra ancora a metà: un blocco mappa è
        APERTO (c'è MAP_START ma manca un MAP_END dopo di esso). Una mappa
        20×20 si genera riga per riga e può rallentare a metà: senza questo
        guard la finestra di stabilità scadeva e si catturava una griglia
        TRONCATA (senza MAP_END). Il deadline complessivo protegge comunque
        dal caso in cui MAP_END non arrivi mai."""
        if not text:
            return False
        up = text.upper()
        i = up.rfind("MAP_START")
        if i == -1:
            return False
        return "MAP_END" not in up[i + len("MAP_START"):]

    def _send_and_wait(self, page, prompt: str, timeout_s: int) -> str:
        deadline = datetime.now().timestamp() + timeout_s
        page.bring_to_front()
        before = self._last_assistant_text(page)
        # forma normalizzata della domanda: serve per NON scambiare la
        # nostra stessa bolla (comparsa dopo l'invio) per la risposta
        prompt_norm = " ".join(prompt.split())

        input_box = self._find_input(page)
        input_box.click(timeout=5000)
        try:
            input_box.fill(prompt, timeout=8000)
        except Exception:
            page.keyboard.press("Control+A")
            page.keyboard.type(prompt, delay=0)
        self._click_send(page, input_box)

        last_text = ""
        stable_since: Optional[float] = None
        while datetime.now().timestamp() < deadline:
            page.wait_for_timeout(self.config.poll_interval_ms)
            text = self._last_assistant_text(page)
            if not text or text == before:
                continue
            # se il testo letto è (un pezzo del)la nostra domanda, è la
            # bolla del giocatore: la risposta del DM non è ancora pronta
            text_norm = " ".join(text.split())
            if text_norm and text_norm in prompt_norm:
                continue
            # Gate streaming: finché la chat sta ancora generando (su Claude
            # anche durante la fase di "pensiero" che precede la risposta) il
            # testo NON è definitivo. Si traccia ma non si conferma mai come
            # stabile, così non si cattura per errore l'etichetta del
            # ragionamento durante la pausa pensiero→risposta.
            if self._is_streaming(page):
                if text != last_text:
                    last_text = text
                stable_since = None
                continue
            if text != last_text:
                last_text = text
                stable_since = datetime.now().timestamp()
                continue
            # Blocco mappa aperto (MAP_START senza MAP_END): la risposta è
            # ancora in arrivo anche se il testo non cresce per un attimo.
            # Non confermarla come stabile finché la mappa non si chiude.
            if self._looks_incomplete(text):
                stable_since = None
                continue
            if stable_since and datetime.now().timestamp() - stable_since >= self.config.stable_seconds:
                return text

        if last_text:
            return last_text
        raise TimeoutError("Nessuna risposta dalla chat web entro il timeout.")

    def _send_chunked_in_page(self, page, prompt: str, timeout_s: int) -> str:
        """Invia `prompt` come sequenza di blocchi quando supera il limite
        della chat web (`chunk_max_chars`). Pattern:

          1) [istruzioni] "Ti darò N blocchi. Conferma con 'Ricevuto N/X'.
             NON rispondere finché non scrivo 'FINE'."
          2) [BLOCCO i/N] testo                          — N-1 volte
          3) [BLOCCO N/N] testo
             FINE. Ora rispondi al prompt completo.

        Ritorna SOLO la risposta finale (quella all'ultimo blocco col
        marker FINE). Le conferme intermedie vengono lette e ignorate.

        DEVE girare sul worker thread (chiamato da send/send_briefing che
        sono già dentro `_run`)."""
        chunks = _split_prompt(prompt, self.config.chunk_max_chars)
        n = len(chunks)
        if n <= 1:
            return self._send_and_wait(page, prompt, timeout_s)

        print(f"[WEBCHAT] prompt lungo ({len(prompt)} char) → "
              f"spezzato in {n} blocchi", flush=True)

        tok_per_block = self.config.chunk_max_tokens
        intro = (
            f"Sto per inviarti un prompt molto lungo suddiviso in {n} "
            f"blocchi consecutivi (~{tok_per_block} token a blocco). NON "
            f"rispondere alla sostanza finché non ricevi il marker 'FINE'. "
            f"Per ogni blocco rispondi SOLO con \"Ricevuto blocco i/{n}\" "
            f"(una riga, niente altro). All'arrivo di 'FINE' processa "
            f"l'INTERO contenuto come un unico prompt e rispondi "
            f"normalmente. Confermi?"
        )
        # intro + conferme intermedie: timeout corto, sono risposte brevi
        inter_to = max(15, int(self.config.chunk_intermediate_timeout))
        try:
            self._send_and_wait(page, intro, inter_to)
        except TimeoutError:
            # nessuna conferma all'intro: andiamo avanti lo stesso, il
            # modello quasi sempre rispetta il pattern anche senza ack
            print("[WEBCHAT] nessuna conferma all'intro chunking; proseguo",
                  flush=True)

        for i, blk in enumerate(chunks[:-1], start=1):
            msg = f"[BLOCCO {i}/{n}]\n{blk}"
            try:
                self._send_and_wait(page, msg, inter_to)
            except TimeoutError:
                print(f"[WEBCHAT] blocco {i}/{n} senza conferma; proseguo",
                      flush=True)

        last = chunks[-1]
        final_msg = (
            f"[BLOCCO {n}/{n}]\n{last}\n\n"
            f"FINE. Ora elabora i {n} blocchi come un unico prompt e "
            f"rispondi normalmente (rispetta tutti i tag e le regole del "
            f"system prompt iniziale)."
        )
        return self._send_and_wait(page, final_msg, timeout_s)

    # ──────────────── API pubblica (eseguita su worker thread) ────────────────

    def open(self) -> str:
        self._set_status("opening", f"avvio Chromium → {self.config.url}")
        def _do():
            page = self._ensure()
            page.bring_to_front()
            return page.url
        try:
            url = self._run(_do, timeout=90)
            self._set_status("idle", f"Chromium aperto: {url}")
            return url
        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            self._set_status("error", "apertura Chromium fallita", msg)
            raise

    def is_open(self) -> bool:
        return self._open

    def is_briefed(self) -> bool:
        """True se il DM ha già ricevuto il briefing iniziale e quindi
        conosce il contesto della partita: i messaggi successivi possono
        contenere SOLO il testo del giocatore."""
        return self._briefing_sent

    def is_briefing_in_flight(self) -> bool:
        """True mentre un briefing (es. force=True per ricaricamento partita
        o avventura precaricata) sta venendo inviato al DM. Permette al
        chiamante di evitare di accodare un secondo briefing in parallelo."""
        return self._briefing_in_flight

    def mark_briefed(self) -> None:
        """Marca il DM come allineato (briefing già inviato per altra via)."""
        self._briefing_sent = True

    def reset_briefing(self) -> None:
        """Dimentica il briefing: il prossimo invio dovrà ripassare TUTTO il
        contesto. Lo si chiama al CAMBIO MODELLO (riapertura da Setup): la
        nuova chat non conosce la partita e potrebbe richiedere un nuovo
        login. Mentre `is_briefed()` è False il frontend disabilita l'input
        (sospende le richieste) e il polling /api/webchat/sync ri-briefa il
        nuovo modello appena è pronto/loggato, riprendendo la comunicazione."""
        self._briefing_sent = False

    def _page_has_input(self) -> bool:
        """Sul worker thread: la pagina mostra GIÀ il campo di scrittura
        della chat? Se sì → utente loggato e chat pronta. Se no (muro di
        login, captcha, pagina ancora in caricamento) → non pronta."""
        page = self._ensure()
        selectors = list(self._profile().get("input", []))
        for s in ("textarea", "div[contenteditable='true']",
                  "[contenteditable='true']", "input[type='text']"):
            if s not in selectors:
                selectors.append(s)
        for sel in selectors:
            try:
                loc = page.locator(sel).last
                if loc.count() and loc.is_visible(timeout=800):
                    return True
            except Exception:
                continue
        return False

    def login_ready(self) -> bool:
        """True se il modello è raggiungibile E loggato (campo input chat
        presente). Finché è False NON si deve inviare nulla a Chromium: la
        richiesta resta sospesa fino a login fatto. Va chiamata quando il
        worker è libero (nessun invio in corso): in quel caso ritorna in ~1s.
        Se un briefing è già in volo è ovviamente pronta (sta scrivendo)."""
        if not self._open:
            return False
        if self._briefing_in_flight:
            return True
        # Worker già impegnato in un invio: la pagina è necessariamente
        # operativa (e loggata). Senza questo check la richiesta restava
        # bloccata 20s in coda dietro l'invio in corso e il task stantio
        # girava comunque a invio finito.
        if self._working:
            return True
        try:
            return bool(self._run(self._page_has_input, timeout=20))
        except Exception:
            return False

    def new_chat(self) -> bool:
        """Apre una conversazione NUOVA nella chat web navigando alla URL
        base del modello (su DeepSeek/Claude/Qwen/Grok la home = chat nuova)
        e dimentica il briefing. La usa «Nuova partita»: la conversazione
        precedente resta nello storico del sito ma il DM riparte pulito,
        senza il contesto (trama, mappe, esiti) della partita conclusa."""
        if not self._open:
            return False

        def _do():
            page = self._ensure()
            page.goto(self.config.url, wait_until="domcontentloaded",
                      timeout=60000)
            page.bring_to_front()
            return True

        try:
            ok = bool(self._run(_do, timeout=90))
        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            print(f"[WEBCHAT] new_chat fallita: {msg}", flush=True)
            self._set_status("error", "apertura nuova conversazione fallita", msg)
            return False
        self._briefing_sent = False
        self._set_status("idle", "nuova conversazione aperta")
        return ok

    def send(self, prompt: str, timeout: Optional[int] = None) -> str:
        timeout_s = timeout or self.config.timeout
        max_chars = max(0, int(self.config.chunk_max_chars or 0))
        chunked = bool(max_chars) and len(prompt) > max_chars

        def _do():
            page = self._ensure()
            if chunked:
                return self._send_chunked_in_page(page, prompt, timeout_s)
            return self._send_and_wait(page, prompt, timeout_s)

        # con N blocchi servono N round-trip: allarga il timeout del worker
        n_blocks = (len(prompt) // max(1, max_chars)) + 1 if chunked else 1
        outer_to = timeout_s + 30 + (n_blocks - 1) * (
            self.config.chunk_intermediate_timeout + 10)
        return self._run(_do, timeout=outer_to)

    def send_briefing(self, briefing: str, force: bool = False) -> str:
        """Invia un messaggio iniziale (system prompt + PG precaricati).
        Idempotente: invia solo la prima volta a meno che force=True.

        Durante un re-briefing forzato (es. nuova avventura caricata,
        partita ripristinata) la flag `_briefing_sent` viene RESETTATA
        prima dell'invio: così `is_briefed()` ritorna False finché il
        nuovo briefing non è effettivamente arrivato al DM. Il frontend
        (che disabilita la chat input quando il DM non è briefato) evita
        la race in cui un "START" del giocatore arriva al modello PRIMA
        del contesto della partita ricaricata.

        Ritorna il TESTO della risposta del modello (stringa vuota in
        caso di errore o briefing saltato/duplicato). I caller che
        vogliono usare la risposta — es. avventura precaricata che
        chiede direttamente la prima scena — possono parsarla; i caller
        fire-and-forget possono semplicemente ignorare il return value
        (lo `str` è truthy/falsy come il vecchio `bool`).
        """
        if not briefing.strip():
            self._set_status("empty", "briefing vuoto: nessun contesto da inviare")
            return ""
        if self._briefing_sent and not force:
            self._set_status("idle", "briefing già consegnato in precedenza")
            return ""
        if self._briefing_in_flight:
            # Un briefing è già in volo. Senza force: non accodarne un
            # secondo identico/concorrente (il chiamante riproverà al
            # prossimo polling). Con force=True (es. /api/load esplicito
            # del giocatore): attendi che il precedente termini — il
            # nuovo stato/storico è autoritativo, non possiamo dropparlo
            # senza rompere la ripresa partita.
            if not force:
                self._set_status("waiting_inflight",
                                 "altro briefing in corso, retry posticipato")
                return ""
            self._set_status("waiting_inflight",
                             "attendo completamento briefing precedente")
            import time as _time
            waited = 0.0
            while self._briefing_in_flight and waited < 120.0:
                _time.sleep(0.5)
                waited += 0.5
            if self._briefing_in_flight:
                self._set_status("error", "",
                                 "briefing precedente non terminato in 120s")
                return ""
        if force:
            self._briefing_sent = False
        self._briefing_in_flight = True

        max_chars = max(0, int(self.config.chunk_max_chars or 0))
        chunked = bool(max_chars) and len(briefing) > max_chars
        timeout_s = self.config.timeout

        def _do():
            page = self._ensure()
            if chunked:
                return self._send_chunked_in_page(page, briefing, timeout_s)
            return self._send_and_wait(page, briefing, timeout_s)

        n_blocks = (len(briefing) // max(1, max_chars)) + 1 if chunked else 1
        outer_to = timeout_s + 30 + (n_blocks - 1) * (
            self.config.chunk_intermediate_timeout + 10)
        self._set_status("sending",
                         f"invio briefing ({len(briefing)} char, "
                         f"{n_blocks} blocco/i, timeout {outer_to}s)")
        try:
            text = self._run(_do, timeout=outer_to)
            if text:
                self._briefing_sent = True
                self._set_status("success",
                                 f"risposta DM ricevuta ({len(text)} char)")
            else:
                self._set_status("error", "",
                                 "DM ha risposto con stringa vuota")
            return text or ""
        except Exception as e:
            msg = str(e).splitlines()[0] if str(e) else type(e).__name__
            print(f"[WEBCHAT] briefing fallito: {e}", flush=True)
            self._set_status("error", "", msg)
            return ""
        finally:
            self._briefing_in_flight = False


__all__ = ["WebchatConfig", "Webchat"]
