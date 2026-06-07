# -*- coding: utf-8 -*-
"""
verifica_mappa.py — verifica della pipeline di scrittura mappa.

Esercita l'intero percorso usato da app.py quando arriva una risposta del DM:

    extract_map  ->  normalize_map  ->  compose_map
                 ->  find_position  ->  check_map_coherence
                 ->  reveal_los     ->  apply_fog

Per ogni fase controlla un invariante e stampa PASS/FAIL. Exit code 0 se
tutto verde, 1 se almeno un controllo fallisce. Nessuna dipendenza esterna,
nessuna scrittura su disco: si appoggia solo a dm/parser.py e dnd/state.py.

Uso:
    python verifica_mappa.py
"""
import sys

from dm import parser
from dnd import state as st

# Contatori globali dei controlli.
_pass = 0
_fail = 0


def check(nome: str, condizione: bool, dettaglio: str = "") -> None:
    """Registra e stampa l'esito di un singolo invariante."""
    global _pass, _fail
    if condizione:
        _pass += 1
        print(f"  [PASS] {nome}")
    else:
        _fail += 1
        print(f"  [FAIL] {nome}" + (f" -> {dettaglio}" if dettaglio else ""))


# Risposta del DM tipo: narrazione + blocco mappa canonico MAP_START..MAP_END.
# Glifi: # muro, . pavimento, * partenza, X obiettivo, @ party, M mostro.
DM_RESPONSE = """\
Entrate nella cripta umida. Una torcia tremola in fondo al corridoio.

MAP_START
########
#*....M#
#.####.#
#.#  #.#
#.#..#.#
#....@.#
######X#
########
MAP_END

Cosa fate?
"""


