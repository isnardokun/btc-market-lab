"""R10-E.9 immutable reconciliation of E.7 archive ranges and E.8 gap evidence.

Accepts in-memory RO reports only. No SQLite, HTTP, CLI, path, --apply or
scheduler. The output is a HUMAN REVIEW QUEUE, not an executable API request.
Two independently generated snapshots cannot prove atomic consistency.
"""
import datetime as dt

from ingestion.config import FRED_SERIES

FRED_FREQUENCIES = {series_id: frequency for series_id, _, frequency, _ in FRED_SERIES}

PROVIDERS = ("researchbitcoin", "bitview", "fred", "yahoo")
REVIEW_MODES = frozenset(("UTC_CALENDAR_DAY", "MONTHLY_OBSERVATION"))
NON_CALENDAR = "EXTERNAL_PUBLICATION_CALENDAR_REQUIRED"
MAX_REVIEW_ITEMS = 40


def _utc_iso(value):
    if not isinstance(value, str):
        raise ValueError("UTC generation timestamp missing")
    stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() != dt.timedelta(0):
        raise ValueError("UTC-aware report timestamps required")
    return stamp.isoformat()


def _verified_envelope(manifest, gaps):
    if not isinstance(manifest, dict) or not isinstance(gaps, dict):
        raise ValueError("Expected E7 and E8 report objects")
    for report, state in ((manifest, "ARCHIVED_RANGES_READ_ONLY"),
                          (gaps, "INTERNAL_GAP_CANDIDATES_READ_ONLY")):
        if (report.get("state") != state
                or report.get("execution_authorized") is not False
                or report.get("historical_completeness") != "NOT_VERIFIED"
                or report.get("http_requests") != 0
                or report.get("sqlite_writes") != 0
                or report.get("db_location") != "REDACTED"):
            raise ValueError("Untrusted or mutable source report")
        _utc_iso(report.get("generated_utc"))
    if (manifest.get("provider_availability") != "NOT_CHECKED"
            or manifest.get("real_tier2_entitlement_verified") is not False
            or gaps.get("real_provider_availability_verified") is not False):
        raise ValueError("Source reports must not claim external availability")
    return (_index_sources(manifest.get("sources"), "E7"),
            _index_sources(gaps.get("sources"), "E8"))


def _index_sources(items, label):
    if not isinstance(items, list) or len(items) != len(PROVIDERS):
        raise ValueError(label + " must contain four sources")
    found = {}
    for source in items:
        if not isinstance(source, dict):
            raise ValueError("Invalid source record")
        name = source.get("provider")
        if name not in PROVIDERS or name in found:
            raise ValueError("Unknown or duplicate provider")
        if (source.get("schema_state") not in ("PRESENT", "NOT_INITIALIZED")
                or source.get("historical_completeness") != "NOT_VERIFIED"
                or not isinstance(source.get("series"), list)):
            raise ValueError("Malformed series source")
        by_metric = {}
        for row in source["series"]:
            if not isinstance(row, dict):
                raise ValueError("Malformed source metric")
            metric = row.get("metric")
            if not isinstance(metric, str) or not metric or metric in by_metric:
                raise ValueError("Empty/duplicate metric")
            if row.get("historical_completeness") != "NOT_VERIFIED":
                raise ValueError("Unexpected verified metric")
            by_metric[metric] = row
        found[name] = (source, by_metric)
    if set(found) != set(PROVIDERS):
        raise ValueError("Missing providers")
    return found


def _validate_slot(slot, mode, first, last):
    if not isinstance(slot, str):
        raise ValueError("Non-string missing slot")
    if mode == "UTC_CALENDAR_DAY":
        day = dt.date.fromisoformat(slot)
        if day.isoformat() != slot or not first < slot < last:
            raise ValueError("Invalid or out-of-range UTC calendar slot")
    elif mode == "MONTHLY_OBSERVATION":
        month = dt.date.fromisoformat(slot + "-01")
        if month.strftime("%Y-%m") != slot or not first[:7] < slot < last[:7]:
            raise ValueError("Invalid or out-of-range monthly slot")
    else:
        raise ValueError("Missing slots cannot use an external-calendar mode")


