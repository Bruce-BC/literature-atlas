# Claude 인수인계서 — Literature Atlas (taxonomy / interaction v2)

대상: 다음 업무를 이어받는 Claude. 이 문서는 공개 저장소 문서만으로 자립한다(비공개 계획·로그·`.omx/` 경로가 필요 없다). 짝 문서: [CLAUDE.md](../CLAUDE.md), [AGENTS.md](../AGENTS.md), [taxonomy-v2-acceptance.md](taxonomy-v2-acceptance.md), [verification.md](verification.md), [implementation-interfaces.md](implementation-interfaces.md), [architecture.md](architecture.md).

## 1. 목표와 현재 상태

- 제품: 로컬 읽기 전용 Zotero 투영 + Obsidian 플러그인. 현재 버전 `0.1.0`(새 릴리스 아님). Community 제출은 사용자 결정으로 **보류(DEFERRED)**. 기존 GitHub Actions CI(`.github/workflows/ci.yml`)가 그대로 동작하는 것이 기대 상태다.
- 기준 커밋: 작업 시작점은 `b7cb0f3`(Bound concept graph rendering for large libraries). `b7cb0f3`은 v2 이전의 역사적 기준선이다. 현재 커밋과 브랜치는 클론한 저장소에서 `git status`/`git log`로 확인한다(이 문서는 특정 커밋 SHA를 약속하지 않는다). 문서 작성일은 2026-11-19(Asia/Seoul).
- 검증된 소스 digest(역사 기록): 124개 소스 파일 집합 `b0c37e14fa9ab6531b4a963b5e3793518b8c1700951594b05d1b05f6b8caddd2`. 이는 **문서 인수인계 이전**의 검증 시점 값이며 현재 트리의 해시가 아니다. 현재 트리 상태를 보증하는 값으로 인용하지 말고, 필요하면 다시 검증한다.

### 해결한 8가지 사용자 보고 문제

1. QIIME 2가 기계적으로 `Life Science` 단일 분류로 들어가는 문제 → 주제·방법·도구·응용 다중 facet + abstention.
2. 카테고리 색이 녹색 계열로 편중 → 영속 append-only OKLCH 색 registry.
3. Field Overview에 개별 노드 전체 논문 표시가 없음 → `전체 논문 지도`(all-papers) 모드.
4. field 클릭 시 paper/concept network 동기화 실패 → 단일 reducer의 원자적 revision.
5. G6 대표 노드(예: Optimization) 클릭 시 하위 노드 펼침/접힘 → 앱 소유 expand/collapse.
6. 저품질 concept(`been`, `burden`, `bundles`)와 KL/OT/QIIME2/MSC 코드 처리 → concept quality ledger + context-aware canonicalization.
7. 텍스트 위주 Selection → 시각적 ego 그래프 + 클릭 가능한 chips + detail drawer.
8. 범례 숫자 정렬 → `Category | Direct | Incl. Subfields` 3열 grid(숫자 우측 정렬).

43개 기준의 근거는 [taxonomy-v2-acceptance.md](taxonomy-v2-acceptance.md)에 있다.

## 2. 모듈 지도

Python(`atlas/`): `taxonomy.py`(facet catalog·증거 수집·분류·fieldStats·concept quality·evidence resolver), `facet_validation.py`(쓰기 검증·cycle 검사), `category_colors.py`(색 registry), `migration.py`(recipe v3 migration·백업·감사·검증), `trace.py`(로컬 trace), `graph.py`(GraphEngine.build), `storage.py`(SQLite, `snapshot_inputs`, `rollback_migration`), `sync.py`(`_enrich` 펜스·발행), `server.py`(HTTP API).

