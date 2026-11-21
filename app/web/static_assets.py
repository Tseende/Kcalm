"""Версионирование статики Mini App по содержимому каждого файла."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

APP_PATH = "/app/"
URL_ATTRIBUTES = {"href", "src", "poster"}


@lru_cache(maxsize=1024)
def _hash_file(path: Path, signature: tuple[int, int, int]) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:20]


def file_version(path: Path) -> str:
    stat = path.stat()
    return _hash_file(path, (stat.st_ino, stat.st_mtime_ns, stat.st_size))


def _safe_json(value: dict) -> str:
    return json.dumps(value, separators=(",", ":")).replace("<", "\\u003c")


class _Index(HTMLParser):
    def __init__(self, versions: dict[str, str]) -> None:
        super().__init__(convert_charrefs=False)
        self.versions = versions
        self.parts: list[str] = []

    def _version_url(self, value: str) -> str:
        target = urlsplit(urljoin(APP_PATH, value))
        if target.netloc or target.scheme or not target.path.startswith(APP_PATH):
            return value
        key = unquote(target.path[len(APP_PATH):])
        version = self.versions.get(key)
        if version is None:
            return value
        query = [(key, value) for key, value in parse_qsl(target.query, keep_blank_values=True) if key != "v"]
        query.append(("v", version))
        return urlunsplit(("", "", target.path, urlencode(query), target.fragment))

    def _start_tag(self, tag, attrs, ending):
        rewritten = [
            (key, self._version_url(value) if key in URL_ATTRIBUTES and value else value)
            for key, value in attrs
        ]
        if rewritten == attrs:
            self.parts.append(self.get_starttag_text())
            return
        serialized = [key if value is None else f'{key}="{escape(value, quote=True)}"'
                      for key, value in rewritten]
        self.parts.append("<" + " ".join([tag, *serialized]) + ending)

    def handle_starttag(self, tag, attrs):
        self._start_tag(tag, attrs, ">")
        if tag == "head":
            # Версии в query сохраняют исходную базу относительных импортов.
            imports = {}
            images = {}
            for name, version in self.versions.items():
                if name.endswith((".js", ".mjs")):
                    url = APP_PATH + quote(name)
                    imports[url] = f"{url}?v={version}"
                if name.startswith("img/"):
                    images[name] = version
            self.parts.extend([
                '\n  <script type="importmap">', _safe_json({"imports": imports}), "</script>",
                '\n  <script id="asset-versions" type="application/json">', _safe_json(images), "</script>",
            ])

    def handle_startendtag(self, tag, attrs):
        self._start_tag(tag, attrs, " />")

    def handle_endtag(self, tag):
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)

    def handle_decl(self, decl):
        self.parts.append(f"<!{decl}>")

    def handle_comment(self, data):
        self.parts.append(f"<!--{data}-->")

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")


@lru_cache(maxsize=4)
def build_index(root: Path) -> tuple[str, bytes]:
    versions = {}
    resolved = root.resolve()
    for path in sorted(root.rglob("*")):
        if (path.is_file() and resolved in path.resolve().parents
                and not any(part.startswith(".") for part in path.relative_to(root).parts)
                and path.name != "index.html"):
            versions[path.relative_to(root).as_posix()] = file_version(path)
    parser = _Index(versions)
    parser.feed((root / "index.html").read_text(encoding="utf-8"))
    parser.close()
    body = "".join(parser.parts).encode()
    return hashlib.sha256(body).hexdigest()[:20], body
