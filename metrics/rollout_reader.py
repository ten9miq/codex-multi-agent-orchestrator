#!/usr/bin/env python3
"""Codex rollout JSONL 共通リーダー。

collect.py と smoke.py から共通利用する内部モジュール。
標準ライブラリのみで動作する。

WindowsではCodexがrollout JSONLへ書き込み中でも解析できるよう、
FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE を指定して読み取り専用で開く。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, TextIO

ROUTE_BY_ROLE = {
    "scout": "SCOUT_LUNA",
    "worker_terra": "WORKER_TERRA",
    "controller_sol": "CONTROLLER_SOL",
    "expert": "EXPERT_SOL",
    "controller_astra": "CONTROLLER_ASTRA",
}
ROUTE_RANK = {
    "DIRECT_LUNA": 0,
    "SCOUT_LUNA": 0,
    "WORKER_TERRA": 1,
    "CONTROLLER_SOL": 2,
    "CONTROLLER_ASTRA": 3,
}
WAIT_TOOLS = {"wait_agent", "list_agents", "wait", "write_stdin"}
KNOWN_TOOLS = WAIT_TOOLS | {
    "spawn_agent",
    "followup_task",
    "send_message",
    "send_input",
    "resume_agent",
}
REWORK_CUES = re.compile(
    r"(?:違う|間違|修正|直して|直せて|まだ|再度|やり直|そうでは|ではなく|not what|wrong|fix it|still|again|instead)",
    re.IGNORECASE,
)
PROTOCOL_STATUS_RE = re.compile(r"ROUTER_STATUS:\s*(COMPLETE|ESCALATE_SOL|ESCALATE_ASTRA|BLOCKED)")
PROTOCOL_ROUTE_RE = re.compile(r"ROUTER_ROUTE:\s*([A-Z0-9_]+)")
PROTOCOL_VERIFY_RE = re.compile(r"ROUTER_VERIFY:\s*(PASS|FAIL|NOT_RUN)")
PROTOCOL_RETRY_RE = re.compile(r"ROUTER_RETRY:\s*(\d+)")
USER_RESULT_RE = re.compile(
    r"USER_RESULT_BEGIN\s*(?P<body>.*?)\s*USER_RESULT_END",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class JsonlStats:
    lines: int = 0
    json_errors: int = 0


def parse_ts(value: Any) -> datetime | None:
    """Codex/ISO時刻とPowerShell/.NETのround-trip時刻をdatetimeへ変換する。

    Windows PowerShellの DateTime.ToString("o") は小数秒を7桁で出力するため、
    Python側では6桁へ正規化してから datetime.fromisoformat() へ渡す。
    """
    if not isinstance(value, str) or not value:
        return None

    text = value.strip().replace("Z", "+00:00")

    # .NET "o" 例:
    #   2026-09-12T20:31:28.1234567+09:00
    # Python datetimeはmicrosecond精度なので7桁以上を6桁へ切り詰める。
    text = re.sub(
        r"(?<=\d\.\d{6})\d+(?=(?:[+-]\d{2}:\d{2})?$)",
        "",
        text,
    )

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        # timezoneなしの --since はUTCではなくローカル時刻として扱う。
        dt = dt.astimezone()
    return dt


def walk(obj: Any) -> Iterable[Any]:
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from walk(value)


def first_key(obj: Any, key: str) -> Any:
    for node in walk(obj):
        if isinstance(node, dict) and key in node:
            return node[key]
    return None


def nested_get(obj: Any, path: str) -> Any:
    current = obj
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def first_path(obj: Any, paths: Iterable[str]) -> Any:
    for path in paths:
        value = nested_get(obj, path)
        if value is not None and value != "":
            return value
    return None


def all_strings(obj: Any) -> str:
    parts: list[str] = []
    for node in walk(obj):
        if isinstance(node, str):
            parts.append(node)
    return "\n".join(parts)


def event_type(obj: dict[str, Any]) -> str | None:
    if obj.get("type") == "event_msg" and isinstance(obj.get("payload"), dict):
        value = obj["payload"].get("type")
        return value if isinstance(value, str) else None
    value = obj.get("type")
    return value if isinstance(value, str) else None


def token_info(obj: Any) -> dict[str, Any] | None:
    for node in walk(obj):
        if isinstance(node, dict) and "total_token_usage" in node and "last_token_usage" in node:
            return node
    return None


def usage_values(value: Any) -> dict[str, int]:
    keys = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")
    if not isinstance(value, dict):
        return {key: 0 for key in keys}
    return {
        "input_tokens": int(value.get("input_tokens") or 0),
        "cached_input_tokens": int(value.get("cached_input_tokens") or 0),
        "output_tokens": int(value.get("output_tokens") or 0),
        "reasoning_tokens": int(value.get("reasoning_output_tokens") or value.get("reasoning_tokens") or 0),
        "total_tokens": int(value.get("total_tokens") or 0),
    }


def total_signature(info: dict[str, Any]) -> tuple[int, int, int, int, int]:
    usage = usage_values(info.get("total_token_usage"))
    return (
        usage["input_tokens"],
        usage["cached_input_tokens"],
        usage["output_tokens"],
        usage["reasoning_tokens"],
        usage["total_tokens"],
    )


def nonnegative_int(value: Any) -> int | None:
    """JSONL中のtoken数・window値を、欠損と0を区別して読む。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def context_snapshot(info: dict[str, Any]) -> tuple[int | None, int | None]:
    """token_count.infoから実効windowと当該要求のcontext token数を返す。

    ``total_token_usage`` はrollout全体の累積値なので用いない。実ログにある
    ``last_token_usage.total_tokens`` をその時点のcontext使用量として採用する。
    旧schemaで ``total_tokens`` がない場合だけ input + output を使う。
    """
    window = nonnegative_int(info.get("model_context_window"))
    usage = info.get("last_token_usage")
    if not isinstance(usage, dict):
        return window, None

    tokens = nonnegative_int(usage.get("total_tokens"))
    if tokens is not None:
        return window, tokens

    input_tokens = nonnegative_int(usage.get("input_tokens"))
    output_tokens = nonnegative_int(usage.get("output_tokens"))
    if input_tokens is None and output_tokens is None:
        return window, None
    return window, (input_tokens or 0) + (output_tokens or 0)


