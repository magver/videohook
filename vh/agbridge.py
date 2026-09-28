"""Прямое подключение к установленному Google Antigravity (language_server.exe `agentapi`).

Antigravity запускает локальный language server со случайным портом и CSRF-токеном в командной строке.
`agentapi` работает только если ему передать адрес сервера, токен и проект через переменные окружения
(внутри терминала Antigravity они уже заданы, снаружи — нет). Мост находит запущенный сервер, подбирает
рабочий маршрут (порт × проект) и кэширует его.

Протокол задач — через файлы: агент получает короткий промпт со ссылкой на instructions.md и записывает
ответ в заданный файл; параллельно читается transcript.jsonl диалога (если агент записал результат
не в тот путь или ответил текстом — ответ всё равно подхватывается).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .core import JOBS_DIR, SUBPROCESS_FLAGS, get_settings, new_id

log = logging.getLogger("videohook.agbridge")

APP_DATA = Path.home() / ".gemini" / "antigravity"
EXE_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / "antigravity" / "Antigravity.exe",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Antigravity" / "Antigravity.exe",
]
PROJECTS = ("outside-of-project", "")

_route: Optional[Tuple[str, str, int, str]] = None   # (exe, csrf, port, project)
_lock = threading.Lock()


class AntigravityError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Поиск сервера
# ---------------------------------------------------------------------------
def installed_exe() -> str:
    custom = (get_settings().get("antigravity_cmd") or "").strip()
    for p in ([Path(custom).expanduser()] if custom else []) + EXE_CANDIDATES:
        if p.name.lower() == "antigravity.exe" and p.exists():
            return str(p)
    return shutil.which("antigravity") or ""


def scan() -> List[Dict[str, Any]]:
    """Запущенные language server'ы Antigravity: путь, CSRF-токен, слушающие порты."""
    try:
        import psutil
    except ImportError:
        log.warning("psutil не установлен — Antigravity не будет найден")
        return []
    servers = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "exe"]):
        try:
            if "language_server" not in (p.info.get("name") or "").lower():
                continue
            cmd = p.info.get("cmdline") or []
            exe = p.info.get("exe") or (cmd[0] if cmd else "")
            if not exe or "antigravity" not in exe.lower() or not os.path.exists(exe):
                continue
            csrf = cmd[cmd.index("--csrf_token") + 1] if "--csrf_token" in cmd else ""
            ports = sorted({c.laddr.port for c in p.net_connections(kind="tcp") if c.status == "LISTEN" and c.laddr})
            if csrf and ports:
                servers.append({"exe": exe, "csrf": csrf, "ports": ports, "pid": p.info["pid"]})
        except Exception:  # noqa: BLE001 — процесс мог завершиться или быть недоступен
            continue
    return servers


def ensure_running(progress: Optional[Callable[[str], None]] = None) -> List[Dict[str, Any]]:
    servers = scan()
    if servers or not get_settings().get("antigravity_auto_launch", True):
        return servers
    exe = installed_exe()
    if not exe:
        return []
    if progress:
        progress("Запускаю Antigravity…")
    try:
        subprocess.Popen([exe], creationflags=SUBPROCESS_FLAGS)
    except OSError as exc:
        log.warning("Antigravity launch: %s", exc)
        return []
    for _ in range(45):
        time.sleep(1.0)
        servers = scan()
        if servers:
            time.sleep(3.0)  # сервер ещё догружается
            return servers
    return []


def status() -> Dict[str, Any]:
    servers = scan()
    return {"running": bool(servers), "installed": bool(servers) or bool(installed_exe()),
            "agentapi": bool(servers), "cmd": servers[0]["exe"] + " agentapi" if servers else "",
            "brain_dir": str(brain_dir()), "model": get_settings().get("antigravity_model")}


def available() -> bool:
    return bool(scan()) or (bool(get_settings().get("antigravity_auto_launch", True)) and bool(installed_exe()))


def brain_dir() -> Path:
    custom = get_settings().get("antigravity_brain_dir")
    return Path(custom).expanduser() if custom else APP_DATA / "brain"


# ---------------------------------------------------------------------------
# agentapi
# ---------------------------------------------------------------------------
def _env(csrf: str, port: int, project: str) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTIGRAVITY_")}
    env.update({"ANTIGRAVITY_CSRF_TOKEN": csrf, "ANTIGRAVITY_LS_ADDRESS": f"localhost:{port}",
                "ANTIGRAVITY_OS": "windows" if os.name == "nt" else "linux",
                "ANTIGRAVITY_APP_DATA_DIR": APP_DATA.as_posix()})
    if project:
        env["ANTIGRAVITY_PROJECT_ID"] = project
    return env


def _run(exe: str, args: List[str], env: Dict[str, str], timeout: int = 60) -> Tuple[int, Dict[str, Any], str]:
    proc = subprocess.run([exe, "agentapi", *args], env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, creationflags=SUBPROCESS_FLAGS)
    out = (proc.stdout or "").strip()
    try:
        data = json.loads(out) if out else {}
    except ValueError:
        data = {"raw": out}
    return proc.returncode, data if isinstance(data, dict) else {"raw": out}, out + (proc.stderr or "")


