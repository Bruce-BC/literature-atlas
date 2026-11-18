# CLAUDE.md — Literature Atlas

Claude Code가 자동으로 읽는 진입점이다. 세부 인수인계는 [docs/claude-handoff.md](docs/claude-handoff.md)(한국어)에 있다. 기여 계약은 [AGENTS.md](AGENTS.md)이며 이 파일과 함께 유효하다.

## 한 줄 요약

로컬 전용·읽기 전용 Zotero 투영(Python 3.11+ loopback bridge) + Obsidian 데스크톱 플러그인(D3/Cytoscape/AntV G6 프런트엔드). 현재 버전은 `0.1.0`이며 새 릴리스가 아니다. Obsidian Community 제출은 사용자 결정으로 **보류(DEFERRED)** 상태다. 현재 커밋/브랜치는 `git status`와 `git log`로 확인한다(기준선 `b7cb0f3`은 v2 이전의 역사적 커밋).

## 읽는 순서

1. [AGENTS.md](AGENTS.md) — 기여 계약(읽기 전용 Zotero, 소스 펜스, 개인정보).
2. [docs/claude-handoff.md](docs/claude-handoff.md) — 목표·상태·모듈·불변식·명령·알려진 한계.
3. [docs/architecture.md](docs/architecture.md), [docs/implementation-interfaces.md](docs/implementation-interfaces.md), [docs/snapshot.schema.json](docs/snapshot.schema.json) — 공개 계약.
4. [docs/taxonomy-v2-acceptance.md](docs/taxonomy-v2-acceptance.md) — 43개 acceptance 기준과 테스트 매핑.
5. [docs/verification.md](docs/verification.md) — 검증 게이트와 최신 범위.

## 반드시 지킬 불변식

- Zotero는 읽기 전용이다. 메타데이터·파일·파일명·첨부·SQLite를 쓰지 않는다.
- source ID · epoch · item generation이 모든 작업을 펜스한다. 부분/실패 스냅샷으로 삭제하지 않고, 다른 source DB 사이에서 overlay를 재매핑하지 않는다.
- 전체 집계는 detail 서브샘플링과 독립이다. citation / semantic / keyword 공출현은 구분된 타입이다.
- 사용자 메타데이터는 항상 텍스트다(HTML 금지). API는 loopback + 인증, 플러그인 핸드셰이크는 정확한 origin + 메모리 전용.
- 실제 라이브러리 데이터, PDF, vault 경로, 토큰, 모델 캐시, `.omx/`는 Git·릴리스 산출물에 넣지 않는다. 공개 fixture는 합성 데이터뿐이다.
- 원본 span 텍스트는 스냅샷·trace·로그에 복제하지 않는다(해시 + 오프셋만, 로컬 resolver가 원본에서 복원).
- 스키마/인터페이스 변경은 `docs/snapshot.schema.json`, `docs/implementation-interfaces.md`, 디코더, 테스트를 함께 갱신한다.
- 실패와 제한은 그대로 보고한다. 실제 추론/호스트 검증을 mock으로 대체하지 않는다.

## 안전하게 시작하기

```sh
git clone https://github.com/Bruce-BC/literature-atlas.git
cd literature-atlas
npm ci
python3 -m pip install -e '.[test,pdf]'   # 기존 의존성만; 새 의존성 추가 금지
python3 -m playwright install chromium
```

npm 스크립트는 `PATH`의 `python3`를 직접 호출한다(`PYTHON` 같은 환경 변수는 효과가 없다). 원하는 인터프리터를 쓰려면 가상환경을 activate하거나 `PATH`를 조정해 `python3`가 그것을 가리키게 한 뒤 `npm run verify`를 실행한다. Node와 Python은 같은 CPU 아키텍처를 권장하며, 실제로는 Python 바이너리와 설치된 네이티브 확장 휠이 같은 아키텍처여야 한다. macOS의 `npm run lint`는 `arch -arm64 python3`를 강제하므로 arm64로 실행 가능한 `python3`가 필요하다. 문서/코드에 개인 절대 경로를 쓰지 않는다.

## 테스트 명령

| 목적 | 명령 |
|---|---|
| 린트(JS 문법 + Python 컴파일 + 스키마) | `npm run lint` |
| JS 단위 | `npm test` |
| Python 전체 | `python3 -m pytest -q tests` |
| 브라우저 E2E | `npm run build && npm run test:e2e` |
| 개인정보 감사 | `npm run audit:privacy` |
| 전체 게이트 | `npm run verify` |
| 성능(합성 1,000 papers / 1,410 concepts) | `npm run build && python3 scripts/benchmark_taxonomy_v2.py` |
| 실제 E5(명시적 opt-in) | `ATLAS_REAL_E5=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 -m pytest -q -rA tests/graph/test_real_e5_smoke.py` |

좁은 변경에는 대상 테스트를 먼저, 그다음 관련 build/lint/브라우저 검사를 실행한다. 소스를 바꾸지 않는 문서 작업에는 `npm run lint`와 `npm run audit:privacy`면 충분하다.

## 작업 규칙

- 다른 기여자의 변경을 되돌리지 않는다. 커밋/푸시는 사용자가 요청할 때만 한다.
- 사용자의 작업 요청이 범위를 승인한다. 그 범위 안의 로컬 편집·테스트는 매번 묻지 말고 자율적으로 진행한다. 요청되지 않은 기능으로 확장하지 않는다. 알려진 한계는 handoff 문서의 목록을 따른다.
- CI(`.github/workflows/ci.yml`)는 `npm run verify`와 동등한 단계를 실행한다.