def reconcile_review_queue(manifest, gaps, *, max_items=40):
    """Correlate aggregate evidence without making any claim about API history."""
    if type(max_items) is not int or not 1 <= max_items <= MAX_REVIEW_ITEMS:
        raise ValueError("Review queue must be bounded, no booleans")
    catalog, gap_data = _verified_envelope(manifest, gaps)

    review = []
    totals = {"archived_series": 0, "potential_gap_series": 0,
              "calendar_required": 0, "no_baseline": 0, "no_internal_gap": 0}
    for provider in PROVIDERS:
        saved_src, stored = catalog[provider]
        gaps_src, observed = gap_data[provider]
        if saved_src["schema_state"] != gaps_src["schema_state"]:
            raise ValueError("Schema state conflict between E7 and E8")
        if saved_src["schema_state"] == "NOT_INITIALIZED":
            if observed or any(row.get("observations") for row in stored.values()):
                raise ValueError("Uninitialized table contains observations")
            continue

        # E7 includes catalog-only metrics with zero observations; E8 only
        # returns metrics with actual archived rows.
        if not set(observed).issubset(stored):
            raise ValueError("E8 series missing from E7 snapshot")
        for metric in sorted(stored):
            saved = stored[metric]
            count = saved.get("observations")
            if type(count) is not int or count < 0:
                raise ValueError("Invalid E7 archival count")
            row = observed.get(metric)
            if count == 0:
                if row is not None or saved.get("first_archived_utc") is not None or saved.get("last_archived_utc") is not None:
                    raise ValueError("E7 empty baseline contradicts E8")
                if saved.get("coverage_state") != "NO_OBSERVATIONS":
                    raise ValueError("Invalid zero-count archival status")
                totals["no_baseline"] += 1
                continue

            if saved.get("coverage_state") != "ARCHIVED_RANGE_ONLY" or row is None:
                raise ValueError("E7/E8 presence mismatch")
            if (row.get("observations") != count
                    or row.get("first_archived_utc") != saved.get("first_archived_utc")
                    or row.get("last_archived_utc") != saved.get("last_archived_utc")):
                raise ValueError("E7/E8 aggregate conflict; independent reads may have drifted")
            distinct = row.get("distinct_utc_dates")
            if type(distinct) is not int or not 1 <= distinct <= count:
                raise ValueError("Invalid count of distinct calendar days")
            totals["archived_series"] += 1

            mode = row.get("gap_method")
            if provider in ("researchbitcoin", "bitview"):
                expected_mode = "UTC_CALENDAR_DAY"
            elif provider == "fred" and FRED_FREQUENCIES.get(metric) == "monthly":
                expected_mode = "MONTHLY_OBSERVATION"
            else:
                expected_mode = NON_CALENDAR
            if mode != expected_mode:
                raise ValueError("Source frequency forbids supplied gap calendar")
            missing_count = row.get("internal_gap_candidates")
            samples = row.get("sample_missing_slots")
            truncated = row.get("sample_truncated")
            if not isinstance(samples, list) or len(samples) > 40 or type(truncated) is not bool:
                raise ValueError("Malformed gap samples")

            if mode == NON_CALENDAR:
                if missing_count is not None or samples or truncated:
                    raise ValueError("External calendar must not fabricate missing days")
                totals["calendar_required"] += 1
                continue
            if mode not in REVIEW_MODES or type(missing_count) is not int or missing_count < 0:
                raise ValueError("Unknown internal gap classification")
            if len(set(samples)) != len(samples) or samples != sorted(samples):
                raise ValueError("Missing slots must be sorted and unique")
            if len(samples) > missing_count or truncated != (missing_count > len(samples)):
                raise ValueError("Gap count/sample mismatch")
            for slot in samples:
                _validate_slot(slot, mode, saved["first_archived_utc"],
                               saved["last_archived_utc"])
            if missing_count == 0:
                totals["no_internal_gap"] += 1
                continue
            totals["potential_gap_series"] += 1
            review.append({
                "provider": provider,
                "metric": metric,
                "gap_method": mode,
                "internal_gap_candidates": missing_count,
                "sample_missing_slots": samples,
                "sample_truncated": truncated,
                "review_reason": "POTENTIAL_INTERNAL_GAP_NOT_PROVIDER_VERIFIED",
                "request_ready": False,
                "calendar_or_entitlement_checked": False,
            })

    review.sort(key=lambda item: (-item["internal_gap_candidates"],
                                   item["provider"], item["metric"]))
    return {
        "state": "ARCHIVE_GAP_REVIEW_ONLY",
        "decision": ("REVIEW_CANDIDATES_PRESENT" if review else
                     "NO_INTERNAL_REVIEW_CANDIDATES"),
        "source_reports_correlated": True,
        "atomic_cross_report_snapshot_verified": False,
        "independent_reports_consistent_on_aggregates_only": True,
        "historical_completeness": "NOT_VERIFIED",
        "real_provider_availability_verified": False,
        "real_tier2_entitlement_verified": False,
        "request_ready": False,
        "execution_authorized": False,
        "http_requests": 0, "sqlite_reads": 0, "sqlite_writes": 0,
        "backfill_cursor_advanced": False,
        "total_review_candidates": len(review),
        "review_queue_truncated": len(review) > max_items,
        "review_queue": review[:max_items],
        "summary": totals,
        "caveat": ("E7 and E8 are independent SQLite read transactions; "
                   "matching counts and min/max DO NOT prove a shared snapshot, "
                   "endpoint entitlement, published calendar or recoverability"),
    }
