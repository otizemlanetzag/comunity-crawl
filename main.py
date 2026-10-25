from __future__ import annotations

import csv
import ipaddress
import os
import queue
import socket
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, ttk
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
TOKEN_FILE = os.path.join(APP_DIR, "github_token.txt")
PAGES_FILE = os.path.join(DATA_DIR, "CRAWLED.CSV")
QUEUE_FILE = os.path.join(DATA_DIR, "CRAWL_QUEUE.CSV")
PAGE_FIELDS = ["site_name", "site_url", "crawled_at", "title", "text", "links", "status"]
QUEUE_FIELDS = ["site_name", "site_url", "status", "added_at", "error"]
USER_AGENT = "CommunityCrawlDesktop/1.0 (+local desktop crawler)"


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_url(url):
    try:
        p = urlparse(url.strip())
        if p.scheme.lower() not in ("http", "https") or not p.hostname:
            return ""
        host = p.hostname.lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        port = p.port
        netloc = host
        if port and not ((p.scheme.lower() == "http" and port == 80) or (p.scheme.lower() == "https" and port == 443)):
            netloc += ":" + str(port)
        path = p.path or "/"
        if path != "/":
            path = path.rstrip("/") or "/"
        return urlunparse((p.scheme.lower(), netloc, path, "", p.query, ""))
    except (ValueError, AttributeError):
        return ""


def public_http_url(url):
    normalized = normalize_url(url)
    if not normalized:
        return False
    host = urlparse(normalized).hostname
    try:
        addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        if not addresses:
            return False
        for item in addresses:
            ip = ipaddress.ip_address(item[4][0])
            if not ip.is_global:
                return False
        return True
    except (OSError, ValueError):
        return False


def read_rows(path, fields):
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=fields).writeheader()
        return []
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