프런트엔드(`frontend/`, ES modules): `decoder.js`(엄격 디코딩), `view-state.js`(reducer·scope·revision), `camera-policy.js`(카메라 계획·줌 클램프), `projection.js`(overview/graph 투영), `all-paper-overview.js`(전체 논문 지도), `overview-expansion.js`(G6 대표 expand/collapse 투영), `category-explorer.js`, `network-views.js`·`network-style.js`·`render.js`·`g6-render.js`(렌더러), `selection-view.js`(시각적 Selection), `trace.js`(로컬 메모리 trace), `density-view.js`·`paper-density.js`, `sampling.js`, `palette.js`, `main.js`(조립).

인터페이스는 [implementation-interfaces.md](implementation-interfaces.md)와 [snapshot.schema.json](snapshot.schema.json)이 정본이다. 변경 시 스키마·디코더·테스트를 함께 바꾼다.

## 3. Recipe v3와 snapshot v1 확장

- recipe: `atlas-graph-v3`. schemaVersion은 **1 유지**; 선택 블록 `facetCatalog`, `paperFacets`, `conceptQualityLedger`, `fieldStats`, `taxonomyState`, `taxonomyRedirects`, `categoryColorRegistry`가 additive로 추가된다. 블록이 없는 v1 스냅샷도 유효하다(디코더 테스트가 보증).
- 저장은 기존 SQLite 싱글톤 payload(`taxonomy`, `overlays`, `snapshots`)를 재사용하며 새 테이블이 없다. `taxonomy`는 `facetCatalogConfig`/`inputState`만 영속하고 생성된 스냅샷 블록은 쓰기 거부된다.
- 입력 상태: `absent/uninitialized`(builtin 상속), `intentional-empty-valid`, `valid-custom`, `invalid`/`disabled`(builtin 재활성화 금지, 마지막 유효 스냅샷 + `taxonomyState{status,reason,recovery}`).

## 4. source / epoch / generation / CAS와 사용자 상태 보존

- 모든 작업은 source ID · epoch · item generation으로 펜스된다. `_enrich`는 build 전에 `SQLiteStore.snapshot_inputs`로 source/generation과 taxonomy·overlay·settings revision을 원자적으로 캡처하고, 발행 시점에 모두 일치할 때만 발행한다. 불일치하면 결과를 폐기하고 최신 입력으로 재큐잉한다.
- 설정/overlay/taxonomy 쓰기는 revision CAS(`If-Match`/`expectedRevision`)를 쓴다. 거부된 쓰기는 CAS를 전진시키지 않는다.
- 사용자 pin·제외·alias·redirect·확정 membership은 migration 중 보존된다. 다른 source/epoch로 태그된 taxonomy/overlay는 병합에 이월되지 않는다.
- 백업: `<dataDir>/migrations/atlas-graph-v3/<epoch>/<generation>.json`(불변 원본, 실제 recipe migration에서만 생성), `<generation>.audit.json`(append-only 발행 감사, ≤50). 보존 정책은 epoch당 최신 5개 generation, 최신 3개 epoch.
- 롤백: `SQLiteStore.rollback_migration(epoch, generation)`은 기록된 발행 revision에 묶이며 source/generation 변경, 더 새로운 발행, 사용자 상태(taxonomy/overlay/settings) 변경이면 거부한다. **HTTP/CLI 트리거는 없다**(의도적).

## 5. 분류: fields / facets

