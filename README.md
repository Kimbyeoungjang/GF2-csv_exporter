# GFL2 Packet CSV Exporter

**GFL2 Packet CSV Exporter**는 소녀전선2 실행 중 **서버에서 클라이언트로 수신되는 로그인 계정 데이터의 패킷 복사본**을 읽어, 보유 인형·무기·부착물·공용키·리몰딩·재화/재료·제대 정보를 CSV/JSON/ZIP 백업으로 정리하는 Windows용 독립 도구입니다.

이 프로그램은 게임 서버로 패킷을 보내거나 수정하기 위한 도구가 아닙니다. 기본 GUI 워크플로에서는 `windows-packet-capture.exe`가 WinDivert를 이용해 **inbound + SNIFF + RECV_ONLY** 방식으로 수신 패킷의 복사본만 관찰하고, 필요한 로그인 데이터 7종이 정상 디코드되면 결과를 저장한 뒤 종료합니다.

> **사용 전 확인**
>
> 이 프로그램은 네트워크 패킷을 캡처해 CSV 데이터를 생성합니다. 프로그램 사용으로 발생하는 모든 문제와 책임은 사용자에게 있습니다. 동의하지 않으면 실행하지 마세요.
>
> 게임의 이용약관, 운영정책, 지역별 법률 및 네트워크 사용 정책은 사용자가 직접 확인해야 합니다.

---

## 1. 주요 기능

- 소녀전선2 로그인 구간의 서버 → 클라이언트 TCP 패킷을 패시브 캡처
- 소녀전선2 프로세스가 사용하는 TCP 연결만 4-tuple 기준으로 선별
- TCP 분할·재전송을 고려한 수신 스트림 재조립
- GF2 메시지 framing 및 payload 조립
- 필수 계정 데이터 7종 자동 판별 및 semantic decode
- CSV 8종 + JSON sidecar + manifest 생성
- 정상 완료본을 `gfl2_user_csv_bundle.zip`으로 자동 패키징
- 미완료/수동 취소 결과를 정상 완료본과 분리하여 저장
- 개인정보 보호형 진단 ZIP 생성
- 메인 GUI는 일반 권한 유지, 캡처 helper에만 관리자 권한 요청
- 7/7 정상 완료 후 사용자 안내를 거쳐 소녀전선2와 Exporter 종료

---

## 2. 지원 환경

### 운영체제

- Windows 10/11 64-bit 권장
- 실시간 캡처 기능은 Windows 전용

### 소스/BAT 실행 시 Python

`pyproject.toml` 기준:

```text
Python >= 3.11, < 3.14
```

런처는 다음 순서로 Python을 찾습니다.

```text
Python 3.13 → 3.12 → 3.11 → 기본 python
```

### 주요 의존성

- PySide6-Essentials: GUI
- psutil: 소녀전선2 프로세스와 TCP 연결 확인
- PyDivert / WinDivert: Windows 패킷 캡처
- PyInstaller: 소스 배포본에서 관리자 캡처 helper 생성 및 EXE 빌드

소스/BAT 방식의 최초 실행은 Python 패키지를 설치하기 위해 인터넷 연결이 필요할 수 있습니다. 미리 빌드된 정식 EXE 배포본은 일반 사용 시 Python 설치를 요구하지 않도록 배포하는 것을 권장합니다.

---

## 3. 가장 간단한 사용법

### 일반 GUI 사용

1. `GFL2-Packet-CSV-Exporter.exe` 또는 `start_packet_csv_exporter.bat`을 실행합니다.
2. 시작 경고문을 읽고 동의하는 경우 **동의하고 실행**을 누릅니다.
3. **캡처 시작**을 누릅니다.
4. Windows가 `windows-packet-capture.exe`에 대해 표시하는 관리자 권한 요청을 승인합니다.
5. 화면에 다음 문구가 표시될 때까지 기다립니다.

```text
캡처 준비 완료 · 이제 소녀전선2에 로그인하세요.
```

6. 위 문구가 표시된 **후에** 소녀전선2에 로그인합니다.
7. 필수 데이터가 수집되는 동안 상태가 `0/7` → `7/7` 형태로 표시됩니다.
8. 7종이 모두 정상 디코드되고 마지막 필수 데이터 이후 약 3초간 추가 수신이 없으면 자동 저장됩니다.
9. 완료 안내창을 확인하면 소녀전선2가 종료되고 CSV Exporter도 종료됩니다.

### 중요한 실행 순서

게임 로그인보다 **캡처 준비 완료가 먼저**여야 합니다. 로그인 초기에 한 번만 수신되는 payload가 있으므로, 게임에 먼저 로그인한 뒤 캡처를 시작하면 필수 데이터가 누락될 수 있습니다.

---

## 4. 자동 완료 조건

정상 GUI 워크플로의 필수 payload는 다음 7종입니다.

