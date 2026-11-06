"""Readable ECR references with independent, fail-closed immutable digest checks."""
from __future__ import annotations

import copy
import re

REFERENCE = re.compile(
    r"(?P<account>[0-9]{12})\.dkr\.ecr\.us-east-1\.amazonaws\.com/"
    r"(?P<repository>aws-eks-istio-lab-dev-orders-(?:worker|api))"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?"
    r"(?:@(?P<digest>sha256:[a-f0-9]{64}))?"
)
FRIENDLY = re.compile(r"(?:worker|api)-[A-Za-z0-9_.-]+-[0-9]{1,20}")


def verified_digest(image: str, ecr=None) -> str:
    """Resolve tags only in fully immutable repositories; pinned history stays valid."""
    match = REFERENCE.fullmatch(image)
    if not match or not (match['tag'] or match['digest']):
        raise PermissionError('Referência de imagem fora do ECR autorizado')
    if match['digest']:
        return match['digest']
    return _tag_digest(match, ecr)


def _tag_digest(match, ecr=None) -> str:
    if ecr is None:
        from automation.live_evidence import client
        ecr = client('ecr')
    args = {'registryId': match['account']}
    repos = ecr.describe_repositories(**args, repositoryNames=[match['repository']])['repositories']
    if len(repos) != 1 or repos[0].get('imageTagMutability') != 'IMMUTABLE':
        raise PermissionError('Tag exige repositório ECR integralmente imutável')
    rows = ecr.describe_images(**args, repositoryName=match['repository'],
                               imageIds=[{'imageTag': match['tag']}])['imageDetails']
    if len(rows) != 1 or not re.fullmatch(r'sha256:[a-f0-9]{64}', rows[0].get('imageDigest', '')):
        raise PermissionError('Não foi possível comprovar o digest da tag ECR')
    return rows[0]['imageDigest']


def pinned_reference(image: str, ecr=None) -> str:
    digest = verified_digest(image, ecr)
    return image if '@' in image else image + '@' + digest


def publication_resources(resources: dict, ecr=None) -> dict:
    """Transform only independently verified version aliases, after authorization."""
    result = copy.deepcopy(resources)
    for resource in result.values():
        if resource.get('kind') != 'Deployment':
            continue
        for container in resource['spec']['template']['spec']['containers']:
            image = container['image']
            match = REFERENCE.fullmatch(image)
            if not match:
                raise PermissionError('Imagem fora do registro autorizado')
            if match['tag'] and FRIENDLY.fullmatch(match['tag']):
                actual = _tag_digest(match, ecr)
                if match['digest'] and actual != match['digest']:
                    raise PermissionError('Tag ECR não corresponde ao digest autorizado')
                container['image'] = image.split('@')[0]
    return result
