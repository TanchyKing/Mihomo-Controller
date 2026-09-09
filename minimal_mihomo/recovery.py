"""Local recovery works without network access or this chat."""
import subprocess
from pathlib import Path
from .storage import ControllerError


def return_to_verge(service):
    binary = Path('/usr/bin/clash-verge')
    if not binary.is_file():
        raise ControllerError('Clash Verge executable was not found.')
    service.action('stop')
    state = service.status()
    if state.get('ActiveState') not in ('inactive', 'failed') or int(state.get('MainPID', 0)):
        raise ControllerError('Our core has not stopped. Clash Verge was not launched.')
    subprocess.Popen([str(binary)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    return 'Our core is stopped and Clash Verge was opened. Enable its previous profile/proxy mode and check connectivity there.'
