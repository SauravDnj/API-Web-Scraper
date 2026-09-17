"""One Tool per Serper API. Each tool knows how to turn the tab's form into
tasks, call its own endpoint, normalize the response into flat rows, and
which columns / filters / stats the UI and exports should use."""
import json
import math
import re
from urllib.parse import urlparse

# ---------------------------------------------------------------- helpers

COUNTRIES = {"india", "united states", "usa", "united kingdom", "uk", "united arab emirates", "uae",
             "canada", "australia", "singapore", "saudi arabia", "germany"}
PINCODE_RE = re.compile(r"(?<!\d)(\d{6}|\d{5}(?:-\d{4})?)(?!\d)")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
EMAIL_JUNK = re.compile(r"\.(png|jpe?g|gif|webp|svg|css|js)$|@(example|domain|email|sentry)\.", re.I)
PHONE_RES = [
    re.compile(r"(?<![\d+])(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"),  # IN mobile
    re.compile(r"(?<![\d+])0\d{2,4}[\s-]\d{6,8}(?!\d)"),  # IN landline
    re.compile(r"\+\d{1,3}[\s-]?\(?\d{2,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}(?!\d)"),  # intl
]
SOCIAL_RE = re.compile(
    r"https?://(?:www\.)?(?:facebook\.com|instagram\.com|linkedin\.com|twitter\.com|x\.com|youtube\.com|"
    r"wa\.me|api\.whatsapp\.com|t\.me|pinterest\.com)/[^\s\)\]\"'<>]+", re.I)
TIME_RANGES = [["", "Any time"], ["qdr:h", "Past hour"], ["qdr:d", "Past 24 hours"], ["qdr:w", "Past week"],
               ["qdr:m", "Past month"], ["qdr:y", "Past year"]]
PAGE_TEXT_LIMIT = 10000  # chars of page text / markdown kept per Webpage row (keeps saved jobs small)


def clean(s) -> str:
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip()


def split_lines(value) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[\n;]+", str(value or ""))
    seen, out = set(), []
    for item in items:
        item = clean(item)
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def split_label(line: str) -> tuple[str, str]:
    """'Business Name | value' -> ('Business Name', 'value'); 'value' -> ('', 'value')."""
    if " | " in line:
        label, value = line.rsplit(" | ", 1)
        return clean(label), clean(value)
    return "", clean(line)


