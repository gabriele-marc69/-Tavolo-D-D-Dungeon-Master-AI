/* ════════════════════════════════════════════════════════════════════
   Music — colonna sonora generativa in stile Strudel / TidalCycles.

   La musica è descritta da PATTERN CICLICI scritti in mini-notation
   (come https://strudel.cc): ogni stringa è un ciclo (battuta), gli
   elementi separati da spazio si dividono il ciclo equamente.
     "a b c"      3 eventi nel ciclo
     "[a b] c"    sotto-suddivisione (a e b nel primo mezzo ciclo)
     "<a b c>"    alterna: un elemento per ciclo, a rotazione
     "~"          pausa
     "a*2"        ripeti a due volte nel suo slot

   Ogni mood è un set di pattern (pad / basso / lead / percussioni).
   Uno scheduler interroga i pattern ciclo per ciclo e li suona con la
   Web Audio API (nessun file audio). Il mood segue lo stato di gioco.

   API pubblica (window.Music): toggle(), applyState(gs), enabled
   ════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ── Mini-notation: parser ──────────────────────────────────────────
  // Una stringa → albero di nodi {t:'seq'|'alt'|'atom'|'rest'|'mul'}.
  function parseMini(str) {
    const toks = (str || '').match(/[\[\]<>~*]|[A-Za-z][\w#-]*|\d+/g) || [];
    let i = 0;
    function parseSeq(close) {
      const items = [];
      while (i < toks.length && toks[i] !== close) items.push(parseItem());
      if (close) i++;                       // consuma il delimitatore di chiusura
      return items;
    }
    function parseItem() {
      let node;
      const t = toks[i];
      if (t === '[')      { i++; node = { t: 'seq', items: parseSeq(']') }; }
      else if (t === '<') { i++; node = { t: 'alt', items: parseSeq('>') }; }
      else if (t === '~') { i++; node = { t: 'rest' }; }
      else                { i++; node = { t: 'atom', v: t }; }
      if (toks[i] === '*') {                 // modificatore di ripetizione
        i++;
        const n = parseInt(toks[i++], 10) || 1;
        node = { t: 'mul', n, item: node };
      }
      return node;
    }
    return { t: 'seq', items: parseSeq(null) };
  }

  // Interroga un nodo per il ciclo `cyc`, riempiendo [start, start+dur]
  // (frazioni 0..1 del ciclo). Accumula eventi {v, start, dur} in `out`.
  function queryNode(node, cyc, start, dur, out) {
    if (!node || node.t === 'rest') return;
    if (node.t === 'atom') { out.push({ v: node.v, start, dur }); return; }
    if (node.t === 'mul') {
      for (let k = 0; k < node.n; k++) {
        queryNode(node.item, cyc * node.n + k,
                  start + dur * k / node.n, dur / node.n, out);
      }
      return;
    }
    if (node.t === 'alt') {
      const n = node.items.length;
      if (n) queryNode(node.items[((cyc % n) + n) % n], cyc, start, dur, out);
      return;
    }
    if (node.t === 'seq') {
      const n = node.items.length;
      for (let k = 0; k < n; k++) {
        queryNode(node.items[k], cyc, start + dur * k / n, dur / n, out);
      }
    }
  }

  // Un Pattern: compila una stringa mini-notation, interrogabile per ciclo.
  function pattern(str) {
    const tree = parseMini(str);
    return { query: (cyc) => { const o = []; queryNode(tree, cyc, 0, 1, o); return o; } };
  }

  // ── Teoria: note e accordi ─────────────────────────────────────────
  const NOTE_BASE = { c: 0, d: 2, e: 4, f: 5, g: 7, a: 9, b: 11 };

  function noteToMidi(s) {
    const m = /^([a-g])([#b]?)(-?\d+)?$/i.exec(s || '');
    if (!m) return null;
    let n = NOTE_BASE[m[1].toLowerCase()];
    if (m[2] === '#') n++; else if (m[2] === 'b') n--;
    const oct = m[3] !== undefined ? parseInt(m[3], 10) : 3;
    return n + (oct + 1) * 12;
  }
  const mtof = (m) => 440 * Math.pow(2, (m - 69) / 12);

  // Accordo "Am" / "C" / "Bb" / "F#m7" / "Bbmaj7" / "Gdim" → triade di
  // frequenze. Riconosce le sigle di qualità (maj/min/m/dim/aug); le
  // estensioni jazz (7, sus4, 9...) vengono ignorate, resta la triade.
  function chordFreqs(name) {
    const m = /^([a-g][#b]?)(maj|min|dim|aug|m)?/i.exec(name || '');
    if (!m) return [];
    const root = noteToMidi(m[1] + '3');
    if (root == null) return [];
    const q = (m[2] || '').toLowerCase();
    const minor = q === 'm' || q === 'min' || q === 'dim';
    const third = minor ? 3 : 4;
    const fifth = q === 'dim' ? 6 : (q === 'aug' ? 8 : 7);
    return [root, root + third, root + fifth].map(mtof);
  }

  // ── Mood: ogni mood è una "tune" fatta di pattern ──────────────────
  // pad/bass/lead sono frasi di 4 BATTUTE: "<[bar1] [bar2] [bar3] [bar4]>"
  // suona una battuta per ciclo, così il lead è una melodia compiuta che
  // si ripete ogni 4 cicli invece di un frammento ribattuto.
  const TUNES = {
    menu: {
      bpm: 50, wave: 'sine', cutoff: 680, gain: 0.50,
      pad:  '<C C Am F>',
      bass: '<[c2 ~ g1 ~] [c2 ~ g1 ~] [a1 ~ e2 ~] [f1 ~ c2 ~]>',
      lead: '<[e4 g4 c5 ~] [d5 c5 g4 e4] [c4 e4 a4 ~] [g4 e4 d4 ~]>',
      drum: '',
    },
    generation: {
      bpm: 54, wave: 'triangle', cutoff: 640, gain: 0.55,
      pad:  '<Dm Dm Bb Gm>',
      bass: '<[d2 ~ a1 ~] [d2 ~ a1 ~] [bb1 ~ f2 ~] [g1 ~ d2 ~]>',
      lead: '<[d4 f4 a4 ~] [a4 g4 f4 d4] [bb4 d5 f5 ~] [d5 bb4 g4 ~]>',
      drum: '',
    },
    explore: {
      bpm: 58, wave: 'triangle', cutoff: 820, gain: 0.62,
      pad:  '<Am Am Dm F>',
      bass: '<[a1 ~ e2 ~] [a1 ~ e2 ~] [d2 ~ a1 ~] [f1 ~ c2 ~]>',
      lead: '<[a4 c5 b4 a4] [e4 g4 a4 ~] [d5 c5 a4 f4] [c5 a4 e4 ~]>',
      drum: '',
    },
    social: {
      bpm: 78, wave: 'triangle', cutoff: 1150, gain: 0.56,
      pad:  '<C G Am F>',
      bass: '<[c2 ~ g1 g1] [g1 ~ d2 d2] [a1 ~ e2 e2] [f1 ~ c2 c2]>',
      lead: '<[e4 g4 c5 e5] [d5 b4 g4 d4] [e4 a4 c5 a4] [c5 a4 f4 ~]>',
      drum: '~ hh ~ hh',
    },
    combat: {
      bpm: 126, wave: 'sawtooth', cutoff: 1500, gain: 0.74,
      pad:  '<Em Em Cm Em>',
      bass: '<[e1 e1 e1 e1] [e1 e1 g1 e1] [c2 c2 c2 c2] [e1 e1 b1 e1]>',
      lead: '<[e4 g4 b4 e5] [d5 b4 g4 b4] [c5 eb5 g5 eb5] [b4 g4 e4 ~]>',
      drum: '[bd bd] hh sd hh',
    },
  };

  // Livello di mix globale della musica. Più alto = colonna sonora più
  // presente. Spinto bene oltre 1: il limiter finale (DynamicsCompressor)
  // assorbe i picchi, quindi più volume senza distorsione/clipping.
  const MIX = 1.9;

  const Music = {
    ctx: null, master: null, padBus: null, limiter: null,
    enabled: false, playing: false, mood: 'menu',
    _timer: null, _cycle: 0, _cycleTime: 0, _pat: {}, _tuneSig: {},

    // Volume utente 0..1 (default 1.0), persistito in localStorage.
    userVolume: (function () {
      const v = parseFloat(localStorage.getItem('music_volume'));
      return isNaN(v) ? 1.0 : Math.max(0, Math.min(1, v));
    })(),

    _ensure() {
      if (this.ctx) return;
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      this.ctx = new AC();

      // limiter finale: tiene a bada i picchi così MIX può spingere
      // forte il volume senza clipping/distorsione. Ultimo nodo prima
      // dell'uscita: ci passa TUTTO (master asciutto + riverbero).
      this.limiter = this.ctx.createDynamicsCompressor();
      this.limiter.threshold.value = -8;
      this.limiter.knee.value = 6;
      this.limiter.ratio.value = 16;
      this.limiter.attack.value = 0.003;
      this.limiter.release.value = 0.18;
      this.limiter.connect(this.ctx.destination);

      this.master = this.ctx.createGain();
      this.master.gain.value = 0;
      this.master.connect(this.limiter);

      // riverbero a convoluzione (impulso sintetico) per dare spazio
      const verb = this.ctx.createConvolver();
      verb.buffer = this._impulse(2.6, 2.4);
      const verbGain = this.ctx.createGain();
      verbGain.gain.value = 0.34;
      this.master.connect(verbGain);
      verbGain.connect(verb);
      verb.connect(this.limiter);

      // bus della pad: lowpass con LFO lento ("respiro")
      this.padBus = this.ctx.createBiquadFilter();
      this.padBus.type = 'lowpass';
      this.padBus.frequency.value = 800;
      this.padBus.Q.value = 5;
      this.padBus.connect(this.master);
      const lfo = this.ctx.createOscillator();
      const lfoG = this.ctx.createGain();
      lfo.frequency.value = 0.07;
      lfoG.gain.value = 240;
      lfo.connect(lfoG);
      lfoG.connect(this.padBus.frequency);
      lfo.start();
    },

    _impulse(dur, decay) {
      const rate = this.ctx.sampleRate;
      const len = Math.floor(rate * dur);
      const buf = this.ctx.createBuffer(2, len, rate);
      for (let c = 0; c < 2; c++) {
        const d = buf.getChannelData(c);
        for (let i = 0; i < len; i++) {
          d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, decay);
        }
      }
      return buf;
    },

    // Compila (e cache) i pattern di una tune.
    _patterns(tune) {
      if (!this._pat[tune]) {
        const T = TUNES[tune];
        this._pat[tune] = {
          pad:  pattern(T.pad),
          bass: pattern(T.bass),
          lead: pattern(T.lead),
          drum: pattern(T.drum),
        };
      }
      return this._pat[tune];
    },

    // ── Controllo ──
    toggle() {
      this._ensure();
      if (!this.ctx) return false;
      if (this.ctx.state === 'suspended') this.ctx.resume();
      this.enabled = !this.enabled;
      if (this.enabled) this._start();
      else this._stop();
      try { localStorage.setItem('music_on', this.enabled ? '1' : '0'); } catch (e) {}
      return this.enabled;
    },

    _start() {
      if (this.playing || !this.ctx) return;
      this.playing = true;
      this._cycle = 0;
      this._cycleTime = this.ctx.currentTime + 0.15;
      this.master.gain.cancelScheduledValues(this.ctx.currentTime);
      this.master.gain.setTargetAtTime(
        TUNES[this.mood].gain * MIX * this.userVolume, this.ctx.currentTime, 1.1);
      this._timer = setInterval(() => this._tick(), 25);
    },

    _stop() {
      this.playing = false;
      if (this._timer) clearInterval(this._timer);
      this._timer = null;
      if (this.master && this.ctx) {
        this.master.gain.setTargetAtTime(0, this.ctx.currentTime, 0.7);
      }
    },

    setMood(m) {
      if (!TUNES[m] || m === this.mood) return;
      this.mood = m;
      if (this.playing && this.ctx) {
        this.master.gain.setTargetAtTime(
          TUNES[m].gain * MIX * this.userVolume, this.ctx.currentTime, 1.3);
      }
    },

    // Volume utente 0..1. Applicato live se la musica sta suonando.
    setVolume(v) {
      v = Math.max(0, Math.min(1, Number(v)));
      if (isNaN(v)) v = 0;
      this.userVolume = v;
      try { localStorage.setItem('music_volume', String(v)); } catch (e) {}
      if (this.playing && this.ctx && this.master) {
        this.master.gain.setTargetAtTime(
          TUNES[this.mood].gain * MIX * v, this.ctx.currentTime, 0.2);
      }
    },

    // Registra/aggiorna una "tune" generata dal DM per uno slot mood.
    // I campi mancanti ereditano dal mood base. Idempotente per contenuto.
    registerTune(name, def) {
      if (!name || !def || typeof def !== 'object') return;
      const sig = JSON.stringify(def);
      if (this._tuneSig[name] === sig) return;   // identica: niente da fare
      this._tuneSig[name] = sig;
      const base = TUNES[name] || TUNES.explore;
      const num = (x, d) => { const n = Number(x); return isNaN(n) ? d : n; };
      const str = (x, d) => (x == null ? d : String(x));
      TUNES[name] = {
        bpm:    Math.max(40, Math.min(160, num(def.bpm, base.bpm))),
        wave:   ['sine', 'triangle', 'sawtooth', 'square'].includes(def.wave)
                  ? def.wave : base.wave,
        cutoff: Math.max(300, Math.min(2000, num(def.cutoff, base.cutoff))),
        gain:   Math.max(0.3, Math.min(0.9, num(def.gain, base.gain))),
        pad:    str(def.pad,  base.pad),
        bass:   str(def.bass, base.bass),
        lead:   str(def.lead, base.lead),
        drum:   str(def.drum, base.drum),
      };
      delete this._pat[name];   // forza ricompilazione dei pattern
      if (this.playing && this.ctx && name === this.mood) {
        this.master.gain.setTargetAtTime(
          TUNES[name].gain * MIX * this.userVolume, this.ctx.currentTime, 0.6);
      }
    },

    _moodFor(gs) {
      if (!gs) return 'menu';
      if (gs.combat_active) return 'combat';
      const ph = gs.phase;
      if (ph === 'setup' || ph === 'registration' || ph === 'character_creation') {
        return 'menu';
      }
      if (ph === 'adventure_generation') return 'generation';
      const zone = (gs.current_zone || '').toLowerCase();
      if (/social|villag|tavern|locand|mercat|incontro/.test(zone)) return 'social';
      return 'explore';
    },

    applyState(gs) {
      // colonne sonore generate dal DM: registra/aggiorna gli slot mood
      if (gs && gs.music && typeof gs.music === 'object') {
        for (const mood in gs.music) this.registerTune(mood, gs.music[mood]);
      }
      this.setMood(this._moodFor(gs));
    },

    // ── Scheduler ciclico (look-ahead, pattern Strudel-like) ──────────
    _tick() {
      if (!this.playing || !this.ctx) return;
      while (this._cycleTime < this.ctx.currentTime + 0.3) {
        const T = TUNES[this.mood];
        const cycDur = 240 / T.bpm;          // 1 ciclo = 1 battuta (4 movimenti)
        this._scheduleCycle(this._cycle, this._cycleTime, cycDur, T);
        this._cycle++;
        this._cycleTime += cycDur;
      }
    },

    _scheduleCycle(cyc, t0, cycDur, T) {
      const P = this._patterns(this.mood);
      for (const ev of P.pad.query(cyc)) {
        this._chord(t0 + ev.start * cycDur, ev.v, ev.dur * cycDur);
      }
      for (const ev of P.bass.query(cyc)) {
        this._note(t0 + ev.start * cycDur, ev.v, ev.dur * cycDur, T, true);
      }
      for (const ev of P.lead.query(cyc)) {
        this._note(t0 + ev.start * cycDur, ev.v, ev.dur * cycDur, T, false);
      }
      for (const ev of P.drum.query(cyc)) {
        this._drum(t0 + ev.start * cycDur, ev.v);
      }
    },

    // Accordo sostenuto (pad): triade attraverso il bus filtrato.
    _chord(t, name, dur) {
      const freqs = chordFreqs(name);
      const rel = 0.9;
      for (const f of freqs) {
        const o = this.ctx.createOscillator();
        const g = this.ctx.createGain();
        o.type = 'sine';
        o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.075, t + 0.4);
        g.gain.setValueAtTime(0.075, t + dur);
        g.gain.exponentialRampToValueAtTime(0.0001, t + dur + rel);
        o.connect(g);
        g.connect(this.padBus);
        o.start(t);
        o.stop(t + dur + rel + 0.1);
      }
    },

    // Nota singola: basso (sostenuto) o lead (pizzicato).
    _note(t, name, dur, T, isBass) {
      const midi = noteToMidi(name);
      if (midi == null) return;
      const o = this.ctx.createOscillator();
      const g = this.ctx.createGain();
      const f = this.ctx.createBiquadFilter();
      o.type = isBass ? 'triangle' : T.wave;
      o.frequency.value = mtof(midi);
      f.type = 'lowpass';
      f.frequency.value = isBass ? 500 : T.cutoff;
      const peak = isBass ? 0.19 : 0.12;
      // lead: durata proporzionale allo slot → frasi legate quando le
      // note sono lunghe, staccato quando sono fitte (combat).
      const len = isBass ? Math.min(dur * 0.95, 1.1)
                         : Math.max(0.16, Math.min(dur * 0.9, 0.7));
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(peak, t + (isBass ? 0.05 : 0.02));
      g.gain.exponentialRampToValueAtTime(0.0001, t + len);
      o.connect(f);
      f.connect(g);
      g.connect(this.master);
      o.start(t);
      o.stop(t + len + 0.05);
    },

    // Percussione: bd (cassa), sd (rullante), hh (charleston).
    _drum(t, type) {
      if (type === 'bd') {
        const o = this.ctx.createOscillator();
        const g = this.ctx.createGain();
        o.type = 'sine';
        o.frequency.setValueAtTime(150, t);
        o.frequency.exponentialRampToValueAtTime(46, t + 0.12);
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.30, t + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.28);
        o.connect(g);
        g.connect(this.master);
        o.start(t);
        o.stop(t + 0.3);
        return;
      }
      // sd / hh: rumore filtrato
      const dur = type === 'sd' ? 0.2 : 0.06;
      const rate = this.ctx.sampleRate;
      const buf = this.ctx.createBuffer(1, Math.floor(rate * dur), rate);
      const d = buf.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      const f = this.ctx.createBiquadFilter();
      f.type = type === 'sd' ? 'bandpass' : 'highpass';
      f.frequency.value = type === 'sd' ? 1900 : 7600;
      const g = this.ctx.createGain();
      const peak = type === 'sd' ? 0.20 : 0.10;
      g.gain.setValueAtTime(peak, t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      src.connect(f);
      f.connect(g);
      g.connect(this.master);
      src.start(t);
      src.stop(t + dur + 0.02);
    },
  };

  window.Music = Music;
})();
