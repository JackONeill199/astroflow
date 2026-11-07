"""Historie für die Veröffentlichung bereinigen – in einem NEUEN Klon, das Quell-Repo bleibt unverändert.

Ablauf (Anleitung: docs/PUBLIC-RELEASE.md → „Historie“):
  1. `git clone --mirror <quelle>` zweimal: `alt.git` (unverändert, zum Vergleich) und `neu.git` (wird umgeschrieben).
  2. In `neu.git` nur `main` und die Tags `v*` behalten.
  3. `git filter-repo` mit
       --mailmap        jede Autor-/Committer-Adresse außer GitHub-noreply → <ID>+<login>@users.noreply.github.com
       --replace-text   Ersetzungen aus der lokalen Liste (Dateiinhalte, Textdateien)
       --replace-message dieselbe Liste + alte Adressen (Commit-Texte, Co-Authored-By …)
  4. Prüfen: keine Muster/alten Adressen mehr (Inhalte, Texte, Autoren), gleiche Commit-Zahl auf main, jedes Tag zeigt auf
     den umgeschriebenen Commit, und die Bäume von main und allen Tags sind bis auf die Ersetzungen gleich.
Gepusht wird NICHTS – das macht der Maintainer danach von Hand (`git push --mirror <neues-repo>` aus `neu.git`).

Die Ersetzungsliste enthält private Begriffe und gehört NIE ins Repo. Standard: `historie-ersetzungen.local.txt` im
Repo-Wurzelordner (per `.gitignore` ausgeschlossen). Format je Zeile, `#` = Kommentar:
    Art | muster ==> ersatz
    Art | regex:muster ==> ersatz          (Python-Regex auf Bytes, Rückverweise wie \\1 erlaubt)
„Art“ ist nur die Bezeichnung im Bericht (z. B. „Pfad“, „Heimnetz“) – der Bericht nennt nie das Muster selbst.

    python installer/historie_bereinigen.py --ziel <leerer Ordner> [--quelle URL|Pfad] [--liste Datei]
        [--noreply-id ID --login NAME] [--filter-repo pfad/zu/git_filter_repo.py]
Benötigt git-filter-repo (`pip install git-filter-repo`) und für die ID ohne `--noreply-id` die GitHub-CLI (`gh api user`).
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUELLE = "https://github.com/JackONeill199/astroflow.git"
KEEP = (b"refs/heads/main", b"refs/tags/v")
GITHUB_OK = (b"@users.noreply.github.com", b"noreply@github.com")


def git(repo: Path, *args: str, inp: bytes | None = None) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], input=inp, capture_output=True)
    if r.returncode:
        raise SystemExit(f"git {' '.join(args[:3])} …: {r.stderr.decode('utf-8', 'replace').strip()[:400]}")
    return r.stdout


# --------------------------------------------------------------------------- Ersetzungsliste
def read_list(path: Path) -> list[tuple[str, bool, bytes, bytes]]:
    """[(Art, regex?, muster, ersatz)] in Dateireihenfolge (filter-repo wendet sie ebenso nacheinander an)."""
    if not path.is_file():
        raise SystemExit(f"Ersetzungsliste fehlt: {path} (Format siehe Kopf dieses Skripts).")
    rules = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        art, sep, rest = line.partition("|")
        if not sep or "==>" not in rest:
            raise SystemExit(f"{path.name}:{n}: erwartet „Art | muster ==> ersatz“.")
        pat, _, rep = rest.strip().partition("==>")
        pat, rep = pat.strip(), rep.strip()
        rx = pat.startswith("regex:")
        if rx:
            pat = pat[6:]
            re.compile(pat.encode())
        if not pat:
            raise SystemExit(f"{path.name}:{n}: leeres Muster.")
        rules.append((art.strip() or "?", rx, pat.encode(), rep.encode()))
    if not rules:
        raise SystemExit(f"{path.name}: keine Ersetzungen.")
    return rules


def filter_repo_file(rules, extra: list[tuple[bytes, bytes]]) -> bytes:
    lines = [(b"regex:" if rx else b"literal:") + pat + b"==>" + rep for _, rx, pat, rep in rules]
    lines += [b"literal:" + a + b"==>" + b for a, b in extra]
    return b"\n".join(lines) + b"\n"


def apply(rules, data: bytes, extra=()) -> bytes:
    for _, rx, pat, rep in rules:
        data = re.sub(pat, rep, data) if rx else data.replace(pat, rep)
    for a, b in extra:
        data = data.replace(a, b)
    return data


def is_text(data: bytes) -> bool:
    return b"\0" not in data[:8192]           # wie git-filter-repo: Binärdateien bleiben unberührt


# --------------------------------------------------------------------------- Prüfungen
def all_blobs(repo: Path) -> dict[bytes, str]:
    out = {}
    for line in git(repo, "rev-list", "--all", "--objects").splitlines():
        sha, _, p = line.partition(b" ")
        if p:
            out.setdefault(sha, p.decode("utf-8", "replace"))
    typ = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)", inp=b"\n".join(out) + b"\n").splitlines()
    blobs = {l.split()[0] for l in typ if l.split()[1] == b"blob"}
    return {s: p for s, p in out.items() if s in blobs}


def blob_reader(repo: Path):
    """Liest viele Blobs über einen `cat-file --batch`-Prozess."""
    proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(sha: bytes) -> bytes:
        proc.stdin.write(sha + b"\n")
        proc.stdin.flush()
        head = proc.stdout.readline().split()
        size = int(head[2])
        data = proc.stdout.read(size)
        proc.stdout.read(1)
        return data
    return read, proc


def hits(rules, data: bytes, emails) -> list[str]:
    found = [art for art, rx, pat, _ in rules if (re.search(pat, data) if rx else pat in data)]
    found += ["E-Mail" for e in emails if e in data]
    return found


def scan(repo: Path, rules, emails) -> dict[str, int]:
    """Treffer je Art in Blobs (nur Text, wie die Ersetzung), Commit-/Tag-Texten und Autor/Committer/Tagger."""
    count: dict[str, int] = {}
    read, proc = blob_reader(repo)
    try:
        for sha in all_blobs(repo):
            data = read(sha)
            if is_text(data):
                for art in hits(rules, data, emails):
                    count["Inhalt: " + art] = count.get("Inhalt: " + art, 0) + 1
    finally:
        proc.stdin.close()
        proc.wait()
    log = git(repo, "log", "--all", "--format=%an <%ae>%x00%cn <%ce>%x00%B%x01")
    tags = git(repo, "for-each-ref", "refs/tags", "--format=%(taggername) %(taggeremail)%00%(contents)%01")
    for kind, blob in (("Commit", log), ("Tag", tags)):
        for entry in blob.split(b"\x01"):
            for art in hits(rules, entry, emails):
                count[f"{kind}: {art}"] = count.get(f"{kind}: {art}", 0) + 1
    return count


def tree(repo: Path, rev: str) -> dict[bytes, tuple[bytes, bytes]]:
    out = {}
    for line in git(repo, "ls-tree", "-r", "-z", rev).split(b"\0"):
        if line:
            meta, _, p = line.partition(b"\t")
            mode, _, sha = meta.split(b" ")
            out[p] = (mode, sha)
    return out


def same_tree(old: Path, new: Path, rev_old: str, rev_new: str, rules, extra) -> list[str]:
    """Abweichungen außer den Ersetzungen (leer = gleich)."""
    a, b = tree(old, rev_old), tree(new, rev_new)
    probs = [f"Pfad fehlt/zusätzlich: {p.decode('utf-8', 'replace')}" for p in sorted(set(a) ^ set(b))]
    ra, pa = blob_reader(old)
    rb, pb = blob_reader(new)
    try:
        for p in sorted(set(a) & set(b)):
            (ma, sa), (mb, sb) = a[p], b[p]
            if ma != mb:
                probs.append(f"Modus anders: {p.decode('utf-8', 'replace')}")
            elif sa != sb and ma != b"160000":
                da, db = ra(sa), rb(sb)
                want = apply(rules, da, extra) if is_text(da) else da
                if want != db:
                    probs.append(f"Inhalt anders als erwartet: {p.decode('utf-8', 'replace')}")
    finally:
        for proc in (pa, pb):
            proc.stdin.close()
            proc.wait()
    return probs


def commit_key(repo: Path, rev: str) -> bytes:
    """Zeitpunkt + Betreff: identifiziert denselben Commit vor und nach dem Umschreiben."""
    return git(repo, "log", "-1", "--format=%at %ct %s", rev).strip()


# --------------------------------------------------------------------------- Ablauf
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ziel", required=True, help="leerer Arbeitsordner (alt.git, neu.git, Bericht)")
    ap.add_argument("--quelle", default=QUELLE, help="URL oder Pfad des alten Repos (Standard: GitHub)")
    ap.add_argument("--liste", default=str(ROOT / "historie-ersetzungen.local.txt"))
    ap.add_argument("--noreply-id", help="numerische GitHub-ID (Standard: gh api user)")
    ap.add_argument("--login", default="JackONeill199")
    ap.add_argument("--filter-repo", help="Pfad zu git_filter_repo.py, falls `git filter-repo` nicht im PATH ist")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    rules = read_list(Path(a.liste))
    uid = a.noreply_id or subprocess.run(["gh", "api", "user", "-q", ".id"], capture_output=True, text=True).stdout.strip()
    if not uid.isdigit():
        raise SystemExit("GitHub-ID unbekannt – `gh auth login` oder --noreply-id angeben.")
    noreply = f"{uid}+{a.login}@users.noreply.github.com".encode()
    fr = [sys.executable, a.filter_repo] if a.filter_repo else ["git", "filter-repo"]

    ziel = Path(a.ziel)
    if ziel.exists() and any(ziel.iterdir()):
        raise SystemExit(f"{ziel} ist nicht leer.")
    ziel.mkdir(parents=True, exist_ok=True)
    alt, neu = ziel / "alt.git", ziel / "neu.git"
    for d in (alt, neu):
        print(f"Klone {d.name} …", flush=True)
        r = subprocess.run(["git", "clone", "-q", "--mirror", a.quelle, str(d)], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(r.stderr)

    # 2. nur main + v*-Tags
    refs = git(neu, "for-each-ref", "--format=%(refname)").split()
    drop = [r for r in refs if not r.startswith(KEEP) or (r.startswith(b"refs/heads/") and r != b"refs/heads/main")]
    if drop:
        git(neu, "update-ref", "--stdin", inp=b"".join(b"delete " + r + b"\n" for r in drop))
    tags = sorted(r.decode() for r in git(neu, "for-each-ref", "refs/tags/v*", "--format=%(refname)").split())
    print(f"Behalten: main + {len(tags)} Tags, entfernt: {len(drop)} Refs", flush=True)

    # 3. Adressen + filter-repo
    who = git(neu, "log", "--all", "--format=%ae%n%ce").split()
    who += [e.strip(b"<>") for e in git(neu, "for-each-ref", "refs/tags", "--format=%(taggeremail)").split()]
    emails = sorted({e for e in who if e and not e.endswith(GITHUB_OK)})
    mailmap = b"".join(a.login.encode() + b" <" + noreply + b"> <" + e + b">\n" for e in emails)
    extra = [(e, noreply) for e in emails]
    work = ziel / "_filter"
    work.mkdir()
    (work / "mailmap").write_bytes(mailmap)
    (work / "ersetzungen").write_bytes(filter_repo_file(rules, extra))
    print(f"Umschreiben: {len(emails)} Adresse(n) → noreply, {len(rules)} Ersetzungsregel(n) …", flush=True)
    r = subprocess.run([*fr, "--force", "--mailmap", str(work / "mailmap"), "--replace-text", str(work / "ersetzungen"),
                        "--replace-message", str(work / "ersetzungen")], cwd=neu, capture_output=True, text=True)
    shutil.rmtree(work)                      # enthält die privaten Begriffe
    if r.returncode:
        raise SystemExit("git filter-repo fehlgeschlagen:\n" + r.stderr[-2000:])
    git(neu, "reflog", "expire", "--expire=now", "--all")
    git(neu, "gc", "-q", "--prune=now", "--aggressive")

    # 4. Prüfen
    ok = True
    rest = scan(neu, rules, emails)
    print("Restfunde:", ", ".join(f"{k} {v}" for k, v in sorted(rest.items())) or "keine")
    ok &= not rest
    n_alt = int(git(alt, "rev-list", "--count", "main"))
    n_neu = int(git(neu, "rev-list", "--count", "main"))
    print(f"Commits auf main: alt {n_alt}, neu {n_neu}")
    ok &= n_alt == n_neu
    left = sorted(r.decode() for r in git(neu, "for-each-ref", "--format=%(refname)").split())
    ok &= left == sorted(["refs/heads/main", *tags])
    probs = same_tree(alt, neu, "main", "main", rules, extra)
    print(f"Baum main: {'gleich bis auf Ersetzungen' if not probs else probs[:10]}")
    ok &= not probs
    bad_tags = []
    for t in tags:
        cn = git(neu, "rev-parse", t + "^{commit}").strip()
        if apply(rules, commit_key(alt, t + "^{commit}"), extra) != commit_key(neu, t + "^{commit}") \
                or same_tree(alt, neu, t, t, rules, extra):
            bad_tags.append(t)
        if not git(neu, "branch", "--contains", cn).strip():
            print(f"  Hinweis: {t} liegt nicht auf main")
    print(f"Tags: {len(tags) - len(bad_tags)} von {len(tags)} zeigen auf den passenden neuen Commit"
          + (f" – Probleme: {bad_tags}" if bad_tags else ""))
    ok &= not bad_tags
    size = sum(f.stat().st_size for f in neu.rglob("*") if f.is_file()) / 1e6
    print(f"neu.git: {size:.0f} MB")
    print("ERGEBNIS:", "in Ordnung – bereit für `git push --mirror` in ein NEUES Repo" if ok else "NICHT in Ordnung")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
