"""JSON document storage.

* Local (your PC):  plain JSON files under ./data
* Vercel:           the same JSON documents in a private Vercel Blob store
                    (BLOB_READ_WRITE_TOKEN) or in Upstash Redis (KV_REST_API_URL /
                    KV_REST_API_TOKEN) - whichever the project has connected

Paths look like  users/<email-hash>.json,  data/<user-id>/jobs.json,  data/<user-id>/jobs/<job-id>.json
Job documents can be large, so they are stored gzip-compressed on Blob (".json.gz").
"""
import base64
import gzip
import json
import os
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent


class StorageError(Exception):
    pass


class LocalStore:
    kind = "local"

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise StorageError("invalid storage key")
        return path

    def get_json(self, key: str):
        path = self._path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put_json(self, key: str, obj):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    # jobs: the browser uploads gzip; locally we keep readable .json files
    def get_gz(self, key: str) -> bytes | None:
        path = self._path(key)
        return gzip.compress(path.read_bytes(), 5) if path.exists() else None

    def put_gz(self, key: str, data: bytes):
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(gzip.decompress(data))
        tmp.replace(path)

    def delete(self, key: str):
        self._path(key).unlink(missing_ok=True)


class BlobStore:
    """Minimal client for a *private* Vercel Blob store (same HTTP API as the @vercel/blob SDK)."""
    kind = "blob"
    API = "https://vercel.com/api/blob"
    API_VERSION = "12"

    def __init__(self, token: str):
        self.token = token
        parts = token.split("_")
        self.store_id = parts[3] if len(parts) > 3 else ""
        if not self.store_id:
            raise StorageError("BLOB_READ_WRITE_TOKEN looks invalid")
        self.session = requests.Session()

    def _headers(self, extra=None):
        h = {"authorization": f"Bearer {self.token}", "x-api-version": self.API_VERSION,
             "x-vercel-blob-store-id": self.store_id}
        h.update(extra or {})
        return h

    def _get_bytes(self, key: str) -> bytes | None:
        # cache=0 skips the CDN so we always read the latest version of the document
        url = f"https://{self.store_id}.private.blob.vercel-storage.com/{key}"
        r = self.session.get(url, params={"cache": "0"}, headers={"authorization": f"Bearer {self.token}"}, timeout=30)
        if r.status_code == 404:
            return None
        if not r.ok:
            raise StorageError(f"Blob read failed ({r.status_code})")
        return r.content

    def _put_bytes(self, key: str, data: bytes, content_type: str):
        r = self.session.put(self.API + "/", params={"pathname": key}, data=data, timeout=60, headers=self._headers({
            "x-vercel-blob-access": "private", "x-add-random-suffix": "0", "x-allow-overwrite": "1",
            "x-content-type": content_type, "x-cache-control-max-age": "60",
        }))
        if not r.ok:
            raise StorageError(f"Blob write failed ({r.status_code}): {r.text[:200]}")

    def get_json(self, key: str):
        data = self._get_bytes(key)
        return None if data is None else json.loads(data.decode("utf-8"))

    def put_json(self, key: str, obj):
        self._put_bytes(key, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")

    def get_gz(self, key: str) -> bytes | None:
        return self._get_bytes(key + ".gz")

    def put_gz(self, key: str, data: bytes):
        self._put_bytes(key + ".gz", data, "application/gzip")

    def delete(self, key: str):
        for k in (key, key + ".gz"):
            url = f"https://{self.store_id}.private.blob.vercel-storage.com/{k}"
            r = self.session.post(self.API + "/delete", json={"urls": [url]}, timeout=30,
                                  headers=self._headers({"content-type": "application/json"}))
            if not r.ok and r.status_code != 404:
                raise StorageError(f"Blob delete failed ({r.status_code})")


class RedisStore:
    """JSON documents in Upstash Redis (Vercel Marketplace), through its REST API.
    Keys are the document paths; gzip job files are stored base64-encoded."""
    kind = "redis"

    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token
        self.session = requests.Session()

    def _cmd(self, *args):
        r = self.session.post(self.url, json=[str(a) for a in args],
                              headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
        if not r.ok:
            raise StorageError(f"Redis {args[0]} failed ({r.status_code}): {r.text[:200]}")
        return r.json().get("result")

    def get_json(self, key: str):
        val = self._cmd("GET", key)
        return json.loads(val) if val else None

    def put_json(self, key: str, obj):
        self._cmd("SET", key, json.dumps(obj, ensure_ascii=False))

    def get_gz(self, key: str) -> bytes | None:
        val = self._cmd("GET", key)
        return base64.b64decode(val) if val else None

    def put_gz(self, key: str, data: bytes):
        self._cmd("SET", key, base64.b64encode(data).decode())

    def delete(self, key: str):
        self._cmd("DEL", key)


def get_store():
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if token:
        return BlobStore(token)
    redis_url = (os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL") or "").strip()
    redis_token = (os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN") or "").strip()
    if redis_url and redis_token:
        return RedisStore(redis_url, redis_token)
    if os.environ.get("VERCEL"):
        raise StorageError("No storage connected: add a Vercel Blob store (BLOB_READ_WRITE_TOKEN) "
                           "or an Upstash Redis database (KV_REST_API_URL / KV_REST_API_TOKEN)")
    return LocalStore(local_data_dir())


def local_data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR") or BASE_DIR / "data")
