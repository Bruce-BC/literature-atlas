# Taxonomy / interaction v2 — acceptance criteria (43)

Self-contained public record of the 43 acceptance criteria and the tests that exercise them. All evidence uses **synthetic** fixtures and the public test suite; it does not depend on any private plan, log or corpus. Status of every row: *passes on synthetic/public scope*. Row TAXV2-033 is **synthetic conformance only**, not blind or private-corpus accuracy. See [verification.md](verification.md) for commands and measured values, and [claude-handoff.md](claude-handoff.md) for context.

Abbreviations: `G` = `tests/graph/test_taxonomy_v2.py`; `P` = `tests/backend/test_taxonomy_v2_persistence.py`; `S` = `tests/backend/test_taxonomy_v2_schema.py`; `C` = `tests/backend/test_category_colors.py`; `A` = `tests/e2e/test_taxonomy_v2_acceptance.py`; `L` = `tests/e2e/test_taxonomy_v2_pipeline.py`; `TR` = `tests/backend/test_taxonomy_v2_trace.py`; `DEC` = `tests/frontend/taxonomy-decoder.test.mjs`; `INT` = `tests/frontend/taxonomy-interaction.test.mjs`; `VIS` = `tests/frontend/taxonomy-visual.test.mjs`; `JTR` = `tests/frontend/taxonomy-trace.test.mjs`; `G6L` = `tests/frontend/g6-lifecycle.test.mjs`. Browser tests drive headless Chromium against the real bridge/app with synthetic data.

## A. Classification ontology and QIIME 2

| ID | Criterion (short) | Evidence (file::test) |
|---|---|---|
| TAXV2-001 | `facetCatalog`/`paperFacets` schema, same-kind `broaderIds`; QIIME 2 + amplicon → Bioinformatics, `tool:qiime2`, `method:amplicon`; QIIME 2 alone ≠ metagenomics | G::test_qiime2_amplicon_primary_per_facet_and_no_metagenomics; S::test_generated_snapshot_validates_json_schema_and_decodes_across_languages; DEC::decoder rejects cross-kind parents, cycles, duplicate references, and bad colors; L::test_generated_qiime2_and_phage_facets_colors_and_counts_reach_the_browser_from_the_real_backend |
| TAXV2-002 | Phage therapy merely using QIIME 2 keeps Phage Biology primary and gains only the tool facet; tool development vs use distinguished | G::test_qiime2_phage_use_stays_phage_and_development_is_bioinformatics; L::(same pipeline test) |
| TAXV2-003 | Only explicit metagenomic evidence adds `method:metagenomics`; broad ancestor never overrides subject primary | G::test_metagenomics_requires_explicit_evidence_and_ancestor_never_primary |
| TAXV2-004 | Evidence tier (title > abstract > indexed/fulltext) and span stored; user-confirmed beats heuristic | G::test_evidence_refs_have_tier_offsets_and_never_raw_text; G::test_title_outranks_abstract_and_fulltext_evidence_tier; G::test_user_confirmed_math_biology_membership_survives_and_wins; G::test_stored_evidence_span_resolves_locally_against_the_original_raw_source_after_a_real_build; G::test_resolver_rejects_edited_inputs_other_normalizations_malformed_refs_and_binds_the_field |
| TAXV2-005 | Below-threshold → abstain; generic biomedical "analysis" yields no Mathematics/Analysis false positive | G::test_generic_biomedical_analysis_abstains_and_primary_is_none; G::test_ambiguous_unrelated_subjects_abstain_primary_but_keep_directs; G::test_v2_gold_conformance_gates_synthetic_not_blind_heldout_accuracy |
| TAXV2-006 | Direct / primary / ancestor / inclusive exposed separately; known primary + unknown = N; primary ≤ direct ≤ inclusive | S::test_field_stats_invariants_in_real_snapshot; S::test_primary_is_always_an_accepted_direct_membership_in_real_snapshot; L (legend DOM equals API `fieldStats`) |
| TAXV2-007 | Taxonomy input states (absent, intentional-empty, valid-custom, invalid, disabled) behave distinctly; invalid/disabled never re-enable builtin | G::test_taxonomy_input_states_are_distinct_and_never_reenable_builtin; G::test_intentional_empty_taxonomy_hides_fields_but_keeps_internal_facets_dangling_free; G::test_invalid_or_disabled_taxonomy_does_not_resurrect_builtin_fields; S::test_every_taxonomy_input_state_produces_a_schema_valid_decodable_snapshot; P::test_invalid_or_disabled_taxonomy_serves_previous_valid_snapshot_with_reason_and_recovery |

## B. Migration and concept quality