- facet 종류: `subject`, `method`, `tool`, `application`. `broaderIds`는 같은 종류만 참조하고 비순환이다(반복 cycle 검사, 2000 entity 초과는 400).
- `directMemberships`(직접 근거), `primaryByFacet`(종류별 최대 1개, abstain이면 `null`, 모든 non-null primary는 같은 종류의 accepted direct membership), 조상 폐쇄는 별도 값이다.
- `fieldStats`는 **unique paper 집합** 기준: `direct`=D(f), `inclusive`=descendant union, `primary`=known subject primary. 불변식: `knownPrimary + unknown = N`, `primary ≤ direct ≤ inclusive`. method/tool/application primary 합계는 N일 필요가 없다.
- 우선순위: 사용자 확정/핀 > 고신뢰 증거 > 휴리스틱. 증거 tier는 title/tags > abstract > indexed/fulltext, 동률은 specificity → 안정 ID. 임계값 미달은 abstain.
- 신뢰도: title 증거와 abstract 증거는 다른 tier. **키워드 tier는 원본 source `keywords`/`tags`만** 읽는다(`source_keyword_items`; 예약 제어 태그 `auto-classifier:`/`auto-primary:` 제외). 빌드가 나중에 발행하는 개념 추출 `keywords`는 읽지 않으므로 abstract/indexed에서 나온 토큰이 키워드 신뢰도로 승격되지 않는다. 증거 tier 가중치는 title 1.0 > keywords 0.92 > abstract 0.8 > indexedText 0.55 > fullText 0.45(`atlas/taxonomy.py`)이다. broad domain(Life Science)의 폴백은 title/tags/abstract/indexedText tier만 인정하고 **fullText 단독은 범주적으로 제외**한다. indexedText는 서로 독립적인 생물학 증거 family가 둘 이상이고 임계값을 충족하면 자격이 생길 수 있다(indexed 단독이 무조건 배제되는 것은 아니다). 더 구체적인 주제나 수동 선택이 있으면 항상 그쪽이 이긴다.
- QIIME 2: 명시적 `QIIME 2`/`QIIME2`/`q2-*` 토큰이 있어야 `tool:qiime2`. QIIME 1·무버전 QIIME, 단독 `Q2-2023`은 tool이 아니다. QIIME 2 + amplicon은 Bioinformatics / `tool:qiime2` / `method:amplicon`. QIIME 2 단독은 metagenomics를 만들지 않는다. 파지 치료가 QIIME 2를 쓰기만 하면 Phage Biology primary 유지 + tool facet만 추가. Life Science는 더 구체적인 주제가 있으면 보류된다(ontology-label-policy-v2).
- 비공개 corpus 정확도를 주장하지 않는다. 15–16절 한계 참조.

## 6. 증거 span · 해시 · resolver 프라이버시

`evidenceRef`는 `field`, `evidenceTier`, `ruleId`, 코드포인트 `spanRange`, `normalization`(`nfkc-casefold-hyphen-v1`), `inputHash`(`sha256:` + 정규화된 필드 텍스트)를 가진다. `evidenceSpan`은 항상 `null`이다. `evidenceRef`는 원문 span을 복제하지 않는다. 로컬 API의 `papers.title`/`abstract`는 정당하게 사용자 라이브러리 메타데이터로 제공되지만, indexed/fullText는 발행 전에 제거되고(`graph.py`) trace 로그와 공개 Git에는 corpus 텍스트가 들어가지 않는다. 로컬 `taxonomy.resolve_evidence_span(sourcePaper, ref)`만 원본 레코드를 재정규화해 해시가 일치할 때 `text[start:end]`를 돌려주고, 편집된 필드·다른 정규화 버전·다른 필드·잘못된 오프셋은 `None`으로 실패 폐쇄한다. 이 경계를 약화하지 않는다.

## 7. concept 품질과 문맥 규칙

- 원장 `conceptQualityLedger`: `raw/active/suppressed/protected/user`. `raw`·`protected`·`user`는 `active`와 겹치는 소유 버킷이고, active↔suppressed만 배타적이다. suppressed ID는 `concepts`에 없지만 `/api/v1/taxonomy/resolve`로 해석된다(`inGraph:false`). 억제는 Zotero 원본 삭제가 아니다.
- 보호 phrase: `disease burden`, `fiber bundles`, `vector bundles`는 보존, 단독 `been`/`burden`/`bundles`는 생성-only 후보로 억제. 구조화된 MSC 코드(`11M20`, `14M25`, `52B20` 등)는 보호된 `extra-msc` concept(`Extra`의 다른 텍스트는 채굴·발행하지 않음).
- KL/OT: 문맥 인지 병합. OT는 강한 수학 용어(transport*, Wasserstein, Sinkhorn, Monge, Kantorovich, coupling, earth mover; 단독 "optimal" 불가)가 필요하고 `occupational therap*`/`ergotherap*`가 차단한다. 문맥 한정 병합은 원장에서 `contextMergedInto`/`mergeScope:"per-paper"`로 표시되며 redirect로 따라가면 안 된다. 전역 안전 병합만 `mergedInto`.
- **noise / protected 코드**: 억제 이유는 `suppress.<ledger reason>` 열거형, 보호 이유는 `protectedBy`. 사용자 pin/제외/alias는 deny 규칙보다 우선한다.

