import os
import uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# --------- CONTURI CU ACCES LA "LECTII" ---------
ALLOWED_LESSONS = {"admin@vianubio", "membriiaccount"}

# -------------------- APP & CONFIG --------------------
app = Flask(__name__, static_url_path="/static", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")  # schimbă în producție

# DB: Postgres (DATABASE_URL) sau fallback la SQLite pt. dev
db_url = os.getenv("DATABASE_URL", "sqlite:///local.db").replace("postgres://", "postgresql://")
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Căi ABSOLUTE pentru static & uploads (evită surprize cu CWD)
STATIC_DIR = os.path.join(app.root_path, "static")
IMAGES_DIR = os.path.join(STATIC_DIR, "images")
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOADS_DIR

# Extensii acceptate
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}

db = SQLAlchemy(app)

# -------------------- MODELE --------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    section = db.Column(db.String(32), nullable=False)   # 'insta' | 'articles' | 'gallery' | 'lectii'
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, default="")
    image_url = db.Column(db.String(512))                # cover (poate fi /static/uploads/..., nume simplu sau URL extern)
    external_url = db.Column(db.String(512))             # ex: link Instagram / extern
    ppt_url = db.Column(db.String(512))                  # link prezentare (Google Slides / PPT public)
    author = db.Column(db.String(128), default="Club BIO")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -------------------- INIT DB + MIGRĂRI LITE --------------------
_tables_ready = False

def _ensure_users():
    """Creează/actualizează cele 2 conturi cerute."""
    wanted = {
        "admin@vianubio": "parola123",
        "membriiaccount": "weluvbio",
    }
    for email, pwd in wanted.items():
        u = User.query.filter_by(email=email).first()
        if not u:
            db.session.add(User(email=email, password_hash=generate_password_hash(pwd)))
    db.session.commit()

def _ensure_columns():
    """
    Adaugă coloana 'ppt_url' în Post dacă lipsește.
    Funcționează pentru SQLite și Postgres (ignoră eroarea dacă deja există).
    """
    try:
        with db.engine.begin() as con:
            con.execute(text("ALTER TABLE post ADD COLUMN ppt_url VARCHAR(512);"))
    except Exception:
        # probabil există deja coloana; ignorăm
        pass

def _init_db_once():
    """Creează tabelele + utilizatorii + migrează coloanele o singură dată."""
    global _tables_ready
    if _tables_ready:
        return
    db.create_all()
    _ensure_columns()
    _ensure_users()
    _tables_ready = True

# pornire
with app.app_context():
    try:
        _init_db_once()
    except Exception as e:
        print("DB init will retry on first request:", e)

@app.before_request
def _maybe_init_db():
    global _tables_ready
    if not _tables_ready:
        try:
            _init_db_once()
        except Exception as e:
            print("DB init retry failed:", e)

# -------------------- HELPERS --------------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def can_view_lessons():
    user = session.get("user_email")
    return user in ALLOWED_LESSONS

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def _unique_filename(original_name: str) -> str:
    """
    Generează un nume unic prietenos:
    <uuid8>-<secure_basename>.<ext>
    """
    base = secure_filename(original_name)
    name, ext = os.path.splitext(base)
    uid = uuid.uuid4().hex[:8]
    return f"{uid}-{name}{ext.lower()}"

def _url_for_static(subpath: str) -> str:
    """Shortcut pentru url_for static (asigură formatarea corectă)."""
    return url_for("static", filename=subpath)

def _resolve_photo_src(name_or_url: str, placeholder_rel: str = "images/placeholder.jpg") -> str:
    """
    Regula de rezolvare pentru ABOUT (membri):
      - dacă începe cu http(s), îl folosim direct
      - altfel, căutăm mai întâi în static/images/<nume>
      - apoi în static/uploads/<nume>
      - dacă nu există, placeholder
    """
    if not name_or_url:
        return _url_for_static(placeholder_rel)

    s = name_or_url.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s

    # Dacă vine deja ca /static/..., îl returnăm ca atare
    if s.startswith("/static/"):
        return s

    # Altfel e doar un nume de fișier -> caută-l în images/ sau uploads/
    images_path = os.path.join(IMAGES_DIR, s)
    uploads_path = os.path.join(UPLOADS_DIR, s)
    if os.path.isfile(images_path):
        return _url_for_static(f"images/{s}")
    if os.path.isfile(uploads_path):
        return _url_for_static(f"uploads/{s}")

    return _url_for_static(placeholder_rel)

def _resolve_post_cover_src(image_url: str, placeholder_rel: str = "images/placeholder.jpg") -> str:
    """
    Regula de rezolvare pentru cover-ul postării:
      - dacă e URL absolut -> folosește-l
      - dacă începe cu /static/ -> folosește-l
      - dacă e doar nume de fișier -> /static/uploads/<nume> (dacă există), altfel placeholder
      - altfel placeholder
    """
    if image_url:
        s = image_url.strip()
        if s.startswith("http://") or s.startswith("https://") or s.startswith("/static/"):
            return s
        # doar nume fișier
        up = os.path.join(UPLOADS_DIR, s)
        if os.path.isfile(up):
            return _url_for_static(f"uploads/{s}")
    return _url_for_static(placeholder_rel)

