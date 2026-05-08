"""LLM-based content generation and intelligent question-answering.

This module is the "mind" of ARIA. It handles:

  generate_text()        — General purpose LLM response (stories, emails, code, etc.)
  answer_with_search()   — Decides whether to answer from LLM knowledge or fetch
                           live data from the web first, then synthesise a response.
  explain()              — Deep explanation of any topic. Searches the web if needed,
                           then structures the answer as a clear, human explanation.
  summarise_url()        — Fetch a URL and summarise its content.
  generate_stream()      — Streaming generator for real-time token output.
  chat()                 — Multi-turn conversation with memory of the session.
  generate_code()        — Specialised code generation with language awareness.
  translate()            — Translate text to any language.
  proofread()            — Grammar/spelling/style correction.
  bullet_points()        — Summarise any text as bullet points.

Web search is done via DuckDuckGo Instant Answer API (no key needed) and
a lightweight HTML scraper fallback — no Playwright required in this module
so it loads instantly.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from typing import Any, Dict, Generator, List, Optional

import ollama

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "llama3"
FAST_MODEL    = "llama3"       # same model; swap to "mistral" if you pull it
VISION_MODEL  = "llava"        # for image/screen understanding

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

# Keep minimal state for tool-style functions (best-effort; no Playwright here).
_LAST_URL: Optional[str] = None

# ─────────────────────────────────────────────────────────────────────────────
# ARIA system persona
# ─────────────────────────────────────────────────────────────────────────────

_ARIA_PERSONA = """You are ARIA (Adaptive Reasoning Intelligence Assistant), an advanced
AI desktop assistant running locally on the user's Windows computer.

Your personality:
- Warm, direct, and highly capable — like a brilliant friend who happens to know everything
- You give real answers, not hedged non-answers
- You speak in plain human language, never robotic or bureaucratic
- You proactively add useful context the user didn't ask for but would want
- You are honest when you don't know something and say so clearly
- You never refuse reasonable requests
- You match the user's tone: casual chat → casual reply, technical question → technical answer

Formatting rules:
- Use markdown only when it genuinely helps (code blocks, tables, lists)
- Keep replies concise unless the user asks for depth
- For explanations: lead with the key insight, then elaborate
- For comparisons: use a table if there are 3+ items
- Always end actionable advice with a concrete next step
"""

_WRITING_PERSONA = """You are an expert writing assistant. Produce clear, engaging,
correctly-structured content. Match the requested style and length exactly.
Never add disclaimers unless asked."""

_CODE_PERSONA = """You are an expert software engineer. Write clean, production-quality code.
Include comments for non-obvious logic. If the language is not specified, use Python.
Always include a brief usage example at the end."""

# ─────────────────────────────────────────────────────────────────────────────
# Low-level Ollama helpers
# ─────────────────────────────────────────────────────────────────────────────

def _chat(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 2048,
) -> str:
    """Send messages to Ollama and return the assistant reply as a string."""
    response = ollama.chat(
        model=model,
        messages=messages,
        options={
            "temperature": temperature,
            "num_predict": max_tokens,
        },
    )
    content = response.get("message", {}).get("content", "")
    return str(content).strip()


def _chat_stream(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
) -> Generator[str, None, None]:
    """Stream tokens from Ollama one chunk at a time."""
    stream = ollama.chat(
        model=model,
        messages=messages,
        stream=True,
        options={"temperature": temperature},
    )
    for chunk in stream:
        token = chunk.get("message", {}).get("content", "")
        if token:
            yield token


def _build_messages(
    system: str,
    user: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = [{"role": "system", "content": system}]
    if history:
        msgs.extend(history)
    msgs.append({"role": "user", "content": user})
    return msgs


# ─────────────────────────────────────────────────────────────────────────────
# Web fetch utilities (no Playwright — fast, lightweight)
# ─────────────────────────────────────────────────────────────────────────────

def _http_get(url: str, timeout: int = 10) -> str:
    """Fetch a URL and return the response body as text."""
    req = urllib.request.Request(url, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _strip_html(html: str, max_chars: int = 8000) -> str:
    """Remove HTML tags and collapse whitespace."""
    # Remove scripts and styles
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _ddg_instant_answer(query: str) -> Optional[str]:
    """Query DuckDuckGo Instant Answer API — returns snippet or None."""
    try:
        q = urllib.parse.quote_plus(query)
        url = f"https://api.duckduckgo.com/?q={q}&format=json&no_redirect=1&no_html=1&skip_disambig=1"
        raw = _http_get(url, timeout=8)
        data = json.loads(raw)
        # AbstractText is the best direct answer
        abstract = (data.get("AbstractText") or "").strip()
        if abstract:
            source = data.get("AbstractSource", "")
            return f"{abstract}\n\nSource: {source}" if source else abstract
        # RelatedTopics as fallback
        topics = data.get("RelatedTopics", [])
        snippets = []
        for t in topics[:4]:
            if isinstance(t, dict):
                text = t.get("Text", "").strip()
                if text:
                    snippets.append(text)
        return "\n".join(snippets) if snippets else None
    except Exception:
        return None


def _ddg_search_results(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Scrape DuckDuckGo HTML search for result titles + URLs."""
    results = []
    try:
        q = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={q}"
        html = _http_get(url, timeout=10)
        # Extract result links
        pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        for m in pattern.finditer(html):
            href = m.group(1).strip()
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if href and title and not href.startswith("//duckduckgo"):
                results.append({"title": title, "url": href})
                if len(results) >= max_results:
                    break
    except Exception:
        pass
    return results