def main() -> int:
    print("== Verifica scrittura mappa ==\n")

    # 1) ESTRAZIONE — extract_map deve trovare il blocco MAP_START..MAP_END
    #    e restituire solo l'interno, niente narrazione.
    print("1) extract_map")
    blocco = parser.extract_map(DM_RESPONSE)
    check("blocco estratto non nullo", blocco is not None)
    check("narrazione esclusa dal blocco",
          blocco is not None and "Entrate nella cripta" not in blocco)
    check("marker partenza '*' presente", bool(blocco) and "*" in blocco)
    check("marker obiettivo 'X' presente", bool(blocco) and "X" in blocco)
    if blocco is None:
        print("\nEstrazione fallita: impossibile proseguire.")
        return 1

    # 2) NORMALIZZAZIONE — griglia SEMPRE rettangolare bw x bh.
    print("\n2) normalize_map (griglia rettangolare)")
    bw, bh = st.measure_map(blocco)
    norm = st.normalize_map(blocco, bw, bh)
    righe = norm.splitlines()
    check("altezza == bh", len(righe) == bh, f"{len(righe)} != {bh}")
    check("tutte le righe larghe bw",
          all(len(r) == bw for r in righe),
          f"larghezze={[len(r) for r in righe]} attesa={bw}")

    # 3) POSIZIONE PARTY — find_position trova '@', e va rimosso dal full.
    print("\n3) find_position / pulizia @")
    at = st.find_position(norm, "@")
    check("posizione @ trovata", at is not None, str(at))
    norm_no_at = norm.replace("@", ".")
    check("@ rimosso da map_full", "@" not in norm_no_at)

    # 4) COMPOSIZIONE — compose_map protegge i muri della base (immutabili):
    #    se una mappa nuova "buca" un muro della base, il muro resta.
    print("\n4) compose_map (muri base immutabili)")
    base = norm_no_at
    # Mappa nuova identica ma con un muro del perimetro trasformato in
    # pavimento: compose deve ripristinarlo a muro.
    bucata = base.replace("########\n#*", "#.#.#.#.\n#*", 1)
    composta = st.compose_map(base, bucata)
    check("muro perimetrale ripristinato",
          composta.splitlines()[0] == base.splitlines()[0],
          f"{composta.splitlines()[0]!r}")
    # I tile non-muro della mappa nuova devono invece passare: spostiamo M.
    mossa = base.replace("M", ".", 1)
    composta2 = st.compose_map(base, mossa)
    check("tile non-muro aggiornabile (M rimosso)", "M" not in composta2)

    # 5) COERENZA — check_map_coherence: griglia valida, X raggiungibile da *.
    print("\n5) check_map_coherence")
    pos = [at[0], at[1]] if at else [1, 1]
    report = st.check_map_coherence(norm, pos)
    check("nessun carattere non valido",
          not any("non validi" in i for i in report["issues"]),
          str(report["issues"]))
    check("partenza '*' rilevata", report["start"] is not None)
    check("obiettivo 'X' rilevato", report["exit"] is not None)
    check("obiettivo raggiungibile dalla partenza", report["connected"],
          str(report["issues"]))

    # 6) FOG OF WAR — reveal_los rivela attorno al party; apply_fog nasconde
    #    il resto. Risultato: stessa dimensione, niente tile rivelate fuori.
    print("\n6) reveal_los / apply_fog")
    gs = st.empty_state()
    gs["map_full"] = norm_no_at
    gs["map_width"], gs["map_height"] = bw, bh
    st.reveal_los(gs, gs["map_full"], pos[0], pos[1], radius=10)
    rivelate = gs.get("revealed_tiles", [])
    check("almeno una tile rivelata", len(rivelate) > 0, str(len(rivelate)))
    fog = st.apply_fog(gs["map_full"], rivelate, party_pos=pos)
    fog_righe = fog.splitlines()
    check("fog mantiene l'altezza", len(fog_righe) == bh,
          f"{len(fog_righe)} != {bh}")
    check("party '@' ridisegnato dalla fog", "@" in fog)
    # La cella del party deve essere fra le rivelate.
    check("cella party rivelata", list(pos) in [list(t) for t in rivelate],
          f"pos={pos}")

    # 7) MAPPA ROTTA — check_map_coherence deve segnalare ok=False e popolare
    #    'issues' per ognuno dei difetti tipici. Specularmente al caso buono.
    print("\n7) check_map_coherence su mappe rotte (ok deve essere False)")

    # 7a) griglia vuota
    rep_vuota = st.check_map_coherence("", None)
    check("vuota -> ok False", rep_vuota["ok"] is False)
    check("vuota -> issue presente", len(rep_vuota["issues"]) > 0)

    # 7b) carattere non valido (z) in mezzo al pavimento
    rotta_char = norm.replace(".", "z", 1)
    rep_char = st.check_map_coherence(rotta_char, None)
    check("char non valido -> ok False", rep_char["ok"] is False,
          str(rep_char["issues"]))
    check("char non valido -> issue 'non validi'",
          any("non validi" in i for i in rep_char["issues"]))

    # 7c) obiettivo 'X' irraggiungibile: muriamo la cella accanto a X così
    #     il flood-fill dalla partenza non lo raggiunge. X in basso (riga 6),
    #     lo isoliamo trasformandone i vicini transitabili in muro.
    grezza = [list(r) for r in norm.splitlines()]
    for yy, row in enumerate(grezza):
        for xx, ch in enumerate(row):
            if ch == "X":
                # mura i 4 vicini ortogonali entro i bordi
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = xx + dx, yy + dy
                    if 0 <= ny < len(grezza) and 0 <= nx < len(grezza[ny]):
                        grezza[ny][nx] = "#"
    isolata = "\n".join("".join(r) for r in grezza)
    rep_iso = st.check_map_coherence(isolata, None)
    check("X isolato -> ok False", rep_iso["ok"] is False,
          str(rep_iso["issues"]))
    check("X isolato -> issue 'raggiungibile'",
          any("raggiungibile" in i for i in rep_iso["issues"]))

    # 7d) marker obiettivo 'X' mancante del tutto
    senza_x = norm.replace("X", ".")
    rep_nox = st.check_map_coherence(senza_x, None)
    check("X mancante -> ok False", rep_nox["ok"] is False)
    check("X mancante -> issue obiettivo",
          any("obiettivo" in i for i in rep_nox["issues"]))

    # ── ESITO
    print(f"\n== Risultato: {_pass} PASS, {_fail} FAIL ==")
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