# -------------------- ROUTE-URI PUBLICE --------------------
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/blog")
def blog():
    # Publicul NU vede secțiunea 'lectii'
    if not can_view_lessons():
        posts = (
            Post.query
            .filter(Post.section != "lectii")
            .order_by(Post.created_at.desc())
            .limit(100)
            .all()
        )
    else:
        posts = Post.query.order_by(Post.created_at.desc()).limit(100).all()

    # atașează cover_src pentru template (imaginea postării)
    for p in posts:
        p.cover_src = _resolve_post_cover_src(p.image_url)

    return render_template("blog.html", posts=posts, can_view_lessons=can_view_lessons())

# -------------------- ABOUT --------------------
@app.get("/about", endpoint="about")
def about():
    """
    Citește echipa din:
      1) static/data/team.json (default)
      2) fallback: content/team.json (dacă vrei să muți acolo)
    Pentru fiecare membru setează m['photo_src'] cu regulile robuste.
    """
    import json

    team = []
    candidates = [
        os.path.join(STATIC_DIR, "data", "team.json"),
        os.path.join(app.root_path, "content", "team.json"),
    ]
    data_path = next((p for p in candidates if os.path.exists(p)), None)

    if data_path:
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                team = json.load(f)
        except Exception as e:
            print("Eroare la citirea team.json:", e)
            team = []

    # rezolvă sursa de imagine pentru fiecare membru
    for m in team:
        photo = (m.get("photo") or "").strip()
        m["photo_src"] = _resolve_photo_src(photo)

    return render_template("about.html", team=team)

# -------------------- API PUBLICE --------------------
@app.get("/api/posts")
def api_posts():
    q = Post.query
    if not can_view_lessons():
        q = q.filter(Post.section != "lectii")
    posts = q.order_by(Post.created_at.desc()).all()

    # includem cover_src calculat
    payload = []
    for p in posts:
        payload.append(dict(
            id=p.id, section=p.section, title=p.title, content=p.content,
            image_url=p.image_url, external_url=p.external_url, ppt_url=p.ppt_url,
            author=p.author, created_at=p.created_at.isoformat(),
            cover_src=_resolve_post_cover_src(p.image_url),
        ))
    return jsonify(payload)

@app.get("/health")
def health():
    # mic sanity check pentru assets
    return {
        "ok": True,
        "static_images_exists": os.path.isdir(IMAGES_DIR),
        "static_uploads_exists": os.path.isdir(UPLOADS_DIR),
        "images_count": len(os.listdir(IMAGES_DIR)) if os.path.isdir(IMAGES_DIR) else 0,
        "uploads_count": len(os.listdir(UPLOADS_DIR)) if os.path.isdir(UPLOADS_DIR) else 0,
    }

# -------------------- AUTH --------------------
@app.get("/login", endpoint="login")
def login_page():
    return render_template("login.html")

@app.post("/login")
def login_action():
    email = request.form.get("email", "").strip()
    pwd = request.form.get("password", "")
    u = User.query.filter_by(email=email).first()
    if not u or not check_password_hash(u.password_hash, pwd):
        flash("Credențiale invalide", "error")
        return redirect(url_for("login"))
    session["logged_in"] = True
    session["user_email"] = u.email
    flash("Autentificat cu succes.", "success")
    return redirect(url_for("admin_new"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# -------------------- ADMIN --------------------
@app.get("/admin/new")
@login_required
def admin_new():
    # trimitem și un flag ca să afișezi opțiunea 'lectii' doar pentru userii cu drepturi
    return render_template("admin_new.html", can_view_lessons=can_view_lessons())

@app.post("/admin/new")
@login_required
def admin_new_post():
    section = request.form.get("section", "articles").strip().lower()
    title = request.form.get("title", "Untitled").strip()
    content = request.form.get("content", "").strip()
    external_url = request.form.get("external_url", "").strip()
    ppt_url = request.form.get("ppt_url", "").strip()
    author = session.get("user_email", "Admin")

    # Dacă utilizatorul NU are drept de 'lectii', blochează
    if section == "lectii" and not can_view_lessons():
        flash("Nu ai drepturi să publici în 'Lecții'.", "error")
        return redirect(url_for("admin_new"))

    # ---- Upload imagine cover (opțional) ----
    image_url = None
    f = request.files.get("image")
    if f and f.filename:
        if allowed_file(f.filename):
            fname = _unique_filename(f.filename)
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], fname)
            f.save(save_path)
            # stocăm doar numele (mai flexibil), nu URL-ul complet
            image_url = fname
        else:
            flash("Format imagine neacceptat (folosește png/jpg/jpeg/gif/webp/svg).", "error")

    p = Post(
        section=section, title=title, content=content,
        image_url=image_url, external_url=external_url,
        ppt_url=ppt_url,
        author=author
    )
    db.session.add(p)
    db.session.commit()

    flash("Postare publicată.", "success")
    return redirect(url_for("blog"))

# -------------------- MAIN --------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
