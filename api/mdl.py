import json, re
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
from curl_cffi import requests as req
from bs4 import BeautifulSoup

BASE = "https://mydramalist.com"

def get_soup(path):
    r = req.get(BASE + path, impersonate="chrome110", timeout=15)
    r.raise_for_status()
    return BeautifulSoup(r.content, "html.parser")

# تبدیل به بزرگ‌ترین سایز: _3m / _3t / _3s / _3c  →  _3f
def full_size(url):
    if not url: return ""
    return re.sub(r'_(\d+)[a-z]?(\.[A-Za-z0-9]+)$', r'_\1f\2', url.split("?")[0])

def img(tag):
    return (tag.get("data-src") or tag.get("src") or "") if tag else ""

def search(q):
    soup = get_soup("/search?q=" + quote(q))
    out = []
    for box in soup.find_all("div", class_="box")[:20]:
        h6 = box.find("h6", class_="title")
        a = h6.find("a") if h6 else None
        if not a: continue
        slug = (a.get("href") or "").strip("/").split("/")[-1]
        if not re.match(r"\d+-", slug): continue
        s = box.find("span", class_="score")
        y = re.search(r"(19|20)\d{2}", box.get_text(" "))
        out.append({"title": h6.get_text(strip=True), "slug": slug,
                    "rating": s.get_text(strip=True) if s else "",
                    "year": y.group(0) if y else "", "poster": img(box.find("img"))})
    return out

def cast_item(li):
    """فقط اسمِ تمیز بازیگر + عکس (بدون نقش)"""
    a = li.find("a", class_="text-primary") or li.find("a", class_="name")
    if not a: return None, None
    b = a.find("b")
    name = (b or a).get_text(strip=True)          # ← فقط اسم بازیگر
    if not name: return None, None
    role = ""
    for el in li.find_all(["div", "small"]):      # فقط برای گروه‌بندی/مرتب‌سازی
        t = el.get_text(strip=True)
        if t and not el.find(["div", "small"]) and \
           re.search(r"main role|support role|guest role|host|narrator", t, re.I):
            role = role or t
    photo = img(li.find("img"))
    return ({"name": name, "photo": photo, "photoFull": full_size(photo),
             "profile": BASE + a["href"] if a.get("href") else ""}, role)

def cast(slug):
    soup = get_soup(f"/{slug}/cast")
    prio = lambda r: next((i for i, p in enumerate([r"main", r"support", r"guest", r"host|narrator"])
                           if re.search(p, r, re.I)), 99)
    groups, headers = [], soup.find_all("h3", class_="header")
    if headers:
        for header in headers:
            ul = header.find_next_sibling("ul")
            if not ul: continue
            members = [m for m, _ in (cast_item(li) for li in ul.find_all("li", class_="list-item")) if m]
            if members: groups.append({"role": header.get_text(strip=True), "members": members})
    else:
        by = {}
        for li in soup.find_all("li", class_="list-item"):
            m, role = cast_item(li)
            if m: by.setdefault(role or "Cast", []).append(m)
        groups = [{"role": r, "members": ms} for r, ms in by.items()]
    groups.sort(key=lambda g: prio(g["role"]))    # اصلی‌ها اول
    return groups

def details(slug):
    soup = get_soup("/" + slug)
    d = {"slug": slug, "url": f"{BASE}/{slug}"}
    t = soup.select_one("h1.film-title")
    d["title"] = t.get_text(strip=True) if t else ""
    poster = img(soup.select_one("div.film-cover img"))
    if not poster:
        og = soup.find("meta", attrs={"property": "og:image"})
        poster = og.get("content", "") if og else ""
    d["poster"], d["posterFull"] = poster, full_size(poster)   # 🌟 کیفیت اصلی
    syn = soup.select_one("div.show-synopsis")
    d["synopsis"] = syn.get_text(" ", strip=True).replace("Edit Translation", "").strip() if syn else ""
    rating = ""
    hfs = soup.select_one(".hfs b")
    if hfs:
        m = re.search(r"\d\.\d", hfs.get_text())
        rating = m.group(0) if m else ""
    if not rating:
        for box in soup.select("div.content-side .box"):
            h = box.select_one(".box-header h3")
            if h and "Statistics" in h.get_text():
                for li in box.find_all("li", class_="list-item"):
                    m = re.search(r"(\d\.\d)", li.get_text()) if "Score:" in li.get_text() else None
                    if m: rating = m.group(1)
    d["rating"] = rating
    genres, info = [], {}
    for li in soup.find_all("li", class_="list-item"):
        b = li.find("b")
        if not b: continue
        key = b.get_text(strip=True).rstrip(":")
        if key == "Genres": genres = [a.get_text(strip=True) for a in li.find_all("a")]
        else: info[key] = li.get_text(" ", strip=True).replace(b.get_text(), "", 1).strip(" :")
    d["genres"], d["info"], d["cast"] = genres, info, cast(slug)
    return d

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        t = qs.get("type", [""])[0]
        try:
            # ---------- دانلود مستقیم عکس با کیفیت اصلی ----------
            if t == "photo":
                url = qs.get("url", [""])[0]
                if not re.match(r"https://(i\.)?mydramalist\.com/", url): raise ValueError("url نامعتبر")
                r = req.get(full_size(url), impersonate="chrome110", timeout=20)
                r.raise_for_status()
                name = re.sub(r'[^\w\- ]+', '', qs.get("name", ["image"])[0]).strip() or "image"
                self.send_response(200)
                self.send_header("Content-Type", r.headers.get("Content-Type", "image/jpeg"))
                self.send_header("Content-Disposition", f'attachment; filename="{name}.jpg"')
                self.end_headers()
                self.wfile.write(r.content)
                return

            if t == "search":
                data = search(qs.get("q", [""])[0])
            elif t == "info":
                slug = qs.get("slug", [""])[0]
                if not re.match(r"^\d+-[a-z0-9-]+$", slug, re.I): raise ValueError("slug نامعتبر")
                data = details(slug)
            else:
                data = {"ok": True, "api": "MDL Web"}
            self._send(200, data)
        except Exception as e:
            self._send(500, {"error": str(e)})

    def _send(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass
