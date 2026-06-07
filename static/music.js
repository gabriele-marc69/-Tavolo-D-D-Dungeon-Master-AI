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

  // Accordo "Am" / "C" / "Bb" / "F#m7" / "Bbmaj7" / "Gdim" / "Dsus4" →
  // accordo di frequenze. Riconosce le sigle di qualità (maj/min/m/dim/
  // aug) e le estensioni più utili (7 dominante, maj7, sus4): danno
  // colore alla pad senza spendere troppa banda audio.
  function chordFreqs(name) {
    const m = /^([a-g][#b]?)(maj7|maj|min|dim|aug|sus4|m7|m|7)?/i.exec(name || '');
    if (!m) return [];
    const root = noteToMidi(m[1] + '3');
    if (root == null) return [];
    const q = (m[2] || '').toLowerCase();
    const minor = q === 'm' || q === 'min' || q === 'dim' || q === 'm7';
    let third = minor ? 3 : 4;
    let fifth = q === 'dim' ? 6 : (q === 'aug' ? 8 : 7);
    const notes = [root, root + third, root + fifth];
    // sus4: sostituisce la terza con la quarta giusta (5 semitoni)
    if (q === 'sus4') notes[1] = root + 5;
    // estensioni 7 / m7 / maj7: aggiungono la settima dell'accordo
    if (q === '7')     notes.push(root + 10);   // dominante (bb7)
    else if (q === 'm7')   notes.push(root + 10);
    else if (q === 'maj7') notes.push(root + 11);
    return notes.map(mtof);
  }

  // ── Mood: ogni mood è una "tune" fatta di pattern ──────────────────
  // DIECI CANALI per mood: 5 MELODICI (pad/bass/lead/arp/pluck) + 5 RITMICI
  // (kick/snare/hats/clap/tom), ognuno un canale audio indipendente.
  //   • MELODICI — frasi di 4 BATTUTE "<[bar1] [bar2] [bar3] [bar4]>":
  //     una battuta per ciclo, così il lead è una melodia compiuta che si
  //     ripete ogni 4 cicli invece di un frammento ribattuto. pad=accordi
  //     tenuti, bass=fondamentali, lead=melodia, arp=arpeggio brillante
  //     (un'ottava sopra), pluck=contromelodia pizzicata.
  //   • RITMICI — UNA battuta da 4 movimenti: "x" = colpo, "X" = accento
  //     (più forte), "~" = pausa. kick→cassa, snare→rullante, hats→charleston,
  //     clap→battito di mani, tom→tamburo intonato. Ogni canale percussivo ha
  //     il suo pattern, così la batteria è un vero impasto di strumenti.
  const TUNES = {
    menu: {
      bpm: 50, wave: 'sine', cutoff: 680, gain: 0.50,
      pad:  '<C C Am F>',
      bass: '<[c2 ~ g1 ~] [c2 ~ g1 ~] [a1 ~ e2 ~] [f1 ~ c2 ~]>',
      lead: '<[e4 g4 c5 ~] [d5 c5 g4 e4] [c4 e4 a4 ~] [g4 e4 d4 ~]>',
      arp:   '<[c5 e5 g5 e5] [c5 e5 g5 e5] [a4 c5 e5 c5] [f4 a4 c5 a4]>',
      pluck: '<[c4 ~ e4 ~] [g3 ~ c4 ~] [a3 ~ c4 ~] [f3 ~ a3 ~]>',
      kick:  'x ~ ~ ~',
      snare: '~ ~ ~ ~',
      hats:  '~ ~ x ~',
      clap:  '~ ~ ~ ~',
      tom:   '~ ~ ~ ~',
    },
    generation: {
      bpm: 54, wave: 'triangle', cutoff: 640, gain: 0.55,
      pad:  '<Dm Dm Bb Gm>',
      bass: '<[d2 ~ a1 ~] [d2 ~ a1 ~] [bb1 ~ f2 ~] [g1 ~ d2 ~]>',
      lead: '<[d4 f4 a4 ~] [a4 g4 f4 d4] [bb4 d5 f5 ~] [d5 bb4 g4 ~]>',
      arp:   '<[d5 f5 a5 f5] [d5 f5 a5 f5] [bb4 d5 f5 d5] [g4 bb4 d5 bb4]>',
      pluck: '<[d4 ~ a4 ~] [d4 ~ a4 ~] [bb3 ~ f4 ~] [g3 ~ d4 ~]>',
      kick:  'x ~ ~ x',
      snare: '~ ~ ~ ~',
      hats:  '~ x ~ x',
      clap:  '~ ~ ~ ~',
      tom:   '~ ~ ~ x',
    },
    explore: {
      bpm: 58, wave: 'triangle', cutoff: 820, gain: 0.62,
      pad:  '<Am Am Dm F>',
      bass: '<[a1 ~ e2 ~] [a1 ~ e2 ~] [d2 ~ a1 ~] [f1 ~ c2 ~]>',
      lead: '<[a4 c5 b4 a4] [e4 g4 a4 ~] [d5 c5 a4 f4] [c5 a4 e4 ~]>',
      arp:   '<[a4 c5 e5 c5] [a4 c5 e5 c5] [d5 f5 a5 f5] [c5 f5 a5 f5]>',
      pluck: '<[a3 ~ e4 ~] [a3 ~ e4 ~] [d4 ~ a4 ~] [c4 ~ a4 ~]>',
      kick:  'x ~ ~ ~',
      snare: '~ ~ ~ ~',
      hats:  '~ x ~ x',
      clap:  '~ ~ x ~',
      tom:   '~ x ~ ~',
    },
    social: {
      bpm: 78, wave: 'triangle', cutoff: 1150, gain: 0.56,
      pad:  '<C G Am F>',
      bass: '<[c2 ~ g1 g1] [g1 ~ d2 d2] [a1 ~ e2 e2] [f1 ~ c2 c2]>',
      lead: '<[e4 g4 c5 e5] [d5 b4 g4 d4] [e4 a4 c5 a4] [c5 a4 f4 ~]>',
      arp:   '<[c5 e5 g5 e5] [g4 b4 d5 b4] [a4 c5 e5 c5] [f4 a4 c5 a4]>',
      pluck: '<[c4 e4 ~ g4] [b3 d4 ~ g4] [a3 c4 ~ e4] [f3 a3 ~ c4]>',
      kick:  'X ~ x ~',
      snare: '~ ~ X ~',
      hats:  'x x x x',
      clap:  '~ x ~ x',
      tom:   '~ ~ x ~',
    },
    combat: {
      bpm: 126, wave: 'sawtooth', cutoff: 1500, gain: 0.74,
      pad:  '<Em Em Cm Em>',
      bass: '<[e1 e1 e1 e1] [e1 e1 g1 e1] [c2 c2 c2 c2] [e1 e1 b1 e1]>',
      lead: '<[e4 g4 b4 e5] [d5 b4 g4 b4] [c5 eb5 g5 eb5] [b4 g4 e4 ~]>',
      arp:   '<[e5 g5 b5 g5] [e5 g5 b5 g5] [c5 eb5 g5 eb5] [e5 g5 b5 g5]>',
      pluck: '<[e4 b4 e5 b4] [e4 b4 e5 b4] [c5 g5 c5 g4] [e4 b4 e5 b4]>',
      kick:  'X ~ x x',
      snare: '~ X ~ X',
      hats:  'x x x x',
      clap:  'x ~ x ~',
      tom:   'x ~ x x',
    },
    boss: {
      bpm: 144, wave: 'sawtooth', cutoff: 1700, gain: 0.82,
      pad:  '<Dm Bb F C>',
      bass: '<[d1 d1 a1 d1] [bb1 bb1 f1 bb1] [f1 f1 c2 f1] [c2 c2 g1 c2]>',
      lead: '<[d5 f5 a5 f5] [bb4 d5 f5 d5] [c5 f5 a5 c5] [a4 c5 e5 a5]>',
      arp:   '<[d5 f5 a5 d6] [bb4 d5 f5 bb5] [f4 a4 c5 f5] [c5 e5 g5 c6]>',
      pluck: '<[d4 a4 d5 a4] [bb3 f4 bb4 f4] [f3 c4 f4 c4] [c4 g4 c5 g4]>',
      kick:  'X x x x',
      snare: '~ X ~ X',
      hats:  'x x [x x] x',
      clap:  'x ~ x ~',
      tom:   'x x ~ x',
    },
  };

  // Canali ritmici: nome del pattern → strumento percussivo da suonare.
  const RHYTHM_CHANNELS = [
    ['kick',  'bd'],
    ['snare', 'sd'],
    ['hats',  'hh'],
    ['clap',  'cp'],
    ['tom',   'tm'],
  ];

  // Canali melodici pizzicati extra: nome del pattern → flag "brillante".
  // arp = arpeggio acuto (un'ottava sopra), pluck = contromelodia morbida.
  const PLUCK_CHANNELS = [
    ['arp',   true],
    ['pluck', false],
  ];

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
      // Early reflections discrete (10-90ms) → senso di spazio fisico,
      // poi coda diffusa con decay esponenziale. Più "stanza in pietra"
      // del rumore bianco filtrato.
      const earlyTimes = [0.012, 0.025, 0.041, 0.063, 0.084, 0.108];
      const earlyGains = [0.60, 0.45, 0.36, 0.28, 0.20, 0.14];
      for (let c = 0; c < 2; c++) {
        const d = buf.getChannelData(c);
        // coda diffusa (decay esponenziale)
        for (let i = 0; i < len; i++) {
          const t = i / len;
          d[i] = (Math.random() * 2 - 1) * Math.pow(1 - t, decay) * 0.55;
        }
        // sovrappone le early reflections (un canale leggermente
        // spostato per dare ampiezza stereo)
        const offset = c === 0 ? 0 : 0.004;
        for (let k = 0; k < earlyTimes.length; k++) {
          const idx = Math.floor((earlyTimes[k] + offset) * rate);
          if (idx >= 0 && idx < len) d[idx] += earlyGains[k];
        }
      }
      return buf;
    },

    // Compila (e cache) i pattern di una tune: 5 melodici + 5 ritmici.
    _patterns(tune) {
      if (!this._pat[tune]) {
        const T = TUNES[tune];
        this._pat[tune] = {
          pad:   pattern(T.pad),
          bass:  pattern(T.bass),
          lead:  pattern(T.lead),
          arp:   pattern(T.arp   || ''),
          pluck: pattern(T.pluck || ''),
          kick:  pattern(T.kick  || ''),
          snare: pattern(T.snare || ''),
          hats:  pattern(T.hats  || ''),
          clap:  pattern(T.clap  || ''),
          tom:   pattern(T.tom   || ''),
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
        pad:    str(def.pad,   base.pad),
        bass:   str(def.bass,  base.bass),
        lead:   str(def.lead,  base.lead),
        arp:    str(def.arp,   base.arp),
        pluck:  str(def.pluck, base.pluck),
        kick:   str(def.kick,  base.kick),
        snare:  str(def.snare, base.snare),
        hats:   str(def.hats,  base.hats),
        clap:   str(def.clap,  base.clap),
        tom:    str(def.tom,   base.tom),
      };
      delete this._pat[name];   // forza ricompilazione dei pattern
      if (this.playing && this.ctx && name === this.mood) {
        this.master.gain.setTargetAtTime(
          TUNES[name].gain * MIX * this.userVolume, this.ctx.currentTime, 0.6);
      }
    },

    _moodFor(gs) {
      if (!gs) return 'menu';
      if (gs.combat_active) {
        // boss in scena: presenza di 'B' o 'D' sulla mappa, oppure
        // "boss" nel nome della zona corrente. Sostituisce combat con
        // boss → musica più epica.
        const m = gs.map_ascii || '';
        const zone = (gs.current_zone || '').toLowerCase();
        if (/[BD]/.test(m) || /boss|drago|dragon|sovran/.test(zone)) {
          return 'boss';
        }
        return 'combat';
      }
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
      // Lead: swing dolce sulle note off-beat (offset 0.5 nel ciclo).
      // Ritarda leggermente le note che non cadono sul beat principale →
      // groove che respira invece di metrica metronomica piatta.
      for (const ev of P.lead.query(cyc)) {
        const onBeat = Math.abs(ev.start * 8 - Math.round(ev.start * 8)) < 1e-3
                       && Math.round(ev.start * 8) % 2 === 0;
        const swing = onBeat ? 0 : cycDur * 0.035;
        this._note(t0 + ev.start * cycDur + swing, ev.v,
                   ev.dur * cycDur, T, false);
      }
      // Melodici pizzicati: arp (arpeggio brillante un'ottava sopra) e
      // pluck (contromelodia morbida). Stesso swing dolce off-beat del lead.
      for (const [chan, bright] of PLUCK_CHANNELS) {
        for (const ev of P[chan].query(cyc)) {
          const onBeat = Math.abs(ev.start * 8 - Math.round(ev.start * 8)) < 1e-3
                         && Math.round(ev.start * 8) % 2 === 0;
          const swing = onBeat ? 0 : cycDur * 0.03;
          this._pluck(t0 + ev.start * cycDur + swing, ev.v,
                      ev.dur * cycDur, T, bright);
        }
      }
      // Ritmica: 5 canali indipendenti (kick/snare/hats/clap/tom), ognuno col suo
      // pattern. Swing dolce sui colpi off-beat (la cassa resta sul beat).
      // Un atomo "X" = accento (colpo più forte).
      for (const [chan, snd] of RHYTHM_CHANNELS) {
        for (const ev of P[chan].query(cyc)) {
          const onBeat = Math.abs(ev.start * 4 - Math.round(ev.start * 4)) < 1e-3;
          const swing = (onBeat || snd === 'bd') ? 0 : cycDur * 0.025;
          const accent = ev.v === 'X' || ev.v === 'A';
          this._drum(t0 + ev.start * cycDur + swing, snd, accent);
        }
      }
    },

    // Accordo sostenuto (pad): voci raddoppiate con detune leggero per
    // ottenere un timbro più ricco e "respirante" (chorus naturale)
    // invece di onde piatte sovrapposte. La voce dispari è leggermente
    // più acuta, la pari leggermente più grave → battimenti morbidi.
    _chord(t, name, dur) {
      const freqs = chordFreqs(name);
      const rel = 0.9;
      // gain per voce inversamente proporzionale al numero di note: un
      // maj7 (4 note) non deve saturare il bus pad rispetto a una triade.
      const voiceGain = 0.085 * Math.pow(3 / Math.max(3, freqs.length), 0.5);
      for (let i = 0; i < freqs.length; i++) {
        const f = freqs[i];
        // 2 oscillatori detunati per voce → corpo più pieno
        for (const detuneCents of [-6, +6]) {
          const o = this.ctx.createOscillator();
          const g = this.ctx.createGain();
          o.type = 'sine';
          o.frequency.value = f;
          o.detune.value = detuneCents;
          g.gain.setValueAtTime(0.0001, t);
          g.gain.exponentialRampToValueAtTime(voiceGain * 0.6, t + 0.4);
          g.gain.setValueAtTime(voiceGain * 0.6, t + dur);
          g.gain.exponentialRampToValueAtTime(0.0001, t + dur + rel);
          o.connect(g);
          g.connect(this.padBus);
          o.start(t);
          o.stop(t + dur + rel + 0.1);
        }
      }
    },

    // Nota singola: basso (sostenuto) o lead (pizzicato con vibrato).
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
      // ADSR migliorato: attack veloce, piccolo decay, sustain, release
      // morbida — invece di salire-e-scendere lineare. Dà più "tocco".
      const atk = isBass ? 0.05 : 0.02;
      const dec = isBass ? 0.12 : 0.07;
      const sus = peak * (isBass ? 0.75 : 0.55);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(peak, t + atk);
      g.gain.exponentialRampToValueAtTime(Math.max(0.001, sus),
                                          t + atk + dec);
      g.gain.exponentialRampToValueAtTime(0.0001, t + len);
      o.connect(f);
      f.connect(g);
      g.connect(this.master);
      // Lead: vibrato lieve (5Hz, ±8 cent) sulle note tenute ≥0.25s.
      // Non sul basso (vibrato sul basso impasta il low-end).
      if (!isBass && len >= 0.25) {
        const lfo = this.ctx.createOscillator();
        const lfoG = this.ctx.createGain();
        lfo.frequency.value = 5;
        lfoG.gain.value = 8;        // ampiezza in cent
        lfo.connect(lfoG);
        lfoG.connect(o.detune);
        lfo.start(t + 0.12);        // entra dopo l'attacco
        lfo.stop(t + len + 0.05);
      }
      o.start(t);
      o.stop(t + len + 0.05);
    },

    // Nota pizzicata: arp (brillante, un'ottava sopra, staccato) o pluck
    // (morbida, contromelodia). Attacco rapido + decay corto → carattere
    // "pizzicato" che riempie i vuoti senza coprire il lead.
    _pluck(t, name, dur, T, bright) {
      const midi = noteToMidi(name);
      if (midi == null) return;
      const o = this.ctx.createOscillator();
      const g = this.ctx.createGain();
      const f = this.ctx.createBiquadFilter();
      o.type = bright ? 'square' : 'triangle';
      o.frequency.value = mtof(midi + (bright ? 12 : 0));   // arp: +1 ottava
      f.type = 'lowpass';
      f.frequency.value = bright ? Math.min(2400, T.cutoff * 1.4) : T.cutoff;
      const peak = bright ? 0.06 : 0.085;
      const len = Math.max(0.08, Math.min(dur * 0.8, bright ? 0.22 : 0.45));
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(peak, t + 0.008);
      g.gain.exponentialRampToValueAtTime(0.0001, t + len);
      o.connect(f);
      f.connect(g);
      g.connect(this.master);
      o.start(t);
      o.stop(t + len + 0.05);
    },

    // Percussione: bd (cassa), sd (rullante), hh (charleston), cp (battito
    // di mani), tm (tamburo intonato).
    // `accent` (colpo "X") rinforza il volume del colpo del ~30%.
    _drum(t, type, accent) {
      const amp = accent ? 1.3 : 1.0;
      if (type === 'bd') {
        const o = this.ctx.createOscillator();
        const g = this.ctx.createGain();
        o.type = 'sine';
        o.frequency.setValueAtTime(150, t);
        o.frequency.exponentialRampToValueAtTime(46, t + 0.12);
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.30 * amp, t + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.28);
        o.connect(g);
        g.connect(this.master);
        o.start(t);
        o.stop(t + 0.3);
        return;
      }
      if (type === 'tm') {
        // tom intonato: sweep di sine medio-grave, più lungo della cassa.
        const o = this.ctx.createOscillator();
        const g = this.ctx.createGain();
        o.type = 'sine';
        o.frequency.setValueAtTime(220, t);
        o.frequency.exponentialRampToValueAtTime(92, t + 0.18);
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.22 * amp, t + 0.01);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.3);
        o.connect(g);
        g.connect(this.master);
        o.start(t);
        o.stop(t + 0.32);
        return;
      }
      if (type === 'cp') {
        // clap: tre brevi raffiche di rumore bandpass ravvicinate → il
        // caratteristico "battito di mani" invece di un colpo secco.
        const rate = this.ctx.sampleRate;
        for (const off of [0, 0.012, 0.024]) {
          const cd = 0.12;
          const buf = this.ctx.createBuffer(1, Math.floor(rate * cd), rate);
          const d = buf.getChannelData(0);
          for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
          const src = this.ctx.createBufferSource();
          src.buffer = buf;
          const f = this.ctx.createBiquadFilter();
          f.type = 'bandpass';
          f.frequency.value = 1200;
          f.Q.value = 1.2;
          const g = this.ctx.createGain();
          g.gain.setValueAtTime(0.13 * amp, t + off);
          g.gain.exponentialRampToValueAtTime(0.0001, t + off + cd);
          src.connect(f);
          f.connect(g);
          g.connect(this.master);
          src.start(t + off);
          src.stop(t + off + cd + 0.02);
        }
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
      const peak = (type === 'sd' ? 0.20 : 0.10) * amp;
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