| ID | Criterion (short) | Evidence |
|---|---|---|
| TAXV2-008 | `atlas-graph-v3` migration records raw/active/suppressed/protected counts and preserves user pins, exclusions, aliases, redirects, memberships and source/epoch/generation | P::test_real_pipeline_snapshot_validates_schema_and_ledger_counts; P::test_base_publish_keeps_redirects_and_state_then_enrich_backs_up_and_rolls_back_true_safe; P::test_validate_migration_detects_redirect_loss_that_the_old_base_publish_would_have_hidden; P::test_unchanged_source_recipe_upgrade_also_keeps_redirects_and_pins_through_enrich |
| TAXV2-009 | KL / Kullback–Leibler, OT / optimal transport, QIIME2 canonicalized with context; occupational-therapy OT and non-divergence KL never merged | G::test_ot_and_kl_acronyms_need_matching_context; G::test_kl_and_ot_canonicalize_only_in_matching_context; G::test_full_pipeline_ot_and_kl_context_per_paper_without_false_shared_merge; G::test_ot_acronym_needs_strong_math_context_and_occupational_morphology_blocks_it; G::test_full_pipeline_occupational_therapist_ot_paper_is_not_optimal_transport |
| TAXV2-010 | `disease burden`, `fiber bundles`, `vector bundles` kept; standalone `been`, `burden`, `bundles` suppressed as generated-only | G::test_phrase_protection_and_boilerplate_suppression_with_ledger_resolution; G::test_suppressed_ids_drop_from_papers_but_stay_in_ledger; G::test_user_pins_aliases_exclusions_and_owned_concepts_are_protected_over_deny_rules |
| TAXV2-011 | MSC codes (e.g. `11M20`, `14M25`, `52B20`) preserved as code facet or protected concept | G::test_msc_codes_survive_from_structured_extra_and_subject_codes_without_leaking_extra; P::test_msc_codes_flow_from_structured_extra_through_sync_to_protected_concepts |
| TAXV2-012 | Concept quality and keyword aggregate independent of sample fraction | L::test_sample_fraction_changes_the_display_but_never_retained_concepts_ledger_memberships_or_keyword_aggregate; VIS::concept category is computed from the FULL eligible papers, not from the display sample |

## C. Colors, counts, layout

| ID | Criterion (short) | Evidence |
|---|---|---|
| TAXV2-013 | Persisted append-only `categoryColorRegistry`; stable semantic colors; no all-green; root/sibling distinction | C::test_registry_is_append_only_across_rename_insert_and_root_addition; C::test_deep_ancestry_and_all_three_palettes_match_between_python_and_javascript; P::test_color_registry_persists_append_only_across_rebuilds; VIS::registry is append-only: insert, root add, reorder and rename never recolor existing categories; VIS::3 palettes x light/dark: actual theme context drives color; contrast >= 3, no all-green, no sibling collisions |
| TAXV2-014 | Light/dark/system: category colors and density ramp are separate channels with contrast and non-color cues | A::test_system_theme_follows_native_media_switch_for_all_palettes_with_distinct_density_and_outline_cues; A::test_palette_theme_colors_are_actual_distinct_and_contrasted; VIS::density ramp is a separate low-chroma channel that never matches any category color; `npm run test:palette` |
| TAXV2-015 | `Category \| Direct \| Incl. Subfields` aligned columns, right-aligned numbers, no overflow at 320 px and 1440 px | A::test_legend_three_aligned_columns_right_aligned_numbers_no_overflow (320-light, 1440-dark) |
| TAXV2-016 | Direct/inclusive are unique paper-ID set cardinalities | G::test_field_stats_unique_sets_under_multi_membership_and_multi_parent; VIS::field counts use accepted direct memberships rather than displayFieldId |

## D. Scope and all-paper overview

| ID | Criterion (short) | Evidence |
|---|---|---|
| TAXV2-017 | One canvas point per active paper, deterministic preset, counters truthful, marker mount/hit-test/native selection works | A::test_all_papers_native_point_click_selects_paper_detail_and_revision; INT::all-paper projection: exactly one deterministic point per active paper including unknown; focus only dims; INT::all-paper preset coordinates are real canvas-scale, unique and non-overlapping for 1000 papers |
| TAXV2-018 | Global / field / detail scopes reflected in state and UI; slider does not change aggregates | A::test_field_focus_dims_points_syncs_lower_networks_and_paper_click_keeps_scope |
| TAXV2-019 | Field click: selection, eligible papers, projection and drawer share one revision; no stale concepts | A::test_field_focus_dims_points_syncs_lower_networks_and_paper_click_keeps_scope; INT::FIELD_SELECT sets scope/selected/detail atomically, clears concept constraint and keeps other expansions |
| TAXV2-020 | Paper click after field select opens paper detail/Zotero action and keeps field focus | A::test_field_focus_dims_points_syncs_lower_networks_and_paper_click_keeps_scope; INT::field -> paper keeps the field as SCOPE only |
| TAXV2-021 | Snapshot replacement clears deleted selection; no stale detail reuse | L::test_poll_driven_append_and_delete_through_the_normal_sync_pipeline; INT::SNAPSHOT_REPLACE same source preserves valid IDs, applies redirects, clears deleted IDs and resets deleted-field scope |
| TAXV2-022 | Cytoscape preset only with finite coverage; deterministic seeds; camera transitions; zoom in [0.2, 3] | A::test_cytoscape_camera_policy_with_real_canvas_positions; INT::camera plan: first visit fits, same signature preserves, topology change seeds and offers Fit new content, revisit restores, epoch resets; INT::zoom is clamped to [0.2, 3]; INT::partial preset positions are completed with finite unique seeds |

