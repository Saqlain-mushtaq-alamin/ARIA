"""Social media intelligence — read posts, generate comments, explain text."""
from __future__ import annotations
import base64, io, re
from typing import Optional
import ollama

from config.settings import get as get_setting

try:
    from vision.screen_reader import llm_busy_context
except Exception:
    from contextlib import nullcontext as llm_busy_context  # type: ignore


def _screenshot_b64() -> str:
    """Take a screenshot and return as base64-encoded PNG."""
    try:
        import mss, mss.tools
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            sct_img = sct.grab(monitor)
            png_bytes = mss.tools.to_png(sct_img.rgb, sct_img.size)
            return base64.b64encode(png_bytes).decode()
    except ImportError:
        pass
    import pyautogui
    img = pyautogui.screenshot()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def read_post_from_screen() -> str:
    """Use LLaVA to extract the post text from the current screen."""
    vision_model = get_setting("llm.vision_model", "llava:7b")
    b64 = _screenshot_b64()
    with llm_busy_context():
        result = ollama.chat(
            model=vision_model,
            messages=[{
                "role": "user",
                "content": (
                    "Read the main social media post on this screen. "
                    "Return ONLY the post text and author name. "
                    "Do not describe the UI. If no post is visible say 'NO_POST'."
                ),
                "images": [b64],
            }]
        )
    return result.get("message", {}).get("content", "").strip()


def comment_on_post(tone: str = "thoughtful") -> str:
    """Read the current post and generate a comment."""
    from modules.content_generator import generate_text

    post_text = read_post_from_screen()
    if "NO_POST" in post_text:
        return "Sir, I could not detect a social media post on the current screen."

    prompt = (
        f"Generate a {tone} comment for this social media post. "
        f"Keep it 1-3 sentences, natural, and engaging. "
        f"Do not start with 'Great post' or generic phrases. "
        f"Return ONLY the comment text.\n\nPost:\n{post_text}"
    )
    comment = generate_text(prompt).strip()
    return comment


def explain_selected_text() -> str:
    """Read whatever text is currently selected/highlighted on screen."""
    from modules.content_generator import generate_text

    # Try clipboard first (faster than vision)
    selected = ""
    try:
        from modules.system_control import get_selected_text
        selected = get_selected_text()
    except Exception:
        pass

    # Fall back to LLaVA visual detection
    if not selected or len(selected) < 3:
        vision_model = get_setting("llm.vision_model", "llava:7b")
        b64 = _screenshot_b64()
        with llm_busy_context():
            result = ollama.chat(
                model=vision_model,
                messages=[{
                    "role": "user",
                    "content": (
                        "There is highlighted/selected text on this screen. "
                        "Extract ONLY the highlighted text. "
                        "Return it exactly as written, nothing else."
                    ),
                    "images": [b64],
                }]
            )
        selected = result.get("message", {}).get("content", "").strip()

    if not selected or len(selected) < 3:
        return "Sir, I could not detect any selected text."

    explanation = generate_text(
        f"Explain this clearly and concisely, Sir:\n\n{selected}"
    )
    short_text = selected[:80] + "..." if len(selected) > 80 else selected
    return f'Selected text: "{short_text}"\n\n{explanation}'
