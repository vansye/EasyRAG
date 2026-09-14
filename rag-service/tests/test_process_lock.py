"""The backend and maintenance CLI cannot own the same runtime simultaneously."""

import subprocess
import sys

from app.application.process_lock import ProcessLock


PROBE = '''
import sys
from app.application.process_lock import ProcessLock, RuntimeLockUnavailable
try:
    with ProcessLock(sys.argv[1]):
        print('ACQUIRED')
except RuntimeLockUnavailable:
    print('BUSY')
    raise SystemExit(23)
'''


def test_other_process_is_rejected_until_the_owner_closes(tmp_path):
    path = tmp_path / 'backend.lock'
    with ProcessLock(path):
        blocked = subprocess.run([sys.executable,'-B','-c',PROBE,str(path)],capture_output=True,text=True,timeout=5)
        assert blocked.returncode == 23 and blocked.stdout.strip() == 'BUSY'
    available = subprocess.run([sys.executable,'-B','-c',PROBE,str(path)],capture_output=True,text=True,timeout=5)
    assert available.returncode == 0 and available.stdout.strip() == 'ACQUIRED'


def test_os_releases_lock_after_owner_exits_without_cleanup(tmp_path):
    path = tmp_path / 'backend.lock'
    crash = "import os,sys; from app.application.process_lock import ProcessLock; hold=ProcessLock(sys.argv[1]); hold.__enter__(); os._exit(0)"
    result = subprocess.run([sys.executable,'-B','-c',crash,str(path)],timeout=5)
    assert result.returncode == 0 and path.is_file()
    with ProcessLock(path):
        pass