def usage_pct(tokens: int | None, window: int | None) -> float | None:
    if tokens is None or window is None or window <= 0:
        return None
    return tokens / window * 100


def extract_tool_calls(obj: Any) -> list[tuple[str, str, dict[str, Any]]]:
    output: list[tuple[str, str, dict[str, Any]]] = []
    for node in walk(obj):
        if not isinstance(node, dict):
            continue
        name = node.get("name") or node.get("tool")
        if not isinstance(name, str):
            continue
        short = name.split(".")[-1].split("__")[-1]
        if short not in KNOWN_TOOLS:
            continue
        call_id = str(node.get("call_id") or node.get("id") or "")
        args: dict[str, Any] = {}
        raw = node.get("arguments")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                args = parsed
        elif isinstance(raw, dict):
            args = raw
        output.append((short, call_id, args))
    return output


def user_text_from_line(obj: dict[str, Any]) -> str:
    for node in walk(obj):
        if not isinstance(node, dict) or node.get("role") != "user":
            continue
        text = all_strings(node)
        if text:
            return text
    return ""


def assistant_text_from_line(obj: dict[str, Any]) -> str:
    """1行中のassistant message本文だけを返す。"""
    for node in walk(obj):
        if not isinstance(node, dict) or node.get("role") != "assistant":
            continue
        text = all_strings(node)
        if text:
            return text
    return ""


def source_metadata(session_meta: dict[str, Any]) -> tuple[bool, str | None, str | None, str | None]:
    source = first_key(session_meta, "source")
    source_text = json.dumps(source, ensure_ascii=False) if source is not None else ""
    role = first_key(source, "agent_role") if source is not None else None
    parent = first_key(source, "parent_thread_id") if source is not None else None
    task_name = first_path(
        source,
        (
            "subagent.thread_spawn.task_name",
            "subagent.thread_spawn.agent_path",
            "subagent.thread_spawn.agent_nickname",
            "subagent.agent_path",
        ),
    ) if isinstance(source, dict) else None
    if task_name is None and source is not None:
        task_name = first_key(source, "task_name") or first_key(source, "agent_path")
    is_child = bool(parent) or "subagent" in source_text.lower() or "thread_spawn" in source_text.lower()
    return (
        is_child,
        role if isinstance(role, str) else None,
        parent if isinstance(parent, str) else None,
        task_name if isinstance(task_name, str) else None,
    )


