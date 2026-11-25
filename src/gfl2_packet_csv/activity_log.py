"""User-visible capture summary, not packet-debug collection.

Only lines already displayed in the Exporter activity pane are eligible.
Only approved status messages and bounded counters are retained before a
bounded UTF-8 text file is placed alongside the normal CSV ZIP backup.
No raw payload, secret, or packet buffer is read by this module.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

# Persist a deliberately small event vocabulary, not sanitized arbitrary text.
# A regex-based redactor cannot reliably recognize names, IPv6, split secrets,
# Unicode paths, or arbitrary account identifiers. Unknown bodies are dropped.
from .account_export_scope import ACCOUNT_LABELS

_MAX_LINES = 1500
_MAX_LINE_LEN = 350
_LABEL = "(?:" + "|".join(re.escape(x) for x in ACCOUNT_LABELS.values()) + ")"
_COUNT = r"(?:0|[1-9][0-9]{0,6}|[1-9][0-9]{0,2}(?:,[0-9]{3}){1,2})"
_FIXED = frozenset({
    "[설정] 디버그 데이터: 생성", "[설정] 디버그 데이터: 생성 안 함",
    "[설정] 선택적 디버그 정보: 저장", "[설정] 선택적 디버그 정보: 저장 안 함",
    "[준비] 관리자 캡처 도구 실행 및 게임 연결 확인 중",
    "[준비] 관리자 캡처 도구 실행 확인 · 네트워크 수집 준비 중",
    "[준비] 게임 연결 및 계정 데이터 12종 수집 대기",
    "[캡처] Windows 관리자 권한 요청이 취소되었습니다.",
    "[캡처] 중지 요청", "[캡처] 시작 응답 제한시간 초과",
    "[캡처] 준비 완료 · 게임에 로그인하세요.", "[캡처] 사용자가 캡처를 취소했습니다.",
    "[연결] 게임 TCP 연결 감지 · 계정 동기화 수신 중", "[연결] 게임 TCP 연결 대기 중",
    "[디버그] 진단 ZIP 저장 완료", "[정리] 의상 목록 미수신 · 현재 착장 정보는 별도 보존",
    "[확인] 지원소대 장착 소자 참조 검증 실패", "[정리] 선택 데이터 변환 실패 · 기본 백업 유지",
    "[저장] CSV ZIP 백업 생성 완료", "[종료] 소녀전선2를 종료합니다.",
    "[종료] 소녀전선2 종료 완료", "[종료] 데이터 저장 완료 · 게임 종료하지 않음",
    "[종료] CSV 저장 완료 · 게임 종료 결과 확인 필요",
    "[종료] 완료 응답 시간 초과 · CSV 백업은 저장됨 · 게임 종료 미확인",
    "[종료] 캡처 도구 종료", "[종료] 프로그램 창 닫기",
    "[IPC 오류] 캡처 도구 종료 확인 실패",
    "소녀전선2 실행 확인 · 서버 연결 대기 중", "소녀전선2 실행 대기 중",
    "캡처를 종료했습니다.", "Exporter 종료 · 캡처 중지 요청", "사용자가 캡처 중지를 요청했습니다.",
    "필수 데이터 수집 완료 · 제한시간 종료 · 백업 검증 및 저장 중…",
    "계정 데이터 12종 수신 완료 · 백업 검증 및 저장 중…",
    "로그인 캡처 제한시간이 끝났지만 필수 데이터가 모두 수신되지 않았습니다. 캡처를 종료합니다.",
})
_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"\[시작\] CSV Exporter v[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3} · 데이터 백업 시작",
    r"\[참조 데이터\] (?:갱신 완료|최신|로컬|캐시|내장|갱신 불가 · 기존 데이터 사용|확인 완료) · [0-9a-f]{12}",
    rf"\[수집\] {_LABEL} 데이터 수신 · (?:[0-9]|1[0-2])/12종",
    rf"\[정리\] 인형 {_COUNT}건, 무기 {_COUNT}건, 파츠 {_COUNT}건, 공용키 {_COUNT}건, 리몰딩 {_COUNT}건, 아이템 {_COUNT}건",
    r"\[저장\] CSV [0-9]{1,3}종 및 manifest 생성",
    r"\[정리\] 계정 데이터 해독 (?:[0-9]|1[0-2])/12종",
    r"\[확인\] 추가 계정 데이터 (?:[0-9]|1[0-2])종 미수신",
    r"\[저장\] (?:능력치 상태|장비 상태) CSV",
    r"\[확인\] (?:능력치 상태|장비 상태) CSV 저장 실패 · 기본 ZIP 유지",
    rf"\[미완료\] 미수신 또는 해독 실패: {_LABEL}(?:, {_LABEL}){{0,11}}",
    r"소녀전선2 연결 확인 · TCP [0-9]{1,5}개",
))
_GENERIC = {
    "오류": "[오류] 세부 내용 생략", "IPC 오류": "[IPC 오류] 세부 내용 생략",
    "캡처 오류": "[캡처 오류] 세부 내용 생략",
}


def safe_activity_text(text: str) -> str:
    """Keep known status messages and bounded counts; never persist free text."""
    if not isinstance(text, str):
        raise TypeError('activity text must be str')
    clean: list[str] = []
    for raw in text.splitlines()[-_MAX_LINES:]:
        line = raw.strip()
        if len(line) > _MAX_LINE_LEN:
            continue
        if line in _FIXED or any(p.fullmatch(line) for p in _PATTERNS):
            clean.append(line)
            continue
        match = re.match(r'^\[([^]]+)\]', line)
        if match and match.group(1) in _GENERIC:
            clean.append(_GENERIC[match.group(1)])
        elif line.startswith("[종료] 게임 프로세스 종료 중 일부 오류:"):
            clean.append("[종료] 게임 프로세스 종료 중 일부 오류")
        elif line.startswith("[디버그] 진단 ZIP 생성 실패:"):
            clean.append("[디버그] 진단 ZIP 생성 실패")
    return '\n'.join(clean) + '\n'


def save_activity_log(
    output_dir: Path, ui_text: str, *, reason: str = 'complete',
    now: datetime | None = None,
) -> Path:
    """Create a new timestamp-named log next to the CSV ZIP; never overwrite.

    Called by the unelevated GUI, separately from opt-in debug recording.
    """
    if reason not in ('complete', 'incomplete', 'cancelled', 'error', 'closed'):
        raise ValueError('invalid capture outcome')
    stamp = now if now is not None else datetime.now().astimezone()
    filename = f'GFL2-Exporter-Log_{stamp:%Y%m%d_%H%M%S}'
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    content = (
        f'GFL2 Packet CSV Exporter 작업 기록\n'
        f'기록 시각: {stamp:%Y-%m-%d %H:%M:%S}\n'
        f'결과: {reason}\n'
        f'※ 상태와 집계 수치만 기록합니다. 계정 보유 규모가 드러날 수 있으니 공개 공유에 주의하세요.\n\n'
        + safe_activity_text(ui_text)
    )
    for sequence in range(1000):
        suffix = f'_{sequence:03d}' if sequence else ''
        target = folder / (filename + suffix + '.txt')
        try:
            with target.open('x', encoding='utf-8-sig', newline='\n') as stream:
                stream.write(content)
            return target
        except FileExistsError:
            continue
    raise FileExistsError('too many activity logs with the same timestamp')