| Payload type | 데이터 | 주요 출력 |
| ---: | --- | --- |
| `11021` | 무기 | `weapons.csv` |
| `11061` | 부착물 | `attachments.csv` |
| `11101` | 보유 인형 및 장착 정보 | `dolls.csv`, `equipment_dolls.csv` |
| `11138` | 공용키 | `common_keys.csv` |
| `11163` | 리몰딩 | `remoldings.csv` |
| `11211` | 재화/재료 | `items.csv` |
| `23201` | 제대 | `formations.csv` |

### 7/7 판정 원칙

필수 payload type을 네트워크에서 단순히 발견했다고 완료 처리하지 않습니다.

**해당 payload의 semantic decode가 실제로 성공한 경우에만** 완료 카운터에 반영합니다.

따라서 서버 스키마가 변경되어 payload는 수신되지만 decoder가 실패하는 상황을 정상 완료로 잘못 처리하지 않습니다.

### 시간 제한

- 캡처 준비 완료 시점부터 로그인 동기화 제한시간: **60초**
- 7/7 완료 후 quiet window: **약 3초**

60초 안에 7종을 모두 얻지 못하면 미완료로 종료합니다. 이 경우 소녀전선2는 자동 종료하지 않습니다.

---

## 5. 출력 위치

### 정상 완료본

프로젝트 루트 또는 실행 EXE가 있는 폴더에 다음 파일이 생성됩니다.

```text
gfl2_user_csv_bundle.zip
```

이 파일은 **7/7 정상 완료 시에만 갱신**됩니다.

### 작업/진단 폴더

```text
gfl2_packet_backup/
├─ dolls.csv
├─ remoldings.csv
├─ formations.csv
├─ equipment_dolls.csv
├─ weapons.csv
├─ attachments.csv
├─ common_keys.csv
├─ items.csv
├─ equipment_user.json
├─ manifest.json
├─ gfl2_packet_debug_raw.zip
└─ gfl2_user_csv_partial.zip     # 미완료/취소 시에만 생성될 수 있음
```

### 정상 완료 ZIP에 포함되는 파일

```text
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
```

CSV 파일은 Excel 등에서 한글을 쉽게 읽을 수 있도록 UTF-8 BOM 형식으로 기록됩니다.

---

# 6. CSV / JSON 스키마

아래 설명은 **현재 Exporter가 실제로 생성하는 스키마**를 기준으로 합니다. 서버 스키마가 변경되면 일부 필드의 의미나 decode 규칙이 달라질 수 있습니다.

## 6.1 `dolls.csv`

보유 인형의 기본 상태입니다.

| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| `doll_id` | integer | 인형 ID. 파일 내 기본키 역할 |
| `level` | integer | 서버에서 읽은 인형 레벨 값 |
| `rank` | integer | 서버에서 읽은 rank 값 |

### 주의

- 이름은 포함하지 않습니다. ID → 이름 매핑은 별도의 게임 데이터 테이블이 필요합니다.
- `doll_id`는 파일 안에서 중복되지 않도록 저장됩니다.

---

## 6.2 `equipment_dolls.csv`

인형별 장착 무기와 키 정보를 정리합니다.

| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| `doll_id` | integer | `dolls.csv.doll_id`와 연결되는 인형 ID |
| `weapon_uid` | integer | 장착 무기의 사용자 인스턴스 UID. 미장착이면 `0`일 수 있음 |
| `fixed_key_ids` | space-separated integers | 고정/고유 키 ID 목록 |
| `common_key_ids` | space-separated integers | 장착 공용키의 key ID 목록 |
| `common_key_uids` | space-separated integers | 장착 공용키의 사용자 인스턴스 UID 목록 |
| `expansion_key_ids` | space-separated integers | 확장키 ID 목록 |

목록형 컬럼은 CSV 호환성을 위해 공백으로 구분합니다.

예:

```text
200903 200905 200906
```

`common_key_uids`는 가능한 경우 `common_keys.csv.uid`와 연결됩니다.

---

## 6.3 `weapons.csv`

보유 무기 인스턴스를 저장합니다.

| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| `uid` | integer | 사용자 보유 무기의 인스턴스 UID. 기본키 역할 |
| `item_id` | integer | Exporter가 주 ID로 판별한 무기 item ID |
| `item_id_candidates` | space-separated integers | payload의 숫자 필드에서 추출한 후보 ID. `item_id`가 있으면 첫 후보로 배치 |
| `level` | integer | 서버에서 읽은 레벨 값 |
| `rank` | integer | 서버에서 읽은 rank 값 |
| `equipped_doll_id` | integer | 서버의 장착 관계에서 읽은 인형 ID. 미장착이면 `0` |

### `item_id_candidates` 주의

이 컬럼은 스키마가 완전히 명명되지 않은 일부 숫자 필드에 대비하기 위한 **보조 후보 정보**입니다. 일반적인 연동에서는 `item_id`를 우선 사용하세요.

### `equipped_doll_id` 주의

이 값은 서버 장착 관계를 보존한 값이며, 항상 현재 `dolls.csv`에 존재하는 ID라고 가정해서는 안 됩니다. 대여/지원/일시적 서버 상태/스키마 의미 차이 등으로 보유 인형 목록 밖의 ID가 나타날 가능성에 대비해 downstream 프로그램에서는 nullable/optional foreign key처럼 처리하는 것을 권장합니다.

