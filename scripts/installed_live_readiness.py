"""Pure assessment of operator-observed maintenance window, not discovery.

This does not authenticate the operator's inventory. Its exact owned receipt
is separately hash-pinned in the grant; a fixture cannot prove host coverage.
"""
from ingestion.package_a_contracts import utc


def assess_writer_readiness(manifest,observations):
    blocked={'state':'BLOCKED','global_writer_participation':'NOT_VERIFIED'}
    try:
        if type(manifest) is not dict or type(observations) is not dict:return blocked
        if manifest['namespace']!='btc-research-global-writer-v1' or not manifest['lock'].startswith('/') or len(manifest['code_sha'])!=40:return blocked
        if not utc(manifest['window_start'])<=utc(observations['observed_utc'])<utc(manifest['window_end']):return blocked
        if observations.get('inventory_complete') is not True:return blocked
        writers=manifest['writers'];processes=observations['processes']
        if type(writers) is not list or not writers or len(writers)>128 or type(processes) is not list or len(processes)>128:return blocked
        expected={w['id']:w['sha256'] for w in writers}
        if len(expected)!=len(writers) or len({p['id'] for p in processes})!=len(processes):return blocked
        if set(expected)!={p['id'] for p in processes}:return blocked
        for p in processes:
            if (p['state']!='STOPPED_OBSERVED' or p['sha256']!=expected[p['id']] or
                p['namespace']!=manifest['namespace'] or p['lock']!=manifest['lock'] or p['code_sha']!=manifest['code_sha']):return blocked
        return {'state':'CONTROLLED_WRITER_WINDOW_OBSERVED','global_writer_participation':'OPERATOR_OBSERVED_NOT_FIXTURE_CERTIFIED'}
    except (KeyError,ValueError,TypeError,AttributeError):return blocked