## 8. 색상: 영속 OKLCH · light/dark

`categoryColorRegistry`는 스냅샷에 영속되는 append-only 목록이다. 같은 namespace 재빌드는 기존 항목을 바이트 단위로 재사용하고(에포크 간에는 non-custom만 이월), 카테고리 추가·삽입·재정렬·이름 변경이 기존 색을 바꾸지 않는다. 저장되는 테마 키는 `<paletteId>:<light|dark>`(`atlas`, `calm`, `mocha`)다. `system`은 저장된 독립 팔레트가 아니라 UI가 렌더 시점에 light/dark로 해석하는 값이다(백엔드가 호환용 `system` 별칭 항목을 `atlas:light`와 같은 값으로 함께 쓰며 디코더가 허용하지만, 독립 색으로 취급하지 않는다). density 색 ramp는 category 색과 **별도 채널**이며 외곽선/텍스트 등 비색 단서를 함께 쓴다. JS fallback 생성기와 Python 생성기는 모든 palette/theme hex가 일치해야 한다(테스트가 검증). JS fallback 단독은 append-only가 아니므로 영속 registry가 정본이다.

## 9. Reducer · 카메라 · 비동기 큐

- `view-state.js`가 `selected/focused/expanded/scope/relation/lod/camera`를 단일 reducer로 관리한다. 이벤트: `FIELD_SELECT`, `PAPER_SELECT`, `OVERVIEW_PAPER_SELECT`, `CONCEPT_SELECT`, `EDGE_SELECT`, `REPRESENTATIVE_TOGGLE`, `RESET`, `SNAPSHOT_REPLACE`(+ ribbon `FIELD_PAIR_SELECT`). hover/키보드 focus는 selection을 바꾸지 않는다. 모든 전이는 한 revision에서 eligible/projection/drawer를 계산한다.
- `SNAPSHOT_REPLACE`: 같은 source는 유효 ID 보존·redirect 적용·삭제 ID 해제, epoch/recipe 변경은 전부 초기화.
- 카메라(`camera-policy.js`): 같은 signature는 positions/zoom/pan 보존, scope 최초 진입은 fit 1회, 같은 scope의 topology add/remove는 카메라 보존 + 명시적 `Fit new content` 버튼만 제공, 방문했던 유효 scope는 복원, epoch/recipe 변경은 폐기 후 fit. 줌은 **[0.2, 3]** 클램프. Cytoscape preset은 모든 노드에 유한 좌표가 있을 때만 쓰고 신규 ID는 결정적 seed로 채운다((0,0) 겹침 없음).
- 비동기: G6 래퍼는 직렬 큐를 쓰며 `desired*`(최신 요청)와 `applied*`(실제 적용 상태)를 구분한다. **최신 intent가 이기고(latest-wins) 적용된 그래프 기준으로 판정**한다(같은 desired topology라도 실제 add를 건너뛰지 않음). 실패한 큐 작업은 pending desired 상태를 지워 동일 재시도가 단락되지 않는다. 드래그 후 trailing click은 억제된다.

## 10. 전체 논문 지도(all-paper)와 한도

