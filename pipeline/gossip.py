"""2027 buzz column: news, analyst talk and investor chatter, public sources only.

One Claude API call per stock with the web search tool (max 3 searches).
Runs forward only; it never tries to reconstruct past sentiment.
"""
import json
import os
import re

from .http import pace, session

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

PROMPT = """You are filling one row of a personal stock watchlist.

Company: {name} ({sym}), sector: {sector}.

Search public sources from roughly the last 90 days: news, analyst notes,
earnings-call remarks, and investor discussion. Focus on what people expect
for the business in 2027. Use only public information.

Reply with ONLY a JSON object, no other text:
{{"buzz": "high" | "medium" | "low",
  "sentiment": "positive" | "mixed" | "negative",
  "outlook_2027": "one sentence, max 25 words",
  "catalysts": ["max 3 items, max 12 words each"],
  "risks": ["max 2 items, max 12 words each"]}}

"buzz" is how much attention the stock is getting, not whether it is good."""


def _extract(content):
    text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    sources, seen = [], set()
    for b in content:
        for c in b.get("citations") or []:
            url = c.get("url")
            if url and url not in seen:
                seen.add(url)
                sources.append({"title": c.get("title") or url, "url": url})
    if not sources:
        for b in content:
            if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
                for res in b["content"][:3]:
                    if res.get("url") and res["url"] not in seen:
                        seen.add(res["url"])
                        sources.append({"title": res.get("title") or res["url"], "url": res["url"]})
    return text, sources[:4]


def buzz(stock):
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    messages = [{"role": "user", "content": PROMPT.format(**stock)}]
    content = []
    for _ in range(3):  # allow a couple of continuations on pause_turn
        pace("anthropic", 1.0)
        r = session().post(API_URL, timeout=180, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"}, json={
            "model": MODEL, "max_tokens": 1200, "messages": messages,
            "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}]})
        r.raise_for_status()
        data = r.json()
        content += data.get("content", [])
        if data.get("stop_reason") != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": data["content"]}]
    text, sources = _extract(content)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise RuntimeError(f"no JSON in reply: {text[:160]}")
    out = json.loads(match.group(0))
    out["sources"] = sources
    out["model"] = MODEL
    return out
