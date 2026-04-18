import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class EbenApi:
	def __init__(self, base_url=None, timeout=10):
		self.base_url = (base_url or os.environ.get("EBEN_API_BASE_URL", "http://10.42.1.1:8000")).rstrip("/")
		self.timeout = timeout

	def _api_url(self, path, query=None):
		url = f"{self.base_url}{path}"
		if query:
			url = f"{url}?{urlencode(query)}"
		return url

	def request(self, method, path, query=None, json_body=None, data=None):
		headers = {}
		payload = data

		if json_body is not None:
			payload = json.dumps(json_body).encode("utf-8")
			headers["Content-Type"] = "application/json"

		request = Request(self._api_url(path, query), data=payload, headers=headers, method=method)

		try:
			with urlopen(request, timeout=self.timeout) as response:
				body = response.read()
				content_type = response.headers.get_content_type()
				charset = response.headers.get_content_charset() or "utf-8"
				text = body.decode(charset, errors="replace")
				return {
					"status": response.status,
					"content_type": content_type,
					"body": body,
					"text": text,
				}
		except HTTPError as exc:
			body = exc.read() if exc.fp else b""
			charset = exc.headers.get_content_charset() if exc.headers else None
			text = body.decode(charset or "utf-8", errors="replace") if body else ""
			raise RuntimeError(f"{exc.code} {exc.reason}\n{text}".strip()) from exc
		except URLError as exc:
			raise RuntimeError(f"Unable to reach Eben API at {self.base_url}: {exc.reason}") from exc