def agentapi(args: List[str], servers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Выполняет команду agentapi, перебирая порты и проекты, пока одна не сработает."""
    global _route
    routes: List[Tuple[str, str, int, str]] = [_route] if _route else []
    for srv in servers if servers is not None else scan():
        for port in srv["ports"]:
            for proj in PROJECTS:
                r = (srv["exe"], srv["csrf"], port, proj)
                if r not in routes:
                    routes.append(r)
    if not routes:
        raise AntigravityError("Antigravity не запущен")
    last_err = "Antigravity не ответил"
    for exe, csrf, port, proj in routes:
        try:
            code, data, text = _run(exe, args, _env(csrf, port, proj))
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_err = str(exc)
            continue
        if code == 0 and data.get("response") is not None and not data.get("error"):
            _route = (exe, csrf, port, proj)
            return data["response"]
        hint = re.search(r'source project_id \\?"([^"\\]+)\\?"', text)
        if hint:
            code, data, text = _run(exe, args, _env(csrf, port, hint.group(1)))
            if code == 0 and data.get("response") is not None and not data.get("error"):
                _route = (exe, csrf, port, hint.group(1))
                return data["response"]
        last_err = str(data.get("error") or text[:300] or last_err)
    _route = None
    raise AntigravityError(last_err)


def new_conversation(prompt: str, title: str, model: str = "") -> str:
    model = model or get_settings().get("antigravity_model_flag") or "flash"
    resp = agentapi(["new-conversation", f"--model={model}", f"--title={title[:80]}", prompt],
                    servers=ensure_running())
    cid = (resp.get("newConversation") or {}).get("conversationId", "")
    if not cid:
        raise AntigravityError("Antigravity не вернул id диалога")
    return cid


# ---------------------------------------------------------------------------
# Задачи с ответом в файле
# ---------------------------------------------------------------------------
def transcript_path(cid: str) -> Path:
    return brain_dir() / cid / ".system_generated" / "logs" / "transcript.jsonl"


def extract_json(text: str) -> Any:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except ValueError:
                return None
    return None


def _read_result(path: Path, expect: str) -> Optional[Any]:
    if not path.exists() or path.stat().st_size < 2:
        return None
    raw = path.read_text(encoding="utf-8", errors="replace")
    if expect == "json":
        return extract_json(raw)
    return raw if raw.strip() else None


def wait_result(cid: str, result_path: Path, expect: str = "json", timeout: int = 600,
                progress: Optional[Callable[[str], None]] = None) -> Any:
    """Ждёт файл результата; параллельно разбирает transcript диалога."""
    deadline = time.time() + timeout
    seen = -1
    final_msg, final_at = "", 0.0
    tpath = transcript_path(cid)
    started = time.time()
    while time.time() < deadline:
        time.sleep(1.5)
        res = _read_result(result_path, expect)
        if res is not None:
            return res
        if not tpath.exists():
            if progress:
                progress(f"Antigravity: ожидание ответа… {int(time.time() - started)} c")
            continue
        try:
            lines = tpath.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            step = int(obj.get("step_index", -1))
            if step <= seen:
                continue
            seen = step
            for tc in obj.get("tool_calls") or []:
                args = tc.get("args") or {}
                if progress:
                    progress("Antigravity: " + str(args.get("toolSummary") or tc.get("name", "работает"))[:100])
                target = str(args.get("TargetFile", "")).replace("\\", "/")
                content = args.get("CodeContent")
                if tc.get("name") == "write_to_file" and isinstance(content, str) and target.endswith(result_path.name):
                    data = extract_json(content) if expect == "json" else content
                    if data:
                        return data
            if obj.get("type") == "PLANNER_RESPONSE" and not obj.get("tool_calls") and obj.get("content"):
                final_msg, final_at = obj["content"], time.time()
                if expect == "json":
                    data = extract_json(final_msg)
                    if data:
                        return data
        if final_at and time.time() - final_at > 15:
            res = _read_result(result_path, expect)
            if res is not None:
                return res
            if expect == "text" and final_msg.strip():
                return final_msg
            raise AntigravityError("Antigravity завершил работу без результата: " + final_msg.strip()[:300])
    raise AntigravityError(f"Antigravity не ответил за {timeout // 60} мин")


def run_task(name: str, instructions: str, expect: str = "json", files: Optional[List[str]] = None,
             timeout: int = 600, progress: Optional[Callable[[str], None]] = None) -> Any:
    """Одна ИИ-задача в новом диалоге Antigravity. Возвращает JSON (dict/list) или текст."""
    with _lock:
        if not ensure_running(progress):
            raise AntigravityError("Antigravity не запущен и не найден на компьютере")
        work = JOBS_DIR / "_ai" / new_id(f"{re.sub(r'[^A-Za-z0-9_]', '_', name)[:20]}_")
        work.mkdir(parents=True, exist_ok=True)
        ext = "json" if expect == "json" else "md"
        result_path = work / f"result.{ext}"
        instr_path = work / "instructions.md"
        body = [f"# VideoHook · {name}", ""]
        if files:
            body += ["## Файлы для изучения (открой каждый инструментом `view_file`)",
                     *[f"- `{Path(f).as_posix()}`" for f in files], ""]
        body += ["## Порядок работы",
                 "1. Изучи задание ниже" + (" и перечисленные файлы." if files else "."),
                 f"2. Запиши ответ инструментом `write_to_file` (Overwrite=true) строго в файл `{result_path.as_posix()}`"
                 + (" — только валидный JSON без markdown." if expect == "json" else "."),
                 "3. Не вызывай `run_command`, не изучай посторонние файлы и код. Это независимая задача.",
                 "", "## Задание", instructions]
        instr_path.write_text("\n".join(body), encoding="utf-8")
        prompt = (f"VideoHook task. Read {instr_path.as_posix()} with view_file"
                  + (", then view the listed media files" if files else "")
                  + f", and write the answer to {result_path.as_posix()} using write_to_file with Overwrite=true. "
                    "Do NOT call run_command. Answer in Russian.")
        if progress:
            progress("Antigravity: создаю диалог…")
        cid = new_conversation(prompt, f"VideoHook: {name}")
        log.info("Antigravity task %s → %s", name, cid)
        return wait_result(cid, result_path, expect, timeout, progress)
