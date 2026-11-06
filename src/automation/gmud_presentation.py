"""Public, escaped presentation metadata; never an authorization input."""
from __future__ import annotations


def context_for(change: dict, *, state: dict | None = None, **extra) -> dict:
    from automation.release_projection import project
    projection=project(change,state) if 'release' in change else {}
    return {key:change.get(key) for key in ['rcl_id','change_id','phase','criticality','application_version',
        'current_version','owner','events','execution_plan','rollback_plan','ci_links','rollback_until',
        'kestra_url','kestra_flow_id','argo_application']} | projection | extra


def public_identity(change: dict) -> str:
    return change.get('rcl_id') or 'GLPI '+str(change['change_id'])
