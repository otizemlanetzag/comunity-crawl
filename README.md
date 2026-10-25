# Community Crawl — Desktop Crawler

A local desktop app for crawling public websites without relying on Vercel function limits.

## Requirements

- Python 3.10+
- Windows, macOS, or Linux

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Run

Windows: double-click `run_windows.bat`  
Or run:

```bash
python main.py
```

## Features

- Desktop GUI built with Tkinter
- Crawl a starting URL and follow discovered links
- Option to stay on the starting hostname
- Configurable page limit and request delay
- Pause/resume and stop controls
- Saves extracted page titles, text, links and status to `data/CRAWLED.CSV`
- Keeps a persistent crawl queue in `data/CRAWL_QUEUE.CSV`
- Exports results to CSV

Only crawl websites you have permission to access. Respect each website's terms and robots.txt, keep request rates modest, and do not use this to bypass access controls. The app checks public IP addresses and refuses private/local network targets to reduce SSRF and local-network access risks.

This is a desktop program: it runs while the computer and app are running. It does not run after the app is closed unless you start it again.