---

## 6.4 `attachments.csv`

부착물 인스턴스를 저장합니다.

| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| `uid` | integer | 부착물 인스턴스 UID |
| `part_id` | integer | 부착물 종류 ID |
| `packed_stats` | integer | 서버의 패킹된 스탯 값 |
| `level_raw` | integer | 서버 원시 레벨 관련 값 |
| `effect_id` | integer | 효과 ID |
| `state_raw` | integer | 서버 원시 상태 값 |
| `packed_calibration` | integer | 서버의 패킹된 교정/캘리브레이션 값 |
| `weapon_uid` | integer | 장착 무기 UID. 미장착은 `0` |

### 주의

`packed_stats`, `level_raw`, `state_raw`, `packed_calibration`은 이름 그대로 **raw/packed 값**입니다. UI에 표시되는 최종 스탯과 동일하다고 단정하지 마세요. 상세 의미를 사용하려면 별도의 게임 테이블 및 해석 규칙이 필요합니다.

---

## 6.5 `common_keys.csv`

보유 공용키 인스턴스입니다.

| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| `uid` | integer | 사용자가 보유한 공용키 인스턴스 UID |
| `key_id` | integer | 공용키 종류 ID |

`equipment_dolls.csv.common_key_uids`와 연결할 수 있습니다.

---

## 6.6 `remoldings.csv`

보유 리몰딩 인스턴스와 서버 원시 옵션 바이트를 호환 가능한 문자열 형태로 저장합니다.

| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| `uid` | string | `U<숫자 UID>` 형식의 리몰딩 인스턴스 ID |
| `stat1` | hex bytes | 첫 번째 옵션 원시 바이트 3개 |
| `stat2` | hex bytes | 두 번째 옵션 원시 바이트 3개 |
| `stat3` | hex bytes | 세 번째 옵션 원시 바이트 3개. 없으면 빈 문자열 |

예:

```text
uid,stat1,stat2,stat3
U1842538220747771905,cf ae 4f,ba b5 8c,c5 aa 86
```

현재 Exporter는 payload 내부 `contents`에서 `0x01` 바이트를 제외한 뒤 3바이트씩 나누어 기록합니다. 이 파일 자체는 옵션 이름/등급/수치의 최종 의미를 해석하지 않습니다.

---

## 6.7 `items.csv`

재화/재료 보유량입니다.

| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| `item_id` | integer | 아이템 ID |
| `quantity` | integer | 보유 수량 |

아이템 이름은 포함하지 않습니다. 별도 게임 데이터 테이블로 매핑해야 합니다.

---

## 6.8 `formations.csv`

저장된 제대를 **제대 1개당 여러 행**으로 펼쳐 저장합니다.

| 컬럼 | 형식 | 설명 |
| --- | --- | --- |
| `formation_name` | string | 서버에 저장된 제대 이름 |
| `position` | integer | 해당 제대 안의 1-based 순서 |
| `doll_id` | integer/string | 배치 인형 ID |
| `doll_name` | string | 호환성용 예약 컬럼. 현재 Exporter에서는 비어 있음 |

예를 들어 제대 12개가 있어도 각 제대 구성원을 행으로 펼치므로 `formations.csv`의 실제 행 수는 12보다 많을 수 있습니다.

빈 제대가 존재하는 경우 호환성을 위해 `position=0`, 빈 `doll_id` 행이 만들어질 수 있습니다.

---

## 6.9 `equipment_user.json`

CSV 소비자가 인형/무기 장착 정보를 더 쉽게 읽을 수 있도록 만든 sidecar JSON입니다.

대략적인 구조:

```json
{
  "schema": 1,
  "dolls": {
    "1001": {
      "weapon_uid": 50,
      "fixed_key_ids": [],
      "common_key_ids": [],
      "common_key_uids": [],
      "expansion_key_ids": []
    }
  },
  "weapons_by_uid": {
    "50": {
      "uid": 50,
      "item_id": 0,
      "item_id_candidates": [],
      "level": 0,
      "rank": 0,
      "equipped_doll_id": 1001
    }
  }
}
```

숫자 값은 예시이며 실제 사용자 데이터와 무관합니다.

---

## 6.10 `manifest.json`

백업 자체의 상태를 나타냅니다.

주요 필드:

| 필드 | 설명 |
| --- | --- |
| `schema_id` | 현재 `gfl2-user-csv-backup` |
| `schema_version` | 백업 스키마 버전 |
| `exported_at` | UTC ISO-8601 저장 시각 |
| `counts` | 각 데이터 종류의 최종 레코드 수 |
| `export_reason` | `login_sync`, `login_sync_incomplete`, `cancelled_partial` 등 저장 이유 |
| `payload_counts` | 관찰/조립된 payload type별 개수 |
| `decode_errors` | payload type별 semantic decode 오류 횟수 |

정상 릴리즈 검증 시에는 필수 7종이 `payload_counts`에 존재하고 `decode_errors`가 비어 있는지 우선 확인하면 좋습니다.

---