- `allPaperOverviewProjection`: active paper ID마다 **정확히 한 점**(unknown 포함), 결정적 preset 좌표, `representedIds` = active IDs(뷰포트 culling/라벨 LOD/edge budget과 독립). field 포커스는 점을 지우지 않고 dim/highlight만 한다. 카운터: `N active`, `M viewport`, `displayed/eligible edges`. 집계 ribbon이나 category paging 수를 N으로 표시하면 안 된다.
- 한도: 전체 지도 edge ≤ 1000, 화면당 총 edge ≤ 1000, concept 300, concept-paper 150, membership 400, keyword 600. full 집계는 detail 샘플링과 무관(`sample` 슬라이더가 집계를 바꾸지 않음).
- G6 앱 소유 expand: SDK 내장 collapse-expand를 단독으로 쓰지 않는다. 대표 노드 첫 클릭 = select+expand(하위 category + direct paper 페이지 **24**, 누적 최대 **150**), 같은 대표 두 번째 클릭 = collapse. 확장된 자식 paper 클릭은 paper selection/detail만 하고 토글하지 않는다. 같은 자식이 여러 owner에 노출되면 semantic 노드 1개로 dedupe하고 owner 상태를 보존한다. collapse 후 숨겨진 자식 선택은 부모 field 선택으로 대체된다. 키보드 Enter/Space, 포인터 click, drag 후 click 억제가 같은 의미를 낸다.

## 11. Selection 그래프와 액션

`selection-view.js`: paper/concept/field/edge의 ego mini-graph + 클릭/포커스 가능한 노드·edge·legend chips + detail drawer. 각 요소는 접근 가능한 이름과 키보드 포커스를 가지며 paper에는 Zotero 열기/PDF 액션이 유지된다. 클릭은 reducer와 같은 이벤트를 쓴다.

## 12. 루프 sync · baseline · 롤백 펜스

폴링 sync는 단일 flight이며 완전한 이중 펜스 소스 스냅샷만 원자적으로 커밋한다. 소스 변경~enrichment 종료 사이에는 `modelStatus.state == "pending"`인 base 스냅샷이 제공되며(현재 active paper + 이전 파생 그래프, 같은 source/epoch의 redirect 이력 이월), strict 스키마/디코더 검증을 통과한다. 검증 실패(`validation-failed`)면 이전 유효 스냅샷을 계속 제공한다. 롤백 펜스는 4절을 따르고 완화하지 않는다. baseline(원본 백업)은 덮어쓰지 않는다. 로컬 trace(`<dataDir>/trace/atlas-trace.jsonl`)는 열거형·제한 정수·절단 해시만 기록하고 네트워크 sink/endpoint가 없다(≤400 레코드·256 KiB).

## 13. 명령

```sh
git clone https://github.com/Bruce-BC/literature-atlas.git && cd literature-atlas
npm ci
python3 -m pip install -e '.[test,pdf]'      # 기존 의존성만
python3 -m playwright install chromium
npm run build
npm run verify                                           # 전체 게이트
```

