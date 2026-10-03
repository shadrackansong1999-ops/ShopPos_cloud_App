# ShopPOS Cloud Relay

The hosted counterpart to ShopPOS's built-in **Settings > Remote Access**
page. Your Electron app already has everything it needs to talk to this:
`python_api/remote/sync_agent.py` pushes sales, stock and more every 30
seconds, and `renderer/js/pages/remote.js` is the setup screen. This folder
is the piece that was missing: the server those talk to, plus a live owner
dashboard you can open from any browser or phone, covering one shop or
several.

**No changes needed on the desktop app side**: the endpoints below match
what it already calls, exactly.

## What it does

- `POST /api/register`: signup or settings update from ShopPOS's Remote
  Access screen. Registering again with the same email and password (for
  example, from a second till or a second location) attaches an
  additional shop to the same owner login, rather than creating a
  separate account.
- `POST /api/push`: receives sales, stock movements, low-stock alerts,
  full inventory, the registered user list and a daily snapshot every 30
  seconds, authenticated with the shop's key.
- `GET /login` + `/dashboard`: a web app the shop owner signs into (same
  email and password used at registration) with six sections:
  - **Overview**: today's revenue, sales count, discounts, low stock
    count, a 30-day trend chart, cashier performance and recent
    transactions.
  - **Sales History**: every synced sale, paginated, filterable by date
    range.
  - **Inventory**: current stock levels, reorder thresholds and prices,
    searchable by name or SKU.
  - **Product Logs**: the stock movement history (sales, restocks,
    adjustments), filterable by date range.
  - **Users**: the shop's registered cashiers and admins, their role and
    last login.
  - **Reports**: revenue by day, payment method mix and top-selling
    products for a chosen date range.
  - A shop switcher in the header if the owner has more than one shop,
    and a theme picker (Midnight, Daylight, Ocean, Sunset, Forest) that
    remembers the choice per browser.

## 1. Deploy

### Option A: Render Blueprint (easiest)

1. Push this `relay_api/` folder to a GitHub repo (or the whole ShopPOS
   repo; Render lets you point a Blueprint at a subfolder).
2. In Render: **New > Blueprint**, select the repo. `render.yaml` here
   provisions a free web service *and* a free Postgres database, and wires
   `DATABASE_URL`/`SECRET_KEY` automatically.
3. Wait for the build. Render gives you a URL like
   `https://shoppos-relay-xxxx.onrender.com`.

### Option B: Manual Render Web Service (matches the in-app guide)

1. **New > Web Service**, connect the repo, set the root directory to
   `ShopPOS_Cloud/relay_api`.
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
4. Add an environment variable `SECRET_KEY` set to any random 32+
   character string.
5. **Storage matters**: Render's free plan has an *ephemeral* disk, it
   wipes on every deploy/restart. Either:
   - add a free **Render Postgres** instance and set `DATABASE_URL` to its
     connection string (the relay auto-detects and uses it), **or**
   - attach a Render **Persistent Disk** at `/data` and set
     `RELAY_DB_PATH=/data/relay.db`.
   Without one of these, a redeploy loses all synced data (fine for
   testing, not for real use).

### Option C: Anywhere else

It's a plain Flask app behind gunicorn: Railway, Fly.io, a small VPS, or
Docker all work the same way. Set `SECRET_KEY` and, ideally,
`DATABASE_URL`.

## Installing it as an app

The dashboard is a installable web app (PWA), so a shop owner can add it to
their phone's home screen or their computer's dock/start menu and open it
like a native app, with its own icon and no browser address bar.

- **Android/Chrome/Edge**: visit the dashboard, then either tap the install
  icon in the address bar, or use the "Install ShopPOS Cloud on this
  device" link that appears on the sign-in page.
- **iPhone/iPad (Safari)**: open the dashboard, tap Share, then
  "Add to Home Screen". iOS does not allow a one-tap install prompt from
  the page itself, this is the only path Apple provides.
- **Desktop (Chrome/Edge)**: an install icon appears in the address bar
  when visiting the dashboard.

Once installed, reopening the app shows the last-known numbers instantly
from a local cache, then refreshes in the background. Signing in, pushing
data, and signing out always go straight to the network and are never
cached.

## 2. Connect ShopPOS to it

1. Open ShopPOS, then Settings, then Remote Access.
2. Paste the relay URL (for example `https://shoppos-relay-xxxx.onrender.com`).
3. Enter an owner email and password (this becomes the dashboard login).
4. Click **Enable Remote Access**. The sync agent starts pushing every 30s.
5. Click **Open Owner Dashboard**, or just visit `<relay-url>/dashboard`
   from any browser and sign in.

To add a second shop or till under the same login, repeat step 2 to 4 on
that till, using the exact same owner email and password. It will show up
as a second option in the dashboard's shop switcher rather than a
separate account.

## Notes on the free tier

Render's free web services sleep after 15 minutes idle. The sync agent's
next push wakes it up, but that first push can take 30 to 60 seconds, and
occasionally the wake-up connection is dropped and needs one automatic
retry (already built in). The paid plan keeps it always on, worth it once
you are relying on this for live monitoring rather than checking in
occasionally.

## Security notes

- Passwords are SHA-256 hashed **before they leave the browser or the
  desktop app**: the relay never sees or stores a plaintext password. This
  matches the hashing scheme already used by `sync_agent.py`. It is
  adequate for this use case but not bank-grade; if you want stronger
  protection, swap in `werkzeug.security.generate_password_hash`
  (bcrypt or scrypt) on both sides.
- Each shop's `shop_key` (used to authenticate `/api/push` and settings
  updates) is stored hashed, never in plaintext.
- A shop can only be updated using its own current `shop_key`, not just
  by knowing its owner's email, so one shop cannot be hijacked by
  re-registering with a guessed or reused email.
- Every dashboard data endpoint checks that the requested shop actually
  belongs to the signed-in owner before returning anything; a shop ID
  that belongs to someone else returns a generic not-found response.
- Registration and login failures use the same generic wording whether
  the email exists or not, so the API does not confirm which emails are
  already registered.

## Multi-shop hosting

One relay deployment can host many owners, and each owner can own several
shops. Registering with a new email creates a new owner; registering
again with an existing owner's correct email and password attaches
another shop to that same owner instead of creating a duplicate account.
The dashboard's shop switcher lets an owner move between their shops
without signing out.