# 7. 개인정보 보호형 디버그 ZIP
```text
gfl2_packet_backup/gfl2_packet_debug_raw.zip
```

실제 패킷 본문이나 조립된 게임 payload 원문을 저장하지 않는 metadata-only 진단 파일입니다.

## 포함 파일

```text
PRIVACY.txt
debug_manifest.json
matched_tcp_index.jsonl
game_payload_index.jsonl
```

## 저장하지 않는 정보

- TCP payload 원문
- 조립된 GF2 game payload 원문
- `.bin` packet/payload 파일
- 로컬 IP 주소
- 원격 IP 주소
- 로컬/원격 포트 번호
- 패킷 본문에 존재할 수 있는 로그인 토큰
- 쿠키
- 비밀번호/credential
- Authorization 값

## `matched_tcp_index.jsonl`

GF2 연결로 확인된 TCP payload의 **메타데이터만** 저장합니다.

| 필드 | 설명 |
| --- | --- |
| `index` | archive 내부 순번 |
| `flow_id` | 실제 IP/포트를 대신하는 실행 내부 익명 flow 번호 |
| `relative_tcp_seq` | 해당 flow의 첫 sequence 기준 상대 위치 |
| `length` | payload byte 길이 |
| `content_tag` | 같은 archive 안에서 중복/재전송을 비교하기 위한 임시 HMAC tag |

## `game_payload_index.jsonl`

GF2 framing/payload assembly 후 semantic decode 전 단계의 메타데이터입니다.

| 필드 | 설명 |
| --- | --- |
| `index` | 순번 |
| `payload_type` | GF2 payload type |
| `length` | payload 길이 |
| `content_tag` | 임시 HMAC tag |
| `wire_shape` | protobuf field number/wire type의 구조적 통계 |

`wire_shape`는 필드 값 자체를 저장하지 않습니다. length-delimited 필드도 내용 대신 크기 구간만 저장합니다.

## `content_tag` 개인정보 보호

`content_tag`는 매 실행 시 메모리에서 새로 만든 랜덤 키로 HMAC-SHA256을 계산한 뒤 일부만 기록합니다.

- HMAC 키는 ZIP에 기록하지 않음
- 다른 실행/다른 ZIP과 같은 payload인지 재계산할 수 없음
- 원문 payload의 일반 SHA-256을 그대로 노출하지 않음

## 그래도 공유 시 주의할 점

metadata-only debug ZIP은 로그인 토큰 등의 **본문 값은 포함하지 않도록 설계**되어 있지만, payload type·길이·보유 데이터 규모와 같은 구조적 정보는 포함합니다. 따라서 완전히 익명인 파일은 아닙니다. 공개 게시보다는 필요한 디버깅 상대에게만 전달하는 것을 권장합니다.

---

# 8. 패킷 처리와 안전 계약

현재 GUI 캡처 backend의 필터는 다음과 같습니다.

```text
inbound and tcp and tcp.PayloadLength > 0
```

WinDivert handle flag:

```text
SNIFF | RECV_ONLY
```

의도한 계약은 다음과 같습니다.

- `inbound`: 서버 → 클라이언트 수신 방향만 캡처
- `SNIFF`: 원본 패킷의 정상 네트워크 경로를 유지하면서 복사본을 관찰
- `RECV_ONLY`: 이 WinDivert handle을 수신 전용으로 사용
- 캡처 backend에 `.send()` 호출 없음
- `WinDivertSend()` 호출 없음
- outbound 패킷 캡처/재주입 경로 없음
- decode는 `bytes(...)`로 복사된 수신 데이터를 대상으로 수행

게임 종료는 패킷을 변조해서 수행하지 않습니다. 7/7 저장 완료 후 캡처가 끝난 상태에서 관리자 helper가 `psutil`을 통해 정확히 `GF2_Exilium` 이름과 일치하는 프로세스만 종료 요청합니다.

---

# 9. 왜 관리자 권한을 요구하는가

메인 프로그램 전체를 관리자 권한으로 실행하지 않습니다.

구조:

```text
GFL2 Packet CSV Exporter GUI
  일반 사용자 권한
        │
        │ 캡처 시작
        ▼
windows-packet-capture.exe
  관리자 권한
        │
        ├─ WinDivert 캡처
        ├─ 프로세스/연결 확인
        ├─ TCP 재조립
        ├─ payload decode
        └─ CSV/ZIP 저장
```

WinDivert를 여는 캡처 helper에만 관리자 권한이 필요합니다. GUI는 UAC 승인을 위해 자기 자신을 관리자 권한으로 재시작하지 않습니다.

---

# 10. `windows-packet-capture.exe`란 무엇인가

CSV Exporter 프로젝트의 캡처 전용 Python 진입점과 패킷 처리 코드를 PyInstaller로 묶은 관리자 권한 helper입니다.

제3자 사이트에서 별도로 내려받는 임의 실행 파일이 아니라 이 프로젝트 소스에서 생성됩니다.

## 소스/BAT 실행 시 생성 과정

`start_packet_csv_exporter.bat`은 다음을 수행합니다.

