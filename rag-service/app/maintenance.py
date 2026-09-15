"""Explicit offline database and derived-index maintenance."""

import argparse
import json
import sys
from contextlib import ExitStack
from dataclasses import asdict

from app.modules.knowledge.public import Knowledge
from app.modules.retrieval.public import Retrieval

from .application.process_lock import ProcessLock
from .application.recovery import rebuild_index
from .application.runtime import RuntimeSettings


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline maintenance; stop the backend before running.')
    parser.add_argument('command', choices=('init-db', 'adopt-legacy-db', 'rebuild-index'))
    args = parser.parse_args(argv)
    try:
        settings = RuntimeSettings()
        with ProcessLock(settings.runtime_lock_file), ExitStack() as cleanup:
            knowledge = Knowledge()
            cleanup.callback(knowledge.close)
            result = {}
            if args.command == 'init-db':
                knowledge.initialize_database()
            elif args.command == 'adopt-legacy-db':
                knowledge.adopt_legacy_database()
            else:
                retrieval = Retrieval()
                cleanup.callback(retrieval.close)
                result = asdict(rebuild_index(knowledge, retrieval))
    except KeyboardInterrupt:
        print(json.dumps({'error': 'MAINTENANCE_INTERRUPTED'}), file=sys.stderr)
        return 130
    except Exception as failure:
        print(json.dumps({'error': 'MAINTENANCE_FAILED', 'cause': type(failure).__name__}), file=sys.stderr)
        return 1
    print(json.dumps({'command': args.command, 'status': 'OK', **result}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
