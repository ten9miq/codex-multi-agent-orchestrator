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
    "worker_luna": "WORKER_LUNA",
    "worker_sol": "WORKER_SOL",
    "controller_sol": "CONTROLLER_SOL",
    "expert": "EXPERT_SOL",
    "controller_astra": "CONTROLLER_ASTRA",
}
WAIT_TOOLS = {"wait_agent", "list_agents", "wait", "write_stdin"}
KNOWN_TOOLS = WAIT_TOOLS | {
    "spawn_agent",
    "followup_task",
    "send_message",
    "send_input",
    "resume_agent",
}
# 連続Root turnの関係を、次turnに入ったuser messageだけから控えめに分類する。
# ``修正`` のように対象が曖昧な語は MODEL_CORRECTION と断定しない。
MODEL_CORRECTION_CUES = re.compile(
    r"(?:違(?:う|い)|間違|誤り|やり直|期待(?:と)?違|できていない|not what|wrong|incorrect|fix (?:it|this))",
    re.IGNORECASE,
)
USER_FOLLOWUP_CUES = re.compile(
    r"(?:追加(?:で|も|して|を)?|追記|さらに|ついでに|別件|もう一つ|also\b|add\b|plus\b|one more|in addition)",
    re.IGNORECASE,
)
AMBIGUOUS_REWORK_CUES = re.compile(
    r"(?:修正|直して|直せて|まだ|再度|もう一度|そうでは|ではなく|still\b|again\b|instead\b)",
    re.IGNORECASE,
)
# 既存possible_immediate_reworkの意味を変えないための旧pattern。
LEGACY_REWORK_CUES = re.compile(
    r"(?:違う|間違|修正|直して|直せて|まだ|再度|やり直|そうでは|ではなく|not what|wrong|fix it|still|again|instead)",
    re.IGNORECASE,
)
REWORK_CLASSES = {"NONE", "USER_FOLLOWUP", "MODEL_CORRECTION", "UNKNOWN"}
PROTOCOL_STATUS_RE = re.compile(r"ROUTER_STATUS:\s*(COMPLETE|ESCALATE_SOL|ESCALATE_ASTRA|BLOCKED)")
PROTOCOL_ROUTE_RE = re.compile(r"ROUTER_ROUTE:\s*([A-Z0-9_]+)")
PROTOCOL_VERIFY_RE = re.compile(r"ROUTER_VERIFY:\s*(PASS|FAIL|NOT_RUN)")
PROTOCOL_RETRY_RE = re.compile(r"ROUTER_RETRY:\s*(\d+)")
USER_RESULT_RE = re.compile(
    r"USER_RESULT_BEGIN\s*(?P<body>.*?)\s*USER_RESULT_END",
    re.IGNORECASE | re.DOTALL,
)


def classify_user_rework_cue(text: str) -> str:
    """次Root turnを始めるuser messageの関係を、明示cueだけで分類する。"""
    if MODEL_CORRECTION_CUES.search(text):
        return "MODEL_CORRECTION"
    if USER_FOLLOWUP_CUES.search(text):
        return "USER_FOLLOWUP"
    if AMBIGUOUS_REWORK_CUES.search(text):
        return "UNKNOWN"
    return "NONE"


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


def is_wait_status_call(name: str, args: dict[str, Any]) -> bool:
    """待機・状態確認だけの call を、旧新JSONLに共通する引数で判定する。"""
    if name in {"wait_agent", "list_agents", "wait"}:
        return True
    # write_stdinは空入力ならprocess待機だが、文字送信は実作業にもなり得る。
    return name == "write_stdin" and not bool(args.get("chars"))


def user_text_from_line(obj: dict[str, Any]) -> str:
    for node in walk(obj):
        if not isinstance(node, dict) or node.get("role") != "user":
            continue
        text = all_strings(node)
        if text:
            return text
    return ""


def assistant_text_from_line(obj: dict[str, Any]) -> str:
    """1行中のassistant message本文だけを返す。

    rollout全体の文字列を再帰走査すると、tool引数・developer prompt・引用された
    protocolまでassistant出力として誤認する。ここではassistant messageの
    ``content``（または旧schemaの``text``）だけを読む。
    """
    for node in walk(obj):
        if not isinstance(node, dict) or node.get("role") != "assistant":
            continue
        content = node.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                item.get("text")
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            text = "\n".join(parts)
            if text:
                return text
        text = node.get("text")
        if not isinstance(text, str):
            continue
        if text:
            return text
    return ""