1. `.venv`가 없으면 전용 Python 가상환경 생성
2. 프로젝트 의존성 검증
3. 필요한 경우 의존성 복구 설치
4. `bin/windows-packet-capture.exe`와 version marker 확인
5. helper가 없거나 앱 버전과 다르면 PyInstaller로 helper 재빌드
6. GUI 실행

helper 버전 marker:

```text
bin/windows-packet-capture.version
```

---

# 11. 캡처 내부 동작

## 11.1 프로세스 연결 선별

WinDivert NETWORK layer 자체에는 PID가 없으므로 다음 방식으로 GF2 트래픽을 선별합니다.

1. `psutil`로 `GF2_Exilium` 프로세스를 찾음
2. 해당 프로세스의 현재 TCP connection 4-tuple을 조회
3. inbound packet의 `local IP:port ← remote IP:port`와 비교
4. 일치하는 packet만 GF2 TCP stream으로 전달

새 연결 직후에는 Windows가 socket 정보를 `psutil`에 노출하기 전에 첫 서버 응답이 올 수 있습니다. 이를 완화하기 위해 일치하지 않은 후보 패킷을 최대 약 **1.5초**, 최대 약 **4 MiB** 범위에서 메모리에 잠시 보관하고, 해당 flow가 곧 GF2 연결로 확인되면 앞 패킷을 소급 처리합니다.

GF2 연결로 확인되지 않은 후보의 packet body는 debug ZIP에 기록하지 않고 메모리에서 폐기합니다.

## 11.2 TCP 재조립

- TCP sequence 기준 순서 복원
- retransmission/overlap 처리
- 순서가 늦게 도착한 segment pending
- flow별 pending upper bound 적용

## 11.3 GF2 framing

TCP는 메시지 경계를 보장하지 않으므로 별도 framer가 GF2 메시지 단위를 복원합니다.

v1.4.2부터는 정상 frame 시작을 이미 동기화한 flow에서 큰 메시지가 여러 TCP segment로 나뉜 경우, frame 내부 바이트를 잘못 새 header로 판단해 앞부분을 버리는 문제가 수정되었습니다. 현재는 동기화된 frame이 불완전하면 필요한 나머지 TCP 데이터를 기다립니다.

---

# 12. 상태 및 로그 의미

주요 상태:

```text
대기 중 · 캡처를 시작하세요.
캡처 도구 시작 중…
캡처 준비 중…
캡처 준비 완료 · 이제 소녀전선2에 로그인하세요.
수집 중 · N/7
수집 및 저장 완료
```

프로세스 상태 로그:

```text
소녀전선2 실행 대기 중
소녀전선2 실행 확인 · 서버 연결 대기 중
소녀전선2 연결 확인 · TCP N개
```

같은 상태는 로그를 가리지 않도록 계속 반복하지 않고, 상태 변경 시 즉시 출력하며 동일 상태가 지속될 때는 약 8초 간격으로만 reminder를 남깁니다.

---

# 13. 정상 / 미완료 / 취소 동작

## 정상 완료

조건:

- 필수 7종 semantic decode 성공
- 마지막 필수 데이터 이후 quiet window 통과

동작:

1. 캡처 종료
2. TCP/assembler flush
3. 개인정보 보호형 debug ZIP 저장
4. CSV/JSON 생성
5. `gfl2_user_csv_bundle.zip` 생성
6. 완료 안내
7. 소녀전선2 종료
8. CSV Exporter 종료

## 미완료

60초 내 7/7 실패 시:

- 캡처 종료
- 소녀전선2는 종료하지 않음
- 정상 완료 ZIP은 덮어쓰지 않음
- 부분 자료는 다음 이름으로 저장

```text
gfl2_packet_backup/gfl2_user_csv_partial.zip
```

- metadata-only debug ZIP 생성
- 누락 payload type을 GUI에서 안내

## 수동 취소

이미 수집된 데이터가 있으면 partial ZIP을 만들 수 있습니다. 이 역시 정상 `gfl2_user_csv_bundle.zip`을 덮어쓰지 않습니다.

---

# 14. 환경 진단

GUI의 **환경 진단** 버튼은 다음 상태를 확인합니다.

- Windows 여부
- `windows-packet-capture.exe` 존재 여부
- PyDivert 사용 가능 여부와 버전
- 소녀전선2 프로세스 탐지 여부
- 현재 TCP 연결 개수
- WinDivert filter 구성 상태

환경 진단은 WinDivert 캡처 handle을 열지 않는 read-only preflight입니다.

---

# 15. 로그 파일

소스/BAT 방식의 시작 문제는 다음 파일을 확인합니다.

```text
logs/startup.log
```

예:

- Python 가상환경 생성 실패
- dependency 설치 실패
- PyInstaller helper 빌드 실패
- import 실패

실시간 캡처의 상세 동작은 GUI의 **작업 로그**와 `gfl2_packet_debug_raw.zip`의 메타데이터를 확인합니다.

---

# 16. 문제 해결

## `0/7`에서 움직이지 않음

