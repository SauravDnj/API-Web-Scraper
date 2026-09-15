"""Thin client for the Serper.dev APIs (google.serper.dev + scrape.serper.dev).
Each user's own API key is passed in per request."""
import time

import requests

GOOGLE_URL = "https://google.serper.dev"
SCRAPE_URL = "https://scrape.serper.dev"
_session = requests.Session()


class SerperError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status

    @property
    def fatal(self) -> bool:
        """Errors that will fail every following request too (bad key, no credits)."""
        msg = str(self).lower()
        return self.status in (401, 403) or "api key" in msg or "credits" in msg or "unauthorized" in msg


class SerperClient:
    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()

    def _request(self, url: str, payload: dict, retries: int = 2, timeout: int = 40) -> dict:
        if not self.api_key:
            raise SerperError("No Serper API key set for this account", 401)
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        last_err, last_status = "request failed", 0
        for attempt in range(retries + 1):
            try:
                r = _session.post(url, json=payload, headers=headers, timeout=timeout)
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(1 + attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_err, last_status = f"HTTP {r.status_code}: {r.text[:200]}", r.status_code
                time.sleep(1.5 * (attempt + 1))
                continue
            try:
                data = r.json()
            except ValueError:
                raise SerperError(f"HTTP {r.status_code}: invalid JSON response", r.status_code)
            if r.status_code != 200:
                raise SerperError(data.get("message") or f"HTTP {r.status_code}", r.status_code)
            return data
        raise SerperError(last_err, last_status)

    def post(self, endpoint: str, payload: dict) -> dict:
        """Any google.serper.dev endpoint: search, images, videos, places, maps, reviews,
        news, shopping, lens, scholar, patents, autocomplete."""
        return self._request(f"{GOOGLE_URL}/{endpoint}", payload)

    def scrape(self, url: str, include_markdown: bool = True) -> dict:
        return self._request(SCRAPE_URL, {"url": url, "includeMarkdown": include_markdown}, retries=1, timeout=60)

    def account(self) -> dict:
        if not self.api_key:
            raise SerperError("No Serper API key set for this account", 401)
        r = _session.get(f"{GOOGLE_URL}/account", headers={"X-API-KEY": self.api_key}, timeout=20)
        if r.status_code != 200:
            try:
                msg = r.json().get("message")
            except ValueError:
                msg = None
            raise SerperError(msg or f"HTTP {r.status_code}", r.status_code)
        return r.json()
