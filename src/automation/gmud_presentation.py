"""Public, escaped presentation metadata; never an authorization input."""
from __future__ import annotations


def context_for(change: dict, **extra) -> dict:
    return {key:change.get(key) for key in ['rcl_id','change_id','phase','criticality','application_version',
        'current_version','owner','events','execution_plan','rollback_plan','ci_links','rollback_until',
        'kestra_url','argo_application']} | extra


def public_identity(change: dict) -> str:
    return change.get('rcl_id') or 'GLPI '+str(change['change_id'])