## E. G6 interaction

| ID | Criterion (short) | Evidence |
|---|---|---|
| TAXV2-023 | App-owned expand/collapse: first click select+expand, second collapse; round-trips exactly | A::test_g6_representative_expand_collapse_child_click_paging_and_dedup; INT::REPRESENTATIVE_TOGGLE: first adds, second removes; always selects the field and replaces hidden child detail |
| TAXV2-024 | Child paper click selects only; rapid toggles: last intent wins, none dropped | A::test_g6_representative_expand_collapse_child_click_paging_and_dedup; A::test_rapid_representative_clicks_last_intent_wins_without_dropping |
| TAXV2-025 | Child paging 24 / max 150, dedup across owners, collapse replaces hidden child selection with parent field | INT::child paging is 24 per page and capped at 150 loaded papers across all owners; INT::a multi-parent/multi-owner child is ONE semantic node whose owner states are all preserved; INT::REPRESENTATIVE_TOGGLE: first adds, second removes; always selects the field and replaces hidden child detail |
| TAXV2-026 | Multi-membership keeps primary and additional facet relations | A::test_multi_facet_detail_shows_subject_method_tool_application_with_primary_and_evidence_tier |
| TAXV2-027 | Same topology signature reuses graph/layout; relayout only on topology change | A::test_g6_lower_network_reuses_graph_for_theme_and_topology_change_adds_nodes; G6L (palette updates redraw without re-layout; rapid updates never skip node add) |
| TAXV2-028 | Per-view budgets (edges ≤ 1000, concepts 300, concept-paper 150, membership 400, keyword 600, loaded papers ≤ 150) with full counts retained | A::test_dense_library_keeps_every_views_node_and_edge_budget_and_truthful_counts; VIS::paper network edges are capped at 1000 deterministically |
| TAXV2-029 | Enter/Space, pointer click and drag-then-click behave consistently | A::test_drag_does_not_trigger_trailing_click_and_keyboard_matches_pointer; A::test_keyboard_enter_toggles_representative_like_pointer; G6L::physical drag does not invoke click selection while a click still selects |

## F. Visual Selection

| ID | Criterion (short) | Evidence |
|---|---|---|
| TAXV2-030 | Ego mini-visualization, clickable nodes/edges/legend and drawer matching detail rows | A::test_selection_ego_graph_nodes_edges_chips_and_keyboard_are_native |
| TAXV2-031 | Keyboard focus and accessible names for all entity kinds; Zotero/PDF actions retained | A::test_selection_ego_graph_nodes_edges_chips_and_keyboard_are_native |
| TAXV2-032 | Deterministic reducer transition table; network and drawer share one revision | INT (reducer suite: FIELD/PAPER/OVERVIEW_PAPER/CONCEPT/EDGE/TOGGLE/RESET/SNAPSHOT_REPLACE); L (revision equality) |

## G. Evaluation, performance, observability

| ID | Criterion (short) | Evidence |
|---|---|---|
| TAXV2-033 | ≥80 synthetic gold cases, fixed hash split, precision/F1/coverage/abstention gates, zero Analysis false positives — **synthetic conformance only** | G::test_gold_fixture_is_frozen_diverse_and_split; G::test_archived_v1_gold_is_unchanged_and_differs_only_by_the_four_predeclared_ontology_corrections; G::test_v2_reference_is_versioned_predeclared_and_preserves_original_cases_and_splits; G::test_v2_gold_conformance_gates_synthetic_not_blind_heldout_accuracy; G::test_public_policy_record_pins_the_four_static_corrections_and_unchanged_inputs_and_splits |
| TAXV2-034 | 1,000-paper / 1,410-concept fixture: cold ×5 (max), warm ×20 (p95) against fixed gates | `scripts/benchmark_taxonomy_v2.py` (7/7 gates; values in [verification.md](verification.md)); tests/e2e/test_browser_g6_performance.py::test_large_synthetic_g6_reuses_graphs_on_selection_and_theme |
| TAXV2-035 | 320/1440 px, light/dark, Cytoscape/G6, all-paper/field/detail runs with screenshots and DOM receipts | A::test_browser_matrix_screenshots_and_dom_receipt |
| TAXV2-036 | Observability limited to enums, counts, versions; no spans, titles, paths, tokens | TR::test_sanitizer_is_a_fail_closed_whitelist_of_enums_and_bounded_integers; TR::test_trace_has_no_outbound_sink_and_no_endpoint; JTR (whitelist, bounded ring, no network/console/storage); L::test_local_trace_records_real_transitions_stays_bounded_and_leaks_nothing_private |
| TAXV2-037 | Existing graph/projection/density/G6/settings/community regressions pass | `npm run verify` (all suites) |

