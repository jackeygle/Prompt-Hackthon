"""Shortlist management and sponsorship tracking: Discovery → Ranking → Shortlist → Sponsorship → Performance → History.

Kept deliberately separate (never merged into one score):
- campaign score / fit   system evaluation (ranking.py), untouched here
- favorite               marketer preference on a shortlist entry
- shortlisted            being considered for a campaign (existing `shortlist` table + position / favorite)
- sponsorship            Prenew decided to work with the creator (`sponsorships`)
- performance            what happened after paying them (`sponsorship_kpis`, one row per KPI, manually entered)
- historical feedback    derived from each sponsorship's own outcome, never stored as counters

Sponsorships survive deleting a campaign report (they are business records), so they snapshot the creator name,
platform, country and campaign label. Every derived number is deterministic and only computed from stored inputs.
"""
import uuid
from dataclasses import dataclass

import db

CURRENCY = "EUR"
CURRENCY_SYMBOL = {"EUR": "€"}
STATUSES = {"in_progress": "In progress", "done": "Done"}
OUTCOMES = {"positive": "👍 Positive", "neutral": "Not rated", "negative": "👎 Negative"}

# KPI registry: new KPI types are one line here; storage is one row per (sponsorship, kpi).
KPIS = {"views": "Views", "clicks": "Clicks", "referrals": "Referrals", "conversions": "Conversions",
        "revenue": "Revenue"}
MONEY_KPIS = {"revenue"}
# derived metric -> (label, numerator, denominator); "price" means the price paid
DERIVED = {"cost_per_view": ("Cost per view", "price", "views"),
           "cost_per_click": ("Cost per click", "price", "clicks"),
           "cost_per_referral": ("Cost per referral", "price", "referrals"),
           "cost_per_conversion": ("Cost per conversion", "price", "conversions"),
           "roas": ("ROAS", "revenue", "price")}

SCHEMA = """
CREATE TABLE IF NOT EXISTS shortlist (campaign_id TEXT, creator_id TEXT, added_at TEXT,
  PRIMARY KEY (campaign_id, creator_id));
CREATE TABLE IF NOT EXISTS sponsorships (
  id TEXT PRIMARY KEY, campaign_id TEXT, creator_id TEXT, status TEXT, price_paid REAL, currency TEXT,
  started_at TEXT, completed_at TEXT, outcome TEXT, notes TEXT,
  creator_name TEXT, platform TEXT, country TEXT, campaign_label TEXT);
CREATE TABLE IF NOT EXISTS sponsorship_kpis (sponsorship_id TEXT, kpi TEXT, value REAL,
  PRIMARY KEY (sponsorship_id, kpi));
"""


def ensure_schema() -> None:
    """Idempotent: creates the tables and adds position / favorite to an existing shortlist table."""
    for stmt in filter(str.strip, SCHEMA.split(";")):
        db.execute(stmt)
    cols = {r["name"] for r in db.query("PRAGMA table_info(shortlist)")}
    if "position" not in cols:
        db.execute("ALTER TABLE shortlist ADD COLUMN position INTEGER")
        db.execute("UPDATE shortlist SET position = rowid")  # keep the old "added" order
    if "favorite" not in cols:
        db.execute("ALTER TABLE shortlist ADD COLUMN favorite INTEGER DEFAULT 0")


# ------------------------------------------------------------------ shortlist: order + favorites
def shortlist(campaign_id: str) -> list[dict]:
    """Entries in the marketer's order."""
    return db.query("SELECT creator_id, position, favorite, added_at FROM shortlist WHERE campaign_id=? "
                    "ORDER BY position, added_at", (campaign_id,))


def shortlist_ids(campaign_id: str) -> list[str]:
    return [r["creator_id"] for r in shortlist(campaign_id)]


def add_to_shortlist(campaign_id: str, creator_id: str) -> None:
    nxt = db.query("SELECT COALESCE(MAX(position), 0) + 1 AS p FROM shortlist WHERE campaign_id=?",
                   (campaign_id,))[0]["p"]
    db.execute("INSERT OR IGNORE INTO shortlist (campaign_id, creator_id, added_at, position, favorite) "
               "VALUES (?,?,?,?,0)", (campaign_id, creator_id, db.now(), nxt))


def remove_from_shortlist(campaign_id: str, creator_id: str) -> None:
    db.execute("DELETE FROM shortlist WHERE campaign_id=? AND creator_id=?", (campaign_id, creator_id))


