"""Five-category >=95 report gate from a fresh installed fixture snapshot.

Does not accept caller calculations, read old production defaults or publish.
Local offline report acceptance is not source entitlement/production approval.
"""
import hashlib
import sqlite3

from analysis.installed_evidence import read_installed_snapshot
from analysis.package_b_analytics import analyze_snapshot
from rendering.package_b_report import audit_candidate_report
from validation.publication_gate import WEIGHTS, PASS_SCORE


def evaluate_installed_fixture(document,*,db_path,lock_dir,asof,snapshot_captured_utc,selections=()):
    errors={category:[] for category in WEIGHTS};local=None;snapshot_sha=None
    try:
        snapshot=read_installed_snapshot(db_path,lock_dir,asof=asof,
            snapshot_captured_utc=snapshot_captured_utc,selections=selections)
        snapshot_sha=snapshot.sha256;local=audit_candidate_report(analyze_snapshot(snapshot),document)
        mapping={'CRITICAL_INPUT_DATA':'exactitud','HTML_DOES_NOT_MATCH_EVIDENCE':'consistencia',
            'ANALYTICS_DO_NOT_MATCH_EVIDENCE':'analisis','MODEL_EVIDENCE_INVALID':'trazabilidad',
            'REMOTE_OR_UNSAFE_DEPENDENCY':'presentacion','LEGACY_FINANCIAL_CONTENT_CHECKS':'consistencia'}
        for failure in local['failures']: errors[mapping.get(failure,'trazabilidad')].append(failure)
    except (ValueError,TypeError,OSError,sqlite3.Error) as error:
        errors['trazabilidad'].append('INSTALLED_EVIDENCE_BLOCKED:'+type(error).__name__)
    scores={category:0 if issues else 100 for category,issues in errors.items()}
    critical=sum(len(issues) for issues in errors.values())
    total=round(sum(scores[c]*WEIGHTS[c] for c in WEIGHTS),2)
    passed=critical==0 and total>=PASS_SCORE
    return {'contract':'installed-marked-fixture-v1','state':'INSTALLED_OFFLINE_REPORT_GATE_PASS' if passed else 'BLOCKED_INSTALLED_OFFLINE_REPORT_GATE',
        'pass':passed,'score_total':total,'pass_score':PASS_SCORE,'scores':scores,'weights':dict(WEIGHTS),
        'critical_count':critical,'all_issues':[item for items in errors.values() for item in items],
        'report_sha256':hashlib.sha256(document.encode()).hexdigest(),'snapshot_sha256':snapshot_sha,
        'local_reconciliation':local,'production_publication_authorized':False,'external_history_verified':False}