1. **캡처 준비 완료**가 뜬 뒤 로그인했는지 확인
2. 환경 진단에서 소녀전선2 프로세스가 탐지되는지 확인
3. TCP 연결 개수가 0이 아닌지 확인
4. 방화벽/보안 제품이 WinDivert를 차단하는지 확인
5. 60초 후 생성된 debug ZIP 확인

## `5/7`, `6/7`에서 종료

`debug_manifest.json`에서 다음을 확인합니다.

- 필요한 payload type이 `assembled_payload_type_counts`에 있었는지
- `collector_payload_counts`에 있었는지
- framing 통계에 비정상적인 drop/resync가 있었는지
- `manifest.json.decode_errors`에 오류가 기록됐는지

## `decode_errors`가 생김

게임 업데이트로 protobuf/wire schema가 바뀌었을 가능성이 있습니다. 해당 payload type과 metadata-only debug ZIP을 기준으로 decoder를 다시 검증해야 합니다.

## `WinError 6: 핸들이 잘못되었습니다`

구버전에서 중지 스레드와 캡처 스레드가 같은 WinDivert handle을 이중 close하는 경쟁 조건이 있었습니다. 현재 버전은 중지 시 `stop_event`와 필요한 경우 receive-side shutdown만 요청하고, 실제 handle close는 캡처 소유 스레드 한 곳에서만 수행합니다.

## 관리자 권한 요청을 취소함

메인 GUI는 그대로 유지되며 캡처만 시작되지 않습니다. 다시 **캡처 시작**을 눌러 재시도할 수 있습니다.

# 17. CLI

일반 사용자는 GUI를 권장합니다. CLI는 개발/진단용에 가깝습니다.

## 직접 캡처

```text
gfl2-packet-csv capture --process GF2_Exilium --output gfl2_packet_backup
```

CLI 캡처는 자동 UAC helper를 사용하지 않습니다. 관리자 권한 터미널에서 실행해야 하며, GUI의 7/7 자동 로그인 lifecycle 대신 `Ctrl+C`로 중지하는 직접 캡처 방식입니다.

## 이미 추출된 단일 payload decode

```text
gfl2-packet-csv decode-payload <payload_type> <input.bin> --output gfl2_packet_backup
```

v1.5.0의 개인정보 보호형 debug ZIP에는 `.bin` raw payload가 들어 있지 않으므로 이 명령은 별도로 보유한 개발용 payload에만 사용할 수 있습니다.

---

# 18. 소스 실행

```text
start_packet_csv_exporter.bat
```

최초 실행 시 `.venv`가 없으면 자동 생성합니다.

주요 폴더:

```text
.venv/                       # 실행용 Python 가상환경
bin/
  windows-packet-capture.exe
  windows-packet-capture.version
logs/
  startup.log
```

---

# 19. Windows EXE 빌드

```text
build_exe.bat
```

빌드용 `.venv-build`을 만들고 PyInstaller를 이용해 다음 구조를 생성합니다.

```text
dist/GFL2-Packet-CSV-Exporter/
├─ GFL2-Packet-CSV-Exporter.exe
├─ windows-packet-capture.exe
└─ ...메인 GUI onedir 런타임 파일
```

- 메인 GUI: onedir, 일반 권한
- 캡처 helper: onefile, `requireAdministrator`

helper의 관리자 manifest 때문에 `windows-packet-capture.exe`를 실행할 때 Windows UAC가 표시됩니다.

---

# 20. 프로젝트 구조

```text
src/gfl2_packet_csv/
├─ capture.py       # WinDivert 패시브 캡처, 프로세스/TCP 매칭, 재조립
├─ framing.py       # GF2 메시지 framing 및 payload assembly
├─ collector.py     # payload semantic decode, CSV/JSON/ZIP export
├─ debug_raw.py     # metadata-only 개인정보 보호형 디버그 기록
├─ lifecycle.py     # 7/7 로그인 동기화 완료/timeout 상태 머신
├─ helper.py        # 관리자 캡처 helper 진입 및 완료 후 게임 종료
├─ gui.py           # PySide6 메인 GUI
├─ ipc.py           # GUI ↔ helper 파일 기반 IPC
├─ cli.py           # 개발/진단용 CLI
└─ wire_probe.py    # protobuf wire-level 구조 parser

packaging/
├─ packet.spec      # 메인 GUI PyInstaller spec
├─ packet_entry.py
├─ helper.spec      # 관리자 helper PyInstaller spec
└─ helper_entry.py

tests/
├─ test_auto_sync_decode.py
└─ test_passive_capture_contract.py
```

---

# 21. 데이터 무결성 권장 검사

외부 프로그램에서 백업 ZIP을 읽을 때 다음을 권장합니다.

1. `manifest.json.schema_id == "gfl2-user-csv-backup"` 확인
2. 지원하는 `schema_version`인지 확인
3. 정상 완료가 필요한 경우 `export_reason == "login_sync"` 확인
4. `decode_errors` 확인
5. `counts`와 실제 CSV 레코드 수 확인
6. CSV 기본키 중복 검사
7. 참조 컬럼은 가능한 경우 FK 검증
8. 단, `weapons.equipped_doll_id`처럼 서버 상태상 현재 보유 목록 밖의 값이 나타날 수 있는 관계는 strict FK로 강제하지 않기

