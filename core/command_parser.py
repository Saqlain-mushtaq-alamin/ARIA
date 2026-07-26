from __future__ import annotations

import re
from typing import Any


def _parse_open_target(target: str) -> dict | None:
    cleaned = (target or "").strip().rstrip(" .,!?:;")
    cleaned = re.sub(r"^(?:the|a|an)\s+", "", cleaned, flags=re.IGNORECASE)
    if not cleaned:
        return None
    lowered = cleaned.lower()

    m = re.search(
        r"(?P<name>[\w\s._-]+?)\s+folder\s+(?:inside|in)\s+(?:the\s+)?(?P<drive>[a-z])\s*drive",
        lowered,
    )
    if m:
        folder = m.group("name").strip().replace("/", "\\")
        folder = re.sub(r"\s+", " ", folder)
        path = f"{m.group('drive').upper()}:\\{folder}"
        return {"intent": "open_folder", "parameters": {"path": path}}

    m = re.search(r"\b([a-z])\s*drive\b", lowered)
    if m and "folder" in lowered:
        return {"intent": "open_folder", "parameters": {"path": f"{m.group(1).upper()}:\\"}}

    if re.match(r"^[a-zA-Z]:[\\/]", cleaned):
        return {"intent": "open_folder", "parameters": {"path": cleaned}}

    if "folder" in lowered and not lowered.endswith(".txt"):
        folder_name = re.sub(r"\bfolder\b", "", cleaned, flags=re.IGNORECASE).strip()
        if folder_name:
            return {"intent": "open_folder", "parameters": {"path": folder_name}}

    return {"intent": "open_app", "parameters": {"app_name": cleaned}}


def _parse_system_command(_: str) -> dict | None:
    return None


def _parse_browser_command(_: str) -> dict | None:
    return None


def _parse_scheduler_command(_: str) -> dict | None:
    return None


def _parse_app_control_command(_: str) -> dict | None:
    return None


def _parse_multistep_command(user_text: str) -> dict | None:
    text = (user_text or "").strip()
    if not text:
        return None

    if not re.search(r"\b(and|then)\b|,", text, flags=re.IGNORECASE):
        return None

    chunks = [
        c.strip()
        for c in re.split(r"\s*(?:,|;|\band then\b|\bthen\b)\s*", text, flags=re.IGNORECASE)
        if c.strip()
    ]
    if len(chunks) < 2 and not re.search(r"\band\b", text, flags=re.IGNORECASE):
        return None

    steps: list[dict[str, Any]] = []
    last_literal_text: str | None = None

    def _parse_save_target(chunk_text: str) -> dict | None:
        if not re.match(r"^save\b", chunk_text.strip(), re.IGNORECASE):
            return None

        path_match = re.search(r"\b([a-zA-Z]:[\\/][^\\/:*?\"<>|]+)", chunk_text)
        if path_match:
            path = path_match.group(1).strip()
            params: dict[str, Any] = {"path": path}
            if last_literal_text:
                params["content"] = last_literal_text
            return {"intent": "save_file", "parameters": params}

        loc_match = re.search(
            r"\b(?:to|in|on|into)\s+(?:the\s+)?(desktop|documents|downloads|pictures|music|videos)\b",
            chunk_text,
            flags=re.IGNORECASE,
        )
        loc = loc_match.group(1).lower() if loc_match else ""

        name_match = re.search(
            r"\b(?:as|named|name)\s+([^\\/:*?\"<>|]+)",
            chunk_text,
            flags=re.IGNORECASE,
        )
        if not name_match:
            name_match = re.search(
                r"\bsave\s+(?:it|this|the\s+file|file)?\s*([^\\/:*?\"<>|]+\.[\w\-]+)",
                chunk_text,
                flags=re.IGNORECASE,
            )
        filename = name_match.group(1).strip() if name_match else ""
        if loc and filename:
            filename = re.split(r"\s+(?:to|in|on|into)\s+", filename, maxsplit=1, flags=re.IGNORECASE)[0].strip()

        if filename and "." not in filename:
            filename = f"{filename}.txt"

        if loc:
            filename = filename or "note.txt"
            path = f"{loc}\\{filename}"
        elif filename:
            path = filename
        else:
            return None

        params = {"path": path}
        if last_literal_text:
            params["content"] = last_literal_text
        return {"intent": "save_file", "parameters": params}

    for chunk in chunks:
        open_match = re.match(r"^open\s+(.+)$", chunk, re.IGNORECASE)
        if open_match:
            targets = [
                t.strip()
                for t in re.split(r"\s+\band\b\s+", open_match.group(1), flags=re.IGNORECASE)
                if t.strip()
            ]
            for target in targets:
                save_target = _parse_save_target(target)
                if save_target:
                    steps.append(save_target)
                    continue
                write_in_open = re.match(r"^(?:write|type)\s+(.+)$", target, re.IGNORECASE)
                if write_in_open:
                    content = write_in_open.group(1).strip()
                    if any(k in content.lower() for k in ("story", "poem", "email", "essay", "article")):
                        steps.append({"intent": "type_text", "parameters": {"generate": True, "prompt": content}})
                    else:
                        steps.append({"intent": "type_text", "parameters": {"text": content}})
                        last_literal_text = content
                    continue
                parsed = _parse_open_target(target)
                if parsed:
                    steps.append(parsed)
            continue

        save_match = _parse_save_target(chunk)
        if save_match:
            steps.append(save_match)
            continue

        write_match = re.match(r"^(?:write|type)\s+(.+)$", chunk, re.IGNORECASE)
        if write_match:
            content = write_match.group(1).strip()
            if any(k in content.lower() for k in ("story", "poem", "email", "essay", "article")):
                steps.append({"intent": "type_text", "parameters": {"generate": True, "prompt": content}})
            else:
                steps.append({"intent": "type_text", "parameters": {"text": content}})
                last_literal_text = content
            continue

        parsed = (
            _parse_system_command(chunk)
            or _parse_app_control_command(chunk)
            or _parse_browser_command(chunk)
            or _parse_scheduler_command(chunk)
        )
        if parsed:
            steps.append(parsed)
            continue

        if len(chunk.split()) <= 4 and not re.search(
            r"\b(turn|set|search|click|extract|save|delete|copy|move)\b",
            chunk,
            re.IGNORECASE,
        ):
            for part in re.split(r"\s+\band\b\s+", chunk, flags=re.IGNORECASE):
                part = part.strip()
                if not part:
                    continue
                parsed = _parse_open_target(part)
                if parsed:
                    steps.append(parsed)

    if len(steps) >= 2:
        return {"intent": "multi_step", "steps": steps}
    return None
