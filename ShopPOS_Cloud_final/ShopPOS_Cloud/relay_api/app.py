"""
ShopPOS Cloud Relay API
========================
The hosted counterpart to the sync agent built into ShopPOS
(python_api/remote/sync_agent.py) and its Settings > Remote Access page
(renderer/js/pages/remote.js).

One owner login can now own several shops (locations). Endpoints the
desktop app calls directly:

    POST /api/register   - shop signup/update, returns shop_id
    POST /api/push        - sync agent pushes data every 30s
                            (auth: X-Shop-Key header)

Everything else is the owner-facing dashboard: login, a shop switcher,
and data endpoints for Overview, Sales History, Inventory, Users,
Product Logs and Reports - each date-range aware where it makes sense.
"""

import os
import json
import hashlib
import secrets
from datetime import datetime, timedelta, date

from flask import Flask, request, jsonify, session, render_template, redirect, url_for, send_from_directory

import db

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") != "development"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)

if not os.environ.get("SECRET_KEY"):
    print("[warn] SECRET_KEY not set - using a random key that resets on every "
          "restart, which will log everyone out. Set SECRET_KEY in your host's "
          "environment variables.")

db.init_db()


# ── Helpers ──────────────────────────────────────────────────────────────
def ok(**data):
    return jsonify({"ok": True, **data})