@contextlib.contextmanager
def open_shared_text(path: Path) -> Iterator[TextIO]:
    """Codexが書き込み中のrolloutを共有読み取りで開く。"""
    if os.name != "nt":
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            yield handle
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    file_attribute_normal = 0x00000080

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE

    handle = create_file(
        str(path),
        generic_read,
        file_share_read | file_share_write | file_share_delete,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        raise OSError(error, f"CreateFileW failed for {path}")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    try:
        fd = msvcrt.open_osfhandle(int(handle), flags)
    except Exception:
        kernel32.CloseHandle(handle)
        raise

    with os.fdopen(fd, "r", encoding="utf-8", errors="replace", newline="") as stream:
        yield stream


def iter_jsonl(path: Path, stats: JsonlStats | None = None) -> Iterator[dict[str, Any]]:
    stats = stats if stats is not None else JsonlStats()
    with open_shared_text(path) as handle:
        for raw in handle:
            stats.lines += 1
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                # 書き込み中rolloutの末尾が不完全なJSONなら、その行だけ無視する。
                # 次回実行時に完成した行を取得できる。
                stats.json_errors += 1
                continue
            if isinstance(obj, dict):
                yield obj


@dataclass
class SessionSummary:
    path: str
    session_id: str
    parent_thread_id: str | None = None
    agent_role: str | None = None
    task_name: str | None = None
    is_child: bool = False
    model: str = ""
    reasoning_effort: str = ""
    multi_agent_version: str = ""
    first_timestamp: str = ""
    last_timestamp: str = ""
    turn_context_count: int = 0
    json_errors: int = 0
    context_window: int | None = None
    context_tokens: int | None = None
    context_peak_tokens: int | None = None
    context_usage_pct: float | None = None
    context_peak_usage_pct: float | None = None


def parse_session_summary(path: Path) -> SessionSummary:
    session_meta: dict[str, Any] = {}
    session_id = path.stem
    is_child = False
    role: str | None = None
    parent: str | None = None
    task_name: str | None = None
    model = ""
    effort = ""
    mav = ""
    first_timestamp = ""
    last_timestamp = ""
    turn_context_count = 0
    context_window: int | None = None
    context_tokens: int | None = None
    context_peak_tokens: int | None = None
    stats = JsonlStats()

    for obj in iter_jsonl(path, stats):
        ts = obj.get("timestamp")
        if isinstance(ts, str):
            if not first_timestamp:
                first_timestamp = ts
            last_timestamp = ts
        typ = obj.get("type")
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
        if typ == "session_meta":
            session_meta = payload if isinstance(payload, dict) else {}
            value = first_key(session_meta, "id") or first_key(session_meta, "thread_id")
            if isinstance(value, str):
                session_id = value
            is_child, role, parent, task_name = source_metadata(session_meta)
        elif typ == "turn_context":
            turn_context_count += 1
            value = first_key(payload, "model")
            if isinstance(value, str):
                model = value
            value = first_key(payload, "effort") or first_key(payload, "reasoning_effort")
            if isinstance(value, str):
                effort = value
            value = first_key(payload, "multi_agent_version")
            if isinstance(value, str):
                mav = value

        value = first_key(payload, "model_context_window")
        snapshot_window = nonnegative_int(value)
        if snapshot_window is not None:
            context_window = snapshot_window
        info = token_info(obj)
        if info is not None:
            snapshot_window, snapshot_tokens = context_snapshot(info)
            if snapshot_window is not None:
                context_window = snapshot_window
            if snapshot_tokens is not None:
                context_tokens = snapshot_tokens
                context_peak_tokens = max(context_peak_tokens or 0, snapshot_tokens)

    return SessionSummary(
        path=str(path),
        session_id=session_id,
        parent_thread_id=parent,
        agent_role=role,
        task_name=task_name,
        is_child=is_child,
        model=model,
        reasoning_effort=effort,
        multi_agent_version=mav,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        turn_context_count=turn_context_count,
        json_errors=stats.json_errors,
        context_window=context_window,
        context_tokens=context_tokens,
        context_peak_tokens=context_peak_tokens,
        context_usage_pct=usage_pct(context_tokens, context_window),
        context_peak_usage_pct=usage_pct(context_peak_tokens, context_window),
    )


@dataclass
class Turn:
    timestamp: str = ""
    end_timestamp: str = ""
    thread_id: str = ""
    parent_thread_id: str | None = None
    root_thread_id: str | None = None
    root_turn_id: str | None = None
    turn_id: str = ""
    is_root: bool = True
    agent_role: str | None = None
    route: str = "UNKNOWN"
    initial_route: str = "UNKNOWN"
    final_route: str = "UNKNOWN"
    model: str = ""
    reasoning_effort: str = ""
    multi_agent_version: str = ""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    context_window: int | None = None
    context_tokens: int | None = None
    context_peak_tokens: int | None = None
    context_usage_pct: float | None = None
    context_peak_usage_pct: float | None = None
    result_chars: int = 0
    user_result_chars: int = 0
    result_estimated_tokens: int = 0
    user_result_estimated_tokens: int = 0
    status: str = "UNKNOWN"
    verification: str = "UNKNOWN"
    escalation_count: int = 0
    retry_count: int = 0
    subagent_count: int = 0
    duration_seconds: float | None = None
    wait_tool_calls: int = 0
    status_only_turn: bool = False
    wait_status_tokens: int = 0
    possible_immediate_rework: bool = False
    first_pass_success: bool = False
    protocol_route: str | None = None
    user_correction_cue: bool = False
    _tool_ids: set[str] = field(default_factory=set, repr=False)
    _tool_names: list[str] = field(default_factory=list, repr=False)
    _spawn_roles: list[str] = field(default_factory=list, repr=False)
    _status_events: list[str] = field(default_factory=list, repr=False)
    _verify_events: list[str] = field(default_factory=list, repr=False)
    _retry_values: list[int] = field(default_factory=list, repr=False)
    _result_text_seen: set[str] = field(default_factory=set, repr=False)

    def add_usage(self, usage: dict[str, int]) -> None:
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
            setattr(self, key, getattr(self, key) + usage[key])

    def finalize(self) -> None:
        # 文字数はUTF-8 tokenizerに依存しない安定した近似値として保存する。
        self.result_estimated_tokens = (self.result_chars + 3) // 4
        self.user_result_estimated_tokens = (self.user_result_chars + 3) // 4
        self.context_usage_pct = usage_pct(self.context_tokens, self.context_window)
        self.context_peak_usage_pct = usage_pct(self.context_peak_tokens, self.context_window)
        if self.agent_role in ROUTE_BY_ROLE:
            self.route = self.initial_route = self.final_route = ROUTE_BY_ROLE[self.agent_role]
        elif self.is_root:
            routes = [ROUTE_BY_ROLE[r] for r in self._spawn_roles if r in ROUTE_BY_ROLE and r != "expert"]
            base = {
                "gpt-5.6-terra": "WORKER_TERRA",
                "gpt-5.6-sol": "CONTROLLER_SOL",
                "gpt-6-astra": "CONTROLLER_ASTRA",
            }.get(self.model, "DIRECT_LUNA")
            if base == "DIRECT_LUNA" and routes:
                self.initial_route = routes[0]
                self.final_route = max(routes, key=lambda route: ROUTE_RANK.get(route, -1))
                ranks = [ROUTE_RANK.get(route, 0) for route in routes]
            else:
                self.initial_route = base
                candidates = [base] + routes
                self.final_route = max(candidates, key=lambda route: ROUTE_RANK.get(route, -1))
                ranks = [ROUTE_RANK.get(base, 0)] + [
                    ROUTE_RANK.get(route, 0)
                    for route in routes
                    if ROUTE_RANK.get(route, 0) > ROUTE_RANK.get(base, 0)
                ]
            self.route = self.final_route
            escalation = 0
            best = ranks[0] if ranks else 0
            for rank in ranks[1:]:
                if rank > best:
                    escalation += 1
                    best = rank
            self.escalation_count = max(self.escalation_count, escalation)
        if self._status_events:
            self.status = self._status_events[-1]
            self.escalation_count = max(
                self.escalation_count,
                sum(1 for status in self._status_events if status.startswith("ESCALATE_")),
            )
        elif self.end_timestamp:
            self.status = "COMPLETE"
        if self._verify_events:
            self.verification = self._verify_events[-1]
        if self._retry_values:
            self.retry_count = max(self._retry_values)
        self.subagent_count = sum(name == "spawn_agent" for name in self._tool_names)
        self.wait_tool_calls = sum(name in WAIT_TOOLS for name in self._tool_names)
        substantive = [name for name in self._tool_names if name not in WAIT_TOOLS]
        self.status_only_turn = bool(self._tool_names) and not substantive
        self.wait_status_tokens = self.total_tokens if self.status_only_turn else 0
        if self.timestamp and self.end_timestamp:
            start, end = parse_ts(self.timestamp), parse_ts(self.end_timestamp)
            if start and end:
                self.duration_seconds = max(0.0, (end - start).total_seconds())
        self.first_pass_success = (
            self.is_root
            and self.status == "COMPLETE"
            and self.escalation_count == 0
            and self.verification != "FAIL"
            and not self.possible_immediate_rework
        )

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        for key in list(data):
            if key.startswith("_") or key == "user_correction_cue":
                data.pop(key, None)
        return data

    def cache_record(self) -> dict[str, Any]:
        data = self.public()
        data["user_correction_cue"] = self.user_correction_cue
        return data


def parse_rollout_turns(path: Path) -> list[Turn]:
    thread_id = path.stem
    is_child = False
    role: str | None = None
    parent: str | None = None
    latest_model = ""
    latest_effort = ""
    latest_mav = ""
    turns: list[Turn] = []
    current: Turn | None = None
    seen_usage_signatures: set[tuple[int, int, int, int, int]] = set()
    pending_user_correction = False

    for obj in iter_jsonl(path):
        typ = obj.get("type")
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
        if typ == "session_meta":
            session_meta = payload if isinstance(payload, dict) else {}
            value = first_key(session_meta, "id") or first_key(session_meta, "thread_id")
            if isinstance(value, str):
                thread_id = value
            is_child, role, parent, _ = source_metadata(session_meta)
        elif typ == "turn_context":
            value = first_key(payload, "model")
            if isinstance(value, str):
                latest_model = value
            value = first_key(payload, "effort") or first_key(payload, "reasoning_effort")
            if isinstance(value, str):
                latest_effort = value
            value = first_key(payload, "multi_agent_version")
            if isinstance(value, str):
                latest_mav = value

        event = event_type(obj)
        user_text = user_text_from_line(obj)
        if user_text and REWORK_CUES.search(user_text):
            if current is None:
                pending_user_correction = True
            else:
                current.user_correction_cue = True

        info = token_info(obj)
        if info is not None:
            snapshot_window, snapshot_tokens = context_snapshot(info)
            if snapshot_window is not None and current is not None:
                current.context_window = snapshot_window
            if snapshot_tokens is not None and current is not None:
                current.context_tokens = snapshot_tokens
                current.context_peak_tokens = max(current.context_peak_tokens or 0, snapshot_tokens)
            signature = total_signature(info)
            is_new = signature not in seen_usage_signatures
            seen_usage_signatures.add(signature)
            if is_new and current is not None:
                current.add_usage(usage_values(info.get("last_token_usage")))

        if event in {"task_started", "turn_started"}:
            if current is not None:
                current.finalize()
                turns.append(current)
            timestamp = obj.get("timestamp") or first_key(payload, "timestamp") or ""
            turn_id = first_key(payload, "turn_id") or first_key(payload, "id") or ""
            current = Turn(
                timestamp=str(timestamp),
                thread_id=thread_id,
                parent_thread_id=parent,
                turn_id=str(turn_id),
                is_root=not is_child,
                agent_role=role,
                model=latest_model,
                reasoning_effort=latest_effort,
                multi_agent_version=latest_mav,
                user_correction_cue=pending_user_correction,
            )
            snapshot_window = nonnegative_int(first_key(payload, "model_context_window"))
            if snapshot_window is not None:
                current.context_window = snapshot_window
            pending_user_correction = False
            continue

        if current is None:
            continue

        # 最終assistant出力のサイズを計測する。tool payloadやuser入力は除外し、
        # USER_RESULT契約がある場合はその範囲を別集計する。
        assistant_text = assistant_text_from_line(obj)
        if assistant_text and assistant_text not in current._result_text_seen:
            current._result_text_seen.add(assistant_text)
            current.result_chars += len(assistant_text)
            match = USER_RESULT_RE.search(assistant_text)
            if match:
                current.user_result_chars += len(match.group("body").strip())

        if typ == "turn_context":
            current.model = latest_model or current.model
            current.reasoning_effort = latest_effort or current.reasoning_effort
            current.multi_agent_version = latest_mav or current.multi_agent_version

        for name, call_id, args in extract_tool_calls(obj):
            dedupe = (
                f"{name}:{call_id}"
                if call_id
                else f"{name}:{len(current._tool_names)}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            )
            if dedupe in current._tool_ids:
                continue
            current._tool_ids.add(dedupe)
            current._tool_names.append(name)
            if name == "spawn_agent":
                agent_role = args.get("agent_type")
                if isinstance(agent_role, str) and agent_role:
                    current._spawn_roles.append(agent_role)

        text = all_strings(obj)
        for match in PROTOCOL_STATUS_RE.finditer(text):
            current._status_events.append(match.group(1))
        for match in PROTOCOL_VERIFY_RE.finditer(text):
            current._verify_events.append(match.group(1))
        for match in PROTOCOL_RETRY_RE.finditer(text):
            current._retry_values.append(int(match.group(1)))
        match = PROTOCOL_ROUTE_RE.search(text)
        if match:
            current.protocol_route = match.group(1)

        if event in {"task_complete", "turn_complete"}:
            current.end_timestamp = str(obj.get("timestamp") or first_key(payload, "timestamp") or "")
            current.finalize()
            turns.append(current)
            current = None

    if current is not None:
        current.finalize()
        turns.append(current)
    return turns