## H. Persistence, reducer, all-paper contracts

| ID | Criterion (short) | Evidence |
|---|---|---|
| TAXV2-038 | Existing SQLite singleton payloads persist v2 data without a new table; CAS rejects stale/foreign writes | P::test_taxonomy_config_roundtrip_control_keys_not_persisted_and_extensions_retained; P::test_taxonomy_config_rejections_do_not_advance_cas; P::test_generated_snapshot_blocks_are_not_writable_taxonomy_payloads; P::test_overlay_facet_overrides_are_canonicalised_and_validated; P::test_slow_build_with_concurrent_taxonomy_write_never_publishes_stale_graph |
| TAXV2-039 | Migration records pre/post state; validation failure serves previous snapshot; rollback cannot clobber newer state | P::test_only_actual_recipe_migrations_write_a_full_backup_and_every_publication_is_audited; P::test_real_pipeline_rollback_true_positive_and_all_rejections; P::test_rollback_refuses_when_user_state_changed_since_the_original_backup; P::test_rollback_refuses_after_source_or_generation_change_and_without_a_published_record; P::test_migration_records_are_bounded_per_epoch_and_overall |
| TAXV2-040 | `fieldStats` equals unique-set cardinalities under multi-membership/multi-parent | G::test_field_stats_unique_sets_under_multi_membership_and_multi_parent; S::test_field_stats_invariants_in_real_snapshot |
| TAXV2-041 | Reducer event table incl. hover/focus no-op, out-of-scope paper, parent replacement, redirect/deletion clearing, atomic updates | INT::hover/keyboard focus and unknown events are no-ops and never select; INT::PAPER_SELECT: in-scope keeps scope; outside without EXPLICIT owner goes to library; explicit valid owner switches; INT::SNAPSHOT_REPLACE resets everything on epoch or recipe change (modelStatus.recipeVersion is authoritative); INT::legacy aliases derive ONLY from the next state |
| TAXV2-042 | `all-papers` projection: exactly one point per active paper incl. unknown; `representedIds` = active IDs regardless of culling; focus only dims | A::test_all_papers_native_point_click_selects_paper_detail_and_revision; A::test_field_focus_dims_points_syncs_lower_networks_and_paper_click_keeps_scope; INT::all-paper projection: exactly one deterministic point per active paper including unknown; focus only dims; L::test_poll_driven_append_and_delete_through_the_normal_sync_pipeline |
| TAXV2-043 | Camera behavior on real canvas positions/zoom/pan across rerender, new/revisited scope, topology change, incompatible filter, epoch/recipe reset | A::test_cytoscape_camera_policy_with_real_canvas_positions; A::test_incompatible_filter_gets_its_own_fitted_camera_and_revisit_restores_the_old_one; L::test_recipe_change_and_source_change_reset_selection_expansion_and_camera_with_native_actions |

## Scope limits shared by these criteria

- Fixtures are synthetic; results are not private-corpus accuracy. The gold v2 reference is a static policy fixture whose four corrections were declared before the rule change (record: `tests/fixtures/taxonomy_gold_v2_policy.json`); it is not a blind held-out set. The `predeclaredBy` field inside `taxonomy_gold_v2.json` names a private planning receipt; that is historical provenance only and is not a runtime input. The public policy record is the authoritative, self-contained correction record, and the pinned fixture and its hashes must not be edited.
- Browser tests run in headless Chromium. Screen-reader behavior is not tested natively. Zotero add/delete is exercised through the real backend with a fake Zotero transport; the original source stays read-only.
- Rollback has a store API but no user-facing trigger.

## Commands

```sh
npm run verify                                                        # all criteria except explicit real-model and benchmark runs
python3 -m pytest -q tests/graph/test_taxonomy_v2.py tests/backend
python3 -m pytest -q tests/e2e/test_taxonomy_v2_acceptance.py tests/e2e/test_taxonomy_v2_pipeline.py
node --test tests/frontend/taxonomy-*.test.mjs tests/frontend/g6-lifecycle.test.mjs
npm run build && python3 scripts/benchmark_taxonomy_v2.py   # TAXV2-034
```