def err(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def current_owner():
    owner_id = session.get("owner_id")
    if not owner_id:
        return None
    return db.query_one("SELECT * FROM owners WHERE id=?", (owner_id,))


def require_login():
    return current_owner()


def owners_shops(owner_id):
    return db.query_all(
        "SELECT id, shop_name, low_stock_count, last_seen FROM shops "
        "WHERE owner_id=? ORDER BY shop_name ASC",
        (owner_id,),
    )


def authorize_shop(owner_id, shop_id):
    """Returns the shop row if it belongs to this owner, else None."""
    if not shop_id:
        return None
    return db.query_one("SELECT * FROM shops WHERE id=? AND owner_id=?", (shop_id, owner_id))


def parse_date_range():
    """?from=YYYY-MM-DD&to=YYYY-MM-DD, defaulting to the last 30 days."""
    today = date.today()
    default_from = (today - timedelta(days=30)).isoformat()
    d_from = request.args.get("from") or default_from
    d_to   = request.args.get("to") or today.isoformat()
    return d_from, d_to


def parse_page():
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = min(200, max(1, int(request.args.get("page_size", 50))))
    except ValueError:
        page_size = 50
    return page, page_size


# ── Registration (called from ShopPOS Settings > Remote Access) ────────────
@app.route("/api/register", methods=["POST"])
def api_register():
    d = request.get_json(silent=True) or {}
    shop_name    = (d.get("shop_name") or "My Shop").strip()
    admin_email  = (d.get("admin_email") or "").strip().lower()
    pw_hash      = d.get("admin_password") or None   # already sha256'd by the client; null = "keep existing"
    shop_key     = d.get("shop_key") or ""
    shop_id_in   = d.get("shop_id")   # present only when updating an existing shop
    auth_header  = request.headers.get("X-Shop-Key", "")

    if not admin_email:
        return err("admin_email is required")
    if not shop_key:
        return err("shop_key is required")

    if shop_id_in:
        # ── Update an existing shop ── authenticated with its own current
        # shop_key (proves this call really comes from that shop's install).
        shop = db.query_one("SELECT * FROM shops WHERE id=?", (shop_id_in,))
        if not shop or shop["shop_key_hash"] != sha256(auth_header):
            return err("Invalid shop_id or shop_key", 401)

        db.execute("UPDATE shops SET shop_name=?, shop_key_hash=? WHERE id=?",
                   (shop_name, sha256(shop_key), shop_id_in))
        if pw_hash:
            db.execute("UPDATE owners SET password_hash=? WHERE id=?", (pw_hash, shop["owner_id"]))
        return ok(shop_id=shop_id_in, message="Shop settings updated")

    # ── New shop registration ──
    if not pw_hash:
        return err("admin_password is required")

    owner = db.query_one("SELECT * FROM owners WHERE email=?", (admin_email,))
    if owner:
        if owner["password_hash"] != pw_hash:
            return err("Incorrect email or password", 401)
        owner_id = owner["id"]
    else:
        owner_id = secrets.token_hex(12)
        db.execute("INSERT INTO owners (id, email, password_hash) VALUES (?, ?, ?)",
                   (owner_id, admin_email, pw_hash))

    shop_id = secrets.token_hex(12)
    db.execute(
        "INSERT INTO shops (id, owner_id, shop_name, shop_key_hash) VALUES (?, ?, ?, ?)",
        (shop_id, owner_id, shop_name, sha256(shop_key)),
    )
    return ok(shop_id=shop_id, message="Shop registered")


# ── Push ingestion (called every 30s by remote/sync_agent.py) ──────────────
@app.route("/api/push", methods=["POST"])
def api_push():
    shop_key = request.headers.get("X-Shop-Key", "")
    d = request.get_json(silent=True) or {}
    shop_id = d.get("shop_id") or ""

    if not shop_id or not shop_key:
        return err("shop_id and X-Shop-Key header are required", 401)

    shop = db.query_one("SELECT * FROM shops WHERE id=?", (shop_id,))
    if not shop or shop["shop_key_hash"] != sha256(shop_key):
        return err("Invalid shop_id or shop_key", 401)

    sales      = d.get("sales") or []
    movements  = d.get("movements") or []
    low_stock  = d.get("low_stock") or []
    inventory  = d.get("inventory") or []
    users      = d.get("users") or []
    snapshot   = d.get("snapshot") or {}

    for s in sales:
        db.execute(
            """INSERT INTO sales
                 (shop_id, remote_sale_id, invoice_number, customer_name, cashier,
                  subtotal, discount, total, amount_paid, change_given,
                  payment_method, status, notes, items_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (shop_id, remote_sale_id) DO UPDATE SET
                  status=excluded.status, notes=excluded.notes""",
            (shop_id, s.get("id"), s.get("invoice_number"), s.get("customer_name"),
             s.get("cashier"), s.get("subtotal"), s.get("discount"), s.get("total"),
             s.get("amount_paid"), s.get("change_given"), s.get("payment_method"),
             s.get("status"), s.get("notes"), json.dumps(s.get("items") or []),
             s.get("created_at")),
        )

    for m in movements:
        db.execute(
            """INSERT INTO movements
                 (shop_id, remote_move_id, product_name, movement_type, quantity,
                  reference, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT (shop_id, remote_move_id) DO NOTHING""",
            (shop_id, m.get("id"), m.get("product_name"), m.get("movement_type"),
             m.get("quantity"), m.get("reference"), m.get("notes"), m.get("created_at")),
        )

    db.execute(
        "UPDATE shops SET low_stock_json=?, low_stock_count=?, inventory_json=?, "
        "users_json=?, last_seen=? WHERE id=?",
        (json.dumps(low_stock), len(low_stock), json.dumps(inventory),
         json.dumps(users), datetime.utcnow().isoformat(), shop_id),
    )

    if snapshot.get("date"):
        by_payment = json.dumps(snapshot.get("by_payment") or [])
        by_cashier = json.dumps(snapshot.get("by_cashier") or [])
        params = (
            shop_id, snapshot["date"],
            snapshot.get("total_revenue", 0), snapshot.get("num_sales", 0),
            snapshot.get("total_discounts", 0), snapshot.get("total_collected", 0),
            by_payment, by_cashier, snapshot.get("low_stock_count", 0),
        )
        now_fn = "now()" if db.USE_POSTGRES else "datetime('now')"
        db.execute(
            f"""INSERT INTO snapshots
                  (shop_id, date, total_revenue, num_sales, total_discounts,
                   total_collected, by_payment_json, by_cashier_json, low_stock_count)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT (shop_id, date) DO UPDATE SET
                  total_revenue=excluded.total_revenue, num_sales=excluded.num_sales,
                  total_discounts=excluded.total_discounts, total_collected=excluded.total_collected,
                  by_payment_json=excluded.by_payment_json, by_cashier_json=excluded.by_cashier_json,
                  low_stock_count=excluded.low_stock_count, updated_at={now_fn}""",
            params,
        )

    return ok(received={"sales": len(sales), "movements": len(movements)})


# ── Owner login / shop switcher ─────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    return redirect(url_for("dashboard") if current_owner() else url_for("login"))


@app.route("/login", methods=["GET"])
def login():
    if current_owner():
        return redirect(url_for("dashboard"))
    return render_template("login.html", error=None)


@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.get_json(silent=True) or {}
    email = (d.get("email") or "").strip().lower()
    password_hash = d.get("password_hash") or ""

    owner = db.query_one("SELECT * FROM owners WHERE email=?", (email,))
    if not owner or owner["password_hash"] != password_hash:
        return err("Incorrect email or password", 401)

    session.permanent = True
    session["owner_id"] = owner["id"]
    return ok()


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/dashboard")
def dashboard():
    owner = require_login()
    if not owner:
        return redirect(url_for("login"))
    shops = owners_shops(owner["id"])
    if not shops:
        return render_template("dashboard.html", shops=[], shop_id=None, shop_name=None)
    shop_id = request.args.get("shop") or shops[0]["id"]
    if not any(s["id"] == shop_id for s in shops):
        shop_id = shops[0]["id"]
    shop_name = next(s["shop_name"] for s in shops if s["id"] == shop_id)
    return render_template("dashboard.html", shops=shops, shop_id=shop_id, shop_name=shop_name)


@app.route("/api/shops")
def api_shops():
    owner = require_login()
    if not owner:
        return err("Not authenticated", 401)
    return ok(shops=owners_shops(owner["id"]))


# ── Dashboard: Overview ──────────────────────────────────────────────────────
@app.route("/api/dashboard/data")
def api_dashboard_data():
    owner = require_login()
    if not owner:
        return err("Not authenticated", 401)
    shop = authorize_shop(owner["id"], request.args.get("shop_id"))
    if not shop:
        return err("Unknown shop", 404)

    shop_id = shop["id"]
    today = datetime.utcnow().strftime("%Y-%m-%d")
    today_snap = db.query_one("SELECT * FROM snapshots WHERE shop_id=? AND date=?", (shop_id, today))

    since = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    trend_rows = db.query_all(
        "SELECT date, total_revenue, num_sales FROM snapshots WHERE shop_id=? AND date>=? ORDER BY date",
        (shop_id, since),
    )

    recent = db.query_all(
        """SELECT invoice_number, customer_name, cashier, total, payment_method,
                  status, created_at
           FROM sales WHERE shop_id=? ORDER BY received_at DESC LIMIT 20""",
        (shop_id,),
    )

    return ok(
        shop_name=shop["shop_name"],
        last_seen=shop["last_seen"],
        today={
            "revenue":    (today_snap or {}).get("total_revenue", 0),
            "num_sales":  (today_snap or {}).get("num_sales", 0),
            "discounts":  (today_snap or {}).get("total_discounts", 0),
            "by_payment": json.loads((today_snap or {}).get("by_payment_json") or "[]"),
            "by_cashier": json.loads((today_snap or {}).get("by_cashier_json") or "[]"),
        },
        trend=trend_rows,
        recent_sales=recent,
        low_stock=json.loads(shop["low_stock_json"] or "[]")[:10],
        low_stock_count=shop["low_stock_count"] or 0,
    )


# ── Dashboard: Sales History (paginated, date range) ────────────────────────
@app.route("/api/dashboard/sales")
def api_dashboard_sales():
    owner = require_login()
    if not owner:
        return err("Not authenticated", 401)
    shop = authorize_shop(owner["id"], request.args.get("shop_id"))
    if not shop:
        return err("Unknown shop", 404)

    d_from, d_to = parse_date_range()
    page, page_size = parse_page()
    offset = (page - 1) * page_size

    total = db.query_one(
        "SELECT COUNT(*) as c FROM sales WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ?",
        (shop["id"], d_from, d_to),
    )["c"]

    rows = db.query_all(
        """SELECT invoice_number, customer_name, cashier, subtotal, discount, total,
                  amount_paid, payment_method, status, created_at
           FROM sales
           WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ?
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (shop["id"], d_from, d_to, page_size, offset),
    )

    return ok(sales=rows, page=page, page_size=page_size, total=total,
               from_date=d_from, to_date=d_to)


# ── Dashboard: Product Logs (stock movements, paginated, date range) ────────
@app.route("/api/dashboard/product-logs")
def api_dashboard_product_logs():
    owner = require_login()
    if not owner:
        return err("Not authenticated", 401)
    shop = authorize_shop(owner["id"], request.args.get("shop_id"))
    if not shop:
        return err("Unknown shop", 404)

    d_from, d_to = parse_date_range()
    page, page_size = parse_page()
    offset = (page - 1) * page_size

    total = db.query_one(
        "SELECT COUNT(*) as c FROM movements WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ?",
        (shop["id"], d_from, d_to),
    )["c"]

    rows = db.query_all(
        """SELECT product_name, movement_type, quantity, reference, notes, created_at
           FROM movements
           WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ?
           ORDER BY created_at DESC
           LIMIT ? OFFSET ?""",
        (shop["id"], d_from, d_to, page_size, offset),
    )

    return ok(logs=rows, page=page, page_size=page_size, total=total,
               from_date=d_from, to_date=d_to)


# ── Dashboard: Inventory ─────────────────────────────────────────────────────
@app.route("/api/dashboard/inventory")
def api_dashboard_inventory():
    owner = require_login()
    if not owner:
        return err("Not authenticated", 401)
    shop = authorize_shop(owner["id"], request.args.get("shop_id"))
    if not shop:
        return err("Unknown shop", 404)

    items = json.loads(shop["inventory_json"] or "[]")
    q = (request.args.get("q") or "").strip().lower()
    if q:
        items = [i for i in items if q in (i.get("name") or "").lower()
                 or q in (i.get("sku") or "").lower()]
    return ok(inventory=items, count=len(items))


# ── Dashboard: Users ──────────────────────────────────────────────────────────
@app.route("/api/dashboard/users")
def api_dashboard_users():
    owner = require_login()
    if not owner:
        return err("Not authenticated", 401)
    shop = authorize_shop(owner["id"], request.args.get("shop_id"))
    if not shop:
        return err("Unknown shop", 404)

    return ok(users=json.loads(shop["users_json"] or "[]"))


# ── Dashboard: Reports (revenue trend, top products, payment mix) ───────────
@app.route("/api/dashboard/reports")
def api_dashboard_reports():
    owner = require_login()
    if not owner:
        return err("Not authenticated", 401)
    shop = authorize_shop(owner["id"], request.args.get("shop_id"))
    if not shop:
        return err("Unknown shop", 404)

    d_from, d_to = parse_date_range()
    shop_id = shop["id"]

    by_day = db.query_all(
        """SELECT date, total_revenue, num_sales, total_discounts
           FROM snapshots WHERE shop_id=? AND date BETWEEN ? AND ?
           ORDER BY date""",
        (shop_id, d_from, d_to),
    )

    totals = db.query_one(
        """SELECT COALESCE(SUM(total_revenue),0) as revenue,
                  COALESCE(SUM(num_sales),0) as num_sales,
                  COALESCE(SUM(total_discounts),0) as discounts
           FROM snapshots WHERE shop_id=? AND date BETWEEN ? AND ?""",
        (shop_id, d_from, d_to),
    )

    payment_rows = db.query_all(
        "SELECT by_payment_json FROM snapshots WHERE shop_id=? AND date BETWEEN ? AND ?",
        (shop_id, d_from, d_to),
    )
    payment_totals = {}
    for r in payment_rows:
        for p in json.loads(r["by_payment_json"] or "[]"):
            m = p.get("method") or "Unknown"
            payment_totals[m] = payment_totals.get(m, 0) + float(p.get("revenue") or 0)
    by_payment = [{"method": k, "revenue": v} for k, v in
                  sorted(payment_totals.items(), key=lambda x: -x[1])]

    # Top products - parsed from each sale's stored line items within range.
    sale_rows = db.query_all(
        "SELECT items_json FROM sales WHERE shop_id=? AND DATE(created_at) BETWEEN ? AND ? "
        "AND status='completed'",
        (shop_id, d_from, d_to),
    )
    product_totals = {}
    for r in sale_rows:
        for item in json.loads(r["items_json"] or "[]"):
            name = item.get("product_name") or "Unknown"
            entry = product_totals.setdefault(name, {"qty": 0, "revenue": 0})
            entry["qty"] += float(item.get("quantity") or 0)
            entry["revenue"] += float(item.get("total") or 0)
    top_products = sorted(
        [{"product_name": k, **v} for k, v in product_totals.items()],
        key=lambda x: -x["revenue"],
    )[:15]

    return ok(
        from_date=d_from, to_date=d_to,
        totals=totals,
        by_day=by_day,
        by_payment=by_payment,
        top_products=top_products,
    )


@app.route("/sw.js")
def service_worker():
    # Served from the root (not /static/sw.js) so its scope covers the
    # whole site rather than just the static folder - a service worker
    # can only control paths at or below the URL it was served from.
    resp = send_from_directory(app.static_folder, "sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.route("/api/health")
def health():
    return ok(status="up", backend="postgres" if db.USE_POSTGRES else "sqlite")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