def task_complete_message(payload: dict[str, Any]) -> str:
    """task_completeが保持する最終agent出力を返す（旧JSONLでは空）。"""
    text = payload.get("last_agent_message")
    return text if isinstance(text, str) else ""


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
    # rollout内で最初に観測した既知のnamed spawn role。選択Routeの推定には使わない。
    first_spawn_role: str | None = None
    # final_route の後方互換 alias。レポートでは「実効 Route」として表示する。
    effective_route: str = "UNKNOWN"
    model: str = ""
    reasoning_effort: str = ""
    multi_agent_version: str = ""
    execution_mode: str = "LEGACY_ROOT_MODEL"
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
    compaction_count: int = 0
    compaction_context_tokens: list[int] = field(default_factory=list)
    compaction_context_windows: list[int] = field(default_factory=list)
    result_chars: int = 0
    user_result_chars: int = 0
    result_estimated_tokens: int = 0
    user_result_estimated_tokens: int = 0
    status: str = "UNKNOWN"
    verification: str = "UNKNOWN"
    # protocol由来の値は、明示的な最終出力を読めた場合だけsourceを保存する。
    # Route推定（agent_role/model/spawn_agent）も区別できるようにする。
    status_source: str | None = None
    verification_source: str | None = None
    route_source: str | None = None
    initial_route_source: str | None = None
    final_route_source: str | None = None
    retry_source: str | None = None
    escalation_count: int = 0
    retry_count: int = 0
    subagent_count: int = 0
    duration_seconds: float | None = None
    wait_tool_calls: int = 0
    status_only_turn: bool = False
    wait_status_tokens: int = 0
    possible_immediate_rework: bool = False
    # 前turnからの関係。collect.pyが同一Root thread内の次turnのcueを転記する。
    rework_class: str = "NONE"
    first_pass_success: bool = False
    protocol_route: str | None = None
    # 次turnのuser messageから抽出した内部用cue。raw textは保存しない。
    user_rework_class: str = "NONE"
    user_legacy_rework_cue: bool = False
    _tool_ids: set[str] = field(default_factory=set, repr=False)
    _tool_names: list[str] = field(default_factory=list, repr=False)
    _tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list, repr=False)
    _spawn_roles: list[str] = field(default_factory=list, repr=False)
    _last_assistant_message: tuple[str, str] | None = field(default=None, repr=False)
    _completion_message: tuple[str, str] | None = field(default=None, repr=False)
    _protocol_applied: bool = field(default=False, repr=False)
    _result_text_seen: set[str] = field(default_factory=set, repr=False)

    def add_usage(self, usage: dict[str, int]) -> None:
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
            setattr(self, key, getattr(self, key) + usage[key])

    def set_last_assistant_message(self, text: str) -> None:
        """最後のassistant messageを、protocol候補として保持する。"""
        if text:
            self._last_assistant_message = (text, "assistant_message")

    def set_completion_message(self, text: str) -> None:
        """task_completeに記録された最終agent出力を優先候補として保持する。"""
        if text:
            self._completion_message = (text, "task_complete.last_agent_message")

    def apply_final_protocol(self) -> None:
        """終了時の明示出力だけからROUTER protocolを取り込む。

        tool argumentsやprompt内のprotocol文字列はこの経路に入らない。completion
        eventのlast_agent_messageがある場合は、直前assistant messageより優先する。
        """
        if self._protocol_applied:
            return
        self._protocol_applied = True
        candidate = self._completion_message or self._last_assistant_message
        if candidate is None:
            return
        text, source = candidate
        statuses = PROTOCOL_STATUS_RE.findall(text)
        verifications = PROTOCOL_VERIFY_RE.findall(text)
        retries = PROTOCOL_RETRY_RE.findall(text)
        routes = PROTOCOL_ROUTE_RE.findall(text)
        if statuses:
            self.status = statuses[-1]
            self.status_source = source
            self.escalation_count = max(
                self.escalation_count,
                sum(1 for status in statuses if status.startswith("ESCALATE_")),
            )
        if verifications:
            self.verification = verifications[-1]
            self.verification_source = source
        if retries:
            self.retry_count = max(int(value) for value in retries)
            self.retry_source = source
        if routes:
            self.protocol_route = routes[-1]
            self.route_source = source

    def finalize(self) -> None:
        self.first_spawn_role = next(
            (role for role in self._spawn_roles if role in ROUTE_BY_ROLE), None
        )
        # 文字数はUTF-8 tokenizerに依存しない安定した近似値として保存する。
        self.result_estimated_tokens = (self.result_chars + 3) // 4
        self.user_result_estimated_tokens = (self.user_result_chars + 3) // 4
        self.context_usage_pct = usage_pct(self.context_tokens, self.context_window)
        self.context_peak_usage_pct = usage_pct(self.context_peak_tokens, self.context_window)
        if self.agent_role in ROUTE_BY_ROLE:
            self.route = self.initial_route = self.final_route = ROUTE_BY_ROLE[self.agent_role]
            self.route_source = self.initial_route_source = self.final_route_source = "agent_role"
        elif self.is_root:
            # Rootのモデルやspawnした子のroleはRoot自身の役割を証明しない。
            # 明示protocolがなければUNKNOWNを維持する。
            self.route = self.initial_route = self.final_route = "UNKNOWN"
        self.apply_final_protocol()
        # 明示protocolがなければ、終了時刻を伴う完了eventを通常完了として扱う。
        # verificationは補完しないため、protocol由来の観測範囲は維持される。
        if self.status == "UNKNOWN" and self.end_timestamp:
            self.status = "COMPLETE"
            self.status_source = "completion_event"
        if self.protocol_route:
            # 終了時の明示Routeをfinalに適用する。初期Routeは別に観測
            # できていなければUNKNOWNのまま残す。
            self.route = self.final_route = self.protocol_route
            self.final_route_source = self.route_source
        if self.agent_role in ROUTE_BY_ROLE or self.multi_agent_version or self.protocol_route:
            self.execution_mode = "ORCHESTRATED_ROUTE"
        self.effective_route = self.final_route
        self.subagent_count = sum(name == "spawn_agent" for name in self._tool_names)
        self.wait_tool_calls = sum(
            is_wait_status_call(name, args) for name, args in self._tool_calls
        )
        # completion_eventだけではagentの最終返却内容を観測したことにならない。
        # 待機専用turnの既存分類は、明示protocolまたはUSER_RESULTだけで解除する。
        has_final_result = bool(
            self.user_result_chars
            or self.status_source in {"assistant_message", "task_complete.last_agent_message"}
        )
        self.status_only_turn = (
            bool(self._tool_names)
            and all(is_wait_status_call(name, args) for name, args in self._tool_calls)
            and not has_final_result
        )
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
            if key.startswith("_") or key in {"user_rework_class", "user_legacy_rework_cue"}:
                data.pop(key, None)
        return data

    def cache_record(self) -> dict[str, Any]:
        data = self.public()
        data["user_rework_class"] = self.user_rework_class
        data["user_legacy_rework_cue"] = self.user_legacy_rework_cue
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
    pending_user_rework_class = "NONE"
    pending_user_legacy_rework_cue = False

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
        if user_text:
            cue_class = classify_user_rework_cue(user_text)
            legacy_cue = bool(LEGACY_REWORK_CUES.search(user_text))
            if current is None:
                pending_user_rework_class = cue_class
                pending_user_legacy_rework_cue = pending_user_legacy_rework_cue or legacy_cue
            elif cue_class != "NONE":
                current.user_rework_class = cue_class
                current.user_legacy_rework_cue = current.user_legacy_rework_cue or legacy_cue

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
                user_rework_class=pending_user_rework_class,
                user_legacy_rework_cue=pending_user_legacy_rework_cue,
            )
            snapshot_window = nonnegative_int(first_key(payload, "model_context_window"))
            if snapshot_window is not None:
                current.context_window = snapshot_window
            pending_user_rework_class = "NONE"
            pending_user_legacy_rework_cue = False
            continue

        if current is None:
            continue

        # ``compacted`` はContextCompactionの完了を示す安定したrollout event。
        # この直前までに観測できたlast_token_usageを発生時contextとして保存する。
        # 古いrolloutにこのeventがなければ、回数は0・contextは空のままとする。
        if event == "compacted":
            current.compaction_count += 1
            if current.context_tokens is not None:
                current.compaction_context_tokens.append(current.context_tokens)
            if current.context_window is not None:
                current.compaction_context_windows.append(current.context_window)

        # 最終assistant出力のサイズを計測する。tool payloadやuser入力は除外し、
        # USER_RESULT契約がある場合はその範囲を別集計する。
        assistant_text = assistant_text_from_line(obj)
        if assistant_text and assistant_text not in current._result_text_seen:
            current._result_text_seen.add(assistant_text)
            current.result_chars += len(assistant_text)
            current.set_last_assistant_message(assistant_text)
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
            current._tool_calls.append((name, args))
            if name == "spawn_agent":
                agent_role = args.get("agent_type")
                if isinstance(agent_role, str) and agent_role:
                    current._spawn_roles.append(agent_role)

        if event in {"task_complete", "turn_complete"}:
            # 新schemaのtask_completeは、assistant messageの複製を
            # last_agent_messageとして保持する。存在すればこれを最終出力とする。
            current.set_completion_message(task_complete_message(payload))
            current.end_timestamp = str(obj.get("timestamp") or first_key(payload, "timestamp") or "")
            current.finalize()
            turns.append(current)
            current = None

    if current is not None:
        current.finalize()
        turns.append(current)
    return turns
