"""Generate a private Nginx map without exposing administrative credentials."""
import base64
import os
from pathlib import Path

source = Path('/opt/cosmotech/secrets/itsm/runtime.env')
values = dict(line.split('=', 1) for line in source.read_text().splitlines()
              if '=' in line and not line.startswith('#'))
encoded = base64.b64encode((values['KESTRA_ADMIN_USERNAME'] + ':' + values['KESTRA_ADMIN_PASSWORD']).encode()).decode()
output = Path('/etc/nginx/cosmotech-kestra-viewer-auth.map')
with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as file:
    file.write('map $cosmotech_view_role $cosmotech_view_authorization {\n'
               '    default "";\n    viewer "Basic ' + encoded + '";\n}\n')
output.chmod(0o600)