- 실제 E5(명시적·오프라인): `.[ai]`를 수동 설치하고 모델을 미리 캐시한 뒤 `ATLAS_REAL_E5=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python3 -m pytest -q -rA tests/graph/test_real_e5_smoke.py`. 플러그인은 모델/코드를 다운로드하거나 pip를 실행하지 않는다. 가짜 provider는 실제 추론 증거가 아니다. 환경변수가 없으면 이 2개 테스트는 skip된다(통과 아님).
- 성능: `npm run build` 후 `python3 scripts/benchmark_taxonomy_v2.py`(합성 1,000 papers / 1,410 concepts, cold 5회 + warm 20회, 게이트 미달 시 비0 종료). 수치는 [verification.md](verification.md).
- Community 패키지 재현성: `npm run build && npm run package && npm run package:check && npm run test:community`. 결정적 ZIP(정규화 타임스탬프)과 SHA-256 manifest, 금지 경로 검사, 반복 빌드 동일성을 확인한다. 산출물은 `.artifacts/`(Git 제외)에 생긴다. 제출 자체는 보류.
- 아키텍처: Node와 Python은 같은 CPU 아키텍처로 맞춘다(arm64/x86_64 혼합 시 네이티브 import 오류). npm 스크립트(`verify`, `test:python`, `test:e2e`, `audit:privacy`, `package` 등)는 `PATH`의 `python3`를 직접 호출하며 `PYTHON` 환경 변수만으로는 인터프리터가 바뀌지 않는다. 의도한 인터프리터를 쓰려면 가상환경을 activate하거나 `PATH`를 조정해 `python3`가 그것을 가리키게 한 뒤 `npm run verify`를 실행한다. Node와 Python은 같은 CPU 아키텍처를 권장하며 실제로는 Python 바이너리와 설치된 네이티브 확장이 일치해야 한다. macOS의 `npm run lint`는 `arch -arm64 python3`를 강제한다. 개인 절대 경로를 문서·코드에 쓰지 않는다.
- E2E 테스트와 벤치마크는 Git 제외 디렉터리 `.omx/context/`에 로컬 영수증을 쓰며 필요하면 디렉터리를 자동 생성한다(미리 존재할 필요 없음). 비공개 영수증 읽기는 존재할 때만 하는 선택 사항이고, 새 클론은 비공개 입력 없이 검증된다. `taxonomy_gold_v2.json`의 `predeclaredBy`는 비공개 계획 영수증을 가리키는 역사적 출처 표기일 뿐 런타임 입력이 아니며, 자립적인 공개 정정 기록은 `tests/fixtures/taxonomy_gold_v2_policy.json`이다. 고정된 fixture와 해시를 수정하지 않는다.

## 14. 검증 범위 요약(최신 기록)

JS 103 / Python 235 통과 + 2 선택(실제 E5 skip) / 브라우저 238 / 실제 E5 명시 실행 2 통과(384차원, 고정 revision, 합성 텍스트) / 성능 7개 게이트 통과(cold 843.8 ms, warm field 137.5, selection 64.1, expand 80.4, collapse 48.5, G6 settle 2583.3, task max 154 ms). 공개 fixture만 사용했다. 실제 Obsidian 호스트에서의 네이티브 QA는 비식별화된 PASS로만 진술한다: 모델 준비 상태(E5, 384차원)와 포인터/시각 검토를 통과했으나 비공개 호스트 수치는 공개 문서에서 제외한다. 상세 한계는 아래와 [verification.md](verification.md).

## 15. 정직성 경계(그대로 유지)

- gold v1(`tests/fixtures/taxonomy_gold.json`)은 원본 동결본이다. v2(`taxonomy_gold_v2.json`)는 사전 선언된 **4건의 검토된 라벨 정정**과 추가 합성 사례를 가진 정적 정책 fixture다. TAXV2-033은 **합성 적합성**이지 블라인드/비공개 정확도가 아니다.
- 네이티브 기준선은 사용자 override가 0건이었으므로 "비어 있지 않은 상태의 보존"은 합성 테스트로만 증명된다.
- 네이티브 키보드 전용 활성화는 별도로 증명되지 않았고 공개 브라우저 테스트가 키보드 의미를 다룬다.
- 추가/삭제 경로는 실제 백엔드 + 가짜 Zotero 전송으로 검증했고 원본 Zotero는 읽기 전용이다.

## 16. 알려진 낮음/비차단 한계 (요청되지 않은 기능으로 확장하지 말 것)

- 롤백은 store API만 있고 사용자 트리거(HTTP/CLI)가 없다.
- 100% 분류 또는 noise 제거 보장은 없다. 모호한 논문은 abstain으로 남는다.
- 성능 수치는 합성 fixture 결과이며 모든 라이브러리에 대한 보장이 아니다.
- valid-custom taxonomy에서는 생성된 subject facet이 사용자 정의 legacy field로 자동 투영되지 않는다.
- 우선순위 후속 과제: **블라인드 검증**(독립적으로 라벨링한 held-out 집합). 미알려진 오류 개수를 주장하지 않는다.
- 요청받으면 한다: Community 제출 재개, 릴리스, 새 버전 — 모두 사용자가 명시적으로 요청할 때만 한다.