`formations`의 manifest count는 **제대 개수**이고 `formations.csv`의 행 수는 **제대 구성원 행 수**이므로 서로 같지 않은 것이 정상입니다.

---

# 22. 보안 및 개인정보 주의사항

## `gfl2_user_csv_bundle.zip`

이 파일은 사용자의 보유 인형, 장비, 재화 등의 **계정 관련 게임 데이터 자체**를 포함합니다. 로그인 비밀번호를 저장하기 위한 파일은 아니지만 개인 게임 계정 상태가 드러날 수 있으므로 공개 업로드에 주의하세요.

## `gfl2_packet_debug_raw.zip`

v1.5.0 이후에는 packet body를 포함하지 않도록 설계했지만, 데이터 규모와 payload 구조 metadata는 포함합니다. 필요할 때만 공유하는 것을 권장합니다.

## `logs/startup.log`

주로 설치/실행 오류 로그입니다. 사용자 경로가 포함될 수 있으므로 공개 공유 전에 Windows 사용자 이름 등 경로 정보를 확인하세요.

---

# 23. 알려진 제한사항

- 게임 업데이트로 payload type 또는 protobuf schema가 바뀌면 decoder 수정이 필요할 수 있음
- 이름/설명 등 정적 게임 데이터는 이 exporter가 제공하지 않음
- `packed_*` 필드의 최종 의미를 모두 해석하지 않음
- NETWORK layer에 PID가 없어 psutil의 TCP 4-tuple 매칭을 사용함
- 사용자 PC의 보안 제품이 WinDivert 또는 PyInstaller helper 실행을 차단할 수 있음
- onefile helper 종료 시 드물게 `_MEI` 임시 디렉터리 정리 경고가 발생할 수 있음
- CLI는 GUI와 lifecycle/권한 처리 방식이 다름
- 서버가 로그인 시 필요한 7종을 보내지 않는 상태에서는 60초 후 미완료 처리됨

---

# 24. FAQ

### Q. 서버로 보내는 패킷도 캡처하거나 수정하나요?

기본 GUI backend는 inbound + SNIFF + RECV_ONLY로 구성되어 있으며 코드에 packet send/reinject 경로를 두지 않습니다.

### Q. 왜 게임을 종료하나요?

정상 7/7 저장 완료 후 캡처 작업이 더 이상 필요하지 않은 상태에서, 안전한 종료 흐름을 명확히 하기 위해 사용자 완료 안내 뒤 게임과 Exporter를 종료하도록 설계되어 있습니다.

### Q. 게임은 자동으로 실행해 주나요?

아닙니다. 캡처 준비 완료 문구가 뜬 뒤 사용자가 직접 게임을 실행/로그인합니다.

### Q. 60초가 지나면 게임도 꺼지나요?

아닙니다. 필수 데이터가 모두 수집되지 않은 미완료 상태에서는 게임을 종료하지 않습니다.

### Q. `gfl2_packet_debug_raw.zip`을 개발자에게 보내도 되나요?

v1.5.0 이후 archive에는 packet body를 넣지 않도록 설계했습니다. 그래도 payload type·크기 같은 구조 metadata는 있으므로 신뢰할 수 있는 디버깅 상대에게만 전달하는 것이 좋습니다.

### Q. 정상 완료 ZIP과 partial ZIP의 차이는?

`gfl2_user_csv_bundle.zip`은 7/7 성공 완료본입니다. `gfl2_user_csv_partial.zip`은 timeout/수동 취소 등 완전하지 않은 결과일 수 있습니다.

### Q. 결과 파일만 다른 도구에서 사용할 수 있나요?

가능합니다. CSV/JSON은 로컬 파일이므로 다른 프로그램이 schema를 구현하면 읽을 수 있습니다. `manifest.json`의 schema/version을 먼저 확인하는 것을 권장합니다.

---

# 25. 라이센스

## 라이선스 및 제3자 소프트웨어

### 프로젝트 라이선스

**GFL2 Packet CSV Exporter**는 **GNU General Public License Version 3 (GPLv3)**에 따라 배포됩니다.

GPLv3의 조건에 따라 이 프로그램의 소스 코드를 사용, 복사, 수정 및 재배포할 수 있습니다. 수정된 버전 또는 이 프로그램을 기반으로 한 파생 저작물을 배포하는 경우에도 GPLv3의 조건을 준수해야 하며, 해당 소스 코드와 라이선스 고지를 함께 제공해야 합니다.

이 프로그램은 유용하게 사용될 것을 기대하여 제공되지만 **어떠한 보증도 제공하지 않습니다.** 상품성, 특정 목적에 대한 적합성 등을 포함한 명시적 또는 묵시적 보증 없이 제공되며, 프로그램의 사용으로 인해 발생하는 문제에 대해서는 관련 법률이 허용하는 범위 내에서 개발자가 책임을 지지 않습니다.

자세한 라이선스 조건은 저장소에 포함된 `LICENSE` 파일의 **GNU General Public License Version 3** 전문을 참고하십시오.