def move(campaign_id: str, creator_id: str, step: int) -> None:
    """Swap with the neighbour above (step=-1) or below (step=+1); positions are renumbered 1..n."""
    ids = shortlist_ids(campaign_id)
    if creator_id not in ids:
        return
    i = ids.index(creator_id)
    j = i + step
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    db.executemany("UPDATE shortlist SET position=? WHERE campaign_id=? AND creator_id=?",
                   [(p, campaign_id, c) for p, c in enumerate(ids, 1)])


def toggle_favorite(campaign_id: str, creator_id: str) -> bool:
    db.execute("UPDATE shortlist SET favorite = 1 - COALESCE(favorite, 0) WHERE campaign_id=? AND creator_id=?",
               (campaign_id, creator_id))
    rows = db.query("SELECT favorite FROM shortlist WHERE campaign_id=? AND creator_id=?", (campaign_id, creator_id))
    return bool(rows and rows[0]["favorite"])


PLATFORM_PREFIX = {"yt": "youtube", "twitch": "twitch", "instagram": "instagram", "tiktok": "tiktok"}


def platform_of(creator_id: str) -> str:
    """Canonical creator IDs carry their platform ('yt:…', 'twitch:…', 'instagram:…', 'tiktok:<handle>')."""
    return PLATFORM_PREFIX.get(creator_id.split(":", 1)[0], "youtube")


def shortlist_groups() -> dict[str, dict]:
    """Global Shortlists = the existing shortlist table grouped by the search (campaign) that produced it."""
    groups: dict[str, dict] = {}
    for r in db.query("SELECT campaign_id, creator_id, favorite FROM shortlist ORDER BY campaign_id, position, added_at"):
        g = groups.setdefault(r["campaign_id"], {"n": 0, "favorites": 0, "platforms": [], "creators": []})
        g["n"] += 1
        g["favorites"] += int(r["favorite"] or 0)
        g["creators"].append(r["creator_id"])
        if (p := platform_of(r["creator_id"])) not in g["platforms"]:
            g["platforms"].append(p)
    return groups


def sponsorship_groups(rows: list[dict]) -> dict[str, dict]:
    """Sponsorships grouped by the campaign they came from (kept even if that report was deleted)."""
    groups: dict[str, dict] = {}
    for r in rows:
        g = groups.setdefault(r["campaign_id"], {"label": r["campaign_label"], "rows": []})
        g["rows"].append(r)
    for g in groups.values():
        g.update(summary(g["rows"]))
    return groups


# ------------------------------------------------------------------ sponsorships
def start_sponsorship(campaign_id: str, creator_id: str, *, creator_name: str, platform: str, country: str | None,
                      campaign_label: str, currency: str = CURRENCY) -> str:
    """One active sponsorship per creator and campaign; returns the active one if it already exists."""
    active = db.query("SELECT id FROM sponsorships WHERE campaign_id=? AND creator_id=? AND status='in_progress'",
                      (campaign_id, creator_id))
    if active:
        return active[0]["id"]
    sid = "s_" + uuid.uuid4().hex[:10]
    db.execute("INSERT INTO sponsorships VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (sid, campaign_id, creator_id, "in_progress", None, currency, db.now(), None, "neutral", "",
                creator_name, platform, country, campaign_label))
    return sid


def get(sponsorship_id: str) -> dict | None:
    rows = db.query("SELECT * FROM sponsorships WHERE id=?", (sponsorship_id,))
    return rows[0] if rows else None


def set_status(sponsorship_id: str, status: str) -> None:
    assert status in STATUSES
    done_at = db.now() if status == "done" else None
    db.execute("UPDATE sponsorships SET status=?, completed_at=CASE WHEN ?='done' THEN COALESCE(completed_at, ?) "
               "ELSE NULL END WHERE id=?", (status, status, done_at, sponsorship_id))


def set_outcome(sponsorship_id: str, outcome: str) -> None:
    """One outcome per sponsorship (changeable), so feedback can't be inflated by repeated clicks."""
    assert outcome in OUTCOMES
    db.execute("UPDATE sponsorships SET outcome=? WHERE id=?", (outcome, sponsorship_id))