def _fetch_page_text(url: str, max_chars: int = 6000) -> str:
    """Download a webpage and return its visible text content."""
    try:
        html = _http_get(url, timeout=12)
        return _strip_html(html, max_chars=max_chars)
    except Exception as exc:
        return f"[Could not fetch {url}: {exc}]"


# ─────────────────────────────────────────────────────────────────────────────
# Tool-style browser helpers (expected by core.agent / core.router)
#
# NOTE: This module intentionally avoids Playwright for fast startup.
# These functions provide lightweight capabilities (open URL, web search, page
# text extraction). Interactive page automation (click/fill) is not supported
# here and returns a clear message.
# ─────────────────────────────────────────────────────────────────────────────

def open_url(url: str, **_: Any) -> str:
    """Open a URL in the user's default browser."""
    global _LAST_URL
    if not url or not str(url).strip():
        raise ValueError("url is required")
    _LAST_URL = str(url).strip()
    try:
        webbrowser.open(_LAST_URL)
        return f"Opened URL: {_LAST_URL}"
    except Exception as exc:
        return f"Could not open URL ({_LAST_URL}): {exc}"


def search_web(
    query: str,
    engine: str = "duckduckgo",
    max_results: int = 5,
    **_: Any,
) -> str:
    """Search the web and return results.

    Uses DuckDuckGo HTML scrape (no key) regardless of engine for now.
    Accepts extra kwargs for compatibility with classifier/router.
    """
    q = (query or "").strip()
    if not q:
        raise ValueError("query is required")

    # Current implementation: DDG scraper fallback.
    results = _ddg_search_results(q, max_results=int(max_results) if max_results else 5)
    payload = {
        "engine": (engine or "duckduckgo"),
        "query": q,
        "results": results,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def extract_text(url: str, max_chars: int = 6000, **_: Any) -> str:
    """Fetch a URL and return visible text content."""
    global _LAST_URL
    if not url or not str(url).strip():
        raise ValueError("url is required")
    _LAST_URL = str(url).strip()
    try:
        return _fetch_page_text(_LAST_URL, max_chars=int(max_chars) if max_chars else 6000)
    except Exception as exc:
        return f"[Could not extract text from {_LAST_URL}: {exc}]"


def click_element(selector: str, **_: Any) -> str:
    """Click a CSS selector on the last page.

    This module does not keep a live browser session; use Playwright-based
    automation if you need interactive clicking.
    """
    sel = (selector or "").strip()
    if not sel:
        raise ValueError("selector is required")
    return "click_element is not supported in lightweight mode (no Playwright session)."


def fill_form(url: Optional[str] = None, fields: Optional[Dict[str, Any]] = None, **_: Any) -> str:
    """Fill a form on a web page.

    This module does not keep a live browser session; use Playwright-based
    automation if you need interactive form filling.
    """
    global _LAST_URL
    if url and str(url).strip():
        _LAST_URL = str(url).strip()
    if not fields:
        raise ValueError("fields is required")
    return "fill_form is not supported in lightweight mode (no Playwright session)."


def _google_news_rss(topic: str = "", count: int = 5) -> str:
    """Fetch Google News RSS and return formatted headlines."""
    try:
        if topic:
            q = urllib.parse.quote_plus(topic)
            url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        else:
            url = "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"
        raw = _http_get(url, timeout=10)
        root = ET.fromstring(raw)
        items = root.findall(".//item")[:count]
        if not items:
            return "No news found."
        lines = [f"📰 Top {len(items)} headlines{f' on {topic}' if topic else ''}:"]
        for i, item in enumerate(items, 1):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()[:16]
            lines.append(f"  {i}. {title}")
            if link:
                lines.append(f"     🔗 {link}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not fetch news: {exc}"


def _wttr_weather(location: str = "") -> str:
    """Fetch weather from wttr.in (no API key)."""
    try:
        loc = urllib.parse.quote_plus(location.strip()) if location.strip() else ""
        url = f"https://wttr.in/{loc}?format=4"
        return _http_get(url, timeout=8).strip()
    except Exception as exc:
        return f"Could not fetch weather: {exc}"


# ─────────────────────────────────────────────────────────────────────────────
# Smart answer routing
# ─────────────────────────────────────────────────────────────────────────────

# Topics that always need live web data
_LIVE_DATA_KEYWORDS = frozenset({
    "weather", "temperature", "rain", "forecast",
    "news", "headline", "latest", "today", "breaking",
    "price", "stock", "crypto", "bitcoin", "exchange rate",
    "score", "match", "game result", "live",
    "current", "right now", "this week", "this month",
    "who won", "who is the", "when did", "is it still",
})

_SEARCH_KEYWORDS = frozenset({
    "search", "find", "look up", "look for", "what is", "who is",
    "how to", "tell me about", "explain", "define", "research",
    "paper", "article", "study",
})


def _needs_web_search(prompt: str) -> bool:
    """Heuristic: return True if this prompt likely needs live web data."""
    lower = prompt.lower()
    return any(kw in lower for kw in _LIVE_DATA_KEYWORDS)


def _needs_search_engine(prompt: str) -> bool:
    lower = prompt.lower()
    return any(kw in lower for kw in _SEARCH_KEYWORDS)


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def generate_text(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    system: Optional[str] = None,
) -> str:
    """Generate a response for any prompt using ARIA's full persona.

    This is the primary entry point called by agent.py and question_generator.py.
    It automatically decides whether to search the web first.

    Args:
        prompt:      The user's request or question.
        model:       Ollama model name.
        temperature: 0 = deterministic, 1 = creative.
        system:      Optional system prompt override.

    Returns:
        A complete response string.
    """
    if not prompt or not prompt.strip():
        return ""

    # Route to specialised handlers for live data
    lower = prompt.lower()
    if any(w in lower for w in ("weather", "temperature", "rain", "forecast")):
        # Extract location
        loc_match = re.search(
            r"(?:weather|temperature|forecast)\s+(?:in|at|for)?\s*([A-Za-z ,]+?)(?:\?|$|\.|,)", prompt, re.IGNORECASE
        )
        location = loc_match.group(1).strip() if loc_match else ""
        weather = _wttr_weather(location)
        if "could not" not in weather.lower():
            return weather

    if any(w in lower for w in ("news", "headline", "latest news", "breaking")):
        topic_match = re.search(r"news (?:about|on|regarding)\s+(.+?)(?:\?|$|\.)", prompt, re.IGNORECASE)
        topic = topic_match.group(1).strip() if topic_match else ""
        return _google_news_rss(topic)

    sys_prompt = system or _ARIA_PERSONA
    msgs = _build_messages(sys_prompt, prompt)
    return _chat(msgs, model=model, temperature=temperature)


def answer_with_search(
    question: str,
    model: str = DEFAULT_MODEL,
    max_sources: int = 3,
) -> str:
    """Answer a question by searching the web first, then synthesising with the LLM.

    Pipeline:
      1. Try DuckDuckGo Instant Answer (fast, no JS)
      2. If no instant answer → scrape top search results
      3. Fetch text from top 2 pages
      4. Feed all gathered context into the LLM for a grounded answer
      5. Return a cited, synthesised response

    Args:
        question:    The user's question.
        model:       Ollama model name.
        max_sources: How many web pages to read.

    Returns:
        A comprehensive answer with source references.
    """
    if not question.strip():
        return "No question provided."

    context_parts: List[str] = []

    # Step 1: DuckDuckGo instant answer
    instant = _ddg_instant_answer(question)
    if instant:
        context_parts.append(f"[Instant Answer]\n{instant}")

    # Step 2: Search results
    results = _ddg_search_results(question, max_results=max_sources + 2)
    source_lines = []
    for i, r in enumerate(results[:max_sources], 1):
        source_lines.append(f"  [{i}] {r['title']} — {r['url']}")

    # Step 3: Fetch top pages
    for r in results[:max_sources]:
        url = r["url"]
        text = _fetch_page_text(url, max_chars=4000)
        if text and not text.startswith("[Could not"):
            context_parts.append(f"[Source: {r['title']} | {url}]\n{text}")

    if not context_parts and not results:
        # No web data — fall back to pure LLM
        return generate_text(question, model=model)

    context_block = "\n\n---\n\n".join(context_parts[:4])

    synthesis_prompt = (
        f"Using the web sources below, answer this question thoroughly and clearly:\n\n"
        f"QUESTION: {question}\n\n"
        f"WEB SOURCES:\n{context_block}\n\n"
        f"Instructions:\n"
        f"- Synthesise the sources into one coherent answer\n"
        f"- Use your own words — do not copy sentences directly\n"
        f"- Cite sources as [1], [2] etc. where relevant\n"
        f"- If sources conflict, note the discrepancy\n"
        f"- End with a 'Sources:' section listing the URLs\n"
        f"- If the sources don't answer the question, say so and use your own knowledge"
    )

    sources_section = ""
    if source_lines:
        sources_section = "\n\nSources checked:\n" + "\n".join(source_lines)

    msgs = _build_messages(_ARIA_PERSONA, synthesis_prompt)
    answer = _chat(msgs, model=model, temperature=0.3, max_tokens=2048)
    return answer + sources_section


def explain(
    topic: str,
    depth: str = "clear",
    model: str = DEFAULT_MODEL,
    search: bool = True,
) -> str:
    """Explain any topic clearly. Searches the web for up-to-date context.

    Args:
        topic:  What to explain (concept, term, technology, event, etc.)
        depth:  "simple" | "clear" | "deep" | "eli5" (explain like I'm 5)
        model:  Ollama model name.
        search: Whether to search the web for context first.

    Returns:
        A structured explanation.
    """
    if not topic.strip():
        return "Nothing to explain."

    depth_instructions = {
        "simple": "Use simple language. No jargon. 2-3 short paragraphs.",
        "clear":  "Be clear and thorough. Use an analogy. 3-4 paragraphs with an example.",
        "deep":   "Go deep. Cover history, mechanism, implications, current state, open questions.",
        "eli5":   "Explain like the user is 5 years old. Use a fun everyday analogy. Keep it short.",
    }.get(depth.lower(), "Be clear and thorough.")

    web_context = ""
    if search:
        instant = _ddg_instant_answer(topic)
        if instant:
            web_context = f"\n\nReference material from the web:\n{instant}"
        else:
            results = _ddg_search_results(topic, max_results=2)
            snippets = []
            for r in results[:2]:
                text = _fetch_page_text(r["url"], max_chars=3000)
                if text and not text.startswith("[Could not"):
                    snippets.append(f"[{r['title']}]\n{text[:2000]}")
            if snippets:
                web_context = "\n\nWeb context:\n" + "\n---\n".join(snippets)

    explain_prompt = (
        f"Explain this topic to the user: \"{topic}\"\n\n"
        f"Style: {depth_instructions}\n"
        f"Structure your response with:\n"
        f"  • A one-sentence headline answer at the top\n"
        f"  • The core explanation\n"
        f"  • A concrete real-world example or analogy\n"
        f"  • Why it matters / what to do with this knowledge\n"
        f"{web_context}"
    )

    msgs = _build_messages(_ARIA_PERSONA, explain_prompt)
    return _chat(msgs, model=model, temperature=0.4, max_tokens=2048)


def summarise_url(
    url: str,
    focus: str = "",
    model: str = DEFAULT_MODEL,
) -> str:
    """Fetch a URL and return an LLM-generated summary.

    Args:
        url:   The webpage to summarise.
        focus: Optional focus area (e.g. "main argument", "methodology").
        model: Ollama model name.

    Returns:
        A concise summary of the page content.
    """
    text = _fetch_page_text(url, max_chars=8000)
    if text.startswith("[Could not"):
        return text

    focus_line = f"\nFocus particularly on: {focus}" if focus else ""
    prompt = (
        f"Summarise the following webpage content clearly and concisely.{focus_line}\n\n"
        f"URL: {url}\n\n"
        f"PAGE CONTENT:\n{text}\n\n"
        f"Provide:\n"
        f"  • Main topic (1 sentence)\n"
        f"  • Key points (3-5 bullets)\n"
        f"  • Notable details or data\n"
        f"  • Your assessment of the content quality/relevance"
    )
    msgs = _build_messages(_ARIA_PERSONA, prompt)
    return _chat(msgs, model=model, temperature=0.3, max_tokens=1024)


def generate_stream(
    prompt: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    system: Optional[str] = None,
) -> Generator[str, None, None]:
    """Stream tokens from the LLM one chunk at a time.

    Use this for real-time display in the UI.

    Args:
        prompt:      The user's request.
        model:       Ollama model name.
        temperature: Creativity level.
        system:      Optional system prompt override.

    Yields:
        Token strings as they arrive from the model.
    """
    if not prompt or not prompt.strip():
        return
    sys_prompt = system or _ARIA_PERSONA
    msgs = _build_messages(sys_prompt, prompt)
    yield from _chat_stream(msgs, model=model, temperature=temperature)


def generate_code(
    request: str,
    language: str = "python",
    model: str = DEFAULT_MODEL,
) -> str:
    """Generate production-quality code for a request.

    Args:
        request:  What the code should do.
        language: Programming language (python, javascript, bash, sql, etc.)
        model:    Ollama model name.

    Returns:
        A complete code block with comments and a usage example.
    """
    prompt = (
        f"Write {language} code for the following task:\n\n{request}\n\n"
        f"Requirements:\n"
        f"  - Clean, readable, production-quality code\n"
        f"  - Comments for non-obvious logic\n"
        f"  - Handle edge cases and errors\n"
        f"  - Include a brief usage example at the end\n"
        f"  - Use a code block with language tag"
    )
    msgs = _build_messages(_CODE_PERSONA, prompt)
    return _chat(msgs, model=model, temperature=0.2, max_tokens=2048)


def chat(
    user_message: str,
    history: Optional[List[Dict[str, str]]] = None,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
) -> str:
    """Multi-turn conversational chat with session history.

    Args:
        user_message: The latest user message.
        history:      List of previous {"role": ..., "content": ...} dicts.
        model:        Ollama model name.
        temperature:  Creativity level.

    Returns:
        The assistant's reply as a string.
    """
    msgs = _build_messages(_ARIA_PERSONA, user_message, history=history)
    return _chat(msgs, model=model, temperature=temperature)


def translate(text: str, target_language: str, model: str = DEFAULT_MODEL) -> str:
    """Translate text into any target language.

    Args:
        text:            Text to translate.
        target_language: Language name (e.g. "Bengali", "French", "Arabic").
        model:           Ollama model name.

    Returns:
        The translated text with a brief note on any nuances.
    """
    prompt = (
        f"Translate the following text into {target_language}.\n"
        f"Output ONLY the translated text — no explanation, no prefix.\n\n"
        f"Text:\n{text}"
    )
    msgs = _build_messages("You are an expert translator.", prompt)
    return _chat(msgs, model=model, temperature=0.1, max_tokens=2048)


def proofread(text: str, model: str = DEFAULT_MODEL) -> str:
    """Correct grammar, spelling, punctuation, and improve clarity.

    Args:
        text:  Text to proofread.
        model: Ollama model name.

    Returns:
        Corrected text followed by a bullet list of changes made.
    """
    prompt = (
        f"Proofread and correct this text. Fix grammar, spelling, punctuation, "
        f"and awkward phrasing. Preserve the author's voice and meaning.\n\n"
        f"OUTPUT FORMAT:\n"
        f"[Corrected text]\n\n"
        f"---\nChanges made:\n• [list each correction]\n\n"
        f"ORIGINAL TEXT:\n{text}"
    )
    msgs = _build_messages(_WRITING_PERSONA, prompt)
    return _chat(msgs, model=model, temperature=0.1, max_tokens=2048)


def bullet_points(text: str, max_points: int = 7, model: str = DEFAULT_MODEL) -> str:
    """Summarise any text as concise bullet points.

    Args:
        text:       Text to summarise.
        max_points: Maximum number of bullet points.
        model:      Ollama model name.

    Returns:
        Bullet point summary.
    """
    prompt = (
        f"Summarise the following text as {max_points} clear bullet points. "
        f"Each bullet should be one complete, informative sentence. "
        f"Output ONLY the bullet points, no intro or outro.\n\n"
        f"TEXT:\n{text}"
    )
    msgs = _build_messages(_ARIA_PERSONA, prompt)
    return _chat(msgs, model=model, temperature=0.2, max_tokens=1024)


def analyse_image(
    image_path: str,
    question: str = "Describe what you see in this image.",
    model: str = VISION_MODEL,
) -> str:
    """Analyse an image using the local vision model (LLaVA).

    Args:
        image_path: Local file path to the image.
        question:   What to ask about the image.
        model:      Vision-capable Ollama model (llava, llava:13b, etc.)

    Returns:
        A text description or answer about the image.
    """
    import base64
    import os

    if not os.path.exists(image_path):
        return f"Image not found: {image_path}"

    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    try:
        response = ollama.chat(
            model=model,
            messages=[{
                "role": "user",
                "content": question,
                "images": [image_b64],
            }],
            options={"temperature": 0.3},
        )
        return response.get("message", {}).get("content", "No response from vision model.").strip()
    except Exception as exc:
        return f"Vision analysis failed: {exc}"


def get_weather(location: str = "") -> str:
    """Get current weather for a location (no API key needed)."""
    return _wttr_weather(location)


def get_news(topic: str = "", count: int = 5) -> str:
    """Get latest news headlines, optionally filtered by topic."""
    return _google_news_rss(topic, count)


def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return formatted results with titles and URLs."""
    results = _ddg_search_results(query, max_results=max_results)
    if not results:
        return f"No results found for: {query}"
    lines = [f"🔍 Search results for \"{query}\":"]
    for i, r in enumerate(results, 1):
        lines.append(f"  {i}. {r['title']}")
        lines.append(f"     🔗 {r['url']}")
    return "\n".join(lines)


def fetch_and_answer(url: str, question: str, model: str = DEFAULT_MODEL) -> str:
    """Fetch a URL and answer a specific question about its content.

    Args:
        url:      Webpage to read.
        question: What to find out from the page.
        model:    Ollama model name.

    Returns:
        An answer grounded in the page content.
    """
    text = _fetch_page_text(url, max_chars=7000)
    if text.startswith("[Could not"):
        return text

    prompt = (
        f"Based on the content of this webpage, answer the following question:\n\n"
        f"QUESTION: {question}\n\n"
        f"PAGE ({url}):\n{text}\n\n"
        f"If the page doesn't contain the answer, say so and provide what you know."
    )
    msgs = _build_messages(_ARIA_PERSONA, prompt)
    return _chat(msgs, model=model, temperature=0.2, max_tokens=1024)