> 이 프로젝트의 GPLv3 라이선스는 아래에 나열된 제3자 소프트웨어의 라이선스를 대체하거나 변경하지 않습니다. 각 제3자 구성요소에는 해당 프로젝트의 원래 라이선스가 각각 적용됩니다.

---

### 제3자 라이브러리 및 구성요소

이 프로젝트는 다음과 같은 오픈소스 라이브러리 및 도구를 사용합니다.

| 구성요소                             | 용도                         | 라이선스                                          |
| -------------------------------- | -------------------------- | --------------------------------------------- |
| **PySide6 / PySide6-Essentials** | 그래픽 사용자 인터페이스 및 Qt 런타임     | LGPLv3 / GPLv3 / Qt Commercial License        |
| **Shiboken6**                    | PySide6 Python/C++ 바인딩 런타임 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| **psutil**                       | 실행 프로세스 및 네트워크 연결 확인       | BSD-3-Clause                                  |
| **PyDivert**                     | Python에서 WinDivert 사용      | LGPL-3.0-or-later OR GPL-2.0-or-later         |
| **WinDivert**                    | Windows 네트워크 패킷 캡처         | LGPLv3 또는 GPLv2 이중 라이선스                       |
| **CPython**                      | Python 실행 환경               | Python Software Foundation License Version 2  |
| **PyInstaller**                  | Windows 실행 파일 패키징          | GPLv2 기반 라이선스 + 실행 파일 배포 예외 조항                |
| **setuptools**                   | Python 패키지 빌드              | MIT License                                   |
| **wheel**                        | Python 패키지 빌드 및 배포         | MIT License                                   |

#### PySide6 / Qt for Python

이 프로그램의 GUI는 Qt for Python의 **PySide6**를 사용합니다.

Qt for Python Community Edition은 LGPLv3/GPLv3 계열의 오픈소스 라이선스로 제공되며, 별도의 Qt 상용 라이선스도 존재합니다. PySide6와 함께 설치되는 Qt 및 Shiboken 구성요소에는 각각 해당 Qt 라이선스 조건이 적용됩니다.

#### PyDivert 및 WinDivert

패킷 캡처 기능은 **PyDivert**와 그 기반 구성요소인 **WinDivert**를 사용합니다.

PyDivert는 `LGPL-3.0-or-later OR GPL-2.0-or-later` 이중 라이선스로 제공되며, WinDivert 역시 LGPLv3/GPLv2 계열의 이중 라이선스로 제공됩니다.

GFL2 Packet CSV Exporter 자체는 GPLv3으로 배포되지만, PyDivert와 WinDivert의 원본 코드 및 바이너리에는 각 프로젝트의 원래 라이선스와 저작권이 그대로 적용됩니다.

#### psutil

프로세스 확인 및 네트워크 연결 식별에는 **psutil**을 사용하며, psutil은 **BSD 3-Clause License**에 따라 배포됩니다.

#### PyInstaller

Windows 실행 파일 생성에는 **PyInstaller**를 사용합니다.

PyInstaller 자체는 GPL 계열 라이선스를 사용하지만, 별도의 예외 조항에 따라 PyInstaller를 이용해 생성한 실행 파일에 PyInstaller 자체의 GPL 라이선스를 강제로 적용할 필요는 없습니다. 따라서 이 프로젝트의 실행 파일은 프로젝트 라이선스인 GPLv3에 따라 배포될 수 있으며, 포함된 제3자 라이브러리에는 각 라이브러리의 개별 라이선스가 계속 적용됩니다.

---

### 제3자 저작권

위 라이브러리와 구성요소의 저작권은 각각의 원 저작자 및 프로젝트에 귀속됩니다.

이 프로젝트는 해당 제3자 프로젝트의 소유권이나 저작권을 주장하지 않습니다. 각 라이브러리의 정확한 라이선스 전문, 저작권 고지 및 추가 조건은 해당 프로젝트에 포함된 `LICENSE`, `COPYING`, `NOTICE` 등의 파일을 우선하여 확인하십시오.

배포 패키지에 제3자 라이브러리 또는 해당 바이너리를 포함하는 경우, 적용되는 라이선스에서 요구하는 라이선스 전문과 저작권 고지를 함께 제공해야 합니다.

---

### Girls' Frontline 2 관련 고지

**Girls' Frontline 2: Exilium**, **Girls' Frontline**, 관련 명칭, 로고, 게임 데이터 및 기타 관련 자산에 대한 권리는 각각의 권리자에게 있습니다.

GFL2 Packet CSV Exporter는 해당 게임의 개발사, 퍼블리셔 또는 운영사와 공식적인 제휴, 승인, 후원 관계가 없는 **독립적인 비공식 오픈소스 프로젝트**입니다.

본 프로젝트의 GPLv3 라이선스는 프로그램 자체의 소스 코드에 적용되는 것이며, 게임과 관련된 상표, 저작권, 데이터 또는 기타 제3자의 지식재산권에 어떠한 권리를 부여하는 것이 아닙니다.