def update_performance(sponsorship_id: str, price_paid: float | None, kpis: dict[str, float | None],
                       notes: str | None = None) -> None:
    """Manually entered business data. None / empty means 'not recorded' and removes a stored value."""
    db.execute("UPDATE sponsorships SET price_paid=? WHERE id=?", (_clean(price_paid), sponsorship_id))
    if notes is not None:
        db.execute("UPDATE sponsorships SET notes=? WHERE id=?", (notes.strip(), sponsorship_id))
    for k, v in kpis.items():
        assert k in KPIS, k
        if (v := _clean(v)) is None:
            db.execute("DELETE FROM sponsorship_kpis WHERE sponsorship_id=? AND kpi=?", (sponsorship_id, k))
        else:
            db.execute("INSERT OR REPLACE INTO sponsorship_kpis VALUES (?,?,?)", (sponsorship_id, k, v))


def _clean(v) -> float | None:
    if v is None or v == "":
        return None
    v = float(v)
    if v < 0:
        raise ValueError("values cannot be negative")
    return v


def kpis(sponsorship_id: str) -> dict[str, float]:
    return {r["kpi"]: r["value"] for r in db.query("SELECT kpi, value FROM sponsorship_kpis WHERE sponsorship_id=?",
                                                   (sponsorship_id,))}


def derived(price_paid: float | None, kpi: dict[str, float]) -> dict[str, float]:
    """Only metrics whose inputs are all recorded and whose divisor is > 0. Never guessed."""
    vals = {**kpi, "price": price_paid}
    out = {}
    for name, (_, num, den) in DERIVED.items():
        a, b = vals.get(num), vals.get(den)
        if a is not None and b:
            out[name] = a / b
    return out


def sponsorships(campaign_id: str | None = None, creator_id: str | None = None) -> list[dict]:
    where, args = [], []
    if campaign_id:
        where.append("campaign_id=?"); args.append(campaign_id)
    if creator_id:
        where.append("creator_id=?"); args.append(creator_id)
    sql = "SELECT * FROM sponsorships" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY started_at DESC"
    rows = db.query(sql, tuple(args))
    for r in rows:
        r["kpis"] = kpis(r["id"])
        r["derived"] = derived(r["price_paid"], r["kpis"])
    return rows


def active_for(campaign_id: str) -> dict[str, dict]:
    """creator_id -> latest sponsorship of this campaign (for shortlist badges)."""
    out: dict[str, dict] = {}
    for r in db.query("SELECT * FROM sponsorships WHERE campaign_id=? ORDER BY started_at", (campaign_id,)):
        out[r["creator_id"]] = r
    return out


# ------------------------------------------------------------------ creator history (derived, never stored)
@dataclass
class History:
    sponsorships: int = 0
    completed: int = 0
    in_progress: int = 0
    total_spend: float | None = None
    total_revenue: float | None = None
    positive: int = 0
    negative: int = 0
    roas: float | None = None   # revenue / spend over sponsorships where both were recorded

    @property
    def any(self) -> bool:
        return self.sponsorships > 0


def history(creator_id: str) -> History:
    rows = sponsorships(creator_id=creator_id)
    h = History(sponsorships=len(rows), completed=sum(r["status"] == "done" for r in rows),
                in_progress=sum(r["status"] == "in_progress" for r in rows),
                positive=sum(r["outcome"] == "positive" for r in rows),
                negative=sum(r["outcome"] == "negative" for r in rows))
    spend = [r["price_paid"] for r in rows if r["price_paid"] is not None]
    revenue = [r["kpis"]["revenue"] for r in rows if "revenue" in r["kpis"]]
    h.total_spend = sum(spend) if spend else None
    h.total_revenue = sum(revenue) if revenue else None
    paired = [(r["kpis"]["revenue"], r["price_paid"]) for r in rows if "revenue" in r["kpis"] and r["price_paid"]]
    if paired:
        h.roas = sum(a for a, _ in paired) / sum(b for _, b in paired)
    return h


def summary(rows: list[dict]) -> dict:
    """Dashboard totals from stored data only (None when nothing was recorded)."""
    spend = [r["price_paid"] for r in rows if r["price_paid"] is not None]
    revenue = [r["kpis"]["revenue"] for r in rows if "revenue" in r["kpis"]]
    return {"active": sum(r["status"] == "in_progress" for r in rows),
            "completed": sum(r["status"] == "done" for r in rows),
            "spend": sum(spend) if spend else None, "revenue": sum(revenue) if revenue else None}