def domain_of(url: str) -> str:
    try:
        host = urlparse(url or "").netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def to_int(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def find_phones(text: str) -> list[str]:
    out = []
    for rx in PHONE_RES:
        for m in rx.findall(text or ""):
            m = clean(m)
            if m not in out:
                out.append(m)
    return out


def indian_mobile(phone) -> str:
    """'098250 12345' / '+91 98250-12345' -> '+91 98250 12345'. Landlines ('0261 246 5555',
    '079 2658 1234'), toll-free and other numbers -> ''."""
    for part in re.split(r"[,/;]", str(phone or "")):
        groups = re.findall(r"\d+", part)
        digits = "".join(groups)
        if len(digits) == 12 and digits.startswith("91"):
            digits = digits[2:]
            groups = groups[1:] if groups and groups[0] == "91" else [digits]
        elif len(digits) == 11 and digits.startswith("0"):
            digits = digits[1:]
            groups = [groups[0][1:]] + groups[1:] if groups else groups
        if not re.fullmatch(r"[6-9]\d{9}", digits):
            continue
        if len(groups) > 1 and 2 <= len(groups[0]) <= 4:  # "674 234 5678" = STD code + landline
            continue
        return f"+91 {digits[:5]} {digits[5:]}"
    return ""


def find_emails(text: str) -> list[str]:
    return list(dict.fromkeys(e for e in EMAIL_RE.findall(text or "") if not EMAIL_JUNK.search(e)))


def location_label(loc: dict) -> str:
    parts = [clean(loc.get(k)) for k in ("area", "city", "state")]
    label = ", ".join(p for p in parts if p)
    pin = clean(loc.get("pincode"))
    return f"{label} {pin}".strip() if pin else label


def parse_address(address: str) -> dict:
    """Best-effort split of 'Shop 1, X Road, Navrangpura, Ahmedabad, Gujarat 380009, India'."""
    out = {"area": "", "city": "", "state": "", "pincode": "", "country": ""}
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    if not parts:
        return out
    if parts[-1].lower() in COUNTRIES or (
            len(parts) >= 2 and not re.search(r"\d", parts[-1]) and PINCODE_RE.search(parts[-2])):
        out["country"] = parts.pop()
        if not parts:
            return out
    for i in range(len(parts) - 1, -1, -1):
        m = PINCODE_RE.search(parts[i])
        if m:
            out["pincode"] = m.group(1)
            rest = parts[:i]
            state = clean(PINCODE_RE.sub("", parts[i]))
            if state:
                out["state"] = state
            elif rest:
                out["state"] = rest.pop()
            if rest:
                out["city"] = rest.pop()
            if rest:
                out["area"] = rest.pop()
            return out
    if len(parts) >= 2:
        out["state"], out["city"] = parts[-1], parts[-2]
    if len(parts) >= 3:
        out["area"] = parts[-3]
    return out


def haversine_km(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def parse_ll(ll) -> tuple[float, float] | None:
    m = re.match(r"@?(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", str(ll or ""))
    return (float(m.group(1)), float(m.group(2))) if m else None


def distance_from(center, place: dict):
    try:
        return round(haversine_km(center[0], center[1], float(place["latitude"]), float(place["longitude"])), 2)
    except (TypeError, KeyError, ValueError):
        return ""


def grid_points(center, radius_km: float, spacing_km: float) -> list[tuple[float, float, tuple]]:
    """Lattice points within radius_km of center, nearest first. Points snap to a
    global lattice so overlapping locations of one job share (and skip) cells."""
    lat0, lng0 = center
    step_lat = spacing_km / 111.32
    step_lng = spacing_km / (111.32 * max(0.2, math.cos(math.radians(round(lat0)))))
    i0, j0 = round(lat0 / step_lat), round(lng0 / step_lng)
    n = int(radius_km / spacing_km) + 1
    pts = []
    for di in range(-n, n + 1):
        for dj in range(-n, n + 1):
            lat, lng = (i0 + di) * step_lat, (j0 + dj) * step_lng
            d = haversine_km(lat0, lng0, lat, lng)
            if d <= radius_km + spacing_km / 2:
                pts.append((d, lat, lng, (i0 + di, j0 + dj)))
    pts.sort(key=lambda p: p[0])
    return [(lat, lng, cell) for _, lat, lng, cell in pts]


def col(key, label, type_="text", show=True, **extra):
    return {"key": key, "label": label, "type": type_, "show": show, **extra}


# ---------------------------------------------------------------- base tools

class Tool:
    id = ""
    label = ""
    icon = ""
    description = ""
    endpoint = ""
    list_key = ""
    credits = 1           # credits per API call (for estimates)
    per_page = 10
    max_pages = 1
    default_pages = 1
    mode = "query"        # query | ids | urls
    location = False      # show the location builder
    categories = False    # show business-category chips
    country = True        # send gl / hl
    options: list = []
    columns: list = []
    filters: list = []
    stats: list = []
    imports: list = []    # [{tool, field, label}] - pull inputs from another tab's job
    input_label = ""
    input_placeholder = ""
    workers = 4           # tasks the browser runs in parallel
    merge_field = ""      # when a duplicate row arrives, append this field's value (e.g. Maps categories)
    dedupe = False        # also treat rows with the same phone, or name + address, as duplicates

    def meta(self) -> dict:
        return {
            "id": self.id, "label": self.label, "icon": self.icon, "description": self.description,
            "credits": self.credits, "per_page": self.per_page, "max_pages": self.max_pages,
            "default_pages": self.default_pages, "mode": self.mode, "location": self.location,
            "categories": self.categories, "country": self.country, "options": self.options,
            "columns": self.columns, "filters": self.filters, "stats": self.stats, "imports": self.imports,
            "input_label": self.input_label, "input_placeholder": self.input_placeholder,
            "workers": self.workers, "merge_field": self.merge_field, "dedupe": self.dedupe,
        }

    def pages(self, spec) -> int:
        return to_int(spec.get("pages"), self.default_pages, 1, self.max_pages)

    def tasks(self, spec: dict) -> list[dict]:
        raise NotImplementedError

    def estimate(self, spec: dict) -> dict:
        tasks = self.tasks(spec)
        pages = self.pages(spec)
        return {"tasks": [t["label"] for t in tasks], "count": len(tasks),
                "max_credits": len(tasks) * pages * self.credits,
                "max_results": len(tasks) * pages * self.per_page}

    def key(self, row: dict) -> str:
        return row.get("link") or json.dumps(row, sort_keys=True)

    def step(self, client, spec: dict, task: dict, cursor: dict | None, claimed: list) -> dict:
        """Do ONE API call for a task and return
        {rows, credits, next, next_if_empty, ...}. `next` is the cursor to continue with;
        the browser uses `next_if_empty` instead when the call produced no new rows."""
        raise NotImplementedError

    def result(self, rows, credits, next_cursor=None, next_if_empty=None, **extra) -> dict:
        for r in rows:
            r["_key"] = self.key(r)
        return {"rows": rows, "credits": credits, "next": next_cursor, "next_if_empty": next_if_empty, **extra}


class QueryTool(Tool):
    """Keyword (x location) searches with page-based pagination."""
    mode = "query"
    input_label = "Search queries / topics"
    input_placeholder = "one per line"

    def tasks(self, spec):
        cats = split_lines(spec.get("categories", [])) if self.categories else []
        lower = {c.lower() for c in cats}
        terms = cats + [k for k in split_lines(spec.get("keywords", [])) if k.lower() not in lower]
        locs = [l for l in spec.get("locations", []) if location_label(l)] if self.location else []
        out, seen = [], set()
        for term in terms:
            for loc in (locs or [{}]):
                label = location_label(loc)
                q = f"{term} in {label}" if label else term
                if q.lower() in seen:
                    continue
                seen.add(q.lower())
                out.append({"label": q, "q": q, "term": term, "location": loc, "loc_label": label})
        return out

    def payload(self, task, spec, page, state) -> dict:
        p = {"q": task["q"]}
        if self.country:
            p["gl"] = clean(spec.get("country")) or "in"
            p["hl"] = clean(spec.get("language")) or "en"
        if page > 1:
            p["page"] = page
        for opt in self.options:
            val = (spec.get("options") or {}).get(opt["key"])
            if val not in (None, "", False):
                p[opt["key"]] = val
        return p

    def normalize(self, item: dict, task: dict) -> dict:
        raise NotImplementedError

    def step(self, client, spec, task, cursor, claimed):
        page = to_int((cursor or {}).get("page"), 1, 1, self.max_pages)
        data = client.post(self.endpoint, self.payload(task, spec, page, {}))
        items = data.get(self.list_key) or []
        rows = [self.normalize(i, task) for i in items]
        for r in rows:
            r.setdefault("query", task["q"])
        nxt = {"page": page + 1} if items and page < self.pages(spec) else None
        return self.result(rows, data.get("credits", self.credits), nxt)


# ---------------------------------------------------------------- query tools

class SearchTool(QueryTool):
    id, label, icon, endpoint, list_key = "search", "Search", "🔎", "search", "organic"
    description = "Google web results. Phones and emails are pulled from titles and snippets."
    max_pages, location, categories = 10, True, True
    options = [{"key": "tbs", "label": "Time range", "type": "select", "choices": TIME_RANGES}]
    columns = [col("title", "Title", "link", text_key="title", href_key="link"), col("domain", "Domain"),
               col("snippet", "Snippet", "long"), col("phones", "Phones", "phone"), col("emails", "Emails", "email"),
               col("date", "Date"), col("category", "Keyword"), col("location", "Location"),
               col("position", "Rank", "num"), col("link", "URL", "url", show=False), col("query", "Query", show=False)]
    filters = [{"type": "select", "key": "category", "label": "All keywords"},
               {"type": "select", "key": "location", "label": "All locations"},
               {"type": "has", "key": "phones|emails", "label": "Has phone/email"}]
    stats = [{"label": "With phone", "key": "phones"}, {"label": "With email", "key": "emails"}]

    def normalize(self, o, task):
        text = f"{o.get('title', '')} {o.get('snippet', '')}"
        return {"title": clean(o.get("title")), "link": o.get("link", ""), "domain": domain_of(o.get("link")),
                "snippet": clean(o.get("snippet")), "phones": ", ".join(find_phones(text)),
                "emails": ", ".join(find_emails(text)), "date": clean(o.get("date")),
                "category": task["term"], "location": task["loc_label"], "position": o.get("position", "")}


class ImagesTool(QueryTool):
    id, label, icon, endpoint, list_key = "images", "Images", "🖼️", "images", "images"
    description = "Google Images: image URL, size, source page."
    max_pages, location = 10, True
    options = [{"key": "tbs", "label": "Time range", "type": "select", "choices": TIME_RANGES}]
    columns = [col("thumbnail", "Image", "image", full_key="image_url"), col("title", "Title", "link", href_key="link"),
               col("source", "Source"), col("domain", "Domain"), col("width", "Width", "num"),
               col("height", "Height", "num"), col("image_url", "Image URL", "url"), col("link", "Page URL", "url", show=False),
               col("category", "Query term"), col("location", "Location", show=False), col("query", "Query", show=False)]
    filters = [{"type": "select", "key": "source", "label": "All sources"},
               {"type": "select", "key": "category", "label": "All queries"}]

    def key(self, row):
        return row.get("image_url") or row.get("link")

    def normalize(self, o, task):
        return {"thumbnail": o.get("thumbnailUrl", ""), "title": clean(o.get("title")), "source": clean(o.get("source")),
                "domain": domain_of(o.get("link")), "width": o.get("imageWidth", ""), "height": o.get("imageHeight", ""),
                "image_url": o.get("imageUrl", ""), "link": o.get("link", ""), "category": task["term"],
                "location": task["loc_label"]}


class VideosTool(QueryTool):
    id, label, icon, endpoint, list_key = "videos", "Videos", "🎬", "videos", "videos"
    description = "Google Videos: YouTube, Facebook, Instagram and other video results."
    max_pages, location = 10, True
    options = [{"key": "tbs", "label": "Time range", "type": "select", "choices": TIME_RANGES}]
    columns = [col("thumbnail", "Thumb", "image", full_key="link"), col("title", "Title", "link", href_key="link"),
               col("channel", "Channel"), col("source", "Platform"), col("duration", "Duration"), col("date", "Date"),
               col("snippet", "Snippet", "long"), col("category", "Query term"), col("link", "URL", "url", show=False),
               col("location", "Location", show=False), col("query", "Query", show=False)]
    filters = [{"type": "select", "key": "source", "label": "All platforms"},
               {"type": "select", "key": "category", "label": "All queries"}]

    def normalize(self, o, task):
        return {"thumbnail": o.get("imageUrl", ""), "title": clean(o.get("title")), "channel": clean(o.get("channel")),
                "source": clean(o.get("source")), "duration": clean(o.get("duration")), "date": clean(o.get("date")),
                "snippet": clean(o.get("snippet")), "link": o.get("link", ""), "category": task["term"],
                "location": task["loc_label"]}


PLACE_COLUMNS = [
    col("name", "Business", "title", sub_key="type"), col("category", "Category"), col("phone", "Phone", "phone"),
    col("website", "Website", "link", text_key="domain", href_key="website"), col("address", "Address", "long"),
    col("area", "Area"), col("city", "City"), col("state", "State"), col("pincode", "Pincode"),
    col("rating", "Rating", "rating"), col("reviews", "Reviews", "num"), col("maps_url", "Map", "link", text="Open", href_key="maps_url"),
    col("type", "Google Type", show=False), col("domain", "Domain", show=False), col("country", "Country", show=False),
    col("latitude", "Latitude", show=False), col("longitude", "Longitude", show=False),
    col("opening_hours", "Opening Hours", show=False), col("types", "All Types", show=False),
    col("description", "Description", show=False), col("place_id", "Place ID", show=False), col("cid", "CID", show=False),
    col("location", "Searched Location", show=False), col("query", "Query", show=False),
]
PLACE_FILTERS = [
    {"type": "select", "key": "category", "label": "All categories", "split": "|"},
    {"type": "select", "key": "city", "label": "All cities"},
    {"type": "select", "key": "area", "label": "All areas"},
    {"type": "select", "key": "pincode", "label": "All pincodes"},
    {"type": "min", "key": "rating", "label": "Any rating", "choices": [3, 4, 4.5]},
    {"type": "has", "key": "phone", "label": "Has phone"},
    {"type": "has", "key": "website", "label": "Has website"},
    {"type": "not", "key": "website", "label": "No website"},
    {"type": "mobile", "key": "phone", "label": "Mobile only"},
    {"type": "unique", "key": "phone", "label": "Hide duplicates"},
]
PLACE_STATS = [{"label": "With phone", "key": "phone"}, {"label": "With website", "key": "website"}]
PLACE_OPTIONS = [
    {"key": "mobile_only", "label": "Mobile numbers only - skip landlines (0261..., 079...) and places without a phone",
     "type": "checkbox", "default": True, "wide": True},
    {"key": "dedupe", "label": "Remove duplicates - same phone number, or same business name + address",
     "type": "checkbox", "default": True, "wide": True},
]


class MapsTool(QueryTool):
    id, label, icon, endpoint, list_key = "maps", "Maps", "🗺️", "maps", "places"
    description = ("Google Maps business leads: phone, website, address, rating, hours, GPS. "
                   "Turn on 'Nearby area grid' to collect 1,000 - 10,000+ leads around each location.")
    credits, per_page, max_pages, default_pages = 3, 20, 10, 3
    location, categories = True, True
    input_label = "Topics / keywords"
    columns = PLACE_COLUMNS[:12] + [col("distance_km", "Km from center", "num1")] + PLACE_COLUMNS[12:]
    filters = PLACE_FILTERS + [{"type": "max", "key": "distance_km", "label": "Any distance", "choices": [1, 2, 3, 5, 10, 20],
                                "suffix": " km"}]
    stats = PLACE_STATS
    dedupe = True
    options = PLACE_OPTIONS + [
        {"key": "grid", "label": "Nearby area grid - search around each location until the lead limit is reached",
         "type": "checkbox", "default": True, "wide": True},
        {"key": "radius", "label": "Nearby radius", "type": "select", "default": "5", "depends": "grid",
         "choices": [[str(r), f"{r} km"] for r in (1, 2, 3, 5, 8, 10, 15, 20, 30, 50)]},
        {"key": "density", "label": "Grid density", "type": "select", "default": "normal", "depends": "grid",
         "choices": [["wide", "Wide - point every 4 km"], ["normal", "Normal - every 2 km"], ["dense", "Dense - every 1 km"]]},
        {"key": "grid_pages", "label": "Pages per grid point", "type": "select", "default": "3", "depends": "grid",
         "choices": [[str(i), f"{i} (up to {i * 20})"] for i in range(1, 6)]},
    ]
    DENSITY = {"wide": (14, 4.0), "normal": (15, 2.0), "dense": (16, 1.0)}

    def grid_settings(self, spec):
        opts = spec.get("options") or {}
        zoom, spacing = self.DENSITY.get(opts.get("density"), self.DENSITY["normal"])
        try:
            radius = max(0.5, min(100.0, float(opts.get("radius") or 5)))
        except ValueError:
            radius = 5.0
        return bool(opts.get("grid")), radius, zoom, spacing, to_int(opts.get("grid_pages"), 3, 1, 5)

    def estimate(self, spec):
        tasks = self.tasks(spec)
        grid, radius, _, spacing, grid_pages = self.grid_settings(spec)
        points = len(grid_points((23.0, 72.0), radius, spacing)) if grid else 0
        calls = self.pages(spec) + points * grid_pages
        return {"tasks": [t["label"] for t in tasks], "count": len(tasks),
                "max_credits": len(tasks) * calls * self.credits, "max_results": len(tasks) * calls * self.per_page,
                "note": (f"{points} nearby grid points per search ({radius:g} km radius, every {spacing:g} km). "
                         "Real unique leads are usually lower than the max because nearby points overlap, "
                         "and paging stops early when a point has nothing new.") if grid else ""}

    merge_field = "category"

    def step(self, client, spec, task, cursor, claimed):
        """Cursor phases:
        base - "<category> in <location>" search, paginated with the GPS `ll` Google returns
        grid - plain "<category>" at GPS points spiralling out from that center (Nearby area grid)"""
        cursor = cursor or {"phase": "base", "page": 1}
        gl, hl = clean(spec.get("country")) or "in", clean(spec.get("language")) or "en"
        grid, radius, zoom, spacing, grid_pages = self.grid_settings(spec)

        if cursor.get("phase", "base") == "base":
            page = to_int(cursor.get("page"), 1, 1, self.max_pages)
            p = {"q": task["q"], "gl": gl, "hl": hl}
            if page > 1:
                p.update(page=page, ll=cursor.get("ll"))
            data = client.post(self.endpoint, p)
            ll = cursor.get("ll") or data.get("ll")
            center = cursor.get("center") or parse_ll(ll)
            places = data.get(self.list_key) or []
            rows, skipped = self._rows(places, {**task, "center": center}, task["q"], spec)
            grid_start = {"phase": "grid", "idx": 0, "gpage": 1, "center": list(center)} if grid and center else None
            more = len(places) >= self.per_page and page < self.pages(spec) and ll
            nxt = {"phase": "base", "page": page + 1, "ll": ll, "center": center and list(center)} if more else grid_start
            return self.result(rows, data.get("credits", self.credits), nxt, grid_start, count=skipped)

        # grid phase
        center = tuple(cursor["center"])
        points = grid_points(center, radius, spacing)
        idx, gpage = to_int(cursor.get("idx"), 0, 0, 10 ** 6), to_int(cursor.get("gpage"), 1, 1, 5)
        taken = set(claimed or [])
        term = task["term"].lower()
        if gpage == 1:  # skip points another location of this job already searched
            while idx < len(points) and self.cell_key(term, zoom, points[idx][2]) in taken:
                idx += 1
        if idx >= len(points):
            return self.result([], 0, None, None, skipped=True)
        lat, lng, cell = points[idx]
        point_ll = f"@{lat:.7f},{lng:.7f},{zoom}z"
        p = {"q": task["term"], "gl": gl, "hl": hl, "ll": point_ll}
        if gpage > 1:
            p["page"] = gpage
        data = client.post(self.endpoint, p)
        places = data.get(self.list_key) or []
        rows, skipped = self._rows(places, {**task, "center": center}, f"{task['term']} near {point_ll}", spec)
        next_point = {"phase": "grid", "idx": idx + 1, "gpage": 1, "center": list(center)} if idx + 1 < len(points) else None
        same_point = ({"phase": "grid", "idx": idx, "gpage": gpage + 1, "center": list(center)}
                      if len(places) >= self.per_page and gpage < grid_pages else None)
        extra = {"claim": self.cell_key(term, zoom, cell), "count": {**skipped, "grid_points": 1}} if gpage == 1 \
            else {"count": skipped}
        return self.result(rows, data.get("credits", self.credits), same_point or next_point, next_point,
                           progress=f"point {idx + 1}/{len(points)}", **extra)

    @staticmethod
    def cell_key(term, zoom, cell) -> str:
        return f"{term}|{zoom}|{cell[0]}|{cell[1]}"

    def _rows(self, places, task, query, spec):
        """Normalized rows, minus non-mobile numbers when 'Mobile numbers only' is on.
        Returns (rows, {"not_mobile": n}) so the browser can show how many were skipped."""
        mobile_only = (spec.get("options") or {}).get("mobile_only", False)
        india = (clean(spec.get("country")) or "in").lower() == "in"
        rows, skipped = [], 0
        for place in places:
            r = self.normalize(place, task)
            r["query"] = query
            if mobile_only:
                mobile = indian_mobile(r["phone"]) if india else r["phone"]
                if not mobile:
                    skipped += 1
                    continue
                r["phone"] = mobile
            rows.append(r)
        return rows, ({"not_mobile": skipped} if skipped else {})

    def key(self, row):
        return row.get("cid") or row.get("place_id") or f"{row.get('name', '').lower()}|{row.get('address', '').lower()}"

    def normalize(self, p, task):
        loc = task["location"]
        addr = clean(p.get("address"))
        parsed = parse_address(addr)
        hours = p.get("openingHours")
        if isinstance(hours, dict):
            hours = "; ".join(f"{d}: {h}" for d, h in hours.items())
        cid = str(p.get("cid") or "")
        return {
            "name": clean(p.get("title")), "category": task["term"], "type": clean(p.get("type") or p.get("category")),
            "phone": clean(p.get("phoneNumber")), "website": clean(p.get("website")),
            "domain": domain_of(p.get("website")), "address": addr,
            "area": parsed["area"] or clean(loc.get("area")), "city": parsed["city"] or clean(loc.get("city")),
            "state": parsed["state"] or clean(loc.get("state")), "pincode": parsed["pincode"] or clean(loc.get("pincode")),
            "country": parsed["country"], "rating": p.get("rating", ""), "reviews": p.get("ratingCount", ""),
            "latitude": p.get("latitude", ""), "longitude": p.get("longitude", ""),
            "maps_url": f"https://www.google.com/maps?cid={cid}" if cid else "",
            "opening_hours": clean(hours), "types": ", ".join(p.get("types") or []),
            "description": clean(p.get("description")), "place_id": clean(p.get("placeId")), "cid": cid,
            "location": task["loc_label"], "distance_km": distance_from(task.get("center"), p),
        }


class PlacesTool(MapsTool):
    id, label, icon, endpoint, list_key = "places", "Places", "📍", "places", "places"
    description = "Google local 'Places' results - cheaper (1 credit) with fewer fields than Maps."
    credits, per_page, max_pages, default_pages = 1, 10, 10, 3
    columns, filters, stats, options = PLACE_COLUMNS, PLACE_FILTERS, PLACE_STATS, PLACE_OPTIONS
    estimate = QueryTool.estimate
    step = QueryTool.step


class NewsTool(QueryTool):
    id, label, icon, endpoint, list_key = "news", "News", "📰", "news", "news"
    description = "Google News articles."
    max_pages, location = 10, True
    options = [{"key": "tbs", "label": "Time range", "type": "select", "choices": TIME_RANGES}]
    columns = [col("thumbnail", "Image", "image", full_key="link"), col("title", "Headline", "link", href_key="link"),
               col("source", "Publisher"), col("date", "Date"), col("snippet", "Snippet", "long"),
               col("category", "Query term"), col("link", "URL", "url", show=False),
               col("location", "Location", show=False), col("query", "Query", show=False)]
    filters = [{"type": "select", "key": "source", "label": "All publishers"},
               {"type": "select", "key": "category", "label": "All queries"}]

    def normalize(self, o, task):
        return {"thumbnail": o.get("imageUrl", ""), "title": clean(o.get("title")), "source": clean(o.get("source")),
                "date": clean(o.get("date")), "snippet": clean(o.get("snippet")), "link": o.get("link", ""),
                "category": task["term"], "location": task["loc_label"]}


class ShoppingTool(QueryTool):
    id, label, icon, endpoint, list_key = "shopping", "Shopping", "🛒", "shopping", "shopping"
    description = "Google Shopping products: price, store, rating."
    credits, per_page, max_pages = 2, 40, 5
    columns = [col("thumbnail", "Image", "image", full_key="link"), col("title", "Product", "link", href_key="link"),
               col("price", "Price"), col("price_value", "Price (number)", "num"), col("source", "Store"),
               col("rating", "Rating", "rating"), col("reviews", "Reviews", "num"), col("product_id", "Product ID", show=False),
               col("category", "Query term"), col("link", "URL", "url", show=False), col("query", "Query", show=False)]
    filters = [{"type": "select", "key": "source", "label": "All stores"},
               {"type": "select", "key": "category", "label": "All queries"},
               {"type": "min", "key": "rating", "label": "Any rating", "choices": [3, 4, 4.5]}]

    def key(self, row):
        return row.get("product_id") or row.get("link")

    def normalize(self, o, task):
        price = clean(o.get("price"))
        m = re.search(r"\d[\d,]*(?:\.\d+)?", price)
        return {"thumbnail": o.get("imageUrl", ""), "title": clean(o.get("title")), "price": price,
                "price_value": float(m.group().replace(",", "")) if m else "", "source": clean(o.get("source")),
                "rating": o.get("rating", ""), "reviews": o.get("ratingCount", ""),
                "product_id": clean(o.get("productId")), "link": o.get("link", ""), "category": task["term"]}


class ScholarTool(QueryTool):
    id, label, icon, endpoint, list_key = "scholar", "Scholar", "🎓", "scholar", "organic"
    description = "Google Scholar papers: authors, year, citations, PDF."
    max_pages, country = 10, False
    columns = [col("title", "Title", "link", href_key="link"), col("publication", "Authors / Publication", "long"),
               col("year", "Year", "num"), col("cited_by", "Cited by", "num"), col("pdf_url", "PDF", "link", text="PDF", href_key="pdf_url"),
               col("snippet", "Snippet", "long"), col("category", "Query term"), col("link", "URL", "url", show=False),
               col("scholar_id", "Scholar ID", show=False), col("query", "Query", show=False)]
    filters = [{"type": "select", "key": "year", "label": "All years"},
               {"type": "select", "key": "category", "label": "All queries"},
               {"type": "has", "key": "pdf_url", "label": "Has PDF"}]

    def key(self, row):
        return row.get("scholar_id") or row.get("link")

    def normalize(self, o, task):
        return {"title": clean(o.get("title")), "publication": clean(o.get("publicationInfo")), "year": o.get("year", ""),
                "cited_by": o.get("citedBy", ""), "pdf_url": o.get("pdfUrl", ""), "snippet": clean(o.get("snippet")),
                "link": o.get("link", ""), "scholar_id": clean(o.get("id")), "category": task["term"]}


class PatentsTool(QueryTool):
    id, label, icon, endpoint, list_key = "patents", "Patents", "📜", "patents", "organic"
    description = "Google Patents: number, inventor, assignee, dates, PDF."
    max_pages, country = 10, False
    columns = [col("title", "Title", "link", href_key="link"), col("publication_number", "Number"),
               col("assignee", "Assignee"), col("inventor", "Inventor"), col("priority_date", "Priority"),
               col("filing_date", "Filed"), col("grant_date", "Granted"), col("publication_date", "Published", show=False),
               col("pdf_url", "PDF", "link", text="PDF", href_key="pdf_url"), col("snippet", "Snippet", "long"),
               col("language", "Language", show=False), col("category", "Query term"),
               col("link", "URL", "url", show=False), col("query", "Query", show=False)]
    filters = [{"type": "select", "key": "assignee", "label": "All assignees"},
               {"type": "select", "key": "category", "label": "All queries"},
               {"type": "has", "key": "grant_date", "label": "Granted only"}]

    def key(self, row):
        return row.get("publication_number") or row.get("link")

    def normalize(self, o, task):
        return {"title": clean(o.get("title")), "publication_number": clean(o.get("publicationNumber")),
                "assignee": clean(o.get("assignee")), "inventor": clean(o.get("inventor")),
                "priority_date": clean(o.get("priorityDate")), "filing_date": clean(o.get("filingDate")),
                "grant_date": clean(o.get("grantDate")), "publication_date": clean(o.get("publicationDate")),
                "pdf_url": o.get("pdfUrl", ""), "snippet": clean(o.get("snippet")), "language": clean(o.get("language")),
                "link": o.get("link", ""), "category": task["term"]}


class AutocompleteTool(QueryTool):
    id, label, icon, endpoint, list_key = "autocomplete", "Autocomplete", "⌨️", "autocomplete", "suggestions"
    description = "Google search suggestions - great for keyword / topic research."
    max_pages, location = 1, True
    input_label = "Seed keywords"
    columns = [col("suggestion", "Suggestion"), col("category", "Seed keyword"), col("location", "Location"),
               col("query", "Query sent", show=False)]
    filters = [{"type": "select", "key": "category", "label": "All seeds"}]

    def key(self, row):
        return f"{row.get('category')}|{row.get('suggestion', '').lower()}"

    def normalize(self, o, task):
        return {"suggestion": clean(o.get("value")), "category": task["term"], "location": task["loc_label"]}


# ---------------------------------------------------------------- id / url tools

class ReviewsTool(Tool):
    id, label, icon, endpoint, list_key = "reviews", "Reviews", "⭐", "reviews", "reviews"
    description = "Google Maps reviews for places (by CID, Place ID or FID). Import places from a Maps or Places job."
    mode, per_page, max_pages, default_pages = "ids", 20, 20, 1
    input_label = "Places (one per line)"
    input_placeholder = "9081223508357860787\nThe Blue Oven | ChIJ-6uZQTyFXjkRs82Ynar8Bn4\n0x395e853c4199abfb:0x7e06fcaa9d98cdb3"
    options = [{"key": "sortBy", "label": "Sort reviews by", "type": "select",
                "choices": [["", "Most relevant"], ["newest", "Newest"], ["highestRating", "Highest rating"],
                            ["lowestRating", "Lowest rating"]]}]
    imports = [{"tool": "maps", "field": "cid", "label": "Places from a Maps job"},
               {"tool": "places", "field": "cid", "label": "Places from a Places job"}]
    columns = [col("place", "Place"), col("rating", "Stars", "rating"), col("date", "Date"),
               col("review", "Review", "long"), col("user", "Reviewer", "link", href_key="user_link"),
               col("user_reviews", "Reviewer reviews", "num"), col("likes", "Likes", "num"), col("photos", "Photos", "num"),
               col("iso_date", "ISO date", show=False), col("link", "Review link", "link", text="Open", href_key="link"),
               col("user_link", "Reviewer link", "url", show=False), col("place_ref", "Place ID / CID", show=False),
               col("review_id", "Review ID", show=False)]
    filters = [{"type": "select", "key": "place", "label": "All places"},
               {"type": "select", "key": "rating", "label": "All stars"},
               {"type": "has", "key": "review", "label": "Has text"}]

    def pages(self, spec):
        return to_int(spec.get("pages"), 1, 1, self.max_pages)

    def tasks(self, spec):
        out, seen = [], set()
        for line in split_lines(spec.get("inputs", [])):
            name, ref = split_label(line)
            if not ref or ref in seen:
                continue
            seen.add(ref)
            if re.fullmatch(r"\d{5,}", ref):
                kind = "cid"
            elif re.fullmatch(r"0x[0-9a-fA-F]+:0x[0-9a-fA-F]+", ref):
                kind = "fid"
            else:
                kind = "placeId"
            out.append({"label": name or ref, "name": name or ref, "kind": kind, "ref": ref})
        return out

    def key(self, row):
        return row.get("review_id") or f"{row.get('place_ref')}|{row.get('user')}|{row.get('iso_date')}"

    def step(self, client, spec, task, cursor, claimed):
        page, token = to_int((cursor or {}).get("page"), 1, 1, self.max_pages), (cursor or {}).get("token")
        sort = (spec.get("options") or {}).get("sortBy")
        p = {task["kind"]: task["ref"], "gl": clean(spec.get("country")) or "in", "hl": clean(spec.get("language")) or "en"}
        if sort:
            p["sortBy"] = sort
        if token:
            p["nextPageToken"] = token
        data = client.post(self.endpoint, p)
        rows = []
        for r in data.get("reviews") or []:
            user = r.get("user") or {}
            rows.append({
                "place": task["name"], "rating": r.get("rating", ""), "date": clean(r.get("date")),
                "review": (r.get("snippet") or "").strip(), "user": clean(user.get("name")),
                "user_reviews": user.get("reviews", ""), "likes": r.get("likes", ""),
                "photos": len(r.get("media") or []), "iso_date": clean(r.get("isoDate")), "link": r.get("link", ""),
                "user_link": user.get("link", ""), "place_ref": task["ref"], "review_id": clean(r.get("id")),
            })
        token = data.get("nextPageToken")
        nxt = {"page": page + 1, "token": token} if rows and token and page < self.pages(spec) else None
        return self.result(rows, data.get("credits", self.credits), nxt)


class LensTool(Tool):
    id, label, icon, endpoint, list_key = "lens", "Image Search (Lens)", "🔍", "lens", "organic"
    description = "Google Lens reverse image search: pages and products that show a similar image."
    mode, credits, per_page = "urls", 3, 60
    input_label = "Image URLs (one per line)"
    input_placeholder = "https://example.com/photo.jpg"
    imports = [{"tool": "images", "field": "image_url", "label": "Images from an Images job"}]
    columns = [col("thumbnail", "Image", "image", full_key="image_url"), col("title", "Title", "link", href_key="link"),
               col("source", "Source"), col("domain", "Domain"), col("input", "Searched image", "link", text="Image", href_key="input"),
               col("image_url", "Image URL", "url", show=False), col("link", "Page URL", "url", show=False)]
    filters = [{"type": "select", "key": "source", "label": "All sources"},
               {"type": "select", "key": "input", "label": "All searched images"}]

    def tasks(self, spec):
        out = []
        for line in split_lines(spec.get("inputs", [])):
            name, url = split_label(line)
            if url.startswith("http"):
                out.append({"label": name or url, "url": url})
        return out

    def key(self, row):
        return f"{row.get('input')}|{row.get('link')}"

    def step(self, client, spec, task, cursor, claimed):
        p = {"url": task["url"]}
        if spec.get("country"):
            p["gl"], p["hl"] = spec["country"], spec.get("language") or "en"
        data = client.post(self.endpoint, p)
        rows = [{"thumbnail": o.get("thumbnailUrl", ""), "title": clean(o.get("title")), "source": clean(o.get("source")),
                 "domain": domain_of(o.get("link")), "input": task["url"], "image_url": o.get("imageUrl", ""),
                 "link": o.get("link", "")} for o in data.get("organic") or []]
        return self.result(rows, data.get("credits", self.credits))


class WebpageTool(Tool):
    id, label, icon = "webpage", "Webpage", "🌐"
    description = "Scrape any web page: title, text, markdown, emails, phones, social links. Import websites from a Maps job."
    mode, credits, per_page, country, workers = "urls", 2, 1, False, 3
    input_label = "Page URLs (one per line)"
    input_placeholder = "https://theblueoven.com\nThe Blue Oven | https://theblueoven.com/menu"
    options = [{"key": "includeMarkdown", "label": "Include markdown", "type": "checkbox", "default": True}]
    imports = [{"tool": "maps", "field": "website", "label": "Websites from a Maps job"},
               {"tool": "places", "field": "website", "label": "Websites from a Places job"},
               {"tool": "search", "field": "link", "label": "URLs from a Search job"}]
    columns = [col("name", "Name"), col("url", "URL", "link", text_key="domain", href_key="url"),
               col("title", "Page title"), col("description", "Description", "long"),
               col("emails", "Emails", "email"), col("phones", "Phones", "phone"), col("socials", "Social links", "links"),
               col("schema_type", "Schema type"), col("schema_address", "Address (schema)", "long"),
               col("words", "Words", "num"), col("text", "Text", "view"), col("markdown", "Markdown", "view", show=False),
               col("domain", "Domain", show=False), col("error", "Error")]
    filters = [{"type": "has", "key": "emails", "label": "Has email"},
               {"type": "has", "key": "phones", "label": "Has phone"},
               {"type": "has", "key": "socials", "label": "Has social links"},
               {"type": "has", "key": "error", "label": "Failed only"}]
    stats = [{"label": "With email", "key": "emails"}, {"label": "With phone", "key": "phones"},
             {"label": "Failed", "key": "error"}]

    def estimate(self, spec):
        n = len(self.tasks(spec))
        return {"tasks": [t["label"] for t in self.tasks(spec)], "count": n, "max_credits": n * self.credits, "max_results": n}

    def tasks(self, spec):
        out, seen = [], set()
        for line in split_lines(spec.get("inputs", [])):
            name, url = split_label(line)
            if not url:
                continue
            if not re.match(r"https?://", url, re.I):
                url = "https://" + url
            if url.lower() in seen:
                continue
            seen.add(url.lower())
            out.append({"label": url, "name": name, "url": url})
        return out

    def key(self, row):
        return row.get("url", "").lower()

    def step(self, client, spec, task, cursor, claimed):
        from serper_client import SerperError
        include_md = (spec.get("options") or {}).get("includeMarkdown", True)
        base = {"name": task["name"], "url": task["url"], "domain": domain_of(task["url"])}
        try:
            data = client.scrape(task["url"], include_markdown=bool(include_md))
        except SerperError as e:
            if e.fatal:
                raise
            return self.result([{**base, "error": str(e)}], 0, error=str(e))
        text, md = data.get("text") or "", data.get("markdown") or ""
        meta = data.get("metadata") or {}
        blob = f"{text}\n{md}"
        emails = find_emails(blob.replace("mailto:", " "))
        phones = find_phones(text)
        tel_links = [clean(t) for t in re.findall(r"tel:([+\d\s-]{7,})", md)]
        schema_type, schema_address = "", ""
        for node in self._jsonld_nodes(data.get("jsonld")):
            if not schema_type and node.get("@type"):
                t = node["@type"]
                schema_type = ", ".join(t) if isinstance(t, list) else str(t)
            if node.get("email"):
                emails += [e for e in find_emails(str(node["email"])) if e not in emails]
            if node.get("telephone") and clean(node["telephone"]) not in phones:
                phones.append(clean(node["telephone"]))
            addr = node.get("address")
            if not schema_address and isinstance(addr, dict):
                schema_address = ", ".join(clean(addr.get(k)) for k in ("streetAddress", "addressLocality", "addressRegion",
                                                                        "postalCode", "addressCountry") if addr.get(k))
        for t in tel_links:
            if t not in phones:
                phones.append(t)
        socials = list(dict.fromkeys(s.rstrip(".,") for s in SOCIAL_RE.findall(blob)))
        row = {**base, "title": clean(meta.get("title") or meta.get("og:title")).replace("&amp;", "&"),
               "description": clean(meta.get("description") or meta.get("og:description")),
               "emails": ", ".join(emails), "phones": ", ".join(phones), "socials": "\n".join(socials[:15]),
               "schema_type": schema_type, "schema_address": schema_address, "words": len(text.split()),
               "text": text[:PAGE_TEXT_LIMIT], "markdown": md[:PAGE_TEXT_LIMIT], "error": ""}
        return self.result([row], data.get("credits", self.credits),
                           progress=f"{row['words']} words, {len(emails)} emails, {len(phones)} phones")

    @staticmethod
    def _jsonld_nodes(jsonld):
        stack, out = [jsonld], []
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                out.append(node)
                if isinstance(node.get("@graph"), list):
                    stack.extend(node["@graph"])
        return out


TOOL_LIST = [SearchTool(), ImagesTool(), VideosTool(), PlacesTool(), MapsTool(), ReviewsTool(), NewsTool(),
             ShoppingTool(), LensTool(), ScholarTool(), PatentsTool(), AutocompleteTool(), WebpageTool()]
TOOLS = {t.id: t for t in TOOL_LIST}
