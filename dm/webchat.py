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

import os
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

try:
    from playwright.sync_api import sync_playwright
    _PW_AVAILABLE = True
except ImportError:
    sync_playwright = None
    _PW_AVAILABLE = False


BASE_DIR = os.path.dirname(os.path.dirname(__file__))


@dataclass
class WebchatConfig:
    url: str = "https://chat.deepseek.com/"
    name: str = "DeepSeek Chat"
    timeout: int = 180        # secondi per la risposta complessiva
    stable_seconds: int = 3   # quanto attendere testo "fermo" prima di considerarlo finito
    poll_interval_ms: int = 1200
    profile_dir: str = field(default_factory=lambda: os.path.join(
        BASE_DIR, ".browser_profiles", "deepseek"))


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
        # Claude.ai (claude.ai): composer = editor ProseMirror
        # contenteditable; pulsante invia con aria-label "Send message";
        # risposte dell'assistente nel contenitore ".font-claude-message".
        "hosts": ("claude.ai",),
        "input": ["div.ProseMirror[contenteditable='true']",
                  "div[contenteditable='true'].ProseMirror",
                  "div[contenteditable='true']",
                  "textarea"],
        "send": ["button[aria-label='Send message']",
                 "button[aria-label*='send' i]",
                 "button[type='submit']"],
        "assistant": [
            "div.font-claude-message",
            "div.font-claude-response",
            "[data-testid='assistant-message']",
            "div[data-is-streaming]",
        ],
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
  const pick = (sels) => {
    let best = '';
    for (const sel of (sels || [])) {
      let nodes;
      try { nodes = document.querySelectorAll(sel); } catch (_) { continue; }
      if (!nodes || !nodes.length) continue;
      const last = nodes[nodes.length - 1];
      const t = clean(last.innerText || last.textContent);
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
        const t = clean(kids[i].innerText);
        if (t.length > 12) { best = t; break; }
      }
    }
  }
  return best;
}
"""


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
        self._open = False

    # ──────────────── worker thread ────────────────

    def _worker_loop(self):
        while True:
            fn, args, kwargs, reply_q = self._tasks.get()
            try:
                reply_q.put(("ok", fn(*args, **kwargs)))
            except Exception as e:
                reply_q.put(("error", str(e)))

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

    def _launch_context(self):
        os.makedirs(self.config.profile_dir, exist_ok=True)
        launch_kwargs = {
            "headless": False,
            "viewport": {"width": 1280, "height": 900},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        try:
            return self._pw.chromium.launch_persistent_context(
                self.config.profile_dir, channel="chrome", **launch_kwargs)
        except Exception:
            return self._pw.chromium.launch_persistent_context(
                self.config.profile_dir, **launch_kwargs)

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
        print(f"[WEBCHAT] Avvio Playwright + Chromium su {self.config.url}", flush=True)
        self._pw = sync_playwright().start()
        try:
            self._context = self._launch_context()
        except Exception as e:
            try: self._pw.stop()
            except Exception: pass
            self._pw = None
            raise RuntimeError(f"Impossibile avviare Chromium: {e}")

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

    def _click_send(self, page, input_box):
        # pulsanti del profilo del sito, poi quelli generici
        button_sels = list(self._profile().get("send", []))
        for s in ("button[type='submit']", "button[aria-label*='send' i]",
                  "button[aria-label*='invia' i]", "button[data-testid*='send']",
                  "button:has(svg)"):
            if s not in button_sels:
                button_sels.append(s)
        for sel in button_sels:
            try:
                btn = page.locator(sel).last
                if btn.count() and btn.is_visible(timeout=500) and btn.is_enabled(timeout=500):
                    btn.click(timeout=1500)
                    return
            except Exception:
                continue
        # nessun pulsante utile: invia da tastiera
        try:
            input_box.press("Enter")
        except Exception:
            pass

    def _profile(self) -> dict:
        """Profilo di selettori per la chat attualmente configurata."""
        return _profile_for(self.config.url)

    def _last_assistant_text(self, page) -> str:
        try:
            sels = self._profile().get("assistant", [])
            return page.evaluate(_JS_LAST_ASSISTANT, sels) or ""
        except Exception:
            return ""

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
            if text != last_text:
                last_text = text
                stable_since = datetime.now().timestamp()
                continue
            if stable_since and datetime.now().timestamp() - stable_since >= self.config.stable_seconds:
                return text

        if last_text:
            return last_text
        raise TimeoutError("Nessuna risposta dalla chat web entro il timeout.")

    # ──────────────── API pubblica (eseguita su worker thread) ────────────────

    def open(self) -> str:
        def _do():
            page = self._ensure()
            page.bring_to_front()
            return page.url
        return self._run(_do, timeout=90)

    def is_open(self) -> bool:
        return self._open

    def is_briefed(self) -> bool:
        """True se il DM ha già ricevuto il briefing iniziale e quindi
        conosce il contesto della partita: i messaggi successivi possono
        contenere SOLO il testo del giocatore."""
        return self._briefing_sent

    def mark_briefed(self) -> None:
        """Marca il DM come allineato (briefing già inviato per altra via)."""
        self._briefing_sent = True

    def send(self, prompt: str, timeout: Optional[int] = None) -> str:
        timeout_s = timeout or self.config.timeout
        def _do():
            page = self._ensure()
            return self._send_and_wait(page, prompt, timeout_s)
        return self._run(_do, timeout=timeout_s + 30)

    def send_briefing(self, briefing: str, force: bool = False) -> bool:
        """Invia un messaggio iniziale (system prompt + PG precaricati).
        Idempotente: invia solo la prima volta a meno che force=True."""
        if not briefing.strip():
            return False
        if self._briefing_sent and not force:
            return False
        def _do():
            page = self._ensure()
            self._send_and_wait(page, briefing, self.config.timeout)
            return True
        try:
            ok = self._run(_do, timeout=self.config.timeout + 30)
            if ok:
                self._briefing_sent = True
            return bool(ok)
        except Exception as e:
            print(f"[WEBCHAT] briefing fallito: {e}", flush=True)
            return False


__all__ = ["WebchatConfig", "Webchat"]