def write_rows(path, fields, rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


class CrawlerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Community Crawl — Desktop")
        self.root.geometry("1040x720")
        self.root.minsize(800, 560)
        self.events = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.running = False
        self.pages = read_rows(PAGES_FILE, PAGE_FIELDS)
        self.queue_rows = read_rows(QUEUE_FILE, QUEUE_FIELDS)
        self._build_ui()
        self._refresh_stats()
        self.root.after(120, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Community Crawl", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Desktop website crawler — runs locally, independently of Vercel.", foreground="#596579").pack(anchor="w", pady=(2, 14))

        form = ttk.LabelFrame(outer, text="New crawl", padding=12)
        form.pack(fill="x")
        ttk.Label(form, text="Starting URL").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar(value="https://wikipedia.org/")
        self.url_entry = ttk.Entry(form, textvariable=self.url_var)
        self.url_entry.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(4, 10))
        ttk.Label(form, text="Maximum pages").grid(row=2, column=0, sticky="w")
        self.limit_var = tk.IntVar(value=100)
        ttk.Spinbox(form, from_=1, to=100000, textvariable=self.limit_var, width=12).grid(row=3, column=0, sticky="w", pady=(4, 10))
        ttk.Label(form, text="Delay between requests (seconds)").grid(row=2, column=1, sticky="w", padx=(18, 0))
        self.delay_var = tk.DoubleVar(value=1.0)
        ttk.Spinbox(form, from_=0.2, to=60.0, increment=0.2, textvariable=self.delay_var, width=12).grid(row=3, column=1, sticky="w", padx=(18, 0), pady=(4, 10))
        self.same_host_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="Stay on starting website", variable=self.same_host_var).grid(row=3, column=2, sticky="w", padx=(18, 0))
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=12)
        self.start_btn = ttk.Button(buttons, text="Start crawl", command=self.start)
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(buttons, text="Pause", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=8)
        self.stop_btn = ttk.Button(buttons, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left")
        ttk.Button(buttons, text="Export CSV…", command=self.export).pack(side="right")
        ttk.Button(buttons, text="Open data folder", command=self.open_data_folder).pack(side="right", padx=8)

        stats = ttk.Frame(outer)
        stats.pack(fill="x", pady=(0, 10))
        self.stats_var = tk.StringVar(value="Ready")
        ttk.Label(stats, textvariable=self.stats_var).pack(side="left")
        self.progress = ttk.Progressbar(stats, mode="determinate", maximum=100)
        self.progress.pack(side="right", fill="x", expand=True, padx=(20, 0))

        log_frame = ttk.LabelFrame(outer, text="Activity", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, wrap="word", height=15, state="disabled", font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Label(outer, text="Saved files: data/CRAWLED.CSV and data/CRAWL_QUEUE.CSV", foreground="#596579").pack(anchor="w", pady=(10, 0))

    def log_message(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _refresh_stats(self):
        self.pages = read_rows(PAGES_FILE, PAGE_FIELDS)
        self.queue_rows = read_rows(QUEUE_FILE, QUEUE_FIELDS)
        ok = sum(1 for row in self.pages if row.get("status") == "ok")
        pending = sum(1 for row in self.queue_rows if row.get("status") == "pending")
        self.stats_var.set(f"Saved pages: {ok}    Pending queue: {pending}")
        
    def start(self):
        if self.running:
            return
        url = normalize_url(self.url_var.get())
        if not url or not public_http_url(url):
            messagebox.showerror("Invalid URL", "Enter a valid public HTTP/HTTPS URL. Private/local network addresses are blocked.")
            return
        try:
            limit = max(1, min(100000, int(self.limit_var.get())))
            delay = max(0.2, min(60.0, float(self.delay_var.get())))
        except (ValueError, tk.TclError):
            messagebox.showerror("Invalid settings", "Check the page limit and request delay.")
            return
        self.stop_event.clear()
        self.pause_event.set()
        self.running = True
        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Pause")
        self.stop_btn.configure(state="normal")
        self.progress.configure(value=0, maximum=limit)
        self.worker = threading.Thread(target=self._crawl_worker, args=(url, limit, delay, self.same_host_var.get()), daemon=True)
        self.worker.start()

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_btn.configure(text="Resume")
            self.events.put(("log", "Paused."))
        else:
            self.pause_event.set()
            self.pause_btn.configure(text="Pause")
            self.events.put(("log", "Resumed."))

    def stop(self):
        self.stop_event.set()
        self.pause_event.set()
        self.events.put(("log", "Stopping after the current request…"))

    def _crawl_worker(self, start_url, limit, delay, same_host):
        start_host = urlparse(start_url).hostname
        todo = deque([start_url])
        queued = {normalize_url(start_url)}
        seen = set()
        existing = read_rows(PAGES_FILE, PAGE_FIELDS)
        for row in existing:
            key = normalize_url(row.get("site_url", ""))
            if key:
                seen.add(key)
        results_by_key = {normalize_url(r.get("site_url", "")): r for r in existing if normalize_url(r.get("site_url", ""))}
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        processed = 0
        try:
            while todo and processed < limit and not self.stop_event.is_set():
                self.pause_event.wait()
                if self.stop_event.is_set():
                    break
                url = todo.popleft()
                key = normalize_url(url)
                if not key or key in queued and key in seen:
                    continue
                if key in seen:
                    continue
                queued.add(key)
                if not public_http_url(url):
                    self.events.put(("log", f"Blocked non-public or invalid URL: {url}"))
                    continue
                try:
                    response = session.get(url, timeout=(5, 15), allow_redirects=True, stream=True)
                    final_url = response.url
                    if not public_http_url(final_url):
                        response.close()
                        self.events.put(("log", f"Blocked unsafe redirect: {final_url}"))
                        continue
                    content_type = response.headers.get("content-type", "").lower()
                    if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                        response.close()
                        seen.add(key)
                        self.events.put(("log", f"Skipped non-HTML: {url}"))
                        continue
                    chunks = []
                    total = 0
                    for chunk in response.iter_content(16384):
                        if self.stop_event.is_set():
                            break
                        total += len(chunk)
                        if total > 2_000_000:
                            break
                        chunks.append(chunk)
                    status_code = response.status_code
                    response.close()
                    if self.stop_event.is_set():
                        break
                    raw = b"".join(chunks)
                    soup = BeautifulSoup(raw, "html.parser")
                    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
                        tag.decompose()
                    title = " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else urlparse(final_url).netloc
                    text_content = " ".join(soup.get_text(" ", strip=True).split())[:50000]
                    links = []
                    for a in soup.find_all("a", href=True):
                        target = normalize_url(urljoin(final_url, a["href"]))
                        if not target:
                            continue
                        if urlparse(target).scheme not in ("http", "https"):
                            continue
                        if same_host and urlparse(target).hostname != start_host:
                            continue
                        if target not in queued and target not in seen:
                            todo.append(target)
                            queued.add(target)
                        if target not in links and len(links) < 300:
                            links.append(target)
                    seen.add(key)
                    seen.add(normalize_url(final_url))
                    row = {
                        "site_name": urlparse(start_url).hostname or "site",
                        "site_url": final_url,
                        "crawled_at": utc_now(),
                        "title": title[:500],
                        "text": text_content,
                        "links": "|".join(links),
                        "status": "ok" if 200 <= status_code < 400 else f"http_{status_code}",
                    }
                    result_key = normalize_url(final_url)
                    results_by_key[result_key] = row
                    write_rows(PAGES_FILE, PAGE_FIELDS, list(results_by_key.values()))
                    processed += 1
                    self.events.put(("progress", processed, limit))
                    self.events.put(("log", f"{status_code}  {title[:100]}  — {final_url}"))
                except requests.RequestException as exc:
                    seen.add(key)
                    self.events.put(("log", f"Request failed: {url} ({exc})"))
                except Exception as exc:
                    seen.add(key)
                    self.events.put(("log", f"Page error: {url} ({exc})"))
                if todo and not self.stop_event.is_set():
                    time.sleep(delay)
            # Persist any links not reached due to page limit for later resume.
            old_queue = read_rows(QUEUE_FILE, QUEUE_FIELDS)
            known = {normalize_url(r.get("site_url", "")) for r in old_queue}
            for pending_url in todo:
                if normalize_url(pending_url) not in known:
                    old_queue.append({
                        "site_name": start_host or "site",
                        "site_url": pending_url,
                        "status": "pending",
                        "added_at": utc_now(),
                        "error": "",
                    })
            write_rows(QUEUE_FILE, QUEUE_FIELDS, old_queue)
            self.events.put(("log", f"Crawl session finished. Processed {processed} page(s)."))
        finally:
            self.events.put(("finished",))

    def _poll_events(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "log":
                    self.log_message(event[1])
                elif event[0] == "progress":
                    self.progress.configure(value=event[1], maximum=event[2])
                elif event[0] == "finished":
                    self.running = False
                    self.start_btn.configure(state="normal")
                    self.pause_btn.configure(state="disabled", text="Pause")
                    self.stop_btn.configure(state="disabled")
                    self._refresh_stats()
        except queue.Empty:
            pass
        self.root.after(120, self._poll_events)

    def export(self):
        self.pages = read_rows(PAGES_FILE, PAGE_FIELDS)
        path = filedialog.asksaveasfilename(
            title="Export crawled pages", defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")], initialfile="CRAWLED.csv"
        )
        if not path:
            return
        try:
            write_rows(path, PAGE_FIELDS, self.pages)
            messagebox.showinfo("Export complete", f"Exported {len(self.pages)} page(s).")
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc))

    def open_data_folder(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(DATA_DIR)
            elif os.sys.platform == "darwin":
                os.spawnlp(os.P_NOWAIT, "open", "open", DATA_DIR)
            else:
                os.spawnlp(os.P_NOWAIT, "xdg-open", "xdg-open", DATA_DIR)
        except OSError as exc:
            messagebox.showerror("Could not open folder", str(exc))

    def _close(self):
        if self.running and not messagebox.askyesno("Quit", "A crawl is running. Stop and exit?"):
            return
        self.stop_event.set()
        self.pause_event.set()
        self.root.destroy()


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "w", encoding="utf-8") as token_file:
            token_file.write("# Paste your GitHub access token on the next line. Keep this file private.\\n")
    read_rows(PAGES_FILE, PAGE_FIELDS)
    read_rows(QUEUE_FILE, QUEUE_FIELDS)
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    CrawlerApp(root)
    root.mainloop()
