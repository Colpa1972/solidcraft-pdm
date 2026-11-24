#!/usr/bin/env python3
"""Lokales PDM fuer SolidCraft: Arbeitsordner + Tresor + Datenbank (SQLite), Bedienung im Browser.
Nur Python-Standardbibliothek. Start: python3 pdm.py   (oder Doppelklick auf PDM-starten.command)"""
import hashlib, io, json, os, re, secrets, shutil, sqlite3, subprocess, sys, threading, time, webbrowser, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

def _find_root():
    """Datenordner: 1) PDM_ROOT, 2) der Ordner ueber dem Programmordner, wenn dort schon ein PDM liegt
    (so kann man den ganzen Ordner verschieben), 3) ~/Documents/PDM."""
    env = os.environ.get("PDM_ROOT")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    here = os.path.dirname(os.path.abspath(__file__))
    up = os.path.dirname(here)
    if os.path.isfile(os.path.join(up, "pdm.sqlite")) or (
            os.path.isdir(os.path.join(up, "Tresor")) and os.path.isdir(os.path.join(up, "Arbeit"))):
        return up
    return os.path.abspath(os.path.expanduser("~/Documents/PDM"))


ROOT = _find_root()
WORK = os.path.join(ROOT, "Arbeit")
VAULT = os.path.join(ROOT, "Tresor")
DBFILE = os.path.join(ROOT, "pdm.sqlite")
PORT = int(os.environ.get("PDM_PORT", "8765"))
EXTS = (".sc", ".solidcraft")
TOKEN = secrets.token_hex(16)
LOCK = threading.Lock()
APP_NAME = os.environ.get("PDM_APP", "SolidCraft")


def now():
    return time.strftime("%Y-%m-%d %H:%M")


def db():
    c = sqlite3.connect(DBFILE)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def init():
    for d in (ROOT, WORK, VAULT):
        os.makedirs(d, exist_ok=True)
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS parts(
          id INTEGER PRIMARY KEY, number TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          description TEXT DEFAULT '', material TEXT DEFAULT '', project TEXT DEFAULT '',
          designer TEXT DEFAULT '', created TEXT,
          co_file TEXT, co_version INTEGER, co_by TEXT, co_time TEXT, co_sha TEXT);
        CREATE TABLE IF NOT EXISTS versions(
          id INTEGER PRIMARY KEY, part_id INTEGER NOT NULL REFERENCES parts(id),
          version INTEGER NOT NULL, revision TEXT DEFAULT '', status TEXT DEFAULT 'In Arbeit',
          file TEXT NOT NULL, sha256 TEXT, size INTEGER, comment TEXT DEFAULT '',
          designer TEXT DEFAULT '', created TEXT, thumb TEXT, released TEXT,
          UNIQUE(part_id, version));
        """)
        cols = [r[1] for r in c.execute("PRAGMA table_info(versions)")]
        if "glb" not in cols:
            c.execute("ALTER TABLE versions ADD COLUMN glb TEXT")


def safe(s, keep=" ._-"):
    s = re.sub(r"[^\w" + re.escape(keep) + "]", "_", s.strip(), flags=re.UNICODE)
    return s.strip(" .") or "x"


def sha_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def thumb_from(path):
    """Vorschaubild aus der SolidCraft-Datei (ZIP mit cache/thumbnail.png)."""
    try:
        with zipfile.ZipFile(path) as z:
            for n in ("cache/thumbnail.png", "thumbnail.png"):
                if n in z.namelist():
                    return z.read(n)
    except Exception:
        pass
    return None


def find_cli():
    for cand in (os.environ.get("PDM_CLI"), shutil.which("solidcraft-cli"),
                 "/Applications/SolidCraft.app/Contents/MacOS/solidcraft-cli",
                 "/Applications/SolidCraft.app/Contents/Resources/solidcraft-cli",
                 "/usr/local/bin/solidcraft-cli", "/opt/homebrew/bin/solidcraft-cli"):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def make_glb(src, dst):
    """3D-Vorschau: zuerst eine GLB-Datei mit gleichem Namen im Arbeitsordner, sonst SolidCraft-CLI (falls vorhanden)."""
    base = os.path.splitext(src)[0]
    for cand in (base + ".glb", base + ".GLB"):
        if os.path.isfile(cand):
            shutil.copy2(cand, dst)
            return True
    cli = find_cli()
    if not cli:
        return False
    import tempfile
    try:
        with tempfile.TemporaryDirectory() as td:
            script = os.path.join(td, "export.json")
            out = os.path.join(td, "model.glb")
            with open(script, "w") as f:
                json.dump([{"command": "doc.open", "params": {"path": src}},
                           {"command": "file.export", "params": {"path": out, "format": "glb"}}], f)
            subprocess.run([cli, "run", script], capture_output=True, timeout=90, cwd=td)
            if os.path.isfile(out) and os.path.getsize(out) > 100:
                shutil.copy2(out, dst)
                return True
    except Exception:
        pass
    return False


def rev_letter(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def ver_dict(r):
    return {k: r[k] for k in ("id", "part_id", "version", "revision", "status", "file", "sha256", "size",
                              "comment", "designer", "created", "released")} | {"has_thumb": bool(r["thumb"]), "has_glb": bool(r["glb"])}


def part_row(c, p):
    last = c.execute("SELECT * FROM versions WHERE part_id=? ORDER BY version DESC LIMIT 1", (p["id"],)).fetchone()
    rel = c.execute("SELECT revision FROM versions WHERE part_id=? AND status='Freigegeben' ORDER BY version DESC LIMIT 1", (p["id"],)).fetchone()
    d = {k: p[k] for k in ("id", "number", "name", "description", "material", "project", "designer", "created", "co_file", "co_version", "co_by", "co_time")}
    d["latest"] = ver_dict(last) if last else None
    d["revision"] = rel["revision"] if rel else ""
    d["modified"] = False
    if p["co_file"]:
        wp = os.path.join(WORK, p["co_file"])
        if not os.path.exists(wp):
            d["missing"] = True
        else:
            d["modified"] = sha_of(wp) != p["co_sha"]
    return d


class Err(Exception):
    pass


# ---------------------------------------------------------------- Aktionen
def list_parts(q):
    with db() as c:
        if q.strip():
            terms = [t for t in q.split() if t]
            sql = "SELECT * FROM parts WHERE " + " AND ".join(
                "(number LIKE ? OR name LIKE ? OR description LIKE ? OR material LIKE ? OR project LIKE ? OR designer LIKE ?)" for _ in terms) + " ORDER BY number"
            args = []
            for t in terms:
                args += [f"%{t}%"] * 6
            rows = c.execute(sql, args).fetchall()
        else:
            rows = c.execute("SELECT * FROM parts ORDER BY number").fetchall()
        return [part_row(c, r) for r in rows]


def get_part(pid):
    with db() as c:
        p = c.execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
        if not p:
            raise Err("Teil nicht gefunden.")
        vs = c.execute("SELECT * FROM versions WHERE part_id=? ORDER BY version DESC", (pid,)).fetchall()
        return {"part": part_row(c, p), "versions": [ver_dict(v) for v in vs]}


def work_files():
    out = []
    with db() as c:
        tracked = {r["co_file"]: r["number"] for r in c.execute("SELECT co_file, number FROM parts WHERE co_file IS NOT NULL")}
    for n in sorted(os.listdir(WORK)):
        p = os.path.join(WORK, n)
        if os.path.isfile(p) and n.lower().endswith(EXTS) and not n.startswith((".", "~")):
            out.append({"file": n, "tracked": tracked.get(n), "size": os.path.getsize(p),
                        "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
    return out


def next_number(c):
    nums = [int(r["number"]) for r in c.execute("SELECT number FROM parts") if r["number"].isdigit()]
    return str(max(nums + [999]) + 1)


def checkin(d):
    fname = os.path.basename(d.get("file", ""))
    src = os.path.join(WORK, fname)
    if not fname.lower().endswith(EXTS) or not os.path.isfile(src):
        raise Err("Datei im Arbeitsordner nicht gefunden.")
    sha = sha_of(src)
    designer = (d.get("designer") or "").strip()
    with db() as c:
        part = None
        tracked = c.execute("SELECT * FROM parts WHERE co_file=?", (fname,)).fetchone()
        if d.get("part_id"):
            part = c.execute("SELECT * FROM parts WHERE id=?", (d["part_id"],)).fetchone()
        elif tracked:
            part = tracked
        if part:
            last = c.execute("SELECT * FROM versions WHERE part_id=? ORDER BY version DESC LIMIT 1", (part["id"],)).fetchone()
            if last and last["sha256"] == sha:
                raise Err("Die Datei ist unveraendert gegenueber Version %d." % last["version"])
            for k in ("name", "description", "material", "project"):
                if d.get(k) is not None and str(d[k]).strip() != "":
                    c.execute(f"UPDATE parts SET {k}=? WHERE id=?", (str(d[k]).strip(), part["id"]))
        else:
            name = (d.get("name") or "").strip()
            if not name:
                raise Err("Bitte einen Namen eingeben.")
            number = (d.get("number") or "").strip() or next_number(c)
            if not re.fullmatch(r"[\w.\-]+", number):
                raise Err("Nummer: nur Buchstaben, Ziffern, Punkt, Minus und Unterstrich.")
            if c.execute("SELECT 1 FROM parts WHERE number=?", (number,)).fetchone():
                raise Err("Nummer %s gibt es schon." % number)
            cur = c.execute("INSERT INTO parts(number,name,description,material,project,designer,created) VALUES(?,?,?,?,?,?,?)",
                            (number, name, (d.get("description") or "").strip(), (d.get("material") or "").strip(),
                             (d.get("project") or "").strip(), designer, now()))
            part = c.execute("SELECT * FROM parts WHERE id=?", (cur.lastrowid,)).fetchone()
            last = None
        ver = (last["version"] if last else 0) + 1
        ext = os.path.splitext(fname)[1].lower()
        rel = os.path.join(safe(part["number"], "._-"), "%s_v%03d%s" % (safe(part["number"], "._-"), ver, ext))
        dst = os.path.join(VAULT, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            raise Err("Tresordatei existiert bereits - Abbruch, um nichts zu ueberschreiben.")
        shutil.copy2(src, dst)
        if sha_of(dst) != sha:
            os.remove(dst)
            raise Err("Kopie in den Tresor fehlgeschlagen (Pruefsumme).")
        os.chmod(dst, 0o444)
        th = thumb_from(dst)
        thumb_rel = None
        if th:
            thumb_rel = rel + ".png"
            with open(os.path.join(VAULT, thumb_rel), "wb") as f:
                f.write(th)
        glb_rel = rel + ".glb"
        if not make_glb(src, os.path.join(VAULT, glb_rel)):
            glb_rel = None
        c.execute("INSERT INTO versions(part_id,version,revision,status,file,sha256,size,comment,designer,created,thumb,glb) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (part["id"], ver, "", "In Arbeit", rel, sha, os.path.getsize(dst), (d.get("comment") or "").strip(), designer, now(), thumb_rel, glb_rel))
        # Eingecheckt: Arbeitskopie bleibt, bleibt ausgecheckt und ist nun unveraendert gegenueber dem Tresor
        keep = bool(d.get("keep_checked_out", True))
        if keep:
            c.execute("UPDATE parts SET co_file=?, co_version=?, co_by=?, co_time=?, co_sha=? WHERE id=?",
                      (fname, ver, designer or "ich", now(), sha, part["id"]))
        else:
            c.execute("UPDATE parts SET co_file=NULL, co_version=NULL, co_by=NULL, co_time=NULL, co_sha=NULL WHERE id=?", (part["id"],))
        return {"part_id": part["id"], "number": part["number"], "version": ver}


def checkout(pid, version=None):
    with db() as c:
        p = c.execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
        if not p:
            raise Err("Teil nicht gefunden.")
        v = (c.execute("SELECT * FROM versions WHERE part_id=? AND version=?", (pid, version)).fetchone() if version
             else c.execute("SELECT * FROM versions WHERE part_id=? ORDER BY version DESC LIMIT 1", (pid,)).fetchone())
        if not v:
            raise Err("Keine Version vorhanden.")
        if p["co_file"]:
            wp = os.path.join(WORK, p["co_file"])
            if os.path.exists(wp):
                if sha_of(wp) != p["co_sha"]:
                    raise Err("Teil ist schon ausgecheckt und hat ungesicherte Aenderungen. Erst einchecken oder Auschecken verwerfen.")
        ext = os.path.splitext(v["file"])[1]
        nm = re.sub(r"[.](sc|solidcraft)$", "", p["name"], flags=re.I)
        fname = "%s_%s%s" % (safe(p["number"], "._-"), safe(nm), ext)
        dst = os.path.join(WORK, fname)
        if os.path.exists(dst) and not (p["co_file"] == fname and sha_of(dst) == p["co_sha"]):
            raise Err("Im Arbeitsordner liegt schon eine andere Datei %s. Bitte erst verschieben oder einchecken." % fname)
        if os.path.exists(dst):
            os.chmod(dst, 0o644)
        shutil.copy2(os.path.join(VAULT, v["file"]), dst)
        os.chmod(dst, 0o644)
        c.execute("UPDATE parts SET co_file=?, co_version=?, co_by=?, co_time=?, co_sha=? WHERE id=?",
                  (fname, v["version"], "ich", now(), v["sha256"], pid))
        return {"file": fname, "version": v["version"]}


def discard_checkout(pid, delete_file=False):
    with db() as c:
        p = c.execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
        if not p or not p["co_file"]:
            raise Err("Das Teil ist nicht ausgecheckt.")
        wp = os.path.join(WORK, p["co_file"])
        if delete_file and os.path.exists(wp):
            # nicht loeschen, sondern wegsortieren
            trash = os.path.join(WORK, "_verworfen")
            os.makedirs(trash, exist_ok=True)
            shutil.move(wp, os.path.join(trash, time.strftime("%Y%m%d-%H%M%S_") + p["co_file"]))
        c.execute("UPDATE parts SET co_file=NULL, co_version=NULL, co_by=NULL, co_time=NULL, co_sha=NULL WHERE id=?", (pid,))
    return {}


def release(vid):
    with db() as c:
        v = c.execute("SELECT * FROM versions WHERE id=?", (vid,)).fetchone()
        if not v:
            raise Err("Version nicht gefunden.")
        if v["status"] == "Freigegeben":
            raise Err("Diese Version ist schon freigegeben (Revision %s)." % v["revision"])
        n = c.execute("SELECT COUNT(*) FROM versions WHERE part_id=? AND status='Freigegeben'", (v["part_id"],)).fetchone()[0]
        rev = rev_letter(n)
        c.execute("UPDATE versions SET status='Freigegeben', revision=?, released=? WHERE id=?", (rev, now(), vid))
        return {"revision": rev}


def open_part(pid):
    with db() as c:
        p = c.execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
        if not p:
            raise Err("Teil nicht gefunden.")
        if not p["co_file"] or not os.path.exists(os.path.join(WORK, p["co_file"])):
            checkout(pid)
            p = c.execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
    path = os.path.join(WORK, p["co_file"])
    launch(path)
    return {"file": p["co_file"]}


def launch(path):
    if sys.platform == "darwin":
        r = subprocess.run(["open", "-a", APP_NAME, path], capture_output=True, text=True)
        if r.returncode != 0:
            r = subprocess.run(["open", path], capture_output=True, text=True)
            if r.returncode != 0:
                raise Err("SolidCraft konnte nicht gestartet werden: " + r.stderr.strip())
    elif sys.platform.startswith("linux"):
        r = subprocess.run(["xdg-open", path], capture_output=True, text=True)
    else:
        os.startfile(path)  # type: ignore[attr-defined]


def upload_glb(vid, data_b64):
    import base64
    try:
        data = base64.b64decode(data_b64, validate=True)
    except Exception:
        raise Err("Ungueltige Datei.")
    if len(data) < 20 or data[:4] != b"glTF":
        raise Err("Das ist keine GLB-Datei (in SolidCraft: Datei > Exportieren > GLB).")
    with db() as c:
        v = c.execute("SELECT * FROM versions WHERE id=?", (vid,)).fetchone()
        if not v:
            raise Err("Version nicht gefunden.")
        rel = v["file"] + ".glb"
        with open(os.path.join(VAULT, rel), "wb") as f:
            f.write(data)
        c.execute("UPDATE versions SET glb=? WHERE id=?", (rel, vid))
    return {"bytes": len(data)}


def upload_thumb(vid, data_b64):
    import base64
    try:
        data = base64.b64decode(data_b64, validate=True)
    except Exception:
        raise Err("Ungueltiges Bild.")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise Err("Das ist kein PNG-Bild.")
    with db() as c:
        v = c.execute("SELECT * FROM versions WHERE id=?", (vid,)).fetchone()
        if not v:
            raise Err("Version nicht gefunden.")
        rel = v["file"] + ".png"
        with open(os.path.join(VAULT, rel), "wb") as f:
            f.write(data)
        c.execute("UPDATE versions SET thumb=? WHERE id=?", (rel, vid))
    return {"bytes": len(data)}


def verify():
    bad = []
    with db() as c:
        for v in c.execute("SELECT v.*, p.number FROM versions v JOIN parts p ON p.id=v.part_id"):
            fp = os.path.join(VAULT, v["file"])
            if not os.path.exists(fp) or sha_of(fp) != v["sha256"]:
                bad.append("%s v%d" % (v["number"], v["version"]))
    return {"fehler": bad}


# ---------------------------------------------------------------- HTTP
class H(BaseHTTPRequestHandler):
    server_version = "PDM"

    def log_message(self, *a):
        pass

    def _ok_host(self):
        h = (self.headers.get("Host") or "").split(":")[0]
        return h in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._ok_host():
            return self._send(403, {"error": "verboten"})
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        try:
            if u.path == "/":
                return self._send(200, PAGE.replace("__TOKEN__", TOKEN).replace("__ROOT__", ROOT), "text/html; charset=utf-8")
            if u.path == "/api/parts":
                return self._send(200, list_parts(qs.get("q", [""])[0]))
            if u.path == "/api/part":
                return self._send(200, get_part(int(qs["id"][0])))
            if u.path == "/api/work":
                return self._send(200, work_files())
            if u.path == "/api/verify":
                return self._send(200, verify())
            if u.path == "/thumb":
                with db() as c:
                    v = c.execute("SELECT thumb FROM versions WHERE id=?", (int(qs["id"][0]),)).fetchone()
                if not v or not v["thumb"]:
                    return self._send(404, b"", "image/png")
                with open(os.path.join(VAULT, v["thumb"]), "rb") as f:
                    return self._send(200, f.read(), "image/png")
            if u.path == "/glb":
                with db() as c:
                    v = c.execute("SELECT glb FROM versions WHERE id=?", (int(qs["id"][0]),)).fetchone()
                if not v or not v["glb"]:
                    return self._send(404, b"", "model/gltf-binary")
                with open(os.path.join(VAULT, v["glb"]), "rb") as f:
                    return self._send(200, f.read(), "model/gltf-binary")
            if u.path == "/static/model-viewer.min.js":
                fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "model-viewer.min.js")
                if not os.path.isfile(fp):
                    return self._send(404, b"", "text/javascript")
                with open(fp, "rb") as f:
                    return self._send(200, f.read(), "text/javascript; charset=utf-8")
            if u.path == "/workthumb":
                n = os.path.basename(qs["file"][0])
                data = thumb_from(os.path.join(WORK, n))
                return self._send(200 if data else 404, data or b"", "image/png")
            return self._send(404, {"error": "nicht gefunden"})
        except Err as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa
            return self._send(500, {"error": "Fehler: %s" % e})

    def do_POST(self):
        if not self._ok_host() or self.headers.get("X-PDM-Token") != TOKEN:
            return self._send(403, {"error": "verboten"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            d = json.loads(self.rfile.read(n) or b"{}")
            with LOCK:
                p = urlparse(self.path).path
                if p == "/api/checkin":
                    r = checkin(d)
                elif p == "/api/checkout":
                    r = checkout(int(d["part_id"]), d.get("version"))
                elif p == "/api/discard":
                    r = discard_checkout(int(d["part_id"]), bool(d.get("delete_file")))
                elif p == "/api/release":
                    r = release(int(d["version_id"]))
                elif p == "/api/open":
                    r = open_part(int(d["part_id"]))
                elif p == "/api/thumb":
                    r = upload_thumb(int(d["version_id"]), d["data"])
                elif p == "/api/glb":
                    r = upload_glb(int(d["version_id"]), d["data"])
                elif p == "/api/reveal":
                    if sys.platform == "darwin":
                        subprocess.run(["open", d.get("where") == "vault" and VAULT or WORK])
                    r = {}
                else:
                    return self._send(404, {"error": "nicht gefunden"})
            return self._send(200, r)
        except Err as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa
            return self._send(500, {"error": "Fehler: %s" % e})


PAGE = r"""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>PDM fuer SolidCraft</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script type="module" src="/static/model-viewer.min.js"></script>
<style>
:root{--bg:#f4f6f9;--fg:#1d2733;--mut:#6b7785;--line:#d9dfe7;--card:#fff;--ac:#1f6feb;--ok:#1a7f37;--warn:#b35900;--bad:#c62828}
@media(prefers-color-scheme:dark){:root{--bg:#14181d;--fg:#e6ebf1;--mut:#97a3b1;--line:#2c343d;--card:#1c2229;--ac:#58a6ff;--ok:#56d364;--warn:#e3a008;--bad:#ff6b6b}}
*{box-sizing:border-box}body{margin:0;font:14px/1.4 -apple-system,system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;gap:12px;align-items:center;padding:10px 16px;background:var(--card);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
h1{font-size:16px;margin:0}input,select,textarea,button{font:inherit;color:inherit}
input,textarea{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:6px 8px;width:100%}
#q{max-width:420px}button{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:5px 10px;cursor:pointer}
button.p{background:var(--ac);border-color:var(--ac);color:#fff}button:disabled{opacity:.5;cursor:default}
main{display:grid;grid-template-columns:1fr 340px;gap:12px;padding:12px 16px}@media(max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden}
table{border-collapse:collapse;width:100%}th,td{padding:7px 9px;text-align:left;border-bottom:1px solid var(--line);vertical-align:middle}
th{font-size:12px;color:var(--mut);font-weight:600}tr.row{cursor:pointer}tr.row:hover{background:color-mix(in srgb,var(--ac) 8%,transparent)}tr.sel{background:color-mix(in srgb,var(--ac) 16%,transparent)}
img.t{width:64px;height:40px;object-fit:contain;background:var(--bg);border-radius:4px;display:block}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px;border:1px solid var(--line)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.mut{color:var(--mut)}
.side{padding:12px;display:flex;flex-direction:column;gap:10px}.side h2{margin:0;font-size:15px}
.big{width:100%;height:170px;object-fit:contain;background:var(--bg);border-radius:6px;border:1px solid var(--line)}
.btns{display:flex;flex-wrap:wrap;gap:6px}label{display:block;font-size:12px;color:var(--mut);margin:6px 0 2px}
#msg{position:fixed;bottom:14px;left:50%;transform:translateX(-50%);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 14px;box-shadow:0 4px 18px #0004;display:none;z-index:20;max-width:90vw}
#dlg{position:fixed;inset:0;background:#0006;display:none;align-items:center;justify-content:center;z-index:15}#dlg .card{width:min(520px,94vw);padding:16px;max-height:92vh;overflow:auto}
.tabs{display:flex;gap:4px;padding:8px 8px 0}.tabs button{border-bottom-left-radius:0;border-bottom-right-radius:0}.tabs .on{background:var(--ac);color:#fff;border-color:var(--ac)}
.small{font-size:12px}
</style></head><body>
<header><h1>PDM fuer SolidCraft</h1><input id="q" placeholder="Suchen: Nummer, Name, Material, Projekt ..." autofocus>
<button onclick="reload()">Aktualisieren</button><span class="mut small" id="info"></span><span style="flex:1"></span>
<button onclick="post('/api/reveal',{where:'work'})">Arbeitsordner</button><button onclick="post('/api/reveal',{where:'vault'})">Tresor</button><button onclick="verifyAll()">Tresor pruefen</button></header>
<main><div class="card"><div class="tabs"><button id="t1" class="on" onclick="tab(1)">Teile</button><button id="t2" onclick="tab(2)">Arbeitsordner <span id="wn"></span></button></div>
<div id="v1"><table><thead><tr><th></th><th>Nummer</th><th>Name</th><th>Rev.</th><th>Ver.</th><th>Status</th><th>Material</th><th>Projekt</th><th>Stand</th></tr></thead><tbody id="rows"></tbody></table><div id="empty" class="mut" style="padding:16px"></div></div>
<div id="v2" style="display:none"><table><thead><tr><th></th><th>Datei</th><th>Geaendert</th><th>Zuordnung</th><th></th></tr></thead><tbody id="wrows"></tbody></table><div id="wempty" class="mut" style="padding:16px"></div></div></div>
<div class="card side" id="side"><div class="mut">Teil in der Liste anklicken. Doppelklick oeffnet es in SolidCraft.</div></div></main>
<div id="dlg"><div class="card" id="dbody"></div></div><div id="msg"></div>
<script>
const TOKEN="__TOKEN__";let parts=[],sel=null,work=[];
const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function ask(t,ok){dlg(`<h2 style="margin:0 0 8px">Bitte bestaetigen</h2><div style="margin:8px 0 12px">${esc(t)}</div><div class="btns"><button class="p" id="askok">Ja</button><button onclick="closeDlg()">Abbrechen</button></div>`);$('#askok').onclick=()=>{closeDlg();ok()}}
window.onerror=(m)=>{try{msg('Fehler in der Oberflaeche: '+m,1)}catch(e){}};
function msg(t,bad){const m=$('#msg');m.textContent=t;m.style.color=bad?'var(--bad)':'';m.style.display='block';clearTimeout(msg.t);msg.t=setTimeout(()=>m.style.display='none',bad?7000:3500)}
async function api(u){const r=await fetch(u);const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}
async function post(u,d){try{const r=await fetch(u,{method:'POST',headers:{'X-PDM-Token':TOKEN,'Content-Type':'application/json'},body:JSON.stringify(d||{})});const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j}catch(e){msg(e.message,1);throw e}}
function state(p){if(!p.co_file)return'<span class="mut">im Tresor</span>';if(p.missing)return'<span class="bad">Datei fehlt im Arbeitsordner</span>';return p.modified?'<span class="warn">ausgecheckt, geaendert</span>':'<span class="ok">ausgecheckt</span>'}
async function reload(){const q=$('#q').value;parts=await api('/api/parts?q='+encodeURIComponent(q));work=await api('/api/work');render();if(sel)showPart(sel,1);autoThumbs()}
function render(){const tb=$('#rows');tb.innerHTML=parts.map(p=>{const l=p.latest;return`<tr class="row ${sel==p.id?'sel':''}" data-id="${p.id}" onclick="showPart(${p.id})" ondblclick="openPart(${p.id})"><td>${l&&l.has_thumb?`<img class="t" style="cursor:pointer" src="/thumb?id=${l.id}&t=${l.thumbv||0}" onclick="event.stopPropagation();showPart(${p.id});pic3d(${p.id})">`:'<div class="t"></div>'}</td><td><b>${esc(p.number)}</b></td><td>${esc(p.name)}</td><td>${esc(p.revision)}</td><td>${l?l.version:''}</td><td>${l?`<span class="tag ${l.status=='Freigegeben'?'ok':''}">${esc(l.status)}</span>`:''}</td><td>${esc(p.material)}</td><td>${esc(p.project)}</td><td>${state(p)}</td></tr>`}).join('');
$('#empty').textContent=parts.length?'':($('#q').value?'Nichts gefunden.':'Noch keine Teile. SolidCraft-Datei im Arbeitsordner speichern, dann unter "Arbeitsordner" einchecken.');
$('#info').textContent=parts.length+' Teile';const n=work.filter(w=>!w.tracked).length;$('#wn').textContent=n?'('+n+' neu)':'';
$('#wrows').innerHTML=work.map(w=>`<tr><td><img class="t" src="/workthumb?file=${encodeURIComponent(w.file)}" onerror="this.style.visibility='hidden'"></td><td>${esc(w.file)}</td><td>${esc(w.time)}</td><td>${w.tracked?'Teil '+esc(w.tracked):'<span class="warn">nicht im PDM</span>'}</td><td>${w.tracked?'':`<button class="p" onclick="newDlg('${esc(w.file).replace(/'/g,"\\'")}')">Als neues Teil einchecken</button>`}</td></tr>`).join('');
$('#wempty').textContent=work.length?'':'Der Arbeitsordner ist leer: '+"__ROOT__"+'/Arbeit'}
function tab(n){$('#v1').style.display=n==1?'':'none';$('#v2').style.display=n==2?'':'none';$('#t1').className=n==1?'on':'';$('#t2').className=n==2?'on':''}
async function showPart(id,keep){sel=id;document.querySelectorAll('#rows tr.row').forEach(r=>r.classList.toggle('sel',r.dataset.id==id));const d=await api('/api/part?id='+id);const p=d.part,v=d.versions;const l=v[0];const g=v.find(x=>x.has_glb);
const co=!!p.co_file;$('#side').innerHTML=`<h2>${esc(p.number)} - ${esc(p.name)}</h2>${l&&l.has_thumb?`<img class="big" src="/thumb?id=${l.id}&t=${l.thumbv||0}" style="cursor:pointer" title="Klicken fuer 3D-Ansicht" onclick="pic3d(${p.id})">`:`<div class="big" style="cursor:pointer;display:flex;align-items:center;justify-content:center" onclick="pic3d(${p.id})"><span class="mut">kein Vorschaubild - hier klicken fuer 3D</span></div>`}<div class="small mut" style="margin:-4px 0 6px">Auf das Bild klicken = 3D-Ansicht</div>
<div class="small">${esc(p.description)||'<span class="mut">keine Beschreibung</span>'}<br><span class="mut">Material:</span> ${esc(p.material)||'-'} &nbsp; <span class="mut">Projekt:</span> ${esc(p.project)||'-'}<br><span class="mut">Konstrukteur:</span> ${esc(p.designer)||'-'} &nbsp; <span class="mut">angelegt:</span> ${esc(p.created)}</div>
<div>${state(p)}${co?`<div class="small mut">Arbeitsdatei: ${esc(p.co_file)}, ab Version ${p.co_version}</div>`:''}</div>
<div class="btns"><button class="p" onclick="openPart(${p.id})">In SolidCraft oeffnen</button>
${co?`<button onclick="inDlg(${p.id})" ${p.missing?'disabled':''}>Einchecken</button><button onclick="discard(${p.id})">Auschecken verwerfen</button>`:`<button onclick="outCo(${p.id})">Auschecken</button>`}</div>
<div class="btns">${g?`<button onclick="view3d(${g.id})">3D-Ansicht (drehbar)</button>`:`<button onclick="pickGlb(${l?l.id:0})">3D-Ansicht hinzufuegen ...</button>`}</div>
<div><b>Versionen</b></div><table class="small">${v.map(x=>`<tr><td>v${x.version}</td><td>${x.status=='Freigegeben'?'<span class="ok">Rev. '+esc(x.revision)+'</span>':'In Arbeit'}</td><td>${esc(x.created)}<br><span class="mut">${esc(x.comment)}</span></td><td>${x.has_glb?`<button onclick="view3d(${x.id})">3D</button>`:''}${x.status!='Freigegeben'?`<button onclick="rel(${x.id})">Freigeben</button>`:''}<button onclick="outCo(${p.id},${x.version})">Auschecken</button></td></tr>`).join('')}</table>`}
async function openPart(id){try{const r=await post('/api/open',{part_id:id});msg('Geoeffnet: '+r.file);reload()}catch(e){}}
async function outCo(id,ver){try{const r=await post('/api/checkout',{part_id:id,version:ver});msg('Ausgecheckt als '+r.file+' (Version '+r.version+')');reload()}catch(e){}}
function discard(id){ask('Auschecken verwerfen? Die Arbeitsdatei wird in "_verworfen" im Arbeitsordner abgelegt, nichts geht verloren.',async()=>{try{await post('/api/discard',{part_id:id,delete_file:true});msg('Verworfen');reload()}catch(e){}})}
function rel(vid){ask('Diese Version freigeben? Danach ist sie unveraenderlich.',async()=>{try{const r=await post('/api/release',{version_id:vid});msg('Freigegeben, Revision '+r.revision);reload()}catch(e){}})}
async function verifyAll(){const r=await api('/api/verify');msg(r.fehler.length?'Fehler im Tresor: '+r.fehler.join(', '):'Tresor in Ordnung: alle Pruefsummen stimmen.',r.fehler.length)}
let thumbBusy=false;
async function snap(glbId,w,h,cam){return new Promise((res,rej)=>{const mv=document.createElement('model-viewer');mv.style.cssText='position:fixed;left:0;top:0;z-index:-1;opacity:0.01;pointer-events:none;width:'+w+'px;height:'+h+'px';mv.setAttribute('loading','eager');mv.setAttribute('reveal','auto');mv.setAttribute('src','/glb?id='+glbId);mv.setAttribute('environment-image','neutral');mv.setAttribute('shadow-intensity','0');if(cam)mv.setAttribute('camera-orbit',cam);mv.setAttribute('interaction-prompt','none');document.body.appendChild(mv);const to=setTimeout(()=>{mv.remove();rej(new Error('Zeitueberschreitung beim Laden der 3D-Datei'))},90000);mv.addEventListener('error',()=>{clearTimeout(to);mv.remove();rej(new Error('3D-Datei konnte nicht geladen werden'))});mv.addEventListener('load',async()=>{try{await new Promise(r=>setTimeout(r,400));const b=await mv.toBlob({mimeType:'image/png',idealAspect:true});clearTimeout(to);mv.remove();const rd=new FileReader();rd.onload=()=>res(rd.result.split(',')[1]);rd.readAsDataURL(b)}catch(e){clearTimeout(to);mv.remove();rej(e)}})})}
async function autoThumbs(){if(thumbBusy)return;thumbBusy=true;try{for(const p of parts){const l=p.latest;if(!l||l.has_thumb||!l.has_glb||autoThumbs.done[l.id])continue;autoThumbs.done[l.id]=1;try{const b=await snap(l.id,480,300,'35deg 65deg auto');await post('/api/thumb',{version_id:l.id,data:b});l.has_thumb=true;l.thumbv=Date.now();render();if(sel==p.id)showPart(sel,1)}catch(e){}}}finally{thumbBusy=false}}
autoThumbs.done={};
async function saveThumbFromView(id){const mv=document.querySelector('#dbody model-viewer');if(!mv)return;try{const b=await mv.toBlob({mimeType:'image/png',idealAspect:true});const rd=new FileReader();rd.onload=async()=>{try{await post('/api/thumb',{version_id:id,data:rd.result.split(',')[1]});msg('Vorschaubild gespeichert');const q=parts.find(x=>x.latest&&x.latest.id==id);if(q)q.latest.thumbv=Date.now();await reload()}catch(e){}};rd.readAsDataURL(b)}catch(e){msg('Konnte kein Bild erzeugen: '+e.message,1)}}
async function pic3d(pid){try{const d=await api('/api/part?id='+pid);const g=d.versions.find(x=>x.has_glb);if(g)view3d(g.id);else{const l=d.versions[0];dlg(`<h2 style="margin:0 0 8px">Keine 3D-Ansicht</h2><div style="margin:8px 0 12px">Zu diesem Teil gibt es noch keine GLB-Datei. In SolidCraft: Datei > Exportieren > GLB, dann hier hinzufuegen.</div><div class="btns"><button class="p" onclick="closeDlg();pickGlb(${l.id})">GLB-Datei waehlen ...</button><button onclick="closeDlg()">Schliessen</button></div>`)}}catch(e){msg(e.message,1)}}
function view3d(id){dlg(`<h2 style="margin:0 0 8px">3D-Ansicht</h2><model-viewer src="/glb?id=${id}" camera-controls auto-rotate shadow-intensity="1" onerror="msg('3D-Datei konnte nicht geladen werden',1)" style="width:100%;height:60vh;background:var(--bg);border-radius:8px"></model-viewer><div class="small mut" style="margin-top:6px">Ziehen = drehen, Mausrad = zoomen, Umschalt + Ziehen = verschieben</div><div class="btns" style="margin-top:8px"><button onclick="saveThumbFromView(${id})">Diese Ansicht als Vorschaubild speichern</button><button onclick="pickGlb(${id})">GLB ersetzen ...</button><button onclick="closeDlg()">Schliessen</button></div>`)}
function pickGlb(id){if(!id)return;const i=document.createElement('input');i.type='file';i.accept='.glb';i.onchange=()=>{const f=i.files[0];if(!f)return;const r=new FileReader();r.onload=async()=>{try{await post('/api/glb',{version_id:id,data:r.result.split(',')[1]});msg('3D-Ansicht gespeichert');await reload();view3d(id)}catch(e){}};r.readAsDataURL(f)};i.click()}
function dlg(h){$('#dbody').innerHTML=h;$('#dlg').style.display='flex'}function closeDlg(){$('#dlg').style.display='none'}
function newDlg(file){dlg(`<h2 style="margin:0 0 8px">Als neues Teil einchecken</h2><div class="mut small">${esc(file)}</div>
<label>Nummer (leer = automatisch)</label><input id="f_number"><label>Name *</label><input id="f_name" value="${esc(file.replace(/[.](sc|solidcraft)$/i,''))}"><label>Beschreibung</label><textarea id="f_description" rows="2"></textarea>
<label>Material</label><input id="f_material"><label>Projekt / Baugruppe</label><input id="f_project"><label>Konstrukteur</label><input id="f_designer" value="${esc(localStorage.designer||'')}"><label>Kommentar zur Version</label><input id="f_comment" value="Erste Version">
<div class="btns" style="margin-top:12px"><button class="p" onclick="doNew('${esc(file).replace(/'/g,"\\'")}')">Einchecken</button><button onclick="closeDlg()">Abbrechen</button></div>`)}
async function doNew(file){const g=k=>$('#f_'+k).value;localStorage.designer=g('designer');try{const r=await post('/api/checkin',{file,number:g('number'),name:g('name'),description:g('description'),material:g('material'),project:g('project'),designer:g('designer'),comment:g('comment')});closeDlg();msg('Eingecheckt: '+r.number+' Version '+r.version);tab(1);sel=r.part_id;reload()}catch(e){}}
function inDlg(id){const p=parts.find(x=>x.id==id);dlg(`<h2 style="margin:0 0 8px">Einchecken: ${esc(p.number)} - ${esc(p.name)}</h2><div class="mut small">Neue Version aus ${esc(p.co_file)}</div>
<label>Kommentar zur Version</label><input id="f_comment" autofocus><label>Konstrukteur</label><input id="f_designer" value="${esc(localStorage.designer||'')}">
<label><input type="checkbox" id="f_keep" checked style="width:auto"> Danach ausgecheckt lassen (weiterarbeiten)</label>
<div class="btns" style="margin-top:12px"><button class="p" onclick="doIn(${id})">Einchecken</button><button onclick="closeDlg()">Abbrechen</button></div>`)}
async function doIn(id){const p=parts.find(x=>x.id==id);localStorage.designer=$('#f_designer').value;try{const r=await post('/api/checkin',{file:p.co_file,part_id:id,comment:$('#f_comment').value,designer:$('#f_designer').value,keep_checked_out:$('#f_keep').checked});closeDlg();msg('Eingecheckt: Version '+r.version);reload()}catch(e){}}
$('#q').addEventListener('input',()=>{clearTimeout(window.qt);window.qt=setTimeout(reload,200)});
document.addEventListener('keydown',e=>{if(e.key=='Escape')closeDlg()});
window.addEventListener('focus',()=>reload().catch(()=>{}));reload();
</script></body></html>"""


def main():
    init()
    srv = None
    for port in range(PORT, PORT + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), H)
            break
        except OSError:
            continue
    if not srv:
        print("Kein freier Port.")
        sys.exit(1)
    url = "http://127.0.0.1:%d/" % srv.server_address[1]
    print("PDM laeuft: %s\nDaten: %s\nBeenden mit Strg+C." % (url, ROOT))
    if not os.environ.get("PDM_NO_BROWSER"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
