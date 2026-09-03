# GFL2 Packet CSV Exporter 1.5.0

소녀전선2 로그인 중 **서버 → 클라이언트로 수신되는 패킷 복사본**에서 사용자 보유 데이터를 읽어 CSV/ZIP 백업으로 저장하는 독립형 도구입니다.

> **사용 전 확인**  
> 이 프로그램은 네트워크 패킷을 캡처해 CSV 데이터를 생성합니다. 사용으로 발생하는 모든 문제와 책임은 사용자에게 있습니다. 동의하지 않으면 실행하지 마세요.

## 사용 흐름

1. `start_packet_csv_exporter.bat` 또는 배포 EXE를 실행합니다.
2. `캡처 시작`을 누릅니다.
3. `windows-packet-capture.exe`에 대한 Windows 관리자 권한 요청을 승인합니다.
4. 화면에 **“캡처 준비 완료 · 이제 소녀전선2에 로그인하세요.”**가 표시된 뒤 게임에 로그인합니다.
5. 필수 데이터 7종이 모두 정상 디코드되고 약 3초간 추가 데이터가 없으면 자동으로 저장합니다.
6. 완료 안내 후 소녀전선2와 CSV Exporter를 종료합니다.

60초 안에 7종을 모두 얻지 못하면 미완료로 종료하며 게임은 종료하지 않습니다.

## 수집 대상 7종

| Payload type | 데이터 |
| ---: | --- |
| 11021 | 무기 |
| 11061 | 부착물 |
| 11101 | 인형 |
| 11138 | 공용키 |
| 11163 | 리몰딩 |
| 11211 | 재화/재료 |
| 23201 | 제대 |

필수 타입은 **수신만으로 완료 처리하지 않고 semantic decode가 성공한 경우에만** 완료 카운터에 반영합니다.

## 출력 파일

프로젝트 루트 또는 실행 EXE 옆에는 정상 완료본만 생성됩니다.

```text
gfl2_user_csv_bundle.zip
```

세부 파일과 진단 자료는 `gfl2_packet_backup`에 생성됩니다.

```text
gfl2_packet_backup/
  dolls.csv
  remoldings.csv
  formations.csv
  equipment_dolls.csv
  weapons.csv
  attachments.csv
  common_keys.csv
  items.csv
  equipment_user.json
  manifest.json
  gfl2_packet_debug_raw.zip
```

미완료/수동 취소 시에는 정상 완료본을 덮어쓰지 않고 다음 파일로 분리합니다.

```text
gfl2_packet_backup/gfl2_user_csv_partial.zip
```

## 개인정보 보호형 디버그 ZIP

파일명 `gfl2_packet_debug_raw.zip`은 **패킷 본문을 저장하지 않습니다.**

ZIP에는 다음과 같은 진단용 메타데이터만 포함합니다.

- packet/payload 길이
- flow 번호(실제 IP/포트 제외)
- flow 시작점 기준 상대 TCP sequence
- payload type
- protobuf field number/wire type 개수와 길이 구간
- framing/resync 통계
- 캡처/디코드 카운터
- archive 내부 중복 판별용 임시 keyed content tag

저장하지 않는 항목:

- TCP payload 원문
- 조립된 game payload 원문
- `.bin` raw payload 파일
- 로컬/원격 IP 주소와 포트
- 패킷 본문에 포함될 수 있는 로그인 정보, 토큰, 쿠키, 자격 증명

content tag는 실행 중 임시 랜덤 키로 만든 HMAC이며 키는 ZIP에 저장하지 않으므로 다른 archive에서 재계산하거나 상호 추적할 수 없습니다.

## 패킷 안전 계약

캡처 backend는 다음 조건으로 고정되어 있습니다.

```text
filter = inbound and tcp and tcp.PayloadLength > 0
flags  = SNIFF | RECV_ONLY
```

- `inbound`: 서버 → 클라이언트 방향만 관찰
- `SNIFF`: 원본 패킷을 정상 네트워크 경로에 그대로 두고 복사본을 수신
- `RECV_ONLY`: 해당 WinDivert handle에서 송신/재주입 비활성화
- 캡처 코드에 `.send()` 또는 `WinDivertSend` 호출 없음
- 게임 종료는 캡처 handle 종료와 CSV 저장이 끝난 뒤 별도 프로세스 API로 수행

## 관리자 권한 구조

메인 GUI는 일반 권한으로 유지됩니다. 실제 WinDivert 캡처만 별도 helper가 담당합니다.

```text
GFL2 Packet CSV Exporter (일반 권한)
        ↓ 캡처 시작
windows-packet-capture.exe (관리자 권한)
        ↓
패시브 캡처 / 디코드 / 저장
```

소스/BAT 실행 시 `windows-packet-capture.exe`가 없거나 버전이 바뀌면 PyInstaller로 자동 준비합니다. 정식 EXE 배포본은 `build_exe.bat`으로 메인 EXE와 helper EXE를 함께 만들 수 있습니다.

## 실행

```text
start_packet_csv_exporter.bat
```

## Windows EXE 빌드

```text
build_exe.bat
```

생성 결과:

```text
dist/GFL2-Packet-CSV-Exporter/
  GFL2-Packet-CSV-Exporter.exe
  windows-packet-capture.exe
```
