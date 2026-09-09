import argparse
from dataclasses import asdict
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import subprocess
from pathlib import Path
import sys
from .controller import Controller
from .diagnostics import report, safe_logs
from .mihomo_api import API
from .models import Settings
from .network import external_ip
from .redaction import redact, redact_url
from .storage import ControllerError, Paths, write_json


def parser():
    root = argparse.ArgumentParser(description='Immutable-profile Mihomo controller. GUI stays unprivileged.')
    commands = root.add_subparsers(dest='command', required=True)
    profile = commands.add_parser('profile').add_subparsers(dest='operation', required=True)
    for operation, argument in [('add-yaml', 'path'), ('add-subscription', 'url')]:
        item = profile.add_parser(operation)
        item.add_argument(argument)
        item.add_argument('--name')
    profile.add_parser('list')
    for operation in ('use', 'refresh', 'remove', 'duplicate'):
        item = profile.add_parser(operation)
        item.add_argument('id')
        if operation == 'use':
            item.add_argument('--health-url', help='Optional HTTPS check through mixed proxy; failure rolls back.')
        if operation == 'duplicate':
            item.add_argument('--name', required=True)
    settings = commands.add_parser('settings').add_subparsers(dest='operation', required=True)
    settings.add_parser('show')
    setting = settings.add_parser('set')
    setting.add_argument('key')
    setting.add_argument('value', help='JSON value, or plain string. Changes apply on next profile use.')
    for name in ('start', 'stop', 'restart', 'status', 'verify', 'rollback', 'recover', 'groups', 'external-ip', 'diagnostics'):
        commands.add_parser(name)
    node = commands.add_parser('select')
    node.add_argument('group')
    node.add_argument('node')
    latency = commands.add_parser('latency')
    latency.add_argument('node')
    logs = commands.add_parser('logs')
    logs.add_argument('--lines', type=int, default=100)
    return root


def run(args, controller):
    paths = controller.paths
    if args.command == 'profile':
        store = controller.profiles
        if args.operation == 'add-yaml':
            return {'id': store.add_yaml(Path(args.path), args.name)}
        if args.operation == 'add-subscription':
            return {'id': store.add_subscription(args.url, controller.candidate, args.name)}
        if args.operation == 'list':
            profiles = store.all()
            for profile in profiles.values():
                if 'url' in profile:
                    profile['url'] = redact_url(profile['url'])
            return profiles
        if args.operation == 'use':
            controller.apply(args.id, args.health_url)
        elif args.operation == 'refresh':
            store.refresh(args.id, controller.candidate)
        elif args.operation == 'remove':
            store.remove(args.id)
        elif args.operation == 'duplicate':
            return {'id': store.duplicate(args.id, args.name)}
    elif args.command == 'settings':
        settings = Settings.load(paths)
        if args.operation == 'show':
            return {k: v for k, v in asdict(settings).items() if k != 'api_secret'}
        if args.key not in asdict(settings) or args.key == 'api_secret':
            raise ControllerError('Unknown or protected setting.')
        try:
            value = json.loads(args.value)
        except ValueError:
            value = args.value
        setattr(settings, args.key, value)
        settings.validate()
        write_json(paths.settings, asdict(settings))
        return {'status': 'Saved desired settings; use a profile to apply them.'}
    elif args.command in ('start', 'restart'):
        controller.start(args.command == 'restart')
    elif args.command == 'stop':
        controller.service.action('stop')
    elif args.command in ('rollback', 'recover'):
        getattr(controller, args.command)()
    elif args.command in ('status', 'diagnostics'):
        return report(controller)
    elif args.command == 'external-ip':
        return external_ip()
    elif args.command == 'logs':
        return safe_logs(controller, max(1, min(args.lines, 1000)))
    elif args.command in ('groups', 'select', 'latency', 'verify'):
        config = controller.config()
        api = API(config)
        if args.command == 'groups':
            return api.groups()
        if args.command == 'select':
            api.select(args.group, args.node)
        elif args.command == 'latency':
            return api.latency(args.node)
        elif args.command == 'verify':
            controller._verify(config)
            settings = Settings.load(paths)
            if settings.tun is not config['tun']['enable'] or settings.mode not in ('source', config['mode']):
                raise ControllerError('DESIRED / RUNTIME STATE MISMATCH: unapplied settings.')
            return {'status': 'Owned service, TUN interface, runtime TUN, mode and groups verified.'}
    return {'status': 'ok'}


def main():
    args = parser().parse_args()
    if os.geteuid() == 0:
        print('ERROR: Run mmctl as your desktop user, not root.', file=sys.stderr)
        return 1
    os.umask(0o077)
    paths = Paths()
    logdir = paths.data / 'logs'
    logdir.mkdir(exist_ok=True, mode=0o700)
    logger = logging.getLogger('minimal_mihomo')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(logdir / 'controller.log', maxBytes=256 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    logger.addHandler(handler)
    try:
        with paths.lock():
            result = run(args, Controller(paths))
        # Do not log arguments, API payloads, config, or profile names.
        logger.info('Command %s succeeded', args.command)
        print(redact(result if isinstance(result, str) else json.dumps(result, indent=2)))
        if isinstance(result, dict) and (result.get('state_mismatch') or result.get('error')):
            logger.warning('Runtime state is unavailable or differs from desired state')
            return 2
        return 0
    except (ControllerError, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        # Only explicitly safe ControllerError messages reach the user.
        message = str(error) if isinstance(error, ControllerError) else f'{type(error).__name__}: operation failed; check file access and settings.'
        logger.error('Command %s failed: %s', args.command, redact(message))
        print('ERROR: ' + redact(message), file=sys.stderr)
        return 1
    finally:
        handler.close()
        logger.removeHandler(handler)


if __name__ == '__main__':
    sys.exit(main())